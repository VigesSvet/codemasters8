"""Хранилище файлов публикаций: изображения и видео по содержимому (SHA-256), под MEDIA_ROOT.

Ключ хранилища — «ab/cd/<sha256>.<ext>»: одинаковые файлы хранятся один раз, а запись MediaAsset ссылается на ключ.
Тип файла определяется по сигнатуре (первым байтам), а не по заявленному Content-Type или имени: HTML, SVG, PDF и
прочее — 415. Пределы размера — MEDIA_MAX_IMAGE_MB / MEDIA_MAX_VIDEO_MB (413), свободного места —
MEDIA_MIN_FREE_MB (507). Временные файлы загрузок — в MEDIA_ROOT/.tmp (та же файловая система, перенос — атомарный
os.replace). Файлы отдаёт GET /api/media/{id}; загруженные вручную и так и не привязанные к публикации удаляются
через сутки (purge_unattached), файлы без единой записи MediaAsset (запись файла прошла, приём публикации — нет) — через
сутки после последнего использования (purge_orphans).
"""
import errno
import hashlib
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .settings import get_settings

log = logging.getLogger("saq.media")

MB = 1024 * 1024
# MIME по сигнатуре → расширение файла в хранилище
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
           "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}
IMAGE_TYPES = frozenset(m for m in ALLOWED if m.startswith("image/"))
VIDEO_TYPES = frozenset(m for m in ALLOWED if m.startswith("video/"))
TYPE_NAMES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF", "video/mp4": "MP4",
              "video/quicktime": "MOV", "video/webm": "WEBM"}
# Основные бренды контейнера ISO BMFF, которые считаются MP4. HEIC/AVIF (ai.ocr.HEIF_BRANDS), 3GP и прочие — не MP4
MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash", b"mmp4",
              b"MSNV"}
UPLOAD_MAX = 10 * MB  # POST /api/media — скриншот из формы ручного ввода; снимок площадки (app.verification) — так же
# Ручные загрузки: скриншот формы (upload — непривязанный удаляется через сутки) и снимок площадки ЖК (site — хранится)
MANUAL_ORIGINS = ("upload", "site", "gallery")  # gallery — фото объекта от оператора (app.gallery)
UPLOAD_HOURLY_FILES = 50  # непривязанных загрузок за час — больше нельзя (429)
UPLOAD_HOURLY_BYTES = 200 * MB
UPLOAD_TTL = timedelta(hours=24)  # непривязанная загрузка удаляется через сутки
BOT_MAX_FILE = 20 * MB
BOT_MAX_FILES = 10
BOT_MAX_BODY = 60 * MB
INTAKE_JSON_MAX = 2 * MB
PUBLICATION_BYTES_MAX = 400 * MB  # все файлы одной публикации, загружаемые анализом (приём через API)
TEMP_TTL_S = 24 * 3600
ORPHAN_TTL_S = 24 * 3600  # файл без записи MediaAsset удаляется через сутки после последней записи/использования
_HEX2 = re.compile(r"^[0-9a-f]{2}$")
_STORED_NAME = re.compile(r"^([0-9a-f]{64})(" + "|".join(re.escape(e) for e in sorted(set(ALLOWED.values()))) + r")$")
HEAD = 64  # байт для определения типа
_CHUNK = MB

NO_SPACE = "Мало места для файлов на сервере — файл не сохранён"
TOO_MANY_UPLOADS = "Слишком много загрузок — попробуйте позже"


