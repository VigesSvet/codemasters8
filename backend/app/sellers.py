"""Кто продаёт: связанные продавцы(T-73), аномальная цена(T-74, app.prices), связи застройщика(T-75, app.affiliations).

Контакты публикации(contacts) - из текста, OCR картинок и речи роликов(абзацы OCR "Речь в ролике:" - источник
speech): казахстанские телефоны(ai.nlp.phones: "+7 777 700 77 33", "8(777) 700-77-33", "87777007733" → 10 цифр без
кода страны), WhatsApp-ссылки(wa.me/…, api.whatsapp.com/send?phone=…) - тот же номер, упоминания @аккаунтов, БИН / БСН
с подписью рядом и верной контрольной цифрой(bin_valid). У каждого контакта - дословный фрагмент вокруг находки.

Аккаунт - площадка + имя аккаунта(без регистра). У Krisha "специалист", "хозяин", "агент" - тип продавца, а не
аккаунт: такая публикация - отдельный аккаунт "krisha.kz / специалист · AD-…". Продавец(группа) - связная компонента
аккаунтов по общим признакам(links): один телефон или WhatsApp, один БИН, одно имя @аккаунта на разных площадках,
упоминание @аккаунта из наших данных, одна и та же картинка(dHash 64 бита, расстояние Хэмминга ≤ IMAGE_MAX_DISTANCE;
однотонные и почти пустые картинки не сравниваются). Хэши картинок кэшируются по SHA-256 файла в JSON рядом с
хранилищем(image_hashes.json): файл по хэшу не меняется, схема БД не трогается.

Это гипотеза для проверки, а не вывод: общий номер бывает у агентства, которое продаёт несколько ЖК, картинка - у
застройщика и перепродающего агента. Ни в балл риска, ни в решения аналитика группа не попадает.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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

Obj = Dict[str, Any]

NOTE = ("Связь по номеру телефона, БИН, имени аккаунта или общей картинке — гипотеза для проверки, а не вывод о "
        "нарушении: общий номер бывает у агентства, которое продаёт несколько ЖК. В балл риска не входит.")
IMAGE_MAX_DISTANCE = 6  # dHash: до 6 из 64 бит - та же картинка после пережатия, обрезки подписи, смены размера
IMAGE_MIN_STDDEV = 12.0  # однотонный фон, почти пустой кадр - у таких dHash совпадает случайно
QUOTE_SIDE = 60
EVIDENCE_MAX = 30  # фрагментов на признак
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


def norm_phone(raw: str) -> Optional[str]:
    """Казахстанский номер в любом написании → 10 цифр без кода страны; не номер РК → None."""
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 11 and d[0] in "78":
        d = d[1:]
    elif len(d) == 12 and d.startswith("77"):  # "+77 …" - лишняя 7 при наборе
        d = d[2:] if d[2] == "7" else d[1:]
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


def segments(text: Optional[str], ocr: Optional[str]) -> List[Tuple[str, str]]:
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


def contacts(text: Optional[str], ocr: Optional[str]) -> List[Contact]:
    """Контакты публикации по источникам; один и тот же контакт в одном источнике - один раз(первая находка)."""
    out: List[Contact] = []
    seen = set()

    def add(kind, value, shown, raw, src, s, a, b):
        if (kind, value, src) in seen:
            return
        seen.add((kind, value, src))
        out.append(Contact(kind, value, shown, raw, src, quote_at(s, a, b)))

    for src, s in segments(text, ocr):
        wa_spans = []
        for m in _WA.finditer(s):
            d = norm_phone(m.group(1))
            if d:
                wa_spans.append((m.start(), m.end()))
                add("wa", d, fmt_phone(d), m.group(0), src, s, m.start(), m.end())
        for raw, d, a, b in _phones(s):
            if any(x <= a < y for x, y in wa_spans):
                continue
            add("phone", d, fmt_phone(d), raw, src, s, a, b)
        for m in _BIN.finditer(s):
            if bin_valid(m.group(1)):
                add("bin", m.group(1), f"БИН {m.group(1)}", m.group(0), src, s, m.start(), m.end())
        for m in _MENTION.finditer(s):
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


def handle_of(account: str) -> Optional[str]:
    """Имя аккаунта соцсети без "@" и регистра: "@An.Groups" → "an.groups"; у Krisha имени нет."""
    a = (account or "").strip()
    return a[1:].lower() if a.startswith("@") and len(a) > 1 else None


def slug(key: str) -> str:
    """Ключ группы для адреса(/sellers?key=…): "instagram:@an.groups" → "instagram-an.groups"."""
    return re.sub(r"[^\w.]+", "-", key.lower()).strip("-")


# ---------- хэши картинок ----------

def dhash(path: Path) -> Optional[int]:
    """dHash 64 бита: оттенки серого 9×8, бит - "левый пиксель ярче правого". Однотонная картинка → None."""
    from PIL import Image, ImageStat

    with Image.open(path) as im:
        im.draft("L", (64, 64))
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


def image_hashes(db: Session) -> Dict[str, List[Tuple]]:
    """Публикация → [(dHash, sha256, ID файла)] её загруженных картинок и кадров видео. Кэш по SHA-256 - в image_hashes.json;
    файл, которого нет на диске(стартовый набор без медиа), пропускается."""
    try:
        import PIL  # noqa: F401
    except ImportError:  # pragma: no cover - Pillow в зависимостях
        return {}
    path = _cache_path()
    try:
        cache: Dict[str, Optional[int]] = json.loads(path.read_text()) if path.is_file() else {}
    except (OSError, ValueError):
        cache = {}
    M = models.MediaAsset
    rows = db.execute(select(M.ad_id, M.sha256, M.storage_key, M.id).where(
        M.status == "downloaded", M.kind.in_(("image", "frame")), M.storage_key.is_not(None))).all()
    out: Dict[str, List[Tuple]] = {}
    dirty = False
    for ad_id, sha, key, mid in rows:
        if not sha:
            continue
        if sha not in cache:
            try:
                p = media.path_of(key)
                if not p.is_file():
                    continue
                cache[sha] = dhash(p)
            except Exception as e:  # битый файл не роняет список продавцов
                log.warning("dHash %s: %s", key, e)
                cache[sha] = None
            dirty = True
        h = cache[sha]
        if h is not None and ad_id:
            out.setdefault(ad_id, []).append((h, sha, mid))
    if dirty:
        try:
            path.write_text(json.dumps(cache))
        except OSError as e:
            log.warning("кэш хэшей картинок не записан: %s", e)
    return out


# ---------- группы ----------

class _DSU:
    def __init__(self):
        self.p: Dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


@dataclass
class _Link:
    kind: str
    value: str
    shown: str
    accounts: set = field(default_factory=set)
    evidence: List[Obj] = field(default_factory=list)
    wa: bool = False  # номер встретился и WhatsApp-ссылкой


def _day(d: Optional[str]) -> Optional[str]:
    return (d or "")[:10] or None


def build(ads: Iterable[Obj], hashes: Optional[Dict[str, List[Tuple]]] = None,
          developers: Iterable[Obj] = ()) -> List[Obj]:
    """Группы продавцов по публикациям - чистая функция. Публикация: {id, platform, account, date, text, ocr, url?,
    complexId?, complexName?, linkConfirmed?}; hashes - публикация → [(dHash, sha256, ID файла?)]; developers - {id, name, bin}
    (БИН из рекламы называет застройщика). Группы - по числу аккаунтов, затем публикаций."""
    ads = list(ads)
    hashes = hashes or {}
    dev_by_bin = {d["bin"]: d for d in developers if d.get("bin")}
    dsu = _DSU()
    acc: Dict[str, Obj] = {}
    by_ad: Dict[str, str] = {}
    links: Dict[Tuple[str, str], _Link] = {}

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
            if len(ln.evidence) < EVIDENCE_MAX:
                ln.evidence.append({"adId": a["id"], "account": a["account"], "source": c.source, "quote": c.quote,
                                    "raw": c.raw, "whatsapp": c.kind == "wa"})
    # одно имя аккаунта на разных площадках; упоминание @аккаунта, который есть в данных
    by_handle: Dict[str, set] = {}
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
        if targets - ln.accounts:
            ln.accounts |= targets
        else:
            del links[(kind, value)]  # упомянут аккаунт вне наших данных или сам себя - связи нет
    # одна картинка у разных аккаунтов
    # (dHash, sha256, публикация, аккаунт, ID файла - для превью /api/media/{id}; в тестах его может не быть)
    flat = [(x[0], x[1], ad_id, by_ad[ad_id], x[2] if len(x) > 2 else None)
            for ad_id, hs in hashes.items() if ad_id in by_ad for x in hs]
    for i in range(len(flat)):
        hi, si, ai, ki, mi = flat[i]
        for j in range(i + 1, len(flat)):
            hj, sj, aj, kj, mj = flat[j]
            if ki == kj:
                continue
            dist = (hi ^ hj).bit_count()
            if dist <= IMAGE_MAX_DISTANCE:
                pair = tuple(sorted((ki, kj)))
                ln = link("image", f"{pair[0]}|{pair[1]}", "одна картинка")
                ln.accounts |= set(pair)
                if len(ln.evidence) < EVIDENCE_MAX:
                    ln.evidence.append({"adId": ai, "otherAdId": aj, "sha256": si, "otherSha256": sj, "mediaId": mi,
                                        "otherMediaId": mj, "distance": dist, "source": "image",
                                        "quote": f"dHash: расстояние {dist} из 64"})
    for ln in links.values():
        ks = sorted(ln.accounts)
        for o in ks[1:]:
            dsu.union(ks[0], o)

    comps: Dict[str, List[str]] = {}
    for k in acc:
        comps.setdefault(dsu.find(k), []).append(k)
    groups = []
    for ks in comps.values():
        kset = set(ks)
        g_links = [ln for ln in links.values() if ln.accounts & kset]
        g_ads = [a for k in ks for a in acc[k]["ads"]]
        accounts = sorted(({"key": k, "platform": acc[k]["platform"], "account": account_label(k, acc[k]["account"]),
                            "ads": len(acc[k]["ads"]), "firstSeen": min((_day(a["date"]) for a in acc[k]["ads"]), default=None),
                            "lastSeen": max((_day(a["date"]) or "" for a in acc[k]["ads"]), default=None) or None}
                           for k in ks), key=lambda x: (-x["ads"], x["key"]))
        primary = accounts[0]
        phones_ = [{"phone": ln.shown, "digits": ln.value, "whatsapp": ln.wa,
                    "accounts": sorted(ln.accounts), "ads": len({e["adId"] for e in ln.evidence}),
                    "evidence": ln.evidence} for ln in g_links if ln.kind == "phone"]
        phones_.sort(key=lambda p: (-len(p["accounts"]), -p["ads"], p["digits"]))
        bins = [{"bin": ln.value, "developerId": dev_by_bin.get(ln.value, {}).get("id"),
                 "developerName": dev_by_bin.get(ln.value, {}).get("name"), "accounts": sorted(ln.accounts),
                 "evidence": ln.evidence} for ln in g_links if ln.kind == "bin"]
        cx: Dict[str, Obj] = {}
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

def ad_rows(db: Session) -> List[Obj]:
    A, C = models.Ad, models.Complex
    names = dict(db.execute(select(C.id, C.name)).all())
    return [{"id": a.id, "platform": a.platform, "account": a.account, "date": a.date, "url": a.url, "text": a.text,
             "ocr": a.ocr, "price": a.price, "complexId": a.complex_id, "complexName": names.get(a.complex_id),
             "linkConfirmed": bool(a.link_confirmed_by)}
            for a in db.execute(select(A.id, A.platform, A.account, A.date, A.url, A.text, A.ocr, A.price,
                                       A.complex_id, A.link_confirmed_by)).all()]


def compute(db: Session) -> List[Obj]:
    devs = [{"id": d.id, "name": d.name, "bin": d.bin} for d in db.scalars(select(models.Developer))]
    return build(ad_rows(db), image_hashes(db), devs)


def stats(groups: List[Obj]) -> Obj:
    return {"groups": len(groups), "linked": sum(g["linked"] for g in groups),
            "accounts": sum(len(g["accounts"]) for g in groups), "ads": sum(g["adsCount"] for g in groups),
            "phones": len({p["digits"] for g in groups for p in g["phones"]}),
            "sharedPhones": sum(len(p["accounts"]) > 1 for g in groups for p in g["phones"]),
            "imageLinks": sum(ln["kind"] == "image" for g in groups for ln in g["links"])}


def find(groups: List[Obj], key: str) -> Optional[Obj]:
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


def ad_hint(groups: List[Obj], ad_id: str) -> Optional[Obj]:
    """Для разбора публикации: её группа и номера - в скольких ещё публикациях и аккаунтах встречается каждый."""
    g = next((g for g in groups if ad_id in g["adIds"]), None)
    if g is None:
        return None
    own = [p for p in g["phones"] if any(e["adId"] == ad_id for e in p["evidence"])]
    return {"groupKey": g["key"], "title": g["title"], "accounts": len(g["accounts"]), "ads": g["adsCount"],
            "linked": g["linked"], "phones": [{"phone": p["phone"], "digits": p["digits"], "whatsapp": p["whatsapp"],
                                               "otherAds": len({e["adId"] for e in p["evidence"]} - {ad_id}),
                                               "accounts": len(p["accounts"])} for p in own],
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
    groups = build(rows, image_hashes(db), [{"id": d.id, "name": d.name, "bin": d.bin} for d in db.scalars(select(models.Developer))])
    return {"seller": ad_hint(groups, ad_id), "price": prices.for_ad(prices.analyze(rows), ad_id), "note": NOTE}


@router.get("/api/sellers/{key}")
def sellers_one(key: str, db: Session = Depends(get_db)):
    """Группа продавца по ключу группы или аккаунта: аккаунты, телефоны, БИН, ЖК, связи с доказательствами, схема и
    публикации."""
    rows = ad_rows(db)
    groups = build(rows, image_hashes(db), [{"id": d.id, "name": d.name, "bin": d.bin} for d in db.scalars(select(models.Developer))])
    g = find(groups, key)
    if g is None:
        raise HTTPException(404, "Продавец не найден")
    by_id = {a["id"]: a for a in rows}
    ads = [{"id": i, "platform": by_id[i]["platform"], "account": by_id[i]["account"], "date": by_id[i]["date"],
            "url": by_id[i]["url"], "complexId": by_id[i]["complexId"], "complexName": by_id[i]["complexName"],
            "snippet": re.sub(r"\s+", " ", by_id[i]["text"] or "")[:160]} for i in g["adIds"]]
    return {**g, "graph": graph(g), "ads": ads, "note": NOTE}


@router.get("/api/price-anomalies")
def price_anomalies(all: bool = Query(False, description="все публикации с ценой за м², а не только ниже рынка"),
                    db: Session = Depends(get_db)):
    """Аномальная цена: цена за м² ниже медианы города на 30% и больше, отметка "100% оплата" - контекст, не балл."""
    from . import prices

    return prices.report(prices.analyze(ad_rows(db)), include_all=all)


@router.get("/api/developers/{dev_id}/affiliations")
def developer_affiliations(dev_id: str, db: Session = Depends(get_db)):
    """Проверка застройщика по БИН в наших данных: ЖК, публикации, уполномоченные компании, совпадения руководителя и
    телефонов с другими застройщиками, ссылки на официальные сервисы."""
    from . import affiliations

    out = affiliations.compute(db, dev_id, compute(db))
    if out is None:
        raise HTTPException(404, "Застройщик не найден")
    return out
