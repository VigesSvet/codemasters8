"""Стартовый набор данных: текущие данные рабочей БД - в репозиторий(shared/snapshot/), оттуда - в пустую БД нового
развёртывания(seed.start при старте API).

    python -m app.snapshot export [--out DIR] [--max-px 1024] [--quality 75]   # выгрузить из DATABASE_URL и MEDIA_ROOT
    python -m app.snapshot import [--dir DIR]                                  # загрузить в пустую БД(иначе - отказ)

Выгрузка только читает: БД - одной транзакцией(PostgreSQL: REPEATABLE READ, READ ONLY), файлы - из MEDIA_ROOT.
Схему не меняет: таблица читается столбцами, которые есть и в модели, и в БД(версия схемы БД - в manifest.json).
Результат(каталог заменяется целиком, только если всё получилось и проверка на секреты пройдена):
- <таблица>.json - строки по возрастанию первичного ключа, ключи по алфавиту, время - ISO 8601 в UTC;
- media/<sha256>.<ext> - изображения публикаций, уменьшенные до max-px по длинной стороне(по умолчанию 1024 px, JPEG
  q75; PNG - только если он меньше; исходный JPEG без EXIF в пределах размера - как есть, если перекодирование не
  экономит 10 %). Строки media_assets получают новые размер, SHA-256, MIME, ключ хранилища и width/height; исходные -
  в meta.snapshot;
- api/<имя>.json - ответы GET /api/… по этому набору(как их отдаст новое развёртывание) для офлайн-режима фронтенда и
  api/media.json - id файла → имя в media/;
- manifest.json - формат, версия схемы, время выгрузки, число строк по таблицам, что не вошло и почему.

Что не входит: видео(строка остаётся "недоступно" с пояснением VIDEO_NOTE и ссылкой на оригинал), кадры видео(строки
удаляются - распознанный по ним текст уже в ads.ocr), задания воркера и аренды, состояния OAuth, токены, счётчики квот,
заявки жителей(bot_submissions - персональные данные заявителей), записи открытого реестра(source_records - их
пересоздаёт загрузка из shared/registry_ust_kamenogorsk.json; выгрузка сверяет их с файлом), кэш кандидатов 2ГИС
(решения аналитика остаются, несостоявшиеся проверки - нет). Подключения - настройки и "включено"; статус проверки,
ошибки, курсоры и время следующего запуска сбрасываются: новое развёртывание проверит подключения заново. Снимки
ответов источников(ingest_*.raw, extra) ещё раз проходят ingest.scrub, тексты ошибок - logs.redact. Перед заменой
каталога все файлы проверяются на значения секретов из настроек и на токеноподобные строки(leaks) - найдено -
выгрузка прерывается.

Загрузка(import и seed.start) - в одной транзакции: таблицы в порядке зависимостей с прежними первичными ключами
(PostgreSQL: последовательности - за максимальный id), файлы - в хранилище через media.store_bytes(тип по сигнатуре,
SHA-256 и ключ сверяются с записью), записи реестра - из shared/registry_ust_kamenogorsk.json(без нового события
журнала: событие о загрузке реестра уже в наборе), флаг meta demo_data = purged. Непустую БД(есть строки хотя бы в
одной таблице набора) загрузка не трогает. Набор прежней известной версии схемы загружается: столбцы, которых в нём
нет, добавлены шагами migrate(nullable или с server_default) и получают NULL или значение по умолчанию, таблицы,
которые добавили шаги после его версии(migrate.tables_after, например screening_decisions), - пустые; набор
неизвестной или более новой версии - отказ(выгрузите набор заново).
"""
import argparse
import hashlib
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
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import DateTime, Integer, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from . import importer, ingest, logs, media, migrate, models, registry
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
Obj = Dict[str, Any]


class SnapshotError(RuntimeError):
    """Набор не выгружен или не загружен: причина по-русски."""


# ---------- что входит в набор ----------