class MediaRejected(Exception):
    """Файл не принят: status — HTTP-код (413 размер, 415 тип, 422 пустой, 429 частота, 507 место),
    detail — по-русски."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class Stored:
    """Сохранённый файл: ключ хранилища, SHA-256, размер в байтах, MIME по сигнатуре."""
    storage_key: str
    sha256: str
    size: int
    mime: str


# ---------- каталоги ----------

def root() -> Path:
    """Корень хранилища (MEDIA_ROOT); создаётся при первом обращении."""
    p = Path(get_settings().media_root)
    p.mkdir(parents=True, exist_ok=True, mode=0o755)
    return p


def tmp_dir() -> Path:
    """Временные файлы загрузок: в том же корне (та же файловая система), доступ только владельцу (0700)."""
    p = root() / ".tmp"
    if not p.is_dir():
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(p, 0o700)
    return p


# ---------- тип и пределы ----------

def sniff(head: bytes) -> Optional[str]:
    """MIME по первым байтам файла или None (не поддерживается): JPEG, PNG, GIF, WEBP; MP4/MOV — по основному бренду
    ftyp (HEIC/AVIF/3GP и прочие бренды — None); WEBM — EBML с «webm» в первых 64 байтах."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in MP4_BRANDS:
            return "video/mp4"
        if brand == b"qt  ":
            return "video/quicktime"
        return None
    if head.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in head[:HEAD]:
        return "video/webm"
    return None


def limit_for(mime: str) -> int:
    """Предел размера в байтах: видео — MEDIA_MAX_VIDEO_MB, остальное (изображения, кадры) — MEDIA_MAX_IMAGE_MB.
    Принимает MIME («video/mp4») или вид файла («video», «image»)."""
    s = get_settings()
    return (s.media_max_video_mb if (mime or "").startswith("video") else s.media_max_image_mb) * MB


def _mb(nbytes: int) -> str:
    return f"{nbytes / MB:g}"


def _unsupported(allowed: Iterable[str]) -> MediaRejected:
    names = [TYPE_NAMES[m] for m in ALLOWED if m in set(allowed)]
    listed = ", ".join(names[:-1]) + f" или {names[-1]}" if len(names) > 1 else "".join(names)
    return MediaRejected(415, f"Неподдерживаемый тип файла — нужен {listed}")


def _check(head: bytes, size: int, allowed: Iterable[str]) -> str:
    if size == 0:
        raise MediaRejected(422, "Файл пустой")
    mime = sniff(head)
    allowed = set(allowed)
    if mime is None or mime not in allowed:
        raise _unsupported(allowed)
    limit = limit_for(mime)
    if size > limit:
        raise MediaRejected(413, f"Файл больше {_mb(limit)} МБ")
    return mime


def ensure_space(nbytes: int = 0) -> None:
    """После записи nbytes байт на диске хранилища должно остаться не меньше MEDIA_MIN_FREE_MB, иначе 507."""
    free = shutil.disk_usage(root()).free
    if free - nbytes < get_settings().media_min_free_mb * MB:
        raise MediaRejected(507, NO_SPACE)


def _key(sha: str, mime: str) -> str:
    return f"{sha[:2]}/{sha[2:4]}/{sha}{ALLOWED[mime]}"


def path_of(storage_key: str) -> Path:
    """Путь файла по ключу хранилища; ключ, ведущий за пределы MEDIA_ROOT («../x», абсолютный путь), — ValueError."""
    if not storage_key or "\x00" in storage_key:
        raise ValueError("пустой ключ хранилища")
    base = root().resolve()
    p = (base / storage_key).resolve()
    if p == base or base not in p.parents:
        raise ValueError(f"ключ хранилища вне MEDIA_ROOT: {storage_key!r}")
    return p


def _reuse(final: Path) -> bool:
    """Такой файл уже есть — он и остаётся; время изменения обновляется: уборка файлов без записей (purge_orphans)
    отсчитывает срок от последнего использования, а не от первой записи. Файла нет — False (его нужно записать)."""
    try:
        os.utime(final)
    except FileNotFoundError:
        return False
    except OSError:
        return final.exists()  # время сменить нельзя (чужой владелец) — файл используется как есть
    return True


def _final(sha: str, mime: str) -> tuple[str, Path]:
    key = _key(sha, mime)
    final = path_of(key)
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    return key, final


# ---------- запись ----------

