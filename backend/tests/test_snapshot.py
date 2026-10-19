"""Стартовый набор (app.snapshot): выгрузка и загрузка (SQLite и PostgreSQL — SAQ_TEST_DATABASE_URL), старт пустой БД,
отказ на непустой БД, первичные ключи и последовательности, файлы, исключённые таблицы, секреты, набор прежней версии
схемы и сам набор в репозитории (shared/snapshot): состав, размер, файлы и ответы API для офлайн-режима фронтенда."""
import hashlib
import io
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

import pytest
from sqlalchemy import func, select

from conftest import mp4_bytes

from app import demo, importer, media, migrate, models, seed, snapshot
from app.db import Base, SessionLocal, engine

SNAP = snapshot.DIR
MAN = snapshot.manifest(SNAP)
NOW = datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)
AUTHOR = "Омарова Ж."
SECRET = "sk-ant-api03-TESTSECRETVALUE0123456789abcdef"
ADMIN_TOKEN = "t-admin-0123456789abcdef0123"


def _count(model, *where) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _jpeg(size=(1600, 900), color=(30, 120, 200)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _start_snapshot(monkeypatch, src: Path = SNAP, files: bool = True) -> None:
    """Пустая БД — как у приложения: стартовый набор src (а не демо-набор тестов)."""
    monkeypatch.setattr(seed, "START", partial(seed.start, src=src, files=files))


# ---------- состав набора ----------

def test_every_table_classified_and_order_follows_foreign_keys():
    names = [s.name for s in snapshot.TABLES]
    assert len(names) == len(set(names))
    assert set(names) | set(snapshot.EXCLUDED) == set(Base.metadata.tables)
    assert not set(names) & set(snapshot.EXCLUDED)
    pos = {n: i for i, n in enumerate(names)}
    for s in snapshot.TABLES:
        for fk in s.model.__table__.foreign_keys:
            parent = fk.column.table.name
            assert parent == s.name or pos[parent] < pos[s.name], (s.name, parent)


def test_compress_downscales_strips_and_keeps_small_jpeg():
    from PIL import Image

    big = _jpeg((2400, 1200))
    out, mime, w, h = snapshot.compress(big, 1024, 75)
    assert (mime, w, h) == ("image/jpeg", 1024, 512) and len(out) < len(big)
    with Image.open(io.BytesIO(out)) as im:
        assert im.size == (1024, 512) and not im.getexif()
    # уже сжатый файл остаётся как есть — повторная выгрузка набора не меняет файлы
    assert snapshot.compress(out, 1024, 75)[0] == out
    # PNG — только если он меньше JPEG; прозрачность в JPEG — на белом
    png = io.BytesIO()
    Image.new("RGBA", (64, 64), (0, 0, 0, 0)).save(png, format="PNG")
    data, mime, w, h = snapshot.compress(png.getvalue(), 1024, 75)
    assert (w, h) == (64, 64) and mime in ("image/png", "image/jpeg") and media.sniff(data[:64]) == mime


def test_leaks_detects_tokens_and_settings_secrets():
    assert snapshot.leaks("обычный текст рекламы, бронь 100 000 ₸, https://instagram.com/p/abc/") == []
    assert snapshot.leaks(f"ключ {SECRET}") == ["ключ Anthropic"]
    assert snapshot.leaks("https://cdn.example/v.mp4?oh=00_AbCdEf123456&oe=6720") == ["подписанная ссылка"]
    assert snapshot.leaks("https://cdn.example/v.mp4?oh=***") == []
    assert snapshot.leaks("Authorization: Bearer abcdefghijklmnop1234") == ["Bearer-токен"]
    assert snapshot.leaks("значение QWERTY-12345678", ["QWERTY-12345678"]) == ["значение секрета из настроек"]
    assert snapshot.leaks("коротко", ["1234567"]) == []  # короче 8 символов — не проверяется


# ---------- набор в репозитории ----------

def test_committed_snapshot_layout():
    """shared/snapshot: формат, версия схемы из цепочки migrate, таблицы и строки, файлы по SHA-256, без исключённых
    таблиц, видео и кадров, не больше 40 МБ."""
    assert MAN is not None, "нет shared/snapshot/manifest.json — выгрузите набор: python -m app.snapshot export"
    assert MAN["format"] == snapshot.FORMAT
    assert MAN["schemaVersion"] == models.SCHEMA_VERSION or migrate.chain_from(MAN["schemaVersion"]) is not None
    # набор прежней версии схемы — без таблиц, которые добавили шаги migrate после неё (загружаются пустыми)
    names = {s.name for s in snapshot.TABLES}
    assert set(MAN["tables"]) <= names and names - set(MAN["tables"]) <= migrate.tables_after(MAN["schemaVersion"])
    for name, info in MAN["tables"].items():
        rows = json.loads((SNAP / info["file"]).read_text(encoding="utf-8"))
        assert len(rows) == info["rows"], name
    for name in snapshot.EXCLUDED:
        assert not (SNAP / f"{name}.json").exists(), name
    assert MAN["registry"]["consistent"], MAN["registry"]
    rows = json.loads((SNAP / "media_assets.json").read_text(encoding="utf-8"))
    assert not [r for r in rows if r["kind"] == "frame"]
    assert all(r["storage_key"] is None and r["status"] != "downloaded" for r in rows if r["kind"] == "video")
    files = {p.name: p for p in (SNAP / "media").iterdir()}
    keys = {Path(r["storage_key"]).name for r in rows if r["storage_key"]}
    assert keys == set(files) and len(files) == MAN["media"]["files"]
    for r in rows:
        if r["storage_key"]:
            data = files[Path(r["storage_key"]).name].read_bytes()
            assert hashlib.sha256(data).hexdigest() == r["sha256"] and len(data) == r["size"], r["id"]
            assert media.sniff(data[:64]) == r["mime"] and r["storage_key"] == media._key(r["sha256"], r["mime"])
    total = sum(p.stat().st_size for p in SNAP.rglob("*") if p.is_file())
    assert total <= 40 * 1024 * 1024, total
    # подключения — без состояния проверки, фактов (ключи, аккаунты) и курсоров
    for c in json.loads((SNAP / "collectors.json").read_text(encoding="utf-8")):
        assert (c["status"], c["facts"], c["state"], c["next_run_at"], c["last_error"]) == (
            "unchecked", {}, {}, None, None)
    # кэш 2ГИС — не выгружен, решения — да
    assert all(g["candidates"] == [] and g["status"] != "candidates"
               for g in json.loads((SNAP / "geo_checks.json").read_text(encoding="utf-8")))


def test_committed_snapshot_has_no_secrets():
    assert snapshot.scan(SNAP) == {}
    text_ = "\n".join(p.read_text(encoding="utf-8") for p in SNAP.rglob("*.json"))
    for key in ("access_token", "refresh_token", '"cookie"', '"phone"', '"email"', '"applicant"'):
        assert key not in text_, key


def test_committed_snapshot_api_views():
    """Ответы API для офлайн-режима: ЖК с «Проверкой по рекламе», реклама с анализом ИИ, объекты из рекламы, риск по
    каждому ЖК; картинки — по карте media.json на файлы набора."""
    api = SNAP / "api"
    load = lambda n: json.loads((api / f"{n}.json").read_text(encoding="utf-8"))  # noqa: E731
    cxs, ads, risk, mm = load("complexes"), load("ads"), load("risk"), load("media")
    assert len(cxs) == MAN["tables"]["complexes"]["rows"] and all(c.get("screening") for c in cxs)
    assert len(ads) == MAN["tables"]["ads"]["rows"] and all(a.get("llm") for a in ads)
    assert {r["complexId"] for r in risk} == {c["id"] for c in cxs}
    assert len(load("discovered")) == MAN["tables"]["discovered_objects"]["rows"]
    assert load("stats")["ads"] == len(ads)
    for a in ads:
        for url in [a.get("image")] + [m["url"] for m in a.get("media") or []]:
            if url:
                assert (SNAP / "media" / mm[url.rsplit("/", 1)[1]]).is_file(), url


# ---------- пустая БД получает набор ----------

def test_fresh_db_loads_snapshot(client, monkeypatch, caplog):
    _start_snapshot(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="saq"):
        seed.reset()
    assert any(r.getMessage().startswith("Пустая БД: загружен стартовый набор") for r in caplog.records)
    for name, info in MAN["tables"].items():
        model = next(s.model for s in snapshot.TABLES if s.name == name)
        assert _count(model) == info["rows"], name
    assert _count(models.SourceRecord) == MAN["registry"]["inFile"]
    with SessionLocal() as db:
        assert db.get(models.Meta, demo.FLAG).value == demo.PURGED
        assert db.get(models.Meta, "schema_version").value == models.SCHEMA_VERSION
        assert not demo.import_registry(db, dry_run=True).created  # реестр и карточки ЖК согласованы
    cxs = client.get("/api/complexes").json()
    assert len(cxs) == 23 and all(c["screening"]["status"] in ("ok", "check", "suspect", "unknown") for c in cxs)
    ads = client.get("/api/ads").json()
    assert len(ads) == 230 and all(a["llm"]["status"] in ("done", "skipped") for a in ads)
    assert len(client.get("/api/discovered").json()) == 15 and len(client.get("/api/analysts").json()) == 3
    assert client.get("/api/stats").json()["ads"] == 230
    # файлы — в хранилище, байты те же, что в наборе
    mm = json.loads((SNAP / "api" / "media.json").read_text(encoding="utf-8"))
    some = ads[0]["image"].rsplit("/", 1)[1]
    r = client.get(f"/api/media/{some}")
    assert r.status_code == 200 and r.content == (SNAP / "media" / mm[some]).read_bytes()
    # видео в набор не вошли — их строки «недоступно» с пояснением и ссылкой на оригинал
    with SessionLocal() as db:
        v = db.scalar(select(models.MediaAsset).where(models.MediaAsset.kind == "video",
                                                      models.MediaAsset.error == snapshot.VIDEO_NOTE).limit(1))
        assert v is not None and v.status == "unavailable" and v.storage_key is None and v.origin_url
    # повторный старт ничего не меняет
    events = _count(models.AuditEvent)
    seed.init_db(engine)
    assert _count(models.AuditEvent) == events and _count(models.Ad) == 230


def test_offline_views_match_api(client, monkeypatch):
    """api/*.json набора — то, что отдаёт API нового развёртывания (код риска и «Проверки по рекламе» тот же). Упал —
    перевыгрузите набор: python -m app.snapshot export."""
    _start_snapshot(monkeypatch, files=False)
    seed.reset()
    as_of = MAN["api"]["asOf"]
    for name, path, dated in snapshot.VIEWS:
        if name == "discovered":  # считается «на сегодня» — сравнивается состав, а не оценка
            got = client.get(path).json()
            want = json.loads((SNAP / "api" / f"{name}.json").read_text(encoding="utf-8"))
            assert [o["id"] for o in got] == [o["id"] for o in want]
            continue
        got = client.get(path, params={"asOf": as_of} if dated else None).json()
        want = json.loads((SNAP / "api" / f"{name}.json").read_text(encoding="utf-8"))
        assert got == want, f"api/{name}.json устарел — перевыгрузите набор: python -m app.snapshot export"


def test_snapshot_not_loaded_into_non_empty_db(client, monkeypatch, capsys):
    """Непустая БД: ни старт, ни команда import набор не загружают и ничего не меняют."""
    ads = _count(models.Ad)
    assert ads == 12  # демо-набор тестов
    with SessionLocal() as db:
        with pytest.raises(snapshot.SnapshotError, match="БД не пустая"):
            snapshot.load(db, SNAP)
    assert snapshot.main(["import", "--dir", str(SNAP)]) == 2
    assert "БД не пустая" in capsys.readouterr().err
    _start_snapshot(monkeypatch)
    seed.init_db(engine)
    assert _count(models.Ad) == ads and _count(models.Ad, models.Ad.origin == "collected") == 0


def test_import_cli_into_empty_db(client, monkeypatch, capsys):
    with SessionLocal() as db:
        db.execute(models.Meta.__table__.delete())
        db.commit()
    seed._drop_app_tables(engine)
    assert snapshot.main(["import", "--dir", str(SNAP)]) == 0
    assert "Загружен стартовый набор" in capsys.readouterr().out
    assert _count(models.Ad) == 230 and _count(models.SourceRecord) == 103
    assert snapshot.main(["import", "--dir", str(SNAP)]) == 2  # второй раз — отказ


def test_sequences_continue_after_import(client, monkeypatch):
    """Первичные ключи набора сохранены; новые строки (журнал, сигнал, запуск сбора) получают id за максимальным —
    на PostgreSQL последовательности переставлены."""
    _start_snapshot(monkeypatch, files=False)
    seed.reset()
    ad = client.get("/api/ads").json()[0]["id"]
    last = _max(models.AuditEvent)
    r = client.post(f"/api/ads/{ad}/comments", json={"author": AUTHOR, "text": "Проверка после загрузки набора"})
    assert r.status_code == 201 and r.json()["id"] == last + 1
    before = {m: _max(m) for m in (models.AuditEvent, models.Signal, models.CollectionRun, models.IngestItem,
                                   models.LlmAnalysis, models.DiscoveredObject, models.GeoCheck)}
    with SessionLocal() as db:
        for model in before:
            obj = _new_row(model)
            db.add(obj)
            db.flush()
            assert obj.id == before[model] + 1, model.__tablename__
        db.rollback()


def _max(model) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.max(model.id))) or 0)


