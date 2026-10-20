"""Хранилище файлов, безопасный загрузчик, кадры видео(integration-spec §6, §16.2 test_media)."""
import errno
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import httpcore
import httpx
import pytest
from sqlalchemy import func, select

from conftest import mp4_bytes, png_bytes

from app import media, models, video
from app.db import SessionLocal
from app.integrations import http
from app.integrations.http import DownloadError

GIF = b"GIF89a" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x02\x00" + brand + b"\x00" * 32


# ---------- хранилище ----------

def test_store_bytes_content_addressed_and_idempotent():
    data = png_bytes((1, 2, 3))
    sha = hashlib.sha256(data).hexdigest()
    s = media.store_bytes(data)
    assert s == media.Stored(f"{sha[:2]}/{sha[2:4]}/{sha}.png", sha, len(data), "image/png")
    path = media.path_of(s.storage_key)
    assert path.read_bytes() == data and oct(path.stat().st_mode & 0o777) == "0o644"
    assert path.is_relative_to(Path(os.environ["MEDIA_ROOT"]).resolve())
    assert media.store_bytes(data) == s  # повтор - тот же ключ, файл не переписывается
    assert len(list(path.parent.iterdir())) == 1
    assert oct(media.tmp_dir().stat().st_mode & 0o777) == "0o700"


def test_sniff_by_signature():
    assert media.sniff(png_bytes()[:64]) == "image/png"  # заявлен JPEG, а байты PNG - PNG
    assert media.sniff(JPEG) == "image/jpeg"
    assert media.sniff(GIF) == "image/gif" and media.sniff(b"GIF87a....") == "image/gif"
    assert media.sniff(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert media.sniff(mp4_bytes()[:64]) == "video/mp4"
    assert media.sniff(ftyp(b"isom")) == "video/mp4" and media.sniff(ftyp(b"mp42")) == "video/mp4"
    assert media.sniff(ftyp(b"qt  ")) == "video/quicktime"
    assert media.sniff(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\x82\x84webm") == "video/webm"
    for brand in (b"heic", b"heix", b"mif1", b"avif", b"3gp4", b"M4A "):
        assert media.sniff(ftyp(brand)) is None, brand
    for other in (b"%PDF-1.7", b"<svg xmlns=", b"<!doctype html>", b"\x1a\x45\xdf\xa3matroska", b""):
        assert media.sniff(other) is None, other


def test_store_rejects_type_size_and_low_disk(env, monkeypatch):
    for bad in (b"plain text, not an image", b"%PDF-1.7 ...", ftyp(b"heic"), ftyp(b"avif")):
        with pytest.raises(media.MediaRejected) as e:
            media.store_bytes(bad)
        assert e.value.status == 415
        assert e.value.detail == "Неподдерживаемый тип файла — нужен JPEG, PNG, WEBP, GIF, MP4, MOV или WEBM"
    with pytest.raises(media.MediaRejected) as e:
        media.store_bytes(mp4_bytes(), allowed=media.IMAGE_TYPES)
    assert (e.value.status, e.value.detail) == (415, "Неподдерживаемый тип файла — нужен JPEG, PNG, WEBP или GIF")
    with pytest.raises(media.MediaRejected) as e:
        media.store_bytes(b"")
    assert e.value.status == 422
    env(MEDIA_MAX_IMAGE_MB="1", MEDIA_MAX_VIDEO_MB="2")
    big = png_bytes()[:16] + b"\x00" * (1024 * 1024)
    with pytest.raises(media.MediaRejected) as e:
        media.store_bytes(big)
    assert (e.value.status, e.value.detail) == (413, "Файл больше 1 МБ")
    assert media.store_bytes(mp4_bytes(1024 * 1024 + 10)).mime == "video/mp4"  # видео - свой предел
    assert media.limit_for("video") == 2 * media.MB and media.limit_for("image/png") == media.MB
    # мало места: свободно меньше MEDIA_MIN_FREE_MB(по умолчанию 1024 МБ) - 507, файл не сохранён
    monkeypatch.setattr(media.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 10, 100 * media.MB))
    data = png_bytes((9, 9, 9))
    with pytest.raises(media.MediaRejected) as e:
        media.store_bytes(data)
    assert (e.value.status, e.value.detail) == (507, "Мало места для файлов на сервере — файл не сохранён")
    assert not media.path_of(media._key(hashlib.sha256(data).hexdigest(), "image/png")).exists()


def test_store_file_moves_and_falls_back_on_exdev(monkeypatch):
    data = png_bytes((4, 5, 6))
    tmp = media.tmp_dir() / "incoming.bin"
    tmp.write_bytes(data)
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append((str(src), str(dst)))
        if len(calls) == 1:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst)

    monkeypatch.setattr(media.os, "replace", flaky)
    s = media.store_file(tmp)
    assert len(calls) == 2 and media.path_of(s.storage_key).read_bytes() == data
    assert not tmp.exists() and s.sha256 == hashlib.sha256(data).hexdigest()
    assert not [p for p in media.tmp_dir().iterdir() if p.name.startswith("mv-")]
    # такой файл уже есть - временный просто удаляется; неподходящий тип - тоже удаляется(файл забран)
    tmp.write_bytes(data)
    assert media.store_file(tmp) == s and not tmp.exists()
    tmp.write_bytes(b"not an image")
    with pytest.raises(media.MediaRejected):
        media.store_file(tmp)
    assert not tmp.exists()


