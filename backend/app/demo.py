"""Демо-набор прежних версий(удаление из БД) и карточки ЖК из открытого реестра.

Демо-набора в приложении больше нет: новая БД получает стартовый набор реальных данных(app.snapshot, shared/snapshot),
вымышленные данные - только у тестов(shared/testdata). purge нужен базам прежних версий, засеянным демо-набором.

    python -m app.demo purge --dry-run     # что будет удалено и что останется - без изменений
    python -m app.demo purge --yes         # удалить демо-данные(SQLite - сначала резервная копия)
    python -m app.demo import-registry     # создать или дополнить карточки ЖК и застройщиков по открытому реестру
    python -m app.demo add-advertised --file x.json [--author ИМЯ] [--note ТЕКСТ]   # ЖК по рекламе(нет в реестре)

Удаляется только демо-набор(origin "demo"): публикации и всё, что на них висит(журнал, анализы ИИ, записи приёма, файлы,
задания), обращения, сигналы и события журнала по демо-ЖК, явные события журнала из сида, сами демо-ЖК и демо-застройщики.
Собранные и внесённые аналитиком публикации не удаляются: привязка к демо-ЖК снимается с записью в журнал. Остаются
аналитики(для входа), записи открытого реестра, карточки ЖК из реестра, подключения и запуски сбора, файлы, задания,
анализы ИИ собранных публикаций и счётчики квот. В той же транзакции ставится флаг meta "demo_data" = "purged"(его же
ставит загрузка стартового набора, app.snapshot); повторный purge ничего не удаляет. Вернуть демо-набор нельзя - его в
приложении нет.

Карточки ЖК из открытого реестра(origin "registry"): по всем объектам(группы записей source_records, как
importer.group), в том числе без координат(координаты - после сверки 2ГИС) - застройщик "rd-<БИН>"(название и БИН
источника) и ЖК "r-<ключ объекта>": название без "ЖК "…"", адрес, координаты(если есть), застройщик; начало и плановое завершение - только если записи объекта согласны между собой(завершение - только
у строящегося по источнику объекта). Стадия, готовность, основание(гарантия ЕО / МИО / БВУ) остаются "нет сведений":
"Завершён"/"В строительстве" источника - фильтр, а не стадия, а документ источника требует проверки применимости; его номер -
справкой в guaranteeRef / mioRef / bank. Повторный импорт идемпотентен и только дополняет пустые поля(и координаты, если
их не было) - правки аналитика(например, координаты из 2ГИС) не перезаписываются. Риск по такой карточке не оценивается до
проверенных сведений(risk.assessed). Основание по открытым данным(раздел "Без разрешения на дольщиков", документ
Homeportal, запись Казреестра) карточка получает в "Проверке по рекламе"(app.screening.permits).

ЖК по рекламе(origin "advertised", add-advertised): JSON-файл - список [{name, aliases[], address, lat?, lng?,
developerName?, developerBin?, accounts[], sourceUrls[], note?}] или {"note", "items": [...]}; повтор идемпотентен
(app.discovery.import_advertised).
"""
import argparse
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import importer, migrate, models, registry

log = logging.getLogger("saq")

Obj = dict[str, Any]
SYSTEM = "Система"
FLAG = "demo_data"  # ключ флага в meta
PURGED = "purged"
REGISTRY = "registry"  # origin карточек ЖК и застройщиков из открытого реестра
ID_MAX = 32  # длина id ЖК и застройщика(String(32))
PG_DUMP = "docker compose exec -T db pg_dump -U saq saq > backup.sql"
UNLINKED = "Привязка снята: демо-ЖК «{name}» удалён"


# ---------- флаг "демо удалены" ----------

def purged(db: Session) -> bool:
    m = db.get(models.Meta, FLAG)
    return bool(m and m.value == PURGED)


# ---------- карточки ЖК из открытого реестра ----------

