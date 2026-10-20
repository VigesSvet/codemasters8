"""OLX.kz - OAuth собственного аккаунта(integration-spec §12.7, §16.3 test_integration_olx, §16.5): старт с
одноразовым state, возврат, обмен кода сервером, users/me с "Version: 2.0", обновление токена, проверка воркером,
плановые проверки, отсутствие токенов в ответах API и журналах. Сеть - только заглушки(http_mock)."""
import hashlib
import json
import logging
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from conftest import FAKE_ADMIN_TOKEN

AUTHOR = "Омарова Ж."
REDIRECT = "https://x.ngrok-free.dev/api/integrations/olx/callback"
CLIENT_ID = "olx-client-id-0001"
CLIENT_SECRET = "FAKEOLX-secret-0123456789abcdef"
TOKEN_URL = "https://www.olx.kz/api/open/oauth/token"
ME_URL = "https://www.olx.kz/api/partner/users/me"
ACCESS = "FAKE-olx-access-0123456789abcdefABCDEF"
REFRESH = "FAKE-olx-refresh-0123456789abcdefABCDEF"
ACCESS2 = "FAKE-olx-access2-9876543210zyxwvuQRST"
REFRESH2 = "FAKE-olx-refresh2-9876543210zyxwvuQRST"
CODE = "FAKE-olx-code-0123456789abcdef"
ME = {"data": {"id": 123456, "name": "ТОО Тест", "email": "owner@example.com", "phone": "+77010000000",
               "is_business": True, "status": "confirmed"}}
LABEL = "ТОО Тест (id 123456)"
HTML = {"content-type": "text/html; charset=UTF-8"}  # OLX отдаёт JSON с таким заголовком
SECURITY_HEADERS = {"cache-control": "no-store", "referrer-policy": "no-referrer",
                    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
                    "x-content-type-options": "nosniff"}


def olx():
    from app.integrations import olx as mod

    return mod


def admin() -> dict:
    return {"X-Admin-Token": FAKE_ADMIN_TOKEN}


def jresp(status: int, body: dict, headers=None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers=headers or HTML)


def token_body(access=ACCESS, refresh=REFRESH, expires_in=86400) -> dict:
    body = {"access_token": access, "expires_in": expires_in, "token_type": "bearer", "scope": "read v2"}
    if refresh:
        body["refresh_token"] = refresh
    return body


@pytest.fixture
def olx_env(env):
    return env(OLX_CLIENT_ID=CLIENT_ID, OLX_CLIENT_SECRET=CLIENT_SECRET, OLX_REDIRECT_URI=REDIRECT,
               SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)


def connector(client) -> dict:
    r = client.get("/api/sources/connectors")
    assert r.status_code == 200, r.text
    return next(c for c in r.json()["connectors"] if c["code"] == "olx")


def start(client) -> str:
    r = client.post("/api/integrations/olx/start", json={"author": AUTHOR}, headers=admin())
    assert r.status_code == 200, r.text
    return parse_qs(urlsplit(r.json()["authorizeUrl"]).query)["state"][0]


def callback(client, **params):
    return client.get("/api/integrations/olx/callback", params=params)


def token_row():
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        return db.get(models.IntegrationToken, "olx")


def state_row(state: str):
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        return db.get(models.OAuthState, hashlib.sha256(state.encode()).hexdigest())


def seed_token(*, expires_in=timedelta(hours=20), refresh=REFRESH, refresh_in=timedelta(days=20), rev=0,
               access=ACCESS, label=None):
    from app import models
    from app.db import SessionLocal

    now = models.utcnow()
    with SessionLocal() as db:
        db.merge(models.IntegrationToken(
            provider="olx", access_token=access, refresh_token=refresh, token_type="bearer", scope="read v2",
            expires_at=now + expires_in if expires_in is not None else None,
            refresh_expires_at=now + refresh_in if refresh and refresh_in is not None else None,
            account_label=label, obtained_at=now, updated_at=now, rev=rev))
        db.commit()


def set_row(**fields):
    from app.db import SessionLocal
    from app.integrations import service

    with SessionLocal() as db:
        r = service.row(db, "olx", create=True)
        for k, v in fields.items():
            setattr(r, k, v)
        db.commit()


def audit_texts(who=None, kind=None) -> list[str]:
    from app import models
    from app.db import SessionLocal

    A = models.AuditEvent
    with SessionLocal() as db:
        q = select(A.what)
        if who is not None:
            q = q.where(A.who == who)
        if kind is not None:
            q = q.where(A.kind == kind)
        return list(db.scalars(q))


def mock_success(http_mock, captured: dict):
    def token(request):
        captured["token"] = request
        return jresp(200, token_body())

    def me(request):
        captured.setdefault("me", []).append(request)
        return jresp(200, ME)

    http_mock.on("POST", TOKEN_URL, handler=token).on("GET", ME_URL, handler=me)


def connect(client, http_mock) -> dict:
    captured: dict = {}
    mock_success(http_mock, captured)
    state = start(client)
    r = callback(client, code=CODE, state=state)
    assert r.status_code == 200, r.text
    captured["state"], captured["response"] = state, r
    return captured


def run_test(client, run_worker, **kw) -> dict:
    r = client.post("/api/sources/olx/test", json={"author": AUTHOR}, headers=admin())
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert run_worker(**kw) >= 1
    return client.get(f"/api/collection-runs/{run_id}").json()


# ---------- 1. не настроен ----------

