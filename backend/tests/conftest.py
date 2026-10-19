import atexit
import hashlib
import importlib
import io
import ipaddress
import os
import re
import shutil
import socket
import sys
import tempfile

# Временная SQLite — до импорта app (engine создаётся при импорте app.db)
_TMP = tempfile.mkdtemp(prefix="saq-test-")
# SAQ_TEST_DATABASE_URL — прогон на другой БД (например, PostgreSQL из docker compose; её таблицы приложения пересоздаются)
os.environ["DATABASE_URL"] = os.getenv("SAQ_TEST_DATABASE_URL") or f"sqlite:///{_TMP}/test.db"
os.environ["SAQ_ENV_FILE"] = ""  # не читать .env репозитория
os.environ["MEDIA_ROOT"] = f"{_TMP}/media"
os.environ["TG_SESSION_PATH"] = f"{_TMP}/private/telegram.session"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

from app import settings as _settings  # noqa: E402

for _name in _settings.ENV_NAMES:
    if _name not in ("SAQ_ENV_FILE", "MEDIA_ROOT", "TG_SESSION_PATH"):
        os.environ.pop(_name, None)  # реальные ключи разработчика в тесты не попадают

from app import logs as _logs  # noqa: E402

_logs.install(handler=False)  # фабрика записей с маскированием; без stderr-обработчика

import pytest  # noqa: E402

import demo_seed as _demo_seed  # noqa: E402 — демо-набор только для тестов (shared/testdata)
from app import seed as _seed  # noqa: E402

TMP = _TMP
FAKE_ADMIN_TOKEN = "t-admin-0123456789abcdef0123"
# Пустая БД тестов получает демо-набор, а не стартовый набор приложения (shared/snapshot — его проверяет test_snapshot.py)
_seed.START = _demo_seed.start


@pytest.fixture(autouse=True)
def _demo_start():
    """Старт пустой БД — демо-набор; тест может переключить его (demo_seed.live(), monkeypatch) — после теста прежний."""
    _seed.START = _demo_seed.start
    yield
    _seed.START = _demo_seed.start


@pytest.fixture(autouse=True)
def _no_asr(monkeypatch):
    """Распознавание речи в тестах — «faster-whisper не установлен»: модель не скачивается и не загружается, даже если
    пакет стоит в .venv. Тесты речи подменяют speech.available и движок сами (test_speech.py)."""
    from app import speech

    monkeypatch.setattr(speech, "available", lambda: False)


@pytest.fixture
def client():
    """Клиент API на чистой пересеянной БД — у каждого теста своя."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.seed import reset

    reset()
    with TestClient(app) as c:
        yield c


# ---------- окружение ----------

@pytest.fixture
def env():
    """set_env(NAME=value, …) → переменные окружения на время теста (None — удалить) и свежие настройки.
    После теста окружение восстанавливается и настройки перечитываются."""
    with pytest.MonkeyPatch.context() as mp:
        def set_env(**kv):
            for k, v in kv.items():
                if v is None:
                    mp.delenv(k, raising=False)
                else:
                    mp.setenv(k, str(v))
            return _settings.reload_settings()

        yield set_env
    _settings.reload_settings()


@pytest.fixture
def admin_headers(env):
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    return {"X-Admin-Token": FAKE_ADMIN_TOKEN}


# ---------- без сети ----------

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if not isinstance(host, str):
        return False
    h = host.strip("[]").split("%", 1)[0].lower()
    if h in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _net_fail(target) -> None:
    pytest.fail(f"сеть в тестах: {target}")


def _raiser(what: str):
    def fail(*_a, **_kw):
        pytest.fail(f"{what} в тестах не настроен — подмените его в тесте")
    return fail


def _optional_module(name: str):
    """Модуль приложения, если он уже есть (часть модулей появляется на следующих шагах фундамента)."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as e:
        if e.name and (name == e.name or name.startswith(e.name + ".")):
            return None
        raise


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Реальная сеть в тестах запрещена: TRANSPORT/RESOLVER падают, sleep мгновенный, сокеты — только loopback/AF_UNIX
    (прогон на PostgreSQL подключается к localhost); фабрики клиентов Telegram и Anthropic и браузера Krisha —
    заглушки-ошибки."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def _check(sock, address):
        if getattr(socket, "AF_UNIX", None) is not None and sock.family == socket.AF_UNIX:
            return
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback(host):
            _net_fail(host)

    def connect(self, address):
        _check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check(self, address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        if not _is_loopback(address[0]):
            _net_fail(address[0])
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)

    http = _optional_module("app.integrations.http")
    if http is not None:
        import httpx

        monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(lambda r: _net_fail(r.url.host)))
        monkeypatch.setattr(http, "RESOLVER", lambda host, port: _net_fail(host))
        monkeypatch.setattr(http, "SLEEP", lambda s: None)
    _optional_module("app.integrations.registry")  # модули подключений загружены — их фабрики подменяются ниже
    for mod_name, attr, what in (("app.integrations.telegram", "CLIENT_FACTORY", "Клиент Telegram"),
                                 ("app.integrations.krisha", "BROWSER_FACTORY", "Браузер Krisha"),
                                 ("app.integrations.analyzer_anthropic", "CLIENT_FACTORY", "Клиент Anthropic")):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _raiser(what))
    monkeypatch.setenv("SAQ_GALLERY_SYNC_HOURS", "0")  # фото Homeportal воркером — только в тестах app.gallery
    service = sys.modules.get("app.integrations.service")
    if service is not None and hasattr(service, "_REJECT_AT"):
        service._REJECT_AT.clear()  # ограничение частоты записи отказов — своё у каждого теста
    _settings.reload_settings()
    yield


