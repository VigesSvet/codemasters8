"""Настройки подключений и инфраструктуры: реальное окружение, затем .env в корне репозитория (без изменения os.environ).

Порядок для каждой переменной: непустое значение окружения → непустое значение из .env → значение по умолчанию; пустая
строка — то же, что не задано. Путь к .env — SAQ_ENV_FILE (пустая строка — файл не читается), иначе <репозиторий>/.env
(в Docker — /.env, обычно его нет). Значения из файла читаются буквально (interpolate=False): «$» экранировать не нужно.

Секреты — объекты Secret: str/repr дают «***», значение — только через .get(). Предупреждения о неверных значениях называют
переменную, но никогда не её значение. Настройки кэшируются на процесс: после правки .env нужен перезапуск API и воркера.
"""
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

log = logging.getLogger("saq.settings")

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
DEFAULT_MEDIA_ROOT = BACKEND / "data" / "media"
DEFAULT_TG_SESSION_PATH = BACKEND / "data" / "private" / "telegram.session"

# ASCII: httpx кодирует заголовки в ASCII (иначе UnicodeEncodeError при каждом запросе)
DEFAULT_UA = "SAQ-Qurylys-monitor/0.3 (+research; construction-ads monitoring)"
# Предел распознавания скриншота (= ai.ocr.MAX_BYTES, сверяется тестом): settings — листовой модуль и ai не импортирует
OCR_MAX_BYTES = 10 * 1024 * 1024
TOKEN_MIN_LEN = 24
TOKEN_MIN_DISTINCT = 12
TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
# Уровни ANTHROPIC_EFFORT (output_config.effort): думать меньше/больше — расход и время ответа
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
FALSE_WORDS = frozenset({"0", "false", "no", "off"})


class Secret:
    """Значение секрета: в логах, repr и f-строках — «***»; само значение — только get()."""

    __slots__ = ("_v",)

    def __init__(self, value: Optional[str] = ""):
        self._v = value or ""

    def get(self) -> str:
        return self._v

    def __bool__(self) -> bool:
        return bool(self._v)

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"

    def __format__(self, spec: str) -> str:
        return format("***", spec)


def _no_secret() -> Secret:
    return Secret("")