def test_not_configured(client, env):
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["group"], c["kind"]) == ("not_configured", "заявка отправлена",
                                                                      "limited", "oauth")
    assert c["missing"] == ["OLX_CLIENT_ID", "OLX_CLIENT_SECRET", "OLX_REDIRECT_URI"]
    assert c["capabilities"] == {"test": True, "collect": False, "toggle": False, "configure": False, "connect": True}
    assert "только к ресурсам своего авторизованного аккаунта" in c["limitation"]
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    r = client.post("/api/integrations/olx/start", json={"author": AUTHOR}, headers=admin())
    assert (r.status_code, r.json()["detail"]) == (409, olx().NOT_CONFIGURED)
    assert r.json()["detail"] == ("OLX не настроен: заявка отправлена, ключей OLX_CLIENT_ID/OLX_CLIENT_SECRET нет "
                                  "на сервере")
    env(OLX_CLIENT_ID=CLIENT_ID, OLX_CLIENT_SECRET=CLIENT_SECRET)
    r = client.post("/api/integrations/olx/start", json={"author": AUTHOR}, headers=admin())
    assert (r.status_code, r.json()["detail"]) == (409, "OLX не настроен: нет на сервере OLX_REDIRECT_URI")
    # без ключей проверка из командной строки не ходит в сеть(conftest запрещает сеть)
    env(OLX_CLIENT_ID=None, OLX_CLIENT_SECRET=None)
    from app.integrations import smoke

    assert smoke.check_one("olx") == "olx: не настроен — нет OLX_CLIENT_ID, OLX_CLIENT_SECRET, OLX_REDIRECT_URI"
    assert client.post("/api/sources/olx/test", json={"author": AUTHOR}, headers=admin()).status_code == 409


def test_admin_required(client, olx_env):
    assert client.post("/api/integrations/olx/start", json={"author": AUTHOR}).status_code == 401
    r = client.post("/api/integrations/olx/start", json={"author": AUTHOR}, headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 403
    assert client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}).status_code == 401
    r = client.post("/api/integrations/olx/start", json={"author": "Система"}, headers=admin())
    assert r.status_code == 422


# ---------- 2. старт ----------

def test_start_builds_authorize_url_and_stores_only_hash(client, olx_env):
    from app import auth, models
    from app.db import SessionLocal

    m = olx()
    with SessionLocal() as db:  # просроченный state удаляется при следующем старте
        old = models.utcnow() - timedelta(hours=1)
        db.add(models.OAuthState(state_hash="0" * 64, provider="olx", created_by=AUTHOR, admin_fp="x", created_at=old,
                                 expires_at=old + timedelta(minutes=10)))
        db.commit()
    before = models.utcnow()
    r = client.post("/api/integrations/olx/start", json={"author": AUTHOR}, headers=admin())
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"authorizeUrl", "expiresAt"}
    u = urlsplit(body["authorizeUrl"])
    assert f"{u.scheme}://{u.netloc}{u.path}" == m.AUTHORIZE == "https://www.olx.kz/oauth/authorize/"
    q = parse_qs(u.query)
    assert {k: v[0] for k, v in q.items() if k != "state"} == {"client_id": CLIENT_ID, "response_type": "code",
                                                                "scope": "read v2", "redirect_uri": REDIRECT}
    state = q["state"][0]
    assert len(state) >= 43 and CLIENT_SECRET not in body["authorizeUrl"]
    with SessionLocal() as db:
        rows = list(db.scalars(select(models.OAuthState)))
    assert len(rows) == 1  # просроченный удалён
    row = rows[0]
    assert row.state_hash == hashlib.sha256(state.encode()).hexdigest()
    assert (row.provider, row.created_by, row.admin_fp, row.used_at) == ("olx", AUTHOR,
                                                                        auth.token_fp(FAKE_ADMIN_TOKEN), None)
    assert all(state not in str(getattr(row, c.key)) for c in models.OAuthState.__table__.columns)
    expires = models.as_utc(row.expires_at)
    assert timedelta(minutes=9) < expires - before <= timedelta(minutes=10, seconds=5)
    # два старта - два разных state
    assert start(client) != state


# ---------- 3. возврат: успех ----------

def test_callback_success(client, olx_env, http_mock):
    from app import models

    before = models.utcnow()
    cap = connect(client, http_mock)
    r = cap["response"]
    # обмен кода - JSON с ключами приложения, кодом, scope и redirect_uri
    req = cap["token"]
    assert req.headers["content-type"].startswith("application/json")
    assert json.loads(req.content) == {"grant_type": "authorization_code", "client_id": CLIENT_ID,
                                       "client_secret": CLIENT_SECRET, "code": CODE, "scope": "read v2",
                                       "redirect_uri": REDIRECT}
    assert "?" not in str(req.url)
    # users/me - Bearer и Version: 2.0, без Content-Type, токен не в адресе
    me = cap["me"][0]
    assert (me.headers["authorization"], me.headers["version"]) == (f"Bearer {ACCESS}", "2.0")
    assert "content-type" not in me.headers and ACCESS not in str(me.url)
    # страница: без токенов и скриптов, с заголовками безопасности
    assert r.headers["content-type"].startswith("text/html")
    for k, v in SECURITY_HEADERS.items():
        assert r.headers[k] == v, k
    text = r.text
    assert f"OLX подключён: {LABEL}. Можно закрыть вкладку и вернуться на страницу «Источники»" in text
    for secret in (ACCESS, REFRESH, CODE, cap["state"], CLIENT_SECRET, "owner@example.com", "+77010000000"):
        assert secret not in text
    assert "<script" not in text.lower()
    # токены - в БД
    tok = token_row()
    assert (tok.access_token, tok.refresh_token, tok.account_label, tok.rev, tok.scope) == (
        ACCESS, REFRESH, LABEL, 0, "read v2")
    assert timedelta(hours=23, minutes=59) < models.as_utc(tok.expires_at) - before <= timedelta(hours=24, minutes=1)
    assert timedelta(days=29) < models.as_utc(tok.refresh_expires_at) - before <= timedelta(days=30, minutes=1)
    # подключение - "подключено · ограничено", не "работает"; сведения без e-mail и телефона
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("ready", "подключено · ограничено")
    assert c["facts"]["account"] == LABEL and c["facts"]["isBusiness"] is True
    assert "чужие объявления так не собираются" in c["statusDetail"]
    assert c["checkedAt"] is not None
    assert "owner@example.com" not in json.dumps(c, ensure_ascii=False)
    # журнал - от автора старта, видом connector: имя владельца аккаунта - сведения администратора
    assert (f"OLX: подключён аккаунт {LABEL} (OAuth). Доступны только ресурсы этого аккаунта — чужие объявления так "
            f"не собираются") in audit_texts(AUTHOR, kind="connector")
    # state погашен
    assert state_row(cap["state"]).used_at is not None


