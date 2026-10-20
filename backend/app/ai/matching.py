"""Сопоставление публикации с ЖК(T-22): гипотезы с уверенностью, причинами и фрагментом-основанием.

Признаки: название(точное, в другом падеже, по транслитерации/с опечаткой - сходство триграмм скелетов; на скриншоте -
и с латинскими буквами-двойниками), адрес("ул./пр./на" + улица + дом - сильный признак, улица и дом без "ул./пр./на" -
средний, только улица - слабый; число с единицей - "9 этаж", "52 м²", "18 лет" - не номер дома), аккаунт(официальный
аккаунт или сайт застройщика; название ЖК в имени аккаунта), телефон застройщика, история аккаунта(его подтверждённые
публикации по этому ЖК), район(слабый).
Уверенность - логистическая комбинация весов признаков; подборка нескольких ЖК в одном тексте снижает уверенность
каждой гипотезы. Привязка по гипотезе - не факт: фактом её делает подтверждение аналитика.
"""
import math
import re
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..risk_model import BUILT, T
from .nlp import Tok, fold_homoglyphs, jaccard, phone_digits, phones, plain, skeleton, stem, tokens, trigrams

Obj = dict[str, Any]

# Вклад признаков в логит уверенности; BIAS - сдвиг: одно точное название ≈ 0.8, один адрес "ул./пр./на + улица + дом"
# ≈ 0.71, улица и дом без "ул./пр./на" ≈ 0.48(низкая: "в Казахстане 66 …" - не обязательно адрес), один аккаунт или
# телефон застройщика с одним строящимся ЖК ≈ 0.48(низкая), один район ≈ 0.23(не показывается)
W = {"name": 3.0, "address": 2.5, "address_bare": 1.5, "street": 0.8, "account": 1.5, "account_name": 2.0, "phone": 1.5,
     "history": 1.2, "history_more": 1.6, "district": 0.4}
BIAS = 1.6
TRANSLIT = 0.95  # совпадение по скелету - почти как точное название
DECLINED = 0.95  # то же название в другом падеже("в Тихом береге")
FUZZY_MIN = 0.5  # минимальное сходство триграмм для нечёткого совпадения названия
CROWD_MIN = 0.6  # сходство названия, с которого ЖК считается "упомянутым"(для подборок)
OCR_FACTOR = 0.9  # признак со скриншота - с поправкой на шум распознавания

_GENERIC_WORDS = ("парк park резиденс residence ривер river тауэр tower квартал kvartal хиллс hills сити city берег плаза "
                  "plaza лайн line хаус house таун town гарден garden сквер square эко eco люкс lux комфорт comfort премиум "
                  "premium дом home вилладж village гранд grand новый жилой residential")
GENERIC = {skeleton(w) for w in _GENERIC_WORDS.split()}

_ADDR = re.compile(r"^\s*(?:ул\.|улица|пр-т|пр\.|просп\.|проспект|мкр\.?|микрорайон|пер\.|переулок|б-р|бульвар)?\s*"
                   r"(?P<street>[^,]+?)\s*,?\s*(?:д\.\s*)?(?P<house>\d+[^\W\d_]?(?:\s*/\s*\d+)?)?\s*$", re.I)
_ADDR_PREFIX = {"ул", "улица", "улице", "улицы", "пр", "проспект", "проспекте", "проспекта", "просп", "мкр", "микрорайон",
                "пер", "переулок", "на", "по", "бульвар", "адрес", "адресу"}
_HOUSE_SKIP = {"д", "дом", "здание", "корпус", "к"}
# Число сразу после улицы - не номер дома, если за ним единица, срок или порядковое "-й этаж": "Абая, 9 этаж",
# "Сатпаева, 52 м²", "Утепова 18 лет", "на Абая 2-комн.", "Кирова 5 млн"
_H = r"[^\S\n]"  # пробел без перевода строки
UNIT_AFTER = (_H + r"?-?" + _H + r"?(?:(?:й|ый|ий|ой|го|ом|ая|я)" + _H + r")?"
              r"(?:%|₸|\$|₽|(?:м|м2|м²|m|m2|m²|кв\.?" + _H + r"?м|эт|тг|км|мин|шт|чел|сот|мес|тыс|млн|млрд|лет)(?![^\W\d_])"
              r"|(?:этаж|квадрат|метр|комн|комнат|год|месяц|сотк|соток|тенге|процент|минут|квартир|человек|дней|день|дня|недел"
              r"|тысяч|миллион|доллар|рубл)[^\W\d_]*)")