@dataclass
class Ctx:
    """Состояние выгрузки: время, каталог файлов, сжатые изображения(исходный SHA-256 → файл набора), счётчики."""
    at: datetime
    media_dir: Path
    max_px: int = MAX_PX
    quality: int = QUALITY
    files: Dict[str, Tuple[str, str, int, int, int]] = field(default_factory=dict)  # sha → (sha, mime, size, w, h)
    dropped_media: set = field(default_factory=set)
    stats: Dict[str, int] = field(default_factory=dict)

    def count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n


def _collector(row: Obj, ctx: Ctx) -> Obj:
    """Настройки и "включено" остаются; статус, факты(ключи, аккаунты), курсоры и расписание - как у новой строки."""
    return {**row, "status": "unchecked", "status_detail": None, "facts": {}, "state": {}, "failures": 0,
            "next_run_at": None, "last_success_at": None, "last_error_at": None, "checked_at": None,
            "last_run_id": None, "last_error": None}


def _run(row: Obj, ctx: Ctx) -> Obj:
    notes = [{**n, "text": logs.redact(n.get("text"), limit=None)} if isinstance(n, dict) else n
             for n in row.get("notes") or []]
    return {**row, "error": logs.redact(row.get("error"), limit=None), "notes": notes}


def _item(row: Obj, ctx: Ctx) -> Obj:
    return {**row, "raw": ingest.scrub(row.get("raw") or {}),
            "analysis_error": logs.redact(row.get("analysis_error"), limit=None)}


def _version(row: Obj, ctx: Ctx) -> Obj:
    return {**row, "raw": ingest.scrub(row.get("raw") or {}), "extra": ingest.scrub(row.get("extra") or {})}


def _llm(row: Obj, ctx: Ctx) -> Obj:
    # request-id Anthropic наружу не выводится и набору не нужен
    return {**row, "request_id": None, "error": logs.redact(row.get("error"), limit=None)}


def _geo(row: Obj, ctx: Ctx) -> Optional[Obj]:
    """Решения аналитика остаются(выбранный дом, "было → стало"); кандидаты 2ГИС - временный кэш, его нет в наборе.
    Проверка без решения(только кандидаты) не выгружается."""
    if row.get("status") == "candidates":
        ctx.count("geo_checks: без решения — не выгружены")
        return None
    return {**row, "candidates": [], "purged_at": row.get("purged_at") or ctx.at.isoformat()}


def _media(row: Obj, ctx: Ctx) -> Optional[Obj]:
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
    src_sha = row.get("sha256") or ""
    got = ctx.files.get(src_sha)
    if got is None:
        try:
            data = media.path_of(row["storage_key"]).read_bytes()
        except (OSError, ValueError) as e:
            raise SnapshotError(f"Файл {row['id']} ({row['storage_key']}) не прочитан из MEDIA_ROOT: "
                                f"{type(e).__name__}") from e
        out, mime, w, h = compress(data, ctx.max_px, ctx.quality)
        sha = hashlib.sha256(out).hexdigest()
        name = f"{sha}{media.ALLOWED[mime]}"
        (ctx.media_dir / name).write_bytes(out)
        got = ctx.files[src_sha] = (sha, mime, len(out), w, h)
        ctx.count("media: исходных байт", len(data))
    sha, mime, size, w, h = got
    if sha != src_sha or "snapshot" not in meta:  # файл набора уже в наборе(повторная выгрузка) - сведения прежние
        meta["snapshot"] = {"sourceSha256": row.get("sha256"), "sourceSize": row.get("size"),
                            "sourceMime": row.get("mime"), "maxPx": ctx.max_px, "quality": ctx.quality}
    meta.update(width=w, height=h)
    ctx.count("media_assets: изображения")
    return {**row, "sha256": sha, "mime": mime, "size": size, "storage_key": media._key(sha, mime)}


@dataclass(frozen=True)
class Spec:
    model: type
    prepare: Optional[Callable[[Obj, Ctx], Optional[Obj]]] = None  # строка БД → строка набора(None - не выгружать)

    @property
    def name(self) -> str:
        return self.model.__tablename__


