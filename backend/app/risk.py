"""Риск-скоринг ЖК и публикаций по ТЗ §5.

Чистые функции над данными в форме API: camelCase-словари, как в frontend/src/data/types.ts.
Веса, пороги, словари и тексты — в shared/risk_model.json. Тот же алгоритм исполняет фронтенд (frontend/src/lib/risk.ts)
как офлайн-фоллбэк; совпадение результатов проверяется на shared/testdata/risk_golden.json.

Правила:
- балл = min(100, сумма весов подтверждённых факторов);
- неизвестные сведения → 0 баллов и «Нужна проверка» (state = "unknown");
- привязка публикации к ЖК — факт только после подтверждения аналитиком; уверенность ИИ — гипотеза, а не факт;
- решение аналитика «это не предложение внести деньги» снимает R2 по публикации (R3 не затрагивается);
- при действующей гарантии ЕО отсутствие отдельного разрешения МИО — не R1;
- красная зона только при score ≥ 60 и подтверждённых R1 + R2;
- карточка ЖК из открытого реестра (origin «registry») без проверенных сведений не оценивается (assessed = False):
  балл 0 не выдаётся за «низкий риск» — UI показывает «риск не оценивается», сигнал не создаётся;
- у карточки из реестра или по рекламе пустой список обращений — R4 «нужна проверка» (unchecked), пока аналитик не
  отметит «Обращений нет — сверено» с источником и датой (appealsCheck); у демо-ЖК пустой список — «нет» (как прежде).
"""
import math
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .risk_model import ALMATY, BUILT, FACTORS, MARKERS, MODEL, OBSERVATIONS, T, VERSION, fill, fmt_date, zone_for

Obj = Dict[str, Any]
NOT_ASSESSED: Obj = MODEL["notAssessed"]

_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


# ---------- текст публикации ----------

def hits(text: str, words: List[str]) -> List[str]:
    """Маркеры из словаря, найденные в тексте; вложенные («взнос» внутри «первый взнос») убираются."""
    t = text.lower()
    found = [w for w in words if w.lower() in t]
    return [w for w in found if not any(o != w and w.lower() in o.lower() for o in found)]


def ad_signals(ad: Obj) -> Dict[str, List[str]]:
    t = f"{ad.get('text') or ''} {ad.get('ocr') or ''}"
    return {"sale": hits(t, MARKERS["sale"]), "funds": hits(t, MARKERS["money"]), "safe": hits(t, MARKERS["safe"])}


def quote(text: Optional[str], marker: str) -> Optional[str]:
    """Фрагмент-основание: предложение с маркером; длинное — обрезается по словам вокруг маркера."""
    if not text:
        return None
    m = marker.lower()
    for s in _SENTENCE.split(text):
        i = s.lower().find(m)
        if i < 0:
            continue
        words = s.split()
        if len(words) <= T["quoteMaxWords"]:
            return " ".join(words)
        wi = len(s[:i].split())
        a = max(0, wi - T["quoteWordsBefore"])
        b = min(len(words), wi + T["quoteWordsAfter"])
        return ("… " if a > 0 else "") + " ".join(words[a:b]) + (" …" if b < len(words) else "")
    return None


def link_confirmed(ad: Obj) -> bool:
    """Привязка к ЖК — факт только после решения аналитика: он подтвердил привязку или саму публикацию.
    Уверенность сопоставления (matchConfidence) — оценка ИИ: сколь угодно высокая, она остаётся гипотезой."""
    return ad.get("status") == "confirmed" or bool((ad.get("linkConfirmedBy") or "").strip())


def terms_rejected(ad: Obj) -> bool:
    """Аналитик отклонил условия оплаты: «это не предложение внести деньги» — публикация не даёт R2."""
    return ad.get("termsReview") == "rejected"


def _pct(x: float) -> int:
    return math.floor(x * 100 + 0.5)


def _num(x: Any) -> Any:
    return int(x) if isinstance(x, float) and x.is_integer() else x


# ---------- сборка результата ----------

def _item(**kw: Any) -> Obj:
    return {k: v for k, v in kw.items() if v is not None and v != ""}