_NOT_HOUSE = re.compile(UNIT_AFTER, re.I)
# между улицей и номером дома - только пробелы, запятая, "д.", "дом", "№": "Утепова, д. 18"; "Казахстане: 66" - не адрес
_HOUSE_GAP = re.compile(_H + r"*,?" + _H + r"*(?:(?:д|дом|здание|корпус|к)\.?" + _H + r"*|№" + _H + r"*)?", re.I)
# адреса в тексте для выдачи(addresses): "ул. Утепова, 18", "пр. Сатпаева 14/2", "мкр Самал-2"; "пр. Абая, 9 этаж" - без дома
ADDRESS_IN_TEXT = re.compile(r"(?<![^\W\d_])(?:ул\.|улица|пр-т|пр\.|просп\.|проспект|мкр\.?|микрорайон|пер\.|переулок)\s*"
                             r"[^\W\d_][\w\-]*(?:\s+[^\W\d_][\w\-]*)?(?:,?\s*(?:д\.\s*)?\d+(?!\d|[.,]\d)(?:[^\W\d_](?![^\W\d_]))?"
                             r"(?:\s*/\s*\d+)?(?!" + UNIT_AFTER + "))?", re.I)

SOCIAL = {"instagram.com", "tiktok.com", "t.me", "telegram.me", "facebook.com", "fb.com", "vk.com", "youtube.com", "threads.net"}
SOCIAL_SKIP = {"p", "reel", "reels", "stories", "tv", "video", "watch", "share", "joinchat"}
SOCIAL_NEXT = {"s", "c", "channel", "user", "groups", "people"}  # аккаунт - следующий сегмент: "t.me/s/nord_hills"
MARKETPLACES = {"krisha.kz", "olx.kz", "2gis.kz", "2gis.ru", "kn.kz", "market.kz"}
# Мессенджеры, сокращатели ссылок и "ссылки в профиле"(с поддоменами): сам хост - не аккаунт, ключ - вся ссылка(иначе
# все wa.me/… стали бы одним аккаунтом с общей историей)
SHARED_HOSTS = {"wa.me", "whatsapp.com", "linktr.ee", "taplink.cc", "taplink.ws", "bit.ly", "goo.gl", "clck.ru", "vk.cc",
                "tinyurl.com", "youtu.be", "telegram.org"}
_MOBILE = re.compile(r"^(?:m|mobile|web)\.(?=(?:" + "|".join(map(re.escape, sorted(SOCIAL))) + r")$)")  # m.instagram.com


def _prefix_eq(t: str, s: str, keep: int = 2) -> bool:
    """Совпадение основы: "Абая"/"Абай", "Утепова"/"Утепов", "Левобережье"/"Левобережном"."""
    if t == s:
        return True
    n = max(4, len(s) - keep)
    return len(s) >= 4 and len(t) >= 4 and (t.startswith(s[:n]) or s.startswith(t[:max(4, len(t) - keep)]))


