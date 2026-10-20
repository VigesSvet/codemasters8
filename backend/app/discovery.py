"""Объекты из рекламы: ЖК, которые реклама называет, а в справочнике ЖК и открытом реестре их нет.

Откуда: после каждого проверенного ответа Claude(app.llm, mentionedComplexes) упомянутые ЖК сравниваются по названию с
ЖК справочника(любого происхождения), с объектами открытого реестра(все названия записей, части в скобках) и с уже
известными объектами из рекламы. Совпало с ЖК или реестром - ничего не создаётся(привязку предлагают правила и Claude);
совпало с объектом из рекламы - к нему добавляется упоминание; иначе - новый объект(status new). Упоминание - одна
строка на объект и публикацию(повторный анализ её обновляет, исчезнувшие упоминания удаляются), счётчики
пересчитываются.
Заявки жителей(source bot) тоже дают упоминания, но их аккаунт в "аккаунты рекламы" не попадает.

Сравнение названий(name_keys): без "ЖК", "МЖК", "жилой комплекс", "ЖК бизнес-класса", кавычек, эмодзи и знаков, без
регистра, ё = е, латиница ≈ кириллица(скелет ai.nlp.skeleton: "Satti City" = "Сатти Сити", "AB-Residence 2" = "АВ
Резиденс 2"); совпадение - тот же ключ, та же основа без номеров и слов очереди("AB-Residence" ≈ "AB-Residence 2",
"Жаным, блок А" ≈ "Жаным") или сходство триграмм ≥ 0.8 у длинных ключей.

Решения аналитика(автор - в журнал, kind data): создать карточку ЖК(origin "advertised", застройщик - найденный по
названию или БИН, иначе новый "advertised", без названия - "не установлен"), привязать к существующему ЖК(название -
его другое написание: упоминания становятся рекламой этого ЖК в "Проверке по рекламе") или отклонить с причиной. После
создания карточки правила сопоставления пересчитываются по публикациям с упоминаниями - без нового запроса к Claude
(задание анализа rulesOnly). Сверка 2ГИС - кандидаты по "Усть-Каменогорск, ЖК <название>" и по адресу из рекламы
(квота, кэш и права - как у сверки адреса ЖК), ничего не принимается автоматически. Импорт ЖК по рекламе списком -
администратору(POST /api/discovered/import, python -m app.demo add-advertised).
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth, importer, models, schemas, screening
from .ai.nlp import STOP, jaccard, skeleton, words
from .db import SessionLocal, get_db

router = APIRouter(tags=["discovery"])

ADVERTISED = "advertised"
UNKNOWN_DEV = screening.PLACEHOLDER_DEV
UNKNOWN_DEV_NAME = "Застройщик не установлен (ЖК по рекламе)"
ID_MAX = 32
ALIASES_MAX = 20
ACCOUNTS_MAX = 20
URLS_MAX = 50
QUOTES_MAX = 3
FUZZY = 0.8
FUZZY_MIN = 6  # символов ключа без пробелов, с которых допустимо нечёткое совпадение
STAGES = ("under_construction", "completed", "unknown")
GEO_MAX = 4  # сверок 2ГИС в карточке объекта
NOT_FOUND = "Объект из рекламы не найден"
DECIDED = "Решение по объекту уже принял другой аналитик — обновите страницу"
NAME_EMPTY = "В названии нет букв и цифр"
RULES_REASON = "карточка ЖК создана по рекламе"

# ---------- названия ----------

_PREFIX = re.compile(r"^\s*(?:мжк|жк|ж\s*/\s*к|ж\.\s*к\.?|жилой\s+комплекс|жилого\s+комплекса|жилом\s+комплексе|"
                     r"жилой\s+квартал|zhk|jk)(?![^\W\d_])[\s.:,\-–—]*", re.I)
_CLASS = re.compile(r"^\s*(?:бизнес|комфорт|премиум|элит|эконом|стандарт)(?:\s*[-+]?\s*плюс)?\s*[-\s]\s*класса"
                    r"(?![^\W\d_])[\s.:,\-–—]*", re.I)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍⃣]")
_EDGE = " \t«»\"'“”„‟‘’‚`´.,:;!?*#•·-–—_/\\|"
_PAREN = re.compile(r"\(([^()]*)\)")
# слова очереди и части объекта: "AB-Residence 2" и "AB-Residence", "Жаным, блок А" и "Жаным" - одна основа
_QUEUE = frozenset({"очередь", "очереди", "очередей", "оч", "блок", "блоки", "блока", "пятно", "пятна", "здание",
                    "корпус", "корпуса", "этап", "фаза", "секция", "литер", "queue", "phase", "block", "stage"})
_ROMAN = re.compile(r"^(?:i{1,3}|iv|v|vi{0,3}|ix|x)$")


def clean_name(raw: Any) -> str:
    """Название для показа: без "ЖК"/"жилой комплекс"/"ЖК бизнес-класса", кавычек и эмодзи по краям, пробелы
    схлопнуты. "ЖК "Nova Park" 🔥" → "Nova Park"."""
    s = " ".join(_EMOJI.sub(" ", str(raw or "")).split()).strip(_EDGE)
    prev = None
    while prev != s:
        prev = s
        s = _CLASS.sub("", _PREFIX.sub("", s)).strip(_EDGE)
    return s[:255]


@dataclass(frozen=True)
class Keys:
    key: str  # скелет всех слов названия
    base: str  # без номеров, букв-литер и слов очереди

    @property
    def flat(self) -> str:
        return self.key.replace(" ", "")


def _keys(text: str) -> Keys:
    toks = [w for w in words(clean_name(text)) if w not in STOP]
    key = " ".join(skeleton(w) for w in toks)
    base = " ".join(skeleton(w) for w in toks
                    if not (w.isdigit() or w in _QUEUE or len(w) == 1 or _ROMAN.match(w)))
    return Keys(key, base)


def name_keys(raw: Any) -> Keys:
    """Ключ сравнения названия: основная часть(до запятой, без частей в скобках) - см. модуль."""
    main = _PAREN.sub(" ", clean_name(raw)).split(",")[0]
    return _keys(main)


def variants(raw: Any) -> list[Keys]:
    """Ключи всех написаний названия: основная часть и содержимое скобок("Престиж(Sparkle Diamond)" - "Престиж" и
    "Sparkle Diamond"; "(ГС: Инвестор)", "(блок А)" - не названия)."""
    name = clean_name(raw)
    out = [name_keys(name)]
    for part in _PAREN.findall(name):
        if ":" in part:
            continue
        k = _keys(part)
        if k.base and k not in out:
            out.append(k)
    return [k for k in out if k.key]


def same(a: Keys, b: Keys) -> bool:
    if a.key and a.key == b.key:
        return True
    if a.base and a.base == b.base and len(a.base.replace(" ", "")) >= 3:
        return True
    fa, fb = a.flat, b.flat
    return min(len(fa), len(fb)) >= FUZZY_MIN and jaccard(fa, fb) >= FUZZY


def norm_key(raw: Any) -> str:
    return name_keys(raw).key[:255]


# ---------- сопоставление с известными ЖК ----------

@dataclass
class Entry:
    keys: Keys
    kind: str  # complex | registry | discovered
    ref: Any  # id ЖК, ключ объекта реестра или id объекта из рекламы
    name: str
    complex_id: str | None = None
    origin: str | None = None
    status: str | None = None


@dataclass
class Matcher:
    """Справочник названий: ЖК(любого происхождения), объекты открытого реестра(все названия записей), объекты из
    рекламы(ключ, название и другие написания)."""
    known: list[Entry] = field(default_factory=list)
    found: list[Entry] = field(default_factory=list)
    by_key: dict[str, int] = field(default_factory=dict)  # norm_key → id объекта из рекламы

    @classmethod
    def load(cls, db: Session) -> "Matcher":
        from . import demo

        m = cls()
        for c in db.scalars(select(models.Complex).order_by(models.Complex.id)):
            m.add_complex(c)
        cards = {e.complex_id for e in m.known}
        R = models.SourceRecord
        names: dict[str, set[str]] = {}
        for key, name in db.execute(select(R.key, R.name)):  # только названия - без разбора записей в схему API
            names.setdefault(key, set()).add(name)
        for key, ns in sorted(names.items()):
            cid = demo.complex_id(key)
            for n in sorted(ns):
                for k in variants(demo.display_name(n)):
                    m.known.append(Entry(k, "registry", key, demo.display_name(n),
                                         complex_id=cid if cid in cards else None, origin="registry"))
        for o in db.scalars(select(models.DiscoveredObject).order_by(models.DiscoveredObject.id)):
            m.add_object(o)
        return m

    def add_complex(self, c: models.Complex) -> None:
        for k in variants(c.name):
            self.known.append(Entry(k, "complex", c.id, c.name, complex_id=c.id, origin=c.origin))

    def add_object(self, o: models.DiscoveredObject) -> None:
        self.by_key[o.norm_key] = o.id
        for n in [o.name, *(o.aliases or [])]:
            for k in variants(n):
                self.found.append(Entry(k, "discovered", o.id, o.name, complex_id=o.complex_id, status=o.status))
        if o.norm_key:
            self.found.append(Entry(Keys(o.norm_key, o.norm_key), "discovered", o.id, o.name,
                                    complex_id=o.complex_id, status=o.status))

    def known_match(self, k: Keys) -> Entry | None:
        exact = next((e for e in self.known if e.keys.key == k.key), None)
        return exact or next((e for e in self.known if same(e.keys, k)), None)

    def object_match(self, k: Keys) -> Entry | None:
        oid = self.by_key.get(k.key)
        if oid is not None:
            return next(e for e in self.found if e.ref == oid)
        return next((e for e in self.found if same(e.keys, k)), None)


# ---------- упоминания ----------

def _merge(old: Iterable[str], new: Iterable[str], limit: int) -> list[str]:
    out: list[str] = []
    for raw in [*old, *new]:
        x = " ".join(str(raw or "").split())
        if x and x not in out:
            out.append(x)
    return out[:limit]


def _new_object(db: Session, name: str, key: str, address: str | None, now: datetime,
                origin: str = "ads") -> models.DiscoveredObject:
    O = models.DiscoveredObject
    models.insert_ignore(db, O, {"norm_key": key, "name": name[:255], "aliases": [], "address_hint": address or None,
                                 "status": "new", "origin": origin, "ads_count": 0, "accounts": [], "source_urls": [],
                                 "first_seen": now, "last_seen": now, "created_at": now}, ("norm_key",))
    return db.scalar(select(O).where(O.norm_key == key))


def _add_alias(o: models.DiscoveredObject, name: str | None) -> None:
    if not name:
        return
    if name.casefold() != (o.name or "").casefold() and all(name.casefold() != a.casefold() for a in o.aliases or []):
        o.aliases = _merge(o.aliases or [], [name], ALIASES_MAX)


def recount(db: Session, ids: Iterable[int]) -> None:
    """Счётчики объектов по упоминаниям: публикаций, аккаунты рекламы(по частоте; заявки жителей - нет), первое и
    последнее упоминание, адрес из рекламы(если не задан)."""
    M, O, A = models.DiscoveredMention, models.DiscoveredObject, models.Ad
    for oid in sorted(set(ids)):
        o = db.get(O, oid)
        if o is None:
            continue
        rows = db.execute(select(M, A.account, A.source).outerjoin(A, A.id == M.ad_id)
                          .where(M.object_id == oid).order_by(M.created_at, M.id)).all()
        o.ads_count = len(rows)
        freq: dict[str, int] = {}
        for m, account, source in rows:
            if account and (source or m.source) != "bot":
                freq[account] = freq.get(account, 0) + 1
        ranked = sorted(freq, key=lambda a: (-freq[a], a))
        o.accounts = _merge(ranked, o.accounts or [], ACCOUNTS_MAX)
        if rows:
            times = [models.as_utc(m.created_at) for m, _, _ in rows if m.created_at]
            if times:
                first = models.as_utc(o.first_seen)
                o.first_seen = min([t for t in (first, *times) if t is not None])
                o.last_seen = max(times)
            if not o.address_hint:
                o.address_hint = next((m.address for m, _, _ in rows if m.address), None)


def sync_mentions(db: Session, ad: models.Ad, mentions: list[dict], stage: dict | None, now: datetime,
                  matcher: Matcher | None = None) -> list[int]:
    """Упоминания публикации по проверенному ответу Claude(в сессии db, без commit): объекты из рекламы создаются,
    упоминания добавляются, обновляются или удаляются; счётчики пересчитываются. Возвращает id объектов публикации."""
    M = models.DiscoveredMention
    m = matcher or Matcher.load(db)
    now = models.as_utc(now)
    wanted: dict[int, dict] = {}
    for x in mentions:
        if not isinstance(x, dict):
            continue
        name = clean_name(x.get("name"))
        k = name_keys(name)
        if len(k.flat) < 2:
            continue
        hit = m.object_match(k)
        if hit is None:
            if m.known_match(k) is not None:
                continue  # ЖК справочника или реестра - привязку предлагают правила и Claude
            o = _new_object(db, name, k.key[:255], x.get("address") or None, now)
            m.add_object(o)
            oid = o.id
        else:
            oid = hit.ref
        wanted.setdefault(oid, {**x, "name": name})
    st = stage or {}
    st_value = st.get("value") if st.get("value") in STAGES else None
    st_quote = (st.get("quote") or None) if st_value in ("under_construction", "completed") else None
    have = {r.object_id: r for r in db.scalars(select(M).where(M.ad_id == ad.id))}
    for oid, r in have.items():
        if oid not in wanted:
            db.delete(r)
    for oid, x in wanted.items():
        vals = {"name": x["name"][:255], "quote": x.get("quote") or "", "address": x.get("address") or None,
                "source": (ad.source or "parser")[:16], "stage": st_value, "stage_quote": st_quote, "updated_at": now}
        r = have.get(oid)
        if r is None:
            models.insert_ignore(db, M, {"object_id": oid, "ad_id": ad.id, "created_at": now, **vals},
                                 ("object_id", "ad_id"))
        else:
            for k_, v in vals.items():
                setattr(r, k_, v)
    db.flush()
    for oid, x in wanted.items():
        o = db.get(models.DiscoveredObject, oid)
        if o is not None:
            _add_alias(o, x["name"])
    recount(db, set(have) | set(wanted))
    return sorted(wanted)


def record_result(ad_id: str, result: dict | None, now: datetime) -> list[int]:
    """После ответа Claude(status done): упоминания публикации - своя сессия и транзакция."""
    res = result or {}
    with SessionLocal() as db:
        ad = db.get(models.Ad, ad_id)
        if ad is None:
            return []
        ids = sync_mentions(db, ad, list(res.get("mentionedComplexes") or []), res.get("objectStage"), now)
        db.commit()
        return ids


# ---------- карточка ЖК по рекламе ----------

def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def new_complex_id(db: Session, key: str) -> str:
    """"a-<латинский ключ>"; длинный или пустой - "ah-<sha1[:16]>"; занятый - с добавкой."""
    slug = _slug(key)
    cid = f"a-{slug}"
    if slug and len(cid) <= ID_MAX and db.get(models.Complex, cid) is None:
        return cid
    n = 0
    while True:
        cid = "ah-" + hashlib.sha1(f"{key}#{n}".encode(), usedforsecurity=False).hexdigest()[:16]
        if db.get(models.Complex, cid) is None:
            return cid
        n += 1


def ensure_developer(db: Session, name: str | None, bin_: str | None) -> tuple[models.Developer, bool]:
    """Застройщик ЖК по рекламе: существующий по БИН, иначе по названию(без формы собственности и кавычек) - но не с
    другим БИН, иначе новый(origin "advertised"); без названия(или только "ТОО") и БИН - "Застройщик не установлен".
    Возвращает(застройщик, создан ли)."""
    D = models.Developer
    org = importer.org_name(name) if name else ""
    if not org and not bin_:
        did, dname = UNKNOWN_DEV, UNKNOWN_DEV_NAME
    else:
        devs = list(db.scalars(select(D).order_by(D.id)))
        hit = next((d for d in devs if bin_ and d.bin == bin_), None) or next(
            (d for d in devs if org and importer.org_name(d.name) == org and not (bin_ and d.bin and d.bin != bin_)),
            None)
        if hit is not None:
            return hit, False
        did = f"ad-{bin_}" if bin_ else "ad-" + hashlib.sha1(org.encode(), usedforsecurity=False).hexdigest()[:12]
        dname = (name or f"БИН {bin_}")[:255]
    d = db.get(D, did)  # заглушка или id, занятый застройщиком, чьи название или БИН с тех пор изменили
    if d is not None:
        return d, False
    d = D(id=did, name=dname, bin=bin_ or "", director="", registered="", objects=0, unfinished=0, site=None,
          instagram=None, phones=[], origin=ADVERTISED)
    db.add(d)
    db.flush()
    return d, True


def _complex_values(name: str, address: str, lat: float | None, lng: float | None) -> dict:
    return {"name": name[:255], "address": (address or "")[:255], "district": "", "lat": lat, "lng": lng, "stage": "",
            "progress": 0, "floors": 0, "flats": 0, "start": "", "planned_end": "",
            "basis": {"guarantee": "unknown", "mio": "unknown", "bvu": "unknown"},
            "readiness": {"declared": None, "confirmed": None}, "in_published_list": "unknown", "cover": "",
            "photos": [], "idle_days": 0, "origin": ADVERTISED}


def rerun_rules(db: Session, ad_ids: Iterable[str], reason: str = RULES_REASON) -> list[str]:
    """Пересчёт правил сопоставления по собранным публикациям - задание анализа без запроса к Claude(rulesOnly)."""
    from . import ingest

    ids = sorted(set(ad_ids))
    if not ids:
        return []
    out = []
    for item in db.scalars(select(models.IngestItem).where(models.IngestItem.ad_id.in_(ids))):
        ingest.enqueue_analysis(db, item.ad_id, item, payload={"rulesOnly": True, "reason": reason})
        out.append(item.ad_id)
    return out


def _mention_ads(db: Session, oid: int) -> list[str]:
    M = models.DiscoveredMention
    return list(db.scalars(select(M.ad_id).where(M.object_id == oid)))


def _event(db: Session, who: str, what: str, *, complex_id: str | None = None, kind: str = "data",
           at: datetime | None = None) -> models.AuditEvent:
    e = models.AuditEvent(kind=kind, who=who, what=what, at=models.as_utc(at) if at else models.utcnow(),
                          complex_id=complex_id)
    db.add(e)
    return e


def _list(xs: list[str], n: int = 5) -> str:
    return ", ".join(xs[:n]) + (f" и ещё {len(xs) - n}" if len(xs) > n else "")


# ---------- импорт ЖК по рекламе ----------

@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    developers_created: list[str] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)  # публикации с пересчётом правил

    def line(self) -> str:
        out = (f"ЖК по рекламе: создано {len(self.created)}, дополнено {len(self.updated)}, без изменений "
               f"{len(self.unchanged)}; застройщиков создано {len(self.developers_created)}")
        if self.skipped:
            out += f"; пропущено {len(self.skipped)} ({'; '.join(self.skipped)})"
        return out

    def out(self) -> dict:
        return {"created": self.created, "updated": self.updated, "unchanged": self.unchanged, "skipped": self.skipped,
                "developers_created": self.developers_created, "event_ids": self.event_ids}


def _item(raw: Any) -> schemas.AdvertisedItemIn:
    return raw if isinstance(raw, schemas.AdvertisedItemIn) else schemas.AdvertisedItemIn.model_validate(raw)


def _urls(xs: Iterable[str]) -> list[str]:
    from .ads_service import safe_url

    return [u for u in (safe_url(x) for x in xs) if u]


def import_advertised(db: Session, items: Iterable[Any], *, author: str, note: str | None = None,
                      now: datetime | None = None) -> ImportReport:
    """Карточки ЖК по рекламе списком(в сессии db, без commit). Идемпотентно по ключу названия: ЖК "advertised" с тем
    же ключом - только дополняются пустые поля(адрес, координаты), другие названия, аккаунты и ссылки; ЖК справочника
    или объект открытого реестра с тем же названием - пропуск с причиной. Объект из рекламы с тем же ключом
    привязывается к карточке(его упоминания - реклама этого ЖК, правила пересчитываются). События журнала - по
    каждой карточке."""
    rep = ImportReport()
    now = models.as_utc(now) or models.utcnow()
    m = Matcher.load(db)
    head = "импорт" + (f": {note}" if note else "")
    for raw in items:
        it = _item(raw)
        name = clean_name(it.name)
        k = name_keys(name)
        if len(k.flat) < 2:
            rep.skipped.append(f"{it.name}: {NAME_EMPTY.lower()}")
            continue
        alias_names = [a for a in (clean_name(x) for x in it.aliases) if a and a.casefold() != name.casefold()]
        keys = [k, *(ak for ak in map(name_keys, alias_names) if ak.key)]
        known = next((e for e in (m.known_match(x) for x in keys) if e is not None), None)
        found = next((e for e in (m.object_match(x) for x in keys) if e is not None), None)
        c: models.Complex | None = None
        if known is not None and known.kind == "complex" and known.origin == ADVERTISED:
            c = db.get(models.Complex, known.ref)
        elif known is not None:
            where = "в открытом реестре" if "registry" in (known.kind, known.origin) else "в справочнике ЖК"
            rep.skipped.append(f"{it.name}: есть {where} — «{known.name}»"
                               + (f" ({known.complex_id})" if known.complex_id else f" (объект {known.ref})"))
            continue
        elif found is not None and found.status == "linked" and found.complex_id:
            c = db.get(models.Complex, found.complex_id)
        obj = db.get(models.DiscoveredObject, found.ref) if found is not None else None
        urls = _urls(it.source_urls)
        if c is not None:
            changed = []
            if not c.address and it.address:
                c.address = it.address[:255]
                changed.append("адрес")
            if c.lat is None and it.lat is not None:
                c.lat, c.lng = it.lat, it.lng
                changed.append("координаты")
            if obj is None or obj.complex_id != c.id:
                # объект с тем же названием не привязан или привязан к другому ЖК - дополняется объект этой карточки
                O = models.DiscoveredObject
                own = db.scalar(select(O).where(O.complex_id == c.id, O.status == "linked").order_by(O.id).limit(1))
                obj = own or obj
            if obj is None:
                obj = _new_object(db, name, k.key[:255], it.address or None, now, origin="import")
                obj.status, obj.complex_id, obj.decided_by, obj.decided_at = "linked", c.id, author, now
                m.add_object(obj)
                changed.append("объект из рекламы")
            before = (list(obj.aliases or []), list(obj.accounts or []), list(obj.source_urls or []))
            obj.aliases = _merge(obj.aliases or [], [x for x in [name, *alias_names]
                                                     if x.casefold() != (obj.name or "").casefold()], ALIASES_MAX)
            obj.accounts = _merge(obj.accounts or [], it.accounts, ACCOUNTS_MAX)
            obj.source_urls = _merge(obj.source_urls or [], urls, URLS_MAX)
            if (list(obj.aliases), list(obj.accounts), list(obj.source_urls)) != before:
                changed.append("названия, аккаунты и ссылки рекламы")
            if changed:
                rep.updated.append(c.id)
                e = _event(db, author, f"Карточка ЖК по рекламе дополнена ({head}): «{c.name}» — {', '.join(changed)}",
                           complex_id=c.id, at=now)
                db.flush()
                rep.event_ids.append(e.id)
            else:
                rep.unchanged.append(c.id)
            continue
        d, created = ensure_developer(db, it.developer_name, it.developer_bin)
        if created:
            rep.developers_created.append(d.id)
        cid = new_complex_id(db, k.key)
        c = models.Complex(id=cid, developer_id=d.id, **_complex_values(name, it.address, it.lat, it.lng))
        db.add(c)
        db.flush()
        m.add_complex(c)
        if obj is None:
            obj = _new_object(db, name, k.key[:255], it.address or None, now, origin="import")
            m.add_object(obj)
        ads = _mention_ads(db, obj.id)
        obj.status, obj.complex_id, obj.decided_by, obj.decided_at = "linked", c.id, author, now
        obj.aliases = _merge(obj.aliases or [], [x for x in [name, *alias_names]
                                                 if x.casefold() != (obj.name or "").casefold()], ALIASES_MAX)
        obj.accounts = _merge(obj.accounts or [], it.accounts, ACCOUNTS_MAX)
        obj.source_urls = _merge(obj.source_urls or [], urls, URLS_MAX)
        obj.note = it.note or note or obj.note
        if not obj.address_hint and it.address:
            obj.address_hint = it.address
        parts = [f"Карточка ЖК создана по рекламе ({head}): «{c.name}» — нет в открытом реестре"]
        if alias_names:
            parts.append(f"другие названия: {_list(alias_names)}")
        if it.accounts:
            parts.append(f"аккаунты: {_list(list(it.accounts))}")
        if urls:
            parts.append(f"ссылок на рекламу: {len(urls)}")
        parts.append(f"застройщик «{d.name}»" + (" — по рекламе" if created and d.id != UNKNOWN_DEV else ""))
        if c.lat is None:
            parts.append("координат нет — сверьте адрес в 2ГИС")
        e = _event(db, author, "; ".join(parts) + ". Сведения рекламы, аналитиком не проверены", complex_id=c.id,
                   at=now)
        db.flush()
        rep.event_ids.append(e.id)
        rep.created.append(c.id)
        if ads:
            rep.rules += rerun_rules(db, ads)
    db.flush()
    return rep


# ---------- вывод ----------

def _obj(db: Session, oid: int) -> models.DiscoveredObject:
    o = db.get(models.DiscoveredObject, oid)
    if o is None:
        raise HTTPException(404, NOT_FOUND)
    return o


def _geo(db: Session, oids: list[int]) -> dict[str, list[dict]]:
    """Последние сверки 2ГИС объектов(по str(id)) - по одной на запрос, не больше GEO_MAX, новые первыми. Два запроса
    на все объекты: сначала выбираются id сверок, затем читаются только они(с кандидатами)."""
    from .integrations import dgis
    from .settings import get_settings

    G = models.GeoCheck
    picked: dict[str, list[int]] = {}
    seen: set[tuple[str, str]] = set()
    for gid, tid, qn in db.execute(select(G.id, G.target_id, G.query_norm)
                                   .where(G.target_kind == "discovered", G.target_id.in_([str(i) for i in oids]))
                                   .order_by(G.fetched_at.desc(), G.id.desc())):
        gids = picked.setdefault(tid, [])
        if (tid, qn) in seen or len(gids) >= GEO_MAX:
            continue
        seen.add((tid, qn))
        gids.append(gid)
    if not picked:
        return {}
    s = get_settings()
    rows = {g.id: g for g in db.scalars(select(G).where(G.id.in_([i for gids in picked.values() for i in gids])))}
    return {tid: [dgis.GeoCheckOut.model_validate(dgis.check_out(rows[i], s)).model_dump(by_alias=True) for i in gids]
            for tid, gids in picked.items()}


def discovered_out(db: Session, objs: list[models.DiscoveredObject], as_of: date | None = None) -> list[dict]:
    """Объекты для API(DiscoveredOut): упоминания, цитаты, стадия, признаки продажи, сверки 2ГИС, оценка."""
    from .settings import get_settings

    M = models.DiscoveredMention
    ids = [o.id for o in objs]
    if not ids:
        return []
    mentions: dict[int, list[models.DiscoveredMention]] = {}
    for r in db.scalars(select(M).where(M.object_id.in_(ids)).order_by(M.id)):
        mentions.setdefault(r.object_id, []).append(r)
    facts = screening.load_facts(db, {r.ad_id for rs in mentions.values() for r in rs})
    names = dict(db.execute(select(models.Complex.id, models.Complex.name)
                            .where(models.Complex.id.in_([o.complex_id for o in objs if o.complex_id]))).all())
    geo = _geo(db, ids)
    days = int(get_settings().screening_days)
    as_of = as_of or screening.today()
    since = as_of.toordinal() - days

    def ad_date(r: models.DiscoveredMention) -> str:
        f = facts.get(r.ad_id)
        return (f.date if f else None) or ""

    out = []
    for o in objs:
        rs = sorted(mentions.get(o.id, []), key=lambda r: (ad_date(r), r.id), reverse=True)
        quotes, sources = [], {}
        stage = {"under_construction": 0, "completed": 0, "unknown": 0, "quotes": []}
        types: dict[str, int] = {}
        ads: list[tuple[screening.AdFacts, str]] = []
        for r in rs:
            f = facts.get(r.ad_id)
            sources[r.source] = sources.get(r.source, 0) + 1
            stage[r.stage if r.stage in STAGES else "unknown"] += 1
            if r.stage in ("under_construction", "completed") and r.stage_quote and len(stage["quotes"]) < QUOTES_MAX:
                stage["quotes"].append({"adId": r.ad_id, "value": r.stage, "quote": r.stage_quote})
            if len(quotes) < QUOTES_MAX:
                quotes.append({"adId": r.ad_id, "quote": r.quote, "name": r.name, "address": r.address,
                               "source": r.source, "date": f.date if f else None, "platform": f.platform if f else None,
                               "account": (f.account if f and f.source != "bot" else None)})
            if f is not None and f.status != "dismissed":
                ads.append((f, "mention"))
                for t in dict.fromkeys(t for t, _ in f.concerning + f.supporting):
                    types[t] = types.get(t, 0) + 1
        concerning = [f for f, _ in ads if f.concerning]
        permit = {"status": "unknown", "text": "Нет в открытых данных: объект известен только по рекламе",
                  "source": None, "url": None, "registryKey": None, "registryStage": None, "verified": False}
        sc = screening.screen(permit, ads, as_of=as_of, days=days, origin=ADVERTISED)
        out.append({
            "id": o.id, "name": o.name, "normKey": o.norm_key, "aliases": list(o.aliases or []),
            "addressHint": o.address_hint, "status": o.status, "origin": o.origin or "ads", "complexId": o.complex_id,
            "complexName": names.get(o.complex_id), "firstSeen": o.first_seen, "lastSeen": o.last_seen,
            "adsCount": o.ads_count or 0, "accounts": list(o.accounts or []), "sourceUrls": list(o.source_urls or []),
            "note": o.note, "decidedBy": o.decided_by, "decidedAt": o.decided_at, "sources": sources,
            "quotes": quotes, "stageHints": stage,
            "saleSignals": {"ads": len(ads), "concerning": len(concerning),
                            "recentConcerning": sum(1 for f in concerning if f.day and f.day.toordinal() >= since),
                            "types": types, "lastAdAt": max((f.date for f, _ in ads if f.date), default=None)},
            "geo": geo.get(str(o.id), []), "screening": sc})
    return out


def _one(db: Session, oid: int) -> dict:
    return discovered_out(db, [_obj(db, oid)])[0]


# ---------- маршруты ----------

OID = Path(..., ge=1, le=2_147_483_647, description="id объекта из рекламы")
STATUS_ORDER = {"new": 0, "linked": 1, "dismissed": 2}


@router.get("/api/discovered", response_model=list[schemas.DiscoveredOut], response_model_by_alias=True)
def list_discovered(status: schemas.DiscoveredStatus | None = Query(None, description="new | linked | dismissed"),
                    db: Session = Depends(get_db)):
    """Объекты из рекламы(ЖК, которых нет в справочнике и открытом реестре): новые первыми, затем по числу публикаций.
    Цитаты(до трёх), стадия по рекламе, признаки продажи, последние сверки 2ГИС и оценка "Проверка по рекламе"."""
    O = models.DiscoveredObject
    q = select(O)
    if status is not None:
        q = q.where(O.status == status)
    objs = sorted(db.scalars(q), key=lambda o: (STATUS_ORDER.get(o.status, 9), -(o.ads_count or 0),
                                                -(models.as_utc(o.last_seen).timestamp() if o.last_seen else 0), o.id))
    return discovered_out(db, objs)


@router.get("/api/discovered/{oid}", response_model=schemas.DiscoveredOut, response_model_by_alias=True)
def get_discovered(oid: int = OID, db: Session = Depends(get_db)):
    return _one(db, oid)


@router.post("/api/discovered/{oid}/geo", response_model=schemas.DiscoveredGeoOut, response_model_by_alias=True,
             status_code=201, responses={200: {"description": "Все ответы — из кэша"},
                                         429: {"description": "Лимит запросов 2ГИС"},
                                         502: {"description": "2ГИС не ответил"},
                                         503: {"description": "2ГИС не настроен или ключ отклонён"}})
def geo_discovered(body: schemas.DiscoveredGeoIn, request: Request, response: Response, oid: int = OID,
                   db: Session = Depends(get_db)):
    """Кандидаты 2ГИС для объекта из рекламы: по "Казахстан, Усть-Каменогорск, ЖК <название>" и, если реклама называла
    адрес, по адресу - кэш первым, квота и права как у "Сверить адрес"(force - мимо кэша, только администратор).
    Ничего не принимается автоматически: выбранный кандидат передаётся в create-complex(candidateId)."""
    from .integrations import dgis

    o = _obj(db, oid)
    queries = [f"Казахстан, {dgis.CITY}, ЖК {o.name}"]
    if o.address_hint and o.address_hint.strip():
        q = dgis.default_query(o.address_hint)
        if dgis.norm_query(q) != dgis.norm_query(queries[0]):
            queries.append(q)
    checks, errors, fresh = [], [], False
    for i, q in enumerate(queries):
        try:
            got = dgis.geo_check(dgis.GeoCheckIn(target_kind="discovered", target_id=str(oid), query=q[:dgis.QUERY_MAX],
                                                 author=body.author, force=body.force), request, Response(), db)
        except HTTPException as e:
            # нет прав - сразу; не удалась последняя, а удачных нет - ошибка последней(иначе - в errors ответа)
            if e.status_code in (401, 403) or (not checks and i == len(queries) - 1):
                raise
            errors.append({"query": q, "status": e.status_code, "detail": str(e.detail)})
            continue
        fresh = fresh or not got.get("cached")
        checks.append(dgis.GeoCheckOut.model_validate(got).model_dump(by_alias=True))
    response.status_code = 201 if fresh else 200
    return {"checks": checks, "errors": errors}


def _candidate(db: Session, oid: int, cand_id: str) -> tuple[models.GeoCheck, dict]:
    G = models.GeoCheck
    for g in db.scalars(select(G).where(G.target_kind == "discovered", G.target_id == str(oid), G.purged_at.is_(None))
                        .order_by(G.fetched_at.desc(), G.id.desc())):
        cand = next((dict(c) for c in g.candidates or [] if str(c.get("id")) == cand_id), None)
        if cand is not None:
            if g.status == "candidates" or (g.status == "accepted" and g.chosen_id == cand_id):
                return g, cand
            raise HTTPException(409, f"Сверку 2ГИС уже решили ({g.decided_by or 'аналитик'}) — выполните сверку заново")
    raise HTTPException(422, "Этого кандидата 2ГИС в сверках объекта нет")


@router.post("/api/discovered/{oid}/create-complex", response_model=schemas.ComplexListOut,
             response_model_by_alias=True, status_code=201,
             responses={409: {"description": "Объект уже привязан, ЖК с таким названием есть или решение уже приняли"},
                        422: {"description": "Кандидата 2ГИС нет в сверках объекта"}})
def create_complex(body: schemas.DiscoveredCreateIn, oid: int = OID, db: Session = Depends(get_db)):
    """Карточка ЖК по объекту из рекламы(origin "advertised": в открытом реестре его нет, сведения рекламы не
    проверены). Застройщик - существующий по БИН или названию, иначе новый "advertised"; без названия - "не установлен".
    Объект - "привязан", в журнал - кто и что создал; правила сопоставления пересчитываются по публикациям с
    упоминаниями без нового запроса к Claude. Формальный риск по такой карточке не оценивается до проверенных
    сведений."""
    o = _obj(db, oid)
    if o.status == "linked":
        name = db.scalar(select(models.Complex.name).where(models.Complex.id == o.complex_id))
        raise HTTPException(409, f"Объект уже привязан к ЖК «{name or o.complex_id}»")
    name = clean_name(body.name)
    k = name_keys(name)
    if len(k.flat) < 2:
        raise HTTPException(422, NAME_EMPTY)
    m = Matcher.load(db)
    hit = m.known_match(k)
    if hit is not None:
        where = "в открытом реестре" if "registry" in (hit.kind, hit.origin) else "в справочнике ЖК"
        raise HTTPException(409, f"ЖК «{hit.name}» уже есть {where}"
                                 + (f" ({hit.complex_id}) — привяжите объект к нему" if hit.complex_id else ""))
    lat, lng = body.lat, body.lng
    geo = cand = None
    if body.candidate_id:
        geo, cand = _candidate(db, oid, body.candidate_id)
        if lat is None:
            lat, lng = cand.get("lat"), cand.get("lon")
    now = models.utcnow()
    try:
        d, dev_created = ensure_developer(db, body.developer_name, body.developer_bin)
        c = models.Complex(id=new_complex_id(db, k.key), developer_id=d.id,
                           **_complex_values(name, body.address, lat, lng))
        db.add(c)
        db.flush()
    except IntegrityError:  # тот же объект параллельно решает другой аналитик: id карточки или застройщика занят
        db.rollback()
        raise HTTPException(409, DECIDED) from None
    O = models.DiscoveredObject
    res = db.execute(update(O).where(O.id == oid, O.status.in_(("new", "dismissed")))
                     .values(status="linked", complex_id=c.id, decided_by=body.author, decided_at=now)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(409, DECIDED)
    db.refresh(o)
    _add_alias(o, name)  # название карточки - тоже написание объекта: его упоминания попадут к этому ЖК
    if geo is not None:
        G = models.GeoCheck
        db.execute(update(G).where(G.id == geo.id)
                   .values(status="accepted", chosen_id=str(cand["id"]), chosen=cand, applied=True,
                           decided_by=geo.decided_by or body.author, decided_at=geo.decided_at or now)
                   .execution_options(synchronize_session=False))
    parts = [f"Карточка ЖК создана по рекламе: «{c.name}» — нет в открытом реестре",
             f"упоминаний в рекламе: {o.ads_count or 0}"]
    if o.accounts:
        parts.append(f"аккаунты: {_list(list(o.accounts))}")
    parts.append(f"застройщик «{d.name}»" + (" — по рекламе" if dev_created and d.id != UNKNOWN_DEV else ""))
    if cand is not None:
        full = cand.get("fullName") or cand.get("name") or cand.get("addressName") or cand["id"]
        parts.append(f"координаты — кандидат 2ГИС {cand['id']} «{full}»")
    elif c.lat is None:
        parts.append("координат нет — сверьте адрес в 2ГИС")
    _event(db, body.author, "; ".join(parts) + ". Сведения рекламы, аналитиком не проверены", complex_id=c.id, at=now)
    rerun_rules(db, _mention_ads(db, oid))
    db.commit()
    row = db.get(models.Complex, c.id)
    return screening.complex_rows(db, [row], screening.compute_complex(db, row))[0]


@router.post("/api/discovered/{oid}/link", response_model=schemas.DiscoveredOut, response_model_by_alias=True,
             responses={409: {"description": "Решение по объекту уже принял другой аналитик"}})
def link_discovered(body: schemas.DiscoveredLinkIn, oid: int = OID, db: Session = Depends(get_db)):
    """Привязать объект из рекламы к существующему ЖК: его название - другое написание этого ЖК, упоминания - реклама
    этого ЖК в "Проверке по рекламе"(гипотеза по публикации). Повтор с тем же ЖК ничего не меняет."""
    from . import registry

    o = _obj(db, oid)
    c = db.get(models.Complex, body.complex_id)
    if c is None:
        raise registry.not_found(db, body.complex_id)
    if o.status == "linked" and o.complex_id == c.id:
        return _one(db, oid)
    old = o.complex_id if o.status == "linked" else None
    now = models.utcnow()
    O = models.DiscoveredObject
    # то же состояние, что прочитано: и статус, и привязка(иначе два аналитика молча перепривязали бы объект)
    res = db.execute(update(O).where(O.id == oid, O.status == o.status, O.complex_id == o.complex_id)
                     .values(status="linked", complex_id=c.id, decided_by=body.author, decided_at=now)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(409, DECIDED)
    if old and old != c.id:
        oldname = db.scalar(select(models.Complex.name).where(models.Complex.id == old)) or old
        _event(db, body.author, f"Объект из рекламы «{o.name}» отвязан от ЖК «{oldname}»: привязан к ЖК «{c.name}»",
               complex_id=old if db.get(models.Complex, old) else None, at=now)
    _event(db, body.author, f"Объект из рекламы «{o.name}» привязан к ЖК «{c.name}» — другое написание названия "
                            f"(упоминаний в рекламе: {o.ads_count or 0})", complex_id=c.id, at=now)
    db.commit()
    db.expire_all()
    return _one(db, oid)


@router.post("/api/discovered/{oid}/dismiss", response_model=schemas.DiscoveredOut, response_model_by_alias=True,
             responses={409: {"description": "Решение по объекту уже принял другой аналитик"}})
def dismiss_discovered(body: schemas.DiscoveredDismissIn, oid: int = OID, db: Session = Depends(get_db)):
    """Отклонить объект из рекламы(не ЖК, подборка, ошибка распознавания…) с причиной; привязка, если была, снимается.
    Карточка ЖК, созданная по объекту, остаётся."""
    o = _obj(db, oid)
    if o.status == "dismissed" and (o.note or "") == body.reason:
        return _one(db, oid)
    old = o.complex_id if o.status == "linked" else None
    now = models.utcnow()
    O = models.DiscoveredObject
    res = db.execute(update(O).where(O.id == oid, O.status == o.status, O.complex_id == o.complex_id)
                     .values(status="dismissed", complex_id=None, note=body.reason, decided_by=body.author,
                             decided_at=now)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(409, DECIDED)
    tail = ""
    if old:
        oldname = db.scalar(select(models.Complex.name).where(models.Complex.id == old)) or old
        tail = f"; привязка к ЖК «{oldname}» снята"
    _event(db, body.author, f"Объект из рекламы «{o.name}» отклонён аналитиком: {body.reason}{tail}",
           complex_id=old if old and db.get(models.Complex, old) else None, at=now)
    db.commit()
    db.expire_all()
    return _one(db, oid)


@router.post("/api/discovered/import", response_model=schemas.AdvertisedImportOut, response_model_by_alias=True,
             responses={401: {"description": "Нет токена администратора"}, 403: {"description": "Неверный токен"},
                        503: {"description": "Администрирование не настроено"}})
def import_discovered(body: schemas.AdvertisedImportIn, admin: auth.Admin = Depends(auth.require_admin),
                      db: Session = Depends(get_db)):
    """Импорт ЖК по рекламе списком(администратор): карточки "advertised" с другими названиями, аккаунтами и ссылками
    рекламы. Повтор того же списка ничего не меняет; ЖК, которые есть в реестре или справочнике, пропускаются."""
    rep = import_advertised(db, body.items, author=body.author, note=body.note)
    db.commit()
    return rep.out()