def _new_row(model):
    at = models.utcnow()
    return {
        models.AuditEvent: lambda: models.AuditEvent(kind="comment", who=AUTHOR, what="тест", at=at),
        models.Signal: lambda: models.Signal(complex_id="r-orda", complex_name="Orda", status="confirmed", score=0,
                                             zone="green", summary="", model_version="x", created_by=AUTHOR),
        models.CollectionRun: lambda: models.CollectionRun(collector="krisha", trigger="manual"),
        models.IngestItem: lambda: models.IngestItem(platform="test", external_id=uuid.uuid4().hex, collector="intake",
                                                     ad_id="x", content_hash="h" * 64),
        models.LlmAnalysis: lambda: models.LlmAnalysis(ad_id="x", content_hash=uuid.uuid4().hex, model="m",
                                                       prompt_version="p", status="skipped"),
        models.DiscoveredObject: lambda: models.DiscoveredObject(norm_key=uuid.uuid4().hex, name="Тест"),
        models.GeoCheck: lambda: models.GeoCheck(target_kind="complex", target_id="r-orda", query="q", query_norm="q",
                                                 status="not_found", requested_by=AUTHOR),
    }[model]()


def test_no_snapshot_dir_starts_with_registry_only(client, monkeypatch, tmp_path, caplog):
    _start_snapshot(monkeypatch, src=tmp_path / "none")
    with caplog.at_level(logging.WARNING, logger="saq"):
        seed.reset()
    assert any("стартового набора нет" in r.getMessage() for r in caplog.records)
    assert _count(models.Ad) == _count(models.Analyst) == 0
    assert _count(models.Complex, models.Complex.origin == "registry") == 15 and _count(models.SourceRecord) == 103


