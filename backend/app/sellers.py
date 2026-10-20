"""Кто продаёт: связанные продавцы, аномальная цена(app.prices), связи застройщика(app.affiliations).

Контакты публикации(contacts) - из текста, OCR картинок и речи роликов(абзацы OCR "Речь в ролике:" - источник
speech): казахстанские телефоны(ai.nlp.phones: "+7 777 700 77 33", "8(777) 700-77-33", "87777007733" → 10 цифр без
кода страны), WhatsApp-ссылки(wa.me/…, api.whatsapp.com/send?phone=…) - тот же номер, упоминания @аккаунтов, БИН / БСН
с подписью рядом и верной контрольной цифрой(bin_valid). У каждого контакта - дословный фрагмент вокруг находки.
Контактов одного вида из одного источника - не больше CONTACTS_MAX.

Аккаунт - площадка + имя аккаунта(без регистра). У Krisha "специалист", "хозяин", "агент" - тип продавца, а не
аккаунт: такая публикация - отдельный аккаунт "krisha.kz / специалист · AD-…". Продавец(группа) - связная компонента
аккаунтов по общим признакам(links): один телефон или WhatsApp, один БИН, одно имя @аккаунта на разных площадках,
упоминание @аккаунта из наших данных, одна и та же картинка(dHash 64 бита, расстояние Хэмминга ≤ IMAGE_MAX_DISTANCE;
однотонные и почти пустые картинки не сравниваются; связь - одна на кластер похожих картинок). Хэши картинок
кэшируются по SHA-256 файла в JSON рядом с хранилищем(image_hashes.json): файл по хэшу не меняется, схема БД не
трогается; новые считаются по одной на процесс(_HASH_LOCK).

Это гипотеза для проверки, а не вывод: общий номер бывает у агентства, которое продаёт несколько ЖК, картинка - у
застройщика и перепродающего агента. Ни в балл риска, ни в решения аналитика группа не попадает.
"""
import json
import logging
import os
import re
import tempfile
import threading
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise, product
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import media, models
from .ai.nlp import phones as _phones
from .ai.nlp import plain
from .db import get_db
from .settings import get_settings

log = logging.getLogger("saq.sellers")

router = APIRouter(tags=["sellers"])

Obj = dict[str, Any]

NOTE = ("Связь по номеру телефона, БИН, имени аккаунта или общей картинке — гипотеза для проверки, а не вывод о "
        "нарушении: общий номер бывает у агентства, которое продаёт несколько ЖК. В балл риска не входит.")
IMAGE_MAX_DISTANCE = 6  # dHash: до 6 из 64 бит - та же картинка после пережатия, обрезки подписи, смены размера
IMAGE_MIN_STDDEV = 12.0  # однотонный фон, почти пустой кадр - у таких dHash совпадает случайно
# Больше(у JPEG - после уменьшения draft) - не декодируется: PNG, WEBP и GIF декодируются целиком(16 Мп RGBA - 64 МБ),
# а для dHash хватает картинки 9×8; 16 Мп - снимок экрана 5K и фото камеры телефона 12 Мп
IMAGE_MAX_PIXELS = 16_000_000
HASH_WAIT_S = 5.0  # сколько запрос ждёт идущего расчёта хэшей(_HASH_LOCK); не дождался - группы по уже посчитанным
HASH_SAVE_S = 1.0  # посчитанные хэши записываются в кэш не реже: параллельный и следующий запрос их не считают заново
QUOTE_SIDE = 60
EVIDENCE_MAX = 30  # фрагментов на признак
CONTACTS_MAX = 20  # контактов одного вида(телефон с WhatsApp, БИН, упоминание) из одного источника публикации
KRISHA_TYPES = {"специалист", "хозяин", "агент", "застройщик", "собственник"}
SPEECH = "Речь в ролике:"
LINK_LABEL = {"phone": "телефон", "wa": "WhatsApp", "bin": "БИН", "handle": "одно имя аккаунта",
              "mention": "упоминание аккаунта", "image": "одна картинка"}


# ---------- контакты ----------

@dataclass
class Contact:
    kind: str  # phone | wa | bin | mention
    value: str  # ключ: 10 цифр телефона, 12 цифр БИН, имя аккаунта без "@"
    shown: str  # как показать: "+7 777 700 77 33", "БИН 201040022748", "@an.groups"
    raw: str  # как в тексте
    source: str  # text | ocr | speech
    quote: str


