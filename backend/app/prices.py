"""Аномальная цена: цена за м² публикации против медианы города - контекст для аналитика, не балл риска.

Цена за м²(parse) - только из текста публикации и OCR картинок; речь роликов не берётся(Whisper искажает числа):
- прямо названная: "от 450 000 ₸/м²", "470 000 тенге за квадратный метр", "Цена за 1 м² - от 450 000 тенге";
- цена квартиры и площадь: у Krisha - поле price и площадь из первой строки("2-комнатная квартира · 60 м²"), в
  тексте соцсетей - одна цена квартиры("Стоимость - от 22 554 000 ₸", "цена 25 млн") и одна площадь("50,12 м²");
  диапазоны("от 39 до 111 м²"), суммы займа, взноса, платежа и несколько разных цен или площадей - не считаются.
Кладовые, паркинг и коммерческие помещения не сравниваются с квартирами. Правдоподобно - 100 000 … 3 000 000 ₸/м².

Медиана - по всем публикациям с ценой за м²(MIN_GROUP и больше - по числу комнат, иначе общая). Ниже рынка -
дешевле THRESHOLD × медианы: "цена ниже рынка на X%". Отметка "100% оплата"("скидка при 100% оплате", "100% төлем",
"только за наличный расчёт", "(наличный расчёт)"; "наличный расчёт или ипотека" - не отметка)
- рядом, с цитатой: низкая цена за полную оплату на стадии строительства - повод проверить основание продажи, но не
нарушение сама по себе.
"""
import re
import statistics
from typing import Any
from collections.abc import Iterable

from .ai.nlp import plain
from .sellers import quote_at, segments

Obj = dict[str, Any]

THRESHOLD = 0.7
MIN_GROUP = 5
PER_M2_RANGE = (100_000, 3_000_000)
NOTE = ("Цена ниже медианы города — контекст для проверки, а не вывод о нарушении: в балл риска не входит. Цена «от» — "
        "стартовая цена продавца, площадь и цена взяты из текста публикации.")

_NUM = r"(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)"
_MULT = r"(?:\s*(млн|миллион\w*|тыс\w*|т\.?\s?р\.?))?"
_CUR = r"(?:\s*(?:₸|тг\.?|тенге|теңге|kzt))?"
_M2 = r"(?:м²|м2|кв\.?\s?м\.?|квадратн\w*\s+метр\w*|шаршы\s+метр\w*)"
# "450 000 ₸/м²", "470 000 тенге за квадратный метр", "130 000 ₸ за 1 м²"
_PER_A = re.compile(_NUM + _MULT + _CUR + r"\s*(?:/|за|за\s+1|per)\s*(?:1\s*)?" + _M2, re.I)
# "Цена за 1 м² - от 450 000 тенге", "стоимость квадратного метра от 400 тыс"
_PER_B = re.compile(r"(?:цена|стоимость)\s+(?:за\s+)?(?:1\s+)?(?:м²|м2|кв\.?\s?м\.?|квадратн\w*\s+метр\w*)[^\d\n]{0,20}?"
                    + _NUM + _MULT, re.I)
# "Цена от 450 000 тенге" без "за м²": сумма меньше цены любой квартиры - это цена за м²(method implied)
_IMPLIED = re.compile(r"(?:цена|стоимость)[^\d\n]{0,15}?" + _NUM + _MULT + r"\s*(?:₸|тг\.?|тенге|теңге)", re.I)
IMPLIED_RANGE = (200_000, 1_500_000)
_AREA = re.compile(r"(?<![\d.,])(\d{2,3}(?:[.,]\d{1,2})?)\s*" + _M2, re.I)
_TOTAL = re.compile(r"(?:цена|стоимость|стоит|💰|от)[^\d\n]{0,25}?" + _NUM + _MULT + r"\s*(₸|тг\.?|тенге|теңге)?", re.I)
_NOT_PRICE = re.compile(r"взнос|займ|кредит|платеж|платёж|ипотек|рассрочк|бронь|брон\w|есть\s*$|скидк|выгод|кэшбэк|"
                        r"депозит|доход|аренд", re.I)
# не квартиры: о кладовых, машино-местах, коммерции - весь пост; "паркинг" - только рядом с ценой(в постах о
# квартирах "подземный паркинг" - среди преимуществ)
_NONRES_POST = re.compile(r"кладов|сақтау\s+орн|қойма|машино-?мест|коммерческ\w+\s+помещ|коммерциял|офисн\w+\s+помещ|"
                          r"нежил", re.I)