def _factor(code: str, state: str, reason: str, values: Optional[Obj] = None, items: Optional[List[Obj]] = None,
            text: Optional[str] = None) -> Obj:
    f = FACTORS[code]
    return {
        "code": code,
        "title": f["title"],
        "points": f["weight"],
        "state": state,
        "reason": reason,
        "evidence": fill(text if text is not None else f["reasons"][reason], values),
        "items": items or [],
    }


def _ad_item(ad: Obj, marker: str) -> Obj:
    # цитата — из текста публикации, иначе из распознанного скриншота; quoteFrom говорит, откуда она взята
    in_text = quote(ad.get("text"), marker)
    q = in_text or quote(ad.get("ocr"), marker)
    return _item(kind="ad", ref=ad["id"], label=ad.get("account"), source=ad.get("platform"), date=ad.get("date"),
                 url=ad.get("url"), marker=marker, quote=q, quoteFrom=("text" if in_text else "ocr") if q else None)


def _by_date_desc(xs: List[Obj]) -> List[Obj]:
    return sorted(sorted(xs, key=lambda x: x["id"]), key=lambda x: x.get("date") or "", reverse=True)


def _as_date(as_of: Any) -> date:
    if as_of is None:  # «сегодня» по Усть-Каменогорску, а не по поясу процесса (как today() во фронтенде)
        return datetime.now(ALMATY).date()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return date.fromisoformat(str(as_of)[:10])


# ---------- факторы ----------

def doc_source(b: Obj, key: str) -> Obj:
    """Провенанс документа основания: своя запись basis.sources[key] целиком (чужую дату не занимает),
    иначе — общая сверка basis.source / basis.checkedAt (ссылки у неё нет). Документ «нет данных» (не yes/no) не сверялся —
    у него нет ни источника, ни даты, даже при общей сверке."""
    if b.get(key, "unknown") not in ("yes", "no"):
        return {"source": None, "checkedAt": None, "url": None}
    own = (b.get("sources") or {}).get(key)
    if own:
        return {"source": own.get("source"), "checkedAt": own.get("checkedAt"), "url": own.get("url")}
    return {"source": b.get("source"), "checkedAt": b.get("checkedAt"), "url": None}


def _sourced(p: Obj) -> bool:
    return bool(p["source"]) and bool(p["checkedAt"])


def factor_r1(c: Obj) -> Obj:
    if c.get("stage") == BUILT:
        return _factor("R1", "clear", "built")
    b = c.get("basis") or {}
    g, m, k = b.get("guarantee", "unknown"), b.get("mio", "unknown"), b.get("bvu", "unknown")
    src = {d["key"]: doc_source(b, d["key"]) for d in MODEL["documents"]}
    items = [_item(kind="document", ref=d["key"], label=d["label"], value=b.get(d["key"], "unknown"), quote=b.get(d["refField"]),
                   source=src[d["key"]]["source"], date=src[d["key"]]["checkedAt"], url=src[d["key"]]["url"])
             for d in MODEL["documents"]]
    if g == "yes":
        # «отсутствие МИО не является R1» — только когда отсутствие МИО установлено; «нет данных» так не называем
        return _factor("R1", "clear", "ok_guarantee_no_mio" if m == "no" else "ok_guarantee", items=items)
    if m == "yes" and k == "yes":
        return _factor("R1", "clear", "ok_mio", items=items)
    if g == "no" and m == "no":
        # отсутствие установлено, только если у каждого из двух документов есть источник и дата сверки (свои или общие)
        pg, pm = src["guarantee"], src["mio"]
        if _sourced(pg) and _sourced(pm):
            v = {"guaranteeSource": pg["source"], "guaranteeCheckedAt": fmt_date(pg["checkedAt"]),
                 "mioSource": pm["source"], "mioCheckedAt": fmt_date(pm["checkedAt"])}
            if (v["guaranteeSource"], v["guaranteeCheckedAt"]) == (v["mioSource"], v["mioCheckedAt"]):
                return _factor("R1", "hit", "absent", {"source": pg["source"], "checkedAt": v["mioCheckedAt"]}, items,
                               FACTORS["R1"]["variants"]["absent_same_source"])
            return _factor("R1", "hit", "absent", v, items)
        missing = [d["label"] for d in MODEL["documents"] if d["key"] in ("guarantee", "mio") and not _sourced(src[d["key"]])]
        return _factor("R1", "unknown", "absent_unsourced", {"documents": ", ".join(missing)}, items)
    if m == "yes":
        return _factor("R1", "unknown", "mio_without_bank", items=items)
    return _factor("R1", "unknown", "unknown", items=items)