def test_callback_reconnect_replaces_token(client, olx_env, http_mock):
    seed_token(access="FAKE-olx-stale-access-0123456789", rev=5, label="Старый (id 1)")
    connect(client, http_mock)
    tok = token_row()
    assert (tok.access_token, tok.refresh_token, tok.rev, tok.account_label) == (ACCESS, REFRESH, 6, LABEL)


# ---------- 4. возврат: отказы ----------

def test_callback_rejections(client, olx_env, http_mock, env):
    from app import models
    from app.db import SessionLocal

    m = olx()
    assert m.BAD_STATE == ("Ссылка авторизации OLX недействительна или устарела — начните подключение заново на "
                           "странице «Источники»")
    mock_success(http_mock, {})
    # повтор
    cap = connect(client, http_mock)
    r = callback(client, code=CODE, state=cap["state"])
    assert r.status_code == 400 and m.BAD_STATE in r.text
    for k, v in SECURITY_HEADERS.items():
        assert r.headers[k] == v
    http_mock.clear()  # дальше - ни одного запроса к OLX
    # неизвестный и пустой state
    assert callback(client, code=CODE, state="no-such-state-0123456789").status_code == 400
    assert callback(client, code=CODE).status_code == 400
    assert callback(client, code=CODE, state="x" * 600).status_code == 400
    # чужие значения с публичного адреса не становятся "секретами" маскирования(список не растёт, текст не портится)
    from app import logs

    junk = ("FAKE-junk-state-0123456789abcdef", "FAKE-junk-code-0123456789abcdef")
    assert callback(client, code=junk[1], state=junk[0]).status_code == 400
    assert all(logs.redact(f"value {v}") == f"value {v}" for v in junk)
    # просроченный
    state = start(client)
    with SessionLocal() as db:
        row = db.get(models.OAuthState, hashlib.sha256(state.encode()).hexdigest())
        row.expires_at = models.utcnow() - timedelta(seconds=1)
        db.commit()
    r = callback(client, code=CODE, state=state)
    assert r.status_code == 400 and m.BAD_STATE in r.text
    # токен администратора сменился между стартом и возвратом - state погашен
    state = start(client)
    env(SAQ_ADMIN_TOKEN="t-admin-other-0123456789abcdefXYZ")
    r = callback(client, code=CODE, state=state)
    assert r.status_code == 400 and m.BAD_STATE in r.text
    assert state_row(state).used_at is not None
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    assert callback(client, code=CODE, state=state).status_code == 400
    # отказ провайдера - 400 с описанием, state погашен
    state = start(client)
    r = callback(client, error="access_denied", error_description="The user denied access", state=state)
    assert r.status_code == 400 and "OLX отказал в доступе: The user denied access" in r.text
    assert state_row(state).used_at is not None
    assert callback(client, code=CODE, state=state).status_code == 400
    # описание экранируется
    state = start(client)
    r = callback(client, error="access_denied", error_description="<script>alert(1)</script>", state=state)
    assert r.status_code == 400 and "<script>" not in r.text and "&lt;script&gt;" in r.text
    # нет кода
    state = start(client)
    r = callback(client, state=state)
    assert r.status_code == 400 and "не передал код авторизации" in r.text
    assert http_mock.calls == []
    tok = token_row()
    assert tok.access_token == ACCESS and tok.rev == 0  # отказы токен не трогают


def test_callback_exchange_failures(client, olx_env, http_mock):
    m = olx()
    http_mock.on("POST", TOKEN_URL, json={"error": "invalid_grant", "error_description": "Code expired"}, status=400)
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 400 and m.BAD_CODE in r.text and token_row() is None
    http_mock.on("POST", TOKEN_URL, json={"error": "invalid_client"}, status=401)
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 400 and "OLX не принял ключи приложения" in r.text and token_row() is None
    assert "OLX_CLIENT" not in r.text  # имена переменных окружения на странице не показываются
    http_mock.on("POST", TOKEN_URL, content=b"<html>502</html>", status=502, headers=HTML)
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502 and "OLX не выдал токен" in r.text and token_row() is None
    # токен выдан, users/me не прошёл - токен сохранён, статус "ошибка" с причиной(не "подключено")
    http_mock.on("POST", TOKEN_URL, handler=lambda req: jresp(200, token_body()))
    http_mock.on("GET", ME_URL, status=500, content=b"oops")
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502 and "аккаунт не подтверждён" in r.text and ACCESS not in r.text
    assert token_row().access_token == ACCESS
    c = connector(client)
    assert c["status"] == "error" and "OLX временно недоступен" in c["statusDetail"]


# ---------- 5. обновление токена ----------

def _ensure(now=None) -> str:
    from app.db import SessionLocal
    from app.integrations import http
    from app.settings import get_settings

    m = olx()
    with http.client(base_url=m.ORIGIN) as c, SessionLocal() as db:
        return m.ensure_token(db, c, get_settings(), now=now)


