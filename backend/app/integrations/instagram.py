"""Instagram - публикации профессиональных аккаунтов через Meta Graph API · Business Discovery(docs/INTEGRATIONS.md,
"Instagram").

Сервер запросов - graph.facebook.com/{META_GRAPH_VERSION}/ (Instagram API with Facebook Login), не graph.instagram.com.
Токен уходит только заголовком Authorization: Bearer, клиент привязан к хосту Graph API: абсолютная ссылка на другой
хост(CDN, чужой сервер) отклоняется до отправки, ссылки paging.next не открываются и не пишутся в журнал - следующая
страница запрашивается курсором .after(…) в fields. Если задан META_APP_SECRET, к каждому запросу добавляется
appsecret_proof(HMAC-SHA256 токена) - он зарегистрирован как секрет и маскируется. Параметр input_token запроса
debug_token Graph API принимает только в адресе - адрес запроса нигде не выводится.

Проверка: разрешения токена, профессиональный Instagram(META_IG_USER_ID, иначе Страницы токена, иначе Страницы
бизнес-портфолио META_BUSINESS_ID), срок действия токена(debug_token). Сбор: для каждого аккаунта из настроек -
business_discovery с публикациями; карусели - со слайдами(children), видео - с обложкой. Файлы скачиваются при сборе
только с CDN Instagram/Facebook: ссылки на медиа временные.

Обход страниц: пока на странице все публикации новые(или аккаунт ещё не загружен целиком - первичная загрузка/перерыв),
не больше IG_FIRST_LOAD_MAX за запуск. Business Discovery не присылает paging.next - только курсоры before/after,
поэтому следующая страница запрашивается всегда, пока есть новый курсор after; конец - пустая страница, нет курсора или
курсор повторился(на последней странице это один лишний запрос). Курсоры не сохраняются; отметка "загружен"
(state.accounts[u].seeded) снимается в начале обхода и ставится только после его обычного завершения - прерванный обход
(лимит, ошибка, время, перезапуск воркера, курсор в неожиданном формате) в следующий раз идёт вглубь снова, без
пропусков и дублей.

Ошибки - по конверту {"error": {...}}, не по HTTP-статусу: истёкший токен - "истёк доступ", нет разрешения - "нет
доступа", лимиты Meta - пауза, использование лимита ≥ 80 % - сбор останавливается и следующий откладывается на час.
Аккаунт, которого нет или который не профессиональный, - ошибка записи; если так со всеми - ошибка настроек.
"""
import hashlib
import hmac
import json
import logging
import math
import re
from datetime import datetime, timedelta, UTC
from typing import TYPE_CHECKING, Any, Optional
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

import httpx

from .. import models
from ..ads_service import safe_url
from ..logs import redact, register_secret
from ..settings import Settings
from . import http
from .base import (AccessDenied, AuthExpired, BadConfig, CheckResult, ConfigError, Connector, ConnectorError, MediaIn,
                   NotConfigured, Publication, RateLimited, SourceError, local_time)

if TYPE_CHECKING:
    from ..models import Collector
    from .context import RunContext

log = logging.getLogger("saq.integrations.instagram")

GRAPH = "https://graph.facebook.com/{version}/"
CDN_HOSTS = ("cdninstagram.com", "fbcdn.net")
NEEDED = {"instagram_basic", "instagram_manage_insights", "pages_read_engagement"}
RECOMMENDED = {"pages_show_list", "business_management"}

TIMEOUT_S = 20.0
USAGE_STOP_PCT = 80  # использование лимита Graph API, после которого запросы в этом запуске прекращаются
USAGE_PAUSE_S = 3600
EXPIRY_WARN_DAYS = 7
SHORT_TOKEN_H = 24
TIME_RESERVE_S = 60
PAGE_LIMIT_MAX = 100
MESSAGE_MAX = 200
# Необязательные поля Business Discovery: ответ(#100) о таком поле - запрос без него, отметка в state.compat
OPTIONAL_FIELDS = ("children", "name", "thumbnail_url", "media_product_type")
MEDIA_FIELDS = ("id", "caption", "media_type", "media_product_type", "media_url", "thumbnail_url", "permalink",
                "timestamp")
CHILD_FIELDS = ("id", "media_type", "media_url", "thumbnail_url")
AUTH_CODES = {190, 102}
RATE_CODES = {4, 17, 32, 341, 613, 80002}  # 341 - "Application limit reached"(троттлинг приложения)
TRANSIENT_CODES = {1, 2}
ACCOUNT_MISSING_SUBCODE = 2207013

