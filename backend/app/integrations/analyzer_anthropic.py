"""Анализатор ИИ (Anthropic) публикаций: подключение для страницы «Подключения» и транспорт запросов этапа «llm»
задания анализа (app.llm).

Ключ только серверный (ANTHROPIC_API_KEY): клиенту SDK он передаётся явно, переменные окружения и профили SDK не
читаются; в браузер, журнал и ответы API ключ не попадает (Secret, маскирование logs.redact). Запрос — Messages API
(официальный SDK anthropic 1.x): структурированный ответ по JSON-схеме (output_config.format) и уровень размышления
(output_config.effort). Модель по умолчанию — Claude Sonnet 5.5 (claude-sonnet-5-5); у неё, как и у Opus 5.5,
размышление адаптивное и через {"type": "disabled"} не отключается — thinking, budget_tokens и temperature не
передаются, ответ модели не предзаполняется. Моделям с серверными резервными моделями (claude-opus-5-5, claude-opus-5, claude-fable-5-1,
claude-sonnet-5-5) добавляется fallbacks: "default" (бета server-side-fallback-2026-07-01): при отказе модели по
политике безопасности Anthropic повторяет тот же запрос на рекомендованной резервной модели в том же вызове.

Повторы SDK выключены (max_retries=0): сетевые ошибки, 429 и 5xx повторяет задание анализа с паузой и учётом
retry-after (app.llm), а не поток воркера. «Проверить подключение» — client.models.retrieve(модель): сведения о модели,
без генерации и без оплаты. Тесты подменяют CLIENT_FACTORY (сети в тестах нет).
"""
import email.utils
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..logs import redact
from ..settings import Settings, get_settings
from .base import AccessDenied, AuthExpired, CheckResult, Connector, NotConfigured, RateLimited, SourceError

try:  # пакет не установлен — подключение показывает ошибку, остальное приложение работает
    import anthropic
except ImportError:  # pragma: no cover — зависимость из requirements.txt
    anthropic = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("saq.integrations")

CODE = "anthropic"
NAME = "Анализатор ИИ (Anthropic)"
KEY_ENV = "ANTHROPIC_API_KEY"
BETA_FALLBACK = "server-side-fallback-2026-07-01"
# модели, у которых есть серверные резервные модели (fallbacks: "default"); Haiku — без них
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"})
SDK_MAX_RETRIES = 0
SDK_MISSING = "Пакет anthropic не установлен на сервере — pip install -r backend/requirements.txt"
ERROR_MAX = 300
# Цены, $ за миллион токенов: вход, выход, запись в кэш (5 мин), чтение из кэша. claude-opus-4-8 — резервная модель
# fallbacks: "default" для отказов категории cyber. Неизвестная модель считается по самой дорогой цене.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-fable-5-1": (10.0, 50.0, 12.50, 0.25),
}
MAX_PRICE = max(PRICES.values(), key=lambda p: (p[1], p[0]))


def supports_fallbacks(model: str) -> bool:
    return model in FALLBACK_MODELS


def price(model: Optional[str]) -> tuple[float, float, float, float]:
    return PRICES.get(model or "", MAX_PRICE)


def cost_usd(model: Optional[str], input_tokens: int = 0, output_tokens: int = 0, cache_creation: int = 0,
             cache_read: int = 0) -> float:
    p_in, p_out, p_cw, p_cr = price(model)
    return (input_tokens * p_in + output_tokens * p_out + cache_creation * p_cw + cache_read * p_cr) / 1_000_000


# ---------- клиент ----------

def _sdk_client(s: Settings) -> Any:
    if anthropic is None:
        raise CallError("sdk", SDK_MISSING)
    # ключ организации без привязки к рабочему пространству требует заголовок anthropic-workspace-id
    headers = {"anthropic-workspace-id": s.anthropic_workspace_id} if s.anthropic_workspace_id else None
    return anthropic.Anthropic(api_key=s.anthropic_api_key.get(), timeout=float(s.anthropic_timeout_s),
                               max_retries=SDK_MAX_RETRIES, default_headers=headers)