class HttpMock:
    """Ответы внешних сервисов в тестах: .on(method, url_or_regex, …) — правило; позже добавленное правило проверяется
    первым (переопределяет прежнее), times=N — правило срабатывает N раз. url — точный адрес (с query или без) или
    re.Pattern (search по полному адресу). .calls — все запросы, .resolve(host, ip) — что вернёт RESOLVER."""

    def __init__(self):
        self.rules: list[dict] = []
        self.calls: list = []
        self.hosts: dict[str, str] = {}

    def on(self, method: str, url, *, json=None, status: int = 200, headers=None, content=None, handler=None,
           times=None) -> "HttpMock":
        self.rules.append({"method": method.upper(), "url": url, "json": json, "status": status, "headers": headers,
                           "content": content, "handler": handler, "times": times})
        return self

    def resolve(self, host: str, ip: str) -> "HttpMock":
        self.hosts[host] = ip
        return self

    def clear(self) -> None:
        self.rules.clear()
        self.calls.clear()

    @staticmethod
    def _match(rule: dict, request) -> bool:
        if rule["method"] not in ("*", request.method):
            return False
        url, full = rule["url"], str(request.url)
        if isinstance(url, re.Pattern):
            return bool(url.search(full))
        return url in (full, full.split("?", 1)[0])

    def handle(self, request):
        import httpx

        self.calls.append(request)
        for rule in reversed(self.rules):
            if rule["times"] is not None and rule["times"] <= 0:
                continue
            if self._match(rule, request):
                if rule["times"] is not None:
                    rule["times"] -= 1
                if rule["handler"] is not None:
                    return rule["handler"](request)
                if rule["json"] is not None:
                    return httpx.Response(rule["status"], json=rule["json"], headers=rule["headers"])
                return httpx.Response(rule["status"], content=rule["content"] or b"", headers=rule["headers"])
        pytest.fail(f"нет заглушки для {request.method} {request.url}")

    def resolver(self, host: str, port: int) -> list:
        return [self.hosts.get(host, "93.184.216.34")]


@pytest.fixture
def http_mock(monkeypatch):
    import httpx

    from app.integrations import http

    mock = HttpMock()
    monkeypatch.setattr(http, "TRANSPORT", httpx.MockTransport(mock.handle))
    monkeypatch.setattr(http, "RESOLVER", mock.resolver)
    return mock


# ---------- воркер и анализ ----------