_NONRES = re.compile(r"паркинг|автотұрақ|" + _NONRES_POST.pattern, re.I)
_ROOMS = re.compile(r"(?<!\d)(\d)\s*-?\s*(?:х\s*)?комн|(одно|двух|трех|трёх|четырех|четырёх)комн|(студи)", re.I)
_ROOM_WORDS = {"одно": 1, "двух": 2, "трех": 3, "трёх": 3, "четырех": 4, "четырёх": 4}
_FULL = re.compile(r"100\s?%[\s-]*(?:предоплат|оплат|төлем)\w*|(?:полн\w+|единовременн\w+)\s+оплат\w*|"
                   r"скидк\w*[^.\n]{0,40}?(?:100\s?%|полн\w+\s+оплат)|(?:только|при)\s+(?:за\s+)?наличн\w+\s+рас+ч[её]т\w*|"
                   r"\(наличн\w+\s+рас+ч[её]т\w*\)", re.I)
_FROM = re.compile(r"\bот(?![^\W\d_])", re.I)  # цена "от": отдельное слово("от450" - тоже), а не часть "работ", "отдел"


def to_number(num: str, mult: str | None = None) -> float | None:
    """'39 999 999' → 39999999; '23,4', 'млн' → 23 400 000; '400', 'тыс' → 400 000."""
    try:
        v = float(re.sub(r"[  ]", "", num).replace(",", "."))
    except ValueError:
        return None
    m = (mult or "").lower()
    if m.startswith(("млн", "миллион")):
        v *= 1_000_000
    elif m.startswith("т"):  # "тыс", "тысяч", "т.р."
        v *= 1_000
    return v


def _has_from(s: str, a: int, b: int) -> bool:
    """Слово "от" начинается в s[a:b]; границы слова - по всему тексту: срез "работ цена" с "от" в начале - не цена "от"."""
    m = _FROM.search(s, a)
    return m is not None and m.start() < b


def rooms_of(text: str) -> int | None:
    """Число комнат: "2-комнатная", "двухкомнатная", "студия" → 0; 4 и больше - 4."""
    m = _ROOMS.search(text or "")
    if not m:
        return None
    if m.group(1):
        return min(int(m.group(1)), 4)
    if m.group(2):
        return _ROOM_WORDS[m.group(2).lower()]
    return 0


def _ok(v: float | None) -> bool:
    return v is not None and PER_M2_RANGE[0] <= v <= PER_M2_RANGE[1]


def _near(s: str, a: int, b: int) -> str:
    """Контекст находки: 160 символов перед ней и 40 после - "кладовые / паркинг" рядом с ценой, а не во всём посте."""
    return s[max(0, a - 160):b + 40]


def _explicit(src: str, s: str) -> Obj | None:
    for rx in (_PER_A, _PER_B, _IMPLIED):
        for m in rx.finditer(s):
            v = to_number(m.group(1), m.group(2))
            if rx is _IMPLIED and (v is None or not IMPLIED_RANGE[0] <= v <= IMPLIED_RANGE[1]
                                   or re.match(r"\s*(?:/|за)\s*(?:1\s*)?(?:м|кв)", s[m.end():m.end() + 10], re.I)):
                continue
            if _ok(v) and not _NONRES.search(_near(s, m.start(), m.end())):
                return {"perM2": round(v), "method": "implied" if rx is _IMPLIED else "explicit", "source": src,
                        "quote": quote_at(s, m.start(), m.end()),
                        "from": _has_from(s, max(0, m.start() - 12), min(m.end(), m.start() + 25))}
    return None


def _areas(s: str) -> list[tuple[float, int, int]]:
    out = []
    for m in _AREA.finditer(s):
        pre, post = s[max(0, m.start() - 25):m.start()].lower(), s[m.end():m.end() + 16].lower()
        # текст прошёл plain(): тире любого вида здесь - "-"
        if re.search(r"(?:от|до|\d)\s*$", pre) or re.match(r"\s*(?:-|до)\s*\d+(?:[.,]\d+)?\s*(?:м²|м2|кв)", post) or re.search(
                r"террас|кухн|балкон|лодж|жил\.?\s*площ|санузел|проект\w*\s*-?\s*$|общая площадь проекта", pre):
            continue
        out.append((float(m.group(1).replace(",", ".")), m.start(), m.end()))
    return out


def _totals(s: str) -> list[tuple[float, int, int]]:
    out = []
    for m in _TOTAL.finditer(s):
        v = to_number(m.group(1), m.group(2))
        if v is None or not (3_000_000 <= v <= 1_000_000_000) or (not m.group(3) and not m.group(2)):
            continue
        ctx = s[max(0, m.start() - 40):m.start(1)]
        if _NOT_PRICE.search(ctx) or re.search(r"до\s*$", s[max(0, m.start(1) - 4):m.start(1)].lower()):
            continue
        out.append((v, m.start(), m.end()))
    return out