def _ads_factor(code: str, built: bool, linked: List[Obj], pick) -> Obj:
    """R2/R3: подтверждённая привязка → hit, только гипотеза привязки → unknown.
    R2 не дают публикации, по которым аналитик отклонил условия оплаты («это не предложение внести деньги»).
    В доказательства «отклонено аналитиком» идут только публикации, привязанные к ЖК фактом: отклонённая публикация
    с привязкой-гипотезой к этому объекту не относится (она и так R2 не даёт) — её не показываем и не считаем."""
    if built:
        return _factor(code, "clear", "built")
    hit_reason, unlinked_reason = ("offer", "offer_unlinked") if code == "R2" else ("ads", "ads_unlinked")
    conf, unconf, rejected = [], [], []
    for a in linked:
        marker = pick(ad_signals(a))
        if not marker:
            continue
        if code == "R2" and terms_rejected(a):
            if link_confirmed(a):
                rejected.append(_ad_item(a, marker))
        else:
            (conf if link_confirmed(a) else unconf).append(_ad_item(a, marker))
    if conf:
        return _factor(code, "hit", hit_reason, {"count": len(conf)}, conf)
    if unconf:
        return _factor(code, "unknown", unlinked_reason, {"count": len(unconf)}, unconf)
    if rejected:
        return _factor(code, "clear", "offer_rejected", {"count": len(rejected)}, rejected)
    return _factor(code, "clear", "none")


def _money_marker(s: Dict[str, List[str]]) -> Optional[str]:
    return s["funds"][0] if s["funds"] else None


def _adv_marker(s: Dict[str, List[str]]) -> Optional[str]:
    return (s["sale"] or s["funds"] or [None])[0]


def factor_r4(c: Obj, appeals: List[Obj]) -> Obj:
    own = [a for a in appeals if a.get("complexId") == c["id"] and a.get("collective") and a.get("status") != "rejected"]
    item = lambda a: _item(kind="appeal", ref=a["id"], label=a.get("subject"), source=a.get("source"), date=a.get("date"),
                           value=a.get("status"))
    verified = _by_date_desc([a for a in own if a.get("status") == "verified"])
    if verified:
        return _factor("R4", "hit", "verified", {"count": len(verified)}, [item(a) for a in verified])
    pending = _by_date_desc([a for a in own if a.get("status") == "pending"])
    if pending:
        return _factor("R4", "unknown", "pending", items=[item(a) for a in pending])
    if c.get("origin") in NOT_ASSESSED["origins"]:
        # карточка реестра или рекламы: обращения сюда никто не заносил — пустой список не значит «обращений нет»;
        # «нет» — только после сверки аналитиком с источником и датой (POST /api/complexes/{id}/appeals-check)
        chk = c.get("appealsCheck") or {}
        if chk.get("source") and chk.get("checkedAt"):
            return _factor("R4", "clear", "none_checked", {"source": chk["source"], "checkedAt": fmt_date(chk["checkedAt"])})
        return _factor("R4", "unknown", "unchecked")
    return _factor("R4", "clear", "none")


def factor_r5(c: Obj) -> Obj:
    if c.get("stage") == BUILT:
        return _factor("R5", "clear", "built")
    r = c.get("readiness") or {}
    d, k = r.get("declared"), r.get("confirmed")
    items = [_item(kind="readiness", ref=key, label=label, value=_num(x.get("value")), source=x.get("source"), date=x.get("date"))
             for key, label, x in (("declared", "Заявленная готовность", d), ("confirmed", "Подтверждённая готовность", k)) if x]
    ok = lambda x: bool(x) and isinstance(x.get("value"), (int, float)) and bool(x.get("source")) and bool(x.get("date"))
    if not (ok(d) and ok(k)):
        return _factor("R5", "unknown", "incomplete", items=items)
    # округление до сотых до сравнения и вывода — одинаково с TS (round() в Python — банковское, не годится)
    diff = _num(math.floor(abs(d["value"] - k["value"]) * 100 + 0.5) / 100)
    values = {"declared": _num(d["value"]), "confirmed": _num(k["value"]), "diff": diff, "threshold": T["readinessGap"]}
    return _factor("R5", "hit" if diff >= T["readinessGap"] else "clear", "mismatch" if diff >= T["readinessGap"] else "match",
                   values, items)