@dataclass(frozen=True, kw_only=True)
class Settings:
    # общее
    env_file: Optional[Path] = None
    media_root: Path = DEFAULT_MEDIA_ROOT
    media_max_image_mb: int = 10
    media_max_video_mb: int = 200
    media_min_free_mb: int = 1024
    admin_token: Secret = field(default_factory=_no_secret)
    ingest_token: Secret = field(default_factory=_no_secret)
    service_token: Secret = field(default_factory=_no_secret)
    allow_schema_reset: bool = False
    collected_autolink: bool = False
    # «Проверка по рекламе» (app.screening): окно свежести рекламы, дней — по дате публикации
    screening_days: int = 120
    worker_poll_s: float = 5.0
    worker_id: Optional[str] = None
    job_lease_s: int = 1800
    job_max_s: int = 3600
    user_agent: str = DEFAULT_UA
    # Meta / Instagram
    meta_app_id: str = ""
    meta_app_secret: Secret = field(default_factory=_no_secret)
    meta_user_access_token: Secret = field(default_factory=_no_secret)
    meta_graph_version: str = "v26.0"
    meta_business_id: str = ""
    meta_page_id: str = ""
    meta_ig_user_id: str = ""
    ig_page_size: int = 25
    ig_first_load_max: int = 50
    # Telegram
    tg_api_id: Optional[int] = None
    tg_api_hash: Secret = field(default_factory=_no_secret)
    tg_phone: Secret = field(default_factory=_no_secret)
    tg_session_path: Path = DEFAULT_TG_SESSION_PATH
    tg_history_limit: int = 50
    tg_edit_window: int = 30
    tg_max_video_mb: int = 50
    tg_download_timeout_s: int = 300
    # Apify / TikTok
    apify_token: Secret = field(default_factory=_no_secret)
    apify_actor_id: str = "clockworks~tiktok-scraper"
    apify_max_total_charge_usd: float = 0.5
    apify_daily_budget_usd: float = 1.0
    apify_max_items_per_profile: int = 5
    apify_run_timeout_s: int = 300
    # 2ГИС
    dgis_api_key: Secret = field(default_factory=_no_secret)
    dgis_quota_limit: int = 900
    dgis_quota_period_start: Optional[date] = None
    dgis_daily_limit: int = 50
    dgis_cache_ttl_days: int = 7
    # Krisha
    krisha_min_delay_s: float = 5.0
    krisha_max_pages: int = 2
    krisha_max_cards: int = 10
    krisha_playwright: bool = False
    # OLX
    olx_client_id: str = ""
    olx_client_secret: Secret = field(default_factory=_no_secret)
    olx_redirect_uri: str = ""
    olx_scopes: str = "read v2"
    # Anthropic (Claude) — этап анализа ИИ публикаций (app.llm)
    anthropic_api_key: Secret = field(default_factory=_no_secret)
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_effort: str = "medium"
    anthropic_max_tokens: int = 16000
    anthropic_daily_budget_usd: float = 5.0
    anthropic_timeout_s: float = 120.0
    anthropic_workspace_id: str = ""
    # дозапрос старых публикаций без ответа Claude (такт лидера воркера): сколько за проход и как часто
    anthropic_backfill_batch: int = 5
    anthropic_backfill_interval_min: int = 10
    # значения, отброшенные правилами токенов (слабые/совпадающие): для подключения не используются, но в логах маскируются
    ignored_secrets: tuple = field(default=(), repr=False)

    def secrets(self) -> list[str]:
        """Все непустые значения секретов (маскирование в логах и текстах ошибок)."""
        out = [getattr(self, f.name).get() for f in fields(self) if isinstance(getattr(self, f.name), Secret)]
        out += [s.get() for s in self.ignored_secrets if isinstance(s, Secret)]
        return [v for v in out if v]

    def _value(self, name: str) -> Any:
        var = _BY_ENV.get(name)
        if var is None:  # настройка подключения вне Settings (env_str)
            return env_str(name) or None
        v = getattr(self, var.field)
        if isinstance(v, Secret):
            return v.get() or None
        if v is None or v == "":
            return None
        return v

    def missing(self, *names: str) -> list[str]:
        """Имена переменных окружения, которые не заданы (только имена, никогда значения)."""
        return [n for n in names if self._value(n) is None]

    def key_fp(self, name: str) -> Optional[str]:
        """Отпечаток заданного значения (sha256[:12]) — заметить смену ключа, не раскрывая его; None — не задано."""
        v = self._value(name)
        return hashlib.sha256(str(v).encode()).hexdigest()[:12] if v is not None else None

    @property
    def public_host(self) -> Optional[str]:
        """Хост публичного адреса OLX Callback (ngrok): через него доступен только путь обратного вызова. Без
        регистра и конечной точки («x.ngrok-free.dev.» — тот же хост)."""
        host = urlsplit(self.olx_redirect_uri).hostname if self.olx_redirect_uri else None
        host = host.lower().rstrip(".") if host else None
        return host or None

    @property
    def public_path(self) -> Optional[str]:
        return (urlsplit(self.olx_redirect_uri).path or "/") if self.public_host else None


@dataclass(frozen=True)
class _Var:
    env: str
    field: str
    kind: str  # str | secret | token | int | float | bool | path | date | optint | optstr | ua | model | effort
    lo: Optional[float] = None  # нижняя граница чисел: меньше — значение по умолчанию с предупреждением
    hi: Optional[float] = None  # верхняя граница чисел: больше — значение по умолчанию с предупреждением


