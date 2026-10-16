"""Каркас подключений: типизированные ошибки, данные публикации от сборщика, результат проверки, базовый класс.

Модуль-лист: импортирует только settings, logs, media, models (as_utc) и risk_model (ALMATY — местное время в
текстах). RunContext (context.py), приём (ingest.py),
задания и сервис подключений сюда не импортируются — модуль подключения, который не загрузился, становится
BrokenConnector в реестре, а не циклическим импортом всего приложения.

Ошибки подключения определяют статус источника (integration-spec §9.4, §10.4): сбой никогда не выглядит как «нет новых».
Обход защиты сайтов не выполняется: проверка доступа/CAPTCHA — AccessBlocked, сбор останавливается до ручного действия.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal, Optional

from .. import media
from .. import settings as _settings
from ..logs import redact
from ..models import as_utc
from ..risk_model import ALMATY
from ..settings import Settings, get_settings

if TYPE_CHECKING:
    from ..models import Collector
    from .context import RunContext

# ---------- ошибки (integration-spec §10.1) ----------


class ConnectorError(Exception):
    """Ошибка источника: detail — по-русски, без секретов (маскируется); code — код ошибки запуска."""

    code: ClassVar[str] = "source_error"
    retryable: bool = False

    def __init__(self, detail: str, *, retry_after: Optional[float] = None):
        self.detail = redact(str(detail)) or ""
        self.retry_after = retry_after
        super().__init__(self.detail)


class NotConfigured(ConnectorError):
    code = "not_configured"


class AuthExpired(ConnectorError):
    """Токен или сессия отозваны/истекли — нужна повторная авторизация."""
    code = "auth_expired"


class AccessDenied(ConnectorError):
    """Нет разрешения (права токена, роль в кабинете, неверный сервисный токен)."""
    code = "access_denied"


class AccessBlocked(ConnectorError):
    """Сайт отвечает проверкой доступа (WAF, CAPTCHA, JS-challenge): сбор остановлен до ручного действия, обход не
    выполняется."""
    code = "access_check"


NeedsManualAction = AccessBlocked


class RateLimited(ConnectorError):
    """Ограничение частоты: через сколько секунд повторить — RateLimited(120), RateLimited(120, "текст") или
    RateLimited("текст", retry_after=120); не указано — через час."""
    code = "rate_limited"

    def __init__(self, seconds: Any = None, detail: Optional[str] = None, *, retry_after: Optional[float] = None):
        if isinstance(seconds, str):
            seconds, detail = None, seconds
        secs = retry_after if retry_after is not None else seconds
        secs = 3600.0 if secs is None else max(float(secs), 0.0)
        super().__init__(detail or f"Ограничение частоты запросов источника — повтор через {secs:.0f} с",
                         retry_after=secs)


class BadConfig(ConnectorError):
    """Настройки подключения не работают (пустой список, ни один аккаунт/канал не доступен): сбор остановлен
    до правки."""
    code = "bad_config"


class SourceError(ConnectorError):
    """Ошибка источника; retryable — повторить задание с паузой (сеть, 5xx), иначе — сразу ошибка запуска."""

    def __init__(self, detail: str, *, retryable: bool = True, retry_after: Optional[float] = None):
        super().__init__(detail, retry_after=retry_after)
        self.retryable = retryable


class Deferred(Exception):
    """Не ошибка: продолжить задание через seconds секунд (ожидание внешнего запуска Apify и т. п.); payload — что
    сохранить в jobs.payload для следующей фазы. Попытка задания при этом не расходуется."""

    def __init__(self, seconds: float, payload: Optional[dict] = None):
        super().__init__(f"повтор через {seconds:g} с")
        self.seconds = float(seconds)
        self.payload = dict(payload or {})


# ---------- данные от сборщика (integration-spec §7.2) ----------

MediaKind = Literal["image", "video"]
MEDIA_STATUSES = ("pending", "downloaded", "failed", "unavailable", "skipped")


@dataclass
class MediaIn:
    """Файл публикации от сборщика. key — стабильный ключ источника (входит в хеш содержимого); url — публичная ссылка
    (приём через API: файл загрузит задание анализа); fetch — ленивая загрузка, её вызывает RunContext.ingest только
    когда файл ещё нужен; stored — уже сохранённые байты."""
    key: str
    kind: MediaKind = "image"
    role: Optional[str] = None  # cover | slide | attachment
    url: Optional[str] = None
    fetch: Optional[Callable[[], "MediaIn"]] = None
    stored: Optional[media.Stored] = None
    status: str = "pending"  # pending | downloaded | failed | unavailable | skipped
    error: Optional[str] = None
    origin_url: Optional[str] = None  # маскируется при сохранении
    origin_ref: Optional[str] = None
    meta: dict = field(default_factory=dict)


@dataclass
class Publication:
    """Публикация от сборщика в общем виде. extra — белый список полей, входящих в хеш содержимого (заголовок, цена,
    адрес Krisha…); raw — снимок ответа источника (очищается при приёме, в хеш не входит); namespace — пространство
    дедупликации (по умолчанию площадка; заявки бота — «tgbot»)."""
    platform: str  # instagram | telegram | tiktok | krisha | olx | facebook | other
    external_id: str
    account: str
    url: str
    text: str
    collected_at: datetime  # время сервера (UTC)
    published_at: Optional[datetime] = None
    media: list[MediaIn] = field(default_factory=list)
    price: Optional[str] = None
    reach: Optional[int] = None
    extra: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    namespace: Optional[str] = None


# ---------- подключение (integration-spec §10.2) ----------

Kind = Literal["collector", "intake", "geo", "oauth", "analyzer"]
ConfigField = Literal["accounts", "channels", "profiles", "searchUrls", "cardUrls"]
CONFIG_MAX_ITEMS = 50
CONFIG_MAX_LEN = 255

# Подписи статусов подключения (integration-spec §10.4); Connector.labels переопределяет отдельные
STATUS_LABELS = {"not_configured": "не настроен", "unchecked": "не проверен", "checking": "проверяется",
                 "queued": "в очереди", "running": "идёт сбор", "ready": "готов к сбору", "working": "работает",
                 "partial": "работает частично", "waiting": "ожидает заявок", "off": "не используется",
                 "error": "ошибка", "expired": "истёк доступ", "denied": "нет доступа",
                 "access_check": "нужно ручное действие", "rate_limited": "пауза: лимит запросов"}
# Подписи статусов запуска (integration-spec §10.5)
RUN_STATUS_LABELS = {"queued": "в очереди", "running": "выполняется", "succeeded": "успешно", "partial": "частично",
                     "failed": "ошибка", "access_check": "проверка доступа на сайте",
                     "rate_limited": "пауза: лимит запросов", "not_configured": "не настроен", "cancelled": "отменён"}
HEALTH_STATUSES = frozenset({"ready", "working", "partial", "rate_limited", "error"})
_PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")


def local_time(dt: Optional[datetime], fmt: str = "%H:%M") -> str:
    """Время для текстов статуса и журнала — местное (Усть-Каменогорск, UTC+5): «14:05» или по fmt."""
    return as_utc(dt).astimezone(ALMATY).strftime(fmt) if dt is not None else ""


def when_text(dt: Optional[datetime]) -> str:
    """«16.10.2026 14:05» по местному времени (Усть-Каменогорск)."""
    return local_time(dt, "%d.%m.%Y %H:%M")


@dataclass
class CheckResult:
    """Итог «Проверить подключение»: ok=False — ошибка проверки (не пустой результат); facts — несекретные сведения."""
    ok: bool
    detail: str
    facts: dict = field(default_factory=dict)


class ConfigError(ValueError):
    """Неверная настройка подключения: field — поле config (camelCase), value — что пришло; маршрут отвечает 422
    с loc ["body", "config", field]."""

    def __init__(self, field_name: str, msg: str, value: Any = None):
        super().__init__(msg)
        self.field, self.msg, self.value = field_name, msg, value


class Connector:
    """Подключение. Атрибуты класса описывают его для страницы «Подключения»; check/collect выполняет воркер."""

    code: ClassVar[str]
    kind: ClassVar[Kind]
    platform: ClassVar[Optional[str]] = None
    name: ClassVar[str]
    via: ClassVar[str]
    group: ClassVar[str]  # collectors | intake | geo | analyzer | limited
    required_env: ClassVar[tuple[str, ...]] = ()
    config_fields: ClassVar[tuple[ConfigField, ...]] = ()
    options: ClassVar[tuple[str, ...]] = ()  # resultsPerPage, downloadVideos
    default_interval_min: ClassVar[Optional[int]] = None
    interval_bounds: ClassVar[tuple[int, int]] = (15, 10080)
    limitation: ClassVar[Optional[str]] = None
    capabilities: ClassVar[dict] = {"test": True, "collect": True, "toggle": True, "configure": True, "connect": False}
    secret_refs: ClassVar[tuple[str, ...]] = ()
    max_attempts: ClassVar[int] = 5
    paid: ClassVar[bool] = False  # платный сбор (TikTok): после ошибки остановка до ручной проверки
    labels: ClassVar[dict[str, str]] = {}
    contract_verified: ClassVar[Optional[bool]] = None
    health_check_min: ClassVar[Optional[int]] = None  # ежедневная проверка, пока сбор включён

    # --- настройка ---

    def missing(self, s: Settings, row: Optional["Collector"] = None) -> list[str]:
        """Имена переменных окружения (и других условий), которых не хватает; пусто — подключение настроено."""
        return s.missing(*self.required_env)

    def missing_detail(self, s: Settings, row: Optional["Collector"] = None) -> str:
        names = self.missing(s, row)
        return f"Нет на сервере: {', '.join(names)}" if names else ""

    def normalize_item(self, field_name: str, value: str) -> str:
        """Один элемент списка настроек (аккаунт, канал, URL). Подключения переопределяют: проверка формата —
        ConfigError(field, «понятное сообщение»)."""
        return value

    def validate_config(self, cfg: dict) -> dict:
        """Проверенные настройки: списки — без пустых и повторов, не больше 50 элементов по 255 символов; поля, которых
        у подключения нет, — ConfigError. Возвращает только переданные поля."""
        out: dict = {}
        known = set(self.config_fields) | set(self.options)
        for key, value in (cfg or {}).items():
            if value is None:
                continue
            if key not in known:
                raise ConfigError(key, "Этот параметр не используется подключением", value)
            if key in self.options:
                out[key] = value
                continue
            if not isinstance(value, (list, tuple)):
                raise ConfigError(key, "Нужен список строк", value)
            items: list[str] = []
            for raw in value:
                if not isinstance(raw, str):
                    raise ConfigError(key, "Элементы списка — строки", value)
                v = raw.strip()
                if not v:
                    continue
                if len(v) > CONFIG_MAX_LEN:
                    raise ConfigError(key, f"Элемент длиннее {CONFIG_MAX_LEN} символов: {v[:40]}…", value)
                v = self.normalize_item(key, v)
                if v not in items:
                    items.append(v)
            if len(items) > CONFIG_MAX_ITEMS:
                raise ConfigError(key, f"Не больше {CONFIG_MAX_ITEMS} элементов", value)
            out[key] = items
        return out

    def status_label(self, status: str) -> str:
        return self.labels.get(status) or STATUS_LABELS.get(status, status)

    def limitation_text(self, s: Settings) -> Optional[str]:
        """Ограничение для страницы «Подключения»: «{TG_EDIT_WINDOW}» и т. п. заменяются значением настройки."""
        if not self.limitation:
            return None

        def value(m: re.Match) -> str:
            var = _settings._BY_ENV.get(m.group(1))
            v = getattr(s, var.field, None) if var is not None else None
            return m.group(0) if v is None or var.kind in ("secret", "token") else str(v)
        return _PLACEHOLDER.sub(value, self.limitation)

    # --- состояние ---

    def computed_status(self, db, row: Optional["Collector"], now: datetime) -> Optional[tuple[str, str]]:
        """Живой статус без запусков (приём, анализатор, OAuth): (status, detail) или None — показывать хранимый."""
        return None

    def can_enable(self, row: Optional["Collector"]) -> Optional[str]:
        """None — сбор можно включить; иначе причина (409)."""
        return None

    def health_due(self, db, row: Optional["Collector"], now: datetime) -> bool:
        """Пора ли плановая проверка подключения: настроено, сбор включён, не остановлено после ошибки, статус
        рабочий, последняя проверка старше health_check_min минут (или её не было)."""
        if not self.health_check_min or row is None or not row.enabled or (row.facts or {}).get("stopped"):
            return False
        if self.missing(get_settings(), row) or row.status not in HEALTH_STATUSES:
            return False
        checked = as_utc(row.checked_at)
        return checked is None or checked <= as_utc(now) - timedelta(minutes=self.health_check_min)

    def report(self, s: Settings) -> Optional[dict]:
        """Сведения, которые видит только воркер (например, есть ли файл сессии Telegram): пишутся в state.report."""
        return None

    # --- работа (воркер) ---

    def check(self, ctx: "RunContext") -> CheckResult:
        raise SourceError("Проверка для этого подключения не предусмотрена", retryable=False)

    def collect(self, ctx: "RunContext") -> None:
        raise SourceError("Сбор публикаций для этого подключения не предусмотрен", retryable=False)
