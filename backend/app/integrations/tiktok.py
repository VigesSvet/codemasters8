"""TikTok через Apify(Actor clockworks/tiktok-scraper, платно) - integration-spec §12.4, план §8.

Сбор идёт фазами одного задания воркера(jobs.payload["phase"]), запрос пользователя не держится открытым:
1. start - проверка лимитов(минимум Actor, лимит на запуск, дневной бюджет) и POST actors/{actor}/runs с проверенным
   Input(tiktok_input.json + профили); ID запуска Apify сразу пишется в БД(set_external_run; не записался - ошибка
   без повтора), затем Deferred;
2. poll - GET actor-runs/{id}?waitForFinish=30, пока запуск не завершится; сеть, тайм-аут, 429 и 5xx - снова Deferred
   (попытка задания не расходуется); после срока - запуск прерывается(abort), стоимость записывается(не подтвердил
   прерывание - не записывается и в дневном лимите считается по лимиту на запуск), сбор - ошибка;
3. dataset - GET datasets/{id}/items постранично; смещение сохраняется после каждой записи(сбой и остановка воркера
   продолжат с неё); сеть, тайм-аут, 429 и 5xx - Deferred, пока Apify недоступен не дольше DATASET_WAIT_S подряд
   (дальше - ошибка и остановка платного сбора): ни "пауза", ни повтор не завершают задание, иначе расписание начало
   бы второй платный запуск, бросив данные первого;
4. cost - итоговая стоимость запуска: max(usageTotalUsd, события × цены, оценка) и проверка окна выдачи: Actor отдаёт
   не больше resultsPerPage последних публикаций профиля, поэтому у профиля, собранного прежде, заполненная выдача без
   единой уже собранной(не закреплённой) публикации значит, что более ранние новые могли выпасть из окна - сбор
   "частично" с причиной, а не "работает"(счётчики по профилям - в jobs.payload["profileStats"], собранные профили -
   в collectors.state["profiles"]).

Повторное задание НИКОГДА не начинает второй платный запуск, если ID запуска уже сохранён(в jobs.payload или у
запуска сбора). POST запуска повторяется, только если запрос точно не ушёл(соединение не установлено) или Apify
отказал(4xx); потерянный ответ, 5xx и ответ без ID - "запуск мог начаться": без повтора, с остановкой платного сбора, а
в дневном лимите такой запуск до конца суток учитывается по лимиту на запуск(collectors.state.unconfirmedStarts).
После любой неповторяемой ошибки, исчерпанных повторов и HTTP 402 воркер останавливает платный сбор до успешной ручной
проверки(D19). Лимит расходов на запуск передаётся точно как задан(без округления) и никогда не повышается.
Расписание Apify(Schedule) не используется - запускает только воркер. Обход защиты не выполняется.

Файлы берутся только из хранилища Apify(Key-value store на api.apify.com) с Bearer-токеном; ссылки CDN TikTok не
используются. Флаги isAd / isSponsored не фильтруют публикации: рекламное предложение бывает и при isAd=false.
"""
import dataclasses
import json
import logging
import math
import re
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from urllib.parse import urlsplit

import httpx

from ..ads_service import safe_url
from ..models import as_utc, iso
from ..risk_model import ALMATY
from ..settings import Settings
from . import http
from .base import (AccessDenied, AuthExpired, BadConfig, CheckResult, ConfigError, Connector, ConnectorError, Deferred,
                   MediaIn, NotConfigured, Publication, RateLimited, SourceError)

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("saq.integrations.tiktok")

API = "https://api.apify.com/v2/"  # actors/… (префикс /v2/acts/ устарел)
APIFY_HOSTS = ("api.apify.com",)
ACTOR_MIN_CHARGE = 0.5  # minimalMaxTotalChargeUsd Actor(если цены не получены)
FALLBACK_PRICES = {"actor-start": 0.005, "result": 0.004, "video-download": 0.002}  # оценка сверху, USD
# запись хранилища: ID хранилища и ключ - по одному сегменту пути(без "/", "\" и пробелов, ключ - не "." и не ".."),
# query допускается
KV_RE = re.compile(r"https://api\.apify\.com/v2/key-value-stores/(?P<store>[A-Za-z0-9][A-Za-z0-9~._-]{0,127})/records/"
                   r"(?P<key>[^/\\?#\s]{1,256})(?:\?[^#\s]*)?")
INPUT_FILE = Path(__file__).with_name("tiktok_input.json")
PAGE_LIMIT = 50  # записей Dataset на страницу
POLL_WAIT_S = 30  # waitForFinish: сколько Apify держит запрос опроса
DEADLINE_MARGIN_S = 300  # запас к APIFY_RUN_TIMEOUT_S до прерывания запуска
START_DELAY_S = 15
POLL_DELAY_S = 20
COST_DELAY_S = 10
DATASET_RETRY_S = 60  # пауза перед повтором чтения Dataset после сети / 5xx(429 - по Retry-After)
DATASET_WAIT_S = 3600  # сколько подряд ждать недоступный Dataset оплаченного запуска, дальше - ошибка
UNCONFIRMED = "unconfirmedStarts"  # collectors.state: запуски, которые Apify мог начать без подтверждения
UNCONFIRMED_KEEP_S = 2 * 86400
SEEN = "profiles"  # collectors.state: собранные прежде профили({имя: {run, at, items}}) - для проверки окна выдачи
SEEN_MAX = 200  # сколько профилей помнить(самые давние забываются)
STATS_MAX = 200  # сколько авторов считать в одном запуске(jobs.payload["profileStats"])

PROFILE_RE = re.compile(r"[A-Za-z0-9._]{2,24}")  # только fullmatch
_PROFILE_URL = re.compile(r"^(?:https?://)?(?:(?:www|m)\.)?tiktok\.com/@([^/?#\s]+)", re.I)
_ACTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}(?:~[A-Za-z0-9][A-Za-z0-9._-]{0,99})?")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9~._-]{0,127}")  # ID запуска / Dataset / хранилища Apify
_POST_ID = re.compile(r"\d{1,32}")  # канонический ID публикации TikTok(как в приёме через API)
TIKTOK_HOSTS = ("tiktok.com",)  # ссылка на публикацию(webVideoUrl) - только tiktok.com и его поддомены
RUNNING = frozenset({"READY", "RUNNING", "TIMING-OUT", "ABORTING"})
FAILED = frozenset({"FAILED", "TIMED-OUT", "ABORTED"})