VARS: tuple[_Var, ...] = (
    _Var("MEDIA_ROOT", "media_root", "path"),
    _Var("MEDIA_MAX_IMAGE_MB", "media_max_image_mb", "int", 1),
    _Var("MEDIA_MAX_VIDEO_MB", "media_max_video_mb", "int", 1),
    _Var("MEDIA_MIN_FREE_MB", "media_min_free_mb", "int", 0),
    _Var("SAQ_ADMIN_TOKEN", "admin_token", "token"),
    _Var("INTEGRATION_INGEST_TOKEN", "ingest_token", "token"),
    _Var("SERVICE_API_TOKEN", "service_token", "token"),
    _Var("SAQ_ALLOW_SCHEMA_RESET", "allow_schema_reset", "bool"),
    _Var("SAQ_COLLECTED_AUTOLINK", "collected_autolink", "bool"),
    _Var("SAQ_SCREENING_DAYS", "screening_days", "int", 1, 3650),
    _Var("SAQ_WORKER_POLL_S", "worker_poll_s", "float", 0.1),
    _Var("SAQ_WORKER_ID", "worker_id", "optstr"),
    _Var("SAQ_JOB_LEASE_S", "job_lease_s", "int", 60),
    _Var("SAQ_JOB_MAX_S", "job_max_s", "int", 60),
    _Var("SAQ_USER_AGENT", "user_agent", "ua"),
    _Var("META_APP_ID", "meta_app_id", "str"),
    _Var("META_APP_SECRET", "meta_app_secret", "secret"),
    _Var("META_USER_ACCESS_TOKEN", "meta_user_access_token", "secret"),
    _Var("META_GRAPH_VERSION", "meta_graph_version", "str"),
    _Var("META_BUSINESS_ID", "meta_business_id", "str"),
    _Var("META_PAGE_ID", "meta_page_id", "str"),
    _Var("META_IG_USER_ID", "meta_ig_user_id", "str"),
    _Var("IG_PAGE_SIZE", "ig_page_size", "int", 1),
    _Var("IG_FIRST_LOAD_MAX", "ig_first_load_max", "int", 1),
    _Var("TG_API_ID", "tg_api_id", "optint", 1),
    _Var("TG_API_HASH", "tg_api_hash", "secret"),
    _Var("TG_PHONE", "tg_phone", "secret"),
    _Var("TG_SESSION_PATH", "tg_session_path", "path"),
    _Var("TG_HISTORY_LIMIT", "tg_history_limit", "int", 1),
    _Var("TG_EDIT_WINDOW", "tg_edit_window", "int", 0),
    _Var("TG_MAX_VIDEO_MB", "tg_max_video_mb", "int", 0),
    _Var("TG_DOWNLOAD_TIMEOUT_S", "tg_download_timeout_s", "int", 1),
    _Var("APIFY_TOKEN", "apify_token", "secret"),
    _Var("APIFY_ACTOR_ID", "apify_actor_id", "str"),
    _Var("APIFY_MAX_TOTAL_CHARGE_USD", "apify_max_total_charge_usd", "float", 0),
    _Var("APIFY_DAILY_BUDGET_USD", "apify_daily_budget_usd", "float", 0),
    _Var("APIFY_MAX_ITEMS_PER_PROFILE", "apify_max_items_per_profile", "int", 1),
    _Var("APIFY_RUN_TIMEOUT_S", "apify_run_timeout_s", "int", 1),
    _Var("DGIS_API_KEY", "dgis_api_key", "secret"),
    _Var("DGIS_QUOTA_LIMIT", "dgis_quota_limit", "int", 0),
    _Var("DGIS_QUOTA_PERIOD_START", "dgis_quota_period_start", "date"),
    _Var("DGIS_DAILY_LIMIT", "dgis_daily_limit", "int", 0),
    _Var("DGIS_CACHE_TTL_DAYS", "dgis_cache_ttl_days", "int", 1),
    _Var("KRISHA_MIN_DELAY_S", "krisha_min_delay_s", "float", 0),
    _Var("KRISHA_MAX_PAGES", "krisha_max_pages", "int", 1),
    _Var("KRISHA_MAX_CARDS", "krisha_max_cards", "int", 0),
    _Var("KRISHA_PLAYWRIGHT", "krisha_playwright", "bool"),
    _Var("OLX_CLIENT_ID", "olx_client_id", "str"),
    _Var("OLX_CLIENT_SECRET", "olx_client_secret", "secret"),
    _Var("OLX_REDIRECT_URI", "olx_redirect_uri", "str"),
    _Var("OLX_SCOPES", "olx_scopes", "str"),
    _Var("ANTHROPIC_API_KEY", "anthropic_api_key", "secret"),
    _Var("ANTHROPIC_MODEL", "anthropic_model", "model"),
    _Var("ANTHROPIC_EFFORT", "anthropic_effort", "effort"),
    _Var("ANTHROPIC_MAX_TOKENS", "anthropic_max_tokens", "int", 1024, 64000),
    # счётчик расхода — целые микродоллары в quota_counters (Integer): верхняя граница держит его в 32 битах
    _Var("ANTHROPIC_DAILY_BUDGET_USD", "anthropic_daily_budget_usd", "float", 0, 1000),
    _Var("ANTHROPIC_TIMEOUT_S", "anthropic_timeout_s", "float", 10, 600),
    # рабочее пространство для ключа организации, не привязанного к нему (заголовок anthropic-workspace-id)
    _Var("ANTHROPIC_WORKSPACE_ID", "anthropic_workspace_id", "str"),
    # дозапрос старых публикаций: 0 — выключен
    _Var("ANTHROPIC_BACKFILL_BATCH", "anthropic_backfill_batch", "int", 0, 100),
    _Var("ANTHROPIC_BACKFILL_INTERVAL_MIN", "anthropic_backfill_interval_min", "int", 1, 1440),
)
_BY_ENV = {v.env: v for v in VARS}
# Переменные прежних версий: приложение их не читает, заданное значение — предупреждение при разборе настроек
RETIRED = {"SAQ_DEMO_DATA": "демо-набора в приложении больше нет — пустая БД получает стартовый набор shared/snapshot "
                            "(README, «Развёртывание со стартовым набором данных»); строку можно удалить из .env"}