# Порядок загрузки - по зависимостям(внешние ключи): родительские таблицы раньше
TABLES: Tuple[Spec, ...] = (
    Spec(models.Analyst), Spec(models.Developer), Spec(models.Complex), Spec(models.Ad), Spec(models.Appeal),
    Spec(models.Signal), Spec(models.AuditEvent), Spec(models.Collector, _collector), Spec(models.CollectionRun, _run),
    Spec(models.IngestItem, _item), Spec(models.IngestVersion, _version), Spec(models.MediaAsset, _media),
    Spec(models.LlmAnalysis, _llm), Spec(models.GeoCheck, _geo), Spec(models.DiscoveredObject),
    Spec(models.DiscoveredMention), Spec(models.ScreeningDecision),
)
EXCLUDED: Dict[str, str] = {
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
}
# Ответы API для офлайн-режима фронтенда: имя файла → путь GET(asOf - дата расчёта риска и "Проверки по рекламе")
VIEWS: Tuple[Tuple[str, str, bool], ...] = (
    ("complexes", "/api/complexes", True), ("developers", "/api/developers", True), ("ads", "/api/ads", False),
    ("appeals", "/api/appeals", False), ("risk", "/api/risk", True), ("stats", "/api/stats", True),
    ("signals", "/api/signals", False), ("analysts", "/api/analysts", False), ("discovered", "/api/discovered", False),
)


# ---------- изображения ----------

KEEP_GAIN = 0.9  # исходный JPEG в пределах размера остаётся как есть, если перекодирование не экономит хотя бы 10 %


def compress(data: bytes, max_px: int = MAX_PX, quality: int = QUALITY) -> Tuple[bytes, str, int, int]:
    """Изображение → (байты, MIME, ширина, высота): длинная сторона не больше max_px(с учётом поворота EXIF), без
    метаданных; JPEG с качеством quality, PNG - только если он меньше JPEG(прозрачность - на белом фоне для JPEG).
    Исходный JPEG без EXIF и не больше max_px остаётся как есть, если перекодирование не экономит 10 %(KEEP_GAIN): так
    повторная выгрузка уже сжатого набора не меняет файлы."""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as src:
        src.load()
        fmt, has_exif = src.format, bool(src.getexif())
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
    rgb.save(buf, format="JPEG", quality=quality, optimize=True)
    best = (buf.getvalue(), "image/jpeg")
    if fmt != "JPEG" or alpha:
        buf = io.BytesIO()
        (im if im.mode in ("RGB", "RGBA", "L", "LA", "P") else rgb).save(buf, format="PNG", optimize=True)
        if len(buf.getvalue()) < len(best[0]):
            best = (buf.getvalue(), "image/png")
    if fmt == "JPEG" and not has_exif and (w, h) == im.size and len(best[0]) > KEEP_GAIN * len(data):
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


def leaks(text_: str, secrets: Iterable[str] = ()) -> List[str]:
    """Что похоже на секрет в тексте: названия находок(не сами значения). secrets - значения секретов из настроек
    (короче 8 символов не проверяются)."""
    found = [name for name, rx in TOKENISH if rx.search(text_)]
    if any(len(s) >= 8 and s in text_ for s in secrets):
        found.append("значение секрета из настроек")
    return found


def scan(root: Path, secrets: Iterable[str] = ()) -> Dict[str, List[str]]:
    """Текстовые файлы набора(*.json) с находками leaks: относительный путь → названия находок."""
    secrets = [s for s in secrets if s]
    out: Dict[str, List[str]] = {}
    for p in sorted(root.rglob("*.json")):
        found = leaks(p.read_text(encoding="utf-8"), secrets)
        if found:
            out[str(p.relative_to(root))] = found
    return out


# ---------- выгрузка ----------

def _jsonable(col, v: Any) -> Any:
    if v is not None and isinstance(col.type, DateTime):
        return models.as_utc(v).isoformat()
    return v


def _read(conn: Connection, model) -> Tuple[List[str], List[Obj]]:
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


def _schema_version(conn: Connection) -> Optional[str]:
    if "meta" not in inspect(conn).get_table_names():
        return None
    return conn.execute(text("SELECT value FROM meta WHERE key = 'schema_version'")).scalar()