TOKEN_INVALID = "Токен Apify недействителен"
PAYMENT = "Apify: нужна оплата/лимит аккаунта"
NO_KV_VIDEO = "Apify не сохранил видео (нет ссылки на хранилище)"
NO_KV_SLIDE = "Apify не сохранил изображение слайд-шоу (нет ссылки на хранилище)"
KV_403 = "Apify отказал в доступе к файлу (403) — проверьте права токена на хранилище"
VIDEOS_OFF = "Скачивание видео выключено в настройках подключения"
PROFILE_HINT = ("Профиль TikTok — @имя или ссылка https://www.tiktok.com/@имя (латиница, цифры, «.» и «_», "
                "2–24 символа)")


# ---------- настройки и Input ----------

def normalize_profile(s: Any) -> str:
    """"@x", "x", "https://www.tiktok.com/@x[/video/…]" → "x"(строчными); иначе ValueError с подсказкой."""
    v = str(s or "").strip()
    m = _PROFILE_URL.match(v)
    if m:
        v = m.group(1)
    elif v.startswith("@"):
        v = v[1:]
    if not PROFILE_RE.fullmatch(v):
        raise ValueError(PROFILE_HINT)
    return v.lower()


def _profiles(cfg: dict) -> list[str]:
    out: list[str] = []
    for p in (cfg or {}).get("profiles") or []:
        try:
            v = normalize_profile(p)
        except ValueError:
            continue
        if v not in out:
            out.append(v)
    return out


@lru_cache(maxsize=1)
def _template_text() -> str:
    return INPUT_FILE.read_text(encoding="utf-8")


def input_template() -> dict:
    """Проверенный Input Actor из tiktok_input.json(без секретов; копия)."""
    data = json.loads(_template_text())
    if not isinstance(data, dict):
        raise BadConfig("Файл tiktok_input.json повреждён — ожидается JSON-объект")
    return data


def build_input(cfg: dict, s: Settings) -> dict:
    """Input запуска: шаблон + профили из настроек + resultsPerPage(не больше APIFY_MAX_ITEMS_PER_PROFILE) +
    shouldDownloadVideos(настройка downloadVideos, по умолчанию - как в шаблоне)."""
    body = input_template()
    cfg = cfg or {}
    body["profiles"] = _profiles(cfg)
    per_page = cfg.get("resultsPerPage")
    if not isinstance(per_page, int) or isinstance(per_page, bool) or per_page < 1:
        per_page = body.get("resultsPerPage") if isinstance(body.get("resultsPerPage"), int) else 5
    body["resultsPerPage"] = max(1, min(int(per_page), int(s.apify_max_items_per_profile)))
    videos = cfg.get("downloadVideos")
    body["shouldDownloadVideos"] = bool(body.get("shouldDownloadVideos", True) if videos is None else videos)
    return body


# ---------- цены и стоимость ----------

