"""Стартовый набор данных: текущие данные рабочей БД - в репозиторий(shared/snapshot/), оттуда - в пустую БД нового
развёртывания(seed.start при старте API).

    python -m app.snapshot export [--out DIR] [--max-px 1024] [--quality 75]   # выгрузить из DATABASE_URL и MEDIA_ROOT
    python -m app.snapshot import [--dir DIR]                                  # загрузить в пустую БД(иначе - отказ)

Выгрузка только читает: БД - одной транзакцией(PostgreSQL: REPEATABLE READ, READ ONLY; SQLite - явный BEGIN), файлы -
из MEDIA_ROOT.
Схему не меняет: таблица читается столбцами, которые есть и в модели, и в БД(версия схемы БД - в manifest.json).
Результат(каталог заменяется целиком, только если всё получилось и проверка на секреты пройдена; заменяется только
прежний набор или пустой каталог - иначе отказ):
- <таблица>.json - строки по возрастанию первичного ключа, ключи по алфавиту, время - ISO 8601 в UTC;
- media/<sha256>.<ext> - изображения публикаций, уменьшенные до max-px по длинной стороне(по умолчанию 1024 px, JPEG
  q75; PNG - только если он меньше; исходный JPEG без метаданных(EXIF, XMP, IPTC, комментарий) в пределах размера - как
  есть, если перекодирование не экономит 10 %). Строки media_assets получают новые размер, SHA-256, MIME, ключ
  хранилища и width/height; исходные - в meta.snapshot;
- api/<имя>.json - ответы GET /api/… по этому набору(как их отдаст новое развёртывание) для офлайн-режима фронтенда и
  api/media.json - id файла → имя в media/;
- manifest.json - формат, версия схемы, время выгрузки, число строк по таблицам, что не вошло и почему.

Что не входит: видео(строка остаётся "недоступно" с пояснением VIDEO_NOTE и ссылкой на оригинал), кадры видео(строки
удаляются - распознанный по ним текст уже в ads.ocr), задания воркера и аренды, состояния OAuth, токены, счётчики квот,
заявки жителей(bot_submissions - персональные данные заявителей), записи открытого реестра(source_records - их
пересоздаёт загрузка из shared/registry_ust_kamenogorsk.json; выгрузка сверяет их с файлом), кэш кандидатов 2ГИС
(решения аналитика остаются, несостоявшиеся проверки - нет), события подключений в журнале(вид connector и прежние
data того же текста - migrate.LEGACY_CONNECTOR_EVENTS: аккаунты, списки источников, лимиты - сведения администратора).
ID заявок жителей(ingest_items пространства "tgbot": прежние ID бота - "{chat_id}:{message_id}") заменяются псевдонимами
BOT_PSEUDONYM - HMAC-SHA256 с ключом, который создаётся на одну выгрузку и нигде не сохраняется. Подключения -
настройки и "включено"; статус проверки, ошибки, курсоры и время следующего запуска сбрасываются: новое развёртывание
проверит подключения заново; у запусков сбора нет автора, ID и стоимости внешнего запуска(у записей приёма - его ID).
Снимки ответов источников(ingest_*.raw, extra) ещё раз проходят ingest.scrub, тексты ошибок - logs.redact. Перед
заменой каталога все файлы проверяются на значения секретов из настроек и на токеноподобные строки(leaks),
ingest_items.json - на ID заявок жителей без псевдонима(bot_ids) - найдено - выгрузка прерывается.

Загрузка(import и seed.start) - в одной транзакции: таблицы в порядке зависимостей с прежними первичными ключами
(PostgreSQL: последовательности - за максимальный id), файлы - в хранилище через media.store_bytes(тип по сигнатуре,
SHA-256 и ключ сверяются с записью; файл - только обычный, не ссылка), записи реестра - из
shared/registry_ust_kamenogorsk.json(без нового события журнала: событие о загрузке реестра уже в наборе), флаг meta
demo_data = purged. Непустую БД(есть строки хотя бы в одной таблице набора) загрузка не трогает. Файл таблицы - только
<таблица>.json в каталоге набора, число строк сверяется с manifest.json. Набор прежней известной версии схемы
загружается: столбцы, которых в нём нет, добавлены шагами migrate(nullable или с server_default) и получают NULL или
значение по умолчанию, таблицы, которые добавили шаги после его версии(migrate.tables_after, например
screening_decisions), - пустые, строки проходят переводы данных этих шагов(migrate.data_after: события подключений
вида data → connector); набор неизвестной или более новой версии - отказ(выгрузите набор заново).
"""
import argparse
import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable, Iterable

from sqlalchemy import DateTime, Integer, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from . import importer, ingest, logs, media, migrate, models, registry
from .integrations.telegram_bot import NAMESPACE as BOT_NAMESPACE
from .risk_model import ALMATY, SHARED
from .settings import get_settings

log = logging.getLogger("saq")

DIR = SHARED / "snapshot"
MANIFEST = "manifest.json"
FORMAT = "saq-snapshot-1"
# 1280 px / q80 дали 41 МБ изображений(весь набор - 45 МБ, больше цели 40 МБ) - поэтому 1024 px / q75
MAX_PX = 1024
QUALITY = 75
VIDEO_NOTE = "не включено в стартовый набор — откройте оригинал"
CHUNK = 500
VIEWS_TIMEOUT_S = 600  # процесс ответов API(загрузка набора в SQLite и GET /api/…) - обычно секунды
# Псевдоним ID заявки жителя: "#" нет среди знаков ID бота(telegram_bot.TelegramIntakeIn) - псевдоним не спутать с
# присланным ID, и повторная выгрузка загруженного набора его не меняет
BOT_PSEUDONYM = re.compile(r"tgbot#[0-9a-f]{24}")
Obj = dict[str, Any]


