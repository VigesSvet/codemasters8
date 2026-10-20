"""Анализатор ИИ(Anthropic) публикаций: подключение для страницы "Подключения" и транспорт запросов этапа "llm"
задания анализа(app.llm).

Ключ только серверный(ANTHROPIC_API_KEY): клиенту SDK он передаётся явно, поэтому переменные окружения и профили
учётных данных SDK не читаются, а адрес API зафиксирован(API_URL - ANTHROPIC_BASE_URL не может увести ключ на другой
хост); в браузер, журнал и ответы API ключ не попадает(Secret, маскирование logs.redact). Запрос - Messages
API(официальный SDK anthropic 1.x) потоком(SSE): структурированный ответ по JSON-схеме(output_config.format) и уровень
размышления(output_config.effort). ANTHROPIC_TIMEOUT_S ограничивает ожидание соединения и очередного фрагмента ответа,
а не весь ответ: пока модель думает, API шлёт ping, поэтому длинная генерация не обрывается на клиенте, пока сервер
продолжает её и выставляет к оплате. Модель по умолчанию - Claude Sonnet 5.5(claude-sonnet-5-5); у неё, как и у
Opus 5.5, размышление адаптивное и через {"type": "disabled"} не отключается - thinking, budget_tokens и temperature
не передаются, ответ модели не предзаполняется. Моделям с серверными резервными моделями(claude-opus-5-5, claude-opus-5, claude-fable-5-1,
claude-sonnet-5-5) добавляется fallbacks: "default"(бета server-side-fallback-2026-07-01): при отказе модели по
политике безопасности Anthropic повторяет тот же запрос на рекомендованной резервной модели в том же вызове.

Повторы SDK выключены(max_retries=0): сетевые ошибки, 429 и 5xx повторяет задание анализа с паузой и учётом
retry-after(app.llm), а не поток воркера. CallError.unbilled - запрос заведомо не оплачен(только тогда app.llm
возвращает резерв бюджета): API отклонил его ответом об ошибке до генерации, соединение с API не установлено или
запрос не ушёл вовсе(клиент SDK не создан, запрос не собран).
Тайм-аут и обрыв после отправки запроса - не unbilled: модель могла ответить, и запрос оплачен. "Проверить
подключение" - client.models.retrieve(модель): сведения о модели, без генерации и без оплаты. Тесты подменяют
CLIENT_FACTORY(сети в тестах нет).
"""
import email.utils
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from ..logs import redact
from ..settings import Settings, get_settings
from .base import AccessDenied, AuthExpired, CheckResult, Connector, NotConfigured, RateLimited, SourceError

try:  # пакет не установлен - подключение показывает ошибку, остальное приложение работает
    import anthropic
    import httpx2  # HTTP-клиент SDK anthropic 1.x(его зависимость)
except ImportError:  # pragma: no cover - зависимость из requirements.txt
    anthropic = None  # type: ignore[assignment]
    httpx2 = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("saq.integrations")

CODE = "anthropic"
NAME = "Анализатор ИИ (Anthropic)"
KEY_ENV = "ANTHROPIC_API_KEY"
API_URL = "https://api.anthropic.com"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
# модели, у которых есть серверные резервные модели(fallbacks: "default"); Haiku - без них
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"})
SDK_MAX_RETRIES = 0
SDK_MISSING = "Пакет anthropic не установлен на сервере — pip install -r backend/requirements.txt"
ERROR_MAX = 300
RETRY_AFTER_MAX_S = 86400.0  # retry-after больше суток не принимается(и не переполняет datetime у вызывающих)
# Цены, $ за миллион токенов: вход, выход, запись в кэш(5 мин), чтение из кэша. claude-opus-4-8, claude-opus-5 и
# claude-sonnet-5 - резервные модели fallbacks: "default"(у claude-sonnet-5-5 - claude-sonnet-5): их попытки в
# usage.iterations считаются по своей цене. Неизвестная модель считается по самой дорогой цене.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 2.50, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-fable-5-1": (10.0, 50.0, 12.50, 0.25),
}
MAX_PRICE = max(PRICES.values(), key=lambda p: (p[1], p[0]))


