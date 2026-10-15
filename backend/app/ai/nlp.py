"""Текст объявлений: нормализация, транслитерация KZ/RU/Latin в общий латинский скелет, токены с позициями в исходном
тексте (для дословных цитат), сходство по символьным триграммам, телефоны Казахстана.

Скелет — грубая фонетическая форма слова: «Алтай Резиденс» и «Altai Residence» → «altai residens», «Шығыс Тауэр» и
«Shygys Tower» → «shigis tauer», «Nord Hils» и «Nord Hills» → «nord hils». По скелету сравниваются названия ЖК, улицы,
районы и аккаунты; цитаты берутся из исходного текста по позициям токенов.
"""
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, Iterable, List, Optional, Tuple

_SPACES = dict.fromkeys(map(ord, "\u00a0\u202f\u2007\u2009\u200a\u2002\u2003\u2004\u2005\u2006\u2008\u205f\u3000"), " ")
_QUOTES = dict.fromkeys(map(ord, "«»\"'“”„‟‘’‚`´"), None)
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def plain(text: Optional[str]) -> str:
    """Неразрывные и узкие пробелы → пробел, типографские тире → '-'. Длина строки не меняется — позиции совпадают
    с исходным текстом (кавычки здесь не удаляются)."""
    return (text or "").translate(_SPACES).translate(_DASHES)


def norm(text: Optional[str]) -> str:
    """Нормализованная форма: нижний регистр, ё→е, пробелы и тире как в plain, без кавычек, пробелы схлопнуты."""
    t = plain(text).translate(_QUOTES).lower().replace("ё", "е")
    return re.sub(r"\s+", " ", t).strip()


class Spans:
    """Непересекающиеся отрезки текста [a, b): пересечение с отрезком и попадание позиции — за O(log n), чтобы длинный
    текст с тысячами чисел не анализировался квадратично."""

    def __init__(self, spans: Iterable[Tuple[int, int]]):
        self.items = sorted(spans)
        self.starts = [a for a, _ in self.items]

    def overlaps(self, a: int, b: int) -> bool:
        i = bisect_left(self.starts, b) - 1
        return i >= 0 and self.items[i][1] > a

    def index(self, pos: int) -> Optional[int]:
        """Номер отрезка, в который попадает позиция, или None."""
        i = bisect_right(self.starts, pos) - 1
        return i if i >= 0 and pos < self.items[i][1] else None


# ---------- транслитерация ----------

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    # казахские буквы
    "ә": "a", "ғ": "g", "қ": "k", "ң": "n", "ө": "o", "ұ": "u", "ү": "u", "һ": "h", "і": "i",
}
# латиница с диакритикой (казахская латиница, турецкая и др.)
_LAT = {"ä": "a", "á": "a", "à": "a", "ö": "o", "ó": "o", "ü": "u", "ú": "u", "ū": "u", "ğ": "g", "ı": "i", "í": "i",
        "ş": "sh", "ç": "ch", "ñ": "n", "é": "e", "è": "e", "ý": "i"}
# Правила для слов, написанных латиницей (английское и казахское латинское написание → как звучит по-русски)
_LATIN_RULES = [(re.compile(p), r) for p, r in (
    (r"ph", "f"), (r"ck", "k"), (r"qu", "kv"), (r"q", "k"), (r"ow", "au"), (r"ou", "au"), (r"ee|ea", "i"), (r"oo", "u"),
    (r"ine$", "ain"), (r"w", "v"), (r"x", "ks"), (r"j", "zh"), (r"c(?=[eiy])", "s"), (r"c(?!h)", "k"), (r"th", "t"),
)]
# Общие правила скелета для обеих письменностей
_COMMON_RULES = [(re.compile(p), r) for p, r in (
    (r"kh", "h"), (r"ts", "s"), (r"y", "i"), (r"z", "s"), (r"(.)\1+", r"\1"), (r"(?<=\w{4})e$", ""),
)]
_CYRILLIC = re.compile(r"[а-яёәғқңөұүһі]")


@lru_cache(maxsize=65536)
def skeleton(word: str) -> str:
    """Латинский скелет одного слова (на входе — нормализованное слово, см. norm)."""
    w = word.lower().replace("ё", "е")
    latin = not _CYRILLIC.search(w)
    w = "".join(_CYR.get(ch, _LAT.get(ch, ch)) for ch in w)
    if latin:
        for rx, rep in _LATIN_RULES:
            w = rx.sub(rep, w)
    for rx, rep in _COMMON_RULES:
        w = rx.sub(rep, w)
    return w


def skeleton_text(text: str) -> str:
    return " ".join(skeleton(t) for t in words(text))


# ---------- токены ----------

_WORD = re.compile(r"[^\W_]+")
# «ЖК», «ж.к.», «жилой комплекс», «тұрғын үй кешені» — не часть названия
STOP = frozenset({"жк", "мжк", "жилой", "жилом", "жилого", "комплекс", "комплекса", "комплексе", "кешені", "кешен", "тұрғын",
                  "zhk", "jk"})


@dataclass(frozen=True)
class Tok:
    norm: str  # нижний регистр, ё→е
    sk: str  # скелет
    start: int  # позиции в исходной строке
    end: int
    cap: bool = False  # в тексте с заглавной буквы (имя собственное или капс)


def words(text: Optional[str]) -> List[str]:
    return [m.group().lower().replace("ё", "е") for m in _WORD.finditer(plain(text))]