USERNAME = re.compile(r"^[a-z0-9._]{1,30}$")
IG_HOSTS = ("instagram.com", "www.instagram.com", "m.instagram.com")
# Первые сегменты пути instagram.com, которые не являются именем профиля
RESERVED_PATHS = frozenset({"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct", "about",
                            "developer", "legal", "web"})
_VERSION = re.compile(r"^v\d{1,3}\.\d{1,2}$")
_ID = re.compile(r"^\d{1,30}$")
_CURSOR = re.compile(r"^[A-Za-z0-9_\-=+/%.]{1,1024}$")

TOKEN_INVALID = "Токен Meta недействителен или истёк — нужна повторная авторизация"
EMPTY_ACCOUNTS = "Список аккаунтов пуст — добавьте аккаунты в настройках"
NO_IG_ACCOUNT = "Не найден профессиональный Instagram, связанный со Страницей"
TIME_UP = "Время запуска истекло — продолжим в следующий раз"
SHORT_TOKEN = "Токен Meta краткосрочный — получите долгосрочный (60 дней) или токен системного пользователя"
COPYRIGHT = "Instagram не выдал файл (возможны авторские права) — сохранена обложка"
NO_FILE = "Instagram не выдал файл (возможны авторские права)"
NO_CHILDREN = "Слайды карусели недоступны через Business Discovery — сохранено только первое изображение"
EXPIRY_UNKNOWN = "Срок действия токена не проверен"
ACCOUNT_MISSING = "аккаунт не найден или не профессиональный"


# ---------- Graph API ----------

class GraphError(Exception):
    """Ошибка из конверта ответа Graph API {"error": {"code", "error_subcode", "message", "is_transient"}}."""

    def __init__(self, code: int, subcode: int | None, message: str, transient: bool, status: int):
        self.code, self.subcode, self.message, self.transient, self.status = code, subcode, message, transient, status
        super().__init__(f"Graph API: код {code}" + (f"/{subcode}" if subcode else "") + f" — {message}")


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):  # OverflowError - Infinity в JSON(json.loads его принимает)
        return None


def _num(v: Any) -> float:
    """Число из заголовка использования лимита; не число, NaN и бесконечность - 0."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def graph_error(body: Any, status: int = 200) -> GraphError | None:
    """GraphError из тела ответа или None(ошибки в конверте нет)."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None
    msg = redact(str(err.get("message") or err.get("error_user_msg") or ""), limit=MESSAGE_MAX) or ""
    return GraphError(_int(err.get("code")) or 0, _int(err.get("error_subcode")), msg, bool(err.get("is_transient")),
                      status)