class SnapshotError(RuntimeError):
    """Набор не выгружен или не загружен: причина по-русски."""


# ---------- что входит в набор ----------

@dataclass
class Ctx:
    """Состояние выгрузки: время, каталог файлов, сжатые изображения(исходный ключ хранилища → файл набора),
    счётчики."""
    at: datetime
    media_dir: Path
    max_px: int = MAX_PX
    quality: int = QUALITY
    files: dict[str, tuple[str, str, int, int, int]] = field(default_factory=dict)  # ключ → (sha, mime, size, w, h)
    dropped_media: set[str] = field(default_factory=set)
    stats: dict[str, int] = field(default_factory=dict)
    # ключ псевдонимов ID заявок жителей: только на эту выгрузку - chat_id не подобрать перебором по набору
    bot_key: bytes = field(default_factory=lambda: os.urandom(32), repr=False)

    def count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n


def _collector(row: Obj, ctx: Ctx) -> Obj:
    """Настройки и "включено" остаются; статус, факты(ключи, аккаунты), курсоры и расписание - как у новой строки."""
    return {**row, "status": "unchecked", "status_detail": None, "facts": {}, "state": {}, "failures": 0,
            "next_run_at": None, "last_success_at": None, "last_error_at": None, "checked_at": None,
            "last_run_id": None, "last_error": None}


def _run(row: Obj, ctx: Ctx) -> Obj:
    """Без автора, ID и стоимости внешнего запуска(Apify) - их, как и в API, видит только администратор."""
    notes = [{**n, "text": logs.redact(n.get("text"), limit=None)} if isinstance(n, dict) else n
             for n in row.get("notes") or []]
    return {**row, "error": logs.redact(row.get("error"), limit=None), "notes": notes, "requested_by": None,
            "external_run_id": None, "cost_usd": None}


def _audit(row: Obj, ctx: Ctx) -> Obj | None:
    """События подключений(аккаунты, списки источников, лимиты, тексты ошибок) - сведения администратора, в набор не
    входят; прежние(вид data до 2026.10-audit-2) - по тексту."""
    kind = row.get("kind")
    if kind == "connector" or migrate.legacy_connector_event(kind, row.get("who"), row.get("what")):
        ctx.count("audit_events: события подключений — не выгружены")
        return None
    return row


def _item(row: Obj, ctx: Ctx) -> Obj:
    # ID внешнего запуска(Apify) - как у запусков сбора(_run)
    out = {**row, "raw": ingest.scrub(row.get("raw") or {}),
           "analysis_error": logs.redact(row.get("analysis_error"), limit=None), "external_run_id": None}
    eid = str(row.get("external_id") or "")
    if row.get("platform") == BOT_NAMESPACE and not BOT_PSEUDONYM.fullmatch(eid):
        # ID заявки от бота мог быть "{chat_id}:{message_id}": chat_id - Telegram ID жителя. Псевдоним уникален, как
        # и ID(пара platform, external_id остаётся уникальной), а ключ не попадает в набор
        digest = hmac.new(ctx.bot_key, eid.encode(), hashlib.sha256).hexdigest()
        out["external_id"] = f"{BOT_NAMESPACE}#{digest[:24]}"
        ctx.count("ingest_items: ID заявок жителей — псевдонимы")
    return out


def _version(row: Obj, ctx: Ctx) -> Obj:
    return {**row, "raw": ingest.scrub(row.get("raw") or {}), "extra": ingest.scrub(row.get("extra") or {})}


def _llm(row: Obj, ctx: Ctx) -> Obj:
    # request-id Anthropic наружу не выводится и набору не нужен
    return {**row, "request_id": None, "error": logs.redact(row.get("error"), limit=None)}


def _geo(row: Obj, ctx: Ctx) -> Obj | None:
    """Решения аналитика остаются(выбранный дом, "было → стало"); кандидаты 2ГИС - временный кэш, его нет в наборе.
    Проверка без решения(только кандидаты) не выгружается."""
    if row.get("status") == "candidates":
        ctx.count("geo_checks: без решения — не выгружены")
        return None
    return {**row, "candidates": [], "purged_at": row.get("purged_at") or ctx.at.isoformat()}


