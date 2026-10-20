"""Хранилище файлов публикаций: изображения и видео по содержимому(SHA-256), под MEDIA_ROOT.

Ключ хранилища - "ab/cd/<sha256>.<ext>": одинаковые файлы хранятся один раз, а запись MediaAsset ссылается на ключ.
Тип файла определяется по сигнатуре(первым байтам), а не по заявленному Content-Type или имени: HTML, SVG, PDF и
прочее - 415. Пределы размера - MEDIA_MAX_IMAGE_MB / MEDIA_MAX_VIDEO_MB(413), свободного места -
MEDIA_MIN_FREE_MB(507; ручной загрузке - на MANUAL_RESERVE_MB больше). Временные файлы загрузок - в MEDIA_ROOT/.tmp(та
же файловая система, перенос - атомарный: жёсткая ссылка os.link, которая не заменяет файл параллельного вызова, без её
поддержки - os.replace). Файлы отдаёт GET /api/media/{id}; загруженные вручную и так и не привязанные к публикации
удаляются через сутки(purge_unattached), файлы без единой записи MediaAsset(запись файла прошла, приём публикации -
нет) - через сутки после последнего использования(purge_orphans). Изображения декодируются через open_image: только
форматы хранилища и предел пикселей. Ручные загрузки ограничены за час и за сутки счётчиками
quota_counters(reserve_upload): удаление записи их не уменьшает.
"""
import errno
import hashlib
import io
import logging
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .settings import get_settings

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger("saq.media")

MB = 1024 * 1024
# MIME по сигнатуре → расширение файла в хранилище
ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
           "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}
IMAGE_TYPES = frozenset(m for m in ALLOWED if m.startswith("image/"))
VIDEO_TYPES = frozenset(m for m in ALLOWED if m.startswith("video/"))
TYPE_NAMES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF", "video/mp4": "MP4",
              "video/quicktime": "MOV", "video/webm": "WEBM"}
# Декодеры Pillow для изображений хранилища(open_image): имена форматов Pillow совпадают с TYPE_NAMES
IMAGE_FORMATS = tuple(TYPE_NAMES[m] for m in ALLOWED if m in IMAGE_TYPES)
# Предел open_image по умолчанию: с запасом для фото камер телефонов(48-50 Мп), декодированное RGBA - до ~256 МБ памяти
IMAGE_MAX_PIXELS = 64_000_000
# Основные бренды контейнера ISO BMFF, которые считаются MP4. HEIC/AVIF(ai.ocr.HEIF_BRANDS), 3GP и прочие - не MP4
MP4_BRANDS = {b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V ", b"dash", b"mmp4",
              b"MSNV"}
UPLOAD_MAX = 10 * MB  # POST /api/media - скриншот из формы ручного ввода; снимок площадки(app.verification) - так же
# Ручные загрузки(accept_image_upload; origin_ref записи): скриншот формы(upload - непривязанный удаляется через сутки),
# снимок площадки ЖК(site) и фото объекта от оператора(gallery, app.gallery) - хранятся
UPLOAD_HOURLY_FILES = 50  # ручных загрузок за час, всех вместе - больше нельзя(429)
UPLOAD_HOURLY_BYTES = 200 * MB
# и за сутки UTC: снимки площадки и фото объекта хранятся без срока, а часовой предел каждый час подряд - 4,8 ГБ в сутки
UPLOAD_DAILY_FILES = 300
UPLOAD_DAILY_BYTES = 1024 * MB  # счётчик quota_counters.count - Integer: предел меньше 2**31
# Ручной загрузке нужно свободного места MEDIA_MIN_FREE_MB + столько: последний запас - собранным файлам и приёму
# публикаций, которые иначе получали бы 507 из-за заполненного вручную диска
MANUAL_RESERVE_MB = 1024
UPLOAD_TTL = timedelta(hours=24)  # непривязанная загрузка удаляется через сутки
BOT_MAX_FILE = 20 * MB
BOT_MAX_FILES = 10
BOT_MAX_BODY = 60 * MB
INTAKE_JSON_MAX = 2 * MB
PUBLICATION_BYTES_MAX = 400 * MB  # все файлы одной публикации, загружаемые анализом(приём через API)
TEMP_TTL_S = 24 * 3600
ORPHAN_TTL_S = 24 * 3600  # файл без записи MediaAsset удаляется через сутки после последней записи/использования
_HEX2 = re.compile(r"^[0-9a-f]{2}$")
_STORED_NAME = re.compile(r"^([0-9a-f]{64})(" + "|".join(re.escape(e) for e in sorted(set(ALLOWED.values()))) + r")$")
HEAD = 64  # байт для определения типа