def test_refresh_when_less_than_hour_left(client, olx_env, http_mock):
    from app import models

    seed_token(expires_in=timedelta(minutes=30), rev=3)
    seen: list = []

    def handler(request):
        seen.append(json.loads(request.content))
        return jresp(200, token_body(ACCESS2, REFRESH2))

    http_mock.on("POST", TOKEN_URL, handler=handler)
    before = models.utcnow()
    assert _ensure() == ACCESS2
    assert seen == [{"grant_type": "refresh_token", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                     "refresh_token": REFRESH}]
    tok = token_row()
    assert (tok.access_token, tok.refresh_token, tok.rev) == (ACCESS2, REFRESH2, 4)
    assert models.as_utc(tok.expires_at) - before > timedelta(hours=23)
    assert models.as_utc(tok.refresh_expires_at) - before > timedelta(days=29)
    # больше часа до истечения - без сети
    http_mock.clear()
    assert _ensure() == ACCESS2 and http_mock.calls == []


def test_refresh_keeps_refresh_token_when_not_returned(client, olx_env, http_mock):
    seed_token(expires_in=timedelta(minutes=5))
    old = token_row()
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, None)))
    assert _ensure() == ACCESS2
    tok = token_row()
    assert (tok.refresh_token, tok.refresh_expires_at, tok.rev) == (REFRESH, old.refresh_expires_at, 1)


def test_refresh_invalid_grant_expired(client, olx_env, http_mock):
    from app.integrations.base import AuthExpired

    m = olx()
    seed_token(expires_in=timedelta(minutes=10))
    http_mock.on("POST", TOKEN_URL, json={"error": "invalid_grant", "error_description": "revoked"}, status=400)
    with pytest.raises(AuthExpired) as e:
        _ensure()
    assert e.value.detail == m.EXPIRED == "Доступ OLX истёк — подключите аккаунт заново"
    assert token_row().access_token == ACCESS


def test_refresh_concurrent_update_not_overwritten(client, olx_env, http_mock):
    from sqlalchemy import update

    from app import models
    from app.db import SessionLocal

    seed_token(expires_in=timedelta(minutes=10), rev=1)

    def handler(request):  # пока идёт запрос, токен обновил другой процесс
        T = models.IntegrationToken
        with SessionLocal() as db:
            db.execute(update(T).where(T.provider == "olx").values(access_token="FAKE-olx-other-0123456789",
                                                                  refresh_token="FAKE-olx-other-rt-0123456789",
                                                                  expires_at=models.utcnow() + timedelta(hours=24),
                                                                  rev=T.rev + 1))
            db.commit()
        return jresp(200, token_body(ACCESS2, REFRESH2))

    http_mock.on("POST", TOKEN_URL, handler=handler)
    assert _ensure() == "FAKE-olx-other-0123456789"
    tok = token_row()
    assert (tok.refresh_token, tok.rev) == ("FAKE-olx-other-rt-0123456789", 2)


def test_refresh_invalid_grant_after_concurrent_refresh(client, olx_env, http_mock):
    from sqlalchemy import update

    from app import models
    from app.db import SessionLocal

    seed_token(expires_in=timedelta(minutes=10), rev=1)

    def handler(request):  # другой процесс уже обменял этот refresh token - OLX отвечает invalid_grant
        T = models.IntegrationToken
        with SessionLocal() as db:
            db.execute(update(T).where(T.provider == "olx").values(access_token="FAKE-olx-other-0123456789",
                                                                  expires_at=models.utcnow() + timedelta(hours=24),
                                                                  rev=T.rev + 1))
            db.commit()
        return jresp(400, {"error": "invalid_grant"})

    http_mock.on("POST", TOKEN_URL, handler=handler)
    assert _ensure() == "FAKE-olx-other-0123456789"


def test_parse_json_ignores_content_type():
    m = olx()
    assert m.parse_json(httpx.Response(200, content=b'{"a": 1}', headers=HTML)) == {"a": 1}
    with pytest.raises(m.SourceError):
        m.parse_json(httpx.Response(200, content=b"<html></html>", headers=HTML))
    with pytest.raises(m.SourceError):
        m.parse_json(httpx.Response(200, content=b"[1, 2]"))
    with pytest.raises(m.SourceError):  # вложенность глубже предела интерпретатора - не RecursionError
        m.parse_json(httpx.Response(200, content=b"[" * 100_000))
    with pytest.raises(m.SourceError):
        m.parse_json(httpx.Response(200, content=b"\xff\xfe{}"))


def test_page_without_env_names():
    """Страница возврата - без имён переменных окружения(как интерфейс, frontend/src/lib/plain.ts)."""
    m = olx()
    page = m._page(m.NOT_CONFIGURED, 409).body.decode()
    assert "OLX_CLIENT" not in page and "ID приложения OLX/секрет приложения OLX" in page
    assert m._plain(f"{m.BAD_CLIENT}: x") == "OLX не принял ключи приложения: x"
    assert m._plain("проверьте права приложения (OLX_SCOPES)") == "проверьте права приложения"
    assert m._plain("OLX_NO_SUCH_NAME не задан") == "настройка сервера не задан"


def test_broken_response_encoding_is_source_error(client, olx_env, http_mock):
    """Ответ OLX с испорченным сжатием(httpx.DecodingError - не TransportError) - ошибка источника с повтором,
    а не необработанное исключение: токен после обмена кода сохранён, статус подключения - "ошибка"."""
    from app.integrations import http
    from app.settings import get_settings

    m = olx()
    broken = {"content-encoding": "gzip", "content-type": "application/json"}
    http_mock.on("GET", ME_URL, content=b"not gzip at all", headers=broken)
    with http.client(base_url=m.ORIGIN) as cl, pytest.raises(m.SourceError) as e:
        m.users_me(cl, ACCESS)
    assert e.value.retryable and "DecodingError" in e.value.detail and ACCESS not in e.value.detail
    http_mock.on("POST", TOKEN_URL, content=b"not gzip at all", headers=broken)
    with http.client(base_url=m.ORIGIN) as cl, pytest.raises(m.SourceError):
        m.exchange_code(cl, CODE, get_settings())
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body()))
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502 and "аккаунт не подтверждён" in r.text
    assert token_row().access_token == ACCESS and connector(client)["status"] == "error"