def _media(row: Obj, ctx: Ctx) -> Obj | None:
    meta = {k: v for k, v in (row.get("meta") or {}).items() if k not in ("src", "retryAt")}
    row = {**row, "meta": meta, "error": logs.redact(row.get("error"), limit=None)}
    if row["kind"] == "frame":
        ctx.dropped_media.add(row["id"])
        ctx.count("media_assets: кадры видео — не выгружены")
        return None
    if row["kind"] == "video":
        if row["status"] == "downloaded":
            ctx.count("media_assets: видео — без файла (недоступно)")
            meta["snapshot"] = {"omitted": "video", "sourceSize": row.get("size"), "sourceMime": row.get("mime")}
            return {**row, "status": "unavailable", "error": VIDEO_NOTE, "storage_key": None, "sha256": None,
                    "size": None, "mime": None, "downloaded_at": None}
        return row
    if row["status"] != "downloaded" or not row.get("storage_key"):
        return row
    src_sha, src_key = row.get("sha256"), row["storage_key"]
    # кэш - по ключу хранилища(он задан содержимым): у строк без sha256 общий ключ "" дал бы им чужой файл
    got = ctx.files.get(src_key)
    if got is None:
        try:
            data = media.path_of(src_key).read_bytes()
        except (OSError, ValueError) as e:
            raise SnapshotError(f"Файл {row['id']} ({src_key}) не прочитан из MEDIA_ROOT: {type(e).__name__}") from e
        try:
            out, mime, w, h = compress(data, ctx.max_px, ctx.quality)
        except Exception as e:  # PIL: повреждённый или неподдерживаемый файл; предел пикселей - MediaRejected(с detail)
            why = e.detail if isinstance(e, media.MediaRejected) else type(e).__name__
            raise SnapshotError(f"Файл {row['id']} ({src_key}) не обработан как изображение: {why}") from e
        sha = hashlib.sha256(out).hexdigest()
        (ctx.media_dir / f"{sha}{media.ALLOWED[mime]}").write_bytes(out)
        got = ctx.files[src_key] = (sha, mime, len(out), w, h)
        ctx.count("media: исходных байт", len(data))
    sha, mime, size, w, h = got
    if sha != src_sha or "snapshot" not in meta:  # файл набора уже в наборе(повторная выгрузка) - сведения прежние
        meta["snapshot"] = {"sourceSha256": row.get("sha256"), "sourceSize": row.get("size"),
                            "sourceMime": row.get("mime"), "maxPx": ctx.max_px, "quality": ctx.quality}
    meta.update(width=w, height=h)
    ctx.count("media_assets: изображения")
    return {**row, "sha256": sha, "mime": mime, "size": size, "storage_key": media.storage_key(sha, mime)}


@dataclass(frozen=True)
class Spec:
    model: type
    prepare: Callable[[Obj, Ctx], Obj | None] | None = None  # строка БД → строка набора(None - не выгружать)

    @property
    def name(self) -> str:
        return self.model.__tablename__


# Порядок загрузки - по зависимостям(внешние ключи): родительские таблицы раньше
TABLES: tuple[Spec, ...] = (
    Spec(models.Analyst), Spec(models.Developer), Spec(models.Complex), Spec(models.Ad), Spec(models.Appeal),
    Spec(models.Signal), Spec(models.AuditEvent, _audit), Spec(models.Collector, _collector),
    Spec(models.CollectionRun, _run), Spec(models.IngestItem, _item), Spec(models.IngestVersion, _version),
    Spec(models.MediaAsset, _media), Spec(models.LlmAnalysis, _llm), Spec(models.GeoCheck, _geo),
    Spec(models.DiscoveredObject), Spec(models.DiscoveredMention), Spec(models.ScreeningDecision),
)
EXCLUDED: dict[str, str] = {
    "jobs": "задания воркера — состояние работы, а не данные",
    "worker_leases": "аренды и пульс воркеров",
    "oauth_states": "одноразовые состояния OAuth",
    "integration_tokens": "токены доступа подключений — секреты",
    "quota_counters": "счётчики расхода квот и дневного бюджета",
    "bot_submissions": "заявки жителей — персональные данные заявителей",
    "resident_alerts": "предупреждения жителям — сообщения, отправленные из этого развёртывания",
    "alert_deliveries": "доставки предупреждений — состояние отправки",
    "alert_subscriptions": "подписки жителей на районы — chat_id Telegram",
    "source_records": "записи открытого реестра — загрузка пересоздаёт их из shared/registry_ust_kamenogorsk.json",
    "meta": "версию схемы и флаги задаёт загрузка",
}
ALSO_EXCLUDED = {
    "video": "видеофайлы — строки остаются «недоступно» со ссылкой на оригинал",
    "frames": "кадры видео — строки и файлы не выгружаются, распознанный текст уже в ads.ocr",
    "geoCandidates": "кэш кандидатов 2ГИС — остаются только решения аналитика",
    "collectorsRuntime": "статус, факты, курсоры и расписание подключений — новое развёртывание проверит их заново",
    "connectorEvents": "события подключений в журнале — аккаунты, списки источников, лимиты, тексты ошибок",
    "runsAdmin": "кто запустил сбор, ID и стоимость платных запусков — сведения администратора",
    "botExternalIds": "ID заявок жителей (chat_id Telegram) — заменены псевдонимами",
}
# Ответы API для офлайн-режима фронтенда: имя файла → путь GET(asOf - дата расчёта риска и "Проверки по рекламе")
VIEWS: tuple[tuple[str, str, bool], ...] = (
    ("complexes", "/api/complexes", True), ("developers", "/api/developers", True), ("ads", "/api/ads", False),
    ("appeals", "/api/appeals", False), ("risk", "/api/risk", True), ("stats", "/api/stats", True),
    ("signals", "/api/signals", False), ("analysts", "/api/analysts", False), ("discovered", "/api/discovered", False),
)


# ---------- изображения ----------

KEEP_GAIN = 0.9  # исходный JPEG в пределах размера остаётся как есть, если перекодирование не экономит хотя бы 10 %
# Сегменты JPEG, с которыми исходный файл может остаться как есть: JFIF(APP0), ICC-профиль(APP2), Adobe(APP14). EXIF и
# XMP(APP1), IPTC(APP13), комментарий(COM) - сведения о съёмке и авторе(место, устройство), такой файл перекодируется
BARE_JPEG = frozenset({"APP0", "APP2", "APP14"})