def parse(ad: Obj) -> Obj | None:
    """Цена за м² публикации или None. Результат: {perM2, method: explicit | total_area, total?, area?, rooms, source,
    quote, from, fullPayment?: {quote, source}}."""
    segs = [(src, s) for src, s in segments(ad.get("text"), ad.get("ocr")) if src != "speech"]
    text = "\n".join(s for _, s in segs)
    full = None
    for src, s in segs:  # речь роликов не берётся: "за наличный расчёт и ипотеку" Whisper путает с условием
        m = _FULL.search(s)
        if m:
            full = {"quote": quote_at(s, m.start(), m.end()), "source": src}
            break
    head = plain(ad.get("text") or "").split("\n", 1)[0]
    rooms = rooms_of(head) if ad.get("platform") == "krisha" else rooms_of(text)
    out = None
    if ad.get("platform") == "krisha" and ad.get("price"):
        pm = re.search(_NUM + _MULT, ad["price"])
        am = re.search(r"·\s*(\d+(?:[.,]\d+)?)\s*м²", head)
        if pm and am and not _NONRES.search(head):
            total, area = to_number(pm.group(1), pm.group(2)), float(am.group(1).replace(",", "."))
            if total and area and _ok(total / area):
                out = {"perM2": round(total / area), "method": "total_area", "total": round(total), "area": area,
                       "source": "price", "quote": f"{ad['price']} · {am.group(1)} м²",
                       "from": bool(_FROM.match(ad["price"].strip()))}
    if out is None and not _NONRES_POST.search(text):
        for src, s in segs:
            out = _explicit(src, s)
            if out:
                break
        if out is None and segs:
            src, s = segs[0]  # цена и площадь - из одного источника: текст публикации или OCR
            areas, totals = _areas(s), _totals(s)
            if len({a for a, _, _ in areas}) == 1 and len({t for t, _, _ in totals}) == 1:
                (area, a0, a1), (total, t0, t1) = areas[0], totals[0]
                if _ok(total / area) and not _NONRES.search(_near(s, a0, a1) + _near(s, t0, t1)):
                    lo, hi = min(a0, t0), max(a1, t1)
                    q = quote_at(s, lo, hi, 30) if hi - lo < 200 else f"{quote_at(s, t0, t1, 20)} … {quote_at(s, a0, a1, 20)}"
                    out = {"perM2": round(total / area), "method": "total_area", "total": round(total), "area": area,
                           "source": src, "quote": q, "from": _has_from(s, max(0, t0 - 3), t1)}
    if out is None:
        return {"perM2": None, "fullPayment": full, "rooms": rooms} if full else None
    return {**out, "rooms": rooms, "fullPayment": full}


def _median(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return statistics.median(xs) if xs else None


def analyze(ads: Iterable[Obj]) -> Obj:
    """Цены всех публикаций, медианы(общая и по числу комнат) и отметка "ниже рынка" у каждой."""
    rows = []
    for a in ads:
        p = parse(a)
        if p:
            rows.append({"adId": a["id"], "platform": a["platform"], "account": a["account"], "date": a.get("date"),
                         "url": a.get("url"), "complexId": a.get("complexId"), "complexName": a.get("complexName"), **p})
    priced = [r for r in rows if r["perM2"]]
    overall = _median(r["perM2"] for r in priced)
    by_rooms: dict[str, Obj] = {}
    for k in sorted({r["rooms"] for r in priced if r["rooms"] is not None}):
        xs = [r["perM2"] for r in priced if r["rooms"] == k]
        by_rooms[str(k)] = {"median": round(_median(xs)), "n": len(xs), "used": len(xs) >= MIN_GROUP}
    for r in rows:
        if not r["perM2"] or overall is None:
            r.update(median=None, medianBasis=None, belowPct=None, anomaly=False)
            continue
        g = by_rooms.get(str(r["rooms"])) if r["rooms"] is not None else None
        med, basis = (g["median"], "rooms") if g and g["used"] else (overall, "city")
        below = round((1 - r["perM2"] / med) * 100)
        r.update(median=round(med), medianBasis=basis, belowPct=below, anomaly=r["perM2"] < THRESHOLD * med)
    return {"rows": rows, "median": round(overall) if overall else None, "n": len(priced), "byRooms": by_rooms}


def for_ad(a: Obj, ad_id: str) -> Obj | None:
    r = next((r for r in a["rows"] if r["adId"] == ad_id), None)
    return {**r, "threshold": THRESHOLD, "cityMedian": a["median"], "n": a["n"]} if r else None


def report(a: Obj, include_all: bool = False) -> Obj:
    rows = a["rows"]
    items = sorted((r for r in rows if include_all or r["anomaly"] or (r["fullPayment"] and r["perM2"])),
                   key=lambda r: (not r["anomaly"], -(r["belowPct"] if r["belowPct"] is not None else -999)))
    return {"items": items, "median": a["median"], "n": a["n"], "byRooms": a["byRooms"], "threshold": THRESHOLD,
            "minGroup": MIN_GROUP, "anomalies": sum(r["anomaly"] for r in rows),
            "fullPayment": [{k: r[k] for k in ("adId", "platform", "account", "date", "fullPayment", "perM2", "anomaly")}
                            for r in rows if r["fullPayment"]],
            "note": NOTE}