_WRAP = re.compile(r"^\s*(?:мжк|жк|жилой\s+комплекс)\s*[«\"“„']\s*(?P<n>[^»\"”“']+?)\s*[»\"”“'](?P<rest>.*)$", re.I)
_PREFIX = re.compile(r"^\s*(?:мжк|жк|жилой\s+комплекс)\s+", re.I)
_CITY = re.compile(r"^\s*г\.?\s*усть-каменогорск\s*,?\s*", re.I)
# способ привлечения средств → поле-справка основания, как назвать документ и что добавить после номеров
FUNDING_REF = {"guarantee": ("guaranteeRef", "гарантия ЕО (КЖК)", ""), "mio": ("mioRef", "разрешение МИО", ""),
               "bvu": ("bank", "участие БВУ, документ", ", банк источником не указан")}
REF_DOCS_MAX = 3


def complex_id(key: str) -> str:
    """"r-<ключ объекта>"; ключ длиннее 30 символов - "rh-<sha1[:16]>"(id ЖК - String(32))."""
    cid = f"r-{key}"
    if len(cid) <= ID_MAX:
        return cid
    return "rh-" + hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def developer_id(bin_: str | None, name: str | None) -> str | None:
    """"rd-<БИН>"; без БИН - "rd-<sha1 названия организации>"; без застройщика - None."""
    if bin_:
        return f"rd-{bin_}"
    org = importer.org_name(name)
    return "rd-" + hashlib.sha1(org.encode("utf-8"), usedforsecurity=False).hexdigest()[:12] if org else None


def display_name(name: str) -> str:
    """"ЖК "Orda"" → "Orda", "МЖК "PARK LINE"" → "PARK LINE", "ЖК "Престиж"(Sparkle Diamond)" → "Престиж(Sparkle
    Diamond)"; без обёртки - как у источника."""
    m = _WRAP.match(name or "")
    if m:
        return " ".join((m["n"] + m["rest"]).split())
    return " ".join(_PREFIX.sub("", name or "").split()) or (name or "").strip()


def _address(addr: str | None) -> str:
    """Адрес без "г. Усть-Каменогорск," - как у карточек ЖК(город добавляет интерфейс; ИИ сравнивает улицу и дом)."""
    return _CITY.sub("", addr or "").strip()


def _single(rs: list[Obj], f: str) -> str:
    """Значение поля, если все записи объекта, где оно есть, согласны; иначе(разные очереди, блоки) - ""(нет сведений)."""
    vals = {r.get(f) for r in rs if r.get(f)}
    return vals.pop() if len(vals) == 1 else ""


def _building(stage: str | None) -> bool:
    """Источник относит объект к строящимся("В строительстве(фильтр источника)"), а не к завершённым."""
    t = (stage or "").lower()
    return "строительств" in t and "заверш" not in t


def _fmt(d: str | None) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if d and len(d) >= 10 else ""


def _ordered(rs: list[Obj]) -> list[Obj]:
    order = {s: i for i, s in enumerate(importer.SOURCE_ORDER)}
    return sorted(rs, key=lambda r: (order.get(r["source"], 9), r.get("row") or 0, r.get("id") or 0))


def _refs(rs: list[Obj]) -> Obj:
    """Документы основания, которые называет источник, - справкой(guaranteeRef / mioRef / bank), без статуса "есть"."""
    out: Obj = {}
    for method, (ref, label, tail) in FUNDING_REF.items():
        by_src: dict[str, list[str]] = {}
        for r in _ordered(rs):
            if r.get("fundingMethod") != method:
                continue
            num = r.get("docNumber") or ""
            doc = (num if "№" in num else f"№ {num}" if num else "без номера") + (
                f" от {_fmt(r['docDate'])}" if r.get("docDate") else "")
            docs = by_src.setdefault(importer.SOURCES.get(r["source"], {}).get("name", r["source"]), [])
            if doc not in docs:
                docs.append(doc)
        if not by_src:
            continue
        parts = []
        for src, docs in by_src.items():
            more = f" и ещё {len(docs) - REF_DOCS_MAX}" if len(docs) > REF_DOCS_MAX else ""
            parts.append(f"По данным открытого реестра ({src}): {label} {'; '.join(docs[:REF_DOCS_MAX])}{more}{tail}")
        out[ref] = ("; ".join(parts) + ". Аналитиком не проверено")[:500]
    return out