# ---------- 6. проверка воркером ----------

def test_check_ok_via_worker(client, olx_env, http_mock, run_worker):
    seed_token()
    calls: list = []
    http_mock.on("GET", ME_URL, handler=lambda r: calls.append(r) or jresp(200, ME))
    run = run_test(client, run_worker)
    assert run["status"] == "succeeded", run
    assert len(calls) == 1 and calls[0].headers["version"] == "2.0"
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["facts"]["account"]) == ("ready", "подключено · ограничено", LABEL)
    assert token_row().account_label == LABEL


def test_check_401_refresh_then_ok(client, olx_env, http_mock, run_worker):
    seed_token()
    http_mock.on("GET", ME_URL, handler=lambda r: jresp(200, ME) if r.headers["authorization"].endswith(ACCESS2)
                 else jresp(401, {"error": "invalid_token"}))
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, REFRESH2)))
    run = run_test(client, run_worker)
    assert run["status"] == "succeeded", run
    assert [str(c.url).split("?")[0] for c in http_mock.calls] == [ME_URL, TOKEN_URL, ME_URL]
    assert (token_row().access_token, token_row().rev) == (ACCESS2, 1)
    assert connector(client)["status"] == "ready"


def test_check_401_refresh_401_expired(client, olx_env, http_mock, run_worker):
    seed_token()
    http_mock.on("GET", ME_URL, json={"error": "invalid_token"}, status=401)
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, REFRESH2)))
    run = run_test(client, run_worker)
    assert (run["status"], run["errorCode"]) == ("failed", "auth_expired")
    assert [str(c.url) for c in http_mock.calls] == [ME_URL, TOKEN_URL, ME_URL]
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["statusDetail"]) == ("expired", "истёк доступ",
                                                                  "Доступ OLX истёк — подключите аккаунт заново")


def test_check_invalid_grant_expired(client, olx_env, http_mock, run_worker):
    seed_token(expires_in=timedelta(minutes=20))
    http_mock.on("POST", TOKEN_URL, json={"error": "invalid_grant"}, status=400)
    run = run_test(client, run_worker)
    assert (run["status"], run["errorCode"]) == ("failed", "auth_expired")
    assert connector(client)["status"] == "expired"


def test_computed_statuses(client, olx_env, http_mock, run_worker):
    m = olx()
    c = connector(client)  # ключи есть, токена нет
    assert (c["status"], c["statusLabel"], c["statusDetail"]) == ("unchecked", "не подключён", m.UNCHECKED)
    run = run_test(client, run_worker)  # проверка без токена - "не подключён", без сети
    assert run["status"] == "not_configured" and http_mock.calls == []
    assert connector(client)["status"] == "unchecked"
    seed_token(refresh_in=timedelta(days=-1))  # refresh token истёк
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["statusDetail"]) == ("expired", "истёк доступ", m.EXPIRED)
    seed_token(expires_in=timedelta(minutes=-1), refresh=None)  # токен истёк, обновить нечем
    assert connector(client)["status"] == "expired"
    seed_token()
    set_row(status="ready", status_detail="ok")
    assert connector(client)["status"] == "ready"


def test_disconnect(client, olx_env, http_mock):
    connect(client, http_mock)
    r = client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}, headers=admin())
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["code"], body["status"], body["statusLabel"]) == ("olx", "unchecked", "не подключён")
    assert "account" not in body["facts"] and token_row() is None
    assert f"OLX: аккаунт {LABEL} отключён — токены удалены с сервера" in audit_texts(AUTHOR, kind="connector")
    r = client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}, headers=admin())
    assert r.status_code == 200  # повтор безопасен


def test_disconnect_without_token_clears_account_facts(client, olx_env):
    """Токена уже нет, а в сведениях остался аккаунт(остаток гонки) - повторное отключение их убирает, журнал не
    засоряется."""
    set_row(status="ready", facts={"account": LABEL, "isBusiness": True, "keyFp": {"OLX_CLIENT_ID": "x"}})
    before = len(audit_texts())
    r = client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}, headers=admin())
    assert r.status_code == 200, r.text
    facts = r.json()["facts"]
    assert "account" not in facts and "isBusiness" not in facts and facts["keyFp"] == {"OLX_CLIENT_ID": "x"}
    assert len(audit_texts()) == before
    # строки подключения нет вовсе - тоже без ошибки
    from app import models
    from app.db import SessionLocal

    with SessionLocal() as db:
        db.delete(db.get(models.Collector, "olx"))
        db.commit()
    assert client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}, headers=admin()).status_code == 200


# ---------- 6а. переподключение и отключение во время проверки ----------

OLD_LABEL = "Старый (id 1)"
OLD_ME = {"data": {"id": 1, "name": "Старый", "is_business": False}}


def reconnect_now(access=ACCESS2, refresh=REFRESH2, me=ME) -> None:
    """То, что делает возврат из OLX: новые токены(новое поколение) и подтверждённый аккаунт."""
    from app import models
    from app.db import SessionLocal
    from app.settings import get_settings

    m = olx()
    now = models.utcnow()
    with SessionLocal() as db:
        m.save_token(db, token_body(access, refresh), now)
        db.commit()
    assert m._connected(m.account_label(me["data"]), me["data"], AUTHOR, now, get_settings())