def test_path_of_stays_inside_root():
    for bad in ("../x", "/etc/passwd", "ab/../../x", "", "ab/\x00"):
        with pytest.raises(ValueError):
            media.path_of(bad)
    assert media.path_of("ab/cd/file.png").name == "file.png"


def _asset(db, **kw) -> models.MediaAsset:
    data = kw.pop("data", None) or png_bytes((7, 7, 7))
    s = media.store_bytes(data)
    kw.setdefault("origin_ref", "upload")
    a = models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="downloaded",
                          storage_key=s.storage_key, sha256=s.sha256, size=s.size, mime=s.mime, **kw)
    db.add(a)
    db.commit()
    return a


def test_purge_unattached_uploads(client):
    now = models.utcnow()
    with SessionLocal() as db:
        old = _asset(db, created_at=now - timedelta(hours=25), data=png_bytes((10, 0, 0)))
        shared_old = _asset(db, created_at=now - timedelta(hours=25), data=png_bytes((0, 10, 0)))
        shared_kept = _asset(db, ad_id="a1", created_at=now - timedelta(hours=25), data=png_bytes((0, 10, 0)))
        fresh = _asset(db, created_at=now - timedelta(hours=2), data=png_bytes((0, 0, 10)))
        paths = {a.id: media.path_of(a.storage_key) for a in (old, shared_old, shared_kept, fresh)}
        assert media.purge_unattached(db, now) == 2
        left = {a.id for a in db.query(models.MediaAsset)}
        assert left == {shared_kept.id, fresh.id}
    assert not paths[old.id].exists()  # файл удалён вместе с записью
    assert paths[shared_old.id].exists()  # тот же файл нужен привязанной записи - остаётся
    assert paths[fresh.id].exists()


def _age(path: Path, hours: float = 25) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_purge_orphans(client):
    """Файл хранилища без записи MediaAsset(запись файла прошла, приём - нет) удаляется через сутки после последнего
    использования; файл с записью, свежий файл и посторонние имена остаются; пустая media_assets - уборки нет."""
    orphan = media.path_of(media.store_bytes(png_bytes((171, 3, 251))).storage_key)
    fresh = media.path_of(media.store_bytes(png_bytes((172, 3, 251))).storage_key)
    stray = media.root() / "00" / "00" / "notes.txt"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"x")
    top = media.root() / ("ab" * 32 + ".png")  # имя как в хранилище, но не в ab/cd/ - не трогается
    top.write_bytes(b"x")
    for p in (orphan, stray, top):
        _age(p)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(models.MediaAsset)) == 0
        assert media.purge_orphans(db) == 0  # записей нет вовсе - похоже на чужую/новую БД: ничего не удаляется
        assert orphan.exists()
        kept = _asset(db, ad_id="a1", data=png_bytes((173, 3, 251)))
        kept_path = media.path_of(kept.storage_key)
        _age(kept_path)
        assert media.purge_orphans(db) >= 1
    assert not orphan.exists()
    assert kept_path.exists() and fresh.exists() and stray.exists() and top.exists()
    stray.unlink()
    top.unlink()


def test_reused_file_is_not_orphan(client):
    """Повторная запись тех же байтов обновляет время файла: идущий запуск, который нашёл старый файл без записи и ещё
    не сохранил свою, не теряет его при уборке."""
    data = png_bytes((174, 3, 251))
    path = media.path_of(media.store_bytes(data).storage_key)
    _age(path)
    assert media.store_bytes(data).storage_key  # тот же файл
    assert path.stat().st_mtime > time.time() - 60
    _age(path)
    tmp = media.tmp_dir() / "reuse.png"
    tmp.write_bytes(data)
    media.store_file(tmp)
    assert not tmp.exists() and path.stat().st_mtime > time.time() - 60
    with SessionLocal() as db:
        _asset(db, ad_id="a1", data=png_bytes((175, 3, 251)))
        media.purge_orphans(db)
    assert path.exists()