NO_SPACE = "Мало места для файлов на сервере — файл не сохранён"
TOO_MANY_UPLOADS = "Слишком много загрузок — попробуйте позже"


class MediaRejected(Exception):
    """Файл не принят: status - HTTP-код(413 размер, 415 тип, 422 пустой, 429 частота, 507 место),
    detail - по-русски."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class Stored:
    """Сохранённый файл: ключ хранилища, SHA-256, размер в байтах, MIME по сигнатуре; created - файл создан этим
    вызовом(такого в хранилище не было и его не записал параллельный вызов) - в сравнении не участвует."""
    storage_key: str
    sha256: str
    size: int
    mime: str
    created: bool = field(default=False, compare=False)


# ---------- каталоги ----------

def root() -> Path:
    """Корень хранилища(MEDIA_ROOT); создаётся при первом обращении."""
    p = Path(get_settings().media_root)
    p.mkdir(parents=True, exist_ok=True, mode=0o755)
    return p


def tmp_dir() -> Path:
    """Временные файлы загрузок: в том же корне(та же файловая система), доступ только владельцу(0700)."""
    p = root() / ".tmp"
    if not p.is_dir():
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(p, 0o700)
    return p


# ---------- тип и пределы ----------

def sniff(head: bytes) -> str | None:
    """MIME по первым байтам файла или None(не поддерживается): JPEG, PNG, GIF, WEBP; MP4/MOV - по основному бренду
    ftyp(HEIC/AVIF/3GP и прочие бренды - None); WEBM - EBML с "webm" в первых 64 байтах."""
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
    """Предел размера в байтах: видео - MEDIA_MAX_VIDEO_MB, остальное(изображения, кадры) - MEDIA_MAX_IMAGE_MB.
    Принимает MIME("video/mp4") или вид файла("video", "image")."""
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


def ensure_space(nbytes: int = 0, *, reserve_mb: int = 0) -> None:
    """После записи nbytes байт на диске хранилища должно остаться не меньше MEDIA_MIN_FREE_MB + reserve_mb мегабайт,
    иначе 507."""
    free = shutil.disk_usage(root()).free
    if free - nbytes < (get_settings().media_min_free_mb + reserve_mb) * MB:
        raise MediaRejected(507, NO_SPACE)


def storage_key(sha: str, mime: str) -> str:
    """Ключ хранилища файла по SHA-256 и MIME(из ALLOWED): "ab/cd/<sha256>.<ext>"."""
    return f"{sha[:2]}/{sha[2:4]}/{sha}{ALLOWED[mime]}"


def path_of(storage_key: str) -> Path:
    """Путь файла по ключу хранилища; ключ, ведущий за пределы MEDIA_ROOT("../x", абсолютный путь), - ValueError."""
    if not storage_key or "\x00" in storage_key:
        raise ValueError("пустой ключ хранилища")
    base = root().resolve()
    p = (base / storage_key).resolve()
    if p == base or base not in p.parents:
        raise ValueError(f"ключ хранилища вне MEDIA_ROOT: {storage_key!r}")
    return p


def _reuse(final: Path, size: int) -> bool:
    """Такой файл уже есть - он и остаётся; время изменения обновляется: уборка файлов без записей(purge_orphans)
    отсчитывает срок от последнего использования, а не от первой записи. Файла нет или его размер не тот(обрезан
    сбоем диска) - False: файл нужно записать заново, иначе хранилище вечно отдавало бы испорченную копию."""
    try:
        os.utime(final)
    except FileNotFoundError:
        return False
    except OSError:
        pass  # время сменить нельзя(чужой владелец) - файл используется как есть, если он цел
    try:
        return final.stat().st_size == size
    except OSError:
        return False


def _final(sha: str, mime: str) -> tuple[str, Path]:
    key = storage_key(sha, mime)
    final = path_of(key)
    final.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    return key, final


# ---------- запись ----------

# os.link не поддерживается файловой системой хранилища - файл ставится заменой(os.replace)
_NO_LINK = frozenset({errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.ENOSYS})


def _place(tmp: Path, final: Path, size: int) -> bool:
    """Готовый временный файл(та же файловая система) → final; True - файл создан этим вызовом. Жёсткая ссылка не
    заменяет существующий файл: если параллельный вызов с теми же байтами успел записать свой, он и остаётся(False) -
    так только один из них считает файл своим и может удалить его при неудаче. Существующий файл не того
    размера(обрезан сбоем диска) заменяется целым - он уже чей-то, поэтому тоже False. Временный файл остаётся -
    удаляет вызывающий."""
    try:
        os.link(tmp, final, follow_symlinks=False)
        return True
    except FileExistsError:
        if not _reuse(final, size):
            os.replace(tmp, final)
        return False
    except OSError as e:
        if e.errno not in _NO_LINK:
            raise
    # без жёстких ссылок создание не исключительное: файл не считается своим - лишний уберёт purge_orphans
    os.replace(tmp, final)
    return False


def store_bytes(data: bytes, *, allowed: Iterable[str] = ALLOWED, reserve_mb: int = 0) -> Stored:
    """Байты → хранилище(тип по сигнатуре, предел размера, свободное место - ensure_space с запасом reserve_mb). Такой
    же файл уже есть - он и остаётся."""
    mime = _check(data[:HEAD], len(data), allowed)
    sha = hashlib.sha256(data).hexdigest()
    key, final = _final(sha, mime)
    created = False
    if not _reuse(final, len(data)):
        ensure_space(len(data), reserve_mb=reserve_mb)
        fd, tmp = tempfile.mkstemp(dir=tmp_dir(), prefix="put-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o644)
            created = _place(Path(tmp), final, len(data))
        finally:
            Path(tmp).unlink(missing_ok=True)
    return Stored(key, sha, len(data), mime, created)


def _move(tmp: Path, final: Path, size: int) -> bool:
    """Временный файл → final(_place, True - создан этим вызовом); другой раздел(EXDEV) - через копию во временный
    файл хранилища. Исходный временный файл удаляется."""
    try:
        created = _place(tmp, final, size)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        fd, staged = tempfile.mkstemp(dir=tmp_dir(), prefix="mv-")
        os.close(fd)
        try:
            shutil.copyfile(tmp, staged)
            os.chmod(staged, 0o644)
            created = _place(Path(staged), final, size)
        finally:
            Path(staged).unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)
    return created


def store_file(tmp: Path, *, allowed: Iterable[str] = ALLOWED) -> Stored:
    """Готовый файл(загрузка во временный файл) → хранилище. Файл забирается: после вызова его нет на прежнем месте -
    он перенесён в хранилище или удалён(такой уже есть, тип или размер не подходит). Символьная ссылка и не обычный
    файл(FIFO, устройство) не принимаются - 415: в хранилище попадает только сам файл, а не ссылка на чужой."""
    tmp = Path(tmp)
    try:
        # O_NOFOLLOW - ссылка не открывается(ELOOP), O_NONBLOCK - открытие FIFO не ждёт писателя
        fd = os.open(tmp, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise _unsupported(allowed)
            size = st.st_size
            mime = _check(f.read(HEAD), size, allowed)
            f.seek(0)
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        key, final = _final(sha, mime)
        created = False
        if _reuse(final, size):
            tmp.unlink(missing_ok=True)
        else:
            ensure_space(0)  # байты уже на диске хранилища(временный файл)
            os.chmod(tmp, 0o644)
            created = _move(tmp, final, size)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        if e.errno == errno.ELOOP:
            raise _unsupported(allowed) from None
        raise
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return Stored(key, sha, size, mime, created)


# ---------- изображения ----------

def _too_large(max_pixels: int, size: tuple[int, int] | None = None) -> MediaRejected:
    dims = f" ({size[0]}×{size[1]} пикселей)" if size else ""
    return MediaRejected(413, f"Изображение больше {max_pixels / 1_000_000:g} Мп{dims}")


def open_image(src: bytes | str | os.PathLike, *, max_pixels: int = IMAGE_MAX_PIXELS,
               formats: Iterable[str] = IMAGE_FORMATS,
               draft: tuple[str, tuple[int, int]] | None = None) -> "Image.Image":
    """Изображение из байтов или файла, декодированное с пределом(как ai.ocr.load): только декодеры formats - без
    перебора всех форматов Pillow; число пикселей по заголовку проверяется до декодирования - больше max_pixels -
    MediaRejected(413), как и общий предел Pillow(DecompressionBombError). draft(режим, размер) - уменьшение JPEG при
    декодировании(Image.draft) до проверки размера. Не тот формат или битый файл - исключение
    Pillow(UnidentifiedImageError, OSError и др.). Изображение закрывает вызывающий(with)."""
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(src) if isinstance(src, bytes) else src, formats=list(formats))
    except Image.DecompressionBombError:
        raise _too_large(max_pixels) from None
    try:
        if draft is not None:
            im.draft(*draft)
        if im.width * im.height > max_pixels:
            raise _too_large(max_pixels, im.size)
        im.load()
    except Image.DecompressionBombError:
        im.close()
        raise _too_large(max_pixels) from None
    except BaseException:
        im.close()
        raise
    return im


# ---------- ручные загрузки и уборка ----------

def _upload_quotas(now: datetime, size: int) -> list[tuple[str, int, int]]:
    """Счётчики ручных загрузок(quota_counters): (имя, предел, расход) - файлы и байты за час и за сутки UTC."""
    t = models.as_utc(now)
    hour, day = f"{t:%Y%m%d%H}", f"{t:%Y%m%d}"
    return [(f"upload-files:{hour}", UPLOAD_HOURLY_FILES, 1), (f"upload-bytes:{hour}", UPLOAD_HOURLY_BYTES, size),
            (f"upload-files-day:{day}", UPLOAD_DAILY_FILES, 1), (f"upload-bytes-day:{day}", UPLOAD_DAILY_BYTES, size)]


def reserve_upload(now: datetime, size: int) -> list[tuple[str, int]]:
    """Лимит ручных загрузок(POST /api/media, снимок площадки, фото объекта - общий счёт): за час не больше
    UPLOAD_HOURLY_FILES файлов и UPLOAD_HOURLY_BYTES байт, за сутки - UPLOAD_DAILY_FILES и UPLOAD_DAILY_BYTES, иначе
    MediaRejected(429). Расход учитывается атомарно(integrations.service.reserve_quota) до записи файла: параллельные
    загрузки не проходят все разом, а удаление записи(фото оператора) и привязка к публикации расход не возвращают -
    иначе загрузка с удалением обходила бы лимит, пока место не кончится и для собранных файлов. Не хватило одного
    счётчика - уже учтённые возвращаются. Возвращает учтённое(имя, расход) - для release_upload, если файл не
    записан."""
    from .integrations import service  # service импортирует модули приёма, которые импортируют media

    taken: list[tuple[str, int]] = []
    for name, limit, amount in _upload_quotas(now, size):
        if not service.reserve_quota(name, limit, now, amount=amount):
            release_upload(taken, now)
            raise MediaRejected(429, TOO_MANY_UPLOADS)
        taken.append((name, amount))
    return taken


def release_upload(taken: list[tuple[str, int]], now: datetime) -> None:
    """Вернуть учтённый reserve_upload расход: файл не записан(отказ хранилища)."""
    from .integrations import service

    for name, amount in taken:
        service.adjust_quota(name, -amount, now)


def accept_image_upload(db: Session, file: BinaryIO, now: datetime) -> Stored:
    """Ручная загрузка изображения(скриншот POST /api/media, снимок площадки, фото объекта) → хранилище: не больше
    UPLOAD_MAX(413), только изображения по сигнатуре(415, пустой - 422), лимит ручных загрузок за час и сутки(429;
    отказ по размеру или типу его не тратит), место на диске с запасом MANUAL_RESERVE_MB для сбора и приёма(507 -
    учтённый расход возвращается). Отказ - HTTPException с текстом по-русски; запись MediaAsset создаёт вызывающий в
    своей сессии db - счётчики лимита пишутся и фиксируются отдельно(reserve_upload), до записи файла."""
    data = file.read(UPLOAD_MAX + 1)
    if len(data) > UPLOAD_MAX:
        raise HTTPException(413, f"Файл больше {UPLOAD_MAX // MB} МБ")
    try:
        _check(data[:HEAD], len(data), IMAGE_TYPES)
        taken = reserve_upload(now, len(data))
        try:
            return store_bytes(data, allowed=IMAGE_TYPES, reserve_mb=MANUAL_RESERVE_MB)
        except BaseException:
            release_upload(taken, now)
            raise
    except MediaRejected as e:
        raise HTTPException(e.status, e.detail) from None


def _referenced(db: Session, shas: Iterable[str]) -> set[str]:
    """Хеши, на которые ссылается хотя бы одна запись MediaAsset(запрос - частями по 500: предел параметров SQLite)."""
    A, shas, used = models.MediaAsset, list(shas), set()
    for i in range(0, len(shas), 500):
        used.update(db.scalars(select(A.sha256).where(A.sha256.in_(shas[i:i + 500]))))
    return used


def _unlink_stale(path: Path, cutoff: float) -> bool:
    """Удалить файл хранилища, если он не использовался с cutoff(store_* обновляет время файла при повторном
    использовании): идущий приём, который нашёл этот файл и ещё не сохранил свою запись, его не теряет. Файл
    использован недавно - остаётся до уборки файлов без записей(purge_orphans)."""
    try:
        if path.lstat().st_mtime >= cutoff:
            return False
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _unlink_unreferenced(db: Session, files: dict[str, str]) -> None:
    """Файлы(sha256 → ключ хранилища), на которые больше не ссылается ни одна запись и которые не использовались
    ORPHAN_TTL_S секунд, удаляются с диска. Транзакцию чтения закрывает до удаления файлов."""
    used = _referenced(db, files)
    db.commit()
    cutoff = time.time() - ORPHAN_TTL_S
    for sha, key in files.items():
        if sha in used:
            continue
        try:
            _unlink_stale(path_of(key), cutoff)
        except (OSError, ValueError) as e:
            log.warning("Файл хранилища %s не удалён: %s", key, type(e).__name__)


def purge_unattached(db: Session, now: datetime) -> int:
    """Ручные загрузки(origin_ref = upload), так и не привязанные к публикации за сутки, удаляются вместе с файлом,
    если на файл не ссылается другая запись и его не использовали повторно за сутки(иначе файл убирает
    purge_orphans). Фиксирует транзакцию сам: файлы удаляются только после commit."""
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
    """Файлы хранилища, на которые не ссылается ни одна запись MediaAsset(файл записан, а приём публикации не прошёл:
    сбой БД, отказ, остановка воркера), удаляются, если не менялись и не использовались max_age_s секунд(store_*
    обновляет время файла при повторном использовании - срок покрывает и идущие запуски). Таблица media_assets пуста -
    уборка пропускается: похоже на новую или чужую БД при том же MEDIA_ROOT. Транзакцию чтения закрывает до удаления
    файлов. Возвращает число удалённых файлов."""
    A = models.MediaAsset
    if db.scalar(select(A.id).limit(1)) is None:
        db.commit()
        return 0
    cutoff = time.time() - max_age_s
    old = _stored_files(cutoff)
    used = _referenced(db, old)
    db.commit()
    n = 0
    for sha, paths in old.items():
        if sha in used:
            continue
        for path in paths:
            try:
                n += _unlink_stale(path, cutoff)
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