def test_check_reconnect_during_check_verifies_current_account(client, olx_env, http_mock, run_worker):
    """Пока users/me со старым токеном идёт к OLX, администратор подключил другой аккаунт: ответ про старый аккаунт не
    пишется в новое подключение, проверяется текущее(новый токен)."""
    seed_token(label=OLD_LABEL)
    set_row(status="ready", facts={"account": OLD_LABEL, "isBusiness": False})
    seen: list = []

    def me(request):
        seen.append(request.headers["authorization"])
        if request.headers["authorization"] == f"Bearer {ACCESS}":
            reconnect_now()
            return jresp(200, OLD_ME)
        return jresp(200, ME)

    http_mock.on("GET", ME_URL, handler=me)
    run = run_test(client, run_worker)
    assert run["status"] == "succeeded", run
    assert seen == [f"Bearer {ACCESS}", f"Bearer {ACCESS2}"]
    assert any(n["text"] == olx().REPLACED_NOTE for n in run["notes"])
    tok = token_row()
    assert (tok.access_token, tok.account_label) == (ACCESS2, LABEL)
    c = connector(client)
    assert (c["status"], c["facts"]["account"], c["facts"]["isBusiness"]) == ("ready", LABEL, True)
    assert OLD_LABEL not in json.dumps(c, ensure_ascii=False)


def test_check_disconnect_during_check_not_resurrected(client, olx_env, http_mock, run_worker):
    """Администратор отключил аккаунт, пока проверка ждала ответ: сведения об аккаунте не возвращаются, статус - "не
    подключён"."""
    seed_token(label=LABEL)
    set_row(status="ready", facts={"account": LABEL, "isBusiness": True})

    def me(request):
        r = client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}, headers=admin())
        assert r.status_code == 200, r.text
        return jresp(200, ME)

    http_mock.on("GET", ME_URL, handler=me)
    run = run_test(client, run_worker)
    assert (run["status"], run["errorCode"]) == ("not_configured", "not_configured"), run
    assert token_row() is None and len(http_mock.calls) == 1
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("unchecked", "не подключён")
    assert "account" not in c["facts"] and "isBusiness" not in c["facts"]


def test_check_replaced_twice_fails_without_account(client, olx_env, http_mock, run_worker):
    """Подключение сменилось и при повторной проверке - ошибка с причиной, сведения о проверенном аккаунте не
    пишутся."""
    seed_token(label=OLD_LABEL)
    tokens = iter([(ACCESS2, REFRESH2), ("FAKE-olx-access3-0123456789abcdefXY", "FAKE-olx-refresh3-0123456789abcd")])

    def me(request):
        from app import models
        from app.db import SessionLocal

        access, refresh = next(tokens)
        with SessionLocal() as db:  # новое подключение без подтверждения аккаунта(подпись сброшена)
            olx().save_token(db, token_body(access, refresh), models.utcnow())
            db.commit()
        return jresp(200, OLD_ME)

    http_mock.on("GET", ME_URL, handler=me)
    run = run_test(client, run_worker)
    m = olx()
    assert (run["status"], run["errorCode"], run["error"]) == ("failed", "source_error", m.REPLACED), run
    assert m.REPLACED == "Аккаунт OLX переподключён или отключён во время проверки — повторите проверку"
    assert len(http_mock.calls) == 2 and token_row().account_label is None
    c = connector(client)
    assert c["status"] == "error" and "account" not in c["facts"]


def test_callback_replaced_during_account_check(client, olx_env, http_mock):
    """Возврат A ждёт users/me, а за это время подключили другой аккаунт(возврат B): A не перезаписывает подпись,
    сведения и статус B и не пишет в журнал "подключён"."""
    other = {"data": {"id": 777, "name": "Другой", "is_business": False}}
    other_label = "Другой (id 777)"
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body()))

    def me(request):
        reconnect_now(me=other)
        return jresp(200, ME)

    http_mock.on("GET", ME_URL, handler=me)
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 409 and olx().REPLACED_PAGE in r.text and LABEL not in r.text
    assert (token_row().access_token, token_row().account_label) == (ACCESS2, other_label)
    c = connector(client)
    assert (c["status"], c["facts"]["account"]) == ("ready", other_label)
    assert not any(LABEL in t for t in audit_texts())
    # то же со сбоем users/me: статус нового подключения не становится "ошибкой"
    http_mock.on("GET", ME_URL, handler=lambda req: reconnect_now(me=other) or jresp(500, {}))
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502
    c = connector(client)
    assert (c["status"], c["facts"]["account"]) == ("ready", other_label)
    # отключение во время возврата: сведения не воскресают
    def disconnect_then_me(request):
        resp = client.post("/api/integrations/olx/disconnect", json={"author": AUTHOR}, headers=admin())
        assert resp.status_code == 200, resp.text
        return jresp(200, ME)

    http_mock.on("GET", ME_URL, handler=disconnect_then_me)
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 409 and token_row() is None
    c = connector(client)
    assert c["status"] == "unchecked" and "account" not in c["facts"]


def test_callback_account_failed_clears_previous_account(client, olx_env, http_mock):
    """Переподключение, users/me не прошёл: сведения о прежнем аккаунте убираются(токен уже другой)."""
    seed_token(label=OLD_LABEL)
    set_row(status="ready", facts={"account": OLD_LABEL, "isBusiness": False})
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, REFRESH2)))
    http_mock.on("GET", ME_URL, status=500, content=b"oops")
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502
    assert (token_row().access_token, token_row().account_label) == (ACCESS2, None)
    c = connector(client)
    assert c["status"] == "error" and "account" not in c["facts"] and "isBusiness" not in c["facts"]


# ---------- 6б. недостаточно прав ----------