def account_keys(value: str | None) -> set[str]:
    """"@altai.build", "https://instagram.com/altai.build/", "t.me/nord_hills_uka", "ertis-invest.kz" → ключи для сравнения."""
    s = (value or "").strip().lower()
    if not s:
        return set()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    if s.startswith("@"):
        return {s[1:].split()[0]} if s[1:].split() else set()
    host, _, path = s.partition("/")
    host = re.split(r"[?#:]", host.strip(), maxsplit=1)[0]  # без порта и параметров: "instagram.com?igsh=…"
    host = _MOBILE.sub("", host)
    if host in SOCIAL:
        segs = [x for x in re.split(r"[/?#]", path.strip()) if x]
        head = segs[0] if segs else ""
        # служебный сегмент сравнивается до снятия "@": "tiktok.com/@video", "youtube.com/@channel" - это аккаунты
        if head in SOCIAL_NEXT:
            seg = segs[1] if len(segs) > 1 else ""
        elif head == "profile.php":  # facebook.com/profile.php?id=… - аккаунт в параметре id
            ids = re.search(r"[?&]id=(\d+)", path)
            seg = f"profile.php?id={ids.group(1)}" if ids else ""
        else:
            seg = "" if head in SOCIAL_SKIP else head
        seg = seg.lstrip("@")
        return {seg} if seg else set()
    # продавец на площадке("olx.kz / агентство дом"), ссылка мессенджера("chat.whatsapp.com/…") или поддомена
    # соцсети("vm.tiktok.com/ZM…"): ключ - вся строка, а не общий для всех хост
    if any(host == d or host.endswith("." + d) for d in SOCIAL | MARKETPLACES | SHARED_HOSTS):
        return {re.sub(r"\s+", " ", s)}
    if re.fullmatch(r"[a-z0-9\-.]+\.[a-z]{2,}", host):
        return {host}
    return {re.sub(r"\s+", " ", s)}


_STREET_START = re.compile(r"\s*(?:ул\.|улица|пр-т|пр\.|просп\.|проспект|мкр\.?|микрорайон|пер\.|переулок|б-р|бульвар)",
                           re.I)
_INITIAL = re.compile(r"(?<![^\W\d_])[^\W\d_]\.\s*")  # "И. Есенберлина", "Ю.Увалиева"


def street_house(address: str) -> tuple[str, str | None]:
    """Улица и номер дома из адреса ЖК: "пр. Абая, 172" → ("Абая", "172"). Область и город перед улицей
    ("Восточно-Казахстанская область, г.Усть-Каменогорск, улица Кокжал Барака, д. 18б" - как в реестре) и инициалы
    ("пр. И. Есенберлина") отбрасываются; участок("ул. Кокжал Барака, уч. 1") - не номер дома: только улица."""
    parts = address.split(",")
    i = next((j for j, p in enumerate(parts) if _STREET_START.match(p)), None)
    m = _ADDR.match(address) if i is None else _ADDR.match(",".join(parts[i:])) or _ADDR.match(parts[i])
    if not m:
        return "", None
    house = re.sub(r"\s+", "", m.group("house")).lower() if m.group("house") else None
    return _INITIAL.sub("", m.group("street")), house


GLUED_MIN = 8  # с такой длины скелета название ищется и внутри склеенного имени аккаунта


def _account_words(key: str) -> tuple[set[str], str]:
    """Скелеты имени аккаунта: склейки до 8 подряд идущих слов("belukha.park.sale" → "beluha", "beluhapark", …) и всё
    имя одной строкой."""
    ws = [skeleton(w) for w in re.findall(r"[^\W_]+", key)]
    return {"".join(ws[i:j]) for i in range(len(ws)) for j in range(i + 1, min(len(ws), i + 8) + 1)}, "".join(ws)


def _in_account(name: str, words: set[str], glued: str) -> bool:
    """Название ЖК(склейка скелетов) в имени аккаунта: целыми словами("@orda_kvartiry", "@belukha.park.sale") или, если
    название длинное, внутри склеенного имени("@nordhillsuka"); короткое "Орда" внутри "@kordan" - не название."""
    return bool(name) and (name in words or (len(name) >= GLUED_MIN and name in glued))


@dataclass
class Entry:
    id: str
    name: str
    address: str
    district: str
    developer_id: str | None
    built: bool
    name_norm: tuple[str, ...]
    name_sk: tuple[str, ...]
    name_stem: tuple[str, ...]  # скелеты основ слов названия(без падежных окончаний)
    alt_sk: tuple[tuple[str, ...], ...]  # другие написания: "Нұр Әлем" по-русски - "Нур Элем"(ә → э)
    street_sk: tuple[str, ...]
    house: str | None
    district_sk: tuple[str, ...]