# Фабрика клиента Messages API: настройки → объект с .beta.messages.create(...) и .models.retrieve(...).
# Тесты подменяют её поддельным клиентом (conftest запрещает сеть).
CLIENT_FACTORY: Callable[[Settings], Any] = _sdk_client


def _close(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 — закрытие пула соединений не должно ронять анализ
            pass


# ---------- ошибки ----------

class CallError(Exception):
    """Ошибка запроса к Anthropic. kind: auth (401) | denied (403, 402) | rate (429) | server (5xx, 529, 408, 409) |
    network (сеть, тайм-аут) | bad_request (400, 404, 413, 422 …) | sdk (нет пакета) | internal. detail — по-русски,
    без секретов и без содержимого публикации."""

    RETRYABLE = frozenset({"rate", "server", "network"})

    def __init__(self, kind: str, detail: str, *, status: Optional[int] = None, retry_after: Optional[float] = None,
                 request_id: Optional[str] = None):
        self.kind, self.status, self.retry_after = kind, status, retry_after
        self.detail = redact(detail, limit=ERROR_MAX) or ""
        self.request_id = request_id
        super().__init__(self.detail)

    @property
    def retryable(self) -> bool:
        return self.kind in self.RETRYABLE

    def connector_error(self) -> Exception:
        """Ошибка подключения для «Проверить подключение» (статус подключения — jobs._failure)."""
        if self.kind == "auth":
            return AuthExpired(self.detail)
        if self.kind == "denied":
            return AccessDenied(self.detail)
        if self.kind == "rate":
            return RateLimited(self.detail, retry_after=self.retry_after or 60.0)
        return SourceError(self.detail, retryable=self.retryable)


def _seconds(raw: Any, scale: float = 1.0) -> Optional[float]:
    try:
        v = float(raw) / scale
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


def _retry_after(headers: Any) -> Optional[float]:
    """retry-after-ms (миллисекунды) или retry-after (секунды или HTTP-дата); нет или не разобрать — None."""
    get = getattr(headers, "get", None)
    if not callable(get):
        return None
    ms = _seconds(get("retry-after-ms"), 1000) if get("retry-after-ms") else None
    if ms is not None:
        return ms
    raw = get("retry-after")
    if not raw:
        return None
    secs = _seconds(raw)
    if secs is not None:
        return secs
    try:
        when = email.utils.parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)


def _api_message(e: Any) -> str:
    """Текст ошибки из тела ответа API (error.message); тело целиком не выводится."""
    body = getattr(e, "body", None)
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else None
    return redact(str(msg), limit=200) if isinstance(msg, str) and msg.strip() else ""


def classify(e: BaseException, s: Optional[Settings] = None) -> CallError:
    """Исключение SDK → CallError (самые частные классы первыми)."""
    if isinstance(e, CallError):
        return e
    s = s or get_settings()
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
    if isinstance(e, anthropic.APITimeoutError):
        return CallError("network", f"Anthropic не ответил за {s.anthropic_timeout_s:g} с")
    if isinstance(e, anthropic.APIConnectionError):
        return CallError("network", f"Сетевая ошибка при обращении к Anthropic ({type(e).__name__})")
    return CallError("internal", f"Внутренняя ошибка анализа ИИ ({type(e).__name__})")


# ---------- запрос и ответ ----------

def build_request(s: Settings, *, system: str, user: str, schema: dict) -> dict:
    """Параметры client.beta.messages.create: модель, предел токенов, структурированный ответ и уровень размышления;
    fallbacks: "default" — только моделям, у которых есть серверные резервные модели. Без thinking (размышление
    адаптивное по умолчанию и не отключается) и без предзаполнения ответа."""
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
    """Ответ модели: stop_reason, текст (JSON по схеме), кто ответил, токены, оценка стоимости."""
    stop_reason: Optional[str]
    text: str
    served_model: Optional[str]
    request_id: Optional[str] = None
    refusal_category: Optional[str] = None
    fallback: bool = False
    tokens: dict = field(default_factory=dict)  # input, output, cache_read, cache_creation
    cost_usd: float = 0.0


