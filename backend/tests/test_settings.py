"""Настройки(.env и окружение, секреты, правила токенов) и маскирование секретов в логах."""
import io
import logging
import os
import re
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import logs, settings
from app.settings import DEFAULT_UA, Secret, Settings, load_settings

REPO = Path(__file__).resolve().parents[2]
GOOD = "t-ingest-0123456789abcdef0123"
GOOD2 = "t-service-0123456789abcdef012"
FAKEKEY = "FAKEKEY12345678"


def _env_file(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "test.env"
    p.write_text(text, encoding="utf-8")
    return p


def _warnings(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "saq.settings")


# ---------- источники значений ----------

def test_env_wins_over_file_and_empty_falls_back(tmp_path):
    p = _env_file(tmp_path, "META_GRAPH_VERSION=v1.0\nANTHROPIC_MODEL=from-file\n")
    s = load_settings({"SAQ_ENV_FILE": str(p), "META_GRAPH_VERSION": "v9.9", "ANTHROPIC_MODEL": ""})
    assert s.meta_graph_version == "v9.9"  # окружение важнее файла
    assert s.anthropic_model == "from-file"  # пустое в окружении → значение из файла
    assert s.env_file == p
    s = load_settings({"SAQ_ENV_FILE": str(_env_file(tmp_path, "META_GRAPH_VERSION=\n"))})
    assert s.meta_graph_version == "v26.0"  # пусто и там, и там → по умолчанию


def test_env_file_disabled_and_custom_path(tmp_path, monkeypatch):
    p = _env_file(tmp_path, "ANTHROPIC_MODEL=custom\n")
    assert load_settings({"SAQ_ENV_FILE": str(p)}).anthropic_model == "custom"
    assert load_settings({"SAQ_ENV_FILE": ""}).anthropic_model == "claude-sonnet-5-5"  # по умолчанию
    assert load_settings({"SAQ_ENV_FILE": ""}).env_file is None
    assert load_settings({"SAQ_ENV_FILE": str(tmp_path / "нет.env")}).env_file is None
    # без SAQ_ENV_FILE - .env в корне репозитория(его содержимое тест не читает: подменён путь по умолчанию)
    monkeypatch.setattr(settings, "DEFAULT_ENV_FILE", p)
    assert settings.env_file_path({}) == p and load_settings({}).anthropic_model == "custom"


def test_env_file_literal_and_environ_untouched(tmp_path, env):
    p = _env_file(tmp_path, "META_BUSINESS_ID=$HOME/x\nSAQ_USER_AGENT='SAQ test ${USER}'\n")
    before = dict(os.environ)
    s = env(SAQ_ENV_FILE=str(p))
    assert s.meta_business_id == "$HOME/x"  # interpolate=False: без подстановки
    assert s.user_agent == "SAQ test ${USER}"
    assert settings.get_settings() is s
    after = dict(os.environ)
    assert after.pop("SAQ_ENV_FILE") == str(p)
    before.pop("SAQ_ENV_FILE", None)
    assert after == before  # значения из файла в os.environ не попадают
    assert "META_BUSINESS_ID" not in os.environ
    assert settings.env_str("META_BUSINESS_ID") == "$HOME/x" and settings.env_str("НЕТ_ТАКОЙ", "d") == "d"


def test_env_helpers(env):
    env(SAQ_TEST_INT="7", SAQ_TEST_FLOAT="1.5", SAQ_TEST_BOOL="Yes", SAQ_TEST_BAD="x")
    assert settings.env_int("SAQ_TEST_INT", 1) == 7 and settings.env_int("SAQ_TEST_BAD", 3) == 3
    assert settings.env_float("SAQ_TEST_FLOAT", 0.0) == 1.5 and settings.env_float("SAQ_TEST_NONE", 2.0) == 2.0
    assert settings.env_bool("SAQ_TEST_BOOL") is True and settings.env_bool("SAQ_TEST_NONE", True) is True


def test_defaults_and_paths():
    s = load_settings({"SAQ_ENV_FILE": ""})
    backend = Path(settings.__file__).resolve().parents[1]
    assert s.media_root == backend / "data" / "media"
    assert s.tg_session_path == backend / "data" / "private" / "telegram.session"
    assert s.apify_actor_id == "clockworks~tiktok-scraper" and s.olx_scopes == "read v2"
    assert s.user_agent == DEFAULT_UA and s.public_host is None and s.public_path is None
    s = load_settings({"SAQ_ENV_FILE": "", "MEDIA_ROOT": "/srv/media", "SAQ_WORKER_POLL_S": "0.5",
                       "KRISHA_PLAYWRIGHT": "on", "DGIS_QUOTA_PERIOD_START": "2026-10-16", "TG_API_ID": "123456"})
    assert s.media_root == Path("/srv/media") and s.worker_poll_s == 0.5 and s.krisha_playwright is True
    assert s.dgis_quota_period_start == date(2026, 10, 16) and s.tg_api_id == 123456


def test_conftest_isolation():
    """conftest: файл .env репозитория не читается, ключи разработчика убраны, файлы - во временном каталоге."""
    s = settings.get_settings()
    assert os.environ["SAQ_ENV_FILE"] == "" and s.env_file is None
    assert s.secrets() == []
    assert str(s.media_root).startswith(tempfile_root()) and str(s.tg_session_path).startswith(tempfile_root())


def tempfile_root() -> str:
    import conftest

    return conftest.TMP


def test_invalid_values_warn_by_name(caplog):
    caplog.set_level(logging.WARNING, logger="saq.settings")
    s = load_settings({"SAQ_ENV_FILE": "", "TG_API_ID": "abc", "DGIS_QUOTA_PERIOD_START": "x", "IG_PAGE_SIZE": "0",
                       "SAQ_ALLOW_SCHEMA_RESET": "maybe", "MEDIA_MAX_IMAGE_MB": "50"})
    assert s.tg_api_id is None and s.dgis_quota_period_start is None and s.ig_page_size == 25
    assert s.allow_schema_reset is False
    assert s.media_max_image_mb == 10  # не больше предела OCR
    text = _warnings(caplog)
    for name in ("TG_API_ID", "DGIS_QUOTA_PERIOD_START", "IG_PAGE_SIZE", "SAQ_ALLOW_SCHEMA_RESET", "MEDIA_MAX_IMAGE_MB"):
        assert name in text
    assert "abc" not in text and "maybe" not in text


def test_ocr_limit_matches_ai():
    from app.ai import ocr

    assert settings.OCR_MAX_BYTES == ocr.MAX_BYTES


def test_settings_dataclass_and_user_agent(caplog):
    s = Settings(media_root=Path("/x"), tg_session_path=Path("/y"))
    with pytest.raises(TypeError):
        Settings(Path("/x"))  # kw_only
    with pytest.raises(FrozenInstanceError):
        s.user_agent = "x"  # type: ignore[misc]
    httpx.Request("GET", "https://x", headers={"User-Agent": s.user_agent})  # ASCII - заголовок собирается
    caplog.set_level(logging.WARNING, logger="saq.settings")
    s = load_settings({"SAQ_ENV_FILE": "", "SAQ_USER_AGENT": "Мониторинг SAQ"})
    assert s.user_agent == DEFAULT_UA
    assert "SAQ_USER_AGENT содержит не-ASCII символы — используется значение по умолчанию" in _warnings(caplog)
    httpx.Request("GET", "https://x", headers={"User-Agent": s.user_agent})
    assert load_settings({"SAQ_ENV_FILE": "", "SAQ_USER_AGENT": "Bot\r\nX: 1"}).user_agent == DEFAULT_UA


# ---------- секреты ----------

def test_secret_never_printed():
    sec = Secret("very-secret-value-123")
    assert str(sec) == "***" and repr(sec) == "Secret('***')" and f"{sec}" == "***" and f"{sec:>5}" == "  ***"
    assert sec.get() == "very-secret-value-123" and bool(sec) and not Secret("")
    s = load_settings({"SAQ_ENV_FILE": "", "APIFY_TOKEN": "apify-secret-value-1", "TG_PHONE": "+77001234567"})
    assert "apify-secret-value-1" not in repr(s) and "+77001234567" not in repr(s)
    assert set(s.secrets()) == {"apify-secret-value-1", "+77001234567"}


def test_missing_and_key_fp():
    s = load_settings({"SAQ_ENV_FILE": "", "APIFY_TOKEN": "apify-secret-value-1", "TG_API_ID": "123"})
    assert s.missing("APIFY_TOKEN", "TG_API_ID", "TG_API_HASH", "OLX_REDIRECT_URI") == ["TG_API_HASH", "OLX_REDIRECT_URI"]
    fp = s.key_fp("APIFY_TOKEN")
    assert fp and len(fp) == 12 and fp not in "apify-secret-value-1" and "apify" not in fp
    assert fp == load_settings({"SAQ_ENV_FILE": "", "APIFY_TOKEN": "apify-secret-value-1"}).key_fp("APIFY_TOKEN")
    assert fp != load_settings({"SAQ_ENV_FILE": "", "APIFY_TOKEN": "apify-secret-value-2"}).key_fp("APIFY_TOKEN")
    assert s.key_fp("TG_API_ID") and s.key_fp("DGIS_API_KEY") is None


def test_token_rules(caplog):
    caplog.set_level(logging.WARNING, logger="saq.settings")
    short, simple = "short-token-1", "abababababababababababababab"
    s = load_settings({"SAQ_ENV_FILE": "", "SAQ_ADMIN_TOKEN": short, "INTEGRATION_INGEST_TOKEN": simple,
                       "SERVICE_API_TOKEN": GOOD2})
    assert not s.admin_token and not s.ingest_token and s.service_token.get() == GOOD2
    assert s.missing("SAQ_ADMIN_TOKEN", "INTEGRATION_INGEST_TOKEN", "SERVICE_API_TOKEN") == [
        "SAQ_ADMIN_TOKEN", "INTEGRATION_INGEST_TOKEN"]
    text = _warnings(caplog)
    assert "SAQ_ADMIN_TOKEN короче 24 символов — считается не заданным" in text
    assert "INTEGRATION_INGEST_TOKEN слишком простой (меньше 12 разных символов) — считается не заданным" in text
    assert short not in text and simple not in text
    assert short in s.secrets() and simple in s.secrets()  # отброшенные значения всё равно маскируются в логах

    caplog.clear()
    s = load_settings({"SAQ_ENV_FILE": "", "SAQ_ADMIN_TOKEN": GOOD2, "SERVICE_API_TOKEN": GOOD2})
    assert not s.admin_token and s.service_token
    assert "SAQ_ADMIN_TOKEN совпадает с SERVICE_API_TOKEN — считается не заданным" in _warnings(caplog)
    caplog.clear()
    s = load_settings({"SAQ_ENV_FILE": "", "SAQ_ADMIN_TOKEN": GOOD, "INTEGRATION_INGEST_TOKEN": GOOD})
    assert not s.admin_token and s.ingest_token
    assert "SAQ_ADMIN_TOKEN совпадает с INTEGRATION_INGEST_TOKEN — считается не заданным" in _warnings(caplog)
    assert GOOD not in _warnings(caplog)
    s = load_settings({"SAQ_ENV_FILE": "", "SAQ_ADMIN_TOKEN": "t-admin-0123456789abcdef0123", "SERVICE_API_TOKEN": GOOD2})
    assert s.admin_token and s.service_token


def test_public_host():
    s = load_settings({"SAQ_ENV_FILE": "", "OLX_REDIRECT_URI": "https://X.ngrok-free.dev/api/integrations/olx/callback"})
    assert s.public_host == "x.ngrok-free.dev" and s.public_path == "/api/integrations/olx/callback"
    s = load_settings({"SAQ_ENV_FILE": "", "OLX_REDIRECT_URI": "https://x.ngrok-free.dev./api/integrations/olx/callback"})
    assert s.public_host == "x.ngrok-free.dev"


def test_env_example_lists_every_name_empty():
    names = {}
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            k, _, v = line.partition("=")
            names[k.strip()] = v.strip()
    assert set(settings.ENV_NAMES) <= set(names)
    assert all(v == "" for v in names.values())


def test_env_labels_cover_every_name():
    """Интерфейс показывает вместо имён переменных их названия(shared/env_labels.json, frontend/src/lib/plain.ts):
    у каждой переменной, которую читает приложение, есть понятное название - без имени переменной и не пустое."""
    import json

    labels = json.loads((REPO / "shared" / "env_labels.json").read_text(encoding="utf-8"))["labels"]
    assert set(settings.ENV_NAMES) | {"DATABASE_URL"} <= set(labels)
    assert all(v.strip() and v == v.strip() and k not in v and "_" not in v for k, v in labels.items())


# ---------- маскирование в логах ----------

@contextmanager
def bare_root():
    """Корневой логгер без обработчиков(pytest добавляет свои на время теста); потом всё как было: обработчики, уровни,
    фабрика записей."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level, logging.getLogRecordFactory(), logs._installed,
             {n: logging.getLogger(n).level for n in logs.NOISY})
    root.handlers = []
    try:
        yield root
    finally:
        root.handlers = saved[0]
        root.setLevel(saved[1])
        logging.setLogRecordFactory(saved[2])
        logs._installed = saved[3]
        for n, lvl in saved[4].items():
            logging.getLogger(n).setLevel(lvl)


def _status_error(url: str) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError("Client error '403 Forbidden' for url '%s'" % url, request=req,
                                 response=httpx.Response(403, request=req))


def test_install_adds_handler_and_masks_exception(caplog):
    buf = io.StringIO()
    with bare_root() as root:
        logs.install(stream=buf, force=True)
        assert len(root.handlers) >= 1 and isinstance(root.handlers[0].formatter, logs.RedactingFormatter)
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").level >= logging.WARNING
        assert logging.getLogger("telethon").level >= logging.WARNING
        root.addHandler(caplog.handler)
        try:
            raise _status_error(f"https://catalog.api.2gis.com/3.0/items?q=x&key={FAKEKEY}")
        except httpx.HTTPStatusError:
            logging.getLogger("app.integrations.dgis").exception("Геокодер вернул ошибку")
    out = buf.getvalue()
    assert "Геокодер вернул ошибку" in out and "key=***" in out and FAKEKEY not in out
    assert "key=***" in caplog.text and FAKEKEY not in caplog.text


def test_install_idempotent():
    buf = io.StringIO()
    with bare_root() as root:
        logs.install(stream=buf, force=True)
        logs.install(stream=buf)
        logs.install(stream=buf, force=True)
        assert len(root.handlers) == 1
        assert not isinstance(root.handlers[0].formatter.inner, logs.RedactingFormatter)
        factory = logging.getLogRecordFactory()
        assert getattr(factory, "_saq_redacting", False) and not getattr(factory._saq_prev, "_saq_redacting", False)
    assert logging.getLogger().handlers  # обработчики pytest возвращены


def test_redact_patterns():
    r = logs.redact
    assert r("GET https://h/cb?code=abc123&state=xyz789&ok=1") == "GET https://h/cb?code=***&state=***&ok=1"
    assert r("https://api.telegram.org/bot123:abc/getMe") == "https://api.telegram.org/bot***/getMe"
    assert r("Authorization: Bearer abc.def-ghi") == "Authorization: Bearer ***"
    assert r("https://user:pass@example.com/x") == "https://example.com/x"
    assert r("u?access_token=a1&refresh_token=b2&X-Amz-Signature=c3&oh=d4&oe=e5") == \
        "u?access_token=***&refresh_token=***&X-Amz-Signature=***&oh=***&oe=***"
    assert r('{"access_token": "abc", "x": 1}') == '{"access_token": "***", "x": 1}'
    assert r("X-API-Key: k-123456") == "X-API-Key: ***"
    assert r("dedupe_key=collect:x") == "dedupe_key=collect:x"  # не параметр запроса
    assert r("x" * 600).endswith("…") and len(r("x" * 600)) == 500 and len(r("x" * 600, limit=None)) == 600
    assert r(None) is None


def _fastest(fn, runs: int = 3) -> float:
    import time

    best = float("inf")
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t)
    return best


def test_redact_is_linear_on_long_tokens():
    """Регрессия: "x-x-x-…" и "a.a.a.…"(путь запроса в журнале доступа uvicorn) маскировались за квадратичное время -
    один запрос с длинным путём останавливал цикл событий API на десятки секунд."""
    for text in ("x-" * 50000, "a." * 50000, "x-a-token-" * 10000, "x-a-" * 25000 + "token"):
        assert _fastest(lambda: logs.redact(text, limit=None)) < 0.1, text[:12]
    r = logs.redact  # маскирование после переписывания шаблонов - прежнее
    assert r("X-Goog-Api-Token: abc123 x") == "X-Goog-Api-Token: *** x"
    assert r("x-telegram-bot-api-secret-token=abc123") == "x-telegram-bot-api-secret-token=***"
    assert r("x-admin-token: abc123") == "x-admin-token: ***" and r("x-token-count: 5") == "x-token-count: 5"
    assert r("see ftp://u:p@files.example.com/a and HTTPS://Tok@h.kz/") == "see ftp://files.example.com/a and HTTPS://h.kz/"
    assert r("mail user@example.com") == "mail user@example.com"


def test_long_log_args_clipped_and_masked(env):
    env(DGIS_API_KEY="dgis-key-FAKE0123456789")
    path = "/" + "x-" * 40000
    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", "GET", path, "1.1", 404),
        None)
    assert len(record.args[2]) == logs.LOG_ARG_MAX + 1 and record.args[2].endswith("…") and record.args[4] == 404
    # секрет на границе обрезки не остаётся видимым наполовину
    edge = "a" * (logs.LOG_ARG_MAX - 5) + " dgis-key-FAKE0123456789 " + "b" * 100
    clipped = logs._arg(edge)
    assert "dgis-key" not in clipped and "dgis-" not in clipped and clipped.endswith("…")
    msg = logging.getLogger("app.test").makeRecord("app.test", logging.INFO, __file__, 1, "x" * 50000, None, None).msg
    assert len(msg) == logs.LOG_ARG_MAX + 1


def test_clipped_secret_cut_by_window_not_shown_after_shrink():
    """Регрессия: секрет, разрезанный концом окна маскирования, не маскируется; если маскирование до него укоротило
    текст больше чем на CLIP_WINDOW, его начало попадало в выводимые LOG_ARG_MAX символов."""
    long_secret = "shrink-secret-" + "x" * 186
    cut = "cut-secret-0123456789abcdefghijklmnopqrstu"
    logs.register_secret(long_secret)
    logs.register_secret(cut)
    head = long_secret * 30  # 6000 символов → 90 после маскирования
    window = logs.LOG_ARG_MAX + logs.CLIP_WINDOW
    text = head + "a" * (window - 20 - len(head)) + cut + "b" * 1000
    out = logs._clipped(text)
    assert cut[:12] not in out and "shrink-secret" not in out and out.endswith("…")
    assert len(logs._clipped("a" * (window + 10))) == logs.LOG_ARG_MAX + 1  # без маскирования - как раньше


def test_redact_settings_and_registered_secrets(env):
    env(APIFY_TOKEN="apify_api_FakeToken0123456789")
    assert logs.redact("token apify_api_FakeToken0123456789 used") == "token *** used"
    logs.register_secret("s3cr3t-value/+=")
    logs.register_secret("short")  # короче 8 - не регистрируется
    text = "a s3cr3t-value/+= b s3cr3t-value%2F%2B%3D c short"
    assert logs.redact(text) == "a *** b *** c short"


def test_record_factory_masks_args_and_keeps_format(env, caplog):
    env(DGIS_API_KEY="dgis-key-FAKE0123456789")
    buf = io.StringIO()
    with bare_root() as root:
        logs.install(stream=buf, force=True)
        root.addHandler(caplog.handler)
        log = logging.getLogger("app.test")
        log.warning("запрос ?key=%s и %d попыток", "dgis-key-FAKE0123456789", 3)
        log.warning("словарь %(a)s", {"a": "Bearer zzz"})
    assert "dgis-key-FAKE" not in caplog.text and "?key=*** и 3 попыток" in caplog.text
    out = buf.getvalue()
    assert "dgis-key-FAKE" not in out and "?key=*** и 3 попыток" in out and "словарь Bearer ***" in out
    assert "Logging error" not in out


def test_uvicorn_access_formatter_still_works():
    from uvicorn.logging import AccessFormatter

    fmt = logs.RedactingFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    with bare_root():
        logs.install(handler=False, force=True)
        record = logging.getLogger("uvicorn.access").makeRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1:5000", "GET", "/api/x?token=abc123", "1.1", 200), None)
    assert isinstance(record.args, tuple) and len(record.args) == 5 and record.args[4] == 200
    line = fmt.format(record)
    assert re.search(r'127\.0\.0\.1:5000 - "GET /api/x\?token=\*\*\* HTTP/1\.1" 200', line), line


def test_non_finite_floats_rejected(caplog):
    """inf/nan в числах с плавающей точкой - не значение: "inf" секунд опроса или долларов бюджета снимало бы предел."""
    caplog.set_level(logging.WARNING, logger="saq.settings")
    s = load_settings({"SAQ_ENV_FILE": "", "SAQ_WORKER_POLL_S": "inf", "APIFY_MAX_TOTAL_CHARGE_USD": "Infinity",
                       "KRISHA_MIN_DELAY_S": "nan", "APIFY_DAILY_BUDGET_USD": "1e400"})
    assert (s.worker_poll_s, s.apify_max_total_charge_usd, s.krisha_min_delay_s, s.apify_daily_budget_usd) == (
        5.0, 0.5, 5.0, 1.0)
    text = _warnings(caplog)
    for name in ("SAQ_WORKER_POLL_S", "APIFY_MAX_TOTAL_CHARGE_USD", "KRISHA_MIN_DELAY_S", "APIFY_DAILY_BUDGET_USD"):
        assert f"{name}: ожидается число" in text


def test_invalid_redirect_uri_not_used(caplog):
    """OLX_REDIRECT_URI не адрес https с хостом и путём обратного вызова OLX - не используется(с предупреждением по
    имени): раньше "https://[x" давал ValueError в public_host на каждом запросе к API, а опечатка в пути("/" или
    "/api/sources/connectors") открывала бы этот путь API через публичный хост(PublicHostGuard)."""
    caplog.set_level(logging.WARNING, logger="saq.settings")
    cb = settings.OLX_CALLBACK_PATH
    for bad in ("https://[x.ngrok-free.dev/cb", "x.ngrok-free.dev/api/integrations/olx/callback", "ftp://x.kz/cb",
                "https://x.ngrok-free.dev/", "https://x.ngrok-free.dev", "https://x.ngrok-free.dev/api/sources/connectors",
                f"https://x.ngrok-free.dev{cb}/", f"https://x.ngrok-free.dev{cb.upper()}", f"http://x.ngrok-free.dev{cb}",
                f"http://localhost:8000{cb}", f"https://x.ngrok-free.dev{cb}#x", f"https://x.ngrok-free.dev{cb}#",
                f"https://x.ngrok-free.dev/api/integrations/olx/../../ads{cb}", f"https://x.ngrok-free.dev{cb[:-4]}\nback",
                f"https://x.ngrok-free.dev{cb[:-4]}\tback"):
        caplog.clear()
        s = load_settings({"SAQ_ENV_FILE": "", "OLX_REDIRECT_URI": bad})
        assert (s.olx_redirect_uri, s.public_host, s.public_path) == ("", None, None), bad
        assert s.missing("OLX_REDIRECT_URI") == ["OLX_REDIRECT_URI"]
        assert "OLX_REDIRECT_URI: ожидается адрес" in _warnings(caplog) and bad not in _warnings(caplog)
    assert settings._url_host("https://[x") is None and settings._url_host("http://H.kz./") == "h.kz"
    s = load_settings({"SAQ_ENV_FILE": "", "OLX_REDIRECT_URI": f"https://X.ngrok-free.dev:443{cb}?from=olx"})
    assert (s.public_host, s.public_path) == ("x.ngrok-free.dev", cb)


def test_olx_callback_path_matches_route():
    """settings.OLX_CALLBACK_PATH - путь маршрута обратного вызова OLX(settings - листовой модуль и olx не импортирует)."""
    from app.integrations import olx

    assert [(r.path, r.methods) for r in olx.router.routes if r.path == settings.OLX_CALLBACK_PATH] == [
        (settings.OLX_CALLBACK_PATH, {"GET"})]


def test_bot_token_format_warned_by_name(caplog):
    """BOT_TOKEN не в формате Bot API - предупреждение при запуске(по имени, без значения). Значение остаётся: отправка
    объясняет "токен в неверном формате" и в запрос его не подставляет(alerts.call), а в логах оно маскируется."""
    from app import alerts

    assert alerts.BOT_TOKEN_FORMAT is settings.BOT_TOKEN_FORMAT  # отправка проверяет тот же формат
    caplog.set_level(logging.WARNING, logger="saq.settings")
    good = "123456789:AAH-test_BotToken_0123456789abcdefXYZ"
    assert load_settings({"SAQ_ENV_FILE": "", "BOT_TOKEN": good}).bot_token.get() == good
    assert "BOT_TOKEN" not in _warnings(caplog)
    for bad in ("123456789:short", "AAH-test_BotToken_0123456789abcdefXYZ", "123456789:AAH-test_BotToken_0123/../getMe",
                "bot123456789:AAH-test_BotToken_0123456789abcdefXYZ",
                "١٢٣٤٥٦٧٨٩:AAH-test_BotToken_0123456789abcdefXYZ"):  # цифры другого алфавита
        caplog.clear()
        s = load_settings({"SAQ_ENV_FILE": "", "BOT_TOKEN": bad})
        assert s.bot_token.get() == bad and bad in s.secrets() and s.missing("BOT_TOKEN") == []
        text = _warnings(caplog)
        assert "BOT_TOKEN: ожидается токен от @BotFather" in text and bad not in text, bad


def test_registered_secret_masked_after_cached_regex():
    """Regex секретов кэшируется: значение, зарегистрированное после первого маскирования, тоже маскируется."""
    assert logs.redact("before cached-later-secret-1") == "before cached-later-secret-1"
    logs.register_secret("cached-later-secret-1")
    assert logs.redact("after cached-later-secret-1") == "after ***"


def test_token_compare():
    from app import auth

    assert auth._same("t-admin-0123456789abcdef0123", "t-admin-0123456789abcdef0123")
    assert not auth._same("t-admin-0123456789abcdef012", "t-admin-0123456789abcdef0123")
    assert not auth._same("", "t-admin-0123456789abcdef0123") and not auth._same("тест", "test")


def test_cors_origins_parsed():
    """SAQ_CORS_ORIGINS: адреса в виде Origin(нижний регистр, без пути и повторов), "*" поглощает список, не адреса -
    пропускаются с предупреждением без значений."""
    assert settings._origins(None) == ((), 0) and settings._origins(" , ") == ((), 0)
    assert settings._origins("https://SAQ.example.kz/, http://localhost:5180,https://saq.example.kz") == (
        ("https://saq.example.kz", "http://localhost:5180"), 0)
    assert settings._origins("http://[::1]:5180") == (("http://[::1]:5180",), 0)
    assert settings._origins("https://a.kz, *") == (("*",), 0)
    assert settings._origins("ftp://a.kz,https://a.kz/x,https://a.kz:0,https://a.kz:70000,a.kz,https://ok.kz") == (
        ("https://ok.kz",), 5)
    s, warns = settings._parse({"SAQ_ENV_FILE": "", "SAQ_CORS_ORIGINS": "https://ok.kz,javascript:alert(1)"})
    assert s.cors_origins == ("https://ok.kz",)
    assert any("SAQ_CORS_ORIGINS" in w for w in warns) and not any("javascript" in w for w in warns)
    assert settings._parse({"SAQ_ENV_FILE": ""})[0].cors_origins == ()


def test_web_hosts_parsed():
    """SAQ_WEB_HOSTS: имена интерфейса(нижний регистр, без конечной точки и повторов), ".домен" - с поддоменами, как в
    allowedHosts Vite; значения со схемой, портом или путём пропускаются с предупреждением без значений."""
    assert settings._host_names(None) == ((), 0) and settings._host_names(" , ") == ((), 0)
    assert settings._host_names("SAQ-Box, .Tailnet.ts.net., saq-box,100.101.102.103") == (
        ("saq-box", ".tailnet.ts.net", "100.101.102.103"), 0)
    assert settings._host_names("http://a.kz,a.kz:5180,a.kz/x,a..kz,-a.kz,*.kz,.,ok.kz") == (("ok.kz",), 7)
    s, warns = settings._parse({"SAQ_ENV_FILE": "", "SAQ_WEB_HOSTS": "ok.kz,javascript:alert(1)"})
    assert s.web_hosts == ("ok.kz",)
    assert any("SAQ_WEB_HOSTS" in w for w in warns) and not any("javascript" in w for w in warns)
    assert settings._parse({"SAQ_ENV_FILE": ""})[0].web_hosts == ()
    # тесты: TestClient шлёт Host "testserver" - conftest задаёт его именем интерфейса
    assert settings.get_settings().web_hosts == ("testserver",)