@dataclass
class Catalog:
    """Справочник для сопоставления: ЖК, застройщики(аккаунты, сайты, телефоны), история аккаунтов."""
    entries: list[Entry]
    developers: dict[str, Obj]
    dev_by_key: dict[str, str] = field(default_factory=dict)
    dev_by_phone: dict[str, str] = field(default_factory=dict)
    history: dict[str, dict[str, int]] = field(default_factory=dict)  # ключ аккаунта → {ЖК: подтверждённых публикаций}

    def complexes_of(self, dev_id: str) -> list[Entry]:
        own = [e for e in self.entries if e.developer_id == dev_id]
        active = [e for e in own if not e.built]
        return active or own


def _natural(cid: str) -> tuple[int, str]:
    return len(cid), cid


def build_catalog(complexes: Iterable[Obj], developers: Iterable[Obj], ads: Iterable[Obj] = (),
                  exclude_ad: str | None = None) -> Catalog:
    entries = []
    for c in sorted(complexes, key=lambda c: _natural(c["id"])):
        name_toks = tokens(c["name"], drop_stop=True)
        name_sk = tuple(t.sk for t in name_toks)
        alt = tuple(t.sk for t in tokens(c["name"].replace("ә", "э").replace("Ә", "Э"), drop_stop=True))
        street, house = street_house(c.get("address") or "")
        entries.append(Entry(
            id=c["id"], name=c["name"], address=c.get("address") or "", district=c.get("district") or "",
            developer_id=c.get("developerId"), built=c.get("stage") == BUILT,
            name_norm=tuple(t.norm for t in name_toks), name_sk=name_sk,
            name_stem=tuple(skeleton(stem(t.norm)) for t in name_toks), alt_sk=(alt,) if alt != name_sk else (),
            street_sk=tuple(t.sk for t in tokens(street)), house=house,
            district_sk=tuple(t.sk for t in tokens(c.get("district")))))
    devs = {d["id"]: d for d in developers}
    cat = Catalog(entries, devs)
    for d in devs.values():
        for key in account_keys(d.get("instagram")) | account_keys(d.get("site")):
            cat.dev_by_key[key] = d["id"]
        for p in d.get("phones") or []:
            cat.dev_by_phone[phone_digits(p)] = d["id"]
    known = {e.id for e in entries}
    for a in ads:
        confirmed = a.get("linkConfirmedBy") or a.get("status") == "confirmed"
        if a.get("id") == exclude_ad or not confirmed or a.get("status") == "dismissed" or a.get("complexId") not in known:
            continue
        if a.get("source") == "bot":  # аккаунт в заявке жителя - его слова, а не аккаунт автора рекламы: не история
            continue
        for key in account_keys(a.get("account")):
            row = cat.history.setdefault(key, {})
            row[a["complexId"]] = row.get(a["complexId"], 0) + 1
    return cat


# ---------- признаки ----------

@dataclass
class Feature:
    feature: str
    detail: str
    weight: float
    quote: str | None = None
    strength: float = 0.0  # сходство названия(для подборок)


@dataclass
class _Windows:
    """Окна одного размера: точные слова, склейки скелетов и основы с заглавной → начала окон по возрастанию."""
    norms: dict[tuple[str, ...], int]  # только первое окно - точное совпадение заканчивает поиск
    joined: dict[str, list[int]]
    stems: dict[tuple[str, ...], list[int]]


