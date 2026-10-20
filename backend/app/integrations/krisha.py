"""Krisha.kz - HTML-сборщик выдачи. API для сбора у Krisha нет.

Страница читается обычным HTTP-запросом с честным User-Agent из настроек, без cookie(cookie "sl-session" защиты
SafeLine не сохраняется и не отправляется), с паузой KRISHA_MIN_DELAY_S + 0…2 с перед каждым запросом после первого,
не больше MAX_REQUESTS запросов за запуск; перенаправления - вручную и только на https://krisha.kz/…; robots.txt сайта
соблюдается(разбирается из ответа клиента подключения, кэш - сутки в состоянии подключения).

Данные страницы: присваивание `window.data = {…}` разбирается JSON-декодером - JavaScript не выполняется. Из снимка
отбрасываются сведения пользователя, настройки и служебные токены(user, config, ipInfo, app*, centrifuge…), остальное -
по белому списку. Поля карточек выдачи - из DOM(div.a-card[data-id]); описание, ЖК, застройщик и блок документов
карточки объявления - из DOM, остальное - из белого списка блока advert.

Если сайт отвечает проверкой доступа(HTTP 468 SafeLine, CAPTCHA, JS-challenge), это не пустая выдача: страница поиска -
AccessBlocked(сбор остановлен до ручного действия), карточка - запуск "частично" с данными из выдачи. Обход проверок не
выполняется: ни подмены браузера, ни повторной отправки cookie, ни решения проверок, ни прокси. Необязательный Playwright
(KRISHA_PLAYWRIGHT=1) открывает закрытую карточку с выключенным JavaScript(скрипт проверки не выполняется никогда) и
берёт только ответ самой навигации: проверка доступа в нём - та же остановка.

Документы на странице - заявление продавца, не проверенное основание: они хранятся только в extra/raw("documents"),
в текст публикации не попадают; createdAt/addedAt - отдельными полями, не как время публикации.

Публикации выдачи принимаются после прохода карточек: карточка получена - одна публикация с её данными(новая
публикация сразу с текстом карточки, без промежуточной версии "из выдачи"), не получена - данные выдачи. За запуск у
объявления не больше одной новой версии, и каждое объявление учитывается в счётчиках запуска один раз.

Ключ файла - само фото(photo_key: путь ссылки без размера и расширения), а не его место в галерее: продавец заменил
или удалил фото - новая версия, новое фото загружается, прежнее перестаёт быть текущим. Превью в выдаче заменено -
новая версия и перечитывание карточки.
"""
import contextlib
import hashlib
import json
import logging
import math
import random
import re
import weakref
from typing import TYPE_CHECKING, Any
from collections.abc import Callable, Iterable, Iterator
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import http
from ..ingest import content_hash
from .base import (AccessBlocked, BadConfig, CheckResult, ConfigError, Connector, MediaIn, Publication, RateLimited,
                   SourceError)

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("saq.integrations.krisha")

HOST = "krisha.kz"
BASE = f"https://{HOST}"
# Фото выдачи и карточек: живая выдача отдаёт их с krisha-photos.kcdn.online(window.data.photoHost), kcdn.kz - прежний
# CDN Krisha. Поддомены разрешены(http.host_allowed)
PHOTO_HOSTS = ("kcdn.online", "kcdn.kz", "krisha.kz")
BROWSER_HOSTS = ("krisha.kz", "kcdn.online", "kcdn.kz")
DEFAULT_SEARCH = "https://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/"
CONTROL_CARD = "https://krisha.kz/a/show/1009924986"
ROBOTS_URL = f"{BASE}/robots.txt"
# Служебные и запрещённые robots.txt параметры/разделы - в настройках не принимаются
DISALLOWED = ("till=", "sub_cat", "raion=", "utm_source", "gclid=", "from=adwords", "/a/show-map/", "/ajax/", "/captcha")
SUMMARY = ("title", "price", "address", "complex", "sellerType")
SELLER_LABELS = {"complex": "застройщик/ЖК", "company": "агентство", "owner": "хозяин", "specialist": "специалист"}
NO_SELLER = "продавец не указан"

MAX_REQUESTS = 30  # запросов к Krisha за один запуск(robots.txt, страницы поиска, карточки)
MAX_REDIRECTS = 3
TIMEOUT_S = 20.0
BODY_MAX = 5 * 1024 * 1024
ROBOTS_MAX = 32 * 1024
ROBOTS_TTL_S = 24 * 3600
CRAWL_DELAY_MAX = 60.0
MAX_SEARCH_URLS = 5
MAX_CARD_URLS = 10
MAX_PHOTOS = 10
BROWSER_TIMEOUT_MS = 30000
HEADERS = {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "ru"}
REDIRECTS = (301, 302, 303, 307, 308)

# Фабрика браузера: вызов возвращает контекстный менеджер с объектом браузера Playwright(sync API). None - Playwright
# из окружения(импорт - только внутри функции, когда KRISHA_PLAYWRIGHT=1); тесты подменяют её заглушкой
BROWSER_FACTORY: Callable[[], Any] | None = None

BLOCKED = ("Krisha.kz отвечает проверкой доступа ({reason}) — автоматический сбор остановлен, нужен ручной шаг; "
           "обход проверок не выполняется")
CARDS_BLOCKED = "Карточки закрыты проверкой доступа ({reason}) — сохранены данные из выдачи"
CHECK_CARDS_BLOCKED = ("Выдача доступна; карточки объявлений закрыты проверкой доступа ({reason}) — собираются данные "
                       "из выдачи")
NO_WINDOW_DATA = "Структура страницы Krisha изменилась — window.data не найден"
MARKUP_CHANGED = "Структура выдачи Krisha изменилась: в window.data {n} объявлений, разобрано 0"
LINKS_CHANGED = "Структура выдачи Krisha изменилась: на странице {n} ссылок на объявления, разобрано 0"
ROBOTS_DENIED = "robots.txt Krisha запрещает {url}"
NO_SELLER_MARKUP = ("Структура выдачи Krisha изменилась: тип продавца не распознан ни в одной карточке страницы — "
                    f"аккаунт публикаций «krisha.kz / {NO_SELLER}», если ЖК не указан")
NO_SEARCH = "Добавьте URL поиска Krisha"
TIME_UP = "Время запуска истекло — продолжим в следующий раз"
BUDGET_UP = f"Достигнут предел запросов к Krisha за запуск ({MAX_REQUESTS}) — продолжим в следующий раз"