def compress(data: bytes, max_px: int = MAX_PX, quality: int = QUALITY) -> tuple[bytes, str, int, int]:
    """Изображение → (байты, MIME, ширина, высота): длинная сторона не больше max_px(с учётом поворота EXIF), без
    метаданных; JPEG с качеством quality, PNG - только если он меньше JPEG(прозрачность - на белом фоне для JPEG).
    Исходный JPEG без метаданных(BARE_JPEG, без данных после конца изображения) и не больше max_px остаётся как есть,
    если перекодирование не экономит 10 %(KEEP_GAIN): так повторная выгрузка уже сжатого набора не меняет файлы.
    Декодирование - media.open_image: только форматы хранилища и предел пикселей(больше - media.MediaRejected)."""
    from PIL import Image, ImageOps

    with media.open_image(data) as src:
        fmt = src.format
        bare = (fmt == "JPEG" and not src.getexif() and data.endswith(b"\xff\xd9")
                and all(marker in BARE_JPEG for marker, _ in getattr(src, "applist", ())))
        im = ImageOps.exif_transpose(src)
    w, h = im.size
    if max(w, h) > max_px:
        k = max_px / max(w, h)
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
    alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    if alpha:
        rgba = im.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, (255, 255, 255))
        rgb.paste(rgba, mask=rgba.getchannel("A"))
    else:
        rgb = im.convert("RGB")
    buf = io.BytesIO()
    # comment="" - Pillow иначе переносит комментарий исходного файла(JPEG, GIF) из info
    rgb.save(buf, format="JPEG", quality=quality, optimize=True, comment=b"")
    best = (buf.getvalue(), "image/jpeg")
    if fmt != "JPEG" or alpha:
        buf = io.BytesIO()
        (im if im.mode in ("RGB", "RGBA", "L", "LA", "P") else rgb).save(buf, format="PNG", optimize=True)
        if len(buf.getvalue()) < len(best[0]):
            best = (buf.getvalue(), "image/png")
    if bare and (w, h) == im.size and len(best[0]) > KEEP_GAIN * len(data):
        best = (data, "image/jpeg")
    return best[0], best[1], im.size[0], im.size[1]


# ---------- проверка на секреты ----------