class Text:
    """Токены одного текста и индексы по ним - строятся один раз на текст, а не на каждый ЖК справочника: перебор всех
    окон текста для каждого ЖК на 20 000 символах и сотнях ЖК занимал минуты(открытый /api/analyze - DoS)."""

    def __init__(self, toks: list[Tok]):
        self.toks = toks
        self.sk = [t.sk for t in toks]
        # склейка скелетов всех токенов и начало каждого токена в ней: слово названия внутри окна ищется одним find
        self.starts: list[int] = []
        pos = 0
        for s in self.sk:
            self.starts.append(pos)
            pos += len(s)
        self.joined = "".join(self.sk)
        self.norm = [t.norm for t in toks]
        self.stem_sk = [skeleton(stem(n)) for n in self.norm]
        self.by_norm: dict[str, list[int]] = {}
        for i, n in enumerate(self.norm):
            self.by_norm.setdefault(n, []).append(i)
        self.by_sk: dict[str, list[int]] = {}
        self.by_head: dict[str, list[int]] = {}  # первые 4 буквы скелета - кандидаты _prefix_eq
        for i, s in enumerate(self.sk):
            self.by_sk.setdefault(s, []).append(i)
            if len(s) >= 4:
                self.by_head.setdefault(s[:4], []).append(i)
        self.by_trigram: dict[str, set[str]] = {}  # триграмма → скелеты текста с ней(кандидаты _near)
        for s in self.by_sk:
            for g in trigrams(s):
                self.by_trigram.setdefault(g, set()).add(s)
        self._windows: dict[int, _Windows] = {}

    def windows(self, size: int) -> _Windows:
        w = self._windows.get(size)
        if w is None:
            w = self._windows[size] = _Windows({}, {}, {})
            for i in range(len(self.toks) - size + 1):
                w.norms.setdefault(tuple(self.norm[i:i + size]), i)
                w.joined.setdefault("".join(self.sk[i:i + size]), []).append(i)
                if self.toks[i].cap:
                    w.stems.setdefault(tuple(self.stem_sk[i:i + size]), []).append(i)
        return w

    def starts_like(self, s: str, keep: int = 2) -> list[int]:
        """Позиции токенов, совпадающих со словом s по _prefix_eq, по возрастанию."""
        cand = set(self.by_sk.get(s, ()))
        if len(s) >= 4:
            cand.update(self.by_head.get(s[:4], ()))
        return sorted(i for i in cand if _prefix_eq(self.sk[i], s, keep))

    def spans(self, word: str) -> list[tuple[int, int]] | None:
        """Отрезки токенов [a, b], где word - часть склейки скелетов или похоже на токен(_near); None - любой
        отрезок(пустое слово). Окно может пройти нечёткое сравнение с названием, только если накрывает такой отрезок для
        каждого отличительного слова названия."""
        if not word:
            return None
        near = {s for g in trigrams(word) for s in self.by_trigram.get(g, ())}
        out = [(i, i) for s in near if _near(word, s) for i in self.by_sk[s]]
        o = self.joined.find(word)
        while o >= 0:
            out.append((bisect_right(self.starts, o) - 1, bisect_right(self.starts, o + len(word) - 1) - 1))
            o = self.joined.find(word, o + 1)
        return out

    def covering(self, spans: list[tuple[int, int]] | None, sizes: Iterable[int]) -> set[tuple[int, int]]:
        """Окна(начало, размер), накрывающие хотя бы один из отрезков."""
        n = len(self.toks)
        if spans is None:
            return {(i, size) for size in sizes for i in range(n - size + 1)}
        return {(i, size) for a, b in spans for size in sizes for i in range(max(0, b - size + 1), min(a, n - size) + 1)}


