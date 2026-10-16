"""Схема и демо-данные БД: версия схемы в таблице meta, сид из shared/seed_data.json (включая исходный журнал) и записи
открытых источников из shared/registry_ust_kamenogorsk.json (собраны импортёром app.importer, отдельный слой без риска)."""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Type, get_args

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Engine

from . import importer, migrate, models, registry
from .ads_service import AD_SOURCES  # noqa: F401 — тексты первого события публикации
from .db import Base, SessionLocal, engine as default_engine
from .pdf import PLATFORMS
from .risk_model import ALMATY, SHARED
from .schemas import AuditKind
from .settings import get_settings

log = logging.getLogger("saq")

SEED = SHARED / "seed_data.json"


def _snake(key: str) -> str:
    return "".join("_" + ch.lower() if ch.isupper() else ch for ch in key)


def _row(model: Type[Base], d: Dict[str, Any]) -> Base:
    """camelCase-ключи верхнего уровня → snake_case; вложенные словари (basis, readiness, photos) — как есть."""
    cols = set(model.__table__.columns.keys())
    data = {_snake(k): v for k, v in d.items() if not k.startswith("_")}
    extra = sorted(set(data) - cols)
    if extra:
        log.warning("Сид: у %s %s пропущены неизвестные поля %s", model.__tablename__, d.get("id"), extra)
    return model(**{k: v for k, v in data.items() if k in cols})


# ---------- исходный журнал ----------

SYSTEM = "Система"
TRI = {"yes": "есть", "no": "нет", "unknown": "нет сведений"}


def utc(value: str, local_time: str = "10:00") -> datetime:
    """Время из сида → UTC. Без смещения — местное время Усть-Каменогорска (UTC+5), как в источниках;
    только дата — фиксация в local_time того же дня. Строки со смещением (+05:00) переводятся как есть."""
    dt = datetime.fromisoformat(value if "T" in value else f"{value}T{local_time}")
    return (dt if dt.tzinfo else dt.replace(tzinfo=ALMATY)).astimezone(timezone.utc)


def _applicants(n: int) -> str:
    tail, tens = n % 10, n % 100
    word = "заявитель" if tail == 1 and tens != 11 else "заявителя" if 2 <= tail <= 4 and not 12 <= tens <= 14 else "заявителей"
    return f"{n} {word}"


def _ad_events(ad: Dict[str, Any], names: Dict[str, str]) -> Iterator[Dict[str, Any]]:
    """Сбор публикации, гипотеза ИИ о ЖК (при сборе) и — если было — подтверждение привязки аналитиком."""
    at = utc(ad["date"])
    cid = ad.get("complexId") if ad.get("complexId") in names else None
    platform = PLATFORMS.get(ad["platform"], ad["platform"])
    yield {"kind": "system", "at": at, "complex_id": cid,
           "what": AD_SOURCES.get(ad.get("source") or "manual", AD_SOURCES["manual"]).format(platform=platform)}
    if not cid:
        yield {"kind": "link", "at": at, "complex_id": None, "what": "ЖК не определён — нужна ручная привязка"}
        return
    conf = int((ad.get("matchConfidence") or 0) * 100 + 0.5)
    when = "при добавлении публикации" if (ad.get("source") or "manual") == "manual" else "при сборе публикации"
    yield {"kind": "ai", "at": at, "complex_id": cid,
           "what": f"Гипотеза ИИ: ЖК «{names[cid]}», уверенность {conf}% (совпадение {when}) — нужна проверка аналитиком"}
    if ad.get("linkConfirmedBy") and ad.get("linkConfirmedAt"):
        yield {"kind": "link", "at": utc(ad["linkConfirmedAt"]), "complex_id": cid, "who": ad["linkConfirmedBy"],
               "what": f"Привязка к ЖК «{names[cid]}» подтверждена аналитиком"}