# ---------- выгрузка и загрузка: круг ----------

def _work_db(client, env) -> dict:
    """БД с тем, что накопилось в работе: собранная публикация с изображением, видео и кадром, снимком источника с
    секретами, анализом ИИ, задания, заявка жителя, токены, квоты, подключение с состоянием, проверки 2ГИС."""
    env(ANTHROPIC_API_KEY=SECRET)
    with SessionLocal() as db:
        demo.import_registry(db)  # карточки ЖК из реестра — как в рабочей БД (иначе загрузка создаст их заново)
        img = media.store_bytes(_jpeg())
        vid = media.store_bytes(mp4_bytes(4096))
        item = models.IngestItem(platform="instagram", external_id="ig-1", collector="instagram", ad_id="a1",
                                 content_hash="c" * 64, analysis_status="done", analyzed_version=1,
                                 raw={"caption": "Бронь 100 000 ₸", "access_token": "EAAB" + "x" * 50,
                                      "user": {"phone": "+77001234567"}, "cookie": "sid=1",
                                      "media_url": "https://cdn.example/p.jpg?oh=00_AbCdEf123456&oe=6720",
                                      "note": f"ключ {SECRET}"})
        db.add(item)
        db.flush()
        db.add(models.IngestVersion(item_id=item.id, version=1, content_hash="c" * 64, text="Бронь", raw=dict(item.raw),
                                    extra={"token": "x" * 20, "location": "Усть-Каменогорск"}))
        image = models.MediaAsset(id=uuid.uuid4().hex, ad_id="a1", item_id=item.id, kind="image", role="cover",
                                  status="downloaded", storage_key=img.storage_key, sha256=img.sha256, size=img.size,
                                  mime=img.mime, meta={"width": 1600, "height": 900, "src": "https://x/y?sig=1"},
                                  ocr_status="done", ocr_text="БРОНЬ", position=0)
        video = models.MediaAsset(id=uuid.uuid4().hex, ad_id="a1", item_id=item.id, kind="video", status="downloaded",
                                  storage_key=vid.storage_key, sha256=vid.sha256, size=vid.size, mime=vid.mime,
                                  origin_url="https://cdn.example/v.mp4", position=1, meta={"duration": 12})
        db.add_all([image, video])
        db.flush()
        db.add(models.MediaAsset(id=uuid.uuid4().hex, ad_id="a1", item_id=item.id, parent_id=video.id, kind="frame",
                                 status="downloaded", storage_key=img.storage_key, sha256=img.sha256, size=img.size,
                                 mime=img.mime, frame_ms=1000))
        db.add(models.LlmAnalysis(ad_id="a1", item_id=item.id, content_hash="d" * 64, model="claude-sonnet-5-5",
                                  prompt_version="saq-llm-2", status="done", result={"summary": "гипотеза"},
                                  request_id="req_0123456789", error=None))
        db.add(models.Job(kind="analyze", ad_id="a1", status="done"))
        db.add(models.WorkerLease(name="scheduler", holder="w1", expires_at=NOW))
        db.add(models.OAuthState(state_hash="s" * 64, provider="olx", created_by="adm", admin_fp="f" * 16,
                                 expires_at=NOW))
        db.add(models.IntegrationToken(provider="olx", access_token="olx-access-" + "z" * 30, refresh_token="r" * 30))
        db.add(models.QuotaCounter(name="anthropic-usd:2026-10-18", count=1234))
        db.add(models.BotSubmission(external_id="bot-1", ad_id="a1",
                                    applicant={"phone": "+77001112233", "name": "Житель"}))
        db.add(models.Collector(code="instagram", enabled=True, config={"accounts": ["@x"]}, status="working",
                                facts={"keyFp": "abcd", "account": "@me"}, state={"cursor": "c1"}, failures=2,
                                next_run_at=NOW, last_error="ошибка", updated_by="Администратор", updated_at=NOW))
        db.add(models.CollectionRun(collector="instagram", trigger="manual", status="failed",
                                    error=f"Ошибка: {SECRET}", notes=[{"at": NOW.isoformat(), "level": "error",
                                                                       "text": f"ключ {SECRET}"}]))
        db.add(models.GeoCheck(target_kind="complex", target_id="c3", query="ул. Протозанова, 81",
                               query_norm="протозанова 81", status="candidates", candidates=[{"id": "1"}],
                               requested_by=AUTHOR))
        db.add(models.GeoCheck(target_kind="complex", target_id="c2", query="ул. Казахстан, 66",
                               query_norm="казахстан 66", status="accepted", candidates=[{"id": "2"}],
                               chosen={"id": "2", "name": "Дом"},
                               applied=True, requested_by=AUTHOR, decided_by=AUTHOR, decided_at=NOW))
        db.commit()
        return {"image": image.id, "video": video.id, "img_sha": img.sha256, "vid_key": vid.storage_key}