def test_housekeeping_purges_orphans(client):
    from app import worker

    with SessionLocal() as db:
        _asset(db, ad_id="a1", data=png_bytes((176, 3, 251)))
    orphan = media.path_of(media.store_bytes(png_bytes((177, 3, 251))).storage_key)
    _age(orphan)
    with SessionLocal() as db:
        worker.housekeeping(db, models.utcnow())
    assert not orphan.exists()


def test_upload_budget(client):
    now = models.utcnow()
    with SessionLocal() as db:
        for _ in range(media.UPLOAD_HOURLY_FILES - 1):
            db.add(models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="downloaded", origin_ref="upload",
                                     size=1000, created_at=now - timedelta(minutes=5)))
        db.add(models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="downloaded", origin_ref="intake",
                                 size=1000, created_at=now))  # файл приёма публикаций - не ручная загрузка, не считается
        db.commit()
        media.check_upload_budget(db, now, size=1000)  # 50-я - можно
        db.add(models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="downloaded", origin_ref="upload",
                                 size=1000, ad_id="a1", created_at=now))  # привязанная к публикации - считается
        db.commit()
        with pytest.raises(media.MediaRejected):
            media.check_upload_budget(db, now, size=1000)
        db.execute(models.MediaAsset.__table__.delete().where(models.MediaAsset.ad_id == "a1"))
        db.commit()
        db.add(models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="downloaded", origin_ref="upload",
                                 size=1000, created_at=now))
        db.commit()
        with pytest.raises(media.MediaRejected) as e:
            media.check_upload_budget(db, now, size=1000)
        assert (e.value.status, e.value.detail) == (429, "Слишком много загрузок — попробуйте позже")
        media.check_upload_budget(db, now + timedelta(hours=2), size=1000)  # час прошёл
        with pytest.raises(media.MediaRejected):
            media.check_upload_budget(db, now + timedelta(hours=2), size=media.UPLOAD_HOURLY_BYTES + 1)


def test_purge_temp():
    stale, fresh = media.tmp_dir() / "dl-stale", media.tmp_dir() / "dl-fresh"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    (media.tmp_dir() / "frames-old").mkdir(exist_ok=True)
    old = time.time() - 2 * 24 * 3600
    os.utime(stale, (old, old))
    os.utime(media.tmp_dir() / "frames-old", (old, old))
    assert media.purge_temp() >= 2
    assert not stale.exists() and not (media.tmp_dir() / "frames-old").exists() and fresh.exists()
    fresh.unlink()


# ---------- адреса ----------

BLOCKED = ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "100.64.0.1", "fc00::1", "::ffff:127.0.0.1",
           "64:ff9b::a9fe:a9fe", "2002:7f00:0001::", "2001:0:4136:e378:8000:63bf:3f57:fefe", "0.0.0.0", "224.0.0.1",
           "192.168.1.10", "fe80::1%eth0", "not-an-ip", "fec0::1", "[feff::1]")  # site-local fec0::/10 - не публичный


def test_is_public_ip():
    for ip in BLOCKED:
        assert not http.is_public_ip(ip), ip
    for ip in ("93.184.216.34", "8.8.8.8", "2606:4700:4700::1111", "[2a00:1450:4001::200e]"):
        assert http.is_public_ip(ip), ip
    # NAT64 целиком в ::/8(is_reserved) - блокируется даже с публичным IPv4 внутри(строже, чем нужно; так безопаснее)
    assert not http.is_public_ip("64:ff9b::808:808")
    # Teredo: клиентский IPv4(192.168.1.1) раскрывается и проверяется отдельно
    import ipaddress

    assert ipaddress.IPv4Address("192.168.1.1") in http._embedded(ipaddress.ip_address("2001:0:4136:e378:8000:63bf:3f57:fefe"))
    assert ipaddress.IPv4Address("169.254.169.254") in http._embedded(ipaddress.ip_address("64:ff9b::a9fe:a9fe"))


def test_host_allowed():
    allowed = ("cdninstagram.com", "fbcdn.net")
    assert http.host_allowed("scontent-x.cdninstagram.com", allowed)
    assert http.host_allowed("CDNINSTAGRAM.COM.", allowed) and http.host_allowed("a.b.fbcdn.net", allowed)
    for bad in ("evilcdninstagram.com", "cdninstagram.com.evil.io", "fbcdn.net.example", "", "xn--"):
        assert not http.host_allowed(bad, allowed), bad