def complex_values(o: Obj, rs: list[Obj]) -> Obj:
    """Поля карточки ЖК по объекту реестра(o - importer.group, rs - его записи). Неизвестное - пусто/0/"unknown"."""
    return {
        "name": display_name(o["name"])[:255],
        "address": _address(o.get("address"))[:255],
        "district": "",
        "lat": o["lat"], "lng": o["lng"],
        "stage": "", "progress": 0, "floors": 0, "flats": 0,
        "start": _single(rs, "start"),
        "planned_end": _single(rs, "plannedEnd") if _building(o.get("stage")) else "",
        "basis": {"guarantee": "unknown", "mio": "unknown", "bvu": "unknown", **_refs(rs)},
        "readiness": {"declared": None, "confirmed": None},
        "in_published_list": "unknown", "cover": "", "photos": [], "idle_days": 0,
    }


FILL = ("name", "address", "start", "planned_end")  # дополняются, только если пусты


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # "название: причина"
    developers_created: list[str] = field(default_factory=list)
    developers_updated: list[str] = field(default_factory=list)

    def line(self) -> str:
        out = (f"Карточки ЖК из открытого реестра: создано {len(self.created)}, дополнено {len(self.updated)}, без изменений "
               f"{len(self.unchanged)}; застройщиков создано {len(self.developers_created)}")
        if self.developers_updated:
            out += f", дополнено {len(self.developers_updated)}"
        if self.skipped:
            out += f"; пропущено {len(self.skipped)} ({'; '.join(self.skipped)})"
        return out


def _objects(db: Session) -> list[tuple[Obj, list[Obj]]]:
    """Объекты реестра(сначала с координатами, затем без - как "objects" и "withoutCoords" в
    shared/registry_ust_kamenogorsk.json) и их записи."""
    rs = registry.records(db)
    by: dict[str, list[Obj]] = {}
    for r in rs:
        by.setdefault(r["key"], []).append(r)
    split = importer.split(importer.group(rs))
    return [(o, by[o["key"]]) for o in split["objects"] + split["withoutCoords"]]


def _sources_line(o: Obj, rs: list[Obj]) -> str:
    names = ", ".join(importer.SOURCES[s]["name"] for s in o["sources"] if s in importer.SOURCES)
    got = o.get("collectedAt")
    return f"{names} (записей: {len(rs)}" + (f", собраны {_fmt(got)}" if got else "") + ")"


def import_registry(db: Session, *, dry_run: bool = False, at: datetime | None = None) -> ImportReport:
    """Создать недостающие и дополнить пустые поля карточек ЖК и застройщиков из открытого реестра(в сессии db, без
    commit). Записей реестра нет - сначала загружается shared/registry_ust_kamenogorsk.json(registry.seed).
    Повторный вызов ничего не меняет. События журнала - по каждой созданной и дополненной карточке."""
    rep = ImportReport()
    now = at or models.utcnow()
    if not dry_run and db.scalar(select(models.SourceRecord.id).limit(1)) is None:
        registry.seed(db, importer.load_registry())
        db.flush()
    devs: dict[str, models.Developer] = {d.id: d for d in db.scalars(select(models.Developer))}
    for o, rs in _objects(db):
        label = display_name(o["name"])
        did = developer_id(o.get("developerBin"), o.get("developer"))
        if did is None:
            rep.skipped.append(f"{label}: застройщик источником не указан")
            continue
        d = devs.get(did)
        if d is None:
            d = models.Developer(id=did, name=(o.get("developer") or "")[:255], bin=o.get("developerBin") or "",
                                 director="", registered="", objects=0, unfinished=0, site=None, instagram=None,
                                 phones=[], origin=REGISTRY)
            devs[did] = d
            rep.developers_created.append(did)
            if not dry_run:
                db.add(d)
        elif d.origin == REGISTRY:
            fill = {k: v for k, v in (("name", (o.get("developer") or "")[:255]), ("bin", o.get("developerBin") or ""))
                    if v and not getattr(d, k)}
            if fill:
                rep.developers_updated.append(did)
                if not dry_run:
                    for k, v in fill.items():
                        setattr(d, k, v)
        cid = complex_id(o["key"])
        values = complex_values(o, rs)
        c = db.get(models.Complex, cid)
        if c is None:
            rep.created.append(cid)
            if dry_run:
                continue
            db.add(models.Complex(id=cid, developer_id=did, origin=REGISTRY, **values))
            db.flush()
            db.add(models.AuditEvent(
                complex_id=cid, kind="data", who=SYSTEM, at=now,
                what=f"Карточка ЖК создана по открытому реестру: {_sources_line(o, rs)}. Сведения источника, аналитиком "
                     "не проверены — риск не оценивается до проверенных данных"
                     + ("; координат в источнике нет — сверьте адрес в 2ГИС" if values["lat"] is None else "")))
            continue
        if c.origin != REGISTRY:
            rep.skipped.append(f"{label}: id {cid} занят ЖК с происхождением «{c.origin}»")
            continue
        fill = {k: values[k] for k in FILL if values[k] and not getattr(c, k)}
        if c.lat is None and c.lng is None and values["lat"] is not None:  # у источника появились координаты
            fill.update(lat=values["lat"], lng=values["lng"])
        if not c.developer_id or c.developer_id not in devs:
            fill["developer_id"] = did
        basis = dict(c.basis or {})
        refs = {k: v for k, v in values["basis"].items() if k not in basis}
        if not fill and not refs:
            rep.unchanged.append(cid)
            continue
        rep.updated.append(cid)
        if dry_run:
            continue
        for k, v in fill.items():
            setattr(c, k, v)
        if refs:
            c.basis = {**basis, **refs}
        names = {"name": "название", "address": "адрес", "start": "начало строительства", "planned_end": "плановое завершение",
                 "developer_id": "застройщик", "lat": "координаты", "lng": None}
        what = ", ".join([names[k] for k in fill if names[k]] + (["документы основания (справка)"] if refs else []))
        db.add(models.AuditEvent(complex_id=cid, kind="data", who=SYSTEM, at=now,
                                 what=f"Карточка ЖК дополнена по открытому реестру ({_sources_line(o, rs)}): {what}"))
    if not dry_run:
        db.flush()
    return rep