def registry_check(conn: Connection) -> Obj:
    """Записи открытого реестра в БД против shared/registry_ust_kamenogorsk.json: загрузка набора пересоздаёт их из
    файла, расхождение(импорт через интерфейс после сборки файла) - предупреждение в manifest.json."""
    data = importer.load_registry() or {"records": []}
    want = {(r["source"], r["sourceRecordId"]): registry._values(r) for r in data["records"]}
    cols = list(registry.COLUMNS.values()) + ["collected_at"]
    R = models.SourceRecord
    have = {}
    if R.__tablename__ in inspect(conn).get_table_names():
        for r in conn.execute(select(R.source, R.source_record_id, *[getattr(R, c) for c in cols])):
            have[(r[0], r[1])] = dict(zip(cols, r[2:]))
    differ = sorted(f"{s}:{i}" for (s, i), v in want.items() if (s, i) in have and any(
        (models.iso(have[(s, i)][c]) != models.iso(v[c])) if c == "collected_at" else have[(s, i)][c] != v[c]
        for c in cols))
    return {"file": importer.REGISTRY.name, "inFile": len(want), "inDb": len(have),
            "onlyInDb": sorted(f"{s}:{i}" for s, i in set(have) - set(want)),
            "onlyInFile": sorted(f"{s}:{i}" for s, i in set(want) - set(have)), "differ": differ,
            "consistent": not differ and set(have) == set(want)}