# Все имена, которые читает приложение (SAQ_ENV_FILE — только из реального окружения)
ENV_NAMES: tuple[str, ...] = tuple(v.env for v in VARS) + ("SAQ_ENV_FILE",)
TOKENS = ("SAQ_ADMIN_TOKEN", "INTEGRATION_INGEST_TOKEN", "SERVICE_API_TOKEN")


# ---------- .env ----------

_FILE_CACHE: dict[str, tuple[tuple, dict]] = {}
_FILE_LOCK = threading.Lock()


def env_file_path(environ: Mapping[str, str] = os.environ) -> Optional[Path]:
    """SAQ_ENV_FILE, если задан (пустая строка — файл отключён), иначе .env в корне репозитория."""
    if "SAQ_ENV_FILE" in environ:
        raw = (environ.get("SAQ_ENV_FILE") or "").strip()
        return Path(raw).expanduser() if raw else None
    return DEFAULT_ENV_FILE


def _file_values(path: Optional[Path], warns: Optional[list] = None) -> dict:
    """Значения из .env буквально (interpolate=False); нет файла или он не читается — пусто (с предупреждением).
    warns — список для предупреждений: при разборе настроек в лог писать нельзя (маскирование в логах само читает
    настройки — была бы рекурсия), предупреждения выводятся после заполнения кэша."""
    say = warns.append if warns is not None else log.warning
    if path is None:
        return {}
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    except OSError as e:
        say(f"Файл настроек {path} недоступен ({type(e).__name__}) — используется только окружение")
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    key = str(path)
    with _FILE_LOCK:
        hit = _FILE_CACHE.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(path, interpolate=False).items() if k and v is not None}
    except Exception as e:  # битый файл/кодировка — не повод не стартовать
        say(f"Файл настроек {path} не прочитан ({type(e).__name__}) — используется только окружение")
        values = {}
    with _FILE_LOCK:
        _FILE_CACHE[key] = (stamp, values)
    return values