# ---------- удаление демо-данных ----------

@dataclass
class Plan:
    """Что удаляет purge: id строк по таблицам; unlink - собранные/внесённые публикации, привязанные к демо-ЖК."""
    ads: list[str] = field(default_factory=list)
    complexes: dict[str, str] = field(default_factory=dict)  # id → название(для событий о снятой привязке)
    developers: list[str] = field(default_factory=list)
    appeals: list[str] = field(default_factory=list)
    signals: list[int] = field(default_factory=list)
    events: list[int] = field(default_factory=list)
    detach: list[int] = field(default_factory=list)  # события собранных публикаций о демо-ЖК: complex_id → NULL
    unlink: list[tuple[str, str]] = field(default_factory=list)  # (публикация, демо-ЖК)
    llm: list[int] = field(default_factory=list)
    items: list[int] = field(default_factory=list)
    versions: list[int] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    bot: list[int] = field(default_factory=list)
    jobs: list[int] = field(default_factory=list)
    geo: list[int] = field(default_factory=list)
    mentions: list[int] = field(default_factory=list)  # упоминания объектов из рекламы в демо-публикациях
    relink: list[int] = field(default_factory=list)  # объекты из рекламы, привязанные к демо-ЖК: снова "new"
    kept_developers: list[str] = field(default_factory=list)  # демо-застройщик у не демо-ЖК - не удаляется
    blocked: list[str] = field(default_factory=list)  # почему удалить нельзя(ничего не удаляется)

    def rows(self) -> list[tuple[str, int]]:
        return [("публикации демо-набора", len(self.ads)), ("ЖК демо-набора", len(self.complexes)),
                ("застройщики демо-набора", len(self.developers)), ("обращения демо-набора", len(self.appeals)),
                ("сигналы по демо-ЖК", len(self.signals)), ("события журнала (демо-публикации, демо-ЖК, сид)", len(self.events)),
                ("анализы ИИ демо-публикаций", len(self.llm)), ("записи приёма и их версии", len(self.items) + len(self.versions)),
                ("файлы демо-публикаций (строки)", len(self.media)), ("заявки бота по демо-публикациям", len(self.bot)),
                ("задания воркера по демо-публикациям", len(self.jobs)), ("проверки адреса 2ГИС по демо-ЖК", len(self.geo)),
                ("упоминания объектов из рекламы в демо-публикациях", len(self.mentions))]

    @property
    def empty(self) -> bool:
        return not any(n for _, n in self.rows()) and not self.unlink and not self.detach and not self.relink


