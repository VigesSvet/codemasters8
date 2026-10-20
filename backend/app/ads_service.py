"""Создание публикации - общее для ручного ввода(POST /api/ads), сборщиков, приёма через API и бота заявок.

Ручной ввод аналитика(Actor trusted): прежнее поведение POST /api/ads без изменений - гипотеза ИИ о ЖК при создании,
подтверждённая аналитиком привязка, события журнала от имени автора. Собранная публикация(COLLECTOR, BOT): поля
очищаются, решения аналитика из внешнего ввода отбрасываются, статус "new", ЖК не привязан, гипотезы считает задание
анализа; в журнале - "Система" и откуда пришла публикация, а не "добавлена вручную".

Заявленные жителем аккаунт и ссылка(source = bot) - не доказательство: сопоставление с ЖК их не учитывает(match_obj)
и в историю аккаунтов они не попадают(ai.matching.build_catalog).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from . import ai, models, world
from .ai import ocr as ocr_engine
from .pdf import PLATFORMS
from .risk import confidence_pct
from .risk_model import T
from .schemas import MEDIA_REF, is_safe_link, local_minutes, strip_ctrl

SYSTEM = "Система"
# Первое событие журнала публикации - откуда она взялась(seed.journal для демо-набора - те же тексты)
AD_SOURCES = {"parser": "Публикация собрана автоматически ({platform})", "manual": "Публикация добавлена вручную",
              "demo": "Демо-запись ({platform})", "bot": "Публикация прислана жителем через Telegram-бота ({platform})"}
# Поля решений аналитика и происхождения: из внешнего ввода не принимаются
DECISION_FIELDS = ("complex_id", "match_confidence", "link_confirmed", "link_confirmed_by", "link_confirmed_at",
                   "rejected_links", "terms_review", "terms_review_by", "terms_review_at", "status", "assignee",
                   "decision_rev", "origin", "source", "id")
REASON_NAMES = {"name": "название", "translit": "название в другом написании", "address": "адрес", "account": "аккаунт",
                "phone": "телефон застройщика", "history": "история аккаунта", "district": "район"}


def _why(h: dict) -> str:
    return ", ".join(dict.fromkeys(REASON_NAMES[r["feature"]] for r in h["reasons"]))


@dataclass(frozen=True)
class Actor:
    """Кто создаёт публикацию: who - автор в журнале; origin/source - происхождение; trusted - ручной ввод аналитика."""
    who: str
    origin: str = "analyst"
    source: str = "manual"
    trusted: bool = True


def analyst(author: str | None) -> Actor:
    """Аналитик из формы ручного ввода; без имени - "Система"(как раньше в POST /api/ads)."""
    return Actor(author or SYSTEM)


COLLECTOR = Actor(SYSTEM, "collected", "parser", trusted=False)
BOT = Actor(SYSTEM, "collected", "bot", trusted=False)


class AdExists(Exception):
    """Публикация с таким id уже есть(409)."""

    def __init__(self, ad_id: str):
        super().__init__(ad_id)
        self.ad_id = ad_id


class ComplexNotFound(Exception):
    """Указанного ЖК нет(404, в main - registry.not_found)."""

    def __init__(self, complex_id: str):
        super().__init__(complex_id)
        self.complex_id = complex_id


def created_text(source: str, platform: str) -> str:
    return AD_SOURCES.get(source, AD_SOURCES["manual"]).format(platform=PLATFORMS.get(platform, platform))


def safe_url(u: str | None) -> str:
    """Ссылка на собранную публикацию: только schemas.is_safe_link(http(s):// с хостом, без учётных данных, пробелов,
    управляющих символов и \\ < > " `, до 512 символов); иначе ""(javascript:, data:, обрезанная ссылка - хуже, чем
    никакой). Ручной ввод(schemas.AdIn) по тем же правилам отвечает 422."""
    v = u.strip() if isinstance(u, str) else ""
    return v if v and is_safe_link(v) else ""


def cover_url(assets) -> str | None:
    """Изображение публикации: первое(по позиции) загруженное текущее изображение не-обложка, затем обложка. Кадры
    видео не берутся."""
    imgs = sorted((a for a in assets if a.status == "downloaded" and a.kind == "image" and a.current
                   and a.parent_id is None), key=lambda a: a.position)
    pick = next((a for a in imgs if a.role != "cover"), None) or (imgs[0] if imgs else None)
    return f"/api/media/{pick.id}" if pick else None


def sanitize_collected(fields: dict) -> dict:
    """Поля собранной публикации: без NUL и управляющих символов, текст и OCR - до предела публикации, длины - как у
    столбцов БД, ссылка - только http(s); решения аналитика и происхождение(DECISION_FIELDS) отбрасываются."""
    out = {k: strip_ctrl(v) for k, v in fields.items() if k not in DECISION_FIELDS}
    for k in ("text", "ocr"):
        if isinstance(out.get(k), str):
            out[k] = ocr_engine.clip(out[k])
    for k, n in (("platform", 16), ("account", 255), ("price", 64), ("date", 32), ("image", 512)):
        if isinstance(out.get(k), str):
            out[k] = out[k].strip()[:n]
    if "url" in out:
        out["url"] = safe_url(out["url"])
    if out.get("price") == "":
        out["price"] = None
    reach = out.get("reach")
    out["reach"] = reach if isinstance(reach, int) and not isinstance(reach, bool) and reach >= 0 else None
    return out


def match_obj(ad) -> dict:
    """Данные публикации для сопоставления с ЖК. У заявки жителя(source = bot) аккаунт и ссылка - его слова, а не
    признак: не учитываются(D20)."""
    bot = getattr(ad, "source", None) == "bot"
    return {"text": ad.text, "ocr": ad.ocr, "account": None if bot else ad.account, "url": None if bot else ad.url}


def _event(db: Session, kind: str, who: str, what: str, at: datetime | None = None, **refs) -> models.AuditEvent:
    e = models.AuditEvent(kind=kind, who=who, what=what, at=models.as_utc(at) if at else models.utcnow(), **refs)
    db.add(e)
    return e


def _attach_upload(db: Session, ad: models.Ad) -> None:
    """Скриншот, загруженный формой(POST /api/media) и указанный в image, привязывается к публикации - условным UPDATE:
    чужую или уже привязанную загрузку так не забрать."""
    m = MEDIA_REF.match(ad.image or "")
    if not m:
        return
    A = models.MediaAsset
    db.execute(update(A).where(A.id == m.group(1), A.ad_id.is_(None), A.origin_ref == "upload")
               .values(ad_id=ad.id).execution_options(synchronize_session=False))


def create_ad(db: Session, fields: dict, *, actor: Actor, at: datetime | None = None, note: str | None = None,
              catalog=None, ad_id: str | None = None) -> models.Ad:
    """Новая публикация в сессии db(flush, без commit). Ручной ввод: AdExists(такой id есть), ComplexNotFound(нет
    ЖК); собранная: id - ad_id или UUID hex(32 символа), статус "new", без привязки и гипотез."""
    if actor.trusted:
        return _create_manual(db, dict(fields), actor, catalog, ad_id)
    return _create_collected(db, dict(fields), actor, at, note, ad_id)


def _create_manual(db: Session, data: dict, actor: Actor, catalog, ad_id: str | None) -> models.Ad:
    """Ручное добавление публикации. Без complexId ЖК подбирает ИИ: лучшая гипотеза с уверенностью от lowConfidence
    становится привязкой-гипотезой(в балл ЖК не входит до подтверждения аналитиком). С complexId и linkConfirmed -
    аналитик выбрал ЖК сам: привязка сразу факт."""
    author = data.pop("author", None) or actor.who
    data["origin"] = actor.origin  # добавлена аналитиком, не из демо-набора
    confirmed = data.pop("link_confirmed", False)
    data["id"] = ad_id or data.get("id") or f"m{uuid.uuid4().hex[:8]}"
    if db.get(models.Ad, data["id"]):
        raise AdExists(data["id"])
    # дата публикации - местное время без смещения, как в источниках(Усть-Каменогорск, UTC+5)
    data["date"] = data.get("date") or local_minutes(models.utcnow())
    linked = None
    if data.get("complex_id"):
        linked = db.get(models.Complex, data["complex_id"])
        if linked is None:
            raise ComplexNotFound(data["complex_id"])
    cat = catalog if catalog is not None else world.catalog(db)
    hyps = [] if confirmed else ai.all_hypotheses(cat, data)
    best = hyps[0] if hyps else None
    hyp = None
    if confirmed:
        data.update(match_confidence=1.0, link_confirmed_by=author, link_confirmed_at=models.utcnow())
    elif linked:  # подсказка формы(выбранная гипотеза) - тоже гипотеза; уверенность - из анализа, если её нет в форме
        hyp = next((h for h in hyps if h["complexId"] == linked.id), None)
        if not data.get("match_confidence") and hyp:
            data["match_confidence"] = hyp["confidence"]
    elif best and best["confidence"] >= T["lowConfidence"]:
        linked, hyp = db.get(models.Complex, best["complexId"]), best
        data["complex_id"], data["match_confidence"] = linked.id, best["confidence"]
    ad = models.Ad(**data)
    db.add(ad)
    _event(db, "system", author, "Публикация добавлена вручную", ad_id=ad.id, complex_id=ad.complex_id)
    if confirmed:
        _event(db, "link", author, f"Привязка к ЖК «{linked.name}» подтверждена аналитиком", ad_id=ad.id,
               complex_id=linked.id)
    elif linked:
        why = _why(hyp) if hyp else "выбран в форме ручного ввода"
        _event(db, "ai", SYSTEM, f"Гипотеза ИИ: ЖК «{linked.name}», уверенность {confidence_pct(ad.match_confidence)}% "
                                 f"({why}) — нужна проверка аналитиком", ad_id=ad.id, complex_id=linked.id)
    else:
        what = "ИИ: ЖК не определён — нужна ручная привязка"
        if best:
            what += (f" (лучшая гипотеза — «{best['complexName']}», {confidence_pct(best['confidence'])}%, ниже порога "
                     f"{confidence_pct(T['lowConfidence'])}%)")
        _event(db, "ai", SYSTEM, what, ad_id=ad.id)
    db.flush()
    _attach_upload(db, ad)
    return ad


AD_COLUMNS = frozenset(models.Ad.__table__.columns.keys())


def _create_collected(db: Session, fields: dict, actor: Actor, at: datetime | None, note: str | None,
                      ad_id: str | None) -> models.Ad:
    data = {k: v for k, v in sanitize_collected(fields).items() if k in AD_COLUMNS}
    when = models.as_utc(at) if at else models.utcnow()
    data["id"] = ad_id or uuid.uuid4().hex
    if db.get(models.Ad, data["id"]):
        raise AdExists(data["id"])
    data["platform"] = data.get("platform") or "other"
    data["account"] = data.get("account") or ""
    data["text"] = data.get("text") or ""
    data["url"] = data.get("url") or ""
    data["date"] = data.get("date") or local_minutes(when)
    # решения - только у аналитика: статус "new", без привязки и уверенности; гипотезы посчитает задание анализа
    data.update(origin=actor.origin, source=actor.source, status="new", match_confidence=0, complex_id=None,
                collected_at=when)
    ad = models.Ad(**data)
    db.add(ad)
    what = created_text(actor.source, ad.platform) + (f" · {note}" if note else "")
    _event(db, "system", actor.who, what, at=when, ad_id=ad.id, complex_id=None)
    db.flush()
    return ad