def name_similarity(e: Entry, text: Text) -> tuple[str, float, list[Tok]] | None:
    """Лучшее совпадение названия ЖК в тексте: ('name', 1.0) - точное; ('name', 0.95) - в другом падеже, с заглавной
    ("в Тихом береге"; "на тихом берегу" - не название); ('translit', 0.95) - тот же скелет(другая письменность,
    "НОРДХИЛЛС" без пробела, "Нур Элем" для "Нұр Әлем"); ('translit', сходство × 0.95) - нечёткое(опечатка, окончание).
    Из равных - первое по тексту. Проверяются только окна-кандидаты из индексов Text: результат тот же, что у перебора
    всех окон размера k-1…k+1, но без перебора."""
    if not e.name_sk:
        return None
    k = len(e.name_sk)
    sizes = sorted({max(1, k - 1), k, k + 1})
    toks = text.toks
    first = text.windows(k).norms.get(e.name_norm)
    if first is not None:
        return "name", 1.0, toks[first:first + k]
    variants = [("".join(sk), [s for s in sk if s not in GENERIC and not s.isdigit()]) for sk in (e.name_sk, *e.alt_sk)]
    numbers = [n for n in e.name_norm if n.isdigit()]
    cands = {(i, size) for size in sizes for joined, _ in variants for i in text.windows(size).joined.get(joined, ())}
    cands.update((i, k) for i in text.windows(k).stems.get(e.name_stem, ()))
    if any(distinct for _, distinct in variants):
        # нечёткое совпадение возможно только в окне со всеми номерами названия и всеми его отличительными словами:
        # кандидаты - окна вокруг самого редкого в тексте из них
        need = [[(i, i) for i in text.by_norm.get(n, ())] for n in numbers]
        for _, distinct in variants:
            if distinct:
                found = need + [text.spans(d) for d in distinct]
                cands |= text.covering(min(found, key=lambda sp: math.inf if sp is None else len(sp)), sizes)
    best: tuple[str, float, list[Tok]] | None = None
    for i, size in sorted(cands):
        win = toks[i:i + size]
        norms = tuple(text.norm[i:i + size])
        w_joined = "".join(text.sk[i:i + size])
        if any(w_joined == joined for joined, _ in variants):
            if best is None or best[1] < TRANSLIT:
                best = ("translit", TRANSLIT, win)
            continue
        if len(win) == k and win[0].cap and tuple(text.stem_sk[i:i + size]) == e.name_stem:
            if best is None or best[1] < DECLINED:
                best = ("name", DECLINED, win)
            continue
        for joined, distinct in variants:
            if not distinct or any(n not in norms for n in numbers):
                continue  # название только из общих слов("Квартал 21") - без нечёткого поиска; номер должен совпасть
            if not all(d in w_joined or any(_near(d, t.sk) for t in win) for d in distinct):
                continue
            sim = jaccard(w_joined, joined)
            if sim >= FUZZY_MIN and (best is None or sim * TRANSLIT > best[1]):
                best = ("translit", round(sim * TRANSLIT, 2), win)
        if best is not None and best[1] >= TRANSLIT:
            break  # выше 0.95 бывает только точное название, а его здесь нет - дальше не улучшить
    return best


@lru_cache(maxsize=65536)
def _near(word: str, token: str) -> bool:
    """Слово текста похоже на отличительное слово названия(скелеты, сходство триграмм от 0.45)."""
    return jaccard(word, token) >= 0.45


def _quote(text: str, win: list[Tok]) -> str:
    return text[win[0].start:win[-1].end]


def _prefix(toks: list[Tok], i: int) -> int | None:
    """Начало "ул.", "пр.", "пр-т", "б-р", "на", "по", "адрес" перед улицей(позиция в тексте) или None."""
    p = toks[i - 1].norm if i > 0 else ""
    pp = toks[i - 2].norm if i > 1 else ""
    if (pp, p) in (("пр", "т"), ("б", "р")):
        return toks[i - 2].start
    return toks[i - 1].start if p in _ADDR_PREFIX else None


def _house(toks: list[Tok], street_end: int, k: int, text: str) -> str | None:
    """Цифры номера дома в токене k: сразу после улицы(пробел, запятая, "д.") и без единицы после числа."""
    if k >= len(toks) or not toks[k].norm[:1].isdigit():
        return None
    digits = re.match(r"\d+", toks[k].norm).group()
    if not _HOUSE_GAP.fullmatch(text, street_end, toks[k].start) or _NOT_HOUSE.match(text, toks[k].start + len(digits)):
        return None
    return digits