# Явные события журнала прежнего демо-набора(seed.audit): кто, что, когда(UTC). Сам набор в приложении больше не
# хранится(shared/testdata - только для тестов), поэтому его события для purge перечислены здесь
SEED_AUDIT = (
    ("Нестерова А.", "Статус: «Новая» → «На проверке» · ответственный: Нестерова А.", "2026-10-12T04:30"),
    ("Нестерова А.", "Статус: «На проверке» → «Нарушение подтверждено». Основание: сверено с картотекой ГАСК ВКО — "
                     "гарантии ЕО и разрешения МИО нет", "2026-10-12T06:10"),
    ("Нестерова А.", "Статус: «Новая» → «На проверке» · ответственный: Нестерова А.", "2026-10-12T10:20"),
    ("Омарова Ж.", "Статус: «Новая» → «Отклонено». Основание: у объекта действующая гарантия ЕО, номер гарантии указан в "
                   "профиле", "2026-10-12T09:05"),
    ("Исаев Т.", "Статус: «Новая» → «Нужна проверка». Основание: запросить в ГАСК сведения о разрешении МИО",
     "2026-10-13T11:40"),
    ("Исаев Т.", "Видео набрало 52 тыс. просмотров — разобрать в первую очередь", "2026-10-13T11:42"),
    ("Омарова Ж.", "Статус: «Новая» → «Нужна проверка». Основание: банк по разрешению МИО не подтверждён",
     "2026-10-11T05:05"),
    ("Омарова Ж.", "Запрос сведений: основание привлечения средств (гарантия ЕО / разрешение МИО / банк). Комментарий: в "
                   "реестре МИО отказ, по гарантии ЕО ответа нет", "2026-10-12T05:15"),
)


def _seed_audit() -> set:
    """События журнала прежнего демо-набора: (кто, что, когда UTC)."""
    return {(who, what, datetime.fromisoformat(at).replace(tzinfo=UTC)) for who, what, at in SEED_AUDIT}


def _ids(db: Session, col, *where) -> list[Any]:
    return list(db.scalars(select(col).where(*where)))


def plan(db: Session) -> Plan:
    A, C, D, AP, S, E = models.Ad, models.Complex, models.Developer, models.Appeal, models.Signal, models.AuditEvent
    p = Plan()
    p.ads = _ids(db, A.id, A.origin == "demo")
    p.complexes = dict(db.execute(select(C.id, C.name).where(C.origin == "demo")).all())
    demo_dev = set(_ids(db, D.id, D.origin == "demo"))
    used = set(_ids(db, C.developer_id, C.origin != "demo"))
    p.kept_developers = sorted(demo_dev & used)
    p.developers = sorted(demo_dev - used)
    p.appeals = _ids(db, AP.id, AP.origin == "demo")
    cx, ads = list(p.complexes), set(p.ads)
    foreign = _ids(db, AP.id, AP.origin != "demo", AP.complex_id.in_(cx)) if cx else []
    if foreign:
        p.blocked.append(f"обращения не из демо-набора по демо-ЖК: {', '.join(foreign)} — перенесите или удалите их вручную")
    p.signals = _ids(db, S.id, S.complex_id.in_(cx)) if cx else []
    p.unlink = [(a, c) for a, c in db.execute(select(A.id, A.complex_id).where(A.origin != "demo", A.complex_id.in_(cx)))] \
        if cx else []
    seed_audit = _seed_audit()
    sigs = set(p.signals)
    conds = [E.ad_id.in_(p.ads)] if p.ads else []
    if cx:
        conds.append(E.complex_id.in_(cx))
    if sigs:
        conds.append(E.signal_id.in_(sigs))
    if seed_audit:
        conds.append(E.what.in_({w for _, w, _ in seed_audit}))
    for e in db.scalars(select(E).where(or_(*conds))) if conds else []:
        foreign_ad = e.ad_id is not None and e.ad_id not in ads
        if e.signal_id in sigs or e.ad_id in ads:
            p.events.append(e.id)
        elif e.complex_id in p.complexes:
            # событие собранной/внесённой публикации о демо-ЖК - остаётся в её истории, без ссылки на удалённый ЖК
            (p.detach if foreign_ad else p.events).append(e.id)
        elif not foreign_ad and (e.who, e.what, models.as_utc(e.at)) in seed_audit:
            p.events.append(e.id)
    if p.ads:
        p.llm = _ids(db, models.LlmAnalysis.id, models.LlmAnalysis.ad_id.in_(p.ads))
        p.items = _ids(db, models.IngestItem.id, models.IngestItem.ad_id.in_(p.ads))
        p.jobs = _ids(db, models.Job.id, models.Job.ad_id.in_(p.ads))
        M = models.MediaAsset
        p.media = _ids(db, M.id, or_(M.ad_id.in_(p.ads), M.item_id.in_(p.items)) if p.items else M.ad_id.in_(p.ads))
        B = models.BotSubmission
        p.bot = _ids(db, B.id, or_(B.ad_id.in_(p.ads), B.item_id.in_(p.items)) if p.items else B.ad_id.in_(p.ads))
    if p.items:
        p.versions = _ids(db, models.IngestVersion.id, models.IngestVersion.item_id.in_(p.items))
    if cx:
        G = models.GeoCheck
        p.geo = _ids(db, G.id, G.target_kind == "complex", G.target_id.in_(cx))
        O = models.DiscoveredObject
        p.relink = _ids(db, O.id, O.complex_id.in_(cx))
    if p.ads:
        M = models.DiscoveredMention
        p.mentions = _ids(db, M.id, M.ad_id.in_(p.ads))
    return p