def factor_r6(c: Obj, as_of: date) -> Obj:
    if c.get("stage") == BUILT:
        return _factor("R6", "clear", "built")
    planned = c.get("plannedEnd")
    if planned:
        days = (as_of - date.fromisoformat(planned[:10])).days
        if days >= T["overdueDays"]:
            return _factor("R6", "hit", "overdue", {"plannedEnd": fmt_date(planned), "days": days},
                           [_item(kind="deadline", ref="plannedEnd", label="Заявленный срок сдачи", date=planned)])
    photos = c.get("photos") or []
    idle = c.get("idleDays") or 0
    items = [_item(kind="photo", ref=p.get("src"), label=p.get("note") or p.get("activity"), source=p.get("source"),
                   date=p.get("date"), value=p.get("activity")) for p in photos]
    if idle >= T["idleDays"]:
        # простой подтверждают только датированные снимки без активности или с низкой; в items — вся серия
        dates = [p["date"] for p in photos if p.get("date") and p.get("activity") in T["idleActivities"]]
        if len(dates) >= T["idlePhotosMin"]:
            return _factor("R6", "hit", "idle", {"idleDays": idle, "from": fmt_date(min(dates)), "to": fmt_date(max(dates))}, items)
        return _factor("R6", "unknown", "idle_unconfirmed", {"idleDays": idle, "photos": T["idlePhotosMin"]}, items)
    if not photos:
        return _factor("R6", "unknown", "no_photos")
    return _factor("R6", "clear", "active")


def observations(c: Obj, dev: Optional[Obj]) -> List[Obj]:
    def obs(code: str, state: str, values: Optional[Obj] = None) -> Obj:
        o = OBSERVATIONS[code]
        return {"code": code, "title": o["title"], "state": state, "text": fill(o["texts"][state], values)}

    lst = c.get("inPublishedList", "unknown")
    out = [obs("O1", {"yes": "ok", "no": "flag"}.get(lst, "unknown"))]
    # застройщик из открытого реестра: портфель (объектов, незавершённых) не получен — профиль неизвестен, а не «в порядке»
    if dev is None or dev.get("origin") in NOT_ASSESSED["origins"]:
        out.append(obs("O2", "unknown"))
    else:
        flag = (dev.get("unfinished") or 0) > 0 and (dev.get("objects") or 0) <= 3
        out.append(obs("O2", "flag" if flag else "ok", {"unfinished": dev.get("unfinished"), "objects": dev.get("objects")}))
    idle = c.get("idleDays") or 0
    short = c.get("stage") != BUILT and T["idleShortDays"] <= idle < T["idleDays"]
    out.append(obs("O3", "flag" if short else "ok", {"idleDays": idle, "threshold": T["idleDays"]}))
    return out


# ---------- итог ----------

def _total(factors: List[Obj]) -> Obj:
    score = min(MODEL["maxScore"], sum(f["points"] for f in factors if f["state"] == "hit"))
    zone = zone_for(score, [f["code"] for f in factors if f["state"] == "hit"])
    unknown = [f["code"] for f in factors if f["state"] == "unknown"]
    return {"score": score, "zone": zone, "needsCheck": bool(unknown), "unknown": unknown}


def assessed(c: Obj, ads: List[Obj], appeals: List[Obj]) -> bool:
    """Оценивается ли риск ЖК. ЖК не из открытого реестра — всегда. Карточка из реестра (сведения источника, аналитиком не
    проверены) — только когда по ней есть проверенные сведения: подтверждённая аналитиком привязка публикации, документ
    основания «есть/нет» с источником и датой сверки, проверенное обращение, подтверждённая готовность с источником и датой
    или датированный снимок площадки с источником."""
    if c.get("origin") not in NOT_ASSESSED["origins"]:
        return True
    if any(a.get("complexId") == c["id"] and a.get("status") != "dismissed" and link_confirmed(a) for a in ads):
        return True
    b = c.get("basis") or {}
    if any(_sourced(doc_source(b, d["key"])) for d in MODEL["documents"]):
        return True
    if any(a.get("complexId") == c["id"] and a.get("status") == "verified" for a in appeals):
        return True
    k = (c.get("readiness") or {}).get("confirmed")
    if k and isinstance(k.get("value"), (int, float)) and k.get("source") and k.get("date"):
        return True
    return any(p.get("date") and p.get("source") for p in c.get("photos") or [])