def supports_fallbacks(model: str) -> bool:
    return model in FALLBACK_MODELS


def price(model: str | None) -> tuple[float, float, float, float]:
    return PRICES.get(model or "", MAX_PRICE)


def cost_usd(model: str | None, input_tokens: int = 0, output_tokens: int = 0, cache_creation: int = 0,
             cache_read: int = 0) -> float:
    p_in, p_out, p_cw, p_cr = price(model)
    return (input_tokens * p_in + output_tokens * p_out + cache_creation * p_cw + cache_read * p_cr) / 1_000_000


# ---------- клиент ----------

def _sdk_client(s: Settings) -> Any:
    if anthropic is None:
        raise CallError("sdk", SDK_MISSING, unbilled=True)
    # ключ организации без привязки к рабочему пространству требует заголовок anthropic-workspace-id
    headers = {"anthropic-workspace-id": s.anthropic_workspace_id} if s.anthropic_workspace_id else None
    return anthropic.Anthropic(api_key=s.anthropic_api_key.get(), base_url=API_URL,
                               timeout=float(s.anthropic_timeout_s), max_retries=SDK_MAX_RETRIES,
                               default_headers=headers)


# Фабрика клиента Messages API: настройки → объект с .beta.messages.stream(...) и .models.retrieve(...).
# Тесты подменяют её поддельным клиентом(conftest запрещает сеть).
CLIENT_FACTORY: Callable[[Settings], Any] = _sdk_client


def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # соединения клиента - не повод терять ответ модели
            log.debug("Клиент Anthropic не закрыт", exc_info=True)


# ---------- ошибки ----------

class CallError(Exception):
    """Ошибка запроса к Anthropic. kind: auth(401) | denied(403, 402) | rate(429) | server(5xx, 529, 408, 409) |
    network(сеть, тайм-аут, обрыв ответа) | bad_request(400, 404, 413, 422 …) | sdk(нет пакета) | internal. detail -
    по-русски, без секретов и без содержимого публикации. unbilled - запрос заведомо не оплачен(API отклонил его до
    генерации или запрос не ушёл); по умолчанию - мог быть оплачен."""

    RETRYABLE = frozenset({"rate", "server", "network"})

    def __init__(self, kind: str, detail: str, *, status: int | None = None, retry_after: float | None = None,
                 request_id: str | None = None, unbilled: bool = False):
        self.kind, self.status, self.retry_after = kind, status, retry_after
        self.detail = redact(detail, limit=ERROR_MAX) or ""
        self.request_id = request_id
        self.unbilled = unbilled
        super().__init__(self.detail)

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE

    def connector_error(self) -> Exception:
        """Ошибка подключения для "Проверить подключение"(статус подключения - jobs._failure)."""
        if self.kind == "auth":
            return AuthExpired(self.detail)
        if self.kind == "denied":
            return AccessDenied(self.detail)
        if self.kind == "rate":
            return RateLimited(self.detail, retry_after=self.retry_after or 60.0)
        return SourceError(self.detail, retryable=self.retryable)


def _seconds(raw: Any, scale: float = 1.0) -> float | None:
    try:
        v = float(raw) / scale
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


def _retry_after(headers: Any) -> float | None:
    """retry-after-ms(миллисекунды) или retry-after(секунды или HTTP-дата), не больше RETRY_AFTER_MAX_S; нет или не
    разобрать - None."""
    get = getattr(headers, "get", None)
    if not callable(get):
        return None
    secs = _seconds(get("retry-after-ms"), 1000)
    if secs is None:
        raw = get("retry-after")
        if not raw:
            return None
        secs = _seconds(raw)
        if secs is None:
            try:
                when = email.utils.parsedate_to_datetime(str(raw))
            except (TypeError, ValueError):
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            secs = max((when - datetime.now(UTC)).total_seconds(), 0.0)
    return min(secs, RETRY_AFTER_MAX_S)