def _raw(name: str, environ: Mapping[str, str], file: Mapping[str, str]) -> Optional[str]:
    """Непустое значение окружения, иначе непустое из .env, иначе None."""
    for src in (environ, file):
        v = src.get(name)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


# ---------- разбор значений ----------

def _num(name: str, raw: Optional[str], default, cast, lo, warns: list, hi=None):
    if raw is None:
        return default
    fallback = "значение не используется" if default is None else f"используется значение по умолчанию ({default})"
    try:
        v = cast(raw)
    except ValueError:
        warns.append(f"{name}: ожидается {'целое число' if cast is int else 'число'} — {fallback}")
        return default
    if cast is float and v != v:  # NaN
        warns.append(f"{name}: ожидается число — {fallback}")
        return default
    if lo is not None and v < lo:
        warns.append(f"{name}: значение меньше {lo:g} — {fallback}")
        return default
    if hi is not None and v > hi:
        warns.append(f"{name}: значение больше {hi:g} — {fallback}")
        return default
    return v


def _bool(name: str, raw: Optional[str], default: bool, warns: list) -> bool:
    if raw is None:
        return default
    word = raw.lower()
    if word in TRUE_WORDS:
        return True
    if word not in FALSE_WORDS:
        warns.append(f"{name}: ожидается 1/0, true/false, yes/no или on/off — считается выключенным")
    return False


def _weak(name: str, value: str) -> Optional[str]:
    if len(value) < TOKEN_MIN_LEN:
        return f"{name} короче {TOKEN_MIN_LEN} символов — считается не заданным"
    if len(set(value)) < TOKEN_MIN_DISTINCT:
        return f"{name} слишком простой (меньше {TOKEN_MIN_DISTINCT} разных символов) — считается не заданным"
    return None