def _tables(out: Path) -> dict:
    return {s.name: (out / f"{s.name}.json").read_text(encoding="utf-8") for s in snapshot.TABLES}


def test_export_import_roundtrip(client, env, monkeypatch, tmp_path):
    ids = _work_db(client, env)
    counts = {s.name: _count(s.model) for s in snapshot.TABLES}
    out = tmp_path / "snap"
    man = snapshot.export(engine, out, now=NOW)
    # исключено: служебные таблицы, токены, квоты, заявки жителей, реестр (пересоздаётся из файла)
    for name in snapshot.EXCLUDED:
        assert not (out / f"{name}.json").exists() and name not in man["tables"]
    assert man["schemaVersion"] == models.SCHEMA_VERSION and man["exportedAt"] == NOW.isoformat()
    assert man["registry"]["consistent"]
    rows = {s.name: json.loads((out / f"{s.name}.json").read_text(encoding="utf-8")) for s in snapshot.TABLES}
    assert man["tables"]["media_assets"]["rows"] == counts["media_assets"] - 1  # кадр видео не выгружен
    assert man["tables"]["geo_checks"]["rows"] == counts["geo_checks"] - 1  # проверка без решения — тоже
    for name in ("ads", "complexes", "developers", "audit_events", "llm_analyses", "ingest_items", "analysts"):
        assert man["tables"][name]["rows"] == counts[name], name
    # строки — по первичному ключу, время — ISO в UTC
    ev = rows["audit_events"]
    assert [e["id"] for e in ev] == sorted(e["id"] for e in ev) and ev[0]["at"].endswith("+00:00")
    # изображение — сжато (1024 px), видео — «недоступно» с пояснением, кадра нет
    media_rows = {r["id"]: r for r in rows["media_assets"]}
    im = media_rows[ids["image"]]
    assert im["sha256"] != ids["img_sha"] and im["meta"]["width"] == 1024
    assert im["meta"]["snapshot"]["sourceSha256"] == ids["img_sha"]
    assert "src" not in im["meta"] and (out / "media" / Path(im["storage_key"]).name).is_file()
    v = media_rows[ids["video"]]
    assert (v["status"], v["error"], v["storage_key"], v["sha256"]) == ("unavailable", snapshot.VIDEO_NOTE, None, None)
    assert v["origin_url"] == "https://cdn.example/v.mp4"
    assert not any(r["kind"] == "frame" for r in rows["media_assets"])
    assert [p.name for p in (out / "media").iterdir()] == [Path(im["storage_key"]).name]
    # снимок источника очищен, подключение — без состояния, ответы API — по набору
    raw = rows["ingest_items"][-1]["raw"]
    assert set(raw) == {"caption", "media_url", "note"} and raw["media_url"] == "https://cdn.example/p.jpg"
    assert SECRET not in json.dumps(rows, ensure_ascii=False)
    c = next(r for r in rows["collectors"] if r["code"] == "instagram")
    assert (c["enabled"], c["config"], c["status"], c["facts"], c["state"], c["failures"], c["next_run_at"]) == (
        True, {"accounts": ["@x"]}, "unchecked", {}, {}, 0, None)
    assert all(r["request_id"] is None for r in rows["llm_analyses"])
    assert rows["geo_checks"][0]["candidates"] == [] and rows["geo_checks"][0]["chosen"]["name"] == "Дом"
    assert snapshot.scan(out, [SECRET]) == {}
    api = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (out / "api").iterdir()}
    assert len(api["ads"]) == counts["ads"] and len(api["complexes"]) == counts["complexes"]
    assert api["media"] == {ids["image"]: Path(im["storage_key"]).name}

    # загрузка в новую (пустую) БД и повторная выгрузка — те же строки и файлы
    _start_snapshot(monkeypatch, src=out)
    seed.reset()
    assert _count(models.Job) == _count(models.BotSubmission) == _count(models.IntegrationToken) == 0
    assert _count(models.OAuthState) == _count(models.QuotaCounter) == _count(models.WorkerLease) == 0
    assert _count(models.SourceRecord) == 103
    r = client.get(f"/api/media/{ids['image']}")
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == im["sha256"]
    out2 = tmp_path / "snap2"
    snapshot.export(engine, out2, now=NOW, views=False)
    assert _tables(out2) == _tables(out)
    assert sorted(p.name for p in (out2 / "media").iterdir()) == sorted(p.name for p in (out / "media").iterdir())


