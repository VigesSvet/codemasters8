"""Маскирование секретов в логах и текстах ошибок.

Два уровня: фабрика записей(logging.setLogRecordFactory) маскирует msg, args, текст исключения и stack_info каждой
записи сразу при создании - это видят и обработчики, добавленные позже(pytest caplog); форматтер-обёртка
RedactingFormatter маскирует уже отформатированную строку у обработчиков root и uvicorn - на случай, когда секрет
собирается только при форматировании. Секреты - значения из Settings.secrets() и зарегистрированные во время работы
(register_secret: токены OLX, StringSession, phone_code_hash, OAuth code/state, appsecret_proof).
"""
import logging
import numbers
import re
import sys
import threading
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, quote_plus

from . import settings as _settings

MASK = "***"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
NOISY = ("httpx", "httpcore", "httpx2", "httpcore2", "anthropic", "telethon")  # SDK anthropic 1.x - на httpx2
WRAPPED = ("", "uvicorn", "uvicorn.access", "uvicorn.error")
REGISTER_MIN = 8  # короче - не регистрируется(маскировались бы случайные совпадения в обычном тексте)
SETTINGS_MIN = 4
# Аргумент записи журнала длиннее - обрезается(путь запроса в журнале доступа, ответ источника): маскирование и вывод
# не тратят время и место на мегабайты. Маскируется окно с запасом CLIP_WINDOW - секрет на границе обрезки не остаётся
# видимым наполовину.
LOG_ARG_MAX = 16 * 1024
CLIP_WINDOW = 4096

# Имена параметров запроса, значения которых не выводятся: "name=***"
SECRET_PARAMS = ("access_token", "refresh_token", "token", "key", "api_key", "apikey", "code", "state", "signature",
                 "client_secret", "password", "hash", "appsecret_proof", "oh", "oe", "sig")
_QUERY = re.compile(r"(?:(?<=[?&;\s,(\"'])|^)((?:%s|x-amz-[a-z0-9-]+)=)[^&#\s\"'<>]+" % "|".join(SECRET_PARAMS),
                    re.I | re.M)
_JSON_KEYS = re.compile(r"([\"'](?:access_token|refresh_token|client_secret|password|token|api_key|apikey|secret|"
                        r"phone_code_hash|session|authorization)[\"']\s*:\s*[\"'])[^\"']+", re.I)
# Шаблоны линейные: длинная строка вида "x-x-x-…" или "a.a.a.…"(путь запроса в журнале доступа uvicorn) не должна
# вызывать квадратичный перебор - маскирование идёт в цикле событий API. Отсюда "x-(сегмент-){1,8}token" вместо
# "x-[a-z0-9-]*-token" и схема URL, проверяемая ретроспективой фиксированной длины вместо "\b[a-z][a-z0-9+.-]*://".
_HEADERS = re.compile(r"\b(x-admin-token|x-api-key|x-(?:[a-z0-9]++-){1,8}token|cookie|set-cookie|authorization)"
                      r"([\"']?\s*[:=]\s*[\"']?)(?!(?:Bearer|Basic)\b)[^\s\"',;]+", re.I)
_BOT_PATH = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_BOT_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}")
_BEARER = re.compile(r"\b(Bearer)\s+[^\s\"',;<>]+", re.I)
_BASIC = re.compile(r"\b(Basic)\s+[A-Za-z0-9+/=]{8,}", re.I)
_USERINFO = re.compile(r"(?<=[a-z0-9+.-]://)[^/\s@?#]++@", re.I)
# %-заполнители формата logging: при наличии args маскируется только текст между ними("key=%s" остаётся форматом)
_PLACEHOLDER = re.compile(r"(%(?:\([^)]{0,128}\))?[#0 +-]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?[diouxXeEfFgGcrsa%])")

_REGISTERED: set[str] = set()
_LOCK = threading.Lock()
_CACHE: list = [(None, None)]  # [(набор секретов, regex)] - одна ячейка: замена атомарна
_installed = False


def _variants(value: str) -> set[str]:
    return {value, quote(value, safe=""), quote_plus(value)}


def register_secret(value: str | None) -> None:
    """Секрет, появившийся во время работы(токены OLX, StringSession, phone_code_hash, OAuth code/state,
    appsecret_proof). Значения короче 8 символов не регистрируются. Маскируются value, quote(value, safe=""),
    quote_plus(value)."""
    if not value or not isinstance(value, str) or len(value) < REGISTER_MIN:
        return
    with _LOCK:
        _REGISTERED.update(_variants(value))


def _secret_re() -> re.Pattern | None:
    try:
        configured = _settings.get_settings().secrets()
    except Exception:  # маскирование не должно ломать запись в лог
        configured = []
    with _LOCK:
        values = set(_REGISTERED)
    for v in configured:
        if len(v) >= SETTINGS_MIN:
            values |= _variants(v)
    key = frozenset(values)
    cached_key, rx = _CACHE[0]
    if cached_key != key:
        pattern = "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True)) if values else None
        rx = re.compile(pattern) if pattern else None
        _CACHE[0] = (key, rx)
    return rx