def _street(e: Entry, idx: Text, text: str) -> Feature | None:
    """Адрес ЖК в тексте: "ул./пр./на" + улица + номер дома - сильный признак; улица и дом без "ул./пр./на" - средний
    (слово-улица может быть просто словом: "в Казахстане 66 …"); улица с "ул./пр./на" без номера - слабый; та же улица
    с другим номером дома - не признак(это другой дом). Число с единицей("9 этаж", "52 м²", "18 лет") или через
    знак конца фразы("Казахстане: 66") - не номер дома."""
    m = len(e.street_sk)
    if not m:
        return None
    toks = idx.toks
    house = re.match(r"\d+", e.house).group() if e.house else None
    found: Feature | None = None
    for i in idx.starts_like(e.street_sk[0]):
        if i + m > len(toks) or not all(_prefix_eq(toks[i + j].sk, e.street_sk[j]) for j in range(1, m)):
            continue
        k = i + m
        while k < len(toks) and toks[k].norm in _HOUSE_SKIP:
            k += 1
        num = _house(toks, toks[i + m - 1].end, k, text)
        prefix = _prefix(toks, i)
        if num and house:
            if num != house:
                continue
            q = _quote(text, toks[i:k + 1])
            if prefix is not None:
                return Feature("address", f"Адрес «{q}» совпадает с адресом ЖК ({e.address}): улица и дом", W["address"], q, 1.0)
            if found is None or found.weight < W["address_bare"]:
                found = Feature("address", f"«{q}» совпадает с адресом ЖК ({e.address}): улица и дом, но без «ул.», «пр.» "
                                           "или «на» — возможно, это не адрес", W["address_bare"], q)
            continue
        if prefix is not None and found is None:
            # номер есть только в тексте(у ЖК - участок или без номера) - дом не сравнить, признак слабый, как без номера
            q = text[prefix:toks[i + m - 1].end]
            detail = f"Упомянута улица ЖК ({e.address})" + ("" if num else ", номер дома не указан")
            found = Feature("address", detail, W["street"], q)
    return found


def _district(e: Entry, idx: Text, text: str) -> Feature | None:
    m = len(e.district_sk)
    if not m:
        return None
    toks = idx.toks
    for i in idx.starts_like(e.district_sk[0], keep=3):
        if i + m <= len(toks) and all(_prefix_eq(toks[i + j].sk, e.district_sk[j], keep=3) for j in range(1, m)):
            q = _quote(text, toks[i:i + m])
            return Feature("district", f"Упомянут район ЖК: {e.district}", W["district"], q)
    return None


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _dev_name(cat: Catalog, dev_id: str) -> str:
    return (cat.developers.get(dev_id) or {}).get("name") or dev_id


