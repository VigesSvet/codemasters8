"""Демо-набор - только для тестов: вымышленные ЖК, застройщики, публикации, обращения, аналитики и исходный журнал из
shared/testdata/demo_seed.json. Приложение его не засевает и не отдаёт(пустая БД приложения получает стартовый набор
shared/snapshot - app.snapshot); тесты подставляют свой старт пустой БД: seed.START = start(conftest.py).

start - пустая БД получает демо-набор(как прежний демо-режим; после python -m app.demo purge - как live_start);
live_start - "боевой" старт без демо-набора и без стартового набора: аналитики для входа, открытый реестр и карточки ЖК
из него(прежний SAQ_DEMO_DATA=0); live() - переключить старт на live_start и пересоздать БД."""
import json
import logging
from datetime import datetime, UTC
from typing import Any, get_args
from collections.abc import Iterator

from sqlalchemy import select

from app import demo, importer, models, registry, seed as app_seed
from app.ads_service import AD_SOURCES
from app.db import Base
from app.pdf import PLATFORMS
from app.risk_model import ALMATY, SHARED
from app.schemas import AuditKind

log = logging.getLogger("saq")

SEED = SHARED / "testdata" / "demo_seed.json"
DATA: dict[str, Any] = json.loads(SEED.read_text(encoding="utf-8"))


def _snake(key: str) -> str:
    return "".join("_" + ch.lower() if ch.isupper() else ch for ch in key)


def _row(model: type[Base], d: dict[str, Any]) -> Base:
    """camelCase-ключи верхнего уровня → snake_case; вложенные словари(basis, readiness, photos) - как есть."""
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
    """Время из сида → UTC. Без смещения - местное время Усть-Каменогорска(UTC+5), как в источниках;
    только дата - фиксация в local_time того же дня. Строки со смещением(+05:00) переводятся как есть."""
    dt = datetime.fromisoformat(value if "T" in value else f"{value}T{local_time}")
    return (dt if dt.tzinfo else dt.replace(tzinfo=ALMATY)).astimezone(UTC)


def _applicants(n: int) -> str:
    tail, tens = n % 10, n % 100
    word = "заявитель" if tail == 1 and tens != 11 else "заявителя" if 2 <= tail <= 4 and not 12 <= tens <= 14 else "заявителей"
    return f"{n} {word}"


def _ad_events(ad: dict[str, Any], names: dict[str, str]) -> Iterator[dict[str, Any]]:
    """Сбор публикации, гипотеза ИИ о ЖК(при сборе) и - если было - подтверждение привязки аналитиком."""
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


def _complex_events(c: dict[str, Any], appeals: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Сведения по ЖК с источником и временем фиксации(ТЗ §4): основание, готовность, обращения, снимки площадки."""
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


def journal(data: dict[str, Any]) -> list[models.AuditEvent]:
    """Исходный журнал демо-БД, детерминированно: публикации(сбор и привязка), сведения по ЖК, явные события seed.audit."""
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
    """Демо-набор целиком: аналитики, застройщики, ЖК, публикации, обращения, исходный журнал и открытый реестр."""
    data = DATA
    db.add_all(_row(models.Analyst, a) for a in data.get("analysts", []))
    db.add_all(_row(models.Developer, d) for d in data["developers"])
    db.flush()
    db.add_all(_row(models.Complex, c) for c in data["complexes"])
    db.flush()
    times = ("linkConfirmedAt", "termsReviewAt")  # время решений аналитика - в DateTime(UTC), как у записей из API
    db.add_all(_row(models.Ad, {**a, **{k: utc(a[k]) for k in times if a.get(k)}}) for a in data["ads"])
    db.add_all(_row(models.Appeal, a) for a in data.get("appeals", []))
    db.flush()
    db.add_all(journal(data))
    db.flush()
    registry.seed(db, importer.load_registry())
    db.flush()


# ---------- старт пустой БД в тестах ----------

def live_start(db) -> None:
    """Без демо-набора и без стартового набора: аналитики для входа(если их нет), открытый реестр(если записей нет) и
    карточки ЖК из него."""
    if db.scalar(select(models.Analyst.id).limit(1)) is None:
        db.add_all(_row(models.Analyst, a) for a in DATA.get("analysts", []))
        db.flush()
    demo.import_registry(db)


def start(db) -> None:
    """Пустая БД - демо-набор; после python -m app.demo purge(флаг demo_data) - как live_start."""
    if demo.purged(db):
        live_start(db)
    elif not db.scalar(select(models.Developer.id).limit(1)):
        seed(db)


def live() -> None:
    """Тест "боевого" режима: старт без демо-набора(live_start) и пересоздание БД. Прежний старт вернёт автофикстура
    conftest._demo_start."""
    app_seed.START = live_start
    app_seed.reset()