def test_redact_url():
    assert http.redact_url("https://user:pw@cdn.example.com:8443/a/b.jpg?sig=SECRET&oh=1#frag") == \
        "https://cdn.example.com:8443/a/b.jpg"
    assert http.redact_url("https://api.telegram.org/file/bot123456:AAE-secret_token/photos/x.jpg") == \
        "https://api.telegram.org/file/bot***/photos/x.jpg"
    assert http.redact_url("HTTPS://Example.COM/path") == "https://example.com/path"
    assert "SECRET" not in http.redact_url("/relative?token=SECRET")


def test_retry_after():
    assert http.retry_after({"Retry-After": "120"}, 60) == 120
    assert http.retry_after(httpx.Headers({"retry-after": "0"}), 60) == 1
    assert http.retry_after({}, 30) == 30 and http.retry_after(None, 5) == 5
    assert http.retry_after({"Retry-After": "garbage"}, 42) == 42
    assert http.retry_after({"Retry-After": "999999999"}, 1) == 86400
    future = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 300))
    assert 250 <= http.retry_after({"Retry-After": future}, 1) <= 301


# ---------- загрузчик ----------

def fetch(url: str, **kw) -> http.Fetched:
    kw.setdefault("max_bytes", 1024 * 1024)
    kw.setdefault("allowed_mime", media.IMAGE_TYPES)
    return http.fetch_to_temp(url, **kw)


def err(url: str, **kw) -> DownloadError:
    with pytest.raises(DownloadError) as e:
        fetch(url, **kw)
    return e.value


def test_fetch_ok_then_store(http_mock):
    data = png_bytes((20, 30, 40))
    http_mock.on("GET", "https://cdn.example.com/a.png", content=data, headers={"Content-Type": "text/html"})
    f = fetch("https://cdn.example.com/a.png?sig=SECRET")
    assert (f.size, f.mime, f.sha256) == (len(data), "image/png", hashlib.sha256(data).hexdigest())
    assert f.final_url == "https://cdn.example.com/a.png" and f.path.parent == media.tmp_dir()
    req = http_mock.calls[0]
    assert req.headers["accept-encoding"] == "identity" and "cookie" not in req.headers
    assert req.headers["user-agent"].startswith("SAQ-Qurylys-monitor")
    s = media.store_file(f.path)
    assert s.sha256 == f.sha256 and not f.path.exists()


def test_fetch_blocks_internal_addresses(http_mock):
    for ip in BLOCKED[:10]:
        http_mock.resolve("files.example.com", ip)
        e = err("https://files.example.com/x.png")
        assert e.code == "blocked_address" and not e.retryable, ip
    for literal in ("https://127.0.0.1/x.png", "https://[::1]/x.png", "https://169.254.169.254/latest/meta-data",
                    "https://[fec0::1]/x.jpg"):
        assert err(literal).code == "blocked_address", literal
    assert http_mock.calls == []  # ни одного запроса во внутреннюю сеть


def test_fetch_redirects(http_mock):
    data = png_bytes((50, 60, 70))
    # публичный адрес перенаправляет во внутреннюю сеть - запрос туда не выполняется
    http_mock.on("GET", "https://cdn.example.com/r", status=302, headers={"Location": "https://inner.example.com/x.png"})
    http_mock.resolve("inner.example.com", "10.0.0.5")
    assert err("https://cdn.example.com/r").code == "blocked_address"
    assert [str(r.url) for r in http_mock.calls] == ["https://cdn.example.com/r"]
    # больше max_redirects
    for i in range(5):
        http_mock.on("GET", f"https://cdn.example.com/hop{i}", status=301,
                     headers={"Location": f"/hop{i + 1}"})
    e = err("https://cdn.example.com/hop0", max_redirects=3)
    assert e.code == "too_many_redirects"
    # https → http - не выполняется
    http_mock.on("GET", "https://cdn.example.com/down", status=302, headers={"Location": "http://cdn.example.com/x.png"})
    assert err("https://cdn.example.com/down").code == "bad_scheme"
    # относительное перенаправление в пределах лимита - файл загружается
    http_mock.on("GET", "https://cdn.example.com/ok", status=307, headers={"Location": "/files/ok.png?x=1"})
    http_mock.on("GET", "https://cdn.example.com/files/ok.png", content=data)
    assert fetch("https://cdn.example.com/ok").final_url == "https://cdn.example.com/files/ok.png"