def _delete(db: Session, model, col, ids: Iterable[Any]) -> None:
    ids = list(ids)
    for i in range(0, len(ids), 500):
        db.execute(model.__table__.delete().where(col.in_(ids[i:i + 500])))


def apply(db: Session, p: Plan, *, at: datetime | None = None) -> ImportReport:
    """Удаление по плану, флаг demo_data = purged, карточки ЖК из реестра и событие журнала - в сессии db(без commit)."""
    if p.blocked:
        raise RuntimeError("; ".join(p.blocked))
    now = at or models.utcnow()
    A, E = models.Ad, models.AuditEvent
    for aid, cid in p.unlink:
        db.execute(update(A).where(A.id == aid, A.complex_id == cid)
                   .values(complex_id=None, match_confidence=0, link_confirmed_by=None, link_confirmed_at=None,
                           decision_rev=A.decision_rev + 1).execution_options(synchronize_session=False))
        db.add(E(ad_id=aid, complex_id=None, kind="link", who=SYSTEM, at=now,
                 what=UNLINKED.format(name=p.complexes.get(cid, cid))))
    if p.detach:
        db.execute(update(E).where(E.id.in_(p.detach)).values(complex_id=None).execution_options(synchronize_session=False))
    _delete(db, E, E.id, p.events)
    _delete(db, models.Signal, models.Signal.id, p.signals)
    _delete(db, models.Appeal, models.Appeal.id, p.appeals)
    _delete(db, models.LlmAnalysis, models.LlmAnalysis.id, p.llm)
    _delete(db, models.Job, models.Job.id, p.jobs)
    _delete(db, models.BotSubmission, models.BotSubmission.id, p.bot)
    M = models.MediaAsset
    if p.media:  # кадры видео ссылаются на видео(parent_id) - сначала они
        db.execute(update(M).where(M.parent_id.in_(p.media)).values(parent_id=None).execution_options(synchronize_session=False))
    _delete(db, M, M.id, p.media)
    _delete(db, models.IngestVersion, models.IngestVersion.id, p.versions)
    _delete(db, models.IngestItem, models.IngestItem.id, p.items)
    _delete(db, models.GeoCheck, models.GeoCheck.id, p.geo)
    O, M = models.DiscoveredObject, models.DiscoveredMention
    touched = set(db.scalars(select(M.object_id).where(M.id.in_(p.mentions)))) if p.mentions else set()
    _delete(db, M, M.id, p.mentions)
    if p.relink:
        db.execute(update(O).where(O.id.in_(p.relink))
                   .values(status="new", complex_id=None, note="Демо-ЖК удалён — привязка снята", decided_by=SYSTEM,
                           decided_at=now).execution_options(synchronize_session=False))
    if touched:
        from . import discovery

        discovery.recount(db, touched)
    _delete(db, A, A.id, p.ads)
    _delete(db, models.ScreeningDecision, models.ScreeningDecision.complex_id, list(p.complexes))  # решения по демо-ЖК
    _delete(db, models.Complex, models.Complex.id, list(p.complexes))
    _delete(db, models.Developer, models.Developer.id, p.developers)
    db.merge(models.Meta(key=FLAG, value=PURGED))
    db.flush()
    db.expire_all()
    rep = import_registry(db, at=now)
    if not p.empty:
        what = "Удалены демо-данные: " + ", ".join(f"{name} — {n}" for name, n in p.rows() if n)
        if p.unlink:
            what += f"; привязка к демо-ЖК снята у собранных и внесённых публикаций — {len(p.unlink)}"
        what += ". Остались аналитики, открытый реестр, собранные и внесённые публикации. " + rep.line()
        db.add(E(kind="data", who=SYSTEM, what=what, at=now))
    db.flush()
    return rep