def appsecret_proof(token: str, app_secret: str) -> str:
    """appsecret_proof Graph API: HMAC-SHA256 токена доступа с ключом App Secret(hex)."""
    return hmac.new(app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def graph_get(client: httpx.Client, path: str, params: dict, s: Settings, *,
              token: str | None = None) -> tuple[dict, httpx.Headers]:
    """GET {path} относительно базового адреса Graph API. Токен - заголовком клиента(token - другой токен, например
    токен приложения для debug_token); при META_APP_SECRET - appsecret_proof к этому токену. Ошибки - типизированные
    ошибки подключения(map_error; исходная GraphError - в __cause__). Абсолютные ссылки(paging.next) не открываются."""
    path = str(path)
    if "://" in path or path.startswith("//"):
        raise SourceError("Ссылки из ответа Graph API не открываются — только пути от базового адреса",
                          retryable=False)
    query = {k: v for k, v in (params or {}).items() if v is not None}
    secret = s.meta_app_secret.get()
    if secret:
        proof = appsecret_proof(token or s.meta_user_access_token.get(), secret)
        register_secret(proof)
        query["appsecret_proof"] = proof
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        resp = client.get(path.lstrip("/"), params=query, headers=headers)
    except httpx.TimeoutException:
        raise SourceError("Graph API не ответил вовремя", retryable=True) from None
    except httpx.RequestError as e:  # сеть, а также тело, которое не распаковалось(DecodingError)
        raise SourceError(f"Сетевая ошибка при обращении к Graph API ({type(e).__name__})", retryable=True) from None
    try:
        body = resp.json()
    except ValueError:
        body = None
    g = graph_error(body, resp.status_code)
    if g is not None:
        raise map_error(g, resp.headers) from g
    if resp.status_code >= 400 or not isinstance(body, dict):
        what = f"HTTP {resp.status_code}" if resp.status_code >= 400 else "ответ не в формате JSON"
        raise SourceError(f"Graph API: {what}", retryable=resp.status_code >= 500 or resp.status_code == 429)
    return body, resp.headers


def _usage_json(headers: Any, name: str) -> Any:
    if headers is None:
        return None
    h = headers if isinstance(headers, httpx.Headers) else httpx.Headers(dict(headers))
    raw = h.get(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _buc_entries(headers: Any) -> Iterable[dict]:
    """Записи X-Business-Use-Case-Usage: {"<business id>": [{"type", "call_count", …}]}."""
    buc = _usage_json(headers, "x-business-use-case-usage")
    if not isinstance(buc, dict):
        return
    for entries in buc.values():
        for e in entries if isinstance(entries, list) else [entries]:
            if isinstance(e, dict):
                yield e


def usage_pct(headers: Any) -> int:
    """Наибольший процент использования лимита из X-App-Usage и X-Business-Use-Case-Usage(call_count,
    total_cputime, total_time)."""
    best = 0.0
    parts = ("call_count", "total_cputime", "total_time")
    app = _usage_json(headers, "x-app-usage")
    if isinstance(app, dict):
        best = max([best] + [_num(app.get(k)) for k in parts])
    for e in _buc_entries(headers):
        best = max([best] + [_num(e.get(k)) for k in parts])
    return int(best)


def regain_seconds(headers: Any) -> float | None:
    """estimated_time_to_regain_access(минуты) из X-Business-Use-Case-Usage → секунды; нет или 0 - None."""
    mins = max([_num(e.get("estimated_time_to_regain_access")) for e in _buc_entries(headers)] or [0.0])
    return mins * 60 if mins > 0 else None


def map_error(err: GraphError, headers: Any = None) -> ConnectorError:
    """Ошибка Graph API → типизированная ошибка подключения(по коду из конверта, не по HTTP-статусу)."""
    c, msg = err.code, err.message or f"код {err.code}"
    if c in AUTH_CODES:
        return AuthExpired(TOKEN_INVALID)
    if c == 10 or 200 <= c <= 299:
        return AccessDenied(f"Нет разрешения Meta: {msg}")
    if c in RATE_CODES:
        secs = regain_seconds(headers) or 3600.0
        return RateLimited(secs, f"Meta ограничила частоту запросов (код {c}) — пауза {max(1, round(secs / 60))} мин")
    if c == 368:
        return RateLimited(3600, "Meta временно ограничила действия приложения (код 368) — пауза 60 мин")
    if c in TRANSIENT_CODES or err.transient:
        return SourceError(f"Временная ошибка Graph API (код {c}): {msg}", retryable=True)
    sub = f"/{err.subcode}" if err.subcode else ""
    return SourceError(f"Ошибка Graph API (код {c}{sub}): {msg}", retryable=False)


def _cause(e: BaseException) -> GraphError | None:
    return e.__cause__ if isinstance(e.__cause__, GraphError) else None


def _account_missing(g: GraphError | None) -> bool:
    """Business Discovery не нашёл аккаунт(нет такого или он не профессиональный): 110/2207013 или(#100) "Invalid
    user id"."""
    if g is None:
        return False
    return g.code == 110 or g.subcode == ACCOUNT_MISSING_SUBCODE or (
        g.code == 100 and "invalid user id" in g.message.lower())


def _unsupported_field(g: GraphError | None, enabled: Iterable[str]) -> str | None:
    """(#100) о необязательном поле, которое ещё запрашивается, → имя поля."""
    if g is None or g.code != 100:
        return None
    for f in enabled:
        if re.search(rf"(?<![A-Za-z0-9_.]){re.escape(f)}(?![A-Za-z0-9_])", g.message):
            return f
    return None


def bd_fields(username: str, *, limit: int, after: str | None = None, with_children: bool = True,
              with_name: bool = True, without: Iterable[str] = ()) -> str:
    """fields запроса Business Discovery: профиль и страница публикаций(media.limit(N)[.after(C)]); without -
    необязательные поля публикации, которые Business Discovery не отдаёт(thumbnail_url, media_product_type)."""
    drop = set(without)
    media = [f for f in MEDIA_FIELDS if f not in drop]
    if with_children:
        media.append("children{" + ",".join(f for f in CHILD_FIELDS if f not in drop) + "}")
    page = f"media.limit({int(limit)})" + (f".after({after})" if after else "")
    top = ["id", "username"] + (["name"] if with_name else []) + ["media_count"]
    return f"business_discovery.username({username}){{{','.join(top)},{page}{{{','.join(media)}}}}}"


# ---------- аккаунты ----------

def normalize_account(s: str) -> str:
    """"@x", "x", "https://www.instagram.com/x/" → "x"(нижний регистр, ^[a-z0-9._]{1,30}$); иначе ValueError."""
    raw = str(s or "").strip()
    v = raw
    low = v.lower()
    if "://" in v or low.startswith(tuple(h + "/" for h in IG_HOSTS)):
        try:
            p = urlsplit(v if "://" in v else "https://" + v)
            host = (p.hostname or "").lower()
        except ValueError:
            raise ValueError(f"Неверный аккаунт Instagram: {raw}") from None
        if p.scheme.lower() not in ("http", "https") or host not in IG_HOSTS:
            raise ValueError(f"Неверный аккаунт Instagram: {raw} — нужна ссылка на профиль instagram.com")
        parts = [x for x in p.path.split("/") if x]
        if not parts or parts[0].lower() in RESERVED_PATHS:
            raise ValueError(f"Неверный аккаунт Instagram: {raw} — нужна ссылка на профиль, а не на публикацию")
        v = parts[0]
    v = v.lstrip("@").lower()
    if not USERNAME.fullmatch(v):
        raise ValueError(f"Неверный аккаунт Instagram: {raw} — нужно имя профиля (@имя) или ссылка на профиль")
    return v


def _parse_time(v: Any) -> datetime | None:
    """"2026-10-09T11:10:23+0000" → UTC."""
    if not v:
        return None
    s = str(v).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z").astimezone(UTC)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return models.as_utc(dt)


def _https(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip().lower().startswith("https://") else None


def _permalink(v: Any, username: str) -> str:
    """Ссылка на публикацию на instagram.com; не https, другой хост или ссылка, которую приём отбросил бы(safe_url), -
    ссылка на профиль."""
    url = safe_url(_https(v))  # разбор адреса safe_url уже проверил - urlsplit ниже не бросает
    if url and (urlsplit(url).hostname or "").lower() in IG_HOSTS:
        return url
    return f"https://www.instagram.com/{username}/"


def _ts(v: Any) -> datetime | None:
    """Unix-время из ответа Graph API → UTC; 0, не число и вне диапазона datetime - None."""
    n = _int(v)
    if not n:
        return None
    try:
        return datetime.fromtimestamp(n, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _from_iso(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return models.as_utc(datetime.fromisoformat(str(v)))
    except ValueError:
        return None


# ---------- публикация ----------

def _fetch(ctx: "RunContext", url: str, key: str, kind: str, role: str | None) -> MediaIn:
    """Файл с CDN Instagram - лениво: скачивается при приёме, только если ещё не загружен."""
    def run() -> MediaIn:
        return ctx.download_media(url, key=key, kind=kind, role=role, allowed_hosts=CDN_HOSTS)
    return MediaIn(key=key, kind=kind, role=role, fetch=run, origin_url=http.redact_url(url))


def _marker(key: str, kind: str, role: str | None, error: str) -> MediaIn:
    """Файла нет в ответе Instagram: запись "недоступен" с причиной."""
    return MediaIn(key=key, kind=kind, role=role, status="unavailable", error=error)


def _node_media(ctx: "RunContext", node: dict, key: str, role: str | None) -> list[MediaIn]:
    """Файлы одной публикации или слайда: изображение; видео + обложка; нет media_url - "недоступен"(+ обложка)."""
    ntype = str(node.get("media_type") or "").upper()
    url, thumb = _https(node.get("media_url")), _https(node.get("thumbnail_url"))
    out: list[MediaIn] = []
    if ntype == "VIDEO":
        out.append(_fetch(ctx, url, key, "video", role) if url else
                   _marker(key, "video", role, COPYRIGHT if thumb else NO_FILE))
        if thumb:
            out.append(_fetch(ctx, thumb, f"{key}:cover", "image", "cover"))
    elif url:
        out.append(_fetch(ctx, url, key, "image", role))
    elif thumb:
        out.append(_marker(key, "image", role, COPYRIGHT))
        out.append(_fetch(ctx, thumb, f"{key}:cover", "image", "cover"))
    else:
        out.append(_marker(key, "image", role, NO_FILE))
    return out


def _children(m: dict) -> list | None:
    ch = m.get("children")
    data = ch.get("data") if isinstance(ch, dict) else None
    return [c for c in data if isinstance(c, dict) and c.get("id")] if isinstance(data, list) else None


def to_publication(m: dict, username: str, ctx: "RunContext") -> Publication:
    """Публикация Business Discovery → Publication. Файлы - ленивые загрузки с CDN; файлы, которых Instagram не отдал,
    - записи "недоступен" с причиной(повторно не считаются, если такая запись уже есть)."""
    mid = str(m["id"]).strip()
    if not _ID.fullmatch(mid):
        raise ValueError("ID публикации Instagram не из цифр")
    mtype = str(m.get("media_type") or "").upper()
    if mtype == "CAROUSEL_ALBUM":
        children = _children(m)
        media: list[MediaIn] = []
        if children:
            for ch in children:
                media += _node_media(ctx, ch, f"ig:{str(ch['id']).strip()}", "slide")
        else:  # слайды недоступны(поле children отключено): первое изображение и отметка о недоступных слайдах
            url = _https(m.get("media_url"))
            if url:
                kind = "video" if ".mp4" in (urlsplit(url).path or "").lower() else "image"
                media.append(_fetch(ctx, url, f"ig:{mid}", kind, "slide"))
            media.append(_marker(f"ig:{mid}:children", "image", "slide", NO_CHILDREN))
    else:
        media = _node_media(ctx, m, f"ig:{mid}", None)
    markers = [i for i, x in enumerate(media) if x.status == "unavailable"]
    if markers:  # уже записанная отметка "недоступен" не учитывается в каждом запуске заново
        k = ctx.known(mid, namespace="instagram")
        if k is not None:
            for i in markers:
                x = media[i]
                if x.key in k.failed_keys or x.key in k.downloaded_keys:
                    media[i] = MediaIn(key=x.key, kind=x.kind, role=x.role)
    product = m.get("media_product_type")
    return Publication(platform="instagram", external_id=mid, account=f"@{username}",
                       url=_permalink(m.get("permalink"), username), text=str(m.get("caption") or ""),
                       collected_at=ctx.now(), published_at=_parse_time(m.get("timestamp")), media=media,
                       extra={"mediaType": mtype or None, "productType": str(product) if product else None},
                       raw=dict(m))


# ---------- подключение ----------

def _token(s: Settings) -> str:
    token = s.meta_user_access_token.get()
    if not token:
        raise NotConfigured("Нет на сервере: META_USER_ACCESS_TOKEN")
    if not token.isascii() or not token.isprintable() or any(ch.isspace() for ch in token):
        raise BadConfig("META_USER_ACCESS_TOKEN содержит недопустимые символы — скопируйте токен заново")
    return token


def _client(ctx: "RunContext", s: Settings, token: str) -> httpx.Client:
    version = (s.meta_graph_version or "").strip()
    if not _VERSION.fullmatch(version):
        raise BadConfig("META_GRAPH_VERSION должна быть вида v26.0")
    return ctx.client(timeout=TIMEOUT_S, base_url=GRAPH.format(version=version),
                      headers={"Authorization": f"Bearer {token}"})


def _digits(value: str, name: str) -> str:
    v = (value or "").strip()
    if not _ID.fullmatch(v):
        raise BadConfig(f"{name} должен состоять из цифр")
    return v


def _pick_ig(pages: Any, page_id: str) -> str | None:
    """ID профессионального Instagram из списка Страниц: Страница META_PAGE_ID, иначе первая со связанным Instagram."""
    found: list[tuple[str, str]] = []
    for p in pages if isinstance(pages, list) else []:
        ig = p.get("instagram_business_account") if isinstance(p, dict) else None
        ig_id = str(ig.get("id") or "").strip() if isinstance(ig, dict) else ""
        if _ID.fullmatch(ig_id):
            found.append((str(p.get("id") or ""), ig_id))
    for pid, ig_id in found:
        if page_id and pid == page_id.strip():
            return ig_id
    return found[0][1] if found else None


def discover_ig_user(get: Callable[[str, dict], dict], ctx: "RunContext", s: Settings) -> str:
    """Профессиональный Instagram токена: Страницы токена(me/accounts), иначе Страницы бизнес-портфолио
    (META_BUSINESS_ID/owned_pages). Нет - BadConfig."""
    params = {"fields": "id,name,instagram_business_account", "limit": 100}
    pages: Any = []
    try:
        pages = get("me/accounts", params).get("data")
    except (AccessDenied, SourceError) as e:
        if getattr(e, "retryable", False):
            raise
        ctx.note(f"Страницы токена не получены: {e.detail}", "warn")
    ig_id = _pick_ig(pages, s.meta_page_id)
    if ig_id is None and s.meta_business_id:
        business = _digits(s.meta_business_id, "META_BUSINESS_ID")
        ig_id = _pick_ig(get(f"{business}/owned_pages", params).get("data"), s.meta_page_id)
    if ig_id is None:
        raise BadConfig(NO_IG_ACCOUNT)
    return ig_id


def _ig_source(s: Settings) -> str:
    """По чему найден Instagram(отпечаток токена, META_PAGE_ID, META_BUSINESS_ID): сменилось - найденный прежде
    igUserId(collectors.state) не используется, иначе сбор шёл бы от чужого аккаунта до следующей проверки."""
    return "|".join((s.key_fp("META_USER_ACCESS_TOKEN") or "", (s.meta_page_id or "").strip(),
                     (s.meta_business_id or "").strip()))


class _Run:
    """Один запуск сбора: клиент, ID нашего Instagram, совместимость полей, лимит Graph API, остановка по времени."""

    def __init__(self, ctx: "RunContext", s: Settings, client: httpx.Client, accounts: list[str]):
        self.ctx, self.s, self.c, self.accounts = ctx, s, client, accounts
        saved = ctx.state.get("compat")
        self.compat = {k: bool(v) for k, v in (saved.items() if isinstance(saved, dict) else ()) if
                       k in OPTIONAL_FIELDS}
        self.ig_id = ""
        self.limited = False  # использование лимита ≥ 80 %: больше запросов к Graph API в этом запуске нет
        self.stopped = False  # время запуска истекло / воркер останавливается
        self.children_noted = False

    def ok(self, f: str) -> bool:
        return self.compat.get(f, True)

    def time_up(self) -> bool:
        if not self.stopped and (self.ctx.stopping() or self.ctx.time_left() < TIME_RESERVE_S):
            self.stopped = True
            self.ctx.mark_partial(TIME_UP)
        return self.stopped

    def usage(self, headers: Any) -> None:
        p = usage_pct(headers)
        if p >= USAGE_STOP_PCT and not self.limited:
            self.limited = True
            self.ctx.mark_partial(f"Использовано {p}% лимита Graph API — запросы остановлены до следующего запуска")
            self.ctx.postpone(USAGE_PAUSE_S, f"Пауза: использовано {p}% лимита Graph API")

    def get(self, path: str, params: dict) -> dict:
        body, headers = graph_get(self.c, path, params, self.s)
        self.usage(headers)
        return body

    def ig_user_id(self) -> str:
        if self.s.meta_ig_user_id:
            return _digits(self.s.meta_ig_user_id, "META_IG_USER_ID")
        cached = str(self.ctx.state.get("igUserId") or "")
        if _ID.fullmatch(cached) and self.ctx.state.get("igUserFor") == _ig_source(self.s):
            return cached
        found = discover_ig_user(self.get, self.ctx, self.s)
        self.ctx.save_state(igUserId=found, igUserFor=_ig_source(self.s))
        return found

    def discovery(self, username: str, limit: int, after: str | None) -> dict:
        """Страница business_discovery; (#100) о необязательном поле - запрос без него(совместимость сохраняется)."""
        for _ in range(len(OPTIONAL_FIELDS) + 1):
            fields = bd_fields(username, limit=limit, after=after, with_children=self.ok("children"),
                               with_name=self.ok("name"),
                               without=[f for f in ("thumbnail_url", "media_product_type") if not self.ok(f)])
            try:
                return self.get(self.ig_id, {"fields": fields})
            except ConnectorError as e:
                g = _cause(e)
                if _account_missing(g):
                    raise _AccountMissing() from None
                f = _unsupported_field(g, [x for x in OPTIONAL_FIELDS if self.ok(x)])
                if f is None:
                    raise
                self.compat[f] = False
                self.ctx.save_state(compat=dict(self.compat))
                self.ctx.note(f"Поле {f} недоступно в Business Discovery — без него", "warn")
        raise SourceError("Business Discovery отклоняет запрос и без необязательных полей", retryable=False)

    def save_account(self, u: str, **patch) -> None:
        accounts = {k: v for k, v in dict(self.ctx.state.get("accounts") or {}).items() if k in self.accounts}
        accounts[u] = {**dict(accounts.get(u) or {}), **patch}
        self.ctx.save_state(accounts=accounts)

    def item(self, m: dict, username: str) -> str:
        """Одна публикация → приём; итог: created | updated | unchanged | foreign | failed."""
        mid = str(m.get("id") or "").strip()
        if not mid:
            self.ctx.item_failed(f"@{username}: публикация без ID в ответе Graph API")
            return "failed"
        try:
            pub = to_publication(m, username, self.ctx)
        except (ValueError, TypeError, KeyError) as e:
            self.ctx.item_failed(f"@{username}: публикация {mid[:40]} не разобрана ({type(e).__name__})")
            return "failed"
        res = self.ctx.ingest(pub)
        if res.status in ("created", "updated") and not self.children_noted \
                and any(x.key.endswith(":children") for x in pub.media):
            self.children_noted = True
            self.ctx.mark_partial(NO_CHILDREN)
        return res.status

    def account(self, u: str) -> None:
        """Обход публикаций одного аккаунта(см. описание модуля)."""
        ctx, s = self.ctx, self.s
        state = dict((ctx.state.get("accounts") or {}).get(u) or {})
        was_seeded = bool(state.get("seeded"))
        cap = max(1, int(s.ig_first_load_max))
        size = max(1, min(int(s.ig_page_size), PAGE_LIMIT_MAX))
        total, after, cursors, username = 0, None, set(), u
        if was_seeded:  # отметка снимается до обхода: прерванный обход следующий запуск пройдёт вглубь снова
            self.save_account(u, seeded=False)
        while True:
            body = self.discovery(u, min(size, cap - total), after)
            bd = body.get("business_discovery")
            if not isinstance(bd, dict):
                raise _AccountMissing()
            got = str(bd.get("username") or "").strip().lower()
            username = got if USERNAME.fullmatch(got) else u
            media = bd.get("media") if isinstance(bd.get("media"), dict) else {}
            data = [x for x in media.get("data") or [] if isinstance(x, dict)]
            all_new = bool(data)
            for m in data:
                if self.time_up():
                    return
                if self.item(m, username) != "created":
                    all_new = False
                total += 1
            paging = media.get("paging") if isinstance(media.get("paging"), dict) else {}
            page_cursors = paging.get("cursors")
            cursor = page_cursors.get("after") if isinstance(page_cursors, dict) else None
            cursor = str(cursor) if cursor else None
            # Business Discovery не присылает paging.next(поле media раскрывается в fields): конец списка - пустая
            # страница, нет курсора after или курсор повторился. Ссылка paging.next, если вдруг придёт, не
            # открывается и не выводится(в ней токен) - следующая страница только по курсору.
            if not data or not cursor or cursor in cursors:
                break
            if was_seeded and not all_new:
                break
            if total >= cap:
                if all_new:
                    ctx.mark_partial(f"@{username}: новых публикаций больше {cap} — старые могли быть пропущены")
                break
            if self.limited or self.time_up():
                return
            if not _CURSOR.fullmatch(cursor):  # курсор подставляется в fields: чужие символы не отправляются
                ctx.mark_partial(f"@{username}: курсор следующей страницы в неожиданном формате — обход остановлен, "
                                 "более старые публикации не получены")
                return  # без отметки "загружен": следующий запуск снова пройдёт аккаунт вглубь
            cursors.add(cursor)
            after = cursor
        self.save_account(u, seeded=True, username=username, walkedAt=models.iso(ctx.now()), lastItems=total)


class _AccountMissing(Exception):
    """Business Discovery не нашёл аккаунт из списка."""


class InstagramConnector(Connector):
    code = "instagram"
    kind = "collector"
    platform = "instagram"
    group = "collectors"
    name = "Instagram"
    via = "Meta Graph API · Business Discovery"
    required_env = ("META_USER_ACCESS_TOKEN",)
    config_fields = ("accounts",)
    default_interval_min = 60
    interval_bounds = (30, 1440)
    health_check_min = 1440
    secret_refs = ("env:META_USER_ACCESS_TOKEN",)
    limitation = ("Только публикации профессиональных аккаунтов из списка; таргетированные показы конкретным людям так "
                  "не видны.")

    def normalize_item(self, field_name: str, value: str) -> str:
        try:
            return "@" + normalize_account(value)
        except ValueError as e:
            raise ConfigError(field_name, str(e), value) from None

    def can_enable(self, row: Optional["Collector"]) -> str | None:
        """Краткосрочный токен(истекает в ближайшие сутки по последней проверке) - сбор не включается."""
        facts = (row.facts or {}) if row is not None else {}
        exp = _from_iso(facts.get("expiresAt"))
        if exp is not None and exp <= models.utcnow() + timedelta(hours=SHORT_TOKEN_H):
            return SHORT_TOKEN
        return None

    def check(self, ctx: "RunContext") -> CheckResult:
        s = ctx.settings
        token = _token(s)
        usage = 0
        with _client(ctx, s, token) as c:
            def get(path: str, params: dict, **kw) -> dict:
                nonlocal usage
                body, headers = graph_get(c, path, params, s, **kw)
                usage = max(usage, usage_pct(headers))
                return body

            perms = get("me/permissions", {"limit": 100}).get("data")
            granted = sorted({str(p.get("permission")) for p in (perms if isinstance(perms, list) else [])
                              if isinstance(p, dict) and p.get("status") == "granted" and p.get("permission")})
            missing = sorted(NEEDED - set(granted))
            if missing:
                raise AccessDenied(f"Нет разрешений: {', '.join(missing)}. Если роль на Странице выдана через Business "
                                   "Manager, нужны также ads_read или ads_management")
            if s.meta_ig_user_id:
                ig_id = _digits(s.meta_ig_user_id, "META_IG_USER_ID")
            else:
                ig_id = discover_ig_user(get, ctx, s)
                ctx.save_state(igUserId=ig_id, igUserFor=_ig_source(s))
            user = get(ig_id, {"fields": "id,username"})
            username = str(user.get("username") or "").strip().lower()
            if not username:
                raise BadConfig(NO_IG_ACCOUNT)
            facts: dict = {"account": f"@{username}", "igUserId": str(user.get("id") or ig_id), "scopes": granted}
            rec = sorted(RECOMMENDED - set(granted))
            if rec:
                ctx.note(f"Нет рекомендуемых разрешений: {', '.join(rec)} — Страница находится только по "
                         "META_IG_USER_ID или META_BUSINESS_ID")
            expiry = self._expiry(get, ctx, s, token, facts)
        if usage:
            facts["usagePct"] = usage
        return CheckResult(True, f"Подключено: @{username}. {expiry}", facts)

    @staticmethod
    def _expiry(get: Callable[..., dict], ctx: "RunContext", s: Settings, token: str, facts: dict) -> str:
        """Срок действия токена(debug_token): токеном приложения, если заданы META_APP_ID и META_APP_SECRET, иначе
        самим токеном(работает для разработчика приложения)."""
        app_id, app_secret = (s.meta_app_id or "").strip(), s.meta_app_secret.get()
        app_token = f"{app_id}|{app_secret}" if app_id and app_secret else None
        if app_token:
            register_secret(app_token)
        try:
            data = get("debug_token", {"input_token": token}, token=app_token).get("data")
        except ConnectorError as e:
            log.info("Instagram: срок действия токена не проверен (%s)", e.code)
            ctx.note(f"{EXPIRY_UNKNOWN}: {e.detail}", "warn")
            return EXPIRY_UNKNOWN
        if not isinstance(data, dict):
            return EXPIRY_UNKNOWN
        if data.get("is_valid") is False:
            raise AuthExpired(TOKEN_INVALID)
        now = ctx.now()
        parts: list[str] = []
        if "expires_at" in data:
            exp = _ts(data.get("expires_at"))
            facts["expiresAt"] = models.iso(exp)
            if exp is None:
                parts.append("Токен без срока действия")
            elif exp - now < timedelta(days=EXPIRY_WARN_DAYS):
                parts.append(f"Токен истекает {local_time(exp, '%d.%m.%Y')} — продлите его (см. docs/INTEGRATIONS.md)")
            else:
                parts.append(f"Токен действует до {local_time(exp, '%d.%m.%Y')}")
        if "data_access_expires_at" in data:
            dax = _ts(data.get("data_access_expires_at"))
            facts["dataAccessExpiresAt"] = models.iso(dax)
            if dax is not None and dax - now < timedelta(days=EXPIRY_WARN_DAYS):
                parts.append(f"Доступ к данным истекает {local_time(dax, '%d.%m.%Y')} — пройдите вход Meta заново")
        return ". ".join(parts) if parts else EXPIRY_UNKNOWN

    def collect(self, ctx: "RunContext") -> None:
        s = ctx.settings
        accounts: list[str] = []
        bad: list[str] = []
        for raw in ctx.config.get("accounts") or []:
            try:
                u = normalize_account(raw)
            except ValueError:
                bad.append(str(raw)[:60])
                continue
            if u not in accounts:
                accounts.append(u)
        if not accounts and not bad:
            raise BadConfig(EMPTY_ACCOUNTS)
        token = _token(s)
        missing = [f"{b} (неверный формат)" for b in bad]
        for b in bad:
            ctx.item_failed(f"{b}: неверный аккаунт Instagram — исправьте список в настройках")
        if not accounts:
            raise BadConfig(f"Ни один аккаунт из списка не доступен: {', '.join(missing)}")
        with _client(ctx, s, token) as c:
            run = _Run(ctx, s, c, accounts)
            run.ig_id = run.ig_user_id()
            for u in accounts:
                if run.limited or run.time_up():
                    break
                try:
                    run.account(u)
                except _AccountMissing:
                    ctx.item_failed(f"@{u}: {ACCOUNT_MISSING}")
                    missing.append(f"@{u}")
        if missing and len(missing) == len(accounts) + len(bad):
            raise BadConfig(f"Ни один аккаунт из списка не доступен: {', '.join(missing)}")


CONNECTOR = InstagramConnector()
router = None