def test_fetch_size_type_and_encoding(http_mock):
    data = png_bytes((1, 1, 1))
    http_mock.on("GET", "https://cdn.example.com/big", content=b"x", headers={"Content-Length": str(10 * 1024 * 1024)})
    assert err("https://cdn.example.com/big").code == "too_large"
    # без Content-Length - обрыв по мере чтения, временный файл удалён
    http_mock.on("GET", "https://cdn.example.com/stream", handler=lambda r: httpx.Response(
        200, content=iter([data[:16] + b"\x00" * 40000, b"\x00" * 40000])))
    before = set(media.tmp_dir().iterdir())
    assert err("https://cdn.example.com/stream", max_bytes=50000).code == "too_large"
    assert set(media.tmp_dir().iterdir()) == before
    http_mock.on("GET", "https://cdn.example.com/gif", content=GIF)
    e = err("https://cdn.example.com/gif", allowed_mime={"image/png"})
    assert e.code == "bad_type" and set(media.tmp_dir().iterdir()) == before
    http_mock.on("GET", "https://cdn.example.com/page", content=b"<!doctype html><html></html>")
    assert err("https://cdn.example.com/page").code == "bad_type"
    http_mock.on("GET", "https://cdn.example.com/gz", handler=lambda r: httpx.Response(
        200, headers={"Content-Encoding": "gzip"}, content=iter([data])))
    assert err("https://cdn.example.com/gz").code == "bad_type"
    http_mock.on("GET", "https://cdn.example.com/404?sig=SECRETSIG", status=404)
    e = err("https://cdn.example.com/404?sig=SECRETSIG")
    assert (e.code, e.retryable) == ("http_404", False)
    assert "SECRETSIG" not in e.detail and "?" not in e.detail and "cdn.example.com/404" in e.detail
    for status, retryable in ((503, True), (429, True), (403, False)):
        http_mock.on("GET", f"https://cdn.example.com/s{status}", status=status)
        e = err(f"https://cdn.example.com/s{status}")
        assert (e.code, e.retryable) == (f"http_{status}", retryable)


def test_fetch_url_rules(http_mock):
    assert err("https://user:pw@cdn.example.com/a.png").code == "bad_url"
    assert err("ftp://cdn.example.com/a.png").code == "bad_scheme"
    assert err("http://cdn.example.com/a.png").code == "bad_scheme"
    assert err("https://cdn.example.com:8443/a.png").code == "bad_url"
    assert err("https:///nohost").code == "bad_url"
    with pytest.raises(ValueError):
        fetch("https://cdn.example.com/a.png", headers={"Authorization": "Bearer x"})
    with pytest.raises(ValueError):
        fetch("https://cdn.example.com/a.png", headers={"X-Apify-Token": "x"})
    # allowed_hosts: поддомены - да, похожие имена - нет; http - только разрешённым хостам и без учётных данных
    data = png_bytes((3, 3, 3))
    http_mock.on("GET", re.compile(r"cdninstagram\.com/"), content=data)
    assert fetch("https://scontent-x.cdninstagram.com/v/a.jpg", allowed_hosts=("cdninstagram.com",)).mime == "image/png"
    e = err("https://evilcdninstagram.com/v/a.jpg", allowed_hosts=("cdninstagram.com",))
    assert e.code == "host_not_allowed"
    assert fetch("http://scontent-x.cdninstagram.com/v/a.jpg", allowed_hosts=("cdninstagram.com",)).size == len(data)
    e = err("http://scontent-x.cdninstagram.com/v/a.jpg", allowed_hosts=("cdninstagram.com",),
            auth_headers={"Authorization": "Bearer t"}, auth_hosts=("cdninstagram.com",))
    assert e.code == "bad_scheme"


def test_fetch_auth_headers_only_to_auth_hosts(http_mock):
    data = png_bytes((8, 8, 8))
    kv = "https://api.apify.com/v2/key-value-stores/s1/records/video"
    http_mock.on("GET", kv, status=302, headers={"Location": "https://storage.example.net/blob/1"})
    http_mock.on("GET", "https://storage.example.net/blob/1", content=data)
    fetch(kv, auth_headers={"Authorization": "Bearer apify-secret-token"}, auth_hosts=("api.apify.com",))
    first, second = http_mock.calls
    assert first.headers["authorization"] == "Bearer apify-secret-token"
    assert "authorization" not in second.headers  # чужой хост после перенаправления - без токена
    http_mock.clear()
    http_mock.on("GET", "https://other.example.com/a.png", content=data)
    fetch("https://other.example.com/a.png", auth_headers={"Authorization": "Bearer x"}, auth_hosts=("api.apify.com",))
    assert "authorization" not in http_mock.calls[0].headers