# ---------- вывод и CLI ----------

def _version(db: Session) -> str | None:
    try:
        m = db.get(models.Meta, "schema_version")
    except SQLAlchemyError:  # нет таблицы meta - БД пустая или чужая
        db.rollback()
        return None
    return m.value if m else None


def kept(db: Session) -> list[tuple[str, int]]:
    def n(model, *where) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    A = models.Ad
    return [("аналитики", n(models.Analyst)), ("записи открытого реестра", n(models.SourceRecord)),
            ("карточки ЖК из реестра", n(models.Complex, models.Complex.origin == REGISTRY)),
            ("карточки ЖК по рекламе", n(models.Complex, models.Complex.origin == "advertised")),
            ("объекты из рекламы", n(models.DiscoveredObject)),
            ("застройщики из реестра", n(models.Developer, models.Developer.origin == REGISTRY)),
            ("собранные публикации", n(A, A.origin == "collected")), ("внесённые аналитиком публикации", n(A, A.origin == "analyst")),
            ("подключения сбора", n(models.Collector)), ("запуски сбора", n(models.CollectionRun)),
            ("файлы публикаций (строки)", n(models.MediaAsset)), ("анализы ИИ", n(models.LlmAnalysis)),
            ("счётчики квот", n(models.QuotaCounter))]


def _print_plan(db: Session, p: Plan, eng: Engine) -> None:
    print(f"БД: {eng.dialect.name}, схема {_version(db)}; флаг demo_data: {'purged' if purged(db) else 'нет'}")
    print("Будет удалено:")
    for name, n in p.rows():
        print(f"  {name}: {n}")
    print(f"Привязка к демо-ЖК будет снята (публикации остаются): {len(p.unlink)}")
    if p.detach:
        print(f"События собранных публикаций о демо-ЖК (остаются, без ссылки на ЖК): {len(p.detach)}")
    if p.kept_developers:
        print(f"Демо-застройщики остаются — на них ссылаются ЖК не из демо-набора: {', '.join(p.kept_developers)}")
    print("Останется:")
    for name, n in kept(db):
        print(f"  {name}: {n}")
    preview = import_registry(db, dry_run=True)
    print(preview.line().replace("создано", "будет создано", 1))
    for reason in p.blocked:
        print(f"Нельзя удалить: {reason}")
    if eng.dialect.name == "postgresql" and not (p.empty and purged(db)):
        print(f"Резервная копия PostgreSQL — до удаления, из каталога с docker-compose.yml:\n  {PG_DUMP}")