def _number(v: Any) -> float | None:
    """Конечное число из ответа Apify или jobs.payload; bool, NaN, бесконечность(json.loads принимает Infinity),
    целое больше float и не числа - None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def usd_param(x: float) -> str:
    """Сумма для Apify и текстов без округления и экспоненты: кратчайшая точная запись числа(float(…) == x)."""
    return format(Decimal(repr(float(x))), "f")


def _event_price(ev: Any) -> float | None:
    """Цена события: eventPriceUsd или(ступенчатые цены) наибольшая из ступеней - оценка сверху."""
    if not isinstance(ev, dict):
        return None
    flat = _number(ev.get("eventPriceUsd"))
    if flat is not None:
        return flat
    tiers = ev.get("eventTieredPricingUsd")
    if isinstance(tiers, dict):
        vals = [v for t in tiers.values() if isinstance(t, dict)
                and (v := _number(t.get("tieredEventPriceUsd"))) is not None]
        if vals:
            return max(vals)
    return None


def _pricing(actor: dict, now: datetime | None = None) -> dict | None:
    """Действующая запись PAY_PER_EVENT из pricingInfos: последняя, вступившая в силу к now(будущие - только если
    действующей нет)."""
    infos = [p for p in (actor or {}).get("pricingInfos") or []
             if isinstance(p, dict) and p.get("pricingModel") == "PAY_PER_EVENT"]
    if not infos:
        return None
    now = as_utc(now) or datetime.now(UTC)

    def started(p: dict) -> datetime:
        raw = p.get("startedAt") or p.get("createdAt")
        try:
            return as_utc(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=UTC)
    infos.sort(key=started)
    current = [p for p in infos if started(p) <= now]
    return (current or infos)[-1]


def prices(actor: dict, now: datetime | None = None) -> dict[str, float]:
    """Цены событий Actor(PAY_PER_EVENT): {событие: USD}; цен нет - пустой словарь(оценка берёт FALLBACK_PRICES)."""
    p = _pricing(actor, now)
    events = ((p or {}).get("pricingPerEvent") or {}).get("actorChargeEvents") or {}
    out: dict[str, float] = {}
    if isinstance(events, dict):
        for name, ev in events.items():
            price = _event_price(ev)
            if price is not None:
                out[str(name)] = price
    return out


def min_charge(actor: dict, now: datetime | None = None) -> float:
    """Минимальный лимит расходов на запуск(minimalMaxTotalChargeUsd); не указан - ACTOR_MIN_CHARGE."""
    v = _number((_pricing(actor, now) or {}).get("minimalMaxTotalChargeUsd"))
    return v if v is not None else ACTOR_MIN_CHARGE


def _all_prices(p: dict | None) -> dict[str, float]:
    return {**FALLBACK_PRICES, **{k: f for k, v in (p or {}).items() if (f := _number(v)) is not None}}


def estimate_usd(n_items: int, videos: bool, prices: dict) -> float:
    """Оценка стоимости запуска: старт + публикации(+ скачивание видео)."""
    p = _all_prices(prices)
    n = max(int(n_items or 0), 0)
    total = p["actor-start"] + n * p["result"] + (n * p["video-download"] if videos else 0.0)
    return round(total, 6)


def run_cost(run: dict, prices: dict, items: int = 0, videos: bool = True) -> float:
    """Стоимость запуска: max(usageTotalUsd, Σ chargedEventCounts × цены, оценка) - пока живой запуск не подтвердил,
    что usageTotalUsd включает оплату событий. Событие без известной цены - по наибольшей известной цене."""
    run = run or {}
    p = _all_prices(prices)
    top = max(p.values())
    usage = _number(run.get("usageTotalUsd")) or 0.0
    events = run.get("chargedEventCounts") or {}
    by_events = 0.0
    if isinstance(events, dict):
        for name, n in events.items():
            count = _number(n)
            if count is not None and count > 0:
                by_events += count * p.get(str(name), top)
    return round(max(usage, by_events, estimate_usd(items, videos, prices)), 6)


# ---------- HTTP ----------

def _token(s: Settings) -> str:
    token = s.apify_token.get() if s.apify_token else ""
    if not token:
        raise NotConfigured("Нет на сервере: APIFY_TOKEN")
    if not token.isascii() or not token.isprintable() or any(c.isspace() for c in token):
        raise BadConfig("APIFY_TOKEN содержит недопустимые символы — проверьте значение в .env")
    return token


def _actor_id(s: Settings) -> str:
    actor = (s.apify_actor_id or "").strip()
    if not _ACTOR_RE.fullmatch(actor):
        raise BadConfig("APIFY_ACTOR_ID задан неверно — нужен вид «владелец~имя», например clockworks~tiktok-scraper")
    return actor


def _client(ctx: "RunContext", token: str, timeout: float = 60.0) -> httpx.Client:
    """Клиент API Apify: привязан к api.apify.com по https, токен - только в заголовке Authorization."""
    return ctx.client(base_url=API, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)


def _send(c: httpx.Client, method: str, path: str, **kw) -> httpx.Response:
    """Запрос к API Apify; сеть и тайм-аут - повторяемая SourceError."""
    try:
        return c.request(method, path, **kw)
    except httpx.TimeoutException:
        raise SourceError("Apify не ответил вовремя", retryable=True) from None
    except httpx.RequestError as e:  # сеть, а также тело, которое не распаковалось(DecodingError)
        raise SourceError(f"Сетевая ошибка при обращении к Apify ({type(e).__name__})", retryable=True) from None


def _apify_error(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error") or {}
        msg = err.get("message") or err.get("type") or ""
    except (ValueError, AttributeError):
        return ""
    return re.sub(r"\s+", " ", str(msg)).strip()[:200]


def _raise_for(resp: httpx.Response, what: str) -> None:
    """Ответ Apify → типизированная ошибка подключения(integration-spec §12.4); 2xx - ничего."""
    code = resp.status_code
    if 200 <= code < 300:
        return
    msg = _apify_error(resp)
    tail = f": {msg}" if msg else ""
    if code == 401:
        raise AuthExpired(TOKEN_INVALID)
    if code == 402:
        raise SourceError(PAYMENT, retryable=False)
    if code == 403:
        raise AccessDenied(f"Apify: у токена нет доступа ({what}){tail}")
    if code == 404:
        raise SourceError(f"Apify: не найдено ({what})", retryable=False)
    if code == 429:
        raise RateLimited(http.retry_after(resp.headers, 60), "Apify: ограничение частоты запросов — повтор позже")
    if code >= 500:
        raise SourceError(f"Apify временно недоступен (HTTP {code}, {what})", retryable=True)
    raise SourceError(f"Apify ответил HTTP {code} ({what}){tail}", retryable=False)


def _data(resp: httpx.Response, what: str) -> dict:
    try:
        data = resp.json().get("data")
    except (ValueError, AttributeError):
        data = None
    if not isinstance(data, dict):
        raise SourceError(f"Apify вернул неожиданный ответ ({what})", retryable=True)
    return data


def _actor(c: httpx.Client, actor: str) -> dict:
    resp = _send(c, "GET", f"actors/{actor}")
    if resp.status_code == 404:
        raise BadConfig(f"Actor «{actor}» не найден в Apify — проверьте APIFY_ACTOR_ID")
    _raise_for(resp, f"Actor {actor}")
    return _data(resp, f"Actor {actor}")


def _check_limits(s: Settings, minimum: float) -> None:
    """Лимит на запуск не ниже минимума Actor и не выше дневного лимита; сам лимит не меняется."""
    cap, budget = float(s.apify_max_total_charge_usd), float(s.apify_daily_budget_usd)
    if not math.isfinite(cap):
        raise BadConfig("APIFY_MAX_TOTAL_CHARGE_USD должен быть конечным числом — исправьте значение в .env")
    if not math.isfinite(budget):  # NaN отключил бы все сравнения с дневным лимитом
        raise BadConfig("APIFY_DAILY_BUDGET_USD должен быть конечным числом — исправьте значение в .env")
    if cap < minimum:
        raise BadConfig(f"APIFY_MAX_TOTAL_CHARGE_USD ({usd_param(cap)}) меньше минимума Actor ({minimum:g}) — "
                        "увеличьте лимит в .env")
    if cap > budget:
        raise BadConfig(f"APIFY_MAX_TOTAL_CHARGE_USD ({usd_param(cap)}) больше дневного лимита APIFY_DAILY_BUDGET_USD "
                        f"({budget:g}) — один запуск мог бы превысить дневной лимит; исправьте значения в .env")


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Начало текущих суток по местному времени(UTC+5) и начало следующих - в UTC."""
    local = as_utc(now).astimezone(ALMATY)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _unconfirmed_usd(state: dict, since: datetime, cap: float) -> float:
    """Сумма неподтверждённых запусков(каждый - по лимиту на запуск в момент попытки) с момента since."""
    total = 0.0
    for e in (state or {}).get(UNCONFIRMED) or []:
        at = _parse_time(e.get("at")) if isinstance(e, dict) else None
        if at is None or at < as_utc(since):
            continue
        cost = _number(e.get("costUsd"))
        total += cost if cost is not None and cost >= 0 else cap
    return total