def _api_message(e: Any) -> str:
    """Текст ошибки из тела ответа API(error.message); тело целиком не выводится."""
    body = getattr(e, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return redact(str(msg), limit=200) if isinstance(msg, str) and msg.strip() else ""


def _api_error(e: Any) -> bool:
    """Тело ответа - ошибка самого API({"type": "error", "error": {...}}), а не страница прокси или шлюза."""
    body = getattr(e, "body", None)
    return isinstance(body, dict) and body.get("type") == "error" and isinstance(body.get("error"), dict)


def _unbilled(e: Any) -> bool:
    """Ошибка, при которой запрос заведомо не оплачен. HTTP 4xx(400, 401, 403, 404, 413, 429 …) - API отклонил запрос
    до генерации; 5xx(500, 529 …) с телом ошибки API - тоже до генерации(в потоке ошибка после начала ответа приходит
    событием error при HTTP 200 и сюда не относится); 5xx без него(шлюз) - неизвестно. Сетевая ошибка - только если
    соединение не установлено(ConnectError, ConnectTimeout, PoolTimeout, ProxyError): тайм-аут чтения и обрыв после
    отправки запроса - модель могла ответить."""
    if isinstance(e, anthropic.APIStatusError):
        code = int(getattr(e, "status_code", 0) or 0)
        return 400 <= code < 500 or (code >= 500 and _api_error(e))
    if isinstance(e, anthropic.APIConnectionError):
        return isinstance(e.__cause__, (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout,
                                        httpx2.ProxyError))
    return False


def classify(e: BaseException, s: Settings | None = None) -> CallError:
    """Исключение SDK → CallError(самые частные классы первыми) с признаком unbilled."""
    if isinstance(e, CallError):
        return e
    err = _classify(e, s or get_settings())
    err.unbilled = anthropic is not None and _unbilled(e)
    return err


def _classify(e: BaseException, s: Settings) -> CallError:
    if anthropic is None:
        return CallError("internal", f"Внутренняя ошибка анализа ИИ ({type(e).__name__})")
    rid = getattr(e, "request_id", None)
    rid = rid if isinstance(rid, str) else None
    if isinstance(e, anthropic.AuthenticationError):
        return CallError("auth", f"Anthropic отклонил ключ {KEY_ENV} (HTTP 401) — замените ключ, перезапустите API и "
                                 "воркер и нажмите «Проверить подключение»", status=401, request_id=rid)
    if isinstance(e, anthropic.PermissionDeniedError):
        return CallError("denied", "Нет доступа к Anthropic API или к модели (HTTP 403) — проверьте права ключа и "
                                   "ANTHROPIC_MODEL", status=403, request_id=rid)
    if isinstance(e, anthropic.NotFoundError):
        return CallError("bad_request", f"Модель «{s.anthropic_model}» не найдена или недоступна организации (HTTP 404) "
                                        "— проверьте ANTHROPIC_MODEL", status=404, request_id=rid)
    if isinstance(e, anthropic.RateLimitError):
        ra = _retry_after(getattr(getattr(e, "response", None), "headers", None))
        return CallError("rate", "Ограничение частоты Anthropic (HTTP 429)" + (f" — повтор через {ra:.0f} с" if ra else ""),
                         status=429, retry_after=ra, request_id=rid)
    if isinstance(e, anthropic.BadRequestError):
        msg = _api_message(e)
        return CallError("bad_request", "Anthropic отклонил запрос (HTTP 400)" + (f": {msg}" if msg else ""),
                         status=400, request_id=rid)
    if isinstance(e, anthropic.APIStatusError):
        code = int(getattr(e, "status_code", 0) or 0)
        if code < 400:  # событие error в потоке после HTTP 200: ответ начат и прерван сервером
            body = getattr(e, "body", None)
            etype = body["error"].get("type") if _api_error(e) else None
            what = "Anthropic перегружен" if etype == "overloaded_error" else "Ошибка сервера Anthropic"
            return CallError("server", f"{what} — ответ прерван", request_id=rid)
        ra = _retry_after(getattr(getattr(e, "response", None), "headers", None))
        if code == 402:
            return CallError("denied", "Проблема оплаты аккаунта Anthropic (HTTP 402) — проверьте баланс в консоли",
                             status=code, request_id=rid)
        if code >= 500 or code in (408, 409):
            what = "Anthropic перегружен" if code == 529 else "Ошибка сервера Anthropic"
            return CallError("server", f"{what} (HTTP {code})", status=code, retry_after=ra, request_id=rid)
        msg = _api_message(e)
        return CallError("bad_request", f"Anthropic отклонил запрос (HTTP {code})" + (f": {msg}" if msg else ""),
                         status=code, request_id=rid)
    # httpx2.* - ошибка чтения потока ответа: SDK передаёт её без обёртки
    if isinstance(e, (anthropic.APITimeoutError, httpx2.TimeoutException)):
        return CallError("network", f"Anthropic не ответил за {s.anthropic_timeout_s:g} с")
    if isinstance(e, (anthropic.APIConnectionError, httpx2.TransportError)):
        return CallError("network", f"Сетевая ошибка при обращении к Anthropic ({type(e).__name__})")
    return CallError("internal", f"Внутренняя ошибка анализа ИИ ({type(e).__name__})")


# ---------- запрос и ответ ----------

def build_request(s: Settings, *, system: str, user: str, schema: dict) -> dict:
    """Параметры client.beta.messages.stream(те же, что у create, без stream): модель, предел токенов, структурированный
    ответ и уровень размышления; fallbacks: "default" - только моделям, у которых есть серверные резервные модели. Без
    thinking(размышление адаптивное по умолчанию и не отключается) и без предзаполнения ответа."""
    model = s.anthropic_model
    req: dict = {"model": model, "max_tokens": int(s.anthropic_max_tokens), "system": system,
                 "messages": [{"role": "user", "content": user}],
                 "output_config": {"effort": s.anthropic_effort, "format": {"type": "json_schema", "schema": schema}}}
    if supports_fallbacks(model):
        req["betas"] = [BETA_FALLBACK]
        req["fallbacks"] = "default"
    return req


@dataclass
class Reply:
    """Ответ модели: stop_reason, текст(JSON по схеме), кто ответил, токены, оценка стоимости."""
    stop_reason: str | None
    text: str
    served_model: str | None
    request_id: str | None = None
    refusal_category: str | None = None
    fallback: bool = False
    tokens: dict = field(default_factory=dict)  # input, output, cache_read, cache_creation
    cost_usd: float = 0.0


def _n(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def usage_cost(usage: Any, requested: str, served: str | None) -> tuple[dict, float]:
    """Токены и стоимость ответа. С серверным fallback верхний usage - только попытка, давшая ответ; полный расход -
    по usage.iterations(каждая попытка - по цене своей модели)."""
    entries = [x for x in (getattr(usage, "iterations", None) or []) if hasattr(x, "input_tokens")]
    tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    total = 0.0
    for x, model in ([(x, getattr(x, "model", None) or requested) for x in entries] if entries
                     else [(usage, served or requested)]):
        i, o = _n(getattr(x, "input_tokens", 0)), _n(getattr(x, "output_tokens", 0))
        cr, cc = _n(getattr(x, "cache_read_input_tokens", 0)), _n(getattr(x, "cache_creation_input_tokens", 0))
        tokens["input"] += i
        tokens["output"] += o
        tokens["cache_read"] += cr
        tokens["cache_creation"] += cc
        total += cost_usd(str(model), i, o, cc, cr)
    return tokens, total


def to_reply(resp: Any, requested: str, request_id: str | None = None) -> Reply:
    """Ответ SDK → Reply. Текст - только блоки после последнего блока fallback: частичный ответ отказавшейся
    модели(если он есть) не склеивается с ответом резервной. request_id - заголовок request-id ответа(у сообщения,
    собранного из потока, своего нет)."""
    blocks = list(getattr(resp, "content", None) or [])
    cut = max((i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"), default=-1)
    text = "".join(getattr(b, "text", "") or "" for b in blocks[cut + 1:] if getattr(b, "type", None) == "text")
    usage = getattr(resp, "usage", None)
    served = getattr(resp, "model", None)
    tokens, cost = usage_cost(usage, requested, served) if usage is not None else ({}, 0.0)
    its = (getattr(usage, "iterations", None) or []) if usage is not None else []
    fallback = any(getattr(b, "type", None) == "fallback" for b in blocks) or any(
        getattr(x, "type", None) == "fallback_message" for x in its)
    details = getattr(resp, "stop_details", None)
    category = getattr(details, "category", None) if details is not None else None
    rid = request_id or getattr(resp, "_request_id", None)
    return Reply(stop_reason=getattr(resp, "stop_reason", None), text=text, served_model=served,
                 request_id=rid if isinstance(rid, str) else None,
                 refusal_category=category if isinstance(category, str) else None, fallback=bool(fallback),
                 tokens=tokens, cost_usd=cost)


def _call(s: Settings, op: Callable[[Any], Any]) -> Any:
    """op(клиент) с новым клиентом SDK. Любая ошибка - и при создании клиента - CallError: вызывающий(app.llm)
    возвращает резерв бюджета только по CallError с unbilled. Внутренняя ошибка(не ответ API) - в журнал с
    трассировкой."""
    client = None
    try:
        client = CLIENT_FACTORY(s)
        return op(client)
    except Exception as e:
        err = classify(e, s)
        if client is None:  # клиент не создан - запрос не ушёл
            err.unbilled = True
        if err.kind == "internal":
            log.warning("Запрос к Anthropic: %s", err.detail, exc_info=e)
        raise err from None
    finally:
        if client is not None:
            _close(client)


def _stream(s: Settings, client: Any, request: dict) -> tuple[Any, str | None]:
    """Потоковый запрос до message_stop: (собранное сообщение, request-id). stream() только собирает запрос(отправка -
    при входе в with), поэтому его ошибка - запрос не ушёл(unbilled). Ошибка чтения потока - CallError с request-id
    ответа(по нему запрос виден в консоли Anthropic); поток кончился без message_stop - обрыв ответа. После
    message_stop поток дальше не читается: сбой на хвосте соединения не превращает полученный ответ в ошибку и повтор."""
    try:
        manager = client.beta.messages.stream(**request)
    except Exception as e:
        err = classify(e, s)
        err.unbilled = True
        raise err from e  # причина - в трассировке внутренней ошибки(_call)
    with manager as stream:
        rid = stream.request_id
        started = stopped = False
        try:
            for event in stream:
                started = started or event.type == "message_start"
                if event.type == "message_stop":
                    stopped = True
                    break
        except (anthropic.APIError, httpx2.HTTPError) as e:  # прочие - внутренняя ошибка(_call)
            err = classify(e, s)
            err.request_id = err.request_id or rid
            raise err from None
        if not (started and stopped):
            raise CallError("network", "Ответ Anthropic оборвался до завершения", request_id=rid)
        return stream.current_message_snapshot, rid


def send(s: Settings, request: dict) -> Reply:
    """Один запрос Messages API потоком(без повторов SDK). Ошибки - CallError."""
    resp, rid = _call(s, lambda client: _stream(s, client, request))
    return to_reply(resp, request["model"], rid)


def retrieve_model(s: Settings) -> Any:
    """Сведения о модели ANTHROPIC_MODEL(GET /v1/models/{id}): ключ принят и модель доступна. Без генерации."""
    return _call(s, lambda client: client.models.retrieve(s.anthropic_model))


# ---------- подключение ----------

class AnthropicAnalyzer(Connector):
    code = CODE
    kind = "analyzer"
    platform = None
    group = "analyzer"
    name = NAME
    via = "Messages API · структурированный ответ (модель — ANTHROPIC_MODEL)"
    required_env = (KEY_ENV,)
    capabilities = {"test": True, "collect": False, "toggle": False, "configure": False, "connect": False}
    secret_refs = (f"env:{KEY_ENV}",)
    labels = {"ready": "готов", "working": "анализирует публикации", "rate_limited": "пауза: лимит"}
    limitation = ("Гипотезы для аналитика: признаки продажи, суммы и кандидаты ЖК с дословными цитатами из текста и "
                  "OCR. Статус, привязку к ЖК и вывод о нарушении ИИ не задаёт. Текст публикации и OCR отправляются в "
                  "Anthropic; дневной бюджет — {ANTHROPIC_DAILY_BUDGET_USD} USD.")

    def computed_status(self, db, row, now: datetime) -> tuple[str, str] | None:
        """Нет пакета SDK - "ошибка"; ключ отклонён, ошибка - хранимый статус с пояснением; дневной бюджет исчерпан -
        "пауза: лимит"(вычисляется по счётчику расхода); иначе - хранимый."""
        if anthropic is None:
            return "error", SDK_MISSING
        if db is None:  # проверка из командной строки - без БД
            return None
        if row is not None and row.status in ("expired", "denied", "error"):
            return None
        from .. import llm

        s = get_settings()
        if float(s.anthropic_daily_budget_usd) <= 0:
            return "off", "ANTHROPIC_DAILY_BUDGET_USD = 0 — анализ ИИ выключен"
        if llm.budget_exhausted(db, s, now):
            spent = llm.spent_today(db, now)
            return "rate_limited", (f"Дневной бюджет анализа ИИ исчерпан: ${spent:.2f} из "
                                    f"${float(s.anthropic_daily_budget_usd):.2f} — продолжится после полуночи "
                                    "(Усть-Каменогорск)")
        return None

    def check(self, ctx: "RunContext") -> CheckResult:
        """Ключ принят и модель доступна: client.models.retrieve(ANTHROPIC_MODEL) - без генерации и без оплаты."""
        s = ctx.settings
        if self.missing(s):
            raise NotConfigured(self.missing_detail(s))
        if anthropic is None:
            raise SourceError(SDK_MISSING, retryable=False)
        try:
            m = retrieve_model(s)
        except CallError as e:
            raise e.connector_error() from None
        mid = str(getattr(m, "id", None) or s.anthropic_model)
        shown = str(getattr(m, "display_name", None) or mid)
        fb = supports_fallbacks(s.anthropic_model)
        # имена без "token": ingest.scrub отбрасывает такие ключи сведений как секретные
        facts = {"model": mid, "displayName": shown, "effort": s.anthropic_effort,
                 "responseLimit": int(s.anthropic_max_tokens), "dailyBudgetUsd": float(s.anthropic_daily_budget_usd),
                 "fallbacks": "default" if fb else None}
        for key, attr in (("contextWindow", "max_input_tokens"), ("outputLimit", "max_tokens")):
            v = getattr(m, attr, None)
            if isinstance(v, int) and not isinstance(v, bool):
                facts[key] = v
        # в тексте - только ID модели, без торгового имени(display_name остаётся в facts)
        detail = (f"Ключ принят, модель {mid} доступна; уровень размышления {s.anthropic_effort}, бюджет "
                  f"${float(s.anthropic_daily_budget_usd):.2f} в день"
                  + ("; при отказе модели — резервная модель Anthropic (fallbacks: default)" if fb else ""))
        return CheckResult(True, detail, facts)


CONNECTOR = AnthropicAnalyzer()
router = None
