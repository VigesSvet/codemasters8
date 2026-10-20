"""Извлечение условий оплаты из текста объявления и распознанного скриншота(T-21).

Правила и словари, без внешних моделей: якорные слова условий(бронь, депозит, первый взнос, рассрочка, ДДУ, оплата
напрямую застройщику, вход/доходность, цена) в любых формах + числа рядом с ними(суммы с валютой и множителем
"тыс./млн", цена за м², проценты, сроки). Каждое условие - с дословной цитатой из того источника, где найдено
(text или ocr), и уверенностью: якорь с числом рядом - выше, одно слово без чисел - ниже, из OCR - ниже(шум
распознавания), противоречие(два разных взноса) - ниже. Результат детерминирован.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from ..risk_model import T
from .nlp import Spans, fold_homoglyphs, phones, plain

Obj = Dict[str, Any]

# Совпадений(якорей и чисел) на источник: объявление - это десятки условий, а не тысячи; предел держит время анализа
# длинного или мусорного текста(до 20 000 символов) в доле секунды. Берутся первые по тексту - результат детерминирован.
MAX_MATCHES = 200

LABELS = {
    "booking": "Платная бронь", "deposit": "Депозит/задаток", "down_payment": "Первоначальный взнос",
    "installment": "Рассрочка", "ddu": "Договор долевого участия", "direct_payment": "Оплата напрямую застройщику",
    "price": "Цена", "investment": "Инвестиционный вход/доходность",
}
FREE_BOOKING = "Бронь без оплаты"
# какие числа принимает условие: amount - сумма, percent - процент, months - срок
ACCEPTS = {
    "booking": {"amount"}, "deposit": {"amount", "percent"}, "down_payment": {"amount", "percent"},
    "installment": {"percent", "months", "amount"}, "investment": {"amount", "percent"}, "price": {"amount"},
    "ddu": set(), "direct_payment": set(),
}
NO_CONFLICT = {"price"}  # разные цены в одном объявлении - норма(1-, 2-, 3-комнатные), не противоречие

L = r"(?<![^\W\d_])"  # слева не буква: "бронь" в "ПРОДАЖ·БРОНЬ50000₸" находится, "взнос" внутри слова - нет
# (вид, выражение, базовая уверенность, нужно ли число рядом, слабый якорь)
_ANCHORS: List[Tuple[str, str, float, bool, bool]] = [
    ("booking", r"брон(?:ь|и|ью|ей|ям|ях|ировани\w*|ирова\w*|иру\w*)", 0.6, False, False),
    ("booking", r"забронир\w*|брондау\w*", 0.6, False, False),
    ("booking", r"(?:за)?фикс(?:ир\w*|аци\w*|ируем)\s+цен\w*", 0.5, False, True),
    ("deposit", r"депозит\w*|задат\w*|кепілақы\w*", 0.7, False, False),
    ("deposit", r"предоплат\w*", 0.65, False, False),
    ("deposit", r"аванс\w*", 0.6, False, False),
    ("down_payment", r"(?:перв\w*|первоначальн\w*|начальн\w*)\s+взнос\w*|бастапқы\s+жарна\w*", 0.75, False, False),
    ("down_payment", r"взнос\w*|жарна\w*", 0.55, False, False),
    ("down_payment", r"пв(?![^\W\d_])", 0.6, True, False),
    ("installment", r"рассрочк\w*|бөліп\s+төле\w*", 0.65, False, False),
    ("ddu", r"договор\w*\s+долевого\s+участия|үлестік\s+қатысу\w*", 0.9, False, False),
    ("ddu", r"дду(?![^\W\d_])", 0.85, False, False),
    ("ddu", r"долев\w*\s+участи\w*", 0.8, False, False),
    ("ddu", r"дольщик\w*", 0.6, False, False),
    ("direct_payment", r"(?:напрямую\s+застройщику|застройщику\s+напрямую|(?:оплат\w*|плат\w*|деньги)\s+(?:напрямую\s+)?"
                       r"застройщику)", 0.8, False, False),
    ("investment", r"вход\s+от", 0.6, False, False),
    ("investment", r"доходност\w*", 0.6, False, False),
    ("investment", r"инвестиц\w*|инвестир\w*|инвестор\w*", 0.5, False, False),
    ("price", r"цен[аеуы]\w*|стоимост\w*|прайс\w*", 0.5, True, False),
]
# \w* в формах слова - только буквы: "РАССРОЧКА0%" из OCR без пробелов - это рассрочка и 0 %
ANCHORS = [(k, re.compile(L + rx.replace(r"\w", r"[^\W\d_]"), re.I), base, needs, weak) for k, rx, base, needs, weak in _ANCHORS]
# Отрицание перед якорем: "без предоплаты", "нет брони" - это не предложение внести деньги
NEGATION_BEFORE = re.compile(r"\b(?:без|нет|не\s+нужн\w*|не\s+требует\w*)\s+$", re.I)
# Отрицание сразу после якоря(через одно-два слова): "бронь не нужна", "взнос при покупке не нужен", "предоплата не
# требуется", "бронирование не обязательно"
NEGATION_AFTER = re.compile(r"\s*(?:[^\W\d_]+\s+){0,2}?не\s+(?:нуж(?:н|ен)|обязател|требу)", re.I)
NEGATABLE = {"booking", "deposit", "down_payment", "installment"}
# Бесплатная бронь: "бронь бесплатная", "бесплатное бронирование", "без оплаты", "бронь 0 тг", "тегін"
FREE = re.compile(r"бесплатн\w*|без\s+оплат\w*|без\s+предоплат\w*|не\s+требует\w*|(?<![\d.,])0\s?(?:₸|тг|тенге)(?![^\W\d_])"
                  r"|тегін", re.I)

# ---------- числа ----------

_NUM = r"(?P<num>\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?!\d)|\d{1,3}(?:\.\d{3}){2,}(?!\d)|\d+(?:[.,]\d+)?)"
_MULT = r"(?P<mult>тыс(?:яч\w*|\.)?|(?P<mln>млн\.?|миллион\w*)|млрд\.?|миллиард\w*)"
_CUR = r"(?P<cur>₸|тг\.?|тенге|kzt|₽|руб(?:\.|л\w*)?|rub|\$|usd|долл(?:\.|ар\w*)?|у\.\s?е\.?)"
_M2 = r"(?P<m2>\s?/\s?(?:м²|м2|кв\.?\s?м\.?|m²|m2)|\s+за\s+(?:1\s?)?(?:м²|м2|кв\.?\s?м\.?|квадрат\w*|метр\w*))"
_B = r"(?![^\W\d_])"  # справа не буква
# "1 млн 200 тыс. тенге" - одна сумма: после "млн" - остаток в тысячах
_MLN_TAIL = r"(?(mln)(?:\s?(?P<num2>\d{1,3})\s?тыс(?:яч[^\W\d_]*|\.)?" + _B + r")?)"
# множитель("5 млн", "1 млн 200 тыс.") или "к"("500к", "$15к")
_SCALE = r"(?:\s?" + _MULT + _B + _MLN_TAIL + r"|(?P<k>[кk])(?![^\W\d_]))?"
QUANTITIES = [
    # $10 000, ₸500 000, $15к - валюта перед числом
    ("amount", re.compile(r"(?<![\w$₸])(?P<cur>\$|usd|₸)\s?" + _NUM + _SCALE + "(?:" + _M2 + ")?", re.I)),
    # 500 000 ₸, 5 млн тенге, 1,2 млн ₸, 290 000 ₸/м², 500к - число, множитель, валюта, "за м²"
    ("amount", re.compile(r"(?<![\d.,])" + _NUM + _SCALE + r"\s?(?:" + _CUR + r"(?![^\W\d_]))?" + "(?:" + _M2 + ")?", re.I)),
    # 20%, "20-30%"(диапазон - нижняя граница: "от 20%")
    ("percent", re.compile(r"(?<![\d.,])(?:(?P<lo>\d{1,3}(?:[.,]\d+)?)\s?-\s?)?" + _NUM + r"\s?(?:%|процент\w*|пайыз\w*)", re.I)),
    ("percent0", re.compile(L + r"(?:без\s+процент\w*|беспроцентн\w*)", re.I)),
    ("months", re.compile(r"(?<![\d.,])" + _NUM + r"\s?(?:мес(?:яц\w*|\.)?|ай(?:ға)?)" + _B, re.I)),
    ("years", re.compile(r"(?<![\w.,])(?P<num>\d{1,2}(?:[.,]\d)?|один|одного|два|двух|три|тр[её]х|полтора)\s?"
                         r"(?:год\w*|лет|жыл\w*)" + _B, re.I)),
    ("half", re.compile(L + r"полгода", re.I)),
    ("area", re.compile(r"(?<![\d.,])" + _NUM + r"\s?(?:м²|м2|кв\.?\s?м\.?|m²|m2|sq\.?\s?m)" + _B, re.I)),
]
_WORDNUM = {"один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3, "полтора": 1.5}
_MULTS = {"тыс": 1000, "млн": 10 ** 6, "миллион": 10 ** 6, "млрд": 10 ** 9, "миллиард": 10 ** 9}


def _number(raw: str, mult: Optional[str]) -> Optional[Decimal]:
    s = re.sub(r"[ \u00a0\u202f\u2009]", "", raw)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", s):
        s = s.replace(".", "")
    s = s.replace(",", ".")
    if not mult and re.fullmatch(r"\d{1,3}\.\d{3}", s):  # "290.000 ₸" - разделитель тысяч, а не дробь
        s = s.replace(".", "")
    try:
        v = Decimal(s)
    except ArithmeticError:
        return None
    if mult:
        m = mult.lower()
        v *= next((x for key, x in _MULTS.items() if m.startswith(key)), 1)
    return v


def _currency(cur: Optional[str]) -> Optional[str]:
    if not cur:
        return None
    c = cur.lower()
    if c.startswith(("$", "usd", "долл", "у")):
        return "USD"
    if c.startswith(("₽", "руб", "rub")):
        return "RUB"
    return "KZT"


def _clean(v: Decimal) -> Any:
    return int(v) if v == v.to_integral_value() else float(v)


@dataclass
class Qty:
    kind: str  # amount | percent | months | area
    start: int
    end: int
    value: Any = None
    currency: Optional[str] = None
    unit: Optional[str] = None
    used: bool = False


def quantities(text: str, masked: List[Tuple[int, int]]) -> List[Qty]:
    """Суммы, проценты, сроки(и площади - чтобы "64 м²" не стали суммой), без пересечений; телефоны исключены."""
    found: List[Tuple[int, int, str, re.Match]] = []
    mask = Spans(masked)
    for kind, rx in QUANTITIES:
        for m in rx.finditer(text):
            if m.end() > m.start() and not mask.overlaps(m.start(), m.end()):
                found.append((m.start(), -(m.end() - m.start()), kind, m))
    out: List[Qty] = []
    taken_end = -1  # по возрастанию начала: пересечение с уже взятыми ⇔ начало раньше конца последнего
    for start, neg, kind, m in sorted(found, key=lambda x: (x[0], x[1])):
        if start < taken_end:
            continue
        q = _qty(kind, m)
        if q:
            out.append(q)
            taken_end = start - neg
    return out


def _qty(kind: str, m: re.Match) -> Optional[Qty]:
    g = m.groupdict()
    if kind == "area":
        return Qty("area", m.start(), m.end())
    if kind == "percent0":
        return Qty("percent", m.start(), m.end(), 0)
    if kind == "half":
        return Qty("months", m.start(), m.end(), 6)
    if kind == "years":
        n = _WORDNUM.get(g["num"].lower())
        n = Decimal(str(n)) if n is not None else _number(g["num"], None)  # "1,5 года" - 18 мес.
        if n is None or not 0 < n <= 10 or n * 12 != (n * 12).to_integral_value():
            return None
        return Qty("months", m.start(), m.end(), _clean(n * 12))
    v = _number(g["num"], g.get("mult"))
    if v is None:
        return None
    if g.get("num2") and g.get("mln"):
        v += Decimal(g["num2"]) * 1000
    if kind == "percent":
        lo = _number(g["lo"], None) if g.get("lo") else None
        v = lo if lo is not None and lo <= v else v
        return Qty("percent", m.start(), m.end(), _clean(v)) if v <= 100 else None
    if kind == "months":
        return Qty("months", m.start(), m.end(), _clean(v)) if 0 < v <= 120 else None
    # сумма: нужна валюта, множитель или разряды "500 000"; иначе это не деньги("18 этажей", "2028")
    if g.get("k"):
        v *= 1000
    grouped = bool(re.search(r"\d[ \u00a0\u202f\u2009.]\d{3}", g["num"]))
    if not (g.get("cur") or g.get("mult") or g.get("k") or grouped):
        return None
    if v < 1000 and not g.get("mult") and _currency(g.get("cur")) in (None, "KZT"):
        return None  # "5 т.", "20 ₸" - не сумма взноса или цены
    return Qty("amount", m.start(), m.end(), _clean(v), _currency(g.get("cur")) or "KZT", "m2" if g.get("m2") else None)


# ---------- фрагменты ----------

_ABBR = {"кв", "комн", "тыс", "млн", "млрд", "руб", "тг", "мес", "ж", "к", "т", "м", "эт", "р", "долл", "у", "е", "в", "с", "стр",
         "корп", "оф", "тел", "п"}
_NAME_ABBR = {"ул", "пр", "мкр", "просп", "пер", "д", "им", "г"}  # "ул. Утепова", "г. Усть-Каменогорск" - не конец фразы
_CAPITAL = re.compile(r"\s+[A-ZА-ЯЁӘҒҚҢӨҰҮҺІ]")
_BOUND = re.compile(r"[;!?\n·•|]|[.,](?=\s|$)")
_SENT = re.compile(r"[!?\n·•|]|\.(?=\s|$)")


def _bounds(text: str, rx: re.Pattern, keep: Spans) -> List[Tuple[int, int]]:
    """Отрезки текста между разделителями. Разделители внутри чисел и телефонов не режут; точка после сокращения
    ("500 тыс. тг", "IV кв. 2028") режет, только если дальше новая фраза с заглавной("24 мес. Звоните"), а после
    "ул.", "пр.", "г." - никогда."""
    cuts = [0]
    for m in rx.finditer(text):
        i = m.start()
        inside = keep.index(i) is not None
        if text[i] == ".":
            w = re.search(r"([^\W\d_]+)$", text[max(0, i - 40):i])  # сокращения короткие - дальше 40 символов не смотрим
            word = w.group(1).lower() if w else ""
            if word in _NAME_ABBR or ((inside or word in _ABBR) and not _CAPITAL.match(text, i + 1)):
                continue
        elif inside:
            continue
        cuts.append(i + 1)
    cuts.append(len(text))
    return [(a, b) for a, b in zip(cuts, cuts[1:], strict=False) if text[a:b].strip()]


def _strip(text: str, a: int, b: int) -> Tuple[int, int]:
    while a < b and (text[a].isspace() or text[a] in ",;:·•|-"):
        a += 1
    while b > a and (text[b - 1].isspace() or text[b - 1] in ",;:·•|-"):
        b -= 1
    return a, b


def quote_span(text: str, clauses: List[Tuple[int, int]], ci: int, pos: int, max_words: int) -> Tuple[int, int]:
    """Дословный фрагмент вокруг условия: его отрезок; из одного слова("ДДУ") - вместе с соседними; длинный - окно слов."""
    a, b = clauses[ci]
    lo, hi = ci, ci
    while len(text[a:b].split()) < 2 and (lo > 0 or hi < len(clauses) - 1):
        if lo > 0:
            lo -= 1
            a = clauses[lo][0]
        if hi < len(clauses) - 1 and len(text[a:b].split()) < 2:
            hi += 1
            b = clauses[hi][1]
    a, b = _strip(text, a, b)
    # слова ищем только рядом с условием: окна в max_words слов хватает с запасом, а длинный отрезок без разделителей
    # не перебирается целиком для каждого условия
    reach = 48 * max_words
    lo, hi = max(a, pos - reach), min(b, pos + reach)
    spans = [(lo + m.start(), lo + m.end()) for m in re.finditer(r"\S+", text[lo:hi])]
    if spans and lo > a and spans[0][0] == lo and not text[lo - 1].isspace():
        spans.pop(0)  # слово, обрезанное краем окна
    if spans and hi < b and spans[-1][1] == hi and not text[hi].isspace():
        spans.pop()
    if not spans:  # условие внутри "слова" в тысячу символов(склеенный мусор) - короткий фрагмент вокруг него
        return max(a, pos - 40), min(b, pos + 80)
    if len(spans) <= max_words and (lo, hi) == (a, b):
        return a, b
    k = next((i for i, (s, _) in enumerate(spans) if s >= pos), len(spans) - 1)
    lo_w = max(0, k - T["quoteWordsBefore"])
    hi_w = min(len(spans), lo_w + max_words)
    return spans[lo_w][0], spans[hi_w - 1][1]


# ---------- условия ----------

@dataclass
class Occ:
    kind: str
    start: int
    end: int
    base: float
    needs: bool
    weak: bool
    clause: int
    source: str
    values: Obj = field(default_factory=dict)  # amount, currency, unit, percent, months
    free: bool = False
    quote: Tuple[int, int] = (0, 0)
    conf: float = 0.0


def _anchors(text: str, busy: Spans) -> List[Tuple[int, int, str, float, bool, bool]]:
    """Якоря условий вне чисел и телефонов; из пересекающихся - раньше начавшийся, при равенстве - более длинный."""
    found = []
    for kind, rx, base, needs, weak in ANCHORS:
        for m in rx.finditer(text):
            if not busy.overlaps(m.start(), m.end()):
                found.append((m.start(), m.end(), kind, base, needs, weak))
    out: List[Tuple[int, int, str, float, bool, bool]] = []
    for item in sorted(found, key=lambda x: (x[0], -(x[1] - x[0]))):
        if not out or item[0] >= out[-1][1]:
            out.append(item)
    return out


def _locator(segments: List[Tuple[int, int]]):
    """Номер отрезка по позиции(позиция вне отрезков - последний отрезок)."""
    spans = Spans(segments)

    def find(pos: int) -> int:
        i = spans.index(pos)
        return len(segments) - 1 if i is None else i
    return find


def _assign(q: Qty, occs: List[Occ]) -> bool:
    """Число - ближайшему условию в том же отрезке, которое такое число принимает: сначала слева, потом справа."""
    field_name = "amount" if q.kind == "amount" else q.kind
    ok = [o for o in occs if field_name in ACCEPTS[o.kind] and field_name not in o.values
          and not (q.unit == "m2" and o.kind != "price")]
    left = [o for o in ok if o.end <= q.start]
    right = [o for o in ok if o.start >= q.end]
    target = left[-1] if left else (right[0] if right else None)
    if target is None:
        return False
    if q.kind == "amount":
        target.values.update(amount=q.value, currency=q.currency, unit=q.unit)
    else:
        target.values[field_name] = q.value
    q.used = True
    return True


def occurrences(source_text: Optional[str], source: str) -> List[Occ]:
    text = plain(source_text)
    if source == "ocr":  # "12 mec" → "12 мес": длина та же, цитата берётся из исходного текста по позициям
        text = fold_homoglyphs(text)
    if not text.strip():
        return []
    masked = [(a, b) for _, _, a, b in phones(text)]
    qs = quantities(text, masked)
    busy = Spans(masked + [(q.start, q.end) for q in qs])
    clauses = _bounds(text, _BOUND, busy)
    sentences = _bounds(text, _SENT, busy)
    clause_of, sentence_of = _locator(clauses), _locator(sentences)
    anchors, qs = _anchors(text, busy), [q for q in qs if q.kind != "area"]
    # предел совпадений - общей границей в тексте: якоря и числа берутся из одного и того же начала текста
    cut = min([len(text)] + [x[0] for x in anchors[MAX_MATCHES:MAX_MATCHES + 1]]
              + [q.start for q in qs[MAX_MATCHES:MAX_MATCHES + 1]])
    anchors, qs = [x for x in anchors if x[0] < cut], [q for q in qs if q.start < cut]
    occs: List[Occ] = []
    for start, end, kind, base, needs, weak in anchors:
        ci = clause_of(start)
        free = bool(NEGATION_BEFORE.search(text[max(0, start - 16):start])) or (
            kind in NEGATABLE and bool(NEGATION_AFTER.match(text, end, clauses[ci][1])))
        if free and kind != "booking":
            continue  # "без предоплаты", "без первоначального взноса", "взнос не нужен" - условия нет
        occs.append(Occ(kind, start, end, base, needs, weak, ci, source, free=free))
    # числа → условиям того же отрезка; отрезок без условий отдаёт числа предыдущему("Бронь квартиры, 200 000 тг")
    by_clause: Dict[int, List[Occ]] = {}
    for o in occs:
        by_clause.setdefault(o.clause, []).append(o)
    for q in qs:
        ci = clause_of(q.start)
        here = by_clause.get(ci, [])
        if not _assign(q, here) and not here and ci > 0:
            prev = by_clause.get(ci - 1)
            if prev and sentence_of(clauses[ci - 1][0]) == sentence_of(q.start):
                _assign(q, prev)
    # оставшиеся суммы - цена(объявление о продаже: "41 500 000 ₸", "от 290 000 ₸/м²")
    for q in qs:
        if q.kind == "amount" and not q.used and q.currency:
            occs.append(Occ("price", q.start, q.end, 0.6, False, False, clause_of(q.start), source,
                            {"amount": q.value, "currency": q.currency, "unit": q.unit}))
    # "бронь бесплатная": отрицание после якоря в том же отрезке
    free_clause: Dict[int, bool] = {}
    for o in occs:
        if o.kind == "booking" and not o.free and not o.values.get("amount"):
            if o.clause not in free_clause:
                a, b = clauses[o.clause]
                free_clause[o.clause] = bool(FREE.search(text, a, b))
            o.free = free_clause[o.clause]
    # слабый якорь "фиксируем цену" - только если в предложении нет другого денежного условия
    money = Counter(sentence_of(o.start) for o in occs if o.kind != "price")
    occs = [o for o in occs if not (o.weak and money[sentence_of(o.start)] > (o.kind != "price"))
            and not (o.needs and not o.values)]
    for o in occs:
        conf = o.base + (0.25 if any(o.values.get(k) is not None for k in ("amount", "percent", "months")) else 0)
        before = text[max(clauses[o.clause][0], o.start - 200):o.start]
        if o.kind == "price" and (o.values.get("unit") == "m2" or re.search(r"цен|стоимост|от\s*$", before, re.I)):
            conf += 0.25
        if o.free:
            conf -= 0.15
        if source == "ocr":
            conf -= 0.1
        o.conf = conf
        o.quote = quote_span(text, clauses, o.clause, o.start, T["quoteMaxWords"])
    return sorted(occs, key=lambda o: o.start)


def _fields(o: Occ) -> Obj:
    v = {k: o.values.get(k) for k in ("amount", "currency", "unit", "percent", "months") if o.values.get(k) is not None}
    if o.kind == "booking":
        v["free"] = o.free
    return v


def _subset(a: Obj, b: Obj) -> bool:
    """Все значения a есть в b и совпадают."""
    return all(k in b and b[k] == v for k, v in a.items())


def _conflict(a: Obj, b: Obj) -> bool:
    return any(k in b and b[k] != v for k, v in a.items() if k != "currency")


def extract(text: Optional[str], ocr: Optional[str] = None) -> List[Obj]:
    """Условия оплаты: текст, затем скриншот; повтор в OCR того же условия не дублирует его, а подтверждает(+0.05)."""
    sources = {"text": text or "", "ocr": ocr or ""}  # plain() не меняет длину - позиции те же, цитата дословная
    kept: List[Occ] = []
    fields: List[Obj] = []  # значения условий kept(по тем же индексам)
    for o in occurrences(text, "text") + occurrences(ocr, "ocr"):
        f = _fields(o)
        j = next((i for i, k in enumerate(kept) if k.kind == o.kind and (_subset(f, fields[i]) or _subset(fields[i], f))),
                 None)
        if j is None:
            kept.append(o)
            fields.append(f)
            continue
        twin = kept[j]
        richer = o if len(f) > len(fields[j]) else twin
        if richer is o:
            o.conf = max(o.conf, twin.conf)
            kept[j], fields[j] = o, f
        if o.source != twin.source:
            richer.conf += 0.05  # то же условие и в тексте, и на скриншоте - подтверждение
    # противоречие: два разных значения одного условия(кроме цены) - уверенность обоих ниже
    for i, o in enumerate(kept):
        if o.kind not in NO_CONFLICT and any(j != i and x.kind == o.kind and _conflict(fields[i], fields[j])
                                             for j, x in enumerate(kept)):
            o.conf -= 0.2
    out = []
    for o in kept:
        conf = round(min(0.97, max(0.1, o.conf)), 2)
        a, b = o.quote
        t: Obj = {"kind": o.kind, "label": FREE_BOOKING if o.free else LABELS[o.kind]}
        t.update({k: v for k, v in o.values.items() if v is not None})
        t.update({"quote": sources[o.source][a:b], "from": o.source, "confidence": conf, "low": conf < T["lowConfidence"],
                  "money": o.kind != "price" and not o.free})
        out.append(t)
    return out