def _parse_time(v: Any) -> datetime | None:
    ts = _number(v)
    if ts is not None and ts > 0:
        try:
            return datetime.fromtimestamp(ts, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str) and v.strip():
        try:
            return as_utc(datetime.fromisoformat(v.strip().replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _valid_id(v: Any) -> str | None:
    v = str(v or "").strip()
    return v if _ID_RE.fullmatch(v) else None


# ---------- публикации ----------

def _kv(url: Any) -> str | None:
    """Ссылка на запись хранилища Apify(Key-value store) или None(CDN TikTok, другие пути API Apify и прочее не
    используются: с этой ссылкой уходит токен Apify)."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    m = KV_RE.fullmatch(url)
    return url if m and m.group("key").strip(".") else None


def _kv_media(ctx: "RunContext", url: str, *, key: str, kind: str, role: str | None, token: str) -> MediaIn:
    """Ленивая загрузка файла из хранилища Apify с Bearer-токеном(только api.apify.com по https; перенаправление на
    другой хост - без заголовка). Отметка "загружен" - только после сохранения байтов."""
    def fetch() -> MediaIn:
        m = ctx.download_media(url, key=key, kind=kind, role=role, allowed_hosts=APIFY_HOSTS,
                               auth_headers={"Authorization": f"Bearer {token}"}, auth_hosts=APIFY_HOSTS)
        if m.status != "downloaded" and m.error_code == "http_403":
            return dataclasses.replace(m, status="failed", error=KV_403)
        return m
    return MediaIn(key=key, kind=kind, role=role, fetch=fetch, origin_url=http.redact_url(url))


def _missing(key: str, kind: str, role: str | None, error: str) -> MediaIn:
    """Файла в хранилище нет: "недоступен"(ленивая отметка - повторяется не больше ATTEMPTS_MAX раз)."""
    def fetch() -> MediaIn:
        return MediaIn(key=key, kind=kind, role=role, status="unavailable", error=error)
    return MediaIn(key=key, kind=kind, role=role, fetch=fetch)


def _media(it: dict, ctx: "RunContext", pid: str, token: str, videos: bool) -> list[MediaIn]:
    vm = it.get("videoMeta") if isinstance(it.get("videoMeta"), dict) else {}
    out: list[MediaIn] = []
    if it.get("isSlideshow"):
        links = it.get("slideshowImageLinks") if isinstance(it.get("slideshowImageLinks"), list) else []
        for i, link in enumerate(links):
            key = f"apify:{pid}:slide:{i}"
            url = _kv(link.get("downloadLink") if isinstance(link, dict) else None)
            out.append(_kv_media(ctx, url, key=key, kind="image", role="slide", token=token) if url
                       else _missing(key, "image", "slide", NO_KV_SLIDE))
    else:
        key = f"apify:{pid}:video"
        if not videos:
            out.append(MediaIn(key=key, kind="video", status="skipped", error=VIDEOS_OFF))
        else:
            media_urls = it.get("mediaUrls") if isinstance(it.get("mediaUrls"), list) else []
            url = next((u for u in map(_kv, [vm.get("downloadAddr"), *media_urls]) if u), None)
            out.append(_kv_media(ctx, url, key=key, kind="video", role=None, token=token) if url
                       else _missing(key, "video", None, NO_KV_VIDEO))
    cover = _kv(vm.get("coverUrl"))
    if cover:
        out.append(_kv_media(ctx, cover, key=f"apify:{pid}:cover", kind="image", role="cover", token=token))
    return out


def item_error(it: Any) -> str:
    """Почему запись Dataset не стала публикацией - для заметки запуска."""
    if not isinstance(it, dict):
        return "TikTok: запись Apify не принята — не объект"
    msg = it.get("error") or it.get("errorDescription") or "нет ID публикации"
    who = it.get("input") or it.get("profile") or it.get("url") or ""
    text = re.sub(r"\s+", " ", str(msg)).strip()[:200]
    return f"TikTok: запись Apify не принята — {text}" + (f" ({str(who)[:100]})" if who else "")


def _post_url(it: dict, name: str, pid: str) -> str:
    """Ссылка на публикацию: webVideoUrl, если она на tiktok.com(чужой хост в карточке - ссылка "в никуда" или на
    подделку), иначе собранная из имени автора и ID; имя не по правилам TikTok - ""."""
    url = safe_url(it.get("webVideoUrl"))
    if url and http.host_allowed(urlsplit(url).hostname or "", TIKTOK_HOSTS):
        return url
    return f"https://www.tiktok.com/@{name}/video/{pid}" if PROFILE_RE.fullmatch(name) else ""


def map_item(it: dict, ctx: "RunContext") -> Publication | None:
    """Запись Dataset → Publication; запись-ошибка({"error": …}) или без ID - None(вызывающий: ctx.item_failed).
    isAd / isSponsored не фильтруют: рекламное предложение бывает и при isAd=false."""
    if not isinstance(it, dict) or it.get("error"):
        return None
    pid = str(it.get("id") or "").strip()
    if not _POST_ID.fullmatch(pid):
        return None
    author = it.get("authorMeta") if isinstance(it.get("authorMeta"), dict) else {}
    name = str(author.get("name") or "").strip().lstrip("@")
    reach = _number(it.get("playCount"))
    videos = ctx.payload.get("videos")
    if videos is None:
        videos = build_input(ctx.config, ctx.settings)["shouldDownloadVideos"]
    return Publication(
        platform="tiktok", external_id=pid, account=f"@{name}" if name else "", url=_post_url(it, name, pid),
        text=str(it.get("text") or ""), collected_at=ctx.now(),
        published_at=_parse_time(it.get("createTimeISO")) or _parse_time(it.get("createTime")),
        media=_media(it, ctx, pid, _token(ctx.settings), bool(videos)),
        reach=int(reach) if reach is not None and reach >= 0 else None,
        extra={"isSlideshow": bool(it.get("isSlideshow"))}, raw=it)


def profile_key(it: Any) -> str | None:
    """Профиль записи Dataset(authorMeta.name, иначе input) в виде настроек("x", строчными) или None."""
    if not isinstance(it, dict):
        return None
    author = it.get("authorMeta") if isinstance(it.get("authorMeta"), dict) else {}
    for raw in (author.get("name"), it.get("input")):
        if isinstance(raw, str) and raw.strip():
            try:
                return normalize_profile(raw)
            except ValueError:
                continue
    return None


def tally(stats: dict, it: dict, status: str) -> None:
    """Учесть принятую запись в счётчиках профиля: [в выдаче, не закреплённых, из них новых(created)].
    Закреплённые публикации стоят в выдаче вне хронологии и непрерывность сбора не подтверждают."""
    key = profile_key(it)
    if key is None or (key not in stats and len(stats) >= STATS_MAX):
        return
    n, chrono, fresh = stats.get(key) or (0, 0, 0)
    pinned = bool(it.get("isPinned"))
    stats[key] = [n + 1, chrono + (0 if pinned else 1), fresh + (1 if not pinned and status == "created" else 0)]


def _stats(raw: Any) -> dict[str, list[int]]:
    """Счётчики профилей из jobs.payload(после отсрочки задания) - только корректные записи."""
    out: dict[str, list[int]] = {}
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        if (isinstance(k, str) and isinstance(v, list) and len(v) == 3
                and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v)):
            out[k] = list(v)
    return out


def _window_hint(per_page: int, s: Settings) -> str:
    mx = int(s.apify_max_items_per_profile)
    if per_page < mx:
        return (f"увеличьте «Публикаций за запуск с каждого профиля» (сейчас {per_page}, предел "
                f"APIFY_MAX_ITEMS_PER_PROFILE = {mx}) или сократите интервал сбора")
    return (f"увеличьте APIFY_MAX_ITEMS_PER_PROFILE в .env (сейчас {mx}) и «Публикаций за запуск с каждого профиля» "
            "или сократите интервал сбора")


def _yield_if_needed(ctx: "RunContext") -> None:
    """Воркер останавливается или мягкий срок запуска близко: задание откладывается без расхода попытки(позиция в
    Dataset уже сохранена) - проверяется перед каждой записью, а не только между страницами."""
    if ctx.stopping() or ctx.time_left() < 60:
        raise Deferred(5)


# ---------- подключение ----------

class TikTokConnector(Connector):
    code = "tiktok"
    kind = "collector"
    platform = "tiktok"
    group = "collectors"
    name = "TikTok"
    via = "Apify · clockworks/tiktok-scraper (платно)"
    required_env = ("APIFY_TOKEN",)
    config_fields = ("profiles",)
    options = ("resultsPerPage", "downloadVideos")
    default_interval_min = 1440
    interval_bounds = (360, 10080)
    max_attempts = 3
    paid = True
    secret_refs = ("env:APIFY_TOKEN",)
    limitation = ("Платный сбор через Apify: лимит расходов на запуск и на день; после ошибки сбор останавливается до "
                  "ручной проверки. За запуск — не больше {APIFY_MAX_ITEMS_PER_PROFILE} последних публикаций с "
                  "профиля: если все они новые, более ранние могли быть пропущены — сбор отмечается «частично». Речь "
                  "в роликах не распознаётся — OCR читает только кадры.")

    # --- настройки ---

    def normalize_item(self, field_name: str, value: str) -> str:
        if field_name == "profiles":
            try:
                return normalize_profile(value)
            except ValueError as e:
                raise ConfigError(field_name, f"{e}: {value[:60]}", value) from None
        return value

    def validate_config(self, cfg: dict) -> dict:
        out = super().validate_config(cfg)
        per_page = out.get("resultsPerPage")
        if per_page is not None and (not isinstance(per_page, int) or isinstance(per_page, bool)
                                     or not 1 <= per_page <= 50):
            raise ConfigError("resultsPerPage", "Публикаций на профиль — целое число от 1 до 50", per_page)
        if "downloadVideos" in out and not isinstance(out["downloadVideos"], bool):
            raise ConfigError("downloadVideos", "Скачивание видео — да или нет", out["downloadVideos"])
        return out

    # --- проверка(без платного запуска) ---

    def check(self, ctx: "RunContext") -> CheckResult:
        s = ctx.settings
        token, actor = _token(s), _actor_id(s)
        with _client(ctx, token, timeout=30.0) as c:
            resp = _send(c, "GET", "users/me")
            _raise_for(resp, "аккаунт")
            username = str(_data(resp, "аккаунт").get("username") or "")
            info = _actor(c, actor)
        now = ctx.now()
        pr, minimum = prices(info, now), min_charge(info, now)
        _check_limits(s, minimum)
        cap, budget = float(s.apify_max_total_charge_usd), float(s.apify_daily_budget_usd)
        model = (_pricing(info, now) or {}).get("pricingModel") or "нет данных о ценах"
        facts = {"account": username, "actor": actor, "minCharge": minimum, "prices": pr, "pricingModel": model,
                 "maxTotalChargeUsd": cap, "dailyBudgetUsd": budget,
                 "maxItemsPerProfile": int(s.apify_max_items_per_profile)}
        who = f"аккаунт {username}" if username else "аккаунт Apify"
        detail = (f"Токен Apify действует ({who}); Actor {actor} доступен. Лимит на запуск ${usd_param(cap)} "
                  f"(минимум Actor ${minimum:g}), дневной лимит ${budget:g}. Платный запуск при проверке не выполнялся")
        return CheckResult(True, detail, facts)

    # --- сбор ---

    def collect(self, ctx: "RunContext") -> None:
        s = ctx.settings
        token = _token(s)
        p = ctx.payload
        run_id = _valid_id(p.get("apifyRunId")) or _valid_id(ctx.external_run_id)
        phase = p.get("phase") if p.get("phase") in ("poll", "dataset", "cost") else "start"
        if run_id is not None and phase == "start":
            phase = "poll"  # запуск Apify уже начат - второй платный запуск не начинается
        if run_id is None and phase != "start":
            raise SourceError("ID запуска Apify потерян — сбор остановлен, проверьте консоль Apify", retryable=False)
        with _client(ctx, token, timeout=POLL_WAIT_S + 30.0) as c:
            if phase == "start":
                self._start(ctx, c, s)  # всегда Deferred
            if phase == "poll":
                self._poll(ctx, c, s, run_id)  # Deferred или SUCCEEDED → dataset
                phase = "dataset"
            if phase == "dataset":
                self._dataset(ctx, c, run_id)  # Deferred → cost
            self._cost(ctx, c, s, run_id)

    def _start(self, ctx: "RunContext", c: httpx.Client, s: Settings) -> None:
        body = build_input(ctx.config, s)
        profiles = body["profiles"]
        if not profiles:
            raise BadConfig("Не указаны профили TikTok — добавьте их в настройках подключения")
        actor = _actor_id(s)
        now = ctx.now()
        info = _actor(c, actor)
        pr, minimum = prices(info, now), min_charge(info, now)
        _check_limits(s, minimum)
        cap, budget = float(s.apify_max_total_charge_usd), float(s.apify_daily_budget_usd)
        videos = bool(body["shouldDownloadVideos"])
        n = len(profiles) * int(body["resultsPerPage"])
        est = estimate_usd(n, videos, pr)
        if est > budget:
            raise BadConfig(f"Оценка запуска (${est:g}) больше дневного лимита APIFY_DAILY_BUDGET_USD (${budget:g}) — "
                            "уменьшите число профилей или публикаций на профиль")
        day_start, day_end = _day_bounds(now)
        unconfirmed = _unconfirmed_usd(ctx.state, day_start, cap)
        spent = ctx.spent_usd(day_start, unknown_cost=cap) + unconfirmed
        if spent + est > budget:
            extra = f"; учтены запуски без подтверждения Apify — ${usd_param(unconfirmed)}" if unconfirmed else ""
            raise RateLimited(max((day_end - as_utc(now)).total_seconds(), 60.0),
                              f"Дневной лимит расходов Apify (${budget:g}) исчерпан{extra}")
        params = {"timeout": int(s.apify_run_timeout_s), "maxTotalChargeUsd": usd_param(cap), "maxItems": n}
        try:
            resp = c.post(f"actors/{actor}/runs", params=params, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:  # запрос не ушёл - запуска нет
            raise SourceError(f"Apify недоступен — запуск не начат ({type(e).__name__})", retryable=True) from None
        except httpx.HTTPError as e:  # запрос мог дойти: повтор мог бы оплатить второй запуск
            self._unconfirmed(ctx, cap, now, f"ответ не получен: {type(e).__name__}")
        if resp.status_code >= 500:  # шлюз или сервер Apify мог создать запуск до ошибки
            self._unconfirmed(ctx, cap, now, f"HTTP {resp.status_code}")
        _raise_for(resp, f"запуск Actor {actor}")  # 4xx: Apify отказал - запуск не создан
        try:
            run = _data(resp, "запуск Actor")
        except SourceError:
            run = {}
        rid = _valid_id(run.get("id"))
        if rid is None:
            self._unconfirmed(ctx, cap, now, f"HTTP {resp.status_code} без ID запуска")
        deadline = as_utc(now) + timedelta(seconds=int(s.apify_run_timeout_s) + DEADLINE_MARGIN_S)
        seen = ctx.state.get(SEEN) if isinstance(ctx.state.get(SEEN), dict) else {}
        try:  # сразу в БД: повтор задания найдёт ID у запуска сбора и не начнёт второй платный запуск
            ctx.set_external_run(rid)
        except Exception as e:  # ID не записан - повтор начал бы второй платный запуск: ошибка без повтора
            log.warning("TikTok: ID запуска Apify %s не записан (%s)", rid, type(e).__name__)
            # стоимость такого запуска не запишется у запуска сбора - до конца суток он в дневном лимите по лимиту
            self._count_at_cap(ctx, cap, now, f"ID запуска Apify {rid} не записан — в дневном лимите учтён как "
                                              f"${usd_param(cap)}")
            raise SourceError(f"Запуск Apify {rid} начат, но не записан — проверьте консоль Apify, затем запустите "
                              "проверку вручную", retryable=False) from None
        # сбой этой записи безопасен: ID уже у запуска сбора, повтор задания продолжит с опроса(collect)
        ctx.save_payload(phase="poll", apifyRunId=rid, datasetId=_valid_id(run.get("defaultDatasetId")),
                         kvStoreId=_valid_id(run.get("defaultKeyValueStoreId")), deadline=iso(deadline), prices=pr,
                         videos=videos, offset=0, items=0, perPage=int(body["resultsPerPage"]), maxItems=n,
                         seeded=[x for x in profiles if x in seen], profileStats={})
        ctx.note(f"Запуск Apify {rid} начат: профилей {len(profiles)}, до {n} публикаций, лимит расходов "
                 f"${usd_param(cap)}")
        raise Deferred(START_DELAY_S)

    def _unconfirmed(self, ctx: "RunContext", cap: float, now: datetime, why: str) -> NoReturn:
        """Apify не подтвердил запуск(ответ потерян, 5xx, нет ID), а запуск мог начаться и оплачиваться: повтор мог бы
        оплатить второй - не повторяем(платный сбор останавливается); до конца суток запуск учитывается в дневном
        лимите по лимиту на запуск(D19: запуски неизвестной стоимости входят в бюджет)."""
        self._count_at_cap(ctx, cap, now, f"Запуск Apify не подтверждён ({why}) — в дневном лимите учтён как "
                                          f"${usd_param(cap)}")
        raise SourceError(f"Apify не подтвердил запуск Actor ({why}) — запуск мог начаться и оплачиваться; проверьте "
                          "консоль Apify, затем запустите проверку вручную", retryable=False) from None

    @staticmethod
    def _count_at_cap(ctx: "RunContext", cap: float, now: datetime, note: str) -> None:
        """Запуск Apify, стоимость которого у запуска сбора не запишется: в collectors.state.unconfirmedStarts(до конца
        суток - в дневном лимите по лимиту на запуск) и заметка-предупреждение. Никогда не бросает."""
        keep = as_utc(now) - timedelta(seconds=UNCONFIRMED_KEEP_S)
        prev = [e for e in ctx.state.get(UNCONFIRMED) or []
                if isinstance(e, dict) and (at := _parse_time(e.get("at"))) is not None and at >= keep]
        try:  # сбой записи не должен превратить ошибку в повторяемую(повтор - второй POST)
            ctx.save_state(**{UNCONFIRMED: (prev + [{"at": iso(now), "runId": ctx.run_id, "costUsd": cap}])[-50:]})
            ctx.note(note, "warn")
        except Exception as e:
            log.warning("TikTok: запуск Apify без записанной стоимости не учтён в дневном лимите (%s)",
                        type(e).__name__)

    def _deadline(self, ctx: "RunContext", s: Settings) -> datetime:
        d = _parse_time(ctx.payload.get("deadline"))
        if d is None:
            d = as_utc(ctx.now()) + timedelta(seconds=int(s.apify_run_timeout_s) + DEADLINE_MARGIN_S)
            ctx.save_payload(deadline=iso(d))
        return d

    def _poll(self, ctx: "RunContext", c: httpx.Client, s: Settings, rid: str) -> None:
        deadline = self._deadline(ctx, s)
        pr, videos = ctx.payload.get("prices") or {}, bool(ctx.payload.get("videos", True))
        try:
            resp = c.get(f"actor-runs/{rid}", params={"waitForFinish": POLL_WAIT_S})
        except httpx.HTTPError as e:
            self._wait(ctx, c, s, rid, deadline, f"Apify не ответил при опросе запуска ({type(e).__name__})")
            return
        if resp.status_code >= 500 or resp.status_code == 429:
            self._wait(ctx, c, s, rid, deadline, f"Apify временно недоступен (HTTP {resp.status_code})",
                       delay=http.retry_after(resp.headers, POLL_DELAY_S) if resp.status_code == 429 else None)
            return
        if resp.status_code == 404:
            raise SourceError(f"Запуск Apify {rid} не найден", retryable=False)
        _raise_for(resp, f"запуск {rid}")
        run = _data(resp, f"запуск {rid}")
        status = str(run.get("status") or "").upper()
        if status in FAILED:
            ctx.set_external_run(rid, cost_usd=run_cost(run, pr, 0, videos))
            raise SourceError(f"Запуск Apify завершился со статусом {status}", retryable=False)
        if status != "SUCCEEDED":  # READY / RUNNING / TIMING-OUT / ABORTING и незнакомые - ждать до срока
            self._wait(ctx, c, s, rid, deadline, None)
            return
        ctx.set_external_run(rid, cost_usd=run_cost(run, pr, 0, videos))  # предварительно; итог - в фазе cost
        ds = _valid_id(ctx.payload.get("datasetId")) or _valid_id(run.get("defaultDatasetId"))
        if ds is None:
            raise SourceError("Apify не вернул набор данных запуска", retryable=False)
        ctx.save_payload(phase="dataset", datasetId=ds, offset=0, items=0)

    def _wait(self, ctx: "RunContext", c: httpx.Client, s: Settings, rid: str, deadline: datetime,
              why: str | None, *, delay: float | None = None) -> None:
        """Запуск ещё идёт(или Apify временно не ответил): Deferred до срока; после срока - прервать запуск, записать
        стоимость(если Apify подтвердил прерывание) и завершить сбор ошибкой."""
        if as_utc(ctx.now()) < deadline:
            if why:
                log.info("TikTok: %s — повтор опроса запуска %s", why, rid)
            raise Deferred(delay or POLL_DELAY_S)
        pr, videos = ctx.payload.get("prices") or {}, bool(ctx.payload.get("videos", True))
        aborted: dict | None = None
        try:
            resp = c.post(f"actor-runs/{rid}/abort")
            if 200 <= resp.status_code < 300:
                aborted = _data(resp, f"прерывание {rid}")
            else:
                ctx.note(f"Apify не прервал запуск {rid} (HTTP {resp.status_code}) — проверьте консоль Apify", "warn")
        except (httpx.HTTPError, SourceError) as e:
            ctx.note(f"Запрос на прерывание запуска {rid} не выполнен ({type(e).__name__}) — проверьте консоль Apify",
                     "warn")
        # прерывание не подтверждено - запуск мог продолжаться и расходовать до лимита на запуск: стоимость не
        # записывается(в дневном лимите такой запуск учитывается по лимиту на запуск), а не занижается по опросу
        ctx.set_external_run(rid, cost_usd=run_cost(aborted, pr, 0, videos) if aborted is not None else None)
        minutes = round((int(s.apify_run_timeout_s) + DEADLINE_MARGIN_S) / 60)
        raise SourceError(f"Запуск Apify не завершился за {minutes} мин — прерван", retryable=False)

    def _dataset(self, ctx: "RunContext", c: httpx.Client, rid: str) -> None:
        ds = _valid_id(ctx.payload.get("datasetId"))
        if ds is None:
            raise SourceError("Apify не вернул набор данных запуска", retryable=False)
        offset, items = int(ctx.payload.get("offset") or 0), int(ctx.payload.get("items") or 0)
        stats = _stats(ctx.payload.get("profileStats"))  # по профилям - для проверки окна выдачи(фаза cost)
        while True:
            _yield_if_needed(ctx)
            try:
                resp = c.get(f"datasets/{ds}/items",
                             params={"format": "json", "clean": "true", "offset": offset, "limit": PAGE_LIMIT})
            except httpx.HTTPError as e:
                self._dataset_wait(ctx, rid, offset, f"Apify не ответил ({type(e).__name__})", DATASET_RETRY_S)
            if resp.status_code == 429:
                self._dataset_wait(ctx, rid, offset, "ограничение частоты запросов Apify (HTTP 429)",
                                   http.retry_after(resp.headers, DATASET_RETRY_S))
            if resp.status_code >= 500:
                self._dataset_wait(ctx, rid, offset, f"Apify временно недоступен (HTTP {resp.status_code})",
                                   DATASET_RETRY_S)
            _raise_for(resp, f"набор данных {ds}")
            try:
                page = resp.json()
            except ValueError:
                page = None
            if not isinstance(page, list):
                raise SourceError("Apify вернул неожиданный ответ (набор данных)", retryable=True)
            if ctx.payload.get("waitSince"):
                ctx.save_payload(waitSince=None)  # Apify снова отвечает - отсчёт ожидания сначала
            for it in page:
                _yield_if_needed(ctx)  # позиция сохранена после предыдущей записи - продолжим с этой
                pub = map_item(it, ctx)
                patch: dict = {}
                if pub is None:
                    ctx.item_failed(item_error(it))
                else:
                    tally(stats, it, ctx.ingest(pub).status)
                    patch["profileStats"] = {k: list(v) for k, v in stats.items()}
                offset += 1
                items += 1
                patch.update(offset=offset, items=items)
                if ctx.first_failure and not ctx.payload.get("firstFailure"):
                    patch["firstFailure"] = ctx.first_failure
                ctx.save_payload(**patch)  # после каждой записи: сбой не примет её повторно
            raw_total = (resp.headers.get("x-apify-pagination-total") or "").strip()
            # isascii: "²" и прочие цифры Unicode isdigit пропускает, а int() на них падает
            total = int(raw_total) if raw_total.isascii() and raw_total.isdigit() else None
            if not page or (total is not None and offset >= total) or (total is None and len(page) < PAGE_LIMIT):
                break
        raise Deferred(COST_DELAY_S, {"phase": "cost"})

    def _dataset_wait(self, ctx: "RunContext", rid: str, offset: int, why: str, delay: float) -> NoReturn:
        """Dataset оплаченного запуска временно недоступен(сеть, тайм-аут, 429, 5xx): Deferred с сохранённой позицией
        (попытка не расходуется, задание не завершается - расписание не начнёт второй платный запуск). Недоступен дольше
        DATASET_WAIT_S подряд - ошибка(платный сбор останавливается до ручной проверки)."""
        now = as_utc(ctx.now())
        since = _parse_time(ctx.payload.get("waitSince")) or now
        if (now - since).total_seconds() < DATASET_WAIT_S:
            log.info("TikTok: %s — повтор чтения набора данных запуска %s", why, rid)
            raise Deferred(delay, {"waitSince": iso(since)}) from None
        minutes = max(round(DATASET_WAIT_S / 60), 1)
        raise SourceError(f"Набор данных запуска Apify {rid} не дочитан: {why} дольше {minutes} мин подряд (прочитано "
                          f"записей: {offset}) — проверьте консоль Apify, затем запустите проверку вручную",
                          retryable=False) from None

    def _cost(self, ctx: "RunContext", c: httpx.Client, s: Settings, rid: str) -> None:
        if ctx.first_failure is None and ctx.payload.get("firstFailure"):
            ctx.first_failure = str(ctx.payload["firstFailure"])  # для "все записи с ошибкой" после отсрочки
        self._window(ctx, s)  # до уточнения стоимости: её сбой не скрывает возможный пропуск публикаций
        pr, videos = ctx.payload.get("prices") or {}, bool(ctx.payload.get("videos", True))
        items = int(ctx.payload.get("items") or 0)
        cap = float(s.apify_max_total_charge_usd)
        try:
            resp = _send(c, "GET", f"actor-runs/{rid}")
            _raise_for(resp, f"запуск {rid}")
            cost = run_cost(_data(resp, f"запуск {rid}"), pr, items, videos)
        except ConnectorError as e:  # публикации уже приняты: сбой уточнения стоимости не делает сбор ошибкой
            ctx.note(f"Итоговая стоимость запуска Apify не получена ({e.detail}) — записана предварительная", "warn")
            return
        ctx.set_external_run(rid, cost_usd=cost)
        ctx.note(f"Запуск Apify {rid}: записей {items}, стоимость ≈ ${cost:g}")
        if cost >= cap - 1e-9:
            ctx.mark_partial(f"Достигнут лимит расходов на запуск (${cap:g}) — часть публикаций могла не собраться")

    def _window(self, ctx: "RunContext", s: Settings) -> None:
        """Окно выдачи(план §14: пропуск не должен выглядеть полным сбором). Actor отдаёт не больше resultsPerPage
        последних публикаций профиля: если у профиля, собранного прежде, выдача заполнена, а среди её не закреплённых
        публикаций нет ни одной уже собранной, более ранние новые публикации могли выпасть из окна - сбор "частично" с
        причиной. Первый сбор профиля - только заметка. Затем профили запоминаются в collectors.state["profiles"]."""
        p = ctx.payload
        stats = _stats(p.get("profileStats"))
        seen = dict(ctx.state[SEEN]) if isinstance(ctx.state.get(SEEN), dict) else {}
        if isinstance(p.get("seeded"), list):  # снимок на старте запуска: повтор фазы cost даёт тот же итог
            seeded = {str(x) for x in p["seeded"]}
        else:  # запуск начат до этой проверки - профили, отмеченные не этим запуском
            seeded = {k for k, v in seen.items() if not (isinstance(v, dict) and v.get("run") == ctx.run_id)}
        per_page = p.get("perPage")
        if not isinstance(per_page, int) or isinstance(per_page, bool) or per_page < 1:
            per_page = int(build_input(ctx.config, s)["resultsPerPage"])
        max_items = p.get("maxItems")
        cap_hit = (isinstance(max_items, int) and not isinstance(max_items, bool) and max_items > 0
                   and sum(v[0] for v in stats.values()) >= max_items)  # maxItems запуска мог обрезать выдачу
        hint = _window_hint(per_page, s)
        for name in sorted(stats):
            n, chrono, fresh = stats[name]
            if fresh < chrono or not (n >= per_page or cap_hit):
                continue  # есть уже собранная публикация(сбор непрерывен) или выдача не заполнена(публикаций меньше)
            if name not in seeded:
                ctx.note(f"@{name}: первый сбор профиля — получены последние публикации ({n}), более ранние не "
                         "собирались")
            elif chrono:
                ctx.mark_partial(f"@{name}: все последние публикации в выдаче ({chrono}) новые — более ранние могли "
                                 f"быть пропущены; {hint}")
            else:
                ctx.mark_partial(f"@{name}: в выдаче только закреплённые публикации ({n}) — новые могли быть "
                                 f"пропущены; {hint}")
        if not stats:
            return
        at = iso(ctx.now())
        for name, (n, _, _) in stats.items():
            seen[name] = {"run": ctx.run_id, "at": at, "items": n}
        if len(seen) > SEEN_MAX:
            def when(k: str) -> str:
                return str(seen[k].get("at") or "") if isinstance(seen[k], dict) else ""
            seen = {k: seen[k] for k in sorted(seen, key=when, reverse=True)[:SEEN_MAX]}
        ctx.save_state(**{SEEN: seen})


CONNECTOR = TikTokConnector()
router = None