def fmt_phone(d10: str) -> str:
    """'7777007733' → '+7 777 700 77 33'; городской 7232xxxxxx - так же по группам 3-3-2-2."""
    d = re.sub(r"\D", "", d10 or "")[-10:]
    if len(d) != 10:
        return d10
    return f"+7 {d[:3]} {d[3:6]} {d[6:8]} {d[8:]}"


def norm_phone(raw: str) -> str | None:
    """Казахстанский номер в любом написании → 10 цифр без кода страны; не номер РК → None."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d[0] in "78":
        d = d[1:]
    elif len(d) == 12 and d.startswith("77"):  # "+77 7…" - лишняя 7 при наборе
        d = d[2:]
    return d if len(d) == 10 and d[0] == "7" else None


def bin_valid(b: str) -> bool:
    """Контрольная цифра ИИН/БИН РК: сумма цифр 1-11 с весами 1…11 по модулю 11; 10 - повтор с весами 3…11, 1, 2;
    снова 10 - номер недействителен."""
    if not re.fullmatch(r"\d{12}", b or ""):
        return False
    d = [int(x) for x in b]
    s = sum(d[i] * (i + 1) for i in range(11)) % 11
    if s == 10:
        s = sum(d[i] * ((i + 2) % 11 + 1) for i in range(11)) % 11
    return s != 10 and s == d[11]


def quote_at(text: str, a: int, b: int, side: int = QUOTE_SIDE) -> str:
    """Дословный фрагмент вокруг [a, b): до side символов с каждой стороны по границе слова, переводы строк - пробел."""
    lo, hi = max(0, a - side), min(len(text), b + side)
    if lo > 0:
        sp = text.find(" ", lo, a)
        lo = sp + 1 if sp != -1 else lo
    if hi < len(text):
        sp = text.rfind(" ", b, hi)
        hi = sp if sp != -1 else hi
    s = re.sub(r"\s+", " ", text[lo:hi]).strip()
    return ("…" if lo > 0 else "") + s + ("…" if hi < len(text) else "")


def segments(text: str | None, ocr: str | None) -> list[tuple[str, str]]:
    """Текст публикации, OCR картинок и речь роликов - отдельно: абзац OCR "Речь в ролике: …" - источник speech."""
    out = [("text", plain(text))] if text else []
    if ocr:
        pics, speech = [], []
        for par in re.split(r"\n\s*\n", plain(ocr)):
            (speech if par.lstrip().startswith(SPEECH) else pics).append(par)
        if pics:
            out.append(("ocr", "\n\n".join(pics)))
        if speech:
            out.append(("speech", "\n\n".join(speech)))
    return out


_WA = re.compile(r"(?:wa\.me/|whatsapp\.com/send/?\?(?:[\w=&]*&)?phone=)\+?(\d[\d\-]{9,14})", re.I)
_BIN = re.compile(r"(?<![\w])(?:ИИН\s*/\s*БИН|БИН\s*/\s*ИИН|БИН|БСН|BIN|ЖСН)\s*[:№#\-–]?\s*(\d{12})(?!\d)", re.I)
_MENTION = re.compile(r"(?<![\w@.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?)")
_EMAIL_TAIL = re.compile(r"^\.(?:kz|ru|com|net|org)\b", re.I)


def contacts(text: str | None, ocr: str | None) -> list[Contact]:
    """Контакты публикации по источникам; один и тот же контакт в одном источнике - один раз(первая находка). Одного
    вида из одного источника - не больше CONTACTS_MAX: в рекламе их единицы, а текст с сотнями номеров - не реклама
    одного продавца(и тысячи связей в группах продавцов)."""
    out: list[Contact] = []
    seen = set()
    taken: dict[tuple[str, str], int] = {}  # (вид, источник) → контактов; WhatsApp - вид "phone"

    def full(kind, src) -> bool:
        return taken.get(("phone" if kind == "wa" else kind, src), 0) >= CONTACTS_MAX

    def add(kind, value, shown, raw, src, s, a, b):
        if (kind, value, src) in seen:
            return
        seen.add((kind, value, src))
        cap = ("phone" if kind == "wa" else kind, src)
        taken[cap] = taken.get(cap, 0) + 1
        out.append(Contact(kind, value, shown, raw, src, quote_at(s, a, b)))

    for src, s in segments(text, ocr):
        wa_starts, wa_ends = [], []  # ссылки WhatsApp по порядку, не пересекаются: номер внутри - не отдельный телефон
        for m in _WA.finditer(s):
            if full("wa", src):
                break
            d = norm_phone(m.group(1))
            if d:
                wa_starts.append(m.start())
                wa_ends.append(m.end())
                add("wa", d, fmt_phone(d), m.group(0), src, s, m.start(), m.end())
        for raw, d, a, b in _phones(s):
            if full("phone", src):
                break
            k = bisect_right(wa_starts, a) - 1
            if k < 0 or a >= wa_ends[k]:
                add("phone", d, fmt_phone(d), raw, src, s, a, b)
        for m in _BIN.finditer(s):
            if full("bin", src):
                break
            if bin_valid(m.group(1)):
                add("bin", m.group(1), f"БИН {m.group(1)}", m.group(0), src, s, m.start(), m.end())
        for m in _MENTION.finditer(s):
            if full("mention", src):
                break
            if _EMAIL_TAIL.match(s[m.end():m.end() + 4]):
                continue
            h = m.group(1).lower()
            add("mention", h, "@" + h, m.group(0), src, s, m.start(), m.end())
    return out


# ---------- аккаунты ----------

def account_key(platform: str, account: str, ad_id: str) -> str:
    """Ключ аккаунта: "instagram:@an.groups"; тип продавца Krisha("специалист") - аккаунт одной публикации."""
    acc = (account or "").strip().lower()
    kind = acc.split("/", 1)[1].strip() if "/" in acc else acc
    if platform == "krisha" and kind in KRISHA_TYPES:
        return f"krisha:{kind}#{ad_id[:8]}"
    return f"{platform}:{acc}"


def account_label(key: str, account: str) -> str:
    """Подпись аккаунта: у типа продавца Krisha - с номером публикации("krisha.kz / специалист · AD-1FD95B8E")."""
    return f"{account} · AD-{key.split('#', 1)[1].upper()}" if "#" in key else account


def handle_of(account: str) -> str | None:
    """Имя аккаунта соцсети без "@" и регистра: "@An.Groups" → "an.groups"; у Krisha имени нет."""
    a = (account or "").strip()
    return a[1:].lower() if a.startswith("@") and len(a) > 1 else None


def slug(key: str) -> str:
    """Ключ группы для адреса(/sellers?key=…): "instagram:@an.groups" → "instagram-an.groups"."""
    return re.sub(r"[^\w.]+", "-", key.lower()).strip("-")


# ---------- хэши картинок ----------

def dhash(path: Path) -> int | None:
    """dHash 64 бита: оттенки серого 9×8, бит - "левый пиксель ярче правого". Однотонная или огромная картинка → None.
    Декодирование - media.open_image: только форматы хранилища, JPEG - сразу уменьшенным(draft), предел пикселей -
    IMAGE_MAX_PIXELS."""
    from PIL import Image, ImageStat

    try:
        im = media.open_image(path, max_pixels=IMAGE_MAX_PIXELS, draft=("L", (64, 64)))
    except media.MediaRejected:
        return None
    with im:
        g = im.convert("L")
        if ImageStat.Stat(g).stddev[0] < IMAGE_MIN_STDDEV:
            return None
        g = g.resize((9, 8), Image.LANCZOS)
        px = list(g.tobytes())
    v = 0
    for y in range(8):
        for x in range(8):
            v = (v << 1) | (px[y * 9 + x] > px[y * 9 + x + 1])
    return v


def _cache_path() -> Path:
    return Path(get_settings().media_root).parent / "image_hashes.json"


def _load_cache(path: Path) -> dict[str, int | None]:
    """Кэш хэшей; нет файла, битый JSON или чужие значения - пустой(хэши пересчитываются)."""
    try:
        raw = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if v is None or (type(v) is int and 0 <= v < 1 << 64)}


def _save_cache(path: Path, cache: dict[str, int | None]) -> None:
    """Запись через временный файл и os.replace: параллельный запрос не прочитает файл наполовину записанным."""
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".image_hashes.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cache, f)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        log.warning("кэш хэшей картинок не записан: %s", e)


# декодирование картинок для dHash - по одной на процесс(как распознавание ai.ocr): параллельные запросы списка
# продавцов не декодируют разом одни и те же новые картинки
_HASH_LOCK = threading.Lock()


def image_hashes(db: Session) -> dict[str, list[tuple]]:
    """Публикация → [(dHash, sha256, ID файла)] её загруженных картинок и кадров видео; файлы без публикации(фото
    объекта, непривязанные загрузки) не читаются. Кэш по SHA-256 - в image_hashes.json; недостающие хэши считает
    _fill_cache. Файл, которого нет на диске(стартовый набор без медиа), пропускается."""
    try:
        import PIL  # noqa: F401
    except ImportError:  # pragma: no cover - Pillow в зависимостях
        return {}
    path = _cache_path()
    cache = _load_cache(path)
    M = models.MediaAsset
    rows = db.execute(select(M.ad_id, M.sha256, M.storage_key, M.id).where(
        M.status == "downloaded", M.kind.in_(("image", "frame")), M.storage_key.is_not(None),
        M.ad_id.is_not(None))).all()
    missing = {sha: key for _, sha, key, _ in rows if sha and sha not in cache}
    if missing:
        cache = _fill_cache(path, missing)
    out: dict[str, list[tuple]] = {}
    for ad_id, sha, _, mid in rows:
        h = cache.get(sha) if sha else None
        if h is not None:
            out.setdefault(ad_id, []).append((h, sha, mid))
    return out


def _fill_cache(path: Path, missing: dict[str, str]) -> dict[str, int | None]:
    """Хэши недостающих файлов(sha256 → ключ хранилища) - под _HASH_LOCK; кэш с ними. Дождавшийся замка перечитывает
    кэш: посчитанное другим запросом не повторяется. Не дождавшийся за HASH_WAIT_S - кэш как есть(записанное идущим
    расчётом); остальные картинки войдут в группы следующего запроса. Посчитанное пишется в кэш не реже HASH_SAVE_S
    и в конце - в том числе при сбое."""
    if not _HASH_LOCK.acquire(timeout=HASH_WAIT_S):
        return _load_cache(path)
    try:
        cache = _load_cache(path)
        saved, dirty = time.monotonic(), False
        try:
            for sha, key in missing.items():
                if sha in cache:
                    continue
                try:
                    p = media.path_of(key)
                    if not p.is_file():
                        continue
                    cache[sha] = dhash(p)
                except Exception as e:  # noqa: BLE001 - битый файл(любая ошибка PIL) не роняет список продавцов
                    log.warning("dHash %s: %s", key, e)
                    cache[sha] = None
                dirty = True
                if time.monotonic() - saved >= HASH_SAVE_S:
                    _save_cache(path, cache)
                    saved, dirty = time.monotonic(), False
        finally:
            if dirty:
                _save_cache(path, cache)
        return cache
    finally:
        _HASH_LOCK.release()


# ---------- группы ----------

class _DSU[K: (str, int)]:
    """Система непересекающихся множеств: аккаунты(str) или номера хэшей картинок(int); корень - наименьший."""

    def __init__(self):
        self.p: dict[K, K] = {}

    def find(self, x: K) -> K:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: K, b: K) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


@dataclass
class _Link:
    kind: str
    value: str
    shown: str
    accounts: set = field(default_factory=set)
    evidence: list[Obj] = field(default_factory=list)  # не больше EVIDENCE_MAX
    ads: set = field(default_factory=set)  # все публикации с признаком(доказательств - не больше EVIDENCE_MAX)
    wa: bool = False  # номер встретился и WhatsApp-ссылкой


def _day(d: str | None) -> str | None:
    return (d or "")[:10] or None


def _near_forest(hs: list[int], d: int) -> list[tuple[int, int]]:
    """Остов кластеров хэшей: пары(j, i), j < i, с расстоянием Хэмминга ≤ d - по одной на каждое слияние, не больше
    len(hs) - 1. Кластеры - те же, что по всем близким парам. У близких хэшей хотя бы один из d + 1 кусков совпадает
    целиком(принцип Дирихле): хэш сравнивается только с хэшами общего куска, причём с хэшами своего кластера - ни с
    одним, а чужого - до первого близкого. Сотни пережатых копий одного рендера(все пары близки) - почти линейно, а
    не перебор всех пар."""
    k = d + 1
    bounds = [64 * b // k for b in range(k + 1)]
    blocks = [(lo, (1 << (hi - lo)) - 1) for lo, hi in pairwise(bounds)]
    buckets: list[dict[int, dict[int, list[int]]]] = [{} for _ in blocks]  # кусок → {корень кластера: его хэши}
    dsu: _DSU[int] = _DSU()
    edges: list[tuple[int, int]] = []
    for i, h in enumerate(hs):
        for (lo, mask), bucket in zip(blocks, buckets, strict=True):
            comps: dict[int, list[int]] = {}
            for r, ms in bucket.get((h >> lo) & mask, {}).items():  # корни после слияний: меньший список - в больший
                root = dsu.find(r)
                cur = comps.setdefault(root, ms)
                if cur is not ms:
                    big, small = (cur, ms) if len(cur) >= len(ms) else (ms, cur)
                    big.extend(small)
                    comps[root] = big
            for r, ms in comps.items():
                if dsu.find(r) == dsu.find(i):
                    continue
                j = next((j for j in ms if (h ^ hs[j]).bit_count() <= d), None)
                if j is not None:
                    dsu.union(i, j)
                    edges.append((j, i))
            comps.setdefault(dsu.find(i), []).append(i)
            bucket[(h >> lo) & mask] = comps
    return edges


def _image_links(flat: list[tuple]) -> list[_Link]:
    """Связи "одна картинка" по картинкам flat - (dHash, sha256, публикация, аккаунт, ID файла). Кластер - картинки,
    связанные цепочкой пар с расстоянием Хэмминга ≤ IMAGE_MAX_DISTANCE; связь - одна на кластер, со всеми его
    аккаунтами(значение - наименьший sha256 кластера), а не на пару аккаунтов: общий рендер в тысяче публикаций дал
    бы полмиллиона связей. Одинаковые хэши сравниваются один раз, различные - по остову(_near_forest). Доказательства -
    до EVIDENCE_MAX пар картинок разных аккаунтов, по одной на пару аккаунтов: сначала одинаковые хэши, затем близкие
    пары остова. Связи с одним аккаунтом не возвращаются."""
    same: dict[int, list[int]] = {}
    for i, x in enumerate(flat):
        same.setdefault(x[0], []).append(i)
    uniq = list(same)
    reps: list[dict[str, int]] = []  # у каждого хэша - первая картинка каждого аккаунта: пары перебирают аккаунты
    for h in uniq:
        first: dict[str, int] = {}
        for i in same[h]:
            first.setdefault(flat[i][3], i)
        reps.append(first)
    forest = _near_forest(uniq, IMAGE_MAX_DISTANCE)
    dsu: _DSU[int] = _DSU()
    for u, v in forest:
        dsu.union(u, v)
    clusters: dict[int, _Link] = {}
    for u, h in enumerate(uniq):
        for i in same[h]:
            _, sha, ad_id, k, _ = flat[i]
            r = dsu.find(u)
            ln = clusters.get(r)
            if ln is None:
                ln = clusters[r] = _Link("image", sha, "одна картинка")
            ln.value = min(ln.value, sha)
            ln.accounts.add(k)
            ln.ads.add(ad_id)
    out = {r: ln for r, ln in clusters.items() if len(ln.accounts) > 1}
    shown: dict[int, set[tuple[str, str]]] = {r: set() for r in out}  # пары аккаунтов в доказательствах

    def full(r: int) -> bool:
        return r not in out or len(out[r].evidence) >= EVIDENCE_MAX

    def prove(r: int, i: int, j: int) -> None:
        (hi, si, ai, ki, mi), (hj, sj, aj, kj, mj) = flat[min(i, j)], flat[max(i, j)]
        pair = (min(ki, kj), max(ki, kj))
        if ki == kj or pair in shown[r]:
            return
        shown[r].add(pair)
        dist = (hi ^ hj).bit_count()
        out[r].evidence.append({"adId": ai, "otherAdId": aj, "sha256": si, "otherSha256": sj, "mediaId": mi,
                                "otherMediaId": mj, "distance": dist, "source": "image",
                                "quote": f"dHash: расстояние {dist} из 64"})

    for u, first in enumerate(reps):  # одинаковый хэш: первый аккаунт - с каждым другим
        r = dsu.find(u)
        i, *others = first.values()
        for j in others:
            if full(r):
                break
            prove(r, i, j)
    for u, v in forest:  # близкие хэши остова: каждый аккаунт одного - с каждым аккаунтом другого
        r = dsu.find(u)
        for i, j in product(reps[u].values(), reps[v].values()):
            if full(r):
                break
            prove(r, i, j)
    return list(out.values())


def build(ads: Iterable[Obj], hashes: dict[str, list[tuple]] | None = None,
          developers: Iterable[Obj] = ()) -> list[Obj]:
    """Группы продавцов по публикациям - чистая функция. Публикация: {id, platform, account, date, text, ocr, url?,
    complexId?, complexName?, linkConfirmed?}; hashes - публикация → [(dHash, sha256, ID файла?)]; developers - {id, name, bin}
    (БИН из рекламы называет застройщика). Группы - по числу аккаунтов, затем публикаций."""
    ads = list(ads)
    hashes = hashes or {}
    dev_by_bin = {d["bin"]: d for d in developers if d.get("bin")}
    dsu: _DSU[str] = _DSU()
    acc: dict[str, Obj] = {}
    by_ad: dict[str, str] = {}
    links: dict[tuple[str, str], _Link] = {}

    def link(kind, value, shown) -> _Link:
        return links.setdefault((kind, value), _Link(kind, value, shown))

    for a in ads:
        k = account_key(a["platform"], a["account"], a["id"])
        by_ad[a["id"]] = k
        dsu.find(k)
        x = acc.setdefault(k, {"key": k, "platform": a["platform"], "account": a["account"], "ads": [],
                               "handle": None if "#" in k else handle_of(a["account"])})
        x["ads"].append(a)
        for c in contacts(a.get("text"), a.get("ocr")):
            kind = "phone" if c.kind == "wa" else c.kind
            ln = link(kind, c.value, c.shown)
            if c.kind == "wa":
                ln.wa = True
            ln.accounts.add(k)
            ln.ads.add(a["id"])
            if len(ln.evidence) < EVIDENCE_MAX:
                ln.evidence.append({"adId": a["id"], "account": a["account"], "source": c.source, "quote": c.quote,
                                    "raw": c.raw, "whatsapp": c.kind == "wa"})
    # одно имя аккаунта на разных площадках; упоминание @аккаунта, который есть в данных
    by_handle: dict[str, set] = {}
    for k, x in acc.items():
        if x["handle"]:
            by_handle.setdefault(x["handle"], set()).add(k)
    for h, ks in by_handle.items():
        if len(ks) > 1:
            ln = link("handle", h, "@" + h)
            ln.accounts |= ks
    for (kind, value), ln in list(links.items()):
        if kind != "mention":
            continue
        targets = by_handle.get(value, set())
        ln.accounts |= targets
        if not targets or len(ln.accounts) < 2:
            del links[(kind, value)]  # упомянут аккаунт вне наших данных или только сам себя - связи нет
    # одна картинка у разных аккаунтов
    # (dHash, sha256, публикация, аккаунт, ID файла - для превью /api/media/{id}; в тестах его может не быть)
    flat = [(x[0], x[1], ad_id, by_ad[ad_id], x[2] if len(x) > 2 else None)
            for ad_id, hs in hashes.items() if ad_id in by_ad for x in hs]
    every = [*links.values(), *_image_links(flat)]
    for ln in every:
        ks = sorted(ln.accounts)
        for o in ks[1:]:
            dsu.union(ks[0], o)
    by_root: dict[str, list[_Link]] = {}  # связи группы - по корню любого её аккаунта, за один проход
    for ln in every:
        if ln.accounts:
            by_root.setdefault(dsu.find(next(iter(ln.accounts))), []).append(ln)

    comps: dict[str, list[str]] = {}
    for k in acc:
        comps.setdefault(dsu.find(k), []).append(k)
    groups = []
    for root, ks in comps.items():
        g_links = by_root.get(root, [])
        g_ads = [a for k in ks for a in acc[k]["ads"]]
        accounts = []
        for k in ks:
            days = [d for d in (_day(a["date"]) for a in acc[k]["ads"]) if d]  # без даты - не сравнивается с датами
            accounts.append({"key": k, "platform": acc[k]["platform"], "account": account_label(k, acc[k]["account"]),
                             "ads": len(acc[k]["ads"]), "firstSeen": min(days, default=None),
                             "lastSeen": max(days, default=None)})
        accounts.sort(key=lambda x: (-x["ads"], x["key"]))
        primary = accounts[0]
        phones_ = [{"phone": ln.shown, "digits": ln.value, "whatsapp": ln.wa, "accounts": sorted(ln.accounts),
                    "ads": len(ln.ads), "adIds": sorted(ln.ads), "evidence": ln.evidence}
                   for ln in g_links if ln.kind == "phone"]
        phones_.sort(key=lambda p: (-len(p["accounts"]), -p["ads"], p["digits"]))
        bins = [{"bin": ln.value, "developerId": dev_by_bin.get(ln.value, {}).get("id"),
                 "developerName": dev_by_bin.get(ln.value, {}).get("name"), "accounts": sorted(ln.accounts),
                 "evidence": ln.evidence} for ln in g_links if ln.kind == "bin"]
        cx: dict[str, Obj] = {}
        for a in g_ads:
            if a.get("complexId"):
                c = cx.setdefault(a["complexId"], {"id": a["complexId"], "name": a.get("complexName") or a["complexId"],
                                                   "ads": 0, "confirmed": 0, "accounts": set()})
                c["ads"] += 1
                c["confirmed"] += bool(a.get("linkConfirmed"))
                c["accounts"].add(by_ad[a["id"]])
        complexes = sorted(({**c, "accounts": sorted(c["accounts"])} for c in cx.values()), key=lambda c: (-c["ads"], c["name"]))
        g_links_out = [{"kind": ln.kind, "label": LINK_LABEL[ln.kind], "value": ln.value, "shown": ln.shown,
                        "accounts": sorted(ln.accounts), "evidence": ln.evidence} for ln in g_links
                       if len(ln.accounts) > 1]
        g_links_out.sort(key=lambda x: (list(LINK_LABEL).index(x["kind"]), x["value"]))
        dates = [d for d in (_day(a["date"]) for a in g_ads) if d]
        groups.append({
            "key": slug(primary["key"]), "title": primary["account"], "accounts": accounts, "phones": phones_,
            "bins": bins, "complexes": complexes, "links": g_links_out, "adsCount": len(g_ads),
            "adIds": [a["id"] for a in sorted(g_ads, key=lambda a: a["date"] or "", reverse=True)],
            "firstSeen": min(dates, default=None), "lastSeen": max(dates, default=None),
            "linked": len(accounts) > 1, "hypothesis": True,
        })
    groups.sort(key=lambda g: (-len(g["accounts"]), -g["adsCount"], g["key"]))
    return groups


# ---------- данные из БД ----------

def ad_rows(db: Session) -> list[Obj]:
    A, C = models.Ad, models.Complex
    names = dict(db.execute(select(C.id, C.name)).all())
    return [{"id": a.id, "platform": a.platform, "account": a.account, "date": a.date, "url": a.url, "text": a.text,
             "ocr": a.ocr, "price": a.price, "complexId": a.complex_id, "complexName": names.get(a.complex_id),
             "linkConfirmed": bool(a.link_confirmed_by)}
            for a in db.execute(select(A.id, A.platform, A.account, A.date, A.url, A.text, A.ocr, A.price,
                                       A.complex_id, A.link_confirmed_by)).all()]


def compute(db: Session, rows: list[Obj] | None = None) -> list[Obj]:
    """Группы продавцов по всем публикациям; rows - уже загруженные ad_rows(db)."""
    D = models.Developer
    devs = [{"id": i, "name": n, "bin": b} for i, n, b in db.execute(select(D.id, D.name, D.bin)).all()]
    return build(ad_rows(db) if rows is None else rows, image_hashes(db), devs)


def stats(groups: list[Obj]) -> Obj:
    return {"groups": len(groups), "linked": sum(g["linked"] for g in groups),
            "accounts": sum(len(g["accounts"]) for g in groups), "ads": sum(g["adsCount"] for g in groups),
            "phones": len({p["digits"] for g in groups for p in g["phones"]}),
            "sharedPhones": sum(len(p["accounts"]) > 1 for g in groups for p in g["phones"]),
            "imageLinks": sum(ln["kind"] == "image" for g in groups for ln in g["links"])}


def find(groups: list[Obj], key: str) -> Obj | None:
    """Группа по своему ключу или по ключу любого её аккаунта(ссылка на аккаунт не устаревает при слиянии групп)."""
    k = slug(key)
    for g in groups:
        if g["key"] == k or any(slug(a["key"]) == k for a in g["accounts"]):
            return g
    return None


def graph(g: Obj) -> Obj:
    """Узлы и рёбра для схемы: аккаунты ↔ телефоны / БИН ↔ ЖК; связи "одно имя", "упоминание", "картинка" - ребро
    аккаунт - аккаунт."""
    nodes = [{"id": a["key"], "kind": "account", "label": a["account"], "platform": a["platform"], "weight": a["ads"]}
             for a in g["accounts"]]
    edges = []
    for p in g["phones"]:
        nid = "phone:" + p["digits"]
        nodes.append({"id": nid, "kind": "phone", "label": p["phone"], "weight": p["ads"]})
        edges += [{"a": k, "b": nid, "kind": "phone"} for k in p["accounts"]]
    for b in g["bins"]:
        nid = "bin:" + b["bin"]
        nodes.append({"id": nid, "kind": "bin", "label": f"БИН {b['bin']}", "weight": len(b["evidence"])})
        edges += [{"a": k, "b": nid, "kind": "bin"} for k in b["accounts"]]
    for c in g["complexes"]:
        nid = "complex:" + c["id"]
        nodes.append({"id": nid, "kind": "complex", "label": c["name"], "weight": c["ads"]})
        edges += [{"a": k, "b": nid, "kind": "complex"} for k in c["accounts"]]
    for ln in g["links"]:
        if ln["kind"] in ("handle", "mention", "image"):
            ks = ln["accounts"]
            edges += [{"a": ks[0], "b": o, "kind": ln["kind"]} for o in ks[1:]]
    return {"nodes": nodes, "edges": edges}


def ad_hint(groups: list[Obj], ad_id: str) -> Obj | None:
    """Для разбора публикации: её группа и номера - в скольких ещё публикациях и аккаунтах встречается каждый."""
    g = next((g for g in groups if ad_id in g["adIds"]), None)
    if g is None:
        return None
    own = [p for p in g["phones"] if ad_id in p["adIds"]]
    return {"groupKey": g["key"], "title": g["title"], "accounts": len(g["accounts"]), "ads": g["adsCount"],
            "linked": g["linked"], "phones": [{"phone": p["phone"], "digits": p["digits"], "whatsapp": p["whatsapp"],
                                               "otherAds": len(p["adIds"]) - 1, "accounts": len(p["accounts"])}
                                              for p in own],
            "links": [{"kind": ln["kind"], "label": ln["label"], "shown": ln["shown"], "accounts": len(ln["accounts"])}
                      for ln in g["links"]]}


# ---------- API ----------

@router.get("/api/sellers")
def sellers_list(linked: bool = Query(False, description="только группы из двух и более аккаунтов"),
                 db: Session = Depends(get_db)):
    """Продавцы: группы аккаунтов по общим контактам и картинкам, со счётчиками и доказательствами."""
    groups = compute(db)
    shown = [g for g in groups if g["linked"]] if linked else groups
    return {"groups": shown, "stats": stats(groups), "note": NOTE, "imageMaxDistance": IMAGE_MAX_DISTANCE,
            "computedAt": date.today().isoformat()}


@router.get("/api/sellers/by-ad/{ad_id}")
def sellers_by_ad(ad_id: str, db: Session = Depends(get_db)):
    """Подсказка разбора публикации: группа продавца, номера и где ещё они встречаются; аномальная цена(app.prices)."""
    from . import prices

    if db.get(models.Ad, ad_id) is None:
        raise HTTPException(404, "Публикация не найдена")
    rows = ad_rows(db)
    return {"seller": ad_hint(compute(db, rows), ad_id), "price": prices.for_ad(prices.analyze(rows), ad_id), "note": NOTE}


@router.get("/api/sellers/{key}")
def sellers_one(key: str, db: Session = Depends(get_db)):
    """Группа продавца по ключу группы или аккаунта: аккаунты, телефоны, БИН, ЖК, связи с доказательствами, схема и
    публикации."""
    rows = ad_rows(db)
    g = find(compute(db, rows), key)
    if g is None:
        raise HTTPException(404, "Продавец не найден")
    by_id = {a["id"]: a for a in rows}
    ads = [{"id": i, "platform": by_id[i]["platform"], "account": by_id[i]["account"], "date": by_id[i]["date"],
            "url": by_id[i]["url"], "complexId": by_id[i]["complexId"], "complexName": by_id[i]["complexName"],
            "snippet": re.sub(r"\s+", " ", by_id[i]["text"] or "")[:160]} for i in g["adIds"]]
    return {**g, "graph": graph(g), "ads": ads, "note": NOTE}


@router.get("/api/price-anomalies")
def price_anomalies(include_all: bool = Query(False, alias="all",
                                             description="все публикации с ценой за м², а не только ниже рынка"),
                    db: Session = Depends(get_db)):
    """Аномальная цена: цена за м² ниже медианы города на 30% и больше, отметка "100% оплата" - контекст, не балл."""
    from . import prices

    return prices.report(prices.analyze(ad_rows(db)), include_all=include_all)


@router.get("/api/developers/{dev_id}/affiliations")
def developer_affiliations(dev_id: str, db: Session = Depends(get_db)):
    """Проверка застройщика по БИН в наших данных: ЖК, публикации, уполномоченные компании, совпадения руководителя и
    телефонов с другими застройщиками, ссылки на официальные сервисы."""
    from . import affiliations

    if db.get(models.Developer, dev_id) is None:  # до расчёта групп продавцов по всем публикациям
        raise HTTPException(404, "Застройщик не найден")
    return affiliations.compute(db, dev_id, compute(db))