def test_export_aborts_on_leak_and_keeps_previous(client, tmp_path):
    """Секрет там, где очистка его не видит (текст публикации), — выгрузка прерывается, прежний каталог не тронут."""
    out = tmp_path / "snap"
    snapshot.export(engine, out, now=NOW, views=False)
    before = (out / "ads.json").read_text(encoding="utf-8")
    with SessionLocal() as db:
        db.get(models.Ad, "a1").text = f"реклама и случайно ключ {SECRET}"
        db.commit()
    with pytest.raises(snapshot.SnapshotError, match="похожее на секреты"):
        snapshot.export(engine, out, now=NOW, views=False)
    assert (out / "ads.json").read_text(encoding="utf-8") == before
    assert not [p for p in tmp_path.iterdir() if p.name != "snap"]  # временный каталог убран


# ---------- набор прежней и неизвестной версии схемы ----------

def _old_snapshot(tmp_path: Path) -> Path:
    """Набор демо-БД, приведённый к версии 2026.10-discovery-1: без столбцов шага verification-1."""
    out = tmp_path / "old"
    snapshot.export(engine, out, now=NOW, views=False)
    man = json.loads((out / snapshot.MANIFEST).read_text(encoding="utf-8"))
    man["schemaVersion"] = "2026.10-discovery-1"
    for name, cols in (("complexes", ("published_list_check", "appeals_check")),
                       ("appeals", ("comment", "created_by", "created_at", "decided_by", "decision_comment"))):
        rows = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
        rows = [{k: v for k, v in r.items() if k not in cols} for r in rows]
        (out / f"{name}.json").write_text(snapshot.dump(rows), encoding="utf-8")
    (out / snapshot.MANIFEST).write_text(snapshot.dump(man), encoding="utf-8")
    return out