_SPACES = re.compile(r"[\s   ]+")
_DIGITS = re.compile(r"^\d{1,16}$")
_CARD_PATH = re.compile(r"^/a/show/(\d{1,16})/?$")
_CARD_LINK = re.compile(r"""href=["'](?:https://(?:www\.)?krisha\.kz)?/a/show/(\d{1,16})""")
_SEARCH_PATH = re.compile(r"^/(prodazha|arenda)/[^?#]*$")
_HOT = re.compile(r"(?:^|[-_])(?:hot|urgent)(?:$|[-_])", re.I)
_USER_TYPE = re.compile(r"^user-([a-z]{2,32})$")
_DEVELOPER_TAIL = re.compile(r"(?:^|\s)от\s+застройщика(?:\s|$).*$", re.I | re.S)
_COMPLEX_PREFIX = re.compile(r"^\s*(?:ЖК|жилой комплекс)\s+", re.I)
_BLOCK_MARKERS = (("safeline", "SafeLine"), ("captcha", "CAPTCHA"))  # safelinechallenge, /.safeline/ - тоже safeline
_PHOTO_SIZE = re.compile(r"-(?:\d{2,5}x\d{2,5}|full)$", re.I)  # "1-750x470", "1-280x175", "1-full"
_PHOTO_FORMATS = frozenset({"webp", "jpg", "jpeg", "png", "avif"})  # каталог формата: /webp/90/…, /jpg/90/…

# Белые списки снимка window.data
SEARCH_FIELDS = ("nbTotal", "ids", "page")
ADVERT_FIELDS = ("id", "uuid", "title", "price", "addressTitle", "createdAt", "addedAt", "userType", "square", "rooms",
                 "floor", "storage", "status", "categoryAlias", "complexName", "isHot")
ADDRESS_FIELDS = ("country", "region", "city", "district", "street", "house_num")
MAP_FIELDS = ("lat", "lon", "lng", "zoom")
SCALAR_MAX = 2000


class _Exhausted(Exception):
    """Предел запросов за запуск исчерпан(MAX_REQUESTS)."""


class _Budget:
    """Счётчик запросов запуска и пауза между ними(crawl-delay из robots.txt - если больше KRISHA_MIN_DELAY_S);
    заметки, которые пишутся в запуск один раз."""

    def __init__(self) -> None:
        self.used = 0
        self.crawl_delay = 0.0
        self.time_up = False
        self.seller_noted = False


_BUDGETS: "weakref.WeakKeyDictionary[Any, _Budget]" = weakref.WeakKeyDictionary()


def _budget(ctx: "RunContext") -> _Budget:
    b = _BUDGETS.get(ctx)
    if b is None:
        b = _BUDGETS[ctx] = _Budget()
    return b


# ---------- разбор страниц ----------

def _clean(s: Any, limit: int = 500) -> str | None:
    """Однострочный текст: пробелы(включая неразрывные) схлопнуты; пусто - None."""
    if s is None:
        return None
    t = _SPACES.sub(" ", str(s)).strip()
    return t[:limit] or None