def _parse(environ: Mapping[str, str]) -> tuple[Settings, list[str]]:
    warns: list[str] = []
    path = env_file_path(environ)
    file = _file_values(path, warns)
    defaults = {f.name: (f.default_factory() if callable(f.default_factory) else f.default) for f in fields(Settings)}
    values: dict[str, Any] = {"env_file": path if path is not None and path.is_file() else None}
    for name, why in RETIRED.items():
        if _raw(name, environ, file) is not None:
            warns.append(f"{name} больше не используется: {why}")
    ignored: list[Secret] = []
    tokens: dict[str, str] = {}
    for var in VARS:
        raw = _raw(var.env, environ, file)
        default = defaults[var.field]
        if var.kind in ("str", "optstr"):
            v = raw if raw is not None else default
        elif var.kind == "secret":
            v = Secret(raw or "")
        elif var.kind == "token":
            v = Secret("")
            if raw:
                problem = _weak(var.env, raw)
                if problem:
                    warns.append(problem)
                    ignored.append(Secret(raw))
                else:
                    v = Secret(raw)
                    tokens[var.env] = raw
        elif var.kind == "int":
            v = _num(var.env, raw, default, int, var.lo, warns, var.hi)
        elif var.kind == "optint":
            v = _num(var.env, raw, None, int, var.lo, warns, var.hi)
        elif var.kind == "float":
            v = _num(var.env, raw, default, float, var.lo, warns, var.hi)
        elif var.kind == "model":
            v = default
            if raw is not None:
                if _MODEL_ID.fullmatch(raw):
                    v = raw
                else:
                    warns.append(f"{var.env}: ожидается идентификатор модели (например, {default}) — используется "
                                 "значение по умолчанию")
        elif var.kind == "effort":
            v = default
            if raw is not None:
                if raw.lower() in EFFORTS:
                    v = raw.lower()
                else:
                    warns.append(f"{var.env}: ожидается одно из {', '.join(EFFORTS)} — используется значение по "
                                 f"умолчанию ({default})")
        elif var.kind == "bool":
            v = _bool(var.env, raw, default, warns)
        elif var.kind == "path":
            v = Path(raw).expanduser() if raw else default
        elif var.kind == "date":
            v = None
            if raw:
                try:
                    v = date.fromisoformat(raw)
                except ValueError:
                    warns.append(f"{var.env}: ожидается дата ГГГГ-ММ-ДД — значение не используется")
        elif var.kind == "ua":
            v = default
            if raw is not None:
                if not raw.isascii():
                    warns.append("SAQ_USER_AGENT содержит не-ASCII символы — используется значение по умолчанию")
                elif not raw.isprintable():
                    warns.append("SAQ_USER_AGENT содержит управляющие символы — используется значение по умолчанию")
                else:
                    v = raw
        else:  # pragma: no cover — опечатка в VARS
            raise ValueError(var.kind)
        values[var.field] = v
    # Токен администратора не может совпадать с токеном клиента приёма или бота: иначе клиент стал бы администратором
    admin = tokens.get("SAQ_ADMIN_TOKEN")
    for other in ("SERVICE_API_TOKEN", "INTEGRATION_INGEST_TOKEN"):
        if admin and tokens.get(other) == admin:
            warns.append(f"SAQ_ADMIN_TOKEN совпадает с {other} — считается не заданным")
            values["admin_token"] = Secret("")
            break
    if values["media_max_image_mb"] * 1024 * 1024 > OCR_MAX_BYTES:
        limit = OCR_MAX_BYTES // (1024 * 1024)
        warns.append(f"MEDIA_MAX_IMAGE_MB больше {limit} МБ (предел распознавания скриншотов) — используется {limit}")
        values["media_max_image_mb"] = limit
    return Settings(**values, ignored_secrets=tuple(ignored)), warns


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    """Свежие настройки из окружения и .env (без кэша); предупреждения — в лог."""
    s, warns = _parse(environ)
    for w in warns:
        log.warning(w)
    return s


_CACHED: Optional[Settings] = None
_LOCK = threading.RLock()


def get_settings() -> Settings:
    """Настройки процесса (кэш). Предупреждения пишутся после заполнения кэша: маскирование в логах само читает
    настройки, и повторного разбора (и рекурсии) не возникает."""
    s = _CACHED
    if s is not None:
        return s
    with _LOCK:
        if _CACHED is None:
            _store(*_parse(os.environ))
        return _CACHED


def _store(s: Settings, warns: list[str]) -> None:
    global _CACHED
    _CACHED = s
    for w in warns:
        log.warning(w)


def reload_settings() -> Settings:
    """Перечитать окружение и .env (тесты; после reload — новые значения во всех get_settings())."""
    with _LOCK:
        with _FILE_LOCK:
            _FILE_CACHE.clear()
        _store(*_parse(os.environ))
        return _CACHED


# ---------- параметры подключений вне Settings ----------

def env_str(name: str, default: str = "") -> str:
    """Непустое значение окружения → из .env → default (параметры отдельных подключений, не входящие в Settings)."""
    raw = _raw(name, os.environ, _file_values(env_file_path(os.environ)))
    return raw if raw is not None else default


def env_int(name: str, default: int) -> int:
    warns: list[str] = []
    v = _num(name, env_str(name) or None, default, int, None, warns)
    for w in warns:
        log.warning(w)
    return v


def env_float(name: str, default: float) -> float:
    warns: list[str] = []
    v = _num(name, env_str(name) or None, default, float, None, warns)
    for w in warns:
        log.warning(w)
    return v


def env_bool(name: str, default: bool = False) -> bool:
    warns: list[str] = []
    v = _bool(name, env_str(name) or None, default, warns)
    for w in warns:
        log.warning(w)
    return v
