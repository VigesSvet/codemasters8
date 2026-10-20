"""HTTP для подключений и безопасная загрузка файлов публикаций.

client() - клиент API источника: честный ASCII User-Agent из настроек, без cookie, без прокси из окружения; с base_url
клиент "привязан" к хосту: запрос на другой хост или не по https(абсолютная ссылка paging.next, CDN) отклоняется до
отправки - токен из заголовков клиента не уходит чужому серверу.

fetch_to_temp() - загрузка файла по ссылке источника во временный файл хранилища: только https(http - лишь для явно
разрешённых хостов и без учётных данных), без логина/пароля в ссылке, порты 80/443, адреса только публичные - на каждом
перенаправлении и ещё раз при подключении(PublicOnlyBackend подключается к уже проверенному IP: подмена DNS между
проверкой и подключением не проходит), не больше max_redirects перенаправлений, https→http не выполняется, учётные
данные - только нужным хостам и только по https, общий срок загрузки, без сжатия, размер и тип файла по сигнатуре.

Тесты подменяют TRANSPORT(httpx.MockTransport), RESOLVER, SLEEP и MONOTONIC. Обход защиты сайтов(подмена браузера,
cookie, решение проверок) здесь не выполняется и не допускается.
"""
import hashlib
import http.cookiejar
import ipaddress
import os
import re
import socket
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from .. import logs, media
from ..settings import get_settings


def _system_resolve(host: str, port: int) -> list[str]:
    """getaddrinfo → IP-адреса строками, без повторов, в порядке ответа."""
    out: list[str] = []
    for *_, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        ip = str(sockaddr[0])
        if ip not in out:
            out.append(ip)
    return out


TRANSPORT: httpx.BaseTransport | None = None  # тесты: httpx.MockTransport
RESOLVER: Callable[[str, int], list[str]] = _system_resolve
SLEEP: Callable[[float], None] = time.sleep
MONOTONIC: Callable[[], float] = time.monotonic
# Учётные данные в обычных заголовках загрузки запрещены: только auth_headers(уходят лишь auth_hosts по https)
FORBIDDEN_HEADERS = re.compile(r"^(authorization|cookie|x-api-key|x-.*-token)$", re.I)
REDIRECTS = (301, 302, 303, 307, 308)
_RETRYABLE = {"network", "timeout"}
_BAD_HOST = re.compile(r"[\s\x00-\x1f\x7f]")
_BOT_PATH = re.compile(r"/bot\d+:[^/]+")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_CHUNK = 64 * 1024


class DownloadError(Exception):
    """Файл не загружен. code: bad_scheme | bad_url | blocked_address | too_many_redirects | http_<status> | timeout |
    network | too_large | bad_type | host_not_allowed | cancelled; detail - по-русски, ссылка только в замаскированном
    виде; retryable - сеть, тайм-аут, HTTP 5xx и 429, прерванная остановкой воркера загрузка(повтор имеет смысл)."""

    def __init__(self, code: str, detail: str, *, retryable: bool | None = None):
        detail = logs.redact(detail) or ""
        super().__init__(detail)
        self.code, self.detail = code, detail
        if retryable is None:
            retryable = code in _RETRYABLE or code == "http_429" or code.startswith("http_5")
        self.retryable = retryable


# ---------- адреса ----------

def _embedded(addr) -> list:
    """Сам адрес и IPv4, вложенные в IPv6: ::ffff:a.b.c.d, NAT64(64:ff9b::/96, 64:ff9b:1::/48), 6to4, Teredo."""
    out = [addr]
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            out.append(addr.ipv4_mapped)
        v = int(addr)
        if addr in _NAT64:
            out.append(ipaddress.IPv4Address(v & 0xFFFFFFFF))
        if addr in _NAT64_LOCAL:  # RFC 6052, префикс /48: IPv4 в битах 48-63 и 72-87
            out.append(ipaddress.IPv4Address((((v >> 64) & 0xFFFF) << 16) | ((v >> 40) & 0xFFFF)))
        if addr.sixtofour is not None:
            out.append(addr.sixtofour)
        if addr.teredo is not None:
            out.extend(addr.teredo)  # (сервер, клиент) - клиентский адрес уже раскрыт ipaddress
    return out