def test_fetch_total_deadline(http_mock, monkeypatch):
    http_mock.on("GET", "https://cdn.example.com/slow.png", content=png_bytes())
    clock = iter([0.0, 1000.0, 1000.0, 1000.0])
    monkeypatch.setattr(http, "MONOTONIC", lambda: next(clock))
    e = err("https://cdn.example.com/slow.png", total_timeout=30)
    assert (e.code, e.retryable) == ("timeout", True)
    assert http_mock.calls == []
    # срок истёк во время чтения
    clock2 = iter([0.0, 1.0, 1000.0, 1000.0])
    monkeypatch.setattr(http, "MONOTONIC", lambda: next(clock2))
    assert err("https://cdn.example.com/slow.png", total_timeout=30).code == "timeout"


def test_fetch_deadline_checked_per_network_block(http_mock, monkeypatch):
    """Регрессия: сервер отдаёт по байту чуть быстрее тайм-аута чтения. Общий срок и остановка воркера проверяются на
    каждом сетевом блоке, а не раз в 64 КБ(раньше ошибка приходила только после всего тела - воркер стоял часами);
    тайм-ауты запроса не длиннее остатка срока."""
    url = "https://cdn.example.com/trickle.png"
    clock, sent, timeouts = [0.0], [], []
    monkeypatch.setattr(http, "MONOTONIC", lambda: clock[0])
    data = png_bytes((181, 3, 251)) + b"\x00" * 4000

    def trickle(request):
        timeouts.append(request.extensions["timeout"])

        def body():
            for i in range(len(data)):
                clock[0] += 20.0  # байт раз в 20 с - меньше тайм-аута чтения 30 с
                sent.append(i)
                yield data[i:i + 1]
        return httpx.Response(200, content=body())

    http_mock.on("GET", url, handler=trickle)
    e = err(url, max_bytes=10 * 1024 * 1024)  # срок по умолчанию: 60 + 10 МБ / 256 КБ = 100 с
    assert (e.code, e.retryable) == ("timeout", True) and e.detail.startswith("Загрузка не уложилась во время")
    assert clock[0] <= 120 and len(sent) <= 6, (clock[0], len(sent))
    assert timeouts[0]["read"] == 30 and timeouts[0]["connect"] == 10
    clock[0], sent[:] = 0.0, []
    flags = iter([False, False, False, True])  # перед запросом, два блока - нет; третий - остановка
    assert err(url, max_bytes=10 * 1024 * 1024, cancel=lambda: next(flags)).code == "cancelled" and len(sent) == 3
    clock[0] = 0.0
    assert err(url, max_bytes=10 * 1024 * 1024, total_timeout=5).code == "timeout"
    assert timeouts[-1]["read"] == 5 and timeouts[-1]["connect"] == 5  # не дольше остатка срока


def test_fetch_cancelled(http_mock):
    """Остановка воркера: загрузка прерывается перед запросом или на следующем блоке; временный файл удаляется."""
    url = "https://cdn.example.com/big.png"
    http_mock.on("GET", url, content=png_bytes((178, 3, 251)) + b"\x00" * (3 * 1024 * 1024))
    before = set(media.tmp_dir().iterdir())
    flags = iter([False, False, True])  # перед запросом, первый блок - нет; второй блок - остановка
    e = err(url, max_bytes=8 * 1024 * 1024, cancel=lambda: next(flags))
    assert (e.code, e.retryable) == ("cancelled", True) and e.detail.startswith("Загрузка прервана: воркер")
    assert set(media.tmp_dir().iterdir()) == before and len(http_mock.calls) == 1
    assert err(url, cancel=lambda: True).code == "cancelled" and len(http_mock.calls) == 1  # запроса не было


def test_download_media_stops_after_grace(client, http_mock, monkeypatch):
    """RunContext.download_media при остановке воркера: идущая загрузка получает DOWNLOAD_STOP_GRACE_S с, затем -
    "failed" с понятной причиной(не SIGKILL посреди файла)."""
    import threading

    from app.integrations import context, registry

    http_mock.on("GET", "https://cdn.example.com/a.png", content=png_bytes((179, 3, 251)))
    http_mock.on("GET", "https://cdn.example.com/b.png", content=png_bytes((180, 3, 251)))
    stop = threading.Event()
    stop.set()
    ctx = context.RunContext("krisha", registry.get("krisha"), stop=stop)
    assert ctx.download_media("https://cdn.example.com/a.png", key="a").status == "downloaded"  # в пределах срока
    monkeypatch.setattr(context, "DOWNLOAD_STOP_GRACE_S", -1.0)
    m = ctx.download_media("https://cdn.example.com/b.png", key="b")
    assert m.status == "failed" and m.error.startswith("Загрузка прервана: воркер останавливается")