def store_bytes(data: bytes, *, allowed: Iterable[str] = ALLOWED) -> Stored:
    """Байты → хранилище (тип по сигнатуре, предел размера, свободное место). Такой же файл уже есть — он и остаётся."""
    mime = _check(data[:HEAD], len(data), allowed)
    sha = hashlib.sha256(data).hexdigest()
    key, final = _final(sha, mime)
    if not _reuse(final):
        ensure_space(len(data))
        fd, tmp = tempfile.mkstemp(dir=tmp_dir(), prefix="put-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, final)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return Stored(key, sha, len(data), mime)


def _hash_file(path: Path) -> tuple[str, bytes]:
    h, head = hashlib.sha256(), b""
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            if len(head) < HEAD:
                head += chunk[:HEAD - len(head)]
            h.update(chunk)
    return h.hexdigest(), head


def _move(tmp: Path, final: Path) -> None:
    """os.replace; другой раздел (EXDEV) — копия во временный файл хранилища и замена оттуда."""
    try:
        os.replace(tmp, final)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    fd, staged = tempfile.mkstemp(dir=tmp_dir(), prefix="mv-")
    os.close(fd)
    try:
        shutil.copyfile(tmp, staged)
        os.chmod(staged, 0o644)
        os.replace(staged, final)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise
    tmp.unlink(missing_ok=True)


def store_file(tmp: Path, *, allowed: Iterable[str] = ALLOWED) -> Stored:
    """Готовый файл (загрузка во временный файл) → хранилище. Файл забирается: после вызова его нет на прежнем месте —
    он перенесён в хранилище или удалён (такой уже есть, тип или размер не подходит)."""
    tmp = Path(tmp)
    try:
        size = tmp.stat().st_size
        with open(tmp, "rb") as f:
            head = f.read(HEAD)
        mime = _check(head, size, allowed)
        sha, _ = _hash_file(tmp)
        key, final = _final(sha, mime)
        if _reuse(final):
            tmp.unlink(missing_ok=True)
        else:
            ensure_space(0)  # байты уже на диске хранилища (временный файл)
            os.chmod(tmp, 0o644)
            _move(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Stored(key, sha, size, mime)


def read_bytes(asset) -> bytes:
    """Содержимое файла записи MediaAsset (по storage_key)."""
    if not getattr(asset, "storage_key", None):
        raise FileNotFoundError(f"у файла {getattr(asset, 'id', '?')} нет содержимого в хранилище")
    return path_of(asset.storage_key).read_bytes()


# ---------- ручные загрузки и уборка ----------

def check_upload_budget(db: Session, now: datetime, size: int = 0) -> None:
    """Частота ручных загрузок (POST /api/media и снимки площадки POST /api/complexes/{id}/photos — общий счёт): за
    последний час не больше UPLOAD_HOURLY_FILES файлов и UPLOAD_HOURLY_BYTES байт вместе с новым, иначе 429. Считаются все загрузки часа — и уже привязанные к публикации:
    иначе привязка через POST /api/ads (image="/api/media/{id}") выводила бы файл из счёта и хранилище росло без
    предела, пока не кончится место и для собранных файлов."""
    A = models.MediaAsset
    since = models.as_utc(now - timedelta(hours=1))
    files, total = db.execute(select(func.count(A.id), func.coalesce(func.sum(A.size), 0))
                              .where(A.origin_ref.in_(MANUAL_ORIGINS), A.created_at >= since)).one()
    if files + 1 > UPLOAD_HOURLY_FILES or (total or 0) + size > UPLOAD_HOURLY_BYTES:
        raise MediaRejected(429, TOO_MANY_UPLOADS)


def _unlink_unreferenced(db: Session, files: dict[str, str]) -> None:
    """Файлы (sha256 → ключ хранилища), на которые больше не ссылается ни одна запись, удаляются с диска."""
    A = models.MediaAsset
    for sha, key in files.items():
        if db.scalar(select(func.count(A.id)).where(A.sha256 == sha)):
            continue
        try:
            path_of(key).unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            log.warning("Файл хранилища %s не удалён: %s", key, type(e).__name__)


def purge_unattached(db: Session, now: datetime) -> int:
    """Ручные загрузки (origin_ref = upload), так и не привязанные к публикации за сутки, удаляются вместе с файлом,
    если на файл не ссылается другая запись. Фиксирует транзакцию сам: файлы удаляются только после commit."""
    A = models.MediaAsset
    cutoff = models.as_utc(now - UPLOAD_TTL)
    rows = list(db.scalars(select(A).where(A.ad_id.is_(None), A.origin_ref == "upload", A.created_at < cutoff)))
    if not rows:
        return 0
    files = {a.sha256: a.storage_key for a in rows if a.sha256 and a.storage_key}
    for a in rows:
        db.delete(a)
    db.commit()
    _unlink_unreferenced(db, files)
    return len(rows)


def _stored_files(cutoff: float) -> dict[str, list[Path]]:
    """Файлы хранилища вида ab/cd/<sha256>.<ext> с временем изменения раньше cutoff: sha256 → пути. Всё остальное
    (.tmp, посторонние имена, ссылки) не трогается."""
    def dirs(path) -> list[os.DirEntry]:
        with os.scandir(path) as it:
            return [d for d in it if _HEX2.match(d.name) and d.is_dir(follow_symlinks=False)]

    out: dict[str, list[Path]] = {}
    for d1 in dirs(root()):
        for d2 in dirs(d1.path):
            with os.scandir(d2.path) as it:
                for f in it:
                    m = _STORED_NAME.match(f.name)
                    if not m or not m.group(1).startswith(d1.name + d2.name) or not f.is_file(follow_symlinks=False):
                        continue
                    try:
                        if f.stat(follow_symlinks=False).st_mtime < cutoff:
                            out.setdefault(m.group(1), []).append(Path(f.path))
                    except OSError:
                        continue
    return out


def purge_orphans(db: Session, max_age_s: float = ORPHAN_TTL_S) -> int:
    """Файлы хранилища, на которые не ссылается ни одна запись MediaAsset (файл записан, а приём публикации не прошёл:
    сбой БД, отказ, остановка воркера), удаляются, если не менялись и не использовались max_age_s секунд (store_*
    обновляет время файла при повторном использовании — срок покрывает и идущие запуски). Таблица media_assets пуста —
    уборка пропускается: похоже на новую или чужую БД при том же MEDIA_ROOT. Транзакцию чтения закрывает до удаления
    файлов. Возвращает число удалённых файлов."""
    A = models.MediaAsset
    if db.scalar(select(A.id).limit(1)) is None:
        db.commit()
        return 0
    cutoff = time.time() - max_age_s
    old = _stored_files(cutoff)
    shas, used = list(old), set()
    for i in range(0, len(shas), 500):
        used.update(db.scalars(select(A.sha256).where(A.sha256.in_(shas[i:i + 500]))))
    db.commit()
    n = 0
    for sha in shas:
        if sha in used:
            continue
        for path in old[sha]:
            try:
                if path.lstat().st_mtime >= cutoff:
                    continue  # файл только что использован повторно
                path.unlink()
                n += 1
            except FileNotFoundError:
                continue
            except OSError as e:
                log.warning("Файл хранилища %s без записи не удалён: %s", path.name, type(e).__name__)
    return n


def purge_temp(max_age_s: float = TEMP_TTL_S) -> int:
    """Брошенные временные файлы и каталоги кадров в MEDIA_ROOT/.tmp старше max_age_s секунд удаляются."""
    cutoff = time.time() - max_age_s
    n = 0
    for p in tmp_dir().iterdir():
        try:
            if p.lstat().st_mtime >= cutoff:
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)
            n += 1
        except OSError:
            continue
    return n