def _public(a) -> bool:
    # site-local fec0::/10(RFC 3879, устаревший) ipaddress считает глобальным - проверяется отдельно
    return a.is_global and not a.is_multicast and not a.is_reserved and not (a.version == 6 and a.is_site_local)


def is_public_ip(ip: str) -> bool:
    """Адрес публичный: он сам и каждый вложенный в него IPv4 - глобальные, не multicast и не зарезервированные.
    Loopback, частные сети, link-local(169.254.x - метаданные облака), CGNAT 100.64/10, ULA fc00::/7, site-local
    fec0::/10 - не публичные."""
    try:
        addr = ipaddress.ip_address(str(ip).strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return all(_public(a) for a in _embedded(addr))


def _literal(host: str) -> str | None:
    h = host.strip("[]").split("%", 1)[0]
    try:
        ipaddress.ip_address(h)
    except ValueError:
        return None
    return h


def _resolve(host: str, port: int) -> list[str]:
    """IP-адреса хоста, все публичные; иначе DownloadError(blocked_address). Ошибка DNS - network(повторяемая)."""
    literal = _literal(host)
    if literal is not None:
        ips = [literal]
    else:
        try:
            ips = list(RESOLVER(host, port) or [])
        except DownloadError:
            raise
        except (OSError, UnicodeError) as e:
            raise DownloadError("network", f"Адрес {host} не найден ({type(e).__name__})", retryable=True) from None
    if not ips or not all(is_public_ip(i) for i in ips):
        raise DownloadError("blocked_address",
                            f"Адрес {host} относится к внутренней или служебной сети — загрузка запрещена")
    return ips


class PublicOnlyBackend(httpcore.SyncBackend):
    """Сетевой слой загрузчика: имя разрешается здесь же, при подключении, и соединение идёт на проверенный публичный IP
    (TLS: SNI и проверка сертификата - по исходному имени, httpcore передаёт его сам). Так проверка адреса и
    подключение не расходятся(DNS rebinding), и это повторяется на каждом перенаправлении."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ips = _resolve(host, port)
        return super().connect_tcp(ips[0], port, timeout, local_address, socket_options)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover - не используется
        raise DownloadError("blocked_address", "Загрузка через локальный сокет запрещена")


class _NoCookies(http.cookiejar.CookieJar):
    """Cookie не принимаются и не отправляются: ни один домен не разрешён политикой."""

    def __init__(self):
        super().__init__(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))


# ---------- ссылки ----------

def redact_url(url: str) -> str:
    """scheme://host[:port]/path - без логина/пароля, query и fragment; "/bot<id>:<token>" → "/bot***"; секреты
    маскируются. Для журналов, ошибок и MediaAsset.origin_url."""
    try:
        p = urlsplit(str(url))
        host, port = p.hostname or "", p.port
    except ValueError:
        return "(некорректная ссылка)"
    path = _BOT_PATH.sub("/bot" + logs.MASK, p.path or "")
    if not p.scheme or not host:
        return logs.redact(path, limit=512) or ""
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return logs.redact(f"{p.scheme.lower()}://{netloc}{path}", limit=512) or ""


def _norm_host(host: str) -> str | None:
    h = (host or "").strip().rstrip(".").lower()
    if not h:
        return None
    try:
        return h.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Хост совпадает с разрешённым или является его поддоменом: "scontent-x.cdninstagram.com" ⊂ "cdninstagram.com",
    "evilcdninstagram.com" - нет. Сравнение после IDNA-кодирования, без регистра и конечной точки."""
    h = _norm_host(host)
    if h is None:
        return False
    for s in allowed or ():
        n = _norm_host(s)
        if n and (h == n or h.endswith("." + n)):
            return True
    return False


def _parts(url: str):
    try:
        p = urlsplit(str(url))
        return p, p.port
    except ValueError:
        raise DownloadError("bad_url", "Некорректная ссылка на файл") from None


def check_public(url: str, *, allow_http: bool = False) -> None:
    """Ранняя проверка ссылки перед каждым запросом: https(http - только allow_http), без логина/пароля, корректный
    хост, порт не указан или 80/443, все адреса хоста публичные. При подключении PublicOnlyBackend проверяет адрес
    ещё раз."""
    p, port = _parts(url)
    scheme = p.scheme.lower()
    if scheme not in ("http", "https"):
        raise DownloadError("bad_scheme", "Ссылка на файл должна начинаться с https://")
    if scheme == "http" and not allow_http:
        raise DownloadError("bad_scheme", f"Файл по http не загружается — нужен https ({redact_url(url)})")
    if "@" in p.netloc:
        raise DownloadError("bad_url", f"Ссылка с логином или паролем не загружается ({redact_url(url)})")
    host = p.hostname
    if not host or _BAD_HOST.search(p.netloc):
        raise DownloadError("bad_url", "В ссылке на файл нет корректного адреса сервера")
    if port not in (None, 80, 443):
        raise DownloadError("bad_url", f"Порт {port} не разрешён для загрузки файлов ({redact_url(url)})")
    _resolve(host, port or (443 if scheme == "https" else 80))


# ---------- клиенты ----------

def _pinned(base_url: str):
    base = httpx.URL(base_url)
    host, port = base.host, base.port or 443

    def pin(request: httpx.Request) -> None:
        u = request.url
        if u.scheme != "https" or u.host != host or (u.port or 443) != port:
            raise DownloadError("host_not_allowed", f"Запрос к {u.host or '?'} отклонён: клиент подключения работает "
                                                    f"только с {host} по https")
    return pin


def client(*, timeout: float = 20.0, headers: dict | None = None, base_url: str = "",
           follow_redirects: bool = False) -> httpx.Client:
    """Клиент API источника: без прокси из окружения и без cookie; User-Agent - SAQ_USER_AGENT. С base_url запросы
    допускаются только на этот хост по https(event hook на каждый запрос, включая перенаправления)."""
    h = {"User-Agent": get_settings().user_agent, **(headers or {})}
    kw: dict = {}
    if base_url:
        kw["base_url"] = base_url
        kw["event_hooks"] = {"request": [_pinned(base_url)]}
    return httpx.Client(timeout=timeout, headers=h, follow_redirects=follow_redirects, trust_env=False,
                        cookies=_NoCookies(), transport=TRANSPORT, **kw)


def download_transport() -> httpx.BaseTransport:
    """Транспорт загрузчика: TRANSPORT(тесты) или HTTPTransport без повторов с PublicOnlyBackend.
    _pool._network_backend - внутреннее поле httpx 0.28 / httpcore 1.0(тест проверяет, что оно есть)."""
    if TRANSPORT is not None:
        return TRANSPORT
    t = httpx.HTTPTransport(retries=0)
    t._pool._network_backend = PublicOnlyBackend()
    return t


# ---------- загрузка файла ----------

@dataclass
class Fetched:
    """Загруженный файл: временный путь в media.tmp_dir(), размер, SHA-256, MIME по сигнатуре, итоговая ссылка
    (замаскирована)."""
    path: Path
    size: int
    sha256: str
    mime: str
    final_url: str


def _mb(nbytes: int) -> str:
    return f"{nbytes / (1024 * 1024):g}"


def _cancelled(url: str) -> DownloadError:
    return DownloadError("cancelled", f"Загрузка прервана: воркер останавливается ({redact_url(url)})", retryable=True)


def _blocks(resp: httpx.Response):
    """Тело ответа по мере поступления: каждый сетевой блок отдаётся сразу(iter_bytes без chunk_size - без буфера до
    64 КБ), длинный блок(тело, уже прочитанное транспортом) режется по _CHUNK. Так срок загрузки, размер и остановка
    воркера проверяются на каждом блоке - сервер, отдающий по байту, не растягивает загрузку за общий срок."""
    for piece in resp.iter_bytes():
        for i in range(0, len(piece), _CHUNK):
            yield piece[i:i + _CHUNK]


def _limits(timeout: float, left: float) -> httpx.Timeout:
    """Тайм-ауты запроса не дольше остатка общего срока(left, с): одно медленное чтение не уводит загрузку далеко
    за срок."""
    left = max(left, 0.001)
    return httpx.Timeout(connect=min(10.0, left), read=min(float(timeout), left), write=min(10.0, left),
                         pool=min(10.0, left))


def _save(resp: httpx.Response, url: str, max_bytes: int, allowed_mime: set, deadline: float,
          cancel: Callable[[], bool] | None = None) -> Fetched:
    fd, name = tempfile.mkstemp(dir=media.tmp_dir(), prefix="dl-")
    path = Path(name)
    try:
        h, size, head = hashlib.sha256(), 0, b""
        with os.fdopen(fd, "wb") as f:
            try:
                # кодирование, кроме identity, отклонено до чтения: iter_bytes отдаёт байты как есть(и тело,
                # уже прочитанное транспортом, - iter_raw его не отдал бы)
                for chunk in _blocks(resp):
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadError("too_large", f"Файл больше {_mb(max_bytes)} МБ ({redact_url(url)})")
                    if MONOTONIC() > deadline:
                        raise DownloadError("timeout", f"Загрузка не уложилась во время ({redact_url(url)})",
                                            retryable=True)
                    if cancel is not None and cancel():
                        raise _cancelled(url)
                    if len(head) < media.HEAD:
                        head += chunk[:media.HEAD - len(head)]
                    h.update(chunk)
                    f.write(chunk)
            except httpx.TimeoutException:
                raise DownloadError("timeout", f"Источник не отдал файл вовремя ({redact_url(url)})") from None
            except httpx.TransportError as e:
                raise DownloadError("network", f"Связь прервалась при загрузке ({type(e).__name__}, "
                                               f"{redact_url(url)})") from None
        mime = media.sniff(head)
        if mime is None or mime not in allowed_mime:
            raise DownloadError("bad_type", f"Тип файла не подходит ({mime or 'не распознан по содержимому'}, "
                                            f"{redact_url(url)})")
        return Fetched(path, size, h.hexdigest(), mime, redact_url(url))
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def fetch_to_temp(url: str, *, max_bytes: int, allowed_mime: Iterable[str],
                  allowed_hosts: Iterable[str] | None = None, auth_headers: dict | None = None,
                  auth_hosts: Iterable[str] = (), timeout: float = 30.0, total_timeout: float | None = None,
                  max_redirects: int = 3, headers: dict | None = None,
                  cancel: Callable[[], bool] | None = None) -> Fetched:
    """Файл по ссылке → временный файл в media.tmp_dir()(дальше - media.store_file). Ошибки - DownloadError;
    временный файл при ошибке удаляется. Учётные данные заголовками в headers - ValueError(только auth_headers).
    cancel() → True - загрузка прерывается перед запросом или на следующем блоке(DownloadError "cancelled"):
    остановка воркера не ждёт большой файл до SIGKILL."""
    extra = dict(headers or {})
    bad = sorted(k for k in extra if FORBIDDEN_HEADERS.match(str(k)))
    if bad:
        raise ValueError(f"Заголовки с учётными данными передаются только через auth_headers: {', '.join(bad)}")
    start = MONOTONIC()
    deadline = start + (total_timeout or min(600.0, 60 + max_bytes / 262144))
    allowed_mime = set(allowed_mime)
    auth_hosts = tuple(auth_hosts or ())
    allowed = tuple(allowed_hosts) if allowed_hosts is not None else None
    p, _ = _parts(url)
    if allowed is not None and not host_allowed(p.hostname or "", allowed):
        raise DownloadError("host_not_allowed", f"Хост {p.hostname or '?'} не входит в разрешённые для загрузки")
    http_ok = allowed is not None and not auth_headers  # http - только разрешённым хостам и без учётных данных
    base = {"User-Agent": get_settings().user_agent, "Accept-Encoding": "identity", **extra}
    current, hops = str(url), 0
    with httpx.Client(transport=download_transport(), trust_env=False, cookies=_NoCookies(),
                      timeout=_limits(timeout, deadline - start), follow_redirects=False) as c:
        while True:
            left = deadline - MONOTONIC()
            if left < 0:
                raise DownloadError("timeout", f"Загрузка не уложилась во время ({redact_url(current)})",
                                    retryable=True)
            if cancel is not None and cancel():
                raise _cancelled(current)
            cp, _ = _parts(current)
            chost = cp.hostname or ""
            check_public(current, allow_http=http_ok and host_allowed(chost, allowed))
            h = dict(base)
            if auth_headers and cp.scheme.lower() == "https" and host_allowed(chost, auth_hosts):
                h.update(auth_headers)
            try:
                resp = c.send(c.build_request("GET", current, headers=h, timeout=_limits(timeout, left)), stream=True)
            except DownloadError:
                raise
            except httpx.TimeoutException:
                raise DownloadError("timeout", f"Источник не ответил вовремя ({redact_url(current)})") from None
            except httpx.InvalidURL:
                raise DownloadError("bad_url", f"Некорректная ссылка на файл ({redact_url(current)})") from None
            except httpx.DecodingError:
                raise DownloadError("bad_type", f"Ответ не декодируется ({redact_url(current)})") from None
            except (httpx.HTTPError, OSError) as e:
                raise DownloadError("network", f"Сетевая ошибка при загрузке ({type(e).__name__}, "
                                               f"{redact_url(current)})") from None
            try:
                if resp.status_code in REDIRECTS and resp.headers.get("location"):
                    nxt = urljoin(current, resp.headers["location"])
                    if cp.scheme.lower() == "https" and urlsplit(nxt).scheme.lower() == "http":
                        raise DownloadError("bad_scheme", f"Перенаправление с https на http не выполняется "
                                                          f"({redact_url(nxt)})")
                    hops += 1
                    if hops > max_redirects:
                        raise DownloadError("too_many_redirects", f"Слишком много перенаправлений (больше "
                                                                  f"{max_redirects}): {redact_url(nxt)}")
                    current = nxt
                    continue
                if not 200 <= resp.status_code < 300:
                    raise DownloadError(f"http_{resp.status_code}", f"Источник ответил HTTP {resp.status_code} "
                                                                    f"({redact_url(current)})")
                enc = (resp.headers.get("content-encoding") or "").strip().lower()
                if enc and enc != "identity":
                    raise DownloadError("bad_type", f"Сжатый ответ ({enc}) не принимается ({redact_url(current)})")
                length = (resp.headers.get("content-length") or "").strip()
                if length.isdigit() and int(length) > max_bytes:
                    raise DownloadError("too_large", f"Файл больше {_mb(max_bytes)} МБ ({redact_url(current)})")
                return _save(resp, current, max_bytes, allowed_mime, deadline, cancel)
            finally:
                resp.close()


def retry_after(headers, default: float) -> float:
    """Retry-After(секунды или HTTP-дата) в секундах, в пределах 1…86400; нет или не разобрать - default."""
    raw = None
    if headers is not None:
        raw = headers.get("retry-after") if hasattr(headers, "get") else None
        if raw is None and isinstance(headers, Mapping):
            raw = next((v for k, v in headers.items() if str(k).lower() == "retry-after"), None)
    secs = float(default)
    if raw is not None:
        s = str(raw).strip()
        try:
            secs = float(int(s)) if s.isdigit() else (
                parsedate_to_datetime(s).astimezone(UTC) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            secs = float(default)
    return float(min(max(secs, 1.0), 86400.0))