def hypotheses(cat: Catalog, text: str | None, ocr: str | None = None, account: str | None = None,
               url: str | None = None) -> tuple[list[Obj], Obj]:
    """Все гипотезы не ниже hypothesisMin по убыванию уверенности(при равенстве - по порядку ЖК) и сведения для
    выдачи: найденные телефоны, адреса, число упомянутых ЖК."""
    sources = [(s, src) for s, src in ((text or "", "text"), (ocr or "", "ocr")) if s.strip()]
    idx = {src: Text(tokens(s)) for s, src in sources}
    # название на скриншоте ищется и в тексте с латинскими двойниками, возвращёнными в кириллицу("Hyp Әлем" → "Нур Әлем");
    # позиции те же - цитата дословная
    name_idx = {src: [Text(tokens(s, drop_stop=True))]
                + ([Text(tokens(fold_homoglyphs(plain(s)), drop_stop=True))] if src == "ocr" else [])
                for s, src in sources}
    feats: dict[str, list[Feature]] = {e.id: [] for e in cat.entries}

    for e in cat.entries:
        # название и адрес: берём лучшее из текста и скриншота(при равенстве - текст)
        best_name: Feature | None = None
        for s, src in sources:
            hits = [h for nt in name_idx[src] for h in [name_similarity(e, nt)] if h]
            if not hits:
                continue
            kind, sim, win = max(hits, key=lambda h: h[1])
            factor = OCR_FACTOR if src == "ocr" else 1.0
            q = _quote(s, win)
            where = " на скриншоте (OCR)" if src == "ocr" else ""
            if kind == "name" and sim < 1:
                detail = f"«{q}» — название «{e.name}» в другом падеже{where}"
            elif kind == "name":
                detail = f"Название «{e.name}» в тексте{where}"
            elif sim >= TRANSLIT:
                detail = f"«{q}» ≈ «{e.name}» — то же название в другом написании (транслитерация){where}"
            else:
                detail = f"«{q}» похоже на «{e.name}»: сходство {round(sim / TRANSLIT * 100)}%{where}"
            f = Feature(kind, detail, round(W["name"] * sim * factor, 2), q, sim)
            if best_name is None or f.weight > best_name.weight:
                best_name = f
        if best_name:
            feats[e.id].append(best_name)
        for kind_fn in (_street, _district):
            found = [(f, src) for s, src in sources for f in [kind_fn(e, idx[src], s)] if f]
            if found:
                f, src = max(found, key=lambda x: x[0].weight)
                if src == "ocr":
                    f.weight = round(f.weight * OCR_FACTOR, 2)
                    f.detail += " (на скриншоте, OCR)"
                feats[e.id].append(f)

    # аккаунт и ссылка: официальный аккаунт/сайт застройщика, название ЖК в имени аккаунта, история аккаунта
    acc_keys = account_keys(account)
    all_keys = acc_keys | account_keys(url)
    for key in sorted(all_keys):
        dev = cat.dev_by_key.get(key)
        if dev:
            own = cat.complexes_of(dev)
            for e in own:
                feats[e.id].append(Feature("account", _split(f"«{key}» — официальный аккаунт/сайт застройщика "
                                                              f"{_dev_name(cat, dev)}", len(own)),
                                           round(W["account"] / math.sqrt(len(own)), 2)))
    for key in sorted(acc_keys):
        words, glued = _account_words(key)
        for e in cat.entries:
            if _in_account("".join(e.name_sk), words, glued):
                feats[e.id].append(Feature("account", f"В имени аккаунта «{key}» — название ЖК «{e.name}»", W["account_name"]))
        for cid, n in sorted((cat.history.get(key) or {}).items()):
            if cid in feats:
                feats[cid].append(Feature("history", f"У аккаунта «{key}» уже есть подтверждённые аналитиком публикации "
                                                     f"по этому ЖК: {n}", W["history_more" if n > 1 else "history"]))

    # телефоны застройщиков
    found_phones: dict[str, str] = {}  # цифры → номер как в тексте(первое написание)
    for s, src in sources:
        for raw, digits, _, _ in phones(s):
            found_phones.setdefault(digits, raw)
            dev = cat.dev_by_phone.get(digits)
            if not dev:
                continue
            own = cat.complexes_of(dev)
            for e in own:
                if any(f.feature == "phone" for f in feats[e.id]):
                    continue
                feats[e.id].append(Feature("phone", _split(f"Телефон {raw} — номер застройщика {_dev_name(cat, dev)}", len(own)),
                                           round(W["phone"] / math.sqrt(len(own)) * (OCR_FACTOR if src == "ocr" else 1), 2),
                                           raw))

    # подборка: несколько ЖК названы в одном тексте - это не реклама конкретного объекта
    named = [e for e in cat.entries if any(f.feature in ("name", "translit") and f.strength >= CROWD_MIN for f in feats[e.id])]
    crowd = max(0.4, 0.6 / math.sqrt(len(named) - 1)) if len(named) >= 2 else 1.0

    out = []
    for e in cat.entries:
        fs = feats[e.id]
        if not fs:
            continue
        conf = round(_sigmoid(sum(f.weight for f in fs) - BIAS) * crowd, 2)
        if conf < T["hypothesisMin"]:
            continue
        fs = sorted(fs, key=lambda f: -f.weight)
        quote = next((f.quote for f in fs if f.quote), None)
        h = {"complexId": e.id, "complexName": e.name, "confidence": conf, "low": conf < T["lowConfidence"],
             "reasons": [{"feature": f.feature, "detail": f.detail, "weight": f.weight} for f in fs]}
        if quote:
            h["quote"] = quote
        out.append(h)
    order = {e.id: i for i, e in enumerate(cat.entries)}
    out.sort(key=lambda h: (-h["confidence"], order[h["complexId"]]))
    addresses: list[str] = []
    for s, _ in sources:
        addresses += [m.group().strip(" ,") for m in ADDRESS_IN_TEXT.finditer(s)]
    # адрес ЖК, найденный и без "ул."("квартиры на Утепова 18")
    addresses += [f.quote for e in cat.entries for f in feats[e.id] if f.feature == "address" and f.strength and f.quote
                  and not any(f.quote in a for a in addresses)]
    addresses = list(dict.fromkeys(addresses))
    return out, {"phones": list(found_phones.values()), "addresses": addresses, "named": [e.name for e in named]}


def _split(detail: str, n: int) -> str:
    return detail + (f" (у застройщика строящихся ЖК: {n} — признак делится между ними)" if n > 1 else "")