def _price(v: Any) -> str | None:
    """Цена одинаково для выдачи и карточки: "58 194 600 ₸"(знак тенге Krisha "〒" → "₸")."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return f"{int(v):,}".replace(",", " ") + " ₸" if v > 0 else None
    return _clean(str(v).replace("〒", "₸"), 64)


def _complex_name(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("name") or v.get("title")
    t = _clean(v, 255)
    return _clean(_COMPLEX_PREFIX.sub("", t), 255) if t else None


def _https(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    return u if u.lower().startswith("https://") and len(u) <= 2048 else None


def _scalar(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return v[:SCALAR_MAX]
    return None


def _has_window_data(body: str) -> bool:
    return any(True for _ in _assignments(body or ""))


def detect_block(status: int, headers: Any, body: str) -> str | None:
    """Причина проверки доступа или None. HTTP 468 - SafeLine всегда; 403/429/503 - признаки SafeLine/CAPTCHA в ответе
    или cookie sl-session; 200 - только HTML-страница с признаками проверки и без данных страницы(window.data): обычная
    страница Krisha с cookie sl-session или словом captcha в скриптах и robots.txt со строкой "Disallow: /captcha" -
    не проверка."""
    if status == 468:
        return "HTTP 468, SafeLine"
    if status not in (200, 403, 429, 503):
        return None
    low = (body or "")[:300_000].lower()
    marker = next((name for needle, name in _BLOCK_MARKERS if needle in low), None)
    if status == 200:
        html = any(tag in low for tag in ("<html", "<body", "<script", "<div"))
        return f"HTTP 200, {marker}" if marker and html and not _has_window_data(body) else None
    if marker is None:
        cookies: list[str] = []
        if headers is not None:
            if hasattr(headers, "get_list"):
                cookies = list(headers.get_list("set-cookie"))
            elif hasattr(headers, "items"):
                cookies = [str(v) for k, v in headers.items() if str(k).lower() == "set-cookie"]
        if any("sl-session=" in c.lower() for c in cookies):
            marker = "SafeLine"
    return f"HTTP {status}, {marker}" if marker else None


def _assignments(html: str) -> Iterator[int]:
    """Позиции начала значения после каждого "window.data ="(не "==", не "window.data.x = …")."""
    i = html.find("window.data")
    while i >= 0:
        j = i + len("window.data")
        while j < len(html) and html[j] in " \t\r\n":
            j += 1
        if html[j:j + 1] == "=" and html[j + 1:j + 2] != "=":
            j += 1
            while j < len(html) and html[j] in " \t\r\n":
                j += 1
            yield j
        i = html.find("window.data", i + 1)


def extract_window_data(html: str) -> dict | None:
    """Объект из присваивания `window.data = {…}` - JSON-декодером(raw_decode), без выполнения JavaScript: первое
    присваивание, значение которого - JSON-объект. Нет - None."""
    text = html or ""
    decoder = json.JSONDecoder()
    for pos in _assignments(text):
        try:
            obj, _ = decoder.raw_decode(text, pos)
        except (ValueError, RecursionError):  # RecursionError - вложенность глубже предела интерпретатора("[[[[…")
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _advert(a: Any) -> dict:
    """Объявление из window.data по белому списку: скалярные поля, адрес, координаты, ссылки фото(https)."""
    if not isinstance(a, dict):
        return {}
    out: dict = {}
    for k in ADVERT_FIELDS:
        if k in a:
            v = _scalar(a[k])
            if v is not None:
                out[k] = v
    if isinstance(a.get("address"), dict):
        addr = {k: _scalar(a["address"][k]) for k in ADDRESS_FIELDS if _scalar(a["address"].get(k)) is not None}
        if addr:
            out["address"] = addr
    if isinstance(a.get("map"), dict):
        pt = {k: a["map"][k] for k in MAP_FIELDS
              if isinstance(a["map"].get(k), (int, float)) and not isinstance(a["map"].get(k), bool)}
        if pt:
            out["map"] = pt
    if isinstance(a.get("complex"), (dict, str)):
        name = _complex_name(a["complex"])
        if name:
            out["complexName"] = name
    photos: list[str] = []
    for p in a["photos"] if isinstance(a.get("photos"), list) else []:
        src = _https(p.get("src") if isinstance(p, dict) else p)
        if src and src not in photos:
            photos.append(src)
            if len(photos) >= MAX_PHOTOS:
                break
    if photos:
        out["photos"] = [{"src": src} for src in photos]
    return out


def _ids(v: Any) -> list[str]:
    out: list[str] = []
    for x in v if isinstance(v, list) else []:
        s = str(x) if isinstance(x, (int, str)) and not isinstance(x, bool) else ""
        if _DIGITS.match(s) and s not in out:
            out.append(s)
    return out[:500]


def sanitize_window_data(d: Any) -> dict:
    """Снимок window.data без сведений пользователя и служебных данных: остаётся только белый список(search, advert,
    adverts и их поля) - user, config, ipInfo, app*, centrifuge, views, cookieDomain в снимок не попадают."""
    if not isinstance(d, dict):
        return {}
    out: dict = {}
    search = d.get("search")
    if isinstance(search, dict):
        s: dict = {}
        for k in SEARCH_FIELDS:
            v = search.get(k)
            if k == "ids":
                if "ids" in search:
                    s["ids"] = _ids(v)
                continue
            if isinstance(v, str):  # живая выдача: nbTotal - строка с разделителями разрядов("1 234")
                digits = _SPACES.sub("", v)
                v = int(digits) if _DIGITS.match(digits) else None
            if isinstance(v, int) and not isinstance(v, bool):
                s[k] = v
        out["search"] = s
    if isinstance(d.get("advert"), dict):
        out["advert"] = _advert(d["advert"])
    adverts = d.get("adverts")
    if isinstance(adverts, dict):
        adverts = list(adverts.values())
    if isinstance(adverts, list):
        out["adverts"] = [x for x in (_advert(a) for a in adverts[:500]) if x.get("id") is not None]
    return out


def markup_error(html: str, search: dict, cards: list) -> SourceError | None:
    """Объявления на странице есть(ids/nbTotal в window.data, а если их там нет - ссылки /a/show/…), а карточек a-card
    не разобрано - разметка изменилась: это ошибка, а не пустая выдача. Пустые ids/nbTotal 0 - выдача правда пуста."""
    if cards:
        return None
    n = len(search.get("ids") or []) or int(search.get("nbTotal") or 0)
    if n:
        return SourceError(MARKUP_CHANGED.format(n=n), retryable=False)
    if "ids" in search or "nbTotal" in search:
        return None
    links = len(set(_CARD_LINK.findall(html or "")))
    return SourceError(LINKS_CHANGED.format(n=links), retryable=False) if links else None


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html or "", "html.parser")


def _text_of(el) -> str | None:
    return _clean(el.get_text(" ", strip=True)) if el is not None else None


def _first(root, selectors: Iterable[str]):
    for sel in selectors:
        el = root.select_one(sel)
        if el is not None and el.get_text(strip=True):
            return el
    return None


def _seller_type(el) -> str | None:
    """Тип продавца карточки выдачи: класс user-<тип> у подвала div.a-card__footer(complex, owner, company,
    specialist - разметка живой выдачи), затем атрибут data-user-type."""
    for footer in el.select(".a-card__footer"):
        for cls in footer.get("class") or []:
            m = _USER_TYPE.match(cls)
            if m:
                return m.group(1)
    return el.get("data-user-type")


def _search_complex(el) -> str | None:
    """ЖК карточки выдачи. Живая разметка: блок .a-card__complex-info = подпись .a-card__complex-label("Новостройка" -
    не название), ссылка "ЖК …" на /complex/show/… и "от застройщика "…"". Сначала текст ссылки, затем текст блока без
    подписи и застройщика, затем прежние классы .a-card__complex / .a-card__complex-name."""
    info = el.select_one(".a-card__complex-info")
    if info is not None:
        link = info.select_one("a[href*='/complex/']") or info.select_one("a")
        name = _complex_name(_text_of(link))
        if name:
            return name
        label = _text_of(info.select_one(".a-card__complex-label")) or ""
        rest = _text_of(info) or ""
        if label and rest.startswith(label):
            rest = rest[len(label):]
        name = _complex_name(_DEVELOPER_TAIL.sub("", rest))
        if name:
            return name
    return _complex_name(_text_of(el.select_one(".a-card__complex, .a-card__complex-name")))


def parse_search(html: str) -> tuple[dict, list[dict]]:
    """Страница поиска → (очищенный window.data, карточки выдачи). Карточка: id, uuid, title, price, address, preview,
    complex, sellerType, photo, hot; повторы(блок "горячих" сверху) - по id, первая в силе. Поля - из DOM карточки,
    window.data(adverts) - запасной вариант."""
    data = sanitize_window_data(extract_window_data(html))
    by_id = {str(a["id"]): a for a in data.get("adverts", []) if a.get("id") is not None}
    cards: list[dict] = []
    seen: set[str] = set()
    for el in _soup(html).select("div.a-card[data-id]"):
        cid = str(el.get("data-id") or "").strip()
        if not _DIGITS.match(cid) or cid in seen:
            continue
        seen.add(cid)
        wd = by_id.get(cid, {})
        photo = None
        for img in el.select("img"):
            photo = next((u for u in (_https(img.get(a)) for a in ("src", "data-src", "data-original")) if u), None)
            if photo:
                break
        if photo is None and wd.get("photos"):
            photo = wd["photos"][0]["src"]
        classes = list(el.get("class") or [])
        for sub in el.select("[class]"):
            classes.extend(sub.get("class") or [])
        complex_name = _search_complex(el) or _complex_name(wd.get("complexName"))
        cards.append({
            "id": cid,
            "uuid": _clean(el.get("data-uuid"), 64),
            "title": _text_of(_first(el, (".a-card__title", "a.a-card__title", ".a-card__header a"))) or _clean(
                wd.get("title")),
            "price": _price(_text_of(el.select_one(".a-card__price"))) or _price(wd.get("price")),
            "address": _text_of(el.select_one(".a-card__subtitle")) or _clean(wd.get("addressTitle")),
            "preview": _text_of(el.select_one(".a-card__text-preview")),
            "complex": complex_name,
            "sellerType": _clean(_seller_type(el) or wd.get("userType"), 32),
            "photo": photo,
            "hot": any(_HOT.search(c) for c in classes) or bool(wd.get("isHot")),
        })
    return data, cards


def _documents(soup) -> list[str]:
    """Блок документов карточки(заявление продавца): тексты пунктов; элементы удаляются из дерева, чтобы не попасть
    в описание."""
    out: list[str] = []
    els = soup.select("[class*='documents__item'], [class*='documents-item'], [class*='documents'] li, "
                      "[data-name*='document']")
    for el in els:
        t = _clean(el.get_text(" ", strip=True), 500)
        if t and t not in out:
            out.append(t)
    for el in soup.select("[class*='documents'], [data-name*='document']"):
        el.decompose()
    return out[:20]


def _description(soup) -> str | None:
    el = _first(soup, (".js-description", ".offer__description .text", ".offer__description", ".a-text"))
    if el is None:
        return None
    lines = [_clean(x, 5000) for x in el.get_text("\n", strip=True).split("\n")]
    return "\n".join(x for x in lines if x)[:20000] or None


def _params(soup) -> tuple[dict, str | None, str | None]:
    """Характеристики карточки {подпись: значение}, ЖК и застройщик(по data-name или подписи)."""
    params: dict[str, str] = {}
    complex_name = developer = None
    for item in soup.select(".offer__info-item, .offer__parameters dl"):
        name = item.get("data-name") or ""
        title = _text_of(item.select_one(".offer__info-title, dt"))
        value = _text_of(item.select_one(".offer__advert-short-info, dd"))
        if not value:
            continue
        key = title or name
        if not key or re.search(r"view|просмотр", f"{key} {name}", re.I):
            continue
        if len(params) < 40:
            params[key[:100]] = value
        low = f"{name} {title or ''}".lower()
        if complex_name is None and ("complex" in name or "жилой комплекс" in low):
            complex_name = _complex_name(value)
        if developer is None and ("builder" in low or "developer" in low or "застройщик" in low):
            developer = value
    return params, complex_name, developer


def parse_card(html: str) -> dict:
    """Карточка объявления → поля по белому списку блока advert с запасными вариантами из DOM; описание, ЖК, застройщик
    и документы - из DOM. Документы - только в "documents"(в описание не попадают)."""
    advert = sanitize_window_data(extract_window_data(html)).get("advert") or {}
    soup = _soup(html)
    documents = _documents(soup)
    params, dom_complex, developer = _params(soup)
    photos = [p["src"] for p in advert.get("photos", [])]
    if not photos:
        for el in soup.select("[data-photo-url], .offer__gallery img, .gallery__main img"):
            src = _https(el.get("data-photo-url") or el.get("src") or el.get("data-src"))
            if src and src not in photos:
                photos.append(src)
    point = advert.get("map") or {}
    cid = str(advert.get("id")) if advert.get("id") is not None else None
    return {
        "id": cid if cid and _DIGITS.match(cid) else None,
        "uuid": _clean(advert.get("uuid"), 64),
        "title": _clean(advert.get("title")) or _text_of(soup.select_one(".offer__advert-title h1, h1")),
        "price": _price(advert.get("price")) or _price(_text_of(soup.select_one(".offer__price"))),
        "address": _clean(advert.get("addressTitle")) or _text_of(soup.select_one(".offer__location span, "
                                                                                  ".offer__location")),
        "complex": dom_complex or _complex_name(advert.get("complexName")),
        "sellerType": _clean(advert.get("userType"), 32),
        "description": _description(soup),
        "developer": developer,
        "documents": documents,
        "photos": photos[:MAX_PHOTOS],
        "createdAt": _clean(advert.get("createdAt"), 40),
        "addedAt": _clean(advert.get("addedAt"), 40),
        "params": params,
        "lat": point.get("lat"),
        "lon": point.get("lon", point.get("lng")),
        "advert": advert,
    }


# ---------- ссылки и настройки ----------

def _is_krisha(url: str) -> bool:
    """https://krisha.kz без логина и с портом по умолчанию - по разбору httpx, тем же, что у запроса: urlsplit иначе
    понимает пробелы, управляющие символы и "\\" в ссылке."""
    try:
        u = httpx.URL(url)
        return u.scheme == "https" and u.host == HOST and not u.userinfo and u.port is None
    except (httpx.InvalidURL, UnicodeError):  # IDNAError - подкласс UnicodeError
        return False


def card_url(external_id: str) -> str:
    return f"{BASE}/a/show/{external_id}"


def _card_id(url: str) -> str | None:
    try:
        m = _CARD_PATH.match(urlsplit(url).path)
    except ValueError:
        return None
    return m.group(1) if m else None


def _page_url(url: str, page: int) -> str:
    """Страница N выдачи: параметр page с сохранением остальных параметров запроса."""
    if page <= 1:
        return url
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != "page"] + [("page", str(page))]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def _norm_url(field: str, value: str) -> str:
    v = value.strip()
    try:
        p = urlsplit(v)
        port = p.port
    except ValueError:
        raise ConfigError(field, f"Некорректная ссылка: {v[:80]}", value) from None
    host = (p.hostname or "").lower()
    if host == f"www.{HOST}":
        host = HOST
    if p.scheme.lower() != "https" or host != HOST or "@" in p.netloc or port not in (None, 443):
        raise ConfigError(field, f"Нужна ссылка https://krisha.kz/…: {v[:80]}", value)
    # "/prodazha/../my/"(в том числе %2e%2e): httpx схлопывает такие сегменты при запросе, а проверка раздела и
    # robots.txt видели бы исходную строку - такие ссылки не принимаются
    path = unquote(p.path)
    if "\\" in path or any(seg in (".", "..") for seg in path.split("/")):
        raise ConfigError(field, f"Ссылка Krisha с «.» или «..» в пути не принимается: {v[:80]}", value)
    low = v.lower()
    bad = next((d for d in DISALLOWED if d in low), None)
    if bad:
        raise ConfigError(field, f"Ссылка Krisha с «{bad.strip('/=')}» не принимается (служебный или закрытый "
                                 f"robots.txt адрес): {v[:80]}", value)
    if field == "cardUrls":
        m = _CARD_PATH.match(p.path)
        if not m:
            raise ConfigError(field, f"Ссылка на объявление Krisha — https://krisha.kz/a/show/<номер>: {v[:80]}", value)
        return card_url(m.group(1))
    if not _SEARCH_PATH.match(p.path):
        raise ConfigError(field, f"Ссылка поиска Krisha — раздел /prodazha/… или /arenda/…: {v[:80]}", value)
    return urlunsplit(("https", HOST, p.path, p.query, ""))


# ---------- сеть ----------

def _client(ctx: "RunContext") -> httpx.Client:
    """Клиент Krisha: честный User-Agent, без cookie, без перенаправлений, только https://krisha.kz."""
    return ctx.client(timeout=TIMEOUT_S, headers=dict(HEADERS), base_url=BASE, follow_redirects=False)