def _n(v: Any) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def usage_cost(usage: Any, requested: str, served: Optional[str]) -> tuple[dict, float]:
    """Токены и стоимость ответа. С серверным fallback верхний usage — только попытка, давшая ответ; полный расход —
    по usage.iterations (каждая попытка — по цене своей модели)."""
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


def to_reply(resp: Any, requested: str) -> Reply:
    blocks = list(getattr(resp, "content", None) or [])
    text = "".join(getattr(b, "text", "") or "" for b in blocks if getattr(b, "type", None) == "text")
    usage = getattr(resp, "usage", None)
    served = getattr(resp, "model", None)
    tokens, cost = usage_cost(usage, requested, served) if usage is not None else ({}, 0.0)
    its = (getattr(usage, "iterations", None) or []) if usage is not None else []
    fallback = any(getattr(b, "type", None) == "fallback" for b in blocks) or any(
        getattr(x, "type", None) == "fallback_message" for x in its)
    details = getattr(resp, "stop_details", None)
    category = getattr(details, "category", None) if details is not None else None
    rid = getattr(resp, "_request_id", None)
    return Reply(stop_reason=getattr(resp, "stop_reason", None), text=text, served_model=served,
                 request_id=rid if isinstance(rid, str) else None,
                 refusal_category=category if isinstance(category, str) else None, fallback=bool(fallback),
                 tokens=tokens, cost_usd=cost)


def send(s: Settings, request: dict) -> Reply:
    """Один запрос Messages API (без повторов SDK). Ошибки — CallError."""
    client = CLIENT_FACTORY(s)
    try:
        resp = client.beta.messages.create(**request)
    except Exception as e:  # noqa: BLE001 — любая ошибка SDK — типизированный CallError
        raise classify(e, s) from None
    finally:
        _close(client)
    return to_reply(resp, request["model"])


def retrieve_model(s: Settings) -> Any:
    """Сведения о модели ANTHROPIC_MODEL (GET /v1/models/{id}): ключ принят и модель доступна. Без генерации."""
    client = CLIENT_FACTORY(s)
    try:
        return client.models.retrieve(s.anthropic_model)
    except Exception as e:  # noqa: BLE001
        raise classify(e, s) from None
    finally:
        _close(client)


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

    def computed_status(self, db, row, now: datetime) -> Optional[tuple[str, str]]:
        """Нет пакета SDK — «ошибка»; ключ отклонён, ошибка — хранимый статус с пояснением; дневной бюджет исчерпан —
        «пауза: лимит» (вычисляется по счётчику расхода); иначе — хранимый."""
        if anthropic is None:
            return "error", SDK_MISSING
        if db is None:  # проверка из командной строки — без БД
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
        """Ключ принят и модель доступна: client.models.retrieve(ANTHROPIC_MODEL) — без генерации и без оплаты."""
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
        # имена без «token»: ingest.scrub отбрасывает такие ключи сведений как секретные
        facts = {"model": mid, "displayName": shown, "effort": s.anthropic_effort,
                 "responseLimit": int(s.anthropic_max_tokens), "dailyBudgetUsd": float(s.anthropic_daily_budget_usd),
                 "fallbacks": "default" if fb else None}
        for key, attr in (("contextWindow", "max_input_tokens"), ("outputLimit", "max_tokens")):
            v = getattr(m, attr, None)
            if isinstance(v, int) and not isinstance(v, bool):
                facts[key] = v
        # в тексте — только ID модели, без торгового имени (display_name остаётся в facts)
        detail = (f"Ключ принят, модель {mid} доступна; уровень размышления {s.anthropic_effort}, бюджет "
                  f"${float(s.anthropic_daily_budget_usd):.2f} в день"
                  + ("; при отказе модели — резервная модель Anthropic (fallbacks: default)" if fb else ""))
        return CheckResult(True, detail, facts)


CONNECTOR = AnthropicAnalyzer()
router = None