def test_public_only_backend_pins_dns(monkeypatch):
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    monkeypatch.setattr(http, "RESOLVER", lambda host, port: next(answers))
    seen = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp",
                        lambda self, host, port, *a, **kw: seen.append((host, port)) or "stream")
    backend = http.PublicOnlyBackend()
    assert backend.connect_tcp("cdn.example.com", 443) == "stream"
    assert seen == [("93.184.216.34", 443)]  # подключение - к проверенному IP, а не к имени
    with pytest.raises(DownloadError) as e:
        backend.connect_tcp("cdn.example.com", 443)  # второй ответ DNS - внутренний адрес(rebinding)
    assert e.value.code == "blocked_address" and seen == [("93.184.216.34", 443)]


def test_download_transport_uses_public_only_backend(monkeypatch):
    assert hasattr(httpx.HTTPTransport(retries=0)._pool, "_network_backend")  # поле httpx/httpcore, на которое опираемся
    monkeypatch.setattr(http, "TRANSPORT", None)
    monkeypatch.setattr(http, "RESOLVER", lambda host, port: ["10.0.0.5"])
    t = http.download_transport()
    assert isinstance(t._pool._network_backend, http.PublicOnlyBackend)
    with pytest.raises(DownloadError) as e:  # подключение не начинается: адрес проверяется в самом транспорте
        t.handle_request(httpx.Request("GET", "https://cdn.example.com/a.png"))
    assert e.value.code == "blocked_address"


# ---------- клиенты API ----------

def test_client_keeps_no_cookies(http_mock):
    http_mock.on("GET", "https://api.example.com/a", json={}, headers={"Set-Cookie": "sl-session=abc123; Path=/"})
    http_mock.on("GET", "https://api.example.com/b", json={})
    with http.client(base_url="https://api.example.com/") as c:
        c.get("a")
        c.get("b")
        assert not list(c.cookies.jar)
    assert "cookie" not in http_mock.calls[1].headers
    assert http_mock.calls[0].headers["user-agent"].startswith("SAQ-Qurylys-monitor")


def test_base_url_client_is_host_pinned(http_mock):
    http_mock.on("GET", "https://api.example.com/next", status=302, headers={"Location": "https://evil.example.com/x"})
    with http.client(base_url="https://api.example.com/", headers={"Authorization": "Bearer t"},
                     follow_redirects=True) as c:
        for url in ("https://evil.example.com/v1/x?after=1", "http://api.example.com/x", "https://api.example.com:8443/x"):
            with pytest.raises(DownloadError) as e:
                c.get(url)
            assert e.value.code == "host_not_allowed"
        with pytest.raises(DownloadError):
            c.get("next")  # перенаправление на другой хост - тоже отклонено, токен туда не уходит
    assert [str(r.url) for r in http_mock.calls] == ["https://api.example.com/next"]


# ---------- кадры видео ----------

def test_video_command_and_limits(monkeypatch, tmp_path):
    monkeypatch.setattr(video.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = video.command(Path("/m/v.mp4"), "video/mp4", tmp_path, timeout=90)
    assert cmd[:5] == ["prlimit", "--as=1610612736", "--cpu=100", "--fsize=67108864", "--"]
    tail = cmd[5:]
    assert tail[:7] == ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f"]
    assert tail[7] == "mov" and tail[tail.index("-i") + 1] == "file:/m/v.mp4"
    # -threads 1 - опция ввода(до -i): ограничивает декодер; иначе декодер H.264 заводит поток на ядро и 1080p
    # не укладывается в --as(регрессия: кадры 1080x1920 не извлекались на 16 ядрах)
    assert tail[tail.index("-i") - 2:tail.index("-i")] == ["-threads", "1"]
    assert tail[tail.index("-filter_threads") + 1] == "1"
    assert tail[tail.index("-vf") + 1] == "fps=1/2.0,scale='min(1280,iw)':-2"
    assert tail[-3:] == ["-frames:v", "8", f"{tmp_path}/f_%03d.png"]
    assert video.command(Path("/m/v.webm"), "video/webm", tmp_path)[5:][7] == "matroska"
    monkeypatch.setattr(video.shutil, "which", lambda name: None)
    assert video.command(Path("/m/v.mp4"), "video/mp4", tmp_path)[0] == "ffmpeg"
    assert not video.ffmpeg_available()


def test_video_failures_return_nothing(monkeypatch, tmp_path):
    src = tmp_path / "v.mp4"
    src.write_bytes(mp4_bytes())

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], kw.get("timeout"))

    monkeypatch.setattr(video.subprocess, "run", timeout)
    before = set(media.tmp_dir().iterdir())
    assert video.extract_frames(src, "video/mp4") == []
    monkeypatch.setattr(video.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, b"", b"bad data"))
    assert video.extract_frames(src, "video/mp4") == []
    assert video._reason(b"[dec:h264] Error submitting packet\n    Last message repeated 1 times\n") == \
        "[dec:h264] Error submitting packet"  # в журнал - содержательная строка, не "Last message repeated"
    assert set(media.tmp_dir().iterdir()) == before  # каталог кадров удалён


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg не установлен")
def test_video_real_frames(tmp_path):
    src = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=10",
                    "-pix_fmt", "yuv420p", str(src)], check=True, capture_output=True, timeout=60)
    frames = video.extract_frames(src, "video/mp4", every_s=2.0, max_frames=8)
    assert [ms for ms, _ in frames] == [0, 2000, 4000][:len(frames)] and len(frames) >= 2
    assert all(media.sniff(png[:64]) == "image/png" for _, png in frames)
    assert len(video.extract_frames(src, "video/mp4", every_s=1.0, max_frames=2)) == 2


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("prlimit"), reason="нет ffmpeg или prlimit")
def test_video_real_frames_1080p_under_limits(tmp_path):
    """Регрессия: 1080p H.264 под prlimit --as - декодер в один поток укладывается в ограничение памяти."""
    src = tmp_path / "hd.mp4"
    made = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                           "testsrc=duration=3:size=1920x1080:rate=30", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                           str(src)], capture_output=True, timeout=120)
    if made.returncode != 0:
        pytest.skip("ffmpeg без libx264")
    frames = video.extract_frames(src, "video/mp4", every_s=1.0, max_frames=3)
    assert len(frames) >= 2 and all(media.sniff(png[:64]) == "image/png" for _, png in frames)