TOKENISH = (
    ("ключ Anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("токен Meta", re.compile(r"\bEA[AB][A-Za-z0-9]{40,}")),
    ("токен Apify", re.compile(r"apify_api_[A-Za-z0-9]{8,}")),
    ("токен Telegram-бота", re.compile(r"\b\d{6,12}:AA[A-Za-z0-9_-]{30,}")),
    ("токен Google", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}")),
    ("Bearer-токен", re.compile(r"(?i)\bbearer\s+(?!\*\*\*)[A-Za-z0-9._~+/-]{16,}")),
    ("ключ AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("токен GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("токен Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("приватный ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("подписанная ссылка", re.compile(r"[?&](?:access_token|refresh_token|client_secret|signature|X-Amz-Signature|"
                                      r"oh|oe|_nc_sid|sig|token)=(?!\*\*\*)[^&\s\"']{6,}", re.I)),
)


def leaks(text_: str, secrets: Iterable[str] = ()) -> list[str]:
    """Что похоже на секрет в тексте: названия находок(не сами значения). secrets - значения секретов из настроек
    (короче 8 символов не проверяются)."""
    found = [name for name, rx in TOKENISH if rx.search(text_)]
    if any(len(s) >= 8 and s in text_ for s in secrets):
        found.append("значение секрета из настроек")
    return found


def bot_ids(rows: Any) -> list[str]:
    """Строки ingest_items.json пространства заявок бота, у которых ID - не псевдоним BOT_PSEUDONYM(в нём chat_id
    жителя): название находки или пусто."""
    if not isinstance(rows, list):
        return []
    bad = sum(1 for r in rows if isinstance(r, dict) and r.get("platform") == BOT_NAMESPACE
              and not BOT_PSEUDONYM.fullmatch(str(r.get("external_id") or "")))
    return [f"ID заявок жителей без псевдонима: {bad}"] if bad else []


def scan(root: Path, secrets: Iterable[str] = ()) -> dict[str, list[str]]:
    """Текстовые файлы набора(*.json) с находками leaks, ingest_items.json - и bot_ids: относительный путь → названия
    находок."""
    secrets = [s for s in secrets if s]
    out: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*.json")):
        text_ = p.read_text(encoding="utf-8")
        found = leaks(text_, secrets)
        if p == root / "ingest_items.json":
            found += bot_ids(json.loads(text_))
        if found:
            out[str(p.relative_to(root))] = found
    return out


# ---------- выгрузка ----------

def _jsonable(col, v: Any) -> Any:
    if v is not None and isinstance(col.type, DateTime):
        return models.as_utc(v).isoformat()
    return v


def _read(conn: Connection, model) -> tuple[list[str], list[Obj]]:
    """Строки таблицы модели - столбцами, которые есть и в модели, и в БД; по возрастанию первичного ключа."""
    t = model.__table__
    have = {c["name"] for c in inspect(conn).get_columns(t.name)}
    cols = [c for c in t.c if c.name in have]
    rows = [{c.name: _jsonable(c, r[i]) for i, c in enumerate(cols)} for r in conn.execute(select(*cols))]
    pk = [c.name for c in t.primary_key.columns]
    rows.sort(key=lambda r: tuple(r[k] for k in pk))
    return [c.name for c in cols], rows


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def _schema_version(conn: Connection) -> str | None:
    if "meta" not in inspect(conn).get_table_names():
        return None
    return conn.execute(text("SELECT value FROM meta WHERE key = 'schema_version'")).scalar()


def registry_check(conn: Connection) -> Obj:
    """Записи открытого реестра в БД против shared/registry_ust_kamenogorsk.json: загрузка набора пересоздаёт их из
    файла, расхождение(импорт через интерфейс после сборки файла) - предупреждение в manifest.json."""
    data = importer.load_registry() or {"records": []}
    want = {(r["source"], r["sourceRecordId"]): registry._values(r) for r in data["records"]}
    cols = [*registry.COLUMNS.values(), "collected_at"]
    R = models.SourceRecord
    have = {}
    if R.__tablename__ in inspect(conn).get_table_names():
        for r in conn.execute(select(R.source, R.source_record_id, *[getattr(R, c) for c in cols])):
            have[(r[0], r[1])] = dict(zip(cols, r[2:], strict=True))
    differ = sorted(f"{s}:{i}" for (s, i), v in want.items() if (s, i) in have and any(
        (models.iso(have[(s, i)][c]) != models.iso(v[c])) if c == "collected_at" else have[(s, i)][c] != v[c]
        for c in cols))
    return {"file": importer.REGISTRY.name, "inFile": len(want), "inDb": len(have),
            "onlyInDb": sorted(f"{s}:{i}" for s, i in set(have) - set(want)),
            "onlyInFile": sorted(f"{s}:{i}" for s, i in set(want) - set(have)), "differ": differ,
            "consistent": not differ and set(have) == set(want)}


def _replaceable(out: Path) -> bool:
    """Каталог out можно заменить выгрузкой: его нет, он пустой или это прежний набор(manifest.json формата FORMAT).
    Иначе export --out с опечаткой(домашний каталог, каталог проекта) удалил бы чужие файлы."""
    if not out.exists():
        return True
    if not out.is_dir():
        return False
    if not any(out.iterdir()):
        return True
    try:
        man = manifest(out)
    except SnapshotError:
        return False
    return man is not None and man.get("format") == FORMAT


def _swap(new: Path, out: Path) -> None:
    """Каталог набора заменяется целиком: прежний - в сторону(под уникальным именем рядом), новый - на его место,
    прежний удаляется. Новый не встал на место - прежний возвращается."""
    if not out.exists():
        new.rename(out)
        return
    old = Path(tempfile.mkdtemp(prefix=f".{out.name}.old-", dir=out.parent))
    out.rename(old)  # пустой каталог old заменяется(POSIX rename)
    try:
        new.rename(out)
    except BaseException:
        old.rename(out)
        raise
    shutil.rmtree(old, ignore_errors=True)


def export(eng: Engine, out: Path = DIR, *, max_px: int = MAX_PX, quality: int = QUALITY, views: bool = True,
           now: datetime | None = None) -> Obj:
    """Выгрузка набора из БД eng и хранилища MEDIA_ROOT в каталог out(заменяется целиком). Возвращает manifest."""
    at = models.as_utc(now) or models.utcnow().replace(microsecond=0)
    out = Path(out).resolve()
    if not _replaceable(out):
        raise SnapshotError(f"Каталог {out} не пустой и не похож на стартовый набор (нет {MANIFEST} формата "
                            f"{FORMAT}) — выгрузка заменяет каталог целиком, укажите другой")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{out.name}.new-", dir=out.parent))
    try:
        os.chmod(work, 0o755)  # mkdtemp создаёт 0700, а каталог набора - обычный каталог репозитория
        (work / "media").mkdir()
        ctx = Ctx(at=at, media_dir=work / "media", max_px=max_px, quality=quality)
        tables: dict[str, Obj] = {}
        media_rows: list[Obj] = []
        with eng.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            elif conn.dialect.name == "sqlite":
                # pysqlite сам не открывает транзакцию для SELECT: без BEGIN каждая таблица читалась бы отдельно и
                # запись воркера между ними дала бы несогласованный набор(файл без публикации, строка без родителя)
                conn.exec_driver_sql("BEGIN")
            version = _schema_version(conn)
            if not _compatible(version):
                raise SnapshotError(f"Схема БД — {version or 'без версии'}: выгрузка умеет только версии из цепочки "
                                    f"migrate (текущая {models.SCHEMA_VERSION})")
            for spec in TABLES:
                cols, rows = _read(conn, spec.model)
                if spec.prepare is not None:
                    rows = [x for x in (spec.prepare(r, ctx) for r in rows) if x is not None]
                if spec.name == "media_assets":
                    if ctx.dropped_media:
                        rows = [{**r, "parent_id": None} if r.get("parent_id") in ctx.dropped_media else r
                                for r in rows]
                    media_rows = rows
                (work / f"{spec.name}.json").write_text(dump(rows), encoding="utf-8")
                tables[spec.name] = {"file": f"{spec.name}.json", "rows": len(rows), "columns": cols}
            reg = registry_check(conn)
            conn.rollback()
        files = sorted((work / "media").iterdir())
        media_map = {r["id"]: Path(r["storage_key"]).name for r in media_rows
                     if r.get("storage_key") and r.get("status") == "downloaded"}
        man: Obj = {
            "_note": "Стартовый набор SAQ Qurylys: реальные данные мониторинга (ЖК открытого реестра и известные по "
                     "рекламе, застройщики, публичная реклама с OCR и анализом ИИ, объекты из рекламы, журнал). "
                     "Загружается в пустую БД при старте API; формат и обновление — backend/app/snapshot.py, README.",
            "format": FORMAT, "schemaVersion": version, "exportedAt": at.isoformat(),
            "tables": tables, "excluded": EXCLUDED, "alsoExcluded": ALSO_EXCLUDED,
            "media": {"dir": "media", "files": len(files), "bytes": sum(p.stat().st_size for p in files),
                      "sourceBytes": ctx.stats.get("media: исходных байт", 0), "maxPx": max_px, "quality": quality},
            "prepared": {k: v for k, v in sorted(ctx.stats.items()) if not k.startswith("media: ")},
            "registry": reg,
        }
        if not reg["consistent"]:
            log.warning("Записи открытого реестра в БД расходятся с %s: %s — загрузка набора возьмёт записи из файла",
                        reg["file"], {k: reg[k] for k in ("onlyInDb", "onlyInFile", "differ")})
        (work / MANIFEST).write_text(dump(man), encoding="utf-8")  # ответы API строятся загрузкой этого набора
        if views:
            (work / "api").mkdir()
            as_of = at.astimezone(ALMATY).date()
            (work / "api" / "media.json").write_text(dump(media_map), encoding="utf-8")
            build_views_subprocess(work, as_of)
            man["api"] = {"dir": "api", "asOf": as_of.isoformat(),
                          "files": sorted(p.name for p in (work / "api").iterdir())}
            (work / MANIFEST).write_text(dump(man), encoding="utf-8")
        found = scan(work, get_settings().secrets())
        if found:
            raise SnapshotError("В выгрузке найдено похожее на секреты — набор не записан: "
                                + "; ".join(f"{p}: {', '.join(k)}" for p, k in found.items()))
        _swap(work, out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return man


# ---------- ответы API для офлайн-режима ----------

def build_views_subprocess(src: Path, as_of: date) -> None:
    """Ответы API считает отдельный процесс на временной SQLite(тот же код, что у нового развёртывания): движок БД
    приложения создаётся при импорте app.db по DATABASE_URL. Секреты и настройки подключений в него не передаются."""
    from .settings import ENV_NAMES

    with tempfile.TemporaryDirectory(prefix="saq-snapshot-views-") as tmp:
        env = {k: v for k, v in os.environ.items() if k not in ENV_NAMES}
        env.update(DATABASE_URL=f"sqlite:///{tmp}/views.db", MEDIA_ROOT=f"{tmp}/media", SAQ_ENV_FILE="",
                   SAQ_SHARED_DIR=str(SHARED), TG_SESSION_PATH=f"{tmp}/telegram.session")
        cmd = [sys.executable, "-m", "app.snapshot", "views", "--dir", str(src), "--as-of", as_of.isoformat()]
        try:
            r = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), env=env, capture_output=True,
                               text=True, timeout=VIEWS_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired as e:
            raise SnapshotError(f"Ответы API по набору не построены: нет итога за {VIEWS_TIMEOUT_S} с") from e
        if r.returncode != 0:
            raise SnapshotError(f"Ответы API по набору не построены (код {r.returncode}): "
                                f"{logs.redact((r.stderr or r.stdout or '').strip()[-2000:], limit=None)}")


def build_views(src: Path, as_of: date) -> list[str]:
    """(в отдельном процессе) Набор src → пустая БД DATABASE_URL(без файлов) → GET /api/… → src/api/<имя>.json.
    БД с таблицами приложения - отказ: в рабочей БД ответы были бы по ней, а не по набору."""
    from functools import partial

    from fastapi.testclient import TestClient

    from . import seed
    from .db import Base, engine
    from .main import app

    if manifest(src) is None:
        raise SnapshotError(f"Нет набора для ответов API: {src / MANIFEST}")
    if set(inspect(engine).get_table_names()) & set(Base.metadata.tables):
        raise SnapshotError("Ответы API по набору строятся только в пустой временной БД (python -m app.snapshot "
                            "export) — в этой БД уже есть таблицы приложения")
    seed.init_db(engine, start=partial(seed.start, src=src, files=False))
    # без lifespan: схема и набор уже загружены. Host - localhost: имя по умолчанию "testserver" отклонил бы
    # main.HostAllowlist - в SAQ_WEB_HOSTS рабочего сервера его нет
    client = TestClient(app, base_url="http://localhost")
    (src / "api").mkdir(exist_ok=True)
    names = []
    for name, path, dated in VIEWS:
        r = client.get(path, params={"asOf": as_of.isoformat()} if dated else None)
        if r.status_code != 200:
            raise SnapshotError(f"GET {path}: {r.status_code} {r.text[:300]}")
        (src / "api" / f"{name}.json").write_text(dump(r.json()), encoding="utf-8")
        names.append(name)
    return names


# ---------- загрузка ----------

@dataclass
class LoadReport:
    rows: dict[str, int] = field(default_factory=dict)
    files: int = 0
    registry: int = 0
    exported_at: str | None = None
    schema: str | None = None

    def line(self) -> str:
        main = ", ".join(f"{k} — {v}" for k, v in self.rows.items() if k in (
            "complexes", "developers", "ads", "llm_analyses", "audit_events", "discovered_objects"))
        return (f"стартовый набор от {self.exported_at or '—'} (схема {self.schema}): {main}; файлов — {self.files}; "
                f"записей открытого реестра — {self.registry}")


def manifest(src: Path = DIR) -> Obj | None:
    """manifest.json набора src; нет файла - None, не JSON-объект - SnapshotError."""
    p = Path(src) / MANIFEST
    if not p.is_file():
        return None
    try:
        man = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SnapshotError(f"{p} не прочитан: {type(e).__name__}") from e
    if not isinstance(man, dict):
        raise SnapshotError(f"{p}: нужен JSON-объект")
    return man


def is_empty(db: Session) -> bool:
    """В таблицах набора нет ни одной строки(новая БД): только в такую загружается набор."""
    return all(db.scalar(select(func.count()).select_from(s.model.__table__)) == 0 for s in TABLES)


def _compatible(version: str | None) -> bool:
    return version == models.SCHEMA_VERSION or migrate.chain_from(version) is not None


def _prepare(table, rows: list[Obj], name: str) -> list[Obj]:
    """Строки набора → значения INSERT: время - datetime; столбцы, которых в наборе нет(набор прежней версии схемы), -
    NULL или server_default, как после шагов migrate; неизвестный столбец - отказ."""
    if not rows:
        return []
    keys = set(rows[0])
    extra = sorted(keys - set(table.c.keys()))
    if extra:
        raise SnapshotError(f"{name}.json: столбцы {extra} не описаны в модели — набор новее кода, выгрузите его "
                            "заново")
    missing = [c for c in table.c if c.name not in keys]
    for c in missing:
        if not c.nullable and c.server_default is None:
            raise SnapshotError(f"{name}.json: нет обязательного столбца {c.name} — выгрузите набор заново")
    nulls = {c.name: None for c in missing if c.server_default is None}
    times = [c.name for c in table.c if isinstance(c.type, DateTime) and c.name in keys]
    out = []
    for r in rows:
        if set(r) != keys:
            raise SnapshotError(f"{name}.json: у строк разный набор столбцов")
        v = {**r, **nulls}
        for k in times:
            if v[k] is not None:
                try:
                    v[k] = models.as_utc(datetime.fromisoformat(v[k]))
                except (TypeError, ValueError) as e:
                    raise SnapshotError(f"{name}.json: {k} = {str(v[k])[:40]!r} — не время ISO 8601") from e
        out.append(v)
    return out


def _table_rows(src: Path, name: str, info: Any) -> list[Obj]:
    """Строки таблицы набора: файл - только "<таблица>.json" в каталоге набора(путь из manifest.json не уводит за
    него), список объектов, число строк - как в manifest.json(обрезанный или подменённый файл - отказ)."""
    file = f"{name}.json"
    if not isinstance(info, dict) or info.get("file") != file:
        raise SnapshotError(f"{MANIFEST}: у таблицы {name} должен быть файл {file}")
    try:
        rows = json.loads((src / file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SnapshotError(f"{file} не прочитан: {type(e).__name__}") from e
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SnapshotError(f"{file}: нужен список строк-объектов")
    if info.get("rows") != len(rows):
        raise SnapshotError(f"{file}: строк {len(rows)}, а в {MANIFEST} — {info.get('rows')}; выгрузите набор заново")
    return rows


def _reset_sequences(db: Session) -> None:
    """PostgreSQL: последовательность целочисленного id - за максимальным значением(вставка без конфликта)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    for s in TABLES:
        t = s.model.__table__
        pk = list(t.primary_key.columns)
        if len(pk) != 1 or not isinstance(pk[0].type, Integer):
            continue
        col = pk[0].name
        db.execute(text(f"SELECT setval(pg_get_serial_sequence(:t, :c), COALESCE((SELECT MAX({col}) FROM {t.name}), 0) "
                        "+ 1, false)"), {"t": t.name, "c": col})


def _restore_files(src: Path, rows: list[Obj]) -> int:
    """Изображения набора → хранилище(media.store_bytes: тип по сигнатуре, место на диске), ключ и SHA-256 -
    сверяются."""
    done: dict[str, str] = {}  # ключ → SHA-256 записанного файла
    for r in rows:
        key = r.get("storage_key")
        if not key or r.get("status") != "downloaded":
            continue
        path = src / "media" / Path(key).name  # только имя: ключ из набора не уводит за каталог media
        if key in done:  # файл уже записан - запись с тем же ключом должна ссылаться на него же
            if done[key] != r.get("sha256"):
                raise SnapshotError(f"Файл набора {path.name} не совпадает с записью {r['id']} (SHA-256 или тип)")
            continue
        # ссылка увела бы чтение за каталог набора, FIFO - повесил бы загрузку
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise SnapshotError(f"Файл набора {path.name} (запись {r['id']}) — не обычный файл (ссылка, каталог)")
        try:
            data = path.read_bytes()
        except OSError as e:
            raise SnapshotError(f"Нет файла набора {path.name} (запись {r['id']}): {type(e).__name__}") from e
        try:
            stored = media.store_bytes(data, allowed=media.IMAGE_TYPES)
        except media.MediaRejected as e:
            raise SnapshotError(f"Файл набора {path.name} (запись {r['id']}) не принят хранилищем: {e}") from e
        if stored.storage_key != key or stored.sha256 != r.get("sha256"):
            raise SnapshotError(f"Файл набора {path.name} не совпадает с записью {r['id']} (SHA-256 или тип)")
        done[key] = stored.sha256
    return len(done)


def load(db: Session, src: Path = DIR, *, files: bool = True) -> LoadReport:
    """Набор src → пустая БД(в сессии db, без commit). Непустая БД, нет набора, несовместимая версия -
    SnapshotError."""
    src = Path(src)
    man = manifest(src)
    if man is None:
        raise SnapshotError(f"Нет стартового набора: {src / MANIFEST}")
    if man.get("format") != FORMAT:
        raise SnapshotError(f"Неизвестный формат набора: {man.get('format')!r} (нужен {FORMAT})")
    if not _compatible(man.get("schemaVersion")):
        raise SnapshotError(f"Набор схемы {man.get('schemaVersion') or 'без версии'} не подходит к коду "
                            f"({models.SCHEMA_VERSION}) — выгрузите его заново: python -m app.snapshot export")
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": migrate.ADVISORY_LOCK})
    if not is_empty(db):
        raise SnapshotError("БД не пустая — стартовый набор загружается только в новую БД")
    tables = man.get("tables")
    if not isinstance(tables, dict):
        raise SnapshotError(f"{MANIFEST}: нет описания таблиц набора")
    rep = LoadReport(exported_at=man.get("exportedAt"), schema=man.get("schemaVersion"))
    media_rows: list[Obj] = []
    later = migrate.tables_after(man.get("schemaVersion"))
    for s in TABLES:
        info = tables.get(s.name)
        if info is None and s.name not in later:
            raise SnapshotError(f"В наборе нет таблицы {s.name}")
        # таблица, которую добавил шаг migrate после версии набора, - пустая(как после обновления схемы)
        rows = _table_rows(src, s.name, info) if info is not None else []
        if s.name == "media_assets":  # кадры(parent_id) - после своих видео
            rows.sort(key=lambda r: r.get("parent_id") is not None)
            media_rows = rows
        values = _prepare(s.model.__table__, rows, s.name)
        for i in range(0, len(values), CHUNK):
            db.execute(s.model.__table__.insert(), values[i:i + CHUNK])
        rep.rows[s.name] = len(values)
    db.flush()
    for fix in migrate.data_after(man.get("schemaVersion")):  # как migrate.upgrade у БД этой версии
        fix(db.connection())
    _reset_sequences(db)
    data = importer.load_registry()
    if data and data.get("records"):
        meta = data.get("meta") or {}
        at = models.as_utc(datetime.fromisoformat(meta["builtAt"])) if meta.get("builtAt") else models.utcnow()
        src_name = (meta.get("builtFrom") or importer.REGISTRY.name).rsplit("/", 1)[-1]
        registry.upsert(db, data["records"], src_name, at)
        rep.registry = len(data["records"])
    from . import demo

    db.merge(models.Meta(key=demo.FLAG, value=demo.PURGED))
    if files:
        rep.files = _restore_files(src, media_rows)
    db.flush()
    return rep


def import_cli(eng: Engine, src: Path) -> int:
    """CLI import: 0 - загружено, 2 - отказ(непустая БД, нет набора, версия)."""
    from . import seed

    if not (Path(src) / MANIFEST).is_file():
        print(f"Нет стартового набора: {Path(src) / MANIFEST}", file=sys.stderr)
        return 2
    present = set(inspect(eng).get_table_names())
    with eng.connect() as conn:  # до обновления схемы и создания таблиц: непустую БД команда не меняет вовсе
        busy = [s.name for s in TABLES if s.name in present
                and conn.execute(select(func.count()).select_from(s.model.__table__)).scalar()]
    if busy:
        print(f"БД не пустая ({', '.join(busy)}) — стартовый набор загружается только в новую БД, ничего не изменено",
              file=sys.stderr)
        return 2
    holder: dict[str, LoadReport] = {}

    def start(db: Session) -> None:
        from . import demo

        holder["rep"] = load(db, src)
        demo.import_registry(db)  # на согласованном наборе - без изменений(как seed.start)

    try:
        seed.init_db(eng, start=start)
    except (SnapshotError, migrate.SchemaError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print("Загружен " + holder["rep"].line())
    return 0


def _int_in(lo: int, hi: int) -> Callable[[str], int]:
    """Тип аргумента argparse: целое от lo до hi(иначе - сообщение и код 2)."""
    def parse(value: str) -> int:
        try:
            n: int | None = int(value)
        except ValueError:
            n = None
        if n is None or not lo <= n <= hi:
            raise argparse.ArgumentTypeError(f"нужно целое число от {lo} до {hi}")
        return n

    return parse


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.snapshot", description="Стартовый набор данных (shared/snapshot)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("export", help="выгрузить текущие данные БД и файлы в каталог набора")
    pe.add_argument("--out", type=Path, default=DIR, help=f"каталог набора (по умолчанию {DIR})")
    pe.add_argument("--max-px", type=_int_in(16, 10000), default=MAX_PX,
                    help=f"длинная сторона изображения, px ({MAX_PX})")
    pe.add_argument("--quality", type=_int_in(1, 100), default=QUALITY, help=f"качество JPEG ({QUALITY})")
    pi = sub.add_parser("import", help="загрузить набор в пустую БД (непустую — отказ)")
    pi.add_argument("--dir", type=Path, default=DIR, help=f"каталог набора (по умолчанию {DIR})")
    pv = sub.add_parser("views", help=argparse.SUPPRESS)
    pv.add_argument("--dir", type=Path, required=True)
    pv.add_argument("--as-of", type=date.fromisoformat, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "views":
        build_views(a.dir, a.as_of)
        return 0
    from .db import engine

    if a.cmd == "import":
        return import_cli(engine, a.dir)
    try:
        man = export(engine, a.out, max_px=a.max_px, quality=a.quality)
    except SnapshotError as e:
        print(str(e), file=sys.stderr)
        return 2
    total = sum(p.stat().st_size for p in Path(a.out).rglob("*") if p.is_file())
    print(f"Набор записан: {a.out} — схема {man['schemaVersion']}, "
          + ", ".join(f"{k} {v['rows']}" for k, v in man["tables"].items())
          + f"; файлов изображений {man['media']['files']} ({man['media']['bytes'] / 1048576:.1f} МБ), всего "
          f"{total / 1048576:.1f} МБ")
    if not man["registry"]["consistent"]:
        print("Внимание: записи открытого реестра в БД расходятся с файлом — см. manifest.json, registry",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