@pytest.fixture
def run_ctx(client):
    """make(code, *, config=None, state=None, trigger="manual", payload=None, now=None) → RunContext настоящего запуска:
    строка подключения (config/state записаны), запуск «выполняется» и задание — счётчики, заметки и фазы пишутся в БД."""
    from app import models
    from app.db import SessionLocal
    from app.integrations import registry, service
    from app.integrations.context import RunContext

    def make(code: str, *, config=None, state=None, trigger: str = "manual", payload=None, now=None):
        at = models.as_utc(now) or models.utcnow()
        kind = "test" if trigger == "test" else "collect"
        with SessionLocal() as db:
            row = service.row(db, code, create=True)
            if config is not None:
                row.config = dict(config)
            if state is not None:
                row.state = dict(state)
            run = models.CollectionRun(collector=code, trigger=trigger, status="running", requested_by="тест",
                                       created_at=at, started_at=at, notes=[])
            db.add(run)
            db.flush()
            job = models.Job(kind=kind, status="running", collector=code, run_id=run.id, attempts=1,
                             max_attempts=registry.get(code).max_attempts, run_after=at, locked_by="test",
                             locked_at=at, payload=dict(payload or {}), created_at=at)
            db.add(job)
            db.commit()
            run_id, job_id = run.id, job.id
            cfg, st = dict(row.config or {}), dict(row.state or {})
        return RunContext(code, registry.get(code), run_id=run_id, job_id=job_id, trigger=trigger, config=cfg,
                          state=st, payload=dict(payload or {}), now=now)

    return make


class Acceptance:
    """Приёмка по §14 плана (integration-spec §16.5): снимок счётчиков, видимость сбоя, отсутствие автоподтверждения,
    файл доступен после закрытия исходной вкладки."""

    def __init__(self, client):
        self.client = client

    @staticmethod
    def snapshot() -> dict:
        from sqlalchemy import func, select

        from app import models
        from app.db import SessionLocal

        with SessionLocal() as db:
            return {m.__tablename__: db.scalar(select(func.count()).select_from(m))
                    for m in (models.Ad, models.IngestItem, models.IngestVersion, models.MediaAsset)}

    def connector(self, code: str) -> dict:
        r = self.client.get("/api/sources/connectors")
        assert r.status_code == 200, r.text
        return next(c for c in r.json()["connectors"] if c["code"] == code)

    def assert_failure_visible(self, code: str, run_id=None) -> dict:
        """Сбой не выглядит пустой выдачей: запуск не «успешно», подключение не «работает», причина видна."""
        if run_id is not None:
            run = self.client.get(f"/api/collection-runs/{run_id}").json()
            assert run["status"] != "succeeded", run
        c = self.connector(code)
        assert c["status"] != "working", c
        assert c["statusDetail"] or c["lastError"], c
        return c

    @staticmethod
    def assert_not_confirmed(ad_id: str) -> None:
        from app import models
        from app.db import SessionLocal

        with SessionLocal() as db:
            ad = db.get(models.Ad, ad_id)
            assert (ad.status, ad.complex_id, ad.link_confirmed_by) == ("new", None, None)

    def media_ok(self, asset_id: str) -> bytes:
        r = self.client.get(f"/api/media/{asset_id}")
        assert r.status_code == 200 and r.content, r.status_code
        return r.content


@pytest.fixture
def accept(client):
    return Acceptance(client)

@pytest.fixture
def run_worker(client):
    """run(now=None, max_jobs=50) → число выполненных заданий (worker.run_once от имени воркера «test»)."""
    from app import worker

    def run(*, now=None, max_jobs: int = 50) -> int:
        return worker.run_once(worker_id="test", now=now, max_jobs=max_jobs)

    return run


@pytest.fixture
def ocr_stub(monkeypatch):
    """OCR без Tesseract: «OCR <sha8>» по байтам; ffmpeg «не установлен»."""
    from app import analysis, video

    def recognize(raw: bytes, content_type=None):
        return {"text": f"OCR {hashlib.sha256(raw).hexdigest()[:8]}", "engine": "tesseract"}

    monkeypatch.setattr(analysis.ocr_engine, "recognize", recognize)
    monkeypatch.setattr(video, "ffmpeg_available", lambda: False)
    return recognize


# ---------- файлы для тестов ----------

def png_bytes(color=(200, 30, 30)) -> bytes:
    """PNG 8×8 заданного цвета (у разных цветов разный sha256)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def mp4_bytes(size: int = 256) -> bytes:
    """Минимальная «MP4» для проверки сигнатуры (ftyp isom); не воспроизводится."""
    head = b"\x00\x00\x00\x18ftypisom" + b"\x00\x00\x02\x00isomiso2"
    return head + b"\x00" * max(size - len(head), 0)