def purge(eng: Engine, *, dry_run: bool = False, yes: bool = False) -> int:
    """CLI purge: 0 - выполнено(или --dry-run), 1 - нужен --yes, 2 - ошибка(схема не текущая, блокировка)."""
    from .db import SessionLocal

    with SessionLocal(bind=eng) as db:
        ver = _version(db)
        if ver != models.SCHEMA_VERSION:
            print(f"Схема БД — {ver or 'нет'}, нужна {models.SCHEMA_VERSION}: сначала запустите API (он обновит схему), "
                  "затем повторите команду", file=sys.stderr)
            return 2
        if eng.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": migrate.ADVISORY_LOCK})
        p = plan(db)
        _print_plan(db, p, eng)
        if p.blocked:
            return 2
        if dry_run:
            print("Пробный прогон (--dry-run): ничего не изменено")
            return 0
        nothing = p.empty and purged(db)
        if nothing:
            rep = import_registry(db)
            db.commit()
            print("Демо-данных нет — удалять нечего, флаг demo_data уже стоит. " + rep.line())
            return 0
        if not yes:
            print("Ничего не удалено: проверьте список и повторите с --yes")
            return 1
        db.rollback()  # отпустить чтение(и блокировку PostgreSQL) - копия SQLite делается без открытой транзакции
    migrate.backup_sqlite(eng, "demo-purge", reason="перед удалением демо-данных")  # путь - в журнал(stderr)
    with SessionLocal(bind=eng) as db:
        if eng.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": migrate.ADVISORY_LOCK})
        p = plan(db)  # заново: между пробным списком и удалением данные могли измениться
        if p.blocked:
            print("Нельзя удалить: " + "; ".join(p.blocked), file=sys.stderr)
            return 2
        rep = apply(db, p)
        db.commit()
    print("Удалено: " + ", ".join(f"{name} — {n}" for name, n in p.rows() if n) if not p.empty else "Демо-данных не было")
    if p.unlink:
        print(f"Привязка к демо-ЖК снята у публикаций: {len(p.unlink)} (событие «Привязка снята» в их журнале)")
    print("Флаг demo_data = purged: демо-набор больше не засевается. " + rep.line())
    return 0


def import_cli(eng: Engine) -> int:
    from .db import SessionLocal

    with SessionLocal(bind=eng) as db:
        ver = _version(db)
        if ver != models.SCHEMA_VERSION:
            print(f"Схема БД — {ver or 'нет'}, нужна {models.SCHEMA_VERSION}: сначала запустите API", file=sys.stderr)
            return 2
        rep = import_registry(db)
        db.commit()
    print(rep.line())
    return 0


def add_advertised_cli(eng: Engine, path: str, *, author: str | None, note: str | None) -> int:
    """CLI add-advertised: 0 - выполнено, 2 - схема не текущая, файл не читается или не в формате."""
    from pydantic import ValidationError

    from . import discovery
    from .db import SessionLocal

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Файл не прочитан: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    items = raw.get("items") if isinstance(raw, dict) else raw
    if not note and isinstance(raw, dict) and isinstance(raw.get("note"), str):
        note = raw["note"]
    if not isinstance(items, list) or not items:
        print("Нужен список ЖК: [{name, aliases, address, lat, lng, developerName, accounts, sourceUrls, note}] или "
              "{\"items\": [...]}", file=sys.stderr)
        return 2
    from . import schemas

    try:
        parsed = [schemas.AdvertisedItemIn.model_validate(x) for x in items]
    except ValidationError as e:
        print(f"Строки файла не в формате: {e}", file=sys.stderr)
        return 2
    with SessionLocal(bind=eng) as db:
        ver = _version(db)
        if ver != models.SCHEMA_VERSION:
            print(f"Схема БД — {ver or 'нет'}, нужна {models.SCHEMA_VERSION}: сначала запустите API", file=sys.stderr)
            return 2
        rep = discovery.import_advertised(db, parsed, author=(author or SYSTEM)[:128], note=note)
        db.commit()
    print(rep.line())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.demo", description="Демо-данные и карточки ЖК из открытого реестра")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("purge", help="удалить демо-данные (кроме аналитиков и открытого реестра)")
    pp.add_argument("--dry-run", action="store_true", help="только показать, что будет удалено")
    pp.add_argument("--yes", action="store_true", help="подтвердить удаление")
    sub.add_parser("import-registry", help="создать или дополнить карточки ЖК по открытому реестру")
    pa = sub.add_parser("add-advertised", help="карточки ЖК по рекламе (нет в открытом реестре) из JSON-файла")
    pa.add_argument("--file", required=True, help="JSON: список ЖК или {\"note\", \"items\"}")
    pa.add_argument("--author", help="кто импортирует (в журнал); по умолчанию «Система»")
    pa.add_argument("--note", help="что за импорт (в журнал)")
    a = ap.parse_args(argv)
    from .db import engine

    if a.cmd == "purge":
        return purge(engine, dry_run=a.dry_run, yes=a.yes)
    if a.cmd == "add-advertised":
        return add_advertised_cli(engine, a.file, author=a.author, note=a.note)
    return import_cli(engine)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