INSUFFICIENT_SCOPE = {"error": "insufficient_scope",
                      "error_description": "The request requires higher privileges than provided by the access token",
                      "error_human_title": "Insufficient scope."}


def test_check_insufficient_scope_is_denied_not_expired(client, olx_env, http_mock, run_worker):
    """Документация OLX: недостаточный scope - HTTP 401 insufficient_scope. Это не истёкший токен: без обновления,
    статус "нет доступа" с подсказкой про OLX_SCOPES, по расписанию не повторяется."""
    seed_token()
    http_mock.on("GET", ME_URL, json=INSUFFICIENT_SCOPE, status=401, headers=HTML)
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, REFRESH2)))
    run = run_test(client, run_worker)
    assert (run["status"], run["errorCode"]) == ("failed", "access_denied"), run
    assert [str(c.url) for c in http_mock.calls] == [ME_URL]  # без обновления токена
    detail = ("У токена OLX недостаточно прав: The request requires higher privileges than provided by the access "
              "token — проверьте OLX_SCOPES (нужны read и v2) и подключите аккаунт заново")
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["statusDetail"]) == ("denied", "нет доступа", detail)
    assert token_row().access_token == ACCESS
    set_row(checked_at=None)
    assert not _scheduled()
    # без описания - без двоеточия
    http_mock.on("GET", ME_URL, json={"error": "insufficient_scope"}, status=401)
    from app.integrations import http

    with http.client(base_url=olx().ORIGIN) as cl, pytest.raises(olx().AccessDenied) as e:
        olx().users_me(cl, ACCESS)
    assert e.value.detail.startswith("У токена OLX недостаточно прав — проверьте OLX_SCOPES")


def test_callback_insufficient_scope(client, olx_env, http_mock):
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body()))
    http_mock.on("GET", ME_URL, json=INSUFFICIENT_SCOPE, status=401)
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502 and "недостаточно прав" in r.text and "проверьте права доступа OLX" in r.text
    assert "OLX_SCOPES" not in r.text
    assert "Доступ OLX истёк" not in r.text
    assert [str(c.url) for c in http_mock.calls] == [TOKEN_URL, ME_URL]
    assert connector(client)["status"] == "denied"


# ---------- 7. плановые проверки ----------

def _scheduled(now=None) -> bool:
    from app import jobs, models, worker
    from app.db import SessionLocal

    with SessionLocal() as db:
        worker.schedule_due(db, models.as_utc(now) or models.utcnow())
        queued = jobs.active(db, "test:olx") is not None
        db.rollback()
    return queued


def test_health_checks_scheduled(client, olx_env):
    from app import models

    now = models.utcnow()
    assert not _scheduled()  # токена нет
    seed_token(expires_in=timedelta(minutes=30))
    set_row(status="ready", checked_at=now - timedelta(hours=2))
    assert _scheduled()  # токен истекает меньше чем через час
    set_row(checked_at=now - timedelta(minutes=5))
    assert not _scheduled()  # только что проверяли - не зацикливаться
    seed_token(expires_in=timedelta(hours=20))
    set_row(checked_at=now - timedelta(hours=25))
    assert _scheduled()  # проверке больше суток
    set_row(checked_at=now - timedelta(hours=1))
    assert not _scheduled()
    set_row(checked_at=now - timedelta(hours=25), status="expired")
    assert not _scheduled()  # истёкший доступ - только повторное подключение
    set_row(status="rate_limited", next_run_at=now + timedelta(minutes=30))
    assert not _scheduled()  # пауза по лимиту частоты
    assert _scheduled(now + timedelta(minutes=31))  # пауза кончилась - проверить снова, не дожидаясь суток
    set_row(checked_at=now - timedelta(minutes=5), next_run_at=now - timedelta(minutes=1))
    assert not _scheduled()  # пауза короче 15 минут - всё равно не чаще раза в 15 минут
    set_row(status="ready")
    seed_token(refresh_in=timedelta(days=-1))
    assert not _scheduled()  # refresh token истёк


def test_health_check_runs_from_worker(client, olx_env, http_mock, run_worker):
    from app import models

    seed_token(expires_in=timedelta(minutes=30))
    set_row(status="ready", checked_at=models.utcnow() - timedelta(hours=26))
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, REFRESH2)))
    http_mock.on("GET", ME_URL, handler=lambda r: jresp(200, ME))
    assert run_worker() == 1
    assert [str(c.url) for c in http_mock.calls] == [TOKEN_URL, ME_URL]
    assert token_row().access_token == ACCESS2
    assert run_worker() == 0  # следующий такт - ничего


# ---------- 8. токены не выходят наружу ----------

def test_no_tokens_in_api_or_logs(client, olx_env, http_mock, run_worker, caplog):
    from app import models
    from app.db import SessionLocal

    caplog.set_level(logging.DEBUG)
    cap = connect(client, http_mock)
    state = cap["state"]
    http_mock.on("GET", ME_URL, json={"error": "invalid_token"}, status=401)
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body(ACCESS2, REFRESH2)))
    run_test(client, run_worker)
    logging.getLogger("saq.test").warning("state=%s code=%s token %s", state, CODE, ACCESS2)
    assert f"OLX: подключён аккаунт {LABEL}" in caplog.text  # журнал действительно перехвачен
    assert "state=*** code=*** token ***" in caplog.text
    secrets = (ACCESS, REFRESH, ACCESS2, REFRESH2, CODE, state, CLIENT_SECRET)
    for text in (client.get("/api/sources/connectors").text, client.get("/api/sources/connectors/olx").text,
                 client.get("/api/collection-runs?source=olx").text, caplog.text, json.dumps(audit_texts())):
        for s in secrets:
            assert s not in text
    with SessionLocal() as db:
        for r in db.scalars(select(models.Collector)):
            dump = json.dumps({"facts": r.facts, "state": r.state, "detail": r.status_detail,
                               "error": r.last_error}, ensure_ascii=False)
            assert not any(s in dump for s in secrets)
        for st in db.scalars(select(models.OAuthState)):
            assert state not in st.state_hash


