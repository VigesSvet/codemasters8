"""«Проверка по рекламе» — детерминированная объяснимая оценка каждого ЖК и застройщика по рекламе и открытым данным.

Это не формальный риск R1–R6 (app.risk не меняется и ничего отсюда не читает) и не вывод о нарушении: статус
публикаций, привязки и сигналы оценка не трогает, только читает. Каждая оценка несёт пояснение DISCLAIMER.

Входы по ЖК:
- основание по открытым данным или сверке (permit): permitted — Homeportal называет документ основания (гарантия ЕО,
  разрешение МИО, участие БВУ) или Казреестр (Ashyq Data — набор разрешений на привлечение денег дольщиков) содержит
  запись объекта, либо аналитик сверил документ «есть»; not_permitted — объект в разделе Homeportal «Без разрешения на
  дольщиков» или аналитик сверил «нет» по применимым документам (гарантии ЕО и разрешения МИО нет — как R1; участие БВУ
  без разрешения МИО основанием не бывает); unknown — сведений нет (ЖК по рекламе, демо без сверки).
  Старшинство (permit_for): сверка аналитика («есть»/«нет» с источником и датой сверки — своей или общей, как
  risk.doc_source) важнее открытых данных: «есть» по любому документу → permitted (source «analyst»); «нет» по гарантии
  ЕО и разрешению МИО → not_permitted (analyst); затем раздел «Без разрешения на дольщиков», затем документ Homeportal —
  кроме документа того вида, по которому аналитик сверил «нет», затем запись Казреестра. «Есть»/«нет» без источника или
  даты сверкой не считается;
- стадия: фильтр источника «Завершён» у объекта реестра, стадия карточки «введён», стадия объекта по рекламе (Claude,
  objectStage — только с дословной цитатой);
- реклама, отнесённая к ЖК (основание отнесения видно в причинах): привязка, подтверждённая аналитиком (confirmed) — и
  тогда только к этому ЖК; кандидат Claude высокой или средней уверенности (claude); упоминание объекта из рекламы,
  привязанного аналитиком к ЖК (discovered); лучшая гипотеза правил сопоставления или привязка-гипотеза (rules).
  Отклонённые аналитиком гипотезы и публикации со статусом «Отклонено» не учитываются;
- признаки приёма денег (concerning): старт продаж, бронь, ДДУ, рассрочка, первоначальный взнос — по проверенному ответу
  Claude, без него — по словарю модели риска (markers.money + «старт продаж»). Продажа, цена и скидка — только
  сопутствующие. Решение аналитика «это не предложение внести деньги» снимает признаки публикации;
- свежесть: дата публикации не раньше SAQ_SCREENING_DAYS (120) дней до даты расчёта.

Статусы ЖК (порядок проверки):
1. есть основание (permitted): ok «В реестре — замечаний нет»; check — если источник считает объект завершённым, а
   свежая реклама с признаком приёма денег предлагает строящийся объект или старт продаж (новая очередь может не иметь
   своего основания);
2. нет основания (not_permitted или unknown): suspect «Подозрение: продажа без разрешения» — есть свежая реклама с
   признаком приёма денег, по которой объект не сдан (стадия не «completed»), и карточка не «введён»; check — свежая
   реклама с признаками есть, но объект сдан по рекламе или карточке (продажа в сданном доме законна), либо раздел «Без
   разрешения на дольщиков» без такой рекламы, либо реклама есть без признаков, либо карточка создана по рекламе;
   unknown «Нет данных для оценки» — нет ни основания, ни рекламы.
basis: confirmed — решающие публикации привязаны аналитиком (у suspect — хотя бы одна), hypothesis — иначе.

Решение аналитика (таблица screening_decisions, маршруты app.decisions) важнее автоматической оценки, пока действует:
- «Объект сдан» (commissioned: акт, дата акта, источник; стадия карточки — «введён») → ok «Дом сдан — замечаний нет»
  (variant commissioned): продажа квартир в сданном доме законна;
- «Проверено — замечаний нет» (no_issues: что проверено, источник) → ok «Проверено — замечаний нет» (variant analyst).
Первая причина — сведения решения (kind «decision»), затем основание и автоматическая оценка без решения; basis
confirmed. Решение снимается автоматически (reopened — вычисляется при чтении, в БД остаётся действующим как история),
когда к ЖК отнесена публикация не раньше минуты решения (дата публикации — местное время; без времени — со следующего
дня) с признаком приёма денег (concerning): у no_issues — любым; у commissioned — только если реклама предлагает
строящийся объект или новую очередь (стадия по рекламе «строится», признак «старт продаж» или слова «строится»,
«новая очередь», «котлован» в тексте) — обычная продажа квартир (в том числе в рассрочку) в сданном доме решение не
снимает. Тогда статус — автоматический (check / suspect / …), первая причина — «Новая реклама после решения аналитика
(<дата>): …», а decision.reopenedBy называет публикацию, признак и цитату. Снять повторное срабатывание: отклонить
публикацию, «это не предложение внести деньги», отклонить привязку — или записать новое решение.

Застройщик: suspect — хотя бы один его ЖК suspect; ok — все его ЖК ok (в реестре, сданы или проверены аналитиком);
unknown — ЖК в системе нет; иначе check.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, lazyload

from . import models
from .pdf import PLATFORMS
from .risk import doc_source, hits, quote as marker_quote
from .risk_model import ALMATY, BUILT, MARKERS
from .settings import get_settings

Obj = Dict[str, Any]
SYSTEM = "Система"
ADVERTISED = "advertised"
# События журнала с гипотезой правил (analysis._hypothesis_event, ads_service._create_manual, seed.journal)
HYPOTHESIS_PREFIX = "Гипотеза ИИ: ЖК «"
NO_HYPOTHESIS_PREFIX = "ИИ: ЖК не определён"

CONCERNING = ("presale", "booking", "ddu", "installment", "down_payment")
SUPPORTING = ("sale", "price", "discount")
SIGNAL_LABELS = {"sale": "продажа", "presale": "старт продаж", "booking": "бронь", "installment": "рассрочка",
                 "down_payment": "первоначальный взнос", "ddu": "ДДУ", "price": "цена", "discount": "скидка",
                 "keys_handover": "сдача ключей", "money": "приём денег", "other": "другое"}
# словарь правил (без ответа Claude): маркер → вид признака; прочие маркеры markers.money — «приём денег»
_RULE_TYPES = {"бронь": "booking", "бронирование": "booking", "забронир": "booking", "депозит": "booking",
               "задаток": "booking", "предоплат": "booking", "первый взнос": "down_payment",
               "первоначальный взнос": "down_payment", "взнос": "down_payment", "рассрочк": "installment",
               "договор долевого участия": "ddu", "долевого участия": "ddu", "долевом участии": "ddu", "дду": "ddu",
               "старт продаж": "presale", "предпродаж": "presale"}
_RULE_MARKERS = list(dict.fromkeys(list(MARKERS["money"]) + ["старт продаж", "предпродаж"]))

LABELS = {"ok": "В реестре — замечаний нет", "check": "Требует проверки",
          "suspect": "Подозрение: продажа без разрешения", "unknown": "Нет данных для оценки"}
DEV_LABELS = {"ok": "Все ЖК в реестре — замечаний нет", "check": "Требует проверки",
              "suspect": "Подозрение: есть ЖК с продажей без разрешения", "unknown": "Нет данных для оценки"}
DEV_OK_DECIDED = "Все ЖК без замечаний — в реестре, сданы или проверены аналитиком"
# Решение аналитика: подпись статуса ok и вариант (ScreeningOut.variant)
DECISION_LABELS = {"commissioned": "Дом сдан — замечаний нет", "no_issues": "Проверено — замечаний нет"}
VARIANTS = {"commissioned": "commissioned", "no_issues": "analyst"}
DECISION_RULE = {
    "commissioned": "Продажа квартир в сданном доме законна; новая реклама со стартом продаж или строящейся очередью "
                    "снимет решение автоматически",
    "no_issues": "Новая реклама с признаком приёма денег после решения снимет его автоматически",
}
# Строящийся объект или новая очередь в тексте рекламы: у сданного дома такая реклама с признаком приёма денег снимает
# отметку «сдан» (обычная продажа квартир в сданном доме — нет)
_BUILDING_MARKERS = ["строится", "строящ", "новая очередь", "новой очереди", "новую очередь", "следующая очередь",
                     "следующей очереди", "котлован", "на этапе строительства", "старт продаж", "предпродаж"]
DISCLAIMER = "Предварительная автоматическая оценка по рекламе и открытым данным — не вывод о нарушении"
RANK = {"suspect": 3, "check": 2, "unknown": 1, "ok": 0}
STATUSES = ("ok", "check", "suspect", "unknown")
BASIS_TEXT = {"confirmed": "привязка к ЖК подтверждена аналитиком",
              "discovered": "название из рекламы привязано к ЖК аналитиком, публикация — гипотеза",
              "claude": "ЖК назван в ответе Claude — гипотеза",
              "rules": "гипотеза правил сопоставления",
              "mention": "объект назван в рекламе (ответ Claude)"}
KIND_ORDER = ("confirmed", "discovered", "claude", "rules", "mention")
REASON_ADS = 3
DOC_LABELS = {"guarantee": "гарантия ЕО (КЖК)", "mio": "разрешение МИО", "bvu": "участие БВУ"}
DOC_ABSENT = {"guarantee": "гарантии ЕО", "mio": "разрешения МИО", "bvu": "участия банка"}  # «… нет»
# Короткое название источника для сверки в один клик (как варианты источника в форме сверки)
SOURCE_SHORT = {"homeportal": "Homeportal", "homeportal_section": "Homeportal", "ashyq": "Казреестр (Ashyq)"}
SUGGEST_REFS = 3
PLACEHOLDER_DEV = "ad-unknown"  # застройщик «не установлен» у ЖК по рекламе (app.discovery)


def today() -> date:
    return datetime.now(ALMATY).date()


def _day(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _fmt_day(value: Optional[str]) -> str:
    d = _day(value)
    return d.strftime("%d.%m.%Y") if d else "без даты"


def _short(s: Optional[str], n: int = 160) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------- публикации ----------

@dataclass
class AdFacts:
    """Что оценка знает о публикации: дата, площадка, решения аналитика, признаки, стадия и к каким ЖК она отнесена."""
    id: str
    date: str
    day: Optional[date]
    platform: str
    account: str
    source: str
    status: str
    complex_id: Optional[str]
    confirmed: bool
    rejected: frozenset
    terms_rejected: bool
    concerning: List[Tuple[str, str]] = field(default_factory=list)  # (вид, цитата)
    supporting: List[Tuple[str, str]] = field(default_factory=list)
    stage: Optional[str] = None  # under_construction | completed | unknown (Claude objectStage)
    stage_quote: str = ""
    building: List[Tuple[str, str]] = field(default_factory=list)  # (маркер строящегося объекта / новой очереди, цитата)
    claude: Dict[str, str] = field(default_factory=dict)  # ЖК → уверенность (high | medium)
    rules: Optional[str] = None  # лучшая гипотеза правил (журнал)
    discovered: Set[str] = field(default_factory=set)  # ЖК по привязанным объектам из рекламы
    from_claude: bool = False

    def targets(self) -> Dict[str, str]:
        """ЖК, к которым публикация отнесена, и основание. Подтверждённая привязка — единственная."""
        if self.status == "dismissed":
            return {}
        if self.confirmed and self.complex_id:
            return {self.complex_id: "confirmed"}
        out: Dict[str, str] = {}
        for cid in sorted(self.discovered):
            out.setdefault(cid, "discovered")
        for cid in self.claude:
            out.setdefault(cid, "claude")
        if self.complex_id:
            out.setdefault(self.complex_id, "rules")
        if self.rules:
            out.setdefault(self.rules, "rules")
        return {cid: k for cid, k in out.items() if cid not in self.rejected}

    @property
    def who(self) -> str:
        if self.source == "bot":
            return "заявка жителя через бота"
        platform = PLATFORMS.get(self.platform, self.platform or "")
        return ", ".join(x for x in (platform, self.account) if x) or "площадка не указана"


def rules_hypotheses(db: Session, ad_ids: Optional[Iterable[str]] = None) -> Dict[str, Optional[str]]:
    """Последняя гипотеза правил по публикации — из журнала («Гипотеза ИИ: ЖК «…»» с ЖК или «ИИ: ЖК не определён»
    от «Системы»): {публикация: ЖК или None}."""
    E = models.AuditEvent
    q = (select(E.ad_id, E.complex_id).where(E.kind == "ai", E.who == SYSTEM, E.ad_id.is_not(None),
                                              or_(E.what.like(HYPOTHESIS_PREFIX + "%"),
                                                  E.what.like(NO_HYPOTHESIS_PREFIX + "%")))
         .order_by(E.at, E.id))
    if ad_ids is not None:
        q = q.where(E.ad_id.in_(list(ad_ids)))
    out: Dict[str, Optional[str]] = {}
    for aid, cid in db.execute(q):
        out[aid] = cid
    return out


def discovered_links(db: Session, ad_ids: Optional[Iterable[str]] = None) -> Dict[str, Set[str]]:
    """Публикация → ЖК, к которым аналитик привязал объекты из рекламы, упомянутые в ней."""
    M, O = models.DiscoveredMention, models.DiscoveredObject
    q = (select(M.ad_id, O.complex_id).join(O, O.id == M.object_id)
         .where(O.status == "linked", O.complex_id.is_not(None)))
    if ad_ids is not None:
        q = q.where(M.ad_id.in_(list(ad_ids)))
    out: Dict[str, Set[str]] = {}
    for aid, cid in db.execute(q):
        out.setdefault(aid, set()).add(cid)
    return out


def _latest_done(ad: models.Ad) -> Optional[models.LlmAnalysis]:
    return next((r for r in ad.llm_analyses if r.status == "done" and r.result), None)


def _building(ad: models.Ad) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for marker in hits(f"{ad.text or ''} {ad.ocr or ''}", _BUILDING_MARKERS):
        q = marker_quote(ad.text, marker) or marker_quote(ad.ocr, marker) or marker
        out.append((marker, _short(q)))
    return out


def _rule_signals(ad: models.Ad) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for marker in hits(f"{ad.text or ''} {ad.ocr or ''}", _RULE_MARKERS):
        kind = _RULE_TYPES.get(marker.lower(), "money")
        q = marker_quote(ad.text, marker) or marker_quote(ad.ocr, marker) or marker
        out.append((kind, _short(q)))
    return out


def facts_of(ad: models.Ad, rules: Optional[str], discovered: Set[str]) -> AdFacts:
    f = AdFacts(id=ad.id, date=ad.date or "", day=_day(ad.date), platform=ad.platform or "", account=ad.account or "",
                source=ad.source or "manual", status=ad.status or "new", complex_id=ad.complex_id,
                confirmed=bool((ad.link_confirmed_by or "").strip()) or ad.status == "confirmed",
                rejected=frozenset(ad.rejected_links or []), terms_rejected=ad.terms_review == "rejected",
                rules=rules, discovered=set(discovered), building=_building(ad))
    row = _latest_done(ad)
    res = dict(row.result or {}) if row is not None else {}
    if row is not None:
        f.from_claude = True
        seen = set()
        for s in res.get("saleSignals") or []:
            t, q = s.get("type"), _short(s.get("quote"))
            if (t, q) in seen:
                continue
            seen.add((t, q))
            if t in CONCERNING:
                f.concerning.append((t, q))
            elif t in SUPPORTING:
                f.supporting.append((t, q))
        for c in res.get("complexCandidates") or []:
            if c.get("confidence") in ("high", "medium") and c.get("complexId"):
                f.claude.setdefault(c["complexId"], c["confidence"])
        st = res.get("objectStage") or {}
        if st.get("value") in ("under_construction", "completed") and st.get("quote"):
            f.stage, f.stage_quote = st["value"], _short(st["quote"])
        elif st:
            f.stage = "unknown"
    else:
        f.concerning = _rule_signals(ad)
    if f.terms_rejected:
        f.concerning = []
    return f


def load_facts(db: Session, ad_ids: Optional[Iterable[str]] = None) -> Dict[str, AdFacts]:
    A = models.Ad
    q = select(A).options(lazyload(A.media), lazyload(A.ingest))
    ids = list(ad_ids) if ad_ids is not None else None
    if ids is not None:
        if not ids:
            return {}
        q = q.where(A.id.in_(ids))
    ads = list(db.scalars(q))
    rules = rules_hypotheses(db, ids)
    disc = discovered_links(db, ids)
    return {ad.id: facts_of(ad, rules.get(ad.id), disc.get(ad.id, set())) for ad in ads}


# ---------- основание по открытым данным ----------

def _stage_of(rs: List[Obj]) -> Optional[str]:
    t = " ".join((r.get("stage") or "") for r in rs).lower()
    if "заверш" in t and "строительств" not in t:
        return "completed"
    if "строительств" in t:
        return "under_construction"
    return None


def _doc(r: Obj) -> str:
    num = r.get("docNumber") or ""
    doc = (num if "№" in num else f"№ {num}") if num else "без номера"
    d = r.get("docDate")
    return doc + (f" от {d[8:10]}.{d[5:7]}.{d[:4]}" if d and len(d) >= 10 else "")


def analyst_checks(b: Obj) -> Dict[str, Obj]:
    """Сверенные документы основания: «есть»/«нет» с источником и датой сверки (своей или общей — risk.doc_source)."""
    out: Dict[str, Obj] = {}
    for d in DOC_LABELS:
        if b.get(d) in ("yes", "no"):
            p = doc_source(b, d)
            if p["source"] and p["checkedAt"]:
                out[d] = p
    return out


def _checked_text(b: Obj, ok: Dict[str, Obj], docs: List[str]) -> str:
    """«(источник, дата)» решающих документов: их собственная сверка (запись аналитика — basis.sources[документ] с
    checkedBy); у сведений без сверки аналитиком (демо-набор) — общая сверка, если она есть."""
    own = (b.get("sources") or {})
    if b.get("source") and b.get("checkedAt") and not any((own.get(d) or {}).get("checkedBy") for d in docs):
        return f" ({b['source']}, {_fmt_day(b['checkedAt'])})"
    parts = list(dict.fromkeys(f"{ok[d]['source']}, {_fmt_day(ok[d]['checkedAt'])}" for d in docs if d in ok))
    return f" ({'; '.join(parts)})" if parts else ""


def _join(xs: List[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " и " + xs[-1]


def permit_for(c: models.Complex, rs: List[Obj], key: Optional[str]) -> Obj:
    """Основание привлекать деньги дольщиков: сверка аналитика (basis «есть/нет» с источником и датой) важнее
    открытых данных; затем раздел «Без разрешения на дольщиков», документ Homeportal, запись Казреестра."""
    b = c.basis or {}
    stage = _stage_of(rs) if rs else None
    base = {"registryKey": key, "registryStage": stage}
    ok = analyst_checks(b)
    yes = [d for d in DOC_LABELS if d in ok and b.get(d) == "yes"]
    no = [d for d in DOC_LABELS if d in ok and b.get(d) == "no"]
    if yes:
        url = next((ok[d]["url"] for d in yes if ok[d].get("url")), None)  # ссылка сверки (своя запись документа)
        return {**base, "status": "permitted", "source": "analyst", "url": url, "verified": True,
                "text": "Основание сверено: " + ", ".join(DOC_LABELS[d] for d in yes) + " — есть"
                        + _checked_text(b, ok, yes)}
    if "guarantee" in no and "mio" in no:
        url = next((ok[d]["url"] for d in no if ok[d].get("url")), None)
        return {**base, "status": "not_permitted", "source": "analyst", "url": url, "verified": True,
                "text": "Основание сверено: " + _join([DOC_ABSENT[d] for d in no]) + " нет" + _checked_text(b, ok, no)}
    section = next((r for r in rs if r.get("source") == "homeportal_section"
                    and "без разрешения" in (r.get("sourceSection") or "").lower()), None)
    if section is not None:
        return {**base, "status": "not_permitted", "source": "homeportal_section", "url": section.get("sourceUrl"),
                "verified": False,
                "text": f"В открытых данных объект — в разделе Homeportal «{section.get('sourceSection')}» (сведения "
                        "источника, не юридический вывод)"}
    # документ Homeportal того вида, по которому аналитик сверил «нет», основанием не считается
    hp = next((r for r in rs if r.get("source") == "homeportal" and r.get("fundingMethod")
               and r["fundingMethod"] not in no), None)
    if hp is not None:
        label = DOC_LABELS.get(hp["fundingMethod"], hp.get("fundingText") or "документ основания")
        return {**base, "status": "permitted", "source": "homeportal", "url": hp.get("sourceUrl"), "verified": False,
                "text": f"В открытых данных есть основание: Homeportal — {label} {_doc(hp)} (сведения источника, "
                        "применимость к очереди аналитиком не проверена)"}
    ash = [r for r in rs if r.get("source") == "ashyq"]
    if ash:
        return {**base, "status": "permitted", "source": "ashyq", "url": ash[0].get("sourceUrl"), "verified": False,
                "text": f"В открытых данных есть основание: Казреестр (Ashyq Data) — запись о разрешении на "
                        f"привлечение денег дольщиков (записей: {len(ash)}; сведения источника, применимость к очереди "
                        "аналитиком не проверена)"}
    if no:
        text = ("Сверено аналитиком: " + ", ".join(f"{DOC_LABELS[d]} — нет" for d in no) + _checked_text(b, ok, no)
                + "; остальные документы не сверены — основание не установлено")
    elif rs:
        text = "Объект есть в открытых данных, но документа-основания у него нет"
    elif c.origin == ADVERTISED:
        text = "Нет в открытом реестре: ЖК известен только по рекламе"
    elif c.origin == "registry":
        text = "Записей открытых данных по объекту не найдено"
    else:
        text = "Сведений об основании (гарантия ЕО, разрешение МИО, участие БВУ) нет"
    return {**base, "status": "unknown", "source": None, "url": None, "verified": False, "text": text}


def registry_groups(db: Session, complexes: Iterable[models.Complex]) -> Dict[str, Tuple[Optional[str], List[Obj]]]:
    """ЖК → (ключ объекта открытых данных, его записи): карточка реестра «r-<ключ>» — со своими записями, прочие — без."""
    from . import demo, registry

    by_key: Dict[str, List[Obj]] = {}
    for r in registry.records(db):
        by_key.setdefault(r["key"], []).append(r)
    key_of = {demo.complex_id(k): k for k in by_key}
    out: Dict[str, Tuple[Optional[str], List[Obj]]] = {}
    for c in complexes:
        key = key_of.get(c.id) if c.origin == "registry" else None
        out[c.id] = (key, by_key.get(key, []) if key else [])
    return out


def permits(db: Session, complexes: Iterable[models.Complex],
            groups: Optional[Dict[str, Tuple[Optional[str], List[Obj]]]] = None) -> Dict[str, Obj]:
    """Основание по каждому ЖК; карточка реестра «r-<ключ>» сопоставляется со своими записями открытых данных."""
    complexes = list(complexes)
    groups = groups if groups is not None else registry_groups(db, complexes)
    return {c.id: permit_for(c, groups[c.id][1], groups[c.id][0]) for c in complexes}


def suggestions(rs: List[Obj]) -> Dict[str, Obj]:
    """Документы основания, которые называет открытый реестр (записи Homeportal со способом привлечения), — подсказка
    для сверки в один клик: номер и дата (до трёх), короткое название источника, ссылка первой записи. Сведения
    источника, а не сверка: «есть» записывает только аналитик (POST /api/complexes/{id}/basis)."""
    from . import importer

    order = {s: i for i, s in enumerate(importer.SOURCE_ORDER)}
    out: Dict[str, Obj] = {}
    for d in DOC_LABELS:
        recs = sorted((r for r in rs if r.get("fundingMethod") == d),
                      key=lambda r: (order.get(r.get("source"), 9), r.get("row") or 0, r.get("id") or 0))
        if not recs:
            continue
        refs = list(dict.fromkeys(_doc(r) for r in recs))
        ref = "; ".join(refs[:SUGGEST_REFS]) + (f" и ещё {len(refs) - SUGGEST_REFS}" if len(refs) > SUGGEST_REFS else "")
        if d == "bvu":
            ref += " (банк источником не указан)"
        first = recs[0]
        out[d] = {"ref": ref, "source": SOURCE_SHORT.get(first["source"], first["source"]),
                  "sourceName": importer.SOURCES.get(first["source"], {}).get("name", first["source"]),
                  "url": first.get("sourceUrl"), "records": len(recs)}
    return out


# ---------- оценка ----------

def _ad_reason(f: AdFacts, kind: str, signal: Optional[Tuple[str, str]]) -> Obj:
    if signal is not None:
        t, q = signal
        text = (f"Реклама {_fmt_day(f.date)} ({f.who}): {SIGNAL_LABELS.get(t, t)} — «{q}»; "
                f"{BASIS_TEXT.get(kind, kind)}")
        return {"text": text, "adId": f.id, "quote": q, "signal": t}
    return {"text": f"Реклама {_fmt_day(f.date)} ({f.who}); {BASIS_TEXT.get(kind, kind)}", "adId": f.id}


def _newest(items: List[Tuple[AdFacts, str]]) -> List[Tuple[AdFacts, str]]:
    return sorted(items, key=lambda x: (x[0].date or "", x[0].id), reverse=True)


def _basis(decisive: List[Tuple[AdFacts, str]], *, any_confirmed: bool) -> str:
    if not decisive:
        return "confirmed"
    flags = [k == "confirmed" for _, k in decisive]
    return "confirmed" if (any(flags) if any_confirmed else all(flags)) else "hypothesis"


def screen(permit: Obj, ads: List[Tuple[AdFacts, str]], *, as_of: date, days: int, origin: str,
           card_completed: bool = False) -> Obj:
    """Оценка одного ЖК (или объекта из рекламы — origin «advertised», permit unknown) по правилам модуля."""
    ads = _newest(ads)
    since = as_of - timedelta(days=days)
    recent = [(f, k) for f, k in ads if f.day is not None and f.day >= since]
    concerning = [(f, k) for f, k in recent if f.concerning]
    suspicious = [(f, k) for f, k in concerning if f.stage != "completed"]
    reasons: List[Obj] = [{"text": permit["text"]}]
    status = permit["status"]
    in_registry = status == "permitted"
    decisive: List[Tuple[AdFacts, str]] = []
    any_confirmed = False

    if in_registry:
        conflict = [(f, k) for f, k in concerning
                    if f.stage == "under_construction" or any(t == "presale" for t, _ in f.concerning)]
        if permit.get("registryStage") == "completed" and conflict:
            result = "check"
            decisive = conflict
            reasons.append({"text": "Источник считает объект завершённым («Завершён»), а свежая реклама предлагает "
                                    "строящийся объект или старт продаж — основание может не распространяться на новую "
                                    "очередь"})
            for f, k in conflict[:REASON_ADS]:
                if f.stage == "under_construction":
                    reasons.append({"text": f"Стадия по рекламе: строится — «{f.stage_quote}»", "adId": f.id,
                                    "quote": f.stage_quote})
                reasons.append(_ad_reason(f, k, f.concerning[0]))
        else:
            result = "ok"
            if concerning:
                reasons.append({"text": f"Свежая реклама с признаками приёма денег есть ({len(concerning)}), "
                                        "основание по открытым данным у объекта есть"})
    elif suspicious and not card_completed:
        result = "suspect"
        decisive, any_confirmed = suspicious, True
        for f, k in suspicious[:REASON_ADS]:
            reasons.append(_ad_reason(f, k, f.concerning[0]))
        building = next(((f, k) for f, k in suspicious if f.stage == "under_construction"), None)
        if building is not None:
            reasons.append({"text": f"Стадия по рекламе: строится — «{building[0].stage_quote}»",
                            "adId": building[0].id, "quote": building[0].stage_quote})
        else:
            reasons.append({"text": "Стадия в рекламе не указана — сдача дома не подтверждена"})
    elif concerning:
        result = "check"
        decisive = concerning
        if card_completed:
            reasons.append({"text": "Карточка ЖК: объект введён — продажа квартир в сданном доме законна, сверьте "
                                    "ввод в эксплуатацию"})
        for f, k in concerning[:REASON_ADS]:
            if f.stage == "completed":
                reasons.append({"text": f"По рекламе дом сдан — «{f.stage_quote}»: продажа квартир в сданном доме "
                                        "законна, сверьте ввод в эксплуатацию", "adId": f.id, "quote": f.stage_quote})
            reasons.append(_ad_reason(f, k, f.concerning[0]))
    elif status == "not_permitted":
        result = "check"
        decisive = ads
        reasons.append({"text": f"Свежей рекламы с признаками приёма денег нет (окно {days} дней)" if ads
                        else "Рекламы по объекту не найдено"})
    elif ads:
        result = "check"
        decisive = ads
        reasons.append({"text": f"Реклама есть ({len(ads)}), но без признаков приёма денег за последние {days} дней"})
        for f, k in ads[:1]:
            reasons.append(_ad_reason(f, k, (f.supporting or [None])[0]))
    elif origin == ADVERTISED:
        result = "check"
        reasons.append({"text": "Карточка создана по рекламе; публикации к ЖК пока не отнесены"})
    else:
        result = "unknown"
        reasons.append({"text": "Нет сведений об основании и нет рекламы — оценивать нечего"})

    last = max((f.date for f, _ in ads if f.date), default=None)
    return {"status": result, "label": LABELS[result], "variant": None, "reasons": reasons,
            "counts": {"ads": len(ads), "concerning": len(concerning), "lastAdAt": last, "windowDays": days},
            "basis": _basis(decisive, any_confirmed=any_confirmed), "inRegistry": in_registry,
            "disclaimer": DISCLAIMER}


# ---------- решение аналитика ----------

def _local_minutes(dt: datetime) -> str:
    return models.as_utc(dt).astimezone(ALMATY).strftime("%Y-%m-%dT%H:%M")


def decision_of(row: models.ScreeningDecision) -> Obj:
    """Действующее решение аналитика → словарь оценки (как ScreeningDecisionOut) и минута решения по местному времени
    (_since, для правила снятия)."""
    return {"id": row.id, "kind": row.kind, "status": "active", "label": DECISION_LABELS[row.kind], "by": row.created_by,
            "at": models.iso(row.created_at), "actNumber": row.act_number, "actDate": row.act_date, "source": row.source,
            "sourceUrl": row.source_url, "reason": row.reason, "comment": row.comment, "reopenedBy": None,
            "_since": _local_minutes(row.created_at)}


def active_decisions(db: Session) -> Dict[str, Obj]:
    D = models.ScreeningDecision
    return {row.complex_id: decision_of(row) for row in db.scalars(select(D).where(D.status == "active").order_by(D.id))}


def after(ad_date: Optional[str], since: str) -> bool:
    """Публикация вышла не раньше решения: время публикации (местное, до минуты) — с минуты решения; дата без времени —
    со следующего дня."""
    d = (ad_date or "").strip()
    if len(d) >= 16 and d[10] == "T":
        return d[:16] >= since[:16]
    return len(d) >= 10 and d[:10] > since[:10]


def act_text(act_number: Optional[str], act_date: Optional[str]) -> str:
    """«акт № 12 от 01.09.2026»; без номера — «акт от 01.09.2026»."""
    num = (act_number or "").strip()
    num = (num if "№" in num else f"№ {num}") if num else ""
    return " ".join(x for x in ("акт", num, f"от {_fmt_day(act_date)}" if act_date else "") if x)


def decision_text(d: Obj) -> str:
    """Сведения решения — первая причина оценки: «Сдан: акт № 12 от 01.09.2026, источник ЕГКН; подтвердил: …»."""
    when = _fmt_day(d.get("_since") or d.get("at"))
    if d["kind"] == "commissioned":
        return f"Сдан: {act_text(d.get('actNumber'), d.get('actDate'))}, источник {d['source']}; подтвердил: {d['by']}, {when}"
    reason = (d.get("reason") or "").strip().rstrip(".")
    return f"Проверено: {reason} — источник {d['source']}; проверил: {d['by']}, {when}"


def reopen_trigger(kind: str, since: str, ads: List[Tuple[AdFacts, str]]) -> Optional[Obj]:
    """Первая публикация после решения, которая его снимает: с признаком приёма денег; у «сдан» — ещё и со строящимся
    объектом или новой очередью (стадия «строится», старт продаж, слова «строится», «новая очередь» в тексте)."""
    for f, k in sorted(ads, key=lambda x: (x[0].date or "", x[0].id)):
        if not f.concerning or not after(f.date, since):
            continue
        stage = None
        signal = f.concerning[0]
        if kind == "commissioned":
            presale = next((x for x in f.concerning if x[0] == "presale"), None)
            if f.stage == "under_construction":
                stage = f.stage_quote
            elif f.building:
                stage = f.building[0][1]
            if presale is None and stage is None:
                continue
            signal = presale or signal
        return {"fact": f, "kind": k, "signal": signal, "stage": stage}
    return None


def with_decision(auto: Obj, d: Obj, ads: List[Tuple[AdFacts, str]]) -> Tuple[Obj, Obj]:
    """Автоматическая оценка + решение аналитика → (оценка, решение для ответа API). Действует — ok с подписью и
    вариантом решения; снято новой рекламой — автоматическая оценка с первой причиной «Новая реклама после решения»."""
    view = {k: v for k, v in d.items() if not k.startswith("_")}
    trig = reopen_trigger(d["kind"], d["_since"], ads)
    if trig is not None:
        f, (t, q) = trig["fact"], trig["signal"]
        view.update(status="reopened", reopenedBy={"adId": f.id, "date": f.date or None, "signal": t, "quote": q})
        extra = f", строится — «{trig['stage']}»" if trig["stage"] and trig["stage"] != q else ""
        text = (f"Новая реклама после решения аналитика ({_fmt_day(d['_since'])}): {_fmt_day(f.date)} ({f.who}) — "
                f"{SIGNAL_LABELS.get(t, t)} «{q}»{extra}; решение «{d['label']}» снято автоматически, оценка — "
                "автоматическая")
        first = {"text": text, "adId": f.id, "quote": q, "signal": t, "kind": "decision"}
        return {**auto, "reasons": [first, *auto["reasons"]]}, view
    reasons = [{"text": decision_text(d), "kind": "decision"}, auto["reasons"][0],
               {"text": f"Без решения аналитика оценка — «{auto['label']}»"}, {"text": DECISION_RULE[d["kind"]]}]
    out = {**auto, "status": "ok", "label": DECISION_LABELS[d["kind"]], "variant": VARIANTS[d["kind"]],
           "reasons": reasons, "basis": "confirmed"}
    return out, view


def screen_developer(d: models.Developer, items: List[Tuple[models.Complex, Obj]]) -> Obj:
    counts = {"complexes": len(items), **{s: 0 for s in STATUSES}}
    for _, sc in items:
        counts[sc["status"]] += 1
    worst = sorted(items, key=lambda x: (-RANK[x[1]["status"]], x[0].name, x[0].id))
    reasons: List[Obj] = []
    if d.id == PLACEHOLDER_DEV:
        reasons.append({"text": "Застройщик не установлен — карточки ЖК созданы по рекламе без названия застройщика"})
    names = lambda st: ", ".join(f"«{c.name}»" for c, sc in worst if sc["status"] == st)  # noqa: E731
    if not items:
        status = "unknown"
        reasons.append({"text": "ЖК этого застройщика в системе нет"})
    elif counts["suspect"]:
        status = "suspect"
        reasons.append({"text": f"ЖК с подозрением на продажу без разрешения ({counts['suspect']}): "
                                f"{names('suspect')}"})
    elif counts["ok"] == len(items):
        status = "ok"
        decided = {v: sum(1 for _, sc in items if sc.get("variant") == v) for v in ("commissioned", "analyst")}
        if any(decided.values()):
            parts = [(len(items) - sum(decided.values()), "в реестре"), (decided["commissioned"], "дом сдан"),
                     (decided["analyst"], "проверено аналитиком")]
            reasons.append({"text": f"Все ЖК застройщика ({len(items)}) без замечаний: "
                                    + ", ".join(f"{t} — {n}" for n, t in parts if n)})
        else:
            reasons.append({"text": f"Все ЖК застройщика ({len(items)}) — с основанием по открытым данным, замечаний по "
                                    "рекламе нет"})
    else:
        status = "check"
        if counts["check"]:
            reasons.append({"text": f"Требуют проверки ({counts['check']}): {names('check')}"})
        if counts["unknown"]:
            reasons.append({"text": f"Без данных ({counts['unknown']}): {names('unknown')}"})
    sus = [sc for _, sc in items if sc["status"] == "suspect"]
    if status == "suspect":
        basis = "confirmed" if any(sc["basis"] == "confirmed" for sc in sus) else "hypothesis"
    else:
        basis = "confirmed" if all(sc["basis"] == "confirmed" for _, sc in items) else "hypothesis"
    label = DEV_OK_DECIDED if status == "ok" and any(sc.get("variant") for _, sc in items) else DEV_LABELS[status]
    return {"status": status, "label": label, "reasons": reasons, "counts": counts,
            "worst": [{"complexId": c.id, "name": c.name, "status": sc["status"], "label": sc["label"]}
                      for c, sc in worst[:3]],
            "basis": basis, "disclaimer": DISCLAIMER}


def advert_info(db: Session) -> Dict[str, Obj]:
    """ЖК → объекты из рекламы, привязанные к нему: другие названия, аккаунты, ссылки, упоминаний."""
    O = models.DiscoveredObject
    out: Dict[str, Obj] = {}
    for o in db.scalars(select(O).where(O.status == "linked", O.complex_id.is_not(None)).order_by(O.id)):
        a = out.setdefault(o.complex_id, {"discoveredIds": [], "aliases": [], "accounts": [], "sourceUrls": [],
                                          "ads": 0})
        a["discoveredIds"].append(o.id)
        for x in [o.name, *(o.aliases or [])]:
            if x and x not in a["aliases"]:
                a["aliases"].append(x)
        for x in o.accounts or []:
            if x not in a["accounts"]:
                a["accounts"].append(x)
        for x in o.source_urls or []:
            if x not in a["sourceUrls"]:
                a["sourceUrls"].append(x)
        a["ads"] += int(o.ads_count or 0)
    return out


@dataclass
class Result:
    complexes: Dict[str, Obj]
    developers: Dict[str, Obj]
    permits: Dict[str, Obj]
    advert: Dict[str, Obj]
    as_of: date
    days: int
    suggest: Dict[str, Obj] = field(default_factory=dict)  # ЖК → документы основания из открытого реестра
    decisions: Dict[str, Obj] = field(default_factory=dict)  # ЖК → решение аналитика (действует или снято рекламой)


def compute(db: Session, as_of: Optional[date] = None) -> Result:
    """Оценка всех ЖК и застройщиков одним проходом (публикации, ответы Claude, журнал гипотез, объекты из рекламы)."""
    days = int(get_settings().screening_days)
    as_of = as_of or today()
    cxs = list(db.scalars(select(models.Complex)))
    devs = list(db.scalars(select(models.Developer)))
    facts = load_facts(db)
    attributed: Dict[str, List[Tuple[AdFacts, str]]] = {}
    for f in facts.values():
        for cid, kind in f.targets().items():
            attributed.setdefault(cid, []).append((f, kind))
    groups = registry_groups(db, cxs)
    pm = permits(db, cxs, groups)
    decs = active_decisions(db)
    comp: Dict[str, Obj] = {}
    decided: Dict[str, Obj] = {}
    for c in cxs:
        ads = attributed.get(c.id, [])
        comp[c.id] = screen(pm[c.id], ads, as_of=as_of, days=days, origin=c.origin, card_completed=c.stage == BUILT)
        if c.id in decs:
            comp[c.id], decided[c.id] = with_decision(comp[c.id], decs[c.id], ads)
    by_dev: Dict[str, List[Tuple[models.Complex, Obj]]] = {}
    for c in cxs:
        by_dev.setdefault(c.developer_id, []).append((c, comp[c.id]))
    dev = {d.id: screen_developer(d, by_dev.get(d.id, [])) for d in devs}
    suggest = {cid: sg for cid, (_, rs) in groups.items() if rs and (sg := suggestions(rs))}
    return Result(comp, dev, pm, advert_info(db), as_of, days, suggest, decided)


def summary(r: Result, db: Optional[Session] = None) -> Obj:
    """Сводка для /api/stats: ЖК и застройщики по статусам, решения аналитика (ok по отметке «сдан» и «проверено», снятые
    рекламой), объекты из рекламы по статусам."""
    out: Obj = {"complexes": {s: 0 for s in STATUSES}, "developers": {s: 0 for s in STATUSES},
                "decisions": {"commissioned": 0, "analyst": 0, "reopened": 0},
                "windowDays": r.days, "disclaimer": DISCLAIMER}
    for sc in r.complexes.values():
        out["complexes"][sc["status"]] += 1
        if sc.get("variant") in out["decisions"]:
            out["decisions"][sc["variant"]] += 1
    out["decisions"]["reopened"] = sum(1 for d in r.decisions.values() if d["status"] == "reopened")
    for sc in r.developers.values():
        out["developers"][sc["status"]] += 1
    if db is not None:
        O = models.DiscoveredObject
        counts = {"new": 0, "linked": 0, "dismissed": 0}
        for st, in db.execute(select(O.status)):
            if st in counts:
                counts[st] += 1
        out["discovered"] = counts
    return out


def complex_rows(db: Session, rows: Iterable[models.Complex], r: Result) -> List[Obj]:
    """ЖК для ответа API (ComplexListOut): поля карточки, основание, оценка, объекты из рекламы."""
    from . import schemas

    out = []
    for c in rows:
        d = schemas.ComplexOut.model_validate(c).model_dump()
        d.update(permit=r.permits.get(c.id), screening=r.complexes.get(c.id), advert=r.advert.get(c.id),
                 basis_suggest=r.suggest.get(c.id), decision=r.decisions.get(c.id))
        out.append(d)
    return out


def developer_rows(rows: Iterable[models.Developer], r: Result) -> List[Obj]:
    from . import schemas

    out = []
    for d in rows:
        x = schemas.DeveloperOut.model_validate(d).model_dump()
        x["screening"] = r.developers.get(d.id)
        out.append(x)
    return out