def redact(text: str | None, limit: int | None = 500) -> str | None:
    """Секреты Settings.secrets() + зарегистрированные(длинные первыми) → "***"; query-параметры access_token,
    refresh_token, token, key, code, state, signature, client_secret, password, hash, appsecret_proof, oh, oe, sig, X-Amz-*
    → "name=***"; "/bot<digits>:<token>/" → "/bot***/"; "Bearer <x>" → "Bearer ***"; userinfo в URL → удалить.
    limit=None - без обрезки."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if text:
        rx = _secret_re()
        if rx is not None:
            text = rx.sub(MASK, text)
        text = _USERINFO.sub("", text)
        text = _BOT_PATH.sub("/bot" + MASK, text)
        text = _BOT_TOKEN.sub(MASK, text)
        text = _BEARER.sub(r"\1 " + MASK, text)
        text = _BASIC.sub(r"\1 " + MASK, text)
        text = _HEADERS.sub(r"\1\2" + MASK, text)
        text = _JSON_KEYS.sub(r"\1" + MASK, text)
        text = _QUERY.sub(r"\1" + MASK, text)
    if limit is not None and len(text) > limit:
        text = text[: max(limit - 1, 0)] + "…"
    return text


def _clipped(text: str) -> str:
    """redact без обрезки для обычных строк; длиннее LOG_ARG_MAX - маскируется только начало(с запасом CLIP_WINDOW)
    и обрезается до LOG_ARG_MAX с "…"."""
    if len(text) <= LOG_ARG_MAX:
        return redact(text, limit=None)
    return redact(text[:LOG_ARG_MAX + CLIP_WINDOW], limit=None)[:LOG_ARG_MAX] + "…"


def _redact_msg(msg: str, has_args: bool) -> str:
    if not has_args:
        return _clipped(msg)
    parts = _PLACEHOLDER.split(msg)  # формат с заполнителями не обрезается - иначе запись не отформатируется
    return "".join(p if i % 2 else redact(p, limit=None) for i, p in enumerate(parts))


def _arg(a: Any) -> Any:
    if a is None or isinstance(a, (bool, numbers.Number)):
        return a
    return _clipped(a if isinstance(a, str) else str(a))


def _scrub(record: logging.LogRecord) -> None:
    args = record.args
    if record.msg is not None and not isinstance(record.msg, str):
        record.msg = str(record.msg)
    if isinstance(record.msg, str):
        record.msg = _redact_msg(record.msg, bool(args))
    if isinstance(args, tuple):
        record.args = tuple(_arg(a) for a in args)
    elif isinstance(args, Mapping):
        record.args = {k: _arg(v) for k, v in args.items()}
    exc = record.exc_info
    if isinstance(exc, tuple) and exc and exc[0] is not None:
        record.exc_text = redact(logging.Formatter().formatException(exc), limit=None)
    if record.stack_info:
        record.stack_info = redact(record.stack_info, limit=None)


def _factory(prev):
    def factory(*args, **kwargs):
        record = prev(*args, **kwargs)
        try:
            _scrub(record)
        except Exception:  # pragma: no cover - запись в лог важнее маскирования одной записи
            pass
        return record

    factory._saq_redacting = True  # type: ignore[attr-defined]
    factory._saq_prev = prev  # type: ignore[attr-defined]
    return factory


class RedactingFormatter(logging.Formatter):
    """Обёртка над чужим форматтером: format(r) = redact(inner.format(r), limit=None) - покрывает msg, args, exc_text,
    stack_info после форматирования(uvicorn AccessFormatter продолжает получать свой кортеж args)."""

    def __init__(self, inner: logging.Formatter | None = None):
        super().__init__()
        self.inner = inner or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        return redact(self.inner.format(record), limit=None)

    def formatTime(self, record, datefmt=None):
        return self.inner.formatTime(record, datefmt)

    def formatException(self, ei):
        return redact(self.inner.formatException(ei), limit=None)


def _wrap(handler: logging.Handler) -> None:
    if not isinstance(handler.formatter, RedactingFormatter):
        handler.setFormatter(RedactingFormatter(handler.formatter))


def install(level=logging.INFO, *, stream=None, handler: bool = True, force: bool = False) -> None:
    """Идемпотентно(force - для тестов):
    1) фабрика записей(logging.setLogRecordFactory, обёртка над прежней): msg(str) → redact; args - кортеж той же длины,
       str-элементы → redact, int/float/bool/None как есть, прочие → redact(str(a)); mapping-args - так же по значениям;
       exc_info есть → record.exc_text = redact(Formatter().formatException(exc_info), limit=None), exc_info сохраняется;
       stack_info → redact;
    2) handler=True и у root нет обработчиков → StreamHandler(stream or sys.stderr) с форматом
       "%(asctime)s %(levelname)s %(name)s: %(message)s", root.setLevel(level);
    3) у каждого обработчика root, "uvicorn", "uvicorn.access", "uvicorn.error" форматтер оборачивается в
       RedactingFormatter(повторно не оборачивать);
    4) httpx, httpcore, httpx2, httpcore2, anthropic, telethon → WARNING. Логгер "saq" не трогается(без своих обработчиков, propagate=True)."""
    global _installed
    if _installed and not force:
        return
    current = logging.getLogRecordFactory()
    if not getattr(current, "_saq_redacting", False):
        logging.setLogRecordFactory(_factory(current))
    root = logging.getLogger()
    if handler and not root.handlers:
        h = logging.StreamHandler(stream or sys.stderr)
        h.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(h)
        root.setLevel(level)
    for name in WRAPPED:
        for h in logging.getLogger(name or None).handlers:
            _wrap(h)
    for name in NOISY:
        lg = logging.getLogger(name)
        if lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)
    _installed = True