# ---------- приёмка по §14 плана(§16.5, применимое к OAuth-подключению) ----------

def test_acceptance_failure_not_empty(client, olx_env, http_mock, run_worker, accept):
    """"сбой не выглядит пустой выдачей": ошибка OLX - запуск не "успешно", подключение не "работает", причина видна."""
    seed_token()
    http_mock.on("GET", ME_URL, status=503, content=b"busy")
    run = run_test(client, run_worker)
    c = accept.assert_failure_visible("olx", run["id"])
    assert c["status"] == "error" and "OLX временно недоступен" in (c["statusDetail"] or c["lastError"])


def test_acceptance_oauth_is_not_collection(client, olx_env, http_mock, accept):
    """Успешный OAuth - не сбор чужих объявлений: публикаций не появляется, сбора нет, статус не "работает"."""
    snap = accept.snapshot()
    connect(client, http_mock)
    assert accept.snapshot() == snap
    c = accept.connector("olx")
    assert c["status"] != "working" and c["statusLabel"] == "подключено · ограничено"
    r = client.post("/api/sources/olx/collect", json={"author": AUTHOR}, headers=admin())
    assert (r.status_code, r.json()["detail"]) == (409, "У этого подключения нет сбора публикаций")
    r = client.post("/api/sources/collect-all", json={"author": AUTHOR}, headers=admin())
    assert all(q["code"] != "olx" for q in r.json()["queued"])


def test_acceptance_rate_limit_no_loop(client, olx_env, http_mock, run_worker):
    """"превышение лимита не вызывает бесконечный цикл": 429 - пауза, следующие такты воркера не обращаются к OLX."""
    seed_token()
    http_mock.on("GET", ME_URL, status=429, content=b"slow down", headers={"Retry-After": "900"})
    run = run_test(client, run_worker)
    assert run["status"] == "rate_limited"
    c = connector(client)
    assert c["status"] == "rate_limited" and c["nextRunAt"] is not None
    calls = len(http_mock.calls)
    assert run_worker() == 0 and run_worker() == 0
    assert len(http_mock.calls) == calls == 1


def test_rate_limited_health_check_retried_after_pause(client, olx_env, http_mock, run_worker):
    """429 на плановой проверке(Retry-After 15 мин): до конца паузы не проверять, после - проверка ставится сама, а
    не через сутки; успех снимает паузу."""
    from app import models

    now = models.utcnow()
    seed_token(expires_in=timedelta(hours=20))
    set_row(status="ready", checked_at=now - timedelta(hours=25))
    http_mock.on("GET", ME_URL, status=429, content=b"slow", headers={"Retry-After": "900"})
    assert run_worker(now=now) == 1
    c = connector(client)
    assert c["status"] == "rate_limited" and c["statusDetail"].startswith("Ограничение частоты: повтор после ")
    assert not _scheduled(now + timedelta(minutes=10))
    assert _scheduled(now + timedelta(minutes=16))
    http_mock.on("GET", ME_URL, handler=lambda r: jresp(200, ME))
    assert run_worker(now=now + timedelta(minutes=16)) == 1
    assert connector(client)["status"] == "ready"


@pytest.mark.parametrize(("raw", "want_s"), [(float("nan"), 3600), (1e300, 30 * 86400)])
def test_callback_429_pause_is_bounded(client, olx_env, http_mock, monkeypatch, raw, want_s):
    """Пауза после 429 на users/me ограничена base.pause_seconds, даже если retry_after ошибки не прошёл через
    RateLimited: NaN - час, огромное значение - потолок MAX_PAUSE_S(30 суток), а не OverflowError и статус без итога."""
    from app import models
    from app.integrations import olx

    made = olx._status_error

    def status_error(resp, what):
        e = made(resp, what)
        e.retry_after = raw
        return e

    monkeypatch.setattr(olx, "_status_error", status_error)
    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body()))
    http_mock.on("GET", ME_URL, status=429, content=b"slow", headers={"Retry-After": "60"})
    before = models.utcnow()
    assert callback(client, code=CODE, state=start(client)).status_code == 502
    from app.db import SessionLocal
    from app.integrations import service

    with SessionLocal() as db:
        pause = models.as_utc(service.row(db, "olx").next_run_at) - before
    assert timedelta(seconds=want_s - 60) < pause <= timedelta(seconds=want_s + 60)


def test_callback_429_respects_retry_after(client, olx_env, http_mock):
    """users/me ответил 429 сразу после обмена кода: пауза до Retry-After(как у плановой проверки), без проверки на
    ближайшем такте; после паузы - проверка."""
    from app import models
    from app.integrations.base import local_time

    http_mock.on("POST", TOKEN_URL, handler=lambda r: jresp(200, token_body()))
    http_mock.on("GET", ME_URL, status=429, content=b"slow", headers={"Retry-After": "3600"})
    before = models.utcnow()
    r = callback(client, code=CODE, state=start(client))
    assert r.status_code == 502 and "ограничил частоту" in r.text
    c = connector(client)
    assert c["status"] == "rate_limited" and c["checkedAt"] is not None
    from app.db import SessionLocal
    from app.integrations import service

    with SessionLocal() as db:
        row = service.row(db, "olx")
        pause = models.as_utc(row.next_run_at)
    assert timedelta(minutes=59) < pause - before <= timedelta(minutes=61)
    assert c["statusDetail"] == f"Ограничение частоты: повтор после {local_time(pause)}"
    assert not _scheduled(before + timedelta(minutes=1))
    assert _scheduled(before + timedelta(minutes=62))