# ---------- маршруты /api/media(integration-spec §11.1; появляются на шаге маршрутов фундамента) ----------

needs_routes = pytest.mark.skipif(importlib.util.find_spec("app.integrations.routes") is None,
                                  reason="маршруты /api/media пишутся на следующем шаге фундамента")


@needs_routes
def test_media_routes_serve_only_downloaded(client):
    data = png_bytes((90, 91, 92))
    with SessionLocal() as db:
        ok = _asset(db, data=data, ad_id="a1", origin_ref="intake")
        pending = models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="pending", meta={"src": "https://x/y"})
        failed = models.MediaAsset(id=os.urandom(16).hex(), kind="image", status="failed")
        db.add_all([pending, failed])
        db.commit()
    r = client.get(f"/api/media/{ok.id}")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"
    for aid in (pending.id, failed.id, "0" * 32):
        assert client.get(f"/api/media/{aid}").status_code == 404, aid
    assert client.get("/api/media/not-an-id").status_code == 422


@needs_routes
def test_media_upload(client):
    data = png_bytes((11, 12, 13))
    r = client.post("/api/media", files={"file": ("shot.jpg", data, "image/jpeg")})  # заявлен JPEG, байты PNG
    assert r.status_code == 201
    body = r.json()
    assert body["mime"] == "image/png" and body["size"] == len(data) and body["url"] == f"/api/media/{body['id']}"
    assert client.get(body["url"]).content == data
    pdf = client.post("/api/media", files={"file": ("doc.pdf", b"%PDF-1.7 " + b"x" * 100, "application/pdf")})
    assert pdf.status_code == 415
    big = client.post("/api/media", files={"file": ("big.png", png_bytes()[:16] + b"\x00" * (11 * 1024 * 1024),
                                                    "image/png")})
    assert big.status_code == 413
    for _ in range(media.UPLOAD_HOURLY_FILES - 1):
        assert client.post("/api/media", files={"file": ("s.png", data, "image/png")}).status_code == 201
    r = client.post("/api/media", files={"file": ("s.png", data, "image/png")})
    assert (r.status_code, r.json()["detail"]) == (429, "Слишком много загрузок — попробуйте позже")


@needs_routes
def test_upload_budget_not_bypassed_by_attaching(client):
    """Регрессия: загрузка, привязанная к публикации через POST /api/ads, выпадала из часового счёта - без входа можно
    было загружать без предела, пока место не кончится и для собранных файлов(507)."""
    codes = []
    for i in range(media.UPLOAD_HOURLY_FILES + 3):
        r = client.post("/api/media", files={"file": ("s.png", png_bytes((i, 7, 99)), "image/png")})
        codes.append(r.status_code)
        if r.status_code == 201:
            ad = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": f"t{i}",
                                               "image": r.json()["url"]})
            assert ad.status_code == 201 and ad.json()["image"] == r.json()["url"]
    assert codes.count(201) == media.UPLOAD_HOURLY_FILES and codes[-3:] == [429, 429, 429]