def _delay(ctx: "RunContext", b: _Budget) -> float:
    return max(float(ctx.settings.krisha_min_delay_s), b.crawl_delay) + random.uniform(0, 2)


def _count_request(ctx: "RunContext") -> None:
    """Учесть запрос: предел за запуск и вежливая пауза перед каждым запросом после первого."""
    b = _budget(ctx)
    if b.used >= MAX_REQUESTS:
        raise _Exhausted()
    if b.used:
        ctx.sleep(_delay(ctx, b))
    b.used += 1


def _read(resp: httpx.Response, url: str) -> bytes:
    body = bytearray()
    for chunk in resp.iter_bytes():
        body += chunk
        if len(body) > BODY_MAX:
            raise SourceError(f"Страница Krisha больше {BODY_MAX // (1024 * 1024)} МБ ({http.redact_url(url)})",
                              retryable=False)
    return bytes(body)


def fetch(ctx: "RunContext", client: httpx.Client, url: str) -> httpx.Response:
    """GET страницы Krisha: перенаправления вручную(не больше MAX_REDIRECTS, каждое - только на https://krisha.kz/…),
    пауза и предел запросов; сеть - повторяемая SourceError. Cookie не сохраняются и не отправляются."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _is_krisha(current):
            raise SourceError(f"Krisha.kz перенаправляет на другой адрес ({http.redact_url(current)}) — переход не "
                              "выполняется", retryable=False)
        _count_request(ctx)
        try:
            with client.stream("GET", current) as resp:
                location = resp.headers.get("location")
                if resp.status_code in REDIRECTS and location:
                    # тем же разбором, что у запроса; некорректный Location httpx отклоняет ещё в send
                    # (RemoteProtocolError - сетевая ошибка ниже)
                    current = str(resp.request.url.join(location))
                    continue
                content = _read(resp, current)
                headers = [(k, v) for k, v in resp.headers.multi_items()
                           if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                return httpx.Response(resp.status_code, headers=headers, content=content, request=resp.request)
        except http.DownloadError as e:  # клиент привязан к krisha.kz: запрос к другому хосту отклонён до отправки
            raise SourceError(e.detail, retryable=False) from None
        except httpx.TimeoutException:
            raise SourceError(f"Krisha.kz не ответил вовремя ({http.redact_url(current)})", retryable=True) from None
        except httpx.HTTPError as e:
            raise SourceError(f"Сетевая ошибка при обращении к Krisha.kz ({type(e).__name__}, "
                              f"{http.redact_url(current)})", retryable=True) from None
    raise SourceError(f"Слишком много перенаправлений Krisha.kz (больше {MAX_REDIRECTS}): {http.redact_url(url)}",
                      retryable=False)


def _status_error(resp: httpx.Response, what: str) -> RateLimited | SourceError | None:
    """Ответ не 200(проверка доступа уже исключена) → ошибка источника; 200 - None."""
    code = resp.status_code
    if code == 200:
        return None
    if code == 429 or (code == 503 and resp.headers.get("retry-after")):
        secs = http.retry_after(resp.headers, 900)
        return RateLimited(secs, f"Krisha.kz ограничил частоту запросов (HTTP {code}) — повтор через {secs:.0f} с")
    return SourceError(f"Krisha.kz ответил HTTP {code} ({what})", retryable=code >= 500)


def robots(ctx: "RunContext", client: httpx.Client) -> RobotFileParser:
    """Правила robots.txt Krisha: GET через клиент подключения → RobotFileParser.parse(никогда .read()); текст
    (не больше 32 КБ) кэшируется на сутки в состоянии подключения. 404/410 и другие 4xx - правил нет(RFC 9309);
    5xx и сеть - ошибка с повтором(до получения правил сбор не идёт); проверка доступа - AccessBlocked."""
    rp = RobotFileParser(ROBOTS_URL)
    cached = ctx.state.get("robots") if isinstance(ctx.state.get("robots"), dict) else None
    now = ctx.now()
    if cached and isinstance(cached.get("text"), str) and isinstance(cached.get("at"), (int, float)) \
            and 0 <= now.timestamp() - cached["at"] < ROBOTS_TTL_S:
        text = cached["text"]
    else:
        resp = fetch(ctx, client, ROBOTS_URL)
        reason = detect_block(resp.status_code, resp.headers, resp.text)
        if reason:
            raise AccessBlocked(BLOCKED.format(reason=reason))
        code = resp.status_code
        if code == 429 or code >= 500:
            err = _status_error(resp, "robots.txt")
            if isinstance(err, RateLimited):
                raise err
            raise SourceError(f"robots.txt Krisha недоступен (HTTP {code}) — сбор отложен до получения правил",
                              retryable=True)
        text = resp.text[:ROBOTS_MAX] if code == 200 else ""
        ctx.save_state(robots={"text": text, "at": now.timestamp(), "status": code})
    rp.parse(text.splitlines())
    delay = rp.crawl_delay(ctx.settings.user_agent)
    if delay:
        _budget(ctx).crawl_delay = min(float(delay), CRAWL_DELAY_MAX)
    return rp


def _allowed(ctx: "RunContext", rp: RobotFileParser, url: str) -> bool:
    return rp.can_fetch(ctx.settings.user_agent, url)


# ---------- браузер(необязательно) ----------

def _browser_enabled(ctx: "RunContext") -> tuple[bool, str | None]:
    """(включён ли Playwright, заметка если включён в настройках, но недоступен)."""
    if not ctx.settings.krisha_playwright:
        return False, None
    if BROWSER_FACTORY is not None:
        return True, None
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "KRISHA_PLAYWRIGHT=1, но Playwright не установлен — проверка карточек браузером не выполнялась"
    return True, None


@contextlib.contextmanager
def _playwright_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def _route_guard(route) -> None:
    """Браузер загружает только документы(нужен лишь ответ навигации: стили, картинки и прочее - лишние запросы к
    сайту мимо счётчика запуска) и только по https с BROWSER_HOSTS(krisha.kz, CDN фото и их поддомены); остальные
    запросы прерываются."""
    req = route.request
    try:
        u = urlsplit(req.url)
        scheme, host = u.scheme.lower(), u.hostname or ""
    except ValueError:
        scheme, host = "", ""
    if req.resource_type == "document" and scheme == "https" and http.host_allowed(host, BROWSER_HOSTS):
        route.continue_()
    else:
        route.abort()


def browser_card(ctx: "RunContext", url: str) -> tuple[str | None, str | None]:
    """Карточка через Playwright: (html, None) или(None, причина). JavaScript выключен - скрипт проверки доступа не
    выполняется; хранилище не сохраняется; используется только ответ самой навигации - проверка доступа в нём(или
    перенаправление с krisha.kz) означает остановку, содержимое после неё не берётся."""
    _count_request(ctx)
    factory = BROWSER_FACTORY or _playwright_browser
    with factory() as browser:
        bctx = browser.new_context(user_agent=ctx.settings.user_agent, java_script_enabled=False,
                                   service_workers="block", locale="ru-RU", extra_http_headers={"Accept-Language": "ru"})
        try:
            bctx.route("**/*", _route_guard)
            page = bctx.new_page()
            try:
                response = page.goto(url, wait_until="commit", timeout=BROWSER_TIMEOUT_MS)
                if response is None:
                    return None, "браузер не получил ответ"
                body = response.text()
                reason = detect_block(response.status, response.headers, body)
                if reason:
                    return None, f"{reason}, браузер"
                if not _is_krisha(str(response.url)) or _card_id(str(response.url)) != _card_id(url):
                    return None, f"перенаправление на {http.redact_url(str(response.url))}, браузер"
                if response.status != 200:
                    return None, f"HTTP {response.status}, браузер"
                return body, None
            finally:
                page.close()
        finally:
            bctx.close()


# ---------- подключение ----------

def _account(s: dict) -> str:
    who = s.get("complex") or SELLER_LABELS.get(s.get("sellerType") or "", NO_SELLER)
    return f"krisha.kz / {who}"


def photo_key(eid: str, src: str, *, cover: bool = False) -> str:
    """Ключ файла фото - по самому фото, а не по его месту в галерее: путь ссылки без хоста, query, каталога формата
    (/webp/…), расширения и размера(-750x470, -280x175, -full). Продавец заменил или удалил фото - набор ключей
    меняется: новая версия, новое фото загружается, прежнее перестаёт быть текущим; тот же снимок в другом размере или
    на другом CDN - тот же ключ. Порядок фото - позиция файла в публикации. Превью выдачи(krisha:{id}:c:…) и фото
    карточки(krisha:{id}:p:…) - разные файлы: это разные размеры одного снимка, превью не подменяет фото карточки."""
    try:
        path = unquote(urlsplit(src).path)
    except ValueError:
        path = ""
    segs = [s for s in path.split("/") if s]
    if len(segs) > 1 and segs[0].lower() in _PHOTO_FORMATS:
        segs = segs[1:]
    if segs:
        stem = segs[-1].rsplit(".", 1)[0] or segs[-1]
        segs[-1] = _PHOTO_SIZE.sub("", stem) or stem
    ident = "/".join(segs) or src
    return f"krisha:{eid}:{'c' if cover else 'p'}:{hashlib.sha256(ident.encode()).hexdigest()[:16]}"


def _is_cover_key(eid: str, key: str) -> bool:
    """Ключ превью выдачи; "krisha:{id}:0" - превью по прежней схеме ключей(по позиции)."""
    return key.startswith(f"krisha:{eid}:c:") or key == f"krisha:{eid}:0"


def _summary(card: dict) -> dict:
    return {k: card.get(k) for k in SUMMARY}


def _join(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p)


class KrishaConnector(Connector):
    code = "krisha"
    kind = "collector"
    platform = "krisha"
    group = "collectors"
    name = "Krisha.kz"
    via = "HTML-сборщик выдачи (API нет)"
    required_env = ()
    config_fields = ("searchUrls", "cardUrls")
    default_interval_min = 360
    interval_bounds = (120, 10080)
    limitation = ("API нет. Если сайт закрывает страницы проверкой доступа, сбор останавливается до ручного действия — "
                  "обход проверок не выполняется. Документы на странице — заявление продавца, не проверенное "
                  "основание.")

    # --- настройки ---

    def normalize_item(self, field_name: str, value: str) -> str:
        return _norm_url(field_name, value)

    def validate_config(self, cfg: dict) -> dict:
        out = super().validate_config(cfg)
        for key, cap, what in (("searchUrls", MAX_SEARCH_URLS, "ссылок поиска"),
                               ("cardUrls", MAX_CARD_URLS, "ссылок на объявления")):
            if len(out.get(key) or []) > cap:
                raise ConfigError(key, f"Не больше {cap} {what} Krisha", (cfg or {}).get(key))
        return out

    # --- проверка ---

    def check(self, ctx: "RunContext") -> CheckResult:
        search_url = (ctx.config.get("searchUrls") or [DEFAULT_SEARCH])[0]
        control = (ctx.config.get("cardUrls") or [CONTROL_CARD])[0]
        with _client(ctx) as client:
            try:
                rp = robots(ctx, client)
                if not _allowed(ctx, rp, search_url):
                    raise BadConfig(ROBOTS_DENIED.format(url=search_url))
                resp = fetch(ctx, client, search_url)
            except _Exhausted:  # pragma: no cover - проверка делает не больше трёх запросов
                raise SourceError(BUDGET_UP, retryable=False) from None
            reason = detect_block(resp.status_code, resp.headers, resp.text)
            if reason:
                raise AccessBlocked(BLOCKED.format(reason=reason))
            err = _status_error(resp, f"поиск {http.redact_url(search_url)}")
            if err is not None:
                raise err
            data, cards = parse_search(resp.text)
            search = data.get("search")
            if not isinstance(search, dict):
                raise SourceError(NO_WINDOW_DATA, retryable=False)
            err = markup_error(resp.text, search, cards)
            if err is not None:
                raise err
            facts: dict = {"search": "ok", "searchUrl": search_url, "searchTotal": search.get("nbTotal"),
                           "searchCards": len(cards), "cardUrl": control}
            total = search.get("nbTotal")
            found = (f"объявлений в выдаче: {total}, разобрано на странице: {len(cards)}" if total is not None
                     else f"разобрано объявлений на странице: {len(cards)}, всего в выдаче — не указано")
            if not _allowed(ctx, rp, control):
                facts["card"] = "robots"
                return CheckResult(True, f"Выдача доступна ({found}); карточки объявлений закрыты robots.txt — "
                                         "собираются данные из выдачи", facts)
            card_reason, html = self._fetch_card(ctx, client, control, facts)
        if card_reason:
            facts.update(card="access_check", cardReason=card_reason)
            return CheckResult(True, CHECK_CARDS_BLOCKED.format(reason=card_reason) + f" ({found})", facts)
        if html is None:
            return CheckResult(True, f"Выдача доступна ({found}); контрольная карточка не загрузилась: "
                                     f"{facts.get('cardError')}", facts)
        card = parse_card(html)
        if not (card["id"] or card["description"]):
            facts.update(card="error", cardError="данные объявления не найдены")
            return CheckResult(True, f"Выдача доступна ({found}); структура карточки объявления не распознана", facts)
        facts["card"] = "ok"
        return CheckResult(True, f"Выдача и карточки объявлений доступны ({found})", facts)

    def _fetch_card(self, ctx: "RunContext", client: httpx.Client, url: str, facts: dict, *,
                    browser_first: bool = False) -> tuple[str | None, str | None]:
        """Карточка: (причина проверки доступа, None) | (None, html) | (None, None) - ошибка(в facts["cardError"]).
        Закрыта проверкой - при KRISHA_PLAYWRIGHT=1 одна попытка через браузер с выключенным JavaScript."""
        reason: str | None = None
        if not browser_first:
            try:
                resp = fetch(ctx, client, url)
            except SourceError as e:
                facts.update(card="error", cardError=e.detail)
                return None, None
            reason = detect_block(resp.status_code, resp.headers, resp.text)
            if not reason:
                err = _status_error(resp, f"карточка {http.redact_url(url)}")
                if isinstance(err, RateLimited):
                    raise err
                if err is not None:
                    facts.update(card="error", cardError=err.detail)
                    return None, None
                return None, resp.text
        on, why = _browser_enabled(ctx)
        if why:
            ctx.note(why, "warn")
        facts["playwright"] = "выполнялась" if on else "не выполнялась"
        if not on:
            return reason, None
        try:
            html, breason = browser_card(ctx, url)
        except _Exhausted:
            raise
        except Exception as e:
            log.warning("Krisha: браузерная проверка карточки не выполнена (%s)", type(e).__name__)
            ctx.note(f"Проверка карточки через браузер не выполнена ({type(e).__name__})", "warn")
            return reason or "браузер недоступен", None
        if breason:
            return breason, None
        return None, html

    # --- сбор ---

    def collect(self, ctx: "RunContext") -> None:
        s = ctx.settings
        search_urls = list(ctx.config.get("searchUrls") or [])
        configured = [cid for cid in (_card_id(u) for u in ctx.config.get("cardUrls") or []) if cid]
        if not search_urls:
            raise BadConfig(NO_SEARCH)
        with _client(ctx) as client:
            rp = robots(ctx, client)
            allowed = [u for u in search_urls if _allowed(ctx, rp, u)]
            for u in search_urls:
                if u not in allowed:
                    ctx.item_failed(ROBOTS_DENIED.format(url=u))
            if not allowed:
                raise BadConfig(ROBOTS_DENIED.format(url=search_urls[0]))
            found: dict[str, dict] = {}  # ID → карточка выдачи этого запуска(первое появление)
            changed: list[str] = []  # новые и изменившиеся в выдаче
            search_only: list[str] = []  # без изменений, версии карточки ещё нет
            # данные выдачи, отложенные до прохода карточек: Publication - принять, None - только "видели"
            pending: dict[str, Publication | None] = {}
            try:
                try:
                    done = self._search_pass(ctx, client, rp, allowed, s.krisha_max_pages, found, changed,
                                             search_only, pending)
                except _Exhausted:
                    ctx.mark_partial(BUDGET_UP)
                    return
                if not done:
                    return
                # очередь карточек: изменившиеся в выдаче; ссылки из настроек, у которых ещё нет версии карточки;
                # объявления только с данными выдачи; затем перечитывание ссылок из настроек - если хватит предела
                watched: list[str] = []
                for cid in configured:
                    k = ctx.known(cid)
                    if k is not None and k.extra.get("source") == "card":
                        watched.append(cid)
                fresh = [cid for cid in configured if cid not in watched]
                queue = list(dict.fromkeys(changed + fresh + search_only + watched))
                try:
                    self._card_pass(ctx, client, rp, queue[:max(int(s.krisha_max_cards), 0)], found,
                                    set(configured), pending)
                except _Exhausted:
                    ctx.mark_partial(BUDGET_UP)
            finally:  # прочитанная выдача принимается и при остановке запуска(предел, время, ошибка карточки)
                self._flush(ctx, pending)

    def _time_up(self, ctx: "RunContext") -> bool:
        if ctx.stopping() or ctx.time_left() < 60:
            b = _budget(ctx)
            if not b.time_up:
                b.time_up = True
                ctx.mark_partial(TIME_UP)
            return True
        return False

    def _flush(self, ctx: "RunContext", pending: dict) -> None:
        """Отложенные данные выдачи(карточка в этом запуске не получена): приём публикаций и отметки "видели" в
        порядке выдачи. Воркер останавливается - остальное будет прочитано в следующий раз."""
        for eid in list(pending):
            if ctx.stopping():
                self._time_up(ctx)
                return
            pub = pending.pop(eid)
            if pub is None:
                ctx.touch(eid)
            else:
                ctx.ingest(pub)

    def _search_pass(self, ctx: "RunContext", client: httpx.Client, rp: RobotFileParser, urls: list[str],
                     max_pages: int, found: dict, changed: list, search_only: list, pending: dict) -> bool:
        """Страницы поиска 1…max_pages каждой ссылки. False - запуск остановлен(время)."""
        b = _budget(ctx)
        for url in urls:
            per_page = 0
            for page in range(1, max(int(max_pages), 1) + 1):
                if self._time_up(ctx):
                    return False
                page_url = _page_url(url, page)
                if page > 1 and not _allowed(ctx, rp, page_url):
                    ctx.note(f"robots.txt Krisha запрещает страницу {page} выдачи — дальше не листаем", "warn")
                    break
                resp = fetch(ctx, client, page_url)
                reason = detect_block(resp.status_code, resp.headers, resp.text)
                if reason:
                    raise AccessBlocked(BLOCKED.format(reason=reason))
                err = _status_error(resp, f"поиск {http.redact_url(page_url)}")
                if isinstance(err, RateLimited):
                    raise err
                if err is not None:
                    if page == 1:  # ссылка из настроек не открылась - ошибка записи(все ссылки - ошибка запуска)
                        ctx.item_failed(f"Поиск Krisha {http.redact_url(page_url)}: {err}")
                    else:
                        ctx.note(f"Страница {page} выдачи Krisha не загрузилась: {err}", "warn")
                    break
                data, cards = parse_search(resp.text)
                search = data.get("search")
                fresh = [c for c in cards if c["id"] not in found]
                if page > 1 and not fresh:  # выдача закончилась(или сайт вернул уже прочитанную страницу)
                    break
                if not isinstance(search, dict):
                    if page > 1:
                        ctx.note(f"Страница {page} выдачи Krisha без window.data — дальше не листаем", "warn")
                        break
                    raise SourceError(NO_WINDOW_DATA, retryable=False)
                ids = search.get("ids") or []
                bad = markup_error(resp.text, search, cards)
                if bad is not None:
                    raise bad
                if cards and not any(c["sellerType"] for c in cards) and not b.seller_noted:
                    b.seller_noted = True
                    ctx.note(NO_SELLER_MARKUP, "warn")
                for card in fresh:  # ID карточек страницы не повторяются(parse_search)
                    found[card["id"]] = card
                    kind = self._search_item(ctx, card, url, pending)
                    if kind == "changed":
                        changed.append(card["id"])
                    elif kind == "search_only":
                        search_only.append(card["id"])
                per_page = per_page or len(ids) or len(cards)
                total = search.get("nbTotal")
                if not cards or (isinstance(total, int) and per_page and page * per_page >= total):
                    break
        return True

    def _search_item(self, ctx: "RunContext", card: dict, search_url: str, pending: dict) -> str | None:
        """Карточка выдачи → отложенный приём(pending). Итог: "changed" - новая или изменилась(в том числе заменено
        превью), "search_only" - без изменений, версии карточки ещё нет; None - без изменений при версии карточки или
        запись другого источника."""
        eid = card["id"]
        s = _summary(card)
        raw = {"source": "search", "searchUrl": search_url,
               "card": {k: card.get(k) for k in ("id", "uuid", "title", "price", "address", "preview", "complex",
                                                 "sellerType", "hot", "photo")}}
        k = ctx.known(eid)
        if k is None or k.extra.get("source") != "card" or k.collector != ctx.code:
            pub = Publication(platform="krisha", external_id=eid, account=_account(s), url=card_url(eid),
                              text=_join(card.get("title"), card.get("address"), card.get("preview")),
                              collected_at=ctx.now(), media=self._cover(ctx, eid, card.get("photo")),
                              price=s.get("price"), extra={**s, "source": "search"}, raw=raw)
            if k is not None and k.collector != ctx.code:  # собрана другим источником: заметка, не перезаписывается
                ctx.ingest(pub)
                return None
            pending[eid] = pub
            return "changed" if k is None or content_hash(pub) != k.content_hash else "search_only"
        # превью заменено другим фото(не тот же снимок в другом размере) - у карточки, скорее всего, тоже новые фото:
        # новое превью принимается сразу, карточка ставится в очередь на перечитывание. Превью в выдаче нет -
        # сохранённое остаётся(как в проходе карточек)
        cover = self._cover(ctx, eid, card.get("photo"))
        new_cover = bool(cover) and [m.key for m in cover] != [key for key in k.media_keys if _is_cover_key(eid, key)]
        if not new_cover and {f: k.extra.get(f) for f in SUMMARY} == s:
            pending[eid] = None
            return None
        # выдача изменилась(цена, заголовок, адрес, превью…), а версия карточки уже есть: описание и фото карточки
        # остаются, заголовок и адрес в тексте - новые(текст собирается так же, как в проходе карточек)
        text = _join(s.get("title"), s.get("address"), k.extra.get("description")) if "description" in k.extra \
            else k.text
        media = [MediaIn(key=key) for key in k.media_keys if not (new_cover and _is_cover_key(eid, key))]
        pending[eid] = Publication(platform="krisha", external_id=eid, account=_account(s), url=card_url(eid),
                                   text=text, collected_at=ctx.now(), media=(cover if new_cover else []) + media,
                                   price=s.get("price"), extra={**k.extra, **s}, raw={**raw, "merged": True})
        return "changed"

    @staticmethod
    def _cover(ctx: "RunContext", eid: str, photo: str | None) -> list[MediaIn]:
        """Превью из выдачи: ключ krisha:{id}:c:… по самому фото(photo_key), роль cover."""
        if not photo:
            return []
        key = photo_key(eid, photo, cover=True)
        return [MediaIn(key=key, kind="image", role="cover", origin_url=photo,
                        fetch=lambda: ctx.download_media(photo, key=key, role="cover", allowed_hosts=PHOTO_HOSTS))]

    def _card_pass(self, ctx: "RunContext", client: httpx.Client, rp: RobotFileParser, ids: list[str],
                   found: dict, configured: set, pending: dict) -> None:
        """Карточки объявлений: первая проверка доступа - "частично", карточки в этом запуске больше не запрашиваются.
        Карточка получена - её публикация заменяет отложенные данные выдачи."""
        robots_noted = False
        use_browser = False
        for eid in ids:
            if self._time_up(ctx):
                return
            url = card_url(eid)
            if not _allowed(ctx, rp, url):
                if eid in configured:
                    ctx.item_failed(ROBOTS_DENIED.format(url=url))
                elif not robots_noted:
                    robots_noted = True
                    ctx.mark_partial("robots.txt Krisha запрещает карточки объявлений — сохранены данные из выдачи")
                continue
            facts: dict = {}
            reason, html = self._fetch_card(ctx, client, url, facts, browser_first=use_browser)
            if reason:
                ctx.mark_partial(CARDS_BLOCKED.format(reason=reason))
                return
            if facts.get("playwright") == "выполнялась":
                use_browser = True  # обычный HTTP уже получил проверку доступа - дальше только браузер
            if html is None:
                ctx.item_failed(f"Карточка Krisha {eid}: {facts.get('cardError') or 'не загрузилась'}")
                continue
            card = parse_card(html)
            if card["id"] != eid and not (card["id"] is None and card["description"]):
                ctx.item_failed(f"Карточка Krisha {eid}: данные объявления не найдены — страница изменилась или "
                                "объявление снято")
                continue
            self._card_item(ctx, eid, card, found.get(eid))
            pending.pop(eid, None)

    def _card_item(self, ctx: "RunContext", eid: str, card: dict, search_card: dict | None) -> None:
        """Публикация с данными карточки: сводка выдачи этого запуска(если объявление в ней было), описание, превью
        выдачи krisha:{id}:c:… (из этого запуска, иначе сохранённое) и фото карточки krisha:{id}:p:… в порядке галереи
        (ключи - photo_key)."""
        s = _summary(search_card) if search_card else _summary(card)
        k = ctx.known(eid)
        media = self._cover(ctx, eid, (search_card or {}).get("photo"))
        if not media and k is not None:
            media = [MediaIn(key=key, kind="image", role="cover") for key in k.media_keys if _is_cover_key(eid, key)]
        seen = {m.key for m in media}
        for src in card["photos"][:MAX_PHOTOS]:
            key = photo_key(eid, src)
            if key in seen:  # тот же снимок в другом размере
                continue
            seen.add(key)
            media.append(MediaIn(key=key, kind="image", role="slide", origin_url=src,
                                 fetch=lambda u=src, key=key: ctx.download_media(u, key=key, role="slide",
                                                                                 allowed_hosts=PHOTO_HOSTS)))
        fields = {f: card[f] for f in ("developer", "documents", "createdAt", "addedAt", "params", "lat", "lon")
                  if card.get(f) not in (None, "", [], {})}
        raw = {"source": "card", "advert": card["advert"], "documents": card["documents"], "params": card["params"]}
        # описание - и в extra: изменение выдачи без новой карточки пересобирает текст тем же способом
        pub = Publication(platform="krisha", external_id=eid, account=_account(s), url=card_url(eid),
                          text=_join(s.get("title") or card.get("title"), s.get("address") or card.get("address"),
                                     card.get("description")),
                          collected_at=ctx.now(), media=media, price=s.get("price"),
                          extra={**s, **fields, "description": card.get("description"), "source": "card"}, raw=raw)
        ctx.ingest(pub)


CONNECTOR = KrishaConnector()
router = None