def score_complex(c: Obj, dev: Optional[Obj], ads: List[Obj], appeals: List[Obj], as_of: Any = None) -> Obj:
    """Риск ЖК: шесть факторов §5, балл, зона, «Нужна проверка», наблюдения вне балла. Карточка из открытого реестра без
    проверенных сведений — assessed False, балл 0 и пояснение «риск не оценивается» (факторы — как есть, для справки)."""
    d = _as_date(as_of)
    built = c.get("stage") == BUILT
    linked = _by_date_desc([a for a in ads if a.get("complexId") == c["id"] and a.get("status") != "dismissed"])
    factors = [
        factor_r1(c),
        _ads_factor("R2", built, linked, _money_marker),
        _ads_factor("R3", built, linked, _adv_marker),
        factor_r4(c, appeals),
        factor_r5(c),
        factor_r6(c, d),
    ]
    t = _total(factors)
    if not assessed(c, ads, appeals):
        return {"score": 0, "zone": zone_for(0, []), "needsCheck": t["needsCheck"], "factors": factors,
                "observations": observations(c, dev), "summary": NOT_ASSESSED["summary"], "modelVersion": VERSION,
                "assessed": False}
    s = MODEL["summaries"]
    key = t["zone"] if t["zone"] != "yellow" else ("yellow_high" if t["score"] >= MODEL["zones"]["redFrom"] else "yellow")
    summary = fill(s[key], {"score": t["score"]})
    if t["needsCheck"]:
        summary += " " + fill(s["needs_check"], {"codes": ", ".join(t["unknown"])})
    return {"score": t["score"], "zone": t["zone"], "needsCheck": t["needsCheck"], "factors": factors,
            "observations": observations(c, dev), "summary": summary, "modelVersion": VERSION, "assessed": True}


def score_ad(ad: Obj, c: Optional[Obj]) -> Obj:
    """Оценка одной публикации: основание объекта (R1) + её собственные R2 и R3, те же веса и зоны."""
    s = ad_signals(ad)
    # «введён» учитываем, только если привязка к ЖК — факт, а не гипотеза
    built = bool(c) and c.get("stage") == BUILT and link_confirmed(ad)
    money, adv = _money_marker(s), _adv_marker(s)
    r = MODEL["ad"]["reasons"]
    if built:
        r2, r3 = _factor("R2", "clear", "built"), _factor("R3", "clear", "built")
    else:
        if not money:
            r2 = _factor("R2", "clear", "none", text=r["none"])
        elif terms_rejected(ad):
            r2 = _factor("R2", "clear", "R2_rejected", {"marker": money}, [_ad_item(ad, money)], r["R2_rejected"])
        else:
            r2 = _factor("R2", "hit", "R2_offer", items=[_ad_item(ad, money)], text=r["R2_offer"])
        r3 = (_factor("R3", "hit", "R3_ads", items=[_ad_item(ad, adv)], text=r["R3_ads"]) if adv
              else _factor("R3", "clear", "none", text=r["none"]))
    if built:
        r1 = _factor("R1", "clear", "built")
    elif r2["state"] != "hit" and r3["state"] != "hit":
        r1 = _factor("R1", "clear", "R1_no_sale", text=r["R1_no_sale"])
    elif c is None:
        r1 = _factor("R1", "unknown", "R1_no_complex", text=r["R1_no_complex"])
    elif not link_confirmed(ad):
        r1 = _factor("R1", "unknown", "R1_link_unconfirmed", {"confidence": _pct(ad.get("matchConfidence") or 0)},
                     text=r["R1_link_unconfirmed"])
    else:
        r1 = factor_r1(c)
    factors = [r1, r2, r3]
    t = _total(factors)
    return {"score": t["score"], "zone": t["zone"], "needsCheck": t["needsCheck"], "factors": factors, "signals": s}