def _swap(new: Path, out: Path) -> None:
    """Каталог набора заменяется целиком: прежний - в сторону, новый - на его место, прежний удаляется."""
    old = out.with_name(out.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out.exists():
        out.rename(old)
    new.rename(out)
    if old.exists():
        shutil.rmtree(old)


def export(eng: Engine, out: Path = DIR, *, max_px: int = MAX_PX, quality: int = QUALITY, views: bool = True,
           now: Optional[datetime] = None) -> Obj:
    """Выгрузка набора из БД eng и хранилища MEDIA_ROOT в каталог out(заменяется целиком). Возвращает manifest."""
    at = models.as_utc(now) or models.utcnow().replace(microsecond=0)
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{out.name}.new-", dir=out.parent))
    try:
        os.chmod(work, 0o755)  # mkdtemp создаёт 0700, а каталог набора - обычный каталог репозитория
        (work / "media").mkdir()
        ctx = Ctx(at=at, media_dir=work / "media", max_px=max_px, quality=quality)
        tables: Dict[str, Obj] = {}
        with eng.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            version = _schema_version(conn)
            if version != models.SCHEMA_VERSION and migrate.chain_from(version) is None:
                raise SnapshotError(f"Схема БД — {version or 'без версии'}: выгрузка умеет только версии из цепочки "
                                    f"migrate (текущая {models.SCHEMA_VERSION})")
            for spec in TABLES:
                cols, rows = _read(conn, spec.model)
                if spec.prepare is not None:
                    rows = [x for x in (spec.prepare(r, ctx) for r in rows) if x is not None]
                if spec.name == "media_assets" and ctx.dropped_media:
                    rows = [{**r, "parent_id": None} if r.get("parent_id") in ctx.dropped_media else r for r in rows]
                (work / f"{spec.name}.json").write_text(dump(rows), encoding="utf-8")
                tables[spec.name] = {"file": f"{spec.name}.json", "rows": len(rows), "columns": cols}
            reg = registry_check(conn)
            conn.rollback()
        files = sorted((work / "media").iterdir())
        media_map = {r["id"]: Path(r["storage_key"]).name
                     for r in json.loads((work / "media_assets.json").read_text(encoding="utf-8"))
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
        r = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise SnapshotError(f"Ответы API по набору не построены (код {r.returncode}): "
                                f"{logs.redact((r.stderr or r.stdout or '').strip()[-2000:], limit=None)}")


def build_views(src: Path, as_of: date) -> List[str]:
    """(в отдельном процессе) Набор src → пустая БД DATABASE_URL(без файлов) → GET /api/… → src/api/<имя>.json."""
    from functools import partial

    from fastapi.testclient import TestClient

    from . import seed
    from .db import engine
    from .main import app

    if manifest(src) is None:
        raise SnapshotError(f"Нет набора для ответов API: {src / MANIFEST}")
    seed.init_db(engine, start=partial(seed.start, src=src, files=False))
    client = TestClient(app)  # без lifespan: схема и набор уже загружены
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
    rows: Dict[str, int] = field(default_factory=dict)
    files: int = 0
    registry: int = 0
    exported_at: Optional[str] = None
    schema: Optional[str] = None

    def line(self) -> str:
        main = ", ".join(f"{k} — {v}" for k, v in self.rows.items() if k in (
            "complexes", "developers", "ads", "llm_analyses", "audit_events", "discovered_objects"))
        return (f"стартовый набор от {self.exported_at or '—'} (схема {self.schema}): {main}; файлов — {self.files}; "
                f"записей открытого реестра — {self.registry}")


def manifest(src: Path = DIR) -> Optional[Obj]:
    p = Path(src) / MANIFEST
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def is_empty(db: Session) -> bool:
    """В таблицах набора нет ни одной строки(новая БД): только в такую загружается набор."""
    return all(db.scalar(select(func.count()).select_from(s.model.__table__)) == 0 for s in TABLES)


def _compatible(version: Optional[str]) -> bool:
    return version == models.SCHEMA_VERSION or migrate.chain_from(version) is not None


def _prepare(table, rows: List[Obj], name: str) -> List[Obj]:
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
                v[k] = models.as_utc(datetime.fromisoformat(v[k]))
        out.append(v)
    return out


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


def _restore_files(src: Path, rows: List[Obj]) -> int:
    """Изображения набора → хранилище(media.store_bytes: тип по сигнатуре, место на диске), ключ и SHA-256 -
    сверяются."""
    done: Dict[str, str] = {}
    for r in rows:
        key = r.get("storage_key")
        if not key or r.get("status") != "downloaded" or key in done:
            continue
        path = src / "media" / Path(key).name
        try:
            data = path.read_bytes()
        except OSError as e:
            raise SnapshotError(f"Нет файла набора {path.name} (запись {r['id']}): {type(e).__name__}") from e
        stored = media.store_bytes(data, allowed=media.IMAGE_TYPES)
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
    rep = LoadReport(exported_at=man.get("exportedAt"), schema=man.get("schemaVersion"))
    media_rows: List[Obj] = []
    later = migrate.tables_after(man.get("schemaVersion"))
    for s in TABLES:
        info = (man.get("tables") or {}).get(s.name)
        if info is None and s.name not in later:
            raise SnapshotError(f"В наборе нет таблицы {s.name}")
        # таблица, которую добавил шаг migrate после версии набора, - пустая(как после обновления схемы)
        rows = json.loads((src / info["file"]).read_text(encoding="utf-8")) if info is not None else []
        if s.name == "media_assets":  # кадры(parent_id) - после своих видео
            rows.sort(key=lambda r: r.get("parent_id") is not None)
            media_rows = rows
        values = _prepare(s.model.__table__, rows, s.name)
        for i in range(0, len(values), CHUNK):
            db.execute(s.model.__table__.insert(), values[i:i + CHUNK])
        rep.rows[s.name] = len(values)
    db.flush()
    _reset_sequences(db)
    data = importer.load_registry()
    if data and data.get("records"):
        meta = data.get("meta") or {}
        at = datetime.fromisoformat(meta["builtAt"]) if meta.get("builtAt") else models.utcnow()
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
    holder: Dict[str, LoadReport] = {}

    def start(db: Session) -> None:
        from . import demo

        holder["rep"] = load(db, src)
        demo.import_registry(db)  # на согласованном наборе - без изменений(как seed.start)

    try:
        seed.init_db(eng, start=start)
    except SnapshotError as e:
        print(str(e), file=sys.stderr)
        return 2
    print("Загружен " + holder["rep"].line())
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.snapshot", description="Стартовый набор данных (shared/snapshot)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("export", help="выгрузить текущие данные БД и файлы в каталог набора")
    pe.add_argument("--out", type=Path, default=DIR, help=f"каталог набора (по умолчанию {DIR})")
    pe.add_argument("--max-px", type=int, default=MAX_PX, help=f"длинная сторона изображения, px ({MAX_PX})")
    pe.add_argument("--quality", type=int, default=QUALITY, help=f"качество JPEG ({QUALITY})")
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