def test_older_schema_snapshot_loads_with_additive_columns(client, monkeypatch, tmp_path):
    with SessionLocal() as db:
        c = db.get(models.Complex, "c1")
        c.appeals_check = {"source": "eOtinish", "checkedAt": "2026-10-17", "checkedBy": AUTHOR}
        db.commit()
    out = _old_snapshot(tmp_path)
    _start_snapshot(monkeypatch, src=out)
    seed.reset()
    assert _count(models.Complex) == 14 + 15 and _count(models.Appeal) == 5
    with SessionLocal() as db:
        assert db.get(models.Complex, "c1").appeals_check is None  # столбца в наборе не было — NULL, как после migrate


def test_unknown_or_newer_snapshot_refused(client, tmp_path):
    out = _old_snapshot(tmp_path)
    man = json.loads((out / snapshot.MANIFEST).read_text(encoding="utf-8"))
    seed._drop_app_tables(engine)
    Base.metadata.create_all(engine)
    for version in ("2027.01-future-1", None):
        (out / snapshot.MANIFEST).write_text(snapshot.dump({**man, "schemaVersion": version}), encoding="utf-8")
        with SessionLocal() as db, pytest.raises(snapshot.SnapshotError, match="выгрузите его заново"):
            snapshot.load(db, out)
    man["schemaVersion"] = models.SCHEMA_VERSION
    (out / snapshot.MANIFEST).write_text(snapshot.dump(man), encoding="utf-8")
    rows = json.loads((out / "ads.json").read_text(encoding="utf-8"))
    (out / "ads.json").write_text(snapshot.dump([{**r, "future_column": 1} for r in rows]), encoding="utf-8")
    with SessionLocal() as db, pytest.raises(snapshot.SnapshotError, match="future_column"):
        snapshot.load(db, out)
    shutil.rmtree(out / "media")
    (out / "media").mkdir()
    with SessionLocal() as db:
        assert snapshot.is_empty(db)
    seed.reset()  # БД тестов — снова демо-набор


def test_registry_check_reports_drift(client):
    with SessionLocal() as db:
        rec = db.scalar(select(models.SourceRecord).where(models.SourceRecord.key == "satti-city"))
        rec.address = "другой адрес"
        db.commit()
    with engine.connect() as conn:
        rep = snapshot.registry_check(conn)
    assert not rep["consistent"] and len(rep["differ"]) == 1 and rep["inFile"] == rep["inDb"] == 103
    assert importer.REGISTRY.name == rep["file"]