def tokens(text: Optional[str], drop_stop: bool = False) -> List[Tok]:
    out: List[Tok] = []
    raw = list(_WORD.finditer(plain(text)))
    for i, m in enumerate(raw):
        n = m.group().lower().replace("ё", "е")
        if drop_stop and (n in STOP or _is_zh_k(raw, i)):
            continue
        out.append(Tok(n, skeleton(n), m.start(), m.end(), m.group()[:1].isupper()))
    return out


# Падежные окончания русских прилагательных и существительных: название ЖК в косвенном падеже («в Тихом береге»,
# «Тихого берега», «в Солнечном квартале») сводится к основе «тих берег», «солнечн квартал»
_ENDINGS = sorted({"ого", "его", "ому", "ему", "ыми", "ими", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ую", "юю", "ом", "ем",
                   "ым", "им", "ых", "их", "ей", "ам", "ям", "ах", "ях", "ов", "ев", "а", "я", "е", "у", "ю", "ы", "и", "о"},
                  key=lambda e: (-len(e), e))
_RUSSIAN = re.compile(r"[а-яё]+")


@lru_cache(maxsize=65536)
def stem(word: str) -> str:
    """Основа русского слова без падежного окончания (не короче трёх букв). Латиница, казахские слова и числа — как есть."""
    if not _RUSSIAN.fullmatch(word):
        return word
    end = next((e for e in _ENDINGS if word.endswith(e) and len(word) - len(e) >= 3), "")
    return word[:len(word) - len(end)]


# ---------- OCR: латинские буквы-двойники ----------

# Смешанная модель rus+eng читает кириллическое слово целиком латиницей из похожих букв: «мес» → «mec», «ВСЕ» → «BCE»,
# «тг» → «Tr». Такие слова рядом с кириллицей или числом на той же строке возвращаются в кириллицу; настоящие латинские
# названия («Nord Hills», «Green Line») содержат и другие буквы и не трогаются. Длина строки не меняется — позиции те же.
_LOOK, _LOOK_CYR = "aceopxykmtbhrABCEHKMOPTXY", "асеорхукмтьнгАВСЕНКМОРТХУ"
_FOLD = str.maketrans(_LOOK, _LOOK_CYR)
_LOOK_WORD = re.compile(f"[{_LOOK}]+")
_MIXED = re.compile(f"[{_LOOK}а-яёА-ЯЁ]+")
_TOKEN = re.compile(r"[^\W\d_]+|\d+")


def fold_homoglyphs(text: str) -> str:
    """Латинские двойники кириллических букв в распознанном тексте → кириллица (только целые слова из двойников рядом
    с кириллицей или числом, и двойники внутри кириллического слова: «Бpонь» → «Бронь»)."""
    toks = list(_TOKEN.finditer(text))
    out = []
    last = 0
    for i, m in enumerate(toks):
        w = m.group()
        if _LOOK_WORD.fullmatch(w):
            near = [toks[j].group() for j, a, b in ((i - 1, i - 1, i), (i + 1, i, i + 1))
                    if 0 <= j < len(toks) and "\n" not in text[toks[a].end():toks[b].start()]]
            fold = any(n.isdigit() or _CYRILLIC.search(n.lower()) for n in near)
        else:
            fold = bool(_CYRILLIC.search(w.lower())) and bool(_MIXED.fullmatch(w)) and w != w.translate(_FOLD)
        if fold:
            out += [text[last:m.start()], w.translate(_FOLD)]
            last = m.end()
    return "".join(out) + text[last:]


def _is_zh_k(raw, i: int) -> bool:
    """«ж.к.» разбивается на токены «ж» и «к»."""
    n = raw[i].group().lower()
    nxt = raw[i + 1].group().lower() if i + 1 < len(raw) else ""
    prv = raw[i - 1].group().lower() if i > 0 else ""
    return (n == "ж" and nxt == "к") or (n == "к" and prv == "ж")


# ---------- сходство ----------

@lru_cache(maxsize=65536)
def trigrams(s: str) -> FrozenSet[str]:
    s = f" {s} "
    return frozenset(s[i:i + 3] for i in range(len(s) - 2))


def jaccard(a: str, b: str) -> float:
    """Сходство Жаккара по символьным триграммам (с границами слова)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    x, y = trigrams(a), trigrams(b)
    return len(x & y) / len(x | y)


# ---------- телефоны ----------

# Разделители ограничены по длине: с «*» длинная серия пробелов/дефисов давала квадратичный перебор (секунды на 20 КБ)
_PHONE = re.compile(r"(?<![\d+])(\+\s?7|8|7)?[\s\-()]{0,4}((?:\d[\s\-()]{0,4}){9}\d)(?!\d)")


def phones(text: Optional[str]) -> List[Tuple[str, str, int, int]]:
    """Казахстанские номера: (как в тексте, 10 цифр без кода страны, начало, конец). Номер начинается с 7 (7xx — мобильные,
    7xxx — городские коды); без префикса +7/8 первая группа цифр должна быть не короче трёх — иначе это цена «7 200 000 000»."""
    out = []
    t = plain(text)
    for m in _PHONE.finditer(t):
        body = re.sub(r"\D", "", m.group(2))
        if not body.startswith("7"):
            continue
        if not m.group(1) and len(re.match(r"\d+", m.group(2)).group()) < 3:
            continue
        out.append((t[m.start():m.end()].strip(), body, m.start(), m.end()))
    return out


def phone_digits(phone: str) -> str:
    """'+7 7232 00-00-02' → '7232000002' (10 цифр без кода страны)."""
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else d