def _complex_events(c: Dict[str, Any], appeals: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Сведения по ЖК с источником и временем фиксации (ТЗ §4): основание, готовность, обращения, снимки площадки."""
    b = c.get("basis") or {}
    if b.get("source") and b.get("checkedAt"):
        yield {"at": utc(b["checkedAt"]), "what": f"Основание сверено ({b['source']}): гарантия ЕО — "
               f"{TRI.get(b.get('guarantee'), TRI['unknown'])}, разрешение МИО — {TRI.get(b.get('mio'), TRI['unknown'])}, "
               f"банк — {TRI.get(b.get('bvu'), TRI['unknown'])}"}
    for key, label in (("declared", "Заявленная"), ("confirmed", "Подтверждённая")):
        r = (c.get("readiness") or {}).get(key)
        if r and r.get("value") is not None and r.get("source") and r.get("date"):
            yield {"at": utc(r["date"]), "what": f"{label} готовность {r['value']}% — {r['source']}"}
    for ap in appeals:
        head = (f"Поступило коллективное обращение ({_applicants(ap.get('applicants') or 1)})" if ap.get("collective")
                else "Поступило обращение")
        yield {"at": utc(ap["date"]), "what": f"{head}: {ap['subject']} — {ap['source']}"}
        if ap.get("verifiedAt") and ap.get("status") in ("verified", "rejected"):
            yield {"at": utc(ap["verifiedAt"]), "what": "Обращение проверено: подтверждено" if ap["status"] == "verified"
                   else "Обращение отклонено после проверки"}
    for p in c.get("photos") or []:
        note = f". {p['note']}" if p.get("note") else ""
        what = f"Снимок площадки ({p.get('source') or 'источник не указан'}): активность — {p.get('activity') or 'нет сведений'}"
        yield {"at": utc(p["date"], "12:00"), "what": what + note}


def journal(data: Dict[str, Any]) -> List[models.AuditEvent]:
    """Исходный журнал демо-БД, детерминированно: публикации (сбор и привязка), сведения по ЖК, явные события seed.audit."""
    names = {c["id"]: c["name"] for c in data["complexes"]}
    out = [models.AuditEvent(ad_id=ad["id"], **{"who": SYSTEM, **e}) for ad in data["ads"] for e in _ad_events(ad, names)]
    for c in data["complexes"]:
        appeals = [a for a in data.get("appeals", []) if a["complexId"] == c["id"]]
        out += [models.AuditEvent(complex_id=c["id"], kind="data", who=SYSTEM, **e) for e in _complex_events(c, appeals)]
    kinds = set(get_args(AuditKind))
    for e in data.get("audit", []):
        if e.get("kind") not in kinds:
            log.warning("Сид: событие журнала с неизвестным видом %r пропущено: %s", e.get("kind"), e.get("what"))
            continue
        out.append(models.AuditEvent(ad_id=e.get("adId"), complex_id=e.get("complexId"), kind=e["kind"], who=e["who"],
                                     what=e["what"], at=utc(e["at"])))
    return out


def seed(db) -> None:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    db.add_all(_row(models.Analyst, a) for a in data.get("analysts", []))
    db.add_all(_row(models.Developer, d) for d in data["developers"])
    db.flush()
    db.add_all(_row(models.Complex, c) for c in data["complexes"])
    db.flush()
    times = ("linkConfirmedAt", "termsReviewAt")  # время решений аналитика — в DateTime (UTC), как у записей из API
    db.add_all(_row(models.Ad, {**a, **{k: utc(a[k]) for k in times if a.get(k)}}) for a in data["ads"])
    db.add_all(_row(models.Appeal, a) for a in data.get("appeals", []))
    db.flush()
    db.add_all(journal(data))
    db.flush()
    registry.seed(db, importer.load_registry())
    db.flush()


# Таблицы приложения прошлых версий схемы: удаляются при пересоздании вместе с текущими (Base.metadata)
LEGACY_TABLES = {"developers", "complexes", "ads", "audit_events", "appeals", "signals", "meta"}


def _app_tables(eng: Engine) -> set:
    """Таблицы приложения, которые есть в БД; чужие таблицы в той же базе не трогаем."""
    return (set(Base.metadata.tables) | LEGACY_TABLES) & set(inspect(eng).get_table_names())


def _version(eng: Engine, tables: set) -> Optional[str]:
    if "meta" not in tables:
        return None
    with SessionLocal(bind=eng) as db:
        m = db.get(models.Meta, "schema_version")
        return m.value if m else None


def _drop_app_tables(eng: Engine) -> None:
    """DROP только таблиц приложения: по именам, зависимые раньше родительских (без reflect — старая схема
    может ссылаться на уже удалённые таблицы, а reflect всей БД задел бы чужие таблицы)."""
    present = _app_tables(eng)
    order = sorted(LEGACY_TABLES - set(Base.metadata.tables)) + [t.name for t in reversed(Base.metadata.sorted_tables)]
    with eng.begin() as conn:
        for name in order:
            if name in present:
                Table(name, MetaData()).drop(conn)


def init_db(eng: Engine = default_engine) -> None:
    """Нет таблиц приложения → таблицы + сид. Известная прошлая версия схемы → обновление на месте с резервной копией
    (migrate.upgrade), данные сохраняются. Неизвестная версия (или нет meta) → таблицы приложения пересоздаются и
    пересеваются, но только если в базе нет собранных/внесённых данных (migrate.durable_rows); иначе SchemaError —
    пока не задан SAQ_ALLOW_SCHEMA_RESET=1. Посторонние таблицы в той же БД остаются как есть."""
    tables = _app_tables(eng)
    if tables:
        ver = _version(eng, tables)
        if ver == models.SCHEMA_VERSION:
            pass
        elif migrate.chain_from(ver) is not None:
            migrate.upgrade(eng, ver)
        else:
            found = migrate.durable_rows(eng, tables)
            if found and not get_settings().allow_schema_reset:
                raise migrate.SchemaError(
                    f"Схема БД неизвестной версии ({ver or 'без версии'}), а в ней есть собранные/внесённые данные "
                    f"({', '.join(f'{k}: {v}' for k, v in found.items())}). Сделайте резервную копию и перенесите данные "
                    "вручную или запустите с SAQ_ALLOW_SCHEMA_RESET=1, чтобы пересоздать таблицы приложения")
            log.warning("Схема демо-БД устарела (%s, нужна %s): таблицы приложения пересоздаются, данные пересеваются из %s",
                        ver or "без версии", models.SCHEMA_VERSION, SEED)
            _drop_app_tables(eng)
    Base.metadata.create_all(eng)
    with SessionLocal(bind=eng) as db:
        db.merge(models.Meta(key="schema_version", value=models.SCHEMA_VERSION))
        if not db.scalar(select(models.Developer.id).limit(1)):
            seed(db)
        db.commit()


def reset(eng: Engine = default_engine) -> None:
    """Чистая пересеянная БД (тесты, ручной сброс демо-данных)."""
    _drop_app_tables(eng)
    init_db(eng)
