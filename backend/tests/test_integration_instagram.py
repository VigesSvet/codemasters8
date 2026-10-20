"""Instagram через Meta Graph API · Business Discovery(integration-spec §12.1, §16.3 test_integration_instagram,
§16.5 столбец I): проверка токена и разрешений, сбор публикаций с файлами, повтор без дублей, правки, обход страниц,
совместимость полей, ошибки и лимиты Graph API, токен не утекает, плановая проверка; приёмка по §14 плана.

Сеть - только поддельный Graph API и CDN поверх http_mock; модуль подключения импортируется внутри тестов."""
import hashlib
import hmac
import json
import logging
import re
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
from sqlalchemy import func, select

from conftest import mp4_bytes, png_bytes

from app import models
from app.db import SessionLocal

GRAPH = "https://graph.facebook.com/v26.0/"
TOKEN = "EAAfakeUserToken0123456789abcdefXYZ"
APP_ID = "1234567890123"
APP_SECRET = "fake-app-secret-0123456789abcdef"
IG_ID = "17841400000000001"
BUSINESS_ID = "1000000000000001"
PAGE_ID = "1000000000000002"
LEAK = "LEAKYPAGINGTOKEN0123456789"  # токен в ссылке paging.next - по ней никогда не ходят
AUTHOR = "Омарова Ж."
ACCOUNT = "tumar.group.astana"
CDN_RE = re.compile(r"^https://[a-z0-9.-]+\.(?:cdninstagram\.com|fbcdn\.net)/")


@pytest.fixture
def ig():
    from app.integrations import instagram

    return instagram


@pytest.fixture
def configured(env):
    """Токен и ID нашего Instagram на сервере."""
    return env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID)


# ---------- поддельный Graph API ----------

def mid(n: int) -> str:
    return f"1790000000{n:07d}"


def cdn(name: str) -> str:
    return f"https://scontent-ams2-1.cdninstagram.com/v/t51.2885-15/{name}?stp=dst-jpg&oh=SIGNED{name}&oe=6720AB"


def image(n: int, caption: str = "", **kw) -> dict:
    return {"id": mid(n), "caption": caption or f"Публикация {n}: квартиры в рассрочку", "media_type": "IMAGE",
            "media_product_type": "FEED", "media_url": cdn(f"{n}.jpg"),
            "permalink": f"https://www.instagram.com/p/C{n}x/", "timestamp": "2026-10-09T11:10:23+0000", **kw}


def video(n: int, *, with_url: bool = True, caption: str = "") -> dict:
    m = {"id": mid(n), "caption": caption or f"Ролик {n}", "media_type": "VIDEO", "media_product_type": "REELS",
         "thumbnail_url": cdn(f"{n}-thumb.jpg"), "permalink": f"https://www.instagram.com/reel/R{n}x/",
         "timestamp": "2026-10-08T09:00:00+0000"}
    if with_url:
        m["media_url"] = cdn(f"{n}.mp4")
    return m


def carousel(n: int, kids: list, caption: str = "") -> dict:
    return {"id": mid(n), "caption": caption or f"Карусель {n}", "media_type": "CAROUSEL_ALBUM",
            "media_product_type": "FEED", "media_url": cdn(f"{kids[0]}.jpg"),
            "permalink": f"https://www.instagram.com/p/K{n}x/", "timestamp": "2026-10-07T08:00:00+0000",
            "children": {"data": [{"id": mid(k), "media_type": "IMAGE", "media_url": cdn(f"{k}.jpg")} for k in kids]}}


def graph_error(code: int, message: str, *, subcode=None, transient=False, status: int = 400,
                headers=None) -> httpx.Response:
    err = {"message": message, "type": "OAuthException", "code": code, "fbtrace_id": "AbCdEf"}
    if subcode is not None:
        err["error_subcode"] = subcode
    if transient:
        err["is_transient"] = True
    return httpx.Response(status, json={"error": err}, headers=headers)


class Graph:
    """Graph API и CDN Instagram поверх http_mock: разрешения, профиль, debug_token, Business Discovery со страницами
    (курсор CUR<смещение>), ошибки по аккаунту/странице, "неподдерживаемые" поля, заголовки использования лимита.

    Страницы - как у настоящего Business Discovery(docs: "the response will not include previous or next fields"):
    только paging.cursors, без paging.next; курсоры есть, если публикаций больше одной страницы(и на последней такой
    странице тоже), запрос после последнего курсора - пустая data без paging. leak_next=True добавляет ссылку
    paging.next с токеном - по ней ходить нельзя; cursor_override подменяет курсор after."""

    def __init__(self, mock):
        self.mock = mock
        self.perms = ["instagram_basic", "instagram_manage_insights", "pages_read_engagement", "pages_show_list",
                      "business_management"]
        self.username = "saq.monitor.demo"
        self.accounts: dict[str, list] = {}
        self.page_errors: dict = {}  # username → fn(after) -> Optional[httpx.Response]
        self.unsupported: set = set()
        self.usage: dict = {}  # заголовки X-App-Usage / X-Business-Use-Case-Usage ответов Business Discovery
        self.debug: object = {"data": {"app_id": APP_ID, "type": "USER", "is_valid": True,
                                       "expires_at": int((models.utcnow() + timedelta(days=50)).timestamp()),
                                       "data_access_expires_at": int((models.utcnow() + timedelta(days=80))
                                                                     .timestamp()),
                                       "scopes": ["instagram_basic"]}}
        self.cdn_fail: set = set()
        self.leak_next = False
        self.cursor_override: object = None
        self.install()

    def install(self) -> None:
        self.mock.on("GET", GRAPH + "me/permissions", handler=self._perms)
        self.mock.on("GET", GRAPH + IG_ID, handler=self._ig)
        self.mock.on("GET", GRAPH + "debug_token", handler=self._debug)
        self.mock.on("GET", CDN_RE, handler=self._cdn)

    def _perms(self, request):
        return httpx.Response(200, json={"data": [{"permission": p, "status": "granted"} for p in self.perms]
                                         + [{"permission": "ads_read", "status": "declined"}]})

    def _debug(self, request):
        if isinstance(self.debug, httpx.Response):
            return self.debug
        return httpx.Response(200, json=self.debug)

    def _cdn(self, request):
        name = urlsplit(str(request.url)).path.rsplit("/", 1)[-1]
        if name in self.cdn_fail:
            return httpx.Response(500, content=b"")
        if name.endswith(".mp4"):
            return httpx.Response(200, content=mp4_bytes(), headers={"Content-Type": "video/mp4"})
        return httpx.Response(200, content=cdn_png(name), headers={"Content-Type": "image/png"})

    def _ig(self, request):
        fields = request.url.params.get("fields", "")
        if not fields.startswith("business_discovery"):
            return httpx.Response(200, json={"id": IG_ID, "username": self.username})
        for f in sorted(self.unsupported):
            asked = "children{" in fields if f == "children" else (",name," in fields if f == "name" else f in fields)
            if asked:
                return graph_error(100, f"(#100) Tried accessing nonexisting field ({f}) on node type (Media)")
        username = re.match(r"business_discovery\.username\(([^)]*)\)", fields).group(1)
        limit = int(re.search(r"media\.limit\((\d+)\)", fields).group(1))
        m_after = re.search(r"\.after\(([^)]*)\)", fields)
        after = m_after.group(1) if m_after else None
        fn = self.page_errors.get(username)
        if fn is not None:
            resp = fn(after)
            if resp is not None:
                return resp
        if username not in self.accounts:
            return graph_error(110, "Invalid user id", subcode=2207013)
        items = self.accounts[username]
        off = int(after[3:]) if after else 0
        page = [dict(x) for x in items[off:off + limit]]
        if "children{" not in fields:
            for x in page:
                x.pop("children", None)
        media: dict = {"data": page}
        if page and (off > 0 or off + len(page) < len(items)):  # публикаций больше одной страницы
            nxt = f"CUR{off + len(page)}"
            media["paging"] = {"cursors": {"before": f"BEF{off}", "after": self.cursor_override or nxt}}
            if self.leak_next and off + len(page) < len(items):
                media["paging"]["next"] = f"https://graph.facebook.com/v26.0/{IG_ID}?access_token={LEAK}&after={nxt}"
        body = {"business_discovery": {"id": "17841499999999999", "username": username, "name": "Застройщик",
                                       "media_count": len(items), "media": media},
                "id": IG_ID}
        return httpx.Response(200, json=body, headers=self.usage or None)

    # --- запросы, которые видел мок ---

    def graph_calls(self) -> list:
        return [r for r in self.mock.calls if r.url.host == "graph.facebook.com"]

    def bd_calls(self) -> list:
        return [r for r in self.graph_calls() if r.url.params.get("fields", "").startswith("business_discovery")]

    def cdn_calls(self) -> list:
        return [r for r in self.mock.calls if r.url.host != "graph.facebook.com"]


def cdn_png(name: str) -> bytes:
    return png_bytes(color=tuple(hashlib.sha256(name.encode()).digest()[:3]))


@pytest.fixture
def graph(http_mock):
    return Graph(http_mock)


# ---------- помощники ----------

def configure(**values) -> None:
    from app.integrations import service

    with SessionLocal() as db:
        r = service.row(db, "instagram", create=True)
        for k, v in values.items():
            setattr(r, k, v)
        db.commit()


def collector():
    from app.integrations import service

    with SessionLocal() as db:
        return service.row(db, "instagram", create=True)


def queue(trigger: str = "manual", now=None) -> int:
    from app.integrations import service

    with SessionLocal() as db:
        run, _ = service.request_run(db, "instagram", trigger=trigger, who=AUTHOR, now=now)
        db.commit()
        return run.id


def run_json(client, run_id: int) -> dict:
    r = client.get(f"/api/collection-runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


def connector(client) -> dict:
    r = client.get("/api/sources/connectors/instagram")
    assert r.status_code == 200, r.text
    return r.json()


def collect(client, run_worker, **kw) -> dict:
    """Сбор через воркер(с заданиями анализа) → RunOut запуска."""
    rid = queue(now=kw.get("now"))
    run_worker(**kw)
    return run_json(client, rid)


def check(client, run_worker) -> dict:
    rid = queue("test")
    run_worker()
    return run_json(client, rid)


def assets(external_id: str) -> dict:
    with SessionLocal() as db:
        item = db.scalar(select(models.IngestItem).where(models.IngestItem.platform == "instagram",
                                                         models.IngestItem.external_id == external_id))
        assert item is not None, external_id
        rows = db.scalars(select(models.MediaAsset).where(models.MediaAsset.item_id == item.id,
                                                          models.MediaAsset.parent_id.is_(None))).all()
        return {a.source_key: a for a in rows}


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def ad_of(external_id: str) -> models.Ad:
    with SessionLocal() as db:
        item = db.scalar(select(models.IngestItem).where(models.IngestItem.external_id == external_id))
        return db.get(models.Ad, item.ad_id)


def fields_of(request) -> str:
    return request.url.params.get("fields", "")


# ---------- 1. не настроен ----------

def test_not_configured_status_and_smoke(client):
    from app.integrations import smoke

    c = connector(client)
    assert (c["status"], c["statusLabel"], c["missing"], c["statusDetail"]) == (
        "not_configured", "не настроен", ["META_USER_ACCESS_TOKEN"], "Нет на сервере: META_USER_ACCESS_TOKEN")
    assert (c["kind"], c["group"], c["via"], c["configFields"], c["intervalMin"], c["intervalBounds"]) == (
        "collector", "collectors", "Meta Graph API · Business Discovery", ["accounts"], 60, [30, 1440])
    # проверка из командной строки - без сети(сеть в тестах падает)
    assert smoke.check_one("instagram") == "instagram: не настроен — нет META_USER_ACCESS_TOKEN"


def test_account_normalization(client, ig, admin_headers):
    assert ig.normalize_account("@Tumar.Group.Astana") == ACCOUNT
    assert ig.normalize_account("https://www.instagram.com/tumar.group.astana/?hl=ru") == ACCOUNT
    assert ig.normalize_account("instagram.com/tumar.group.astana/reels/") == ACCOUNT
    for bad in ("https://www.instagram.com/p/C1x/", "https://evil.example/tumar", "@a b", "x" * 31, ""):
        with pytest.raises(ValueError):
            ig.normalize_account(bad)
    r = client.patch("/api/sources/instagram", headers=admin_headers,
                     json={"author": AUTHOR, "config": {"accounts": ["https://instagram.com/Tumar.Group.Astana/",
                                                                     "@tumar.group.astana", "altai.build"]}})
    assert r.status_code == 200, r.text
    assert r.json()["config"] == {"accounts": ["@tumar.group.astana", "@altai.build"]}
    r = client.patch("/api/sources/instagram", headers=admin_headers,
                     json={"author": AUTHOR, "config": {"accounts": ["https://www.instagram.com/p/C1x/"]}})
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"] == ["body", "config", "accounts"] and err["msg"].startswith("Неверный аккаунт Instagram")


# ---------- 2-5. проверка подключения ----------

def test_check_ok_bearer_only(client, configured, graph, run_worker):
    run = check(client, run_worker)
    assert run["status"] == "succeeded", run
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("ready", "готов к сбору")
    assert c["statusDetail"].startswith("Подключено: @saq.monitor.demo. Токен действует до ")
    facts = c["facts"]
    assert (facts["account"], facts["igUserId"]) == ("@saq.monitor.demo", IG_ID)
    assert "instagram_manage_insights" in facts["scopes"] and "ads_read" not in facts["scopes"]
    assert facts["expiresAt"] and facts["dataAccessExpiresAt"]
    calls = graph.graph_calls()
    assert {urlsplit(str(r.url)).path for r in calls} == {"/v26.0/me/permissions", f"/v26.0/{IG_ID}",
                                                          "/v26.0/debug_token"}
    for r in calls:
        assert r.headers["Authorization"] == f"Bearer {TOKEN}"
        assert "access_token" not in str(r.url) and "appsecret_proof" not in str(r.url)
    # токен - только в заголовке; debug_token требует input_token в адресе - единственное исключение
    assert [r for r in calls if TOKEN in str(r.url)] == [r for r in calls if r.url.path.endswith("debug_token")]
    assert TOKEN not in json.dumps(client.get("/api/sources/connectors").json())


def test_check_appsecret_proof_masked(client, env, graph, run_worker, caplog):
    caplog.set_level(logging.INFO)
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, META_APP_SECRET=APP_SECRET)
    assert check(client, run_worker)["status"] == "succeeded"
    proof = hmac.new(APP_SECRET.encode(), TOKEN.encode(), hashlib.sha256).hexdigest()
    user_calls = [r for r in graph.graph_calls() if not r.url.path.endswith("debug_token")]
    assert user_calls and all(r.url.params["appsecret_proof"] == proof for r in user_calls)
    # appsecret_proof зарегистрирован как секрет: в журнале - "***"
    logging.getLogger("app.integrations.instagram").warning("запрос %s", f"{GRAPH}me?appsecret_proof={proof}")
    logging.getLogger("app.integrations.instagram").warning(f"значение {proof}")
    assert proof not in caplog.text and TOKEN not in caplog.text and APP_SECRET not in caplog.text
    assert "***" in caplog.text


def test_check_missing_permission_denied(client, configured, graph, run_worker):
    graph.perms.remove("instagram_manage_insights")
    run = check(client, run_worker)
    assert (run["status"], run["errorCode"]) == ("failed", "access_denied")
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("denied", "нет доступа")
    assert c["statusDetail"] == ("Нет разрешений: instagram_manage_insights. Если роль на Странице выдана через "
                                 "Business Manager, нужны также ads_read или ads_management")
    assert not graph.bd_calls()


def test_owned_pages_fallback(client, env, http_mock, graph, run_ctx, ig):
    env(META_USER_ACCESS_TOKEN=TOKEN, META_BUSINESS_ID=BUSINESS_ID, META_PAGE_ID=PAGE_ID)
    http_mock.on("GET", GRAPH + "me/accounts", json={"data": []})
    http_mock.on("GET", GRAPH + f"{BUSINESS_ID}/owned_pages", json={"data": [
        {"id": "999", "name": "Другая", "instagram_business_account": {"id": "17841400000000777"}},
        {"id": PAGE_ID, "name": "SAQ Qurylys", "instagram_business_account": {"id": IG_ID}}]})
    ctx = run_ctx("instagram", trigger="test")
    res = ig.CONNECTOR.check(ctx)
    assert res.ok and res.facts["igUserId"] == IG_ID and res.facts["account"] == "@saq.monitor.demo"
    paths = [r.url.path for r in graph.graph_calls()]
    assert paths.index("/v26.0/me/accounts") < paths.index(f"/v26.0/{BUSINESS_ID}/owned_pages")
    assert collector().state["igUserId"] == IG_ID
    # ни Страниц токена, ни бизнес-портфолио - ошибка настроек
    env(META_BUSINESS_ID=None)
    with pytest.raises(ig.BadConfig) as e:
        ig.CONNECTOR.check(run_ctx("instagram", trigger="test"))
    assert e.value.detail == "Не найден профессиональный Instagram, связанный со Страницей"


def test_token_expiry_and_short_token(client, env, graph, run_worker, run_ctx, ig, admin_headers):
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID)
    soon = models.utcnow() + timedelta(days=3)
    graph.debug["data"]["expires_at"] = int(soon.timestamp())
    res = ig.CONNECTOR.check(run_ctx("instagram", trigger="test"))
    when = ig.local_time(soon, "%d.%m.%Y")
    assert res.detail.startswith(f"Подключено: @saq.monitor.demo. Токен истекает {when} — продлите его "
                                 "(см. docs/INTEGRATIONS.md)")
    assert res.facts["expiresAt"] == models.iso(soon.replace(microsecond=0))
    debug = [r for r in graph.graph_calls() if r.url.path.endswith("debug_token")][-1]
    assert debug.headers["Authorization"] == f"Bearer {TOKEN}" and debug.url.params["input_token"] == TOKEN
    # с App ID и App Secret - токеном приложения(и appsecret_proof к нему)
    env(META_APP_ID=APP_ID, META_APP_SECRET=APP_SECRET)
    ig.CONNECTOR.check(run_ctx("instagram", trigger="test"))
    debug = [r for r in graph.graph_calls() if r.url.path.endswith("debug_token")][-1]
    app_token = f"{APP_ID}|{APP_SECRET}"
    assert debug.headers["Authorization"] == f"Bearer {app_token}"
    assert debug.url.params["appsecret_proof"] == hmac.new(APP_SECRET.encode(), app_token.encode(),
                                                           hashlib.sha256).hexdigest()
    # бессрочный токен; ошибка debug_token - "не проверен", но подключение работает; is_valid=false - истёк
    graph.debug["data"]["expires_at"] = 0
    res = ig.CONNECTOR.check(run_ctx("instagram", trigger="test"))
    assert res.facts["expiresAt"] is None and "Токен без срока действия" in res.detail
    graph.debug = graph_error(190, "Invalid OAuth access token - Cannot parse access token")
    res = ig.CONNECTOR.check(run_ctx("instagram", trigger="test"))
    assert res.ok and res.detail == "Подключено: @saq.monitor.demo. Срок действия токена не проверен"
    graph.debug = {"data": {"is_valid": False, "expires_at": 0}}
    with pytest.raises(ig.AuthExpired):
        ig.CONNECTOR.check(run_ctx("instagram", trigger="test"))
    # краткосрочный токен(истекает через 2 ч) - сбор не включается
    env(META_APP_ID=None, META_APP_SECRET=None)
    graph.debug = {"data": {"is_valid": True, "expires_at": int((models.utcnow() + timedelta(hours=2)).timestamp())}}
    assert check(client, run_worker)["status"] == "succeeded"
    r = client.patch("/api/sources/instagram", headers=admin_headers, json={"author": AUTHOR, "enabled": True})
    assert (r.status_code, r.json()["detail"]) == (409, "Токен Meta краткосрочный — получите долгосрочный (60 дней) "
                                                        "или токен системного пользователя")
    graph.debug = {"data": {"is_valid": True, "expires_at": int((models.utcnow() + timedelta(days=59)).timestamp())}}
    assert check(client, run_worker)["status"] == "succeeded"
    r = client.patch("/api/sources/instagram", headers=admin_headers, json={"author": AUTHOR, "enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True


# ---------- 6-8. сбор, повтор, правка ----------

def three_items(graph) -> list:
    items = [image(1, "Старт продаж, рассрочка 0%"), video(2, with_url=False), carousel(3, [31, 32])]
    graph.accounts[ACCOUNT] = items
    configure(config={"accounts": [f"@{ACCOUNT}"]})
    return items


def test_collect_media_kinds(client, configured, graph, run_worker, ocr_stub):
    three_items(graph)
    run = collect(client, run_worker)
    assert run["status"] == "partial", run  # видео без файла - "файлов не загружено: 1"
    assert run["counts"] == {"seen": 3, "new": 3, "updated": 0, "unchanged": 0, "failed": 0, "mediaSaved": 4,
                             "mediaFailed": 1}
    assert count(models.Ad) and count(models.IngestItem) == 3
    ad = ad_of(mid(1))
    assert (ad.platform, ad.account, ad.url, ad.origin, ad.source, ad.status) == (
        "instagram", f"@{ACCOUNT}", "https://www.instagram.com/p/C1x/", "collected", "parser", "new")
    assert ad.date == "2026-10-09T16:10"  # время публикации - по Усть-Каменогорску
    a = assets(mid(1))
    assert (a[f"ig:{mid(1)}"].status, a[f"ig:{mid(1)}"].kind) == ("downloaded", "image")
    assert a[f"ig:{mid(1)}"].origin_url == "https://scontent-ams2-1.cdninstagram.com/v/t51.2885-15/1.jpg"
    v = assets(mid(2))
    assert (v[f"ig:{mid(2)}"].status, v[f"ig:{mid(2)}"].kind, v[f"ig:{mid(2)}"].error) == (
        "unavailable", "video", "Instagram не выдал файл (возможны авторские права) — сохранена обложка")
    assert (v[f"ig:{mid(2)}:cover"].status, v[f"ig:{mid(2)}:cover"].role) == ("downloaded", "cover")
    assert ad_of(mid(2)).image == f"/api/media/{v[f'ig:{mid(2)}:cover'].id}"
    k = assets(mid(3))
    assert sorted(k) == [f"ig:{mid(31)}", f"ig:{mid(32)}"]
    assert all((x.status, x.role) == ("downloaded", "slide") for x in k.values())
    # файлы - только с CDN Instagram/Facebook, без токена
    cdn_calls = graph.cdn_calls()
    assert len(cdn_calls) == 4 and all(CDN_RE.match(str(r.url)) for r in cdn_calls)
    assert all("Authorization" not in r.headers for r in cdn_calls)
    item = client.get(f"/api/ads/{ad.id}").json()
    assert item["media"][0]["url"] == f"/api/media/{a[f'ig:{mid(1)}'].id}"
    # снимок ответа источника без подписанных query у ссылок
    with SessionLocal() as db:
        raw = db.scalar(select(models.IngestItem.raw).where(models.IngestItem.external_id == mid(1)))
    assert raw["media_url"] == "https://scontent-ams2-1.cdninstagram.com/v/t51.2885-15/1.jpg"


def test_cdn_host_not_allowed(client, configured, graph, run_worker, ocr_stub):
    graph.accounts[ACCOUNT] = [image(5, media_url="https://evil.example/5.jpg")]
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    assert run["counts"]["new"] == 1 and run["counts"]["mediaFailed"] == 1
    a = assets(mid(5))[f"ig:{mid(5)}"]
    assert a.status == "failed" and "evil.example" in (a.error or "")
    assert not graph.cdn_calls()  # к чужому хосту запроса не было


def test_carousel_without_children(client, configured, graph, run_worker, ocr_stub):
    graph.accounts[ACCOUNT] = [carousel(4, [41, 42])]
    configure(config={"accounts": [ACCOUNT]}, state={"compat": {"children": False}})
    run = collect(client, run_worker)
    assert run["status"] == "partial"
    assert "children{" not in fields_of(graph.bd_calls()[0])
    a = assets(mid(4))
    assert (a[f"ig:{mid(4)}"].status, a[f"ig:{mid(4)}"].role) == ("downloaded", "slide")
    assert (a[f"ig:{mid(4)}:children"].status, a[f"ig:{mid(4)}:children"].error) == (
        "unavailable", "Слайды карусели недоступны через Business Discovery — сохранено только первое изображение")
    assert any(n["text"] == "Слайды карусели недоступны через Business Discovery — сохранено только первое "
                            "изображение" for n in run["notes"])
    assert "Слайды карусели недоступны" in connector(client)["statusDetail"]


def test_rerun_unchanged_and_failed_image_retried(client, configured, graph, run_worker, ocr_stub):
    items = three_items(graph)
    graph.accounts[ACCOUNT] = items + [image(6)]
    graph.cdn_fail.add("6.jpg")
    first = collect(client, run_worker)
    assert first["counts"]["new"] == 4 and assets(mid(6))[f"ig:{mid(6)}"].status == "failed"
    graph.cdn_fail.clear()
    graph.mock.calls.clear()
    second = collect(client, run_worker)
    assert second["counts"] == {"seen": 4, "new": 0, "updated": 0, "unchanged": 4, "failed": 0, "mediaSaved": 1,
                                "mediaFailed": 0}
    assert second["status"] == "succeeded", second  # отметка "недоступен" не считается заново
    assert [urlsplit(str(r.url)).path.rsplit("/", 1)[-1] for r in graph.cdn_calls()] == ["6.jpg"]
    assert assets(mid(6))[f"ig:{mid(6)}"].status == "downloaded"
    assert ad_of(mid(6)).image is not None
    graph.mock.calls.clear()
    third = collect(client, run_worker)
    assert third["counts"]["unchanged"] == 4 and not graph.cdn_calls()
    assert count(models.IngestItem) == 4 and count(models.IngestVersion) == 4


def test_caption_edited_new_version(client, configured, graph, run_worker, ocr_stub):
    items = three_items(graph)
    collect(client, run_worker)
    items[0]["caption"] = "Старт продаж, рассрочка 0% до 2028 года"
    run = collect(client, run_worker)
    assert (run["counts"]["updated"], run["counts"]["unchanged"]) == (1, 2)
    ad = ad_of(mid(1))
    assert ad.text == "Старт продаж, рассрочка 0% до 2028 года"
    with SessionLocal() as db:
        item = db.scalar(select(models.IngestItem).where(models.IngestItem.external_id == mid(1)))
        assert item.version == 2
    versions = client.get(f"/api/ads/{ad.id}/versions").json()
    assert [v["text"] for v in versions] == ["Старт продаж, рассрочка 0% до 2028 года", "Старт продаж, рассрочка 0%"]


# ---------- 9. обход страниц ----------

def test_paging_first_load_cap_and_seeded(client, env, graph, run_worker, ocr_stub):
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, IG_PAGE_SIZE="2", IG_FIRST_LOAD_MAX="5")
    graph.accounts[ACCOUNT] = [image(n) for n in range(10, 17)]  # 7 публикаций, новые первыми
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    pages = [fields_of(r) for r in graph.bd_calls()]
    assert len(pages) == 3
    assert "media.limit(2){" in pages[0] and "media.limit(2).after(CUR2){" in pages[1]
    assert "media.limit(1).after(CUR4){" in pages[2]
    assert run["counts"]["new"] == 5 and run["status"] == "partial"
    assert f"@{ACCOUNT}: новых публикаций больше 5 — старые могли быть пропущены" in [n["text"] for n in run["notes"]]
    assert collector().state["accounts"][ACCOUNT]["seeded"] is True
    # следующий запуск: на первой странице есть уже известная - дальше не идём
    graph.accounts[ACCOUNT].insert(0, image(9))
    graph.mock.calls.clear()
    run = collect(client, run_worker)
    assert len(graph.bd_calls()) == 1 and (run["counts"]["new"], run["counts"]["unchanged"]) == (1, 1)
    assert run["status"] == "succeeded"


def test_paging_without_next_walks_all_pages(client, env, graph, run_worker, ocr_stub):
    """Business Discovery не присылает paging.next: страницы - по курсору after до пустой страницы; новые публикации
    дальше первой страницы не теряются(ревью C1: обход останавливался на первой странице)."""
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, IG_PAGE_SIZE="2", IG_FIRST_LOAD_MAX="50")
    graph.accounts[ACCOUNT] = [image(n) for n in range(140, 147)]  # 7 публикаций - 4 страницы
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    assert (run["status"], run["counts"]["new"]) == ("succeeded", 7), run
    afters = [re.search(r"media\.limit\(2\)(?:\.after\(([^)]*)\))?\{", fields_of(r)).group(1)
              for r in graph.bd_calls()]
    assert afters == [None, "CUR2", "CUR4", "CUR6", "CUR7"]  # последний запрос - пустая страница
    assert not any("старые могли быть пропущены" in n["text"] for n in run["notes"])
    assert collector().state["accounts"][ACCOUNT]["seeded"] is True
    # между запусками - три новые публикации, больше одной страницы: вторая страница тоже запрашивается
    graph.accounts[ACCOUNT][:0] = [image(n) for n in range(150, 153)]
    graph.mock.calls.clear()
    run = collect(client, run_worker)
    assert (run["status"], run["counts"]["new"], run["counts"]["unchanged"]) == ("succeeded", 3, 1), run
    assert len(graph.bd_calls()) == 2
    with SessionLocal() as db:
        ids = sorted(db.scalars(select(models.IngestItem.external_id)).all())
    assert ids == sorted(mid(n) for n in [*range(140, 147), *range(150, 153)])


def test_odd_cursor_partial_not_seeded(client, env, graph, run_worker, ocr_stub):
    """Курсор в неожиданном формате(в fields не подставляется) - "частично", аккаунт не отмечен "загружен": следующий
    запуск снова идёт вглубь; если следующая страница не нужна, курсор не важен."""
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, IG_PAGE_SIZE="2")
    graph.accounts[ACCOUNT] = [image(n) for n in range(160, 165)]
    graph.cursor_override = "QVFI~odd){id}"
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    assert (run["status"], run["counts"]["new"]) == ("partial", 2), run
    assert len(graph.bd_calls()) == 1 and not any("odd" in fields_of(r) for r in graph.bd_calls())
    reason = (f"@{ACCOUNT}: курсор следующей страницы в неожиданном формате — обход остановлен, более старые "
              "публикации не получены")
    assert reason in [n["text"] for n in run["notes"]]
    assert not (collector().state.get("accounts") or {}).get(ACCOUNT, {}).get("seeded")
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("partial", "работает частично") and reason in c["statusDetail"]
    # курсор снова в порядке - аккаунт пройден вглубь до конца
    graph.cursor_override = None
    run = collect(client, run_worker)
    assert (run["status"], run["counts"]["new"], run["counts"]["unchanged"]) == ("succeeded", 3, 2), run
    assert collector().state["accounts"][ACCOUNT]["seeded"] is True
    # загруженный аккаунт: на первой странице есть известная - следующая не нужна, странный курсор не мешает
    graph.accounts[ACCOUNT].insert(0, image(159))
    graph.cursor_override = "QVFI~odd){id}"
    run = collect(client, run_worker)
    assert (run["status"], run["counts"]["new"], run["counts"]["unchanged"]) == ("succeeded", 1, 1), run


def test_paging_rate_limited_mid_walk_not_seeded(client, env, graph, run_worker, ocr_stub):
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, IG_PAGE_SIZE="2")
    graph.accounts[ACCOUNT] = [image(n) for n in range(20, 25)]
    graph.page_errors[ACCOUNT] = lambda after: graph_error(4, "Application request limit reached") \
        if after == "CUR2" else None
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    assert (run["status"], run["errorCode"], run["counts"]["new"]) == ("rate_limited", "rate_limited", 2)
    assert not (collector().state.get("accounts") or {}).get(ACCOUNT, {}).get("seeded")
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("rate_limited", "пауза: лимит запросов")


# ---------- 10. совместимость полей ----------

def test_children_unsupported_retried_without(client, configured, graph, run_worker, ocr_stub):
    graph.accounts[ACCOUNT] = [image(30), carousel(31, [311, 312])]
    graph.unsupported.add("children")
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    calls = graph.bd_calls()
    assert len(calls) == 2 and "children{" in fields_of(calls[0]) and "children{" not in fields_of(calls[1])
    assert run["counts"]["new"] == 2
    assert "Поле children недоступно в Business Discovery — без него" in [n["text"] for n in run["notes"]]
    assert collector().state["compat"] == {"children": False}
    assert assets(mid(31))[f"ig:{mid(31)}:children"].status == "unavailable"
    graph.mock.calls.clear()
    collect(client, run_worker)
    assert [("children{" in fields_of(r)) for r in graph.bd_calls()] == [False]


# ---------- 11. ошибки и лимиты ----------

def test_token_expired_not_rescheduled(client, configured, graph, run_worker, ocr_stub):
    now = models.utcnow()
    graph.page_errors[ACCOUNT] = lambda after: graph_error(190, "Error validating access token: Session has expired",
                                                           subcode=463)
    configure(config={"accounts": [ACCOUNT]}, enabled=True, status="ready", checked_at=now)
    run_worker(now=now)  # расписание: сбор пора запускать
    with SessionLocal() as db:
        runs = db.scalars(select(models.CollectionRun).where(models.CollectionRun.collector == "instagram")).all()
        assert [(r.trigger, r.status, r.error_code) for r in runs] == [("schedule", "failed", "auth_expired")]
        what = db.scalars(select(models.AuditEvent.what).where(models.AuditEvent.what.like("Источник «Instagram»%")))\
            .all()
    assert what == ["Источник «Instagram»: истёк доступ. Токен Meta недействителен или истёк — нужна повторная "
                    "авторизация"]
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("expired", "истёк доступ")
    graph.mock.calls.clear()
    run_worker(now=now + timedelta(hours=3))
    assert not graph.graph_calls()  # истёкший доступ не планируется
    assert count(models.CollectionRun) == 1


def test_rate_limit_code_4_uses_regain_time(client, configured, graph, run_worker, ocr_stub):
    graph.accounts[ACCOUNT] = [image(40)]
    buc = json.dumps({BUSINESS_ID: [{"type": "instagram", "call_count": 100, "total_cputime": 20, "total_time": 30,
                                     "estimated_time_to_regain_access": 15}]})
    graph.page_errors[ACCOUNT] = lambda after: graph_error(4, "Application request limit reached",
                                                           headers={"X-Business-Use-Case-Usage": buc})
    configure(config={"accounts": [ACCOUNT]})
    now = models.utcnow()
    run = collect(client, run_worker, now=now)
    assert (run["status"], run["statusLabel"]) == ("rate_limited", "пауза: лимит запросов")
    assert run["error"] == "Meta ограничила частоту запросов (код 4) — пауза 15 мин"
    nxt = models.as_utc(collector().next_run_at)
    assert abs((nxt - now).total_seconds() - 900) < 5


def test_usage_over_80_stops_and_postpones(client, configured, graph, run_worker, ocr_stub):
    graph.accounts[ACCOUNT] = [image(50), image(51)]
    graph.accounts["altai.build"] = [image(52)]
    graph.usage = {"X-App-Usage": json.dumps({"call_count": 90, "total_cputime": 10, "total_time": 12})}
    configure(config={"accounts": [ACCOUNT, "altai.build"]}, interval_min=30)
    now = models.utcnow()
    run = collect(client, run_worker, now=now)
    assert run["status"] == "partial" and run["counts"]["new"] == 2  # полученная страница принята
    assert len(graph.bd_calls()) == 1  # второй аккаунт не запрашивался
    texts = [n["text"] for n in run["notes"]]
    assert "Пауза: использовано 90% лимита Graph API" in texts
    assert "Использовано 90% лимита Graph API — запросы остановлены до следующего запуска" in texts
    nxt = models.as_utc(collector().next_run_at)
    assert abs((nxt - now).total_seconds() - 3600) < 5  # не через 30 мин по расписанию, а через час


def test_usage_pause_not_shorter_than_interval(client, configured, graph, run_worker, ocr_stub):
    """Пауза по лимиту Graph API не делает следующий запуск раньше расписания(интервал сутки - не через час)."""
    graph.accounts[ACCOUNT] = [image(53)]
    graph.usage = {"X-App-Usage": json.dumps({"call_count": 90})}
    configure(config={"accounts": [ACCOUNT]}, interval_min=1440)
    now = models.utcnow()
    run = collect(client, run_worker, now=now)
    assert run["status"] == "partial"
    nxt = models.as_utc(collector().next_run_at)
    assert (nxt - now).total_seconds() >= 1440 * 60 - 5


# ---------- 12. аккаунты, которых нет ----------

def test_missing_accounts_partial_then_error(client, configured, graph, run_worker, ocr_stub):
    graph.accounts[ACCOUNT] = [image(60)]
    configure(config={"accounts": [ACCOUNT, "nope.kz"]})
    run = collect(client, run_worker)
    assert run["status"] == "partial" and (run["counts"]["new"], run["counts"]["failed"]) == (1, 1)
    assert "@nope.kz: аккаунт не найден или не профессиональный" in [n["text"] for n in run["notes"]]
    configure(config={"accounts": ["nope.kz", "ghost.kz"]})
    run = collect(client, run_worker)
    assert (run["status"], run["errorCode"]) == ("failed", "bad_config")
    assert run["error"] == "Ни один аккаунт из списка не доступен: @nope.kz, @ghost.kz"
    c = connector(client)
    assert (c["status"], c["stopped"]) == ("error", True)
    configure(config={"accounts": []})
    run = collect(client, run_worker)
    assert run["error"] == "Список аккаунтов пуст — добавьте аккаунты в настройках"


# ---------- 13. токен не утекает ----------

def test_no_token_in_urls_logs_or_notes(client, env, graph, run_worker, run_ctx, ig, ocr_stub, caplog):
    from app.integrations import http
    from app.settings import get_settings

    caplog.set_level(logging.INFO)
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, IG_PAGE_SIZE="5")
    with ig._client(run_ctx("instagram"), get_settings(), TOKEN) as c:
        # ссылки вида paging.next не открываются; абсолютный адрес чужого хоста клиент не отправляет
        with pytest.raises(ig.SourceError):
            ig.graph_get(c, f"{GRAPH}{IG_ID}?access_token={LEAK}&after=CUR2", {}, get_settings())
        with pytest.raises(http.DownloadError) as e:
            c.get(cdn("1.jpg"))
        assert e.value.code == "host_not_allowed"
    assert graph.mock.calls == []
    graph.accounts[ACCOUNT] = [image(n) for n in range(70, 80)]
    graph.leak_next = True  # ответ со ссылкой paging.next с токеном: следующая страница - только курсором
    configure(config={"accounts": [ACCOUNT]})
    graph.page_errors[ACCOUNT] = lambda after: graph_error(1, f"An unknown error occurred (token {TOKEN})",
                                                           status=500) if after else None
    run = collect(client, run_worker)
    assert run["status"] == "running" and run["counts"]["new"] == 5  # временная ошибка второй страницы - повтор
    assert any(n["text"].startswith("Временная ошибка Graph API (код 1): An unknown error occurred (token ***)")
               for n in run["notes"]), run["notes"]
    assert len(graph.bd_calls()) == 2 and ".after(CUR5){" in fields_of(graph.bd_calls()[1])
    assert not [r for r in graph.mock.calls if LEAK in str(r.url) or "access_token" in str(r.url)]
    blob = json.dumps(run) + caplog.text + json.dumps(client.get("/api/sources/connectors").json())
    assert TOKEN not in blob and LEAK not in blob
    with SessionLocal() as db:
        raws = db.scalars(select(models.IngestItem.raw)).all()
    assert "oh=SIGNED" not in json.dumps(raws)


# ---------- 14. плановая проверка ----------

def test_daily_health_check_scheduled(client, configured, graph, run_worker, ocr_stub):
    now = models.utcnow()
    configure(config={"accounts": [ACCOUNT]}, enabled=True, status="working", checked_at=now - timedelta(hours=25),
              next_run_at=now + timedelta(hours=1), last_success_at=now - timedelta(minutes=10))
    run_worker(now=now)
    with SessionLocal() as db:
        runs = db.scalars(select(models.CollectionRun).where(models.CollectionRun.collector == "instagram")).all()
    assert [(r.trigger, r.status) for r in runs] == [("test", "succeeded")]
    r = collector()
    assert models.as_utc(r.checked_at) >= now - timedelta(seconds=5) and r.status == "working"
    assert not graph.bd_calls()
    run_worker(now=now + timedelta(minutes=30))
    assert count(models.CollectionRun) == 1  # следующая - через сутки


# ---------- приёмка по §14 плана(integration-spec §16.5, столбец I) ----------

def test_acceptance_repeat_no_duplicate(client, configured, graph, run_worker, accept, ocr_stub):
    three_items(graph)
    collect(client, run_worker)
    before = accept.snapshot()
    graph.mock.calls.clear()
    run = collect(client, run_worker)
    assert accept.snapshot() == before and run["counts"]["unchanged"] == 3
    assert not graph.cdn_calls()


def test_acceptance_failure_not_empty(client, configured, graph, run_worker, accept, ocr_stub):
    graph.page_errors[ACCOUNT] = lambda after: graph_error(200, "(#200) Requires instagram_basic permission")
    configure(config={"accounts": [ACCOUNT]})
    run = collect(client, run_worker)
    c = accept.assert_failure_visible("instagram", run["id"])
    assert (c["status"], c["statusDetail"]) == ("denied", "Нет разрешения Meta: (#200) Requires instagram_basic "
                                                          "permission")
    # ответ без конверта ошибки(HTTP 502) - тоже не "нет новых": повтор, затем ошибка
    graph.page_errors[ACCOUNT] = lambda after: httpx.Response(502, content=b"<html>Bad Gateway</html>")
    run = collect(client, run_worker)
    assert run["status"] == "running"  # повтор задания позже
    assert any("Graph API: HTTP 502" in n["text"] for n in run["notes"])
    for i in range(1, 7):
        run_worker(now=models.utcnow() + timedelta(hours=2 * i))
    run = run_json(client, run["id"])
    assert run["status"] == "failed"
    accept.assert_failure_visible("instagram", run["id"])


def test_acceptance_restart_keeps_position(client, env, graph, run_ctx, ig, accept, monkeypatch):
    env(META_USER_ACCESS_TOKEN=TOKEN, META_IG_USER_ID=IG_ID, IG_PAGE_SIZE="2")
    graph.accounts[ACCOUNT] = [image(n) for n in range(80, 84)]
    configure(config={"accounts": [ACCOUNT]})
    ig.CONNECTOR.collect(run_ctx("instagram"))
    assert collector().state["accounts"][ACCOUNT]["seeded"] is True
    # пять новых сверху; вторая страница падает - отметка "загружен" уже снята
    graph.accounts[ACCOUNT][:0] = [image(n) for n in range(90, 95)]
    graph.page_errors[ACCOUNT] = lambda after: graph_error(2, "Service temporarily unavailable", transient=True,
                                                           status=503) if after == "CUR2" else None
    with pytest.raises(ig.SourceError) as e:
        ig.CONNECTOR.collect(run_ctx("instagram"))
    assert e.value.retryable
    assert collector().state["accounts"][ACCOUNT]["seeded"] is False
    graph.page_errors.clear()
    ig.CONNECTOR.collect(run_ctx("instagram"))  # перезапуск: обход вглубь, без пропусков и дублей
    with SessionLocal() as db:
        ids = sorted(db.scalars(select(models.IngestItem.external_id)).all())
    assert ids == sorted(mid(n) for n in [*range(80, 84), *range(90, 95)])
    assert collector().state["accounts"][ACCOUNT]["seeded"] is True
    # сбой приёма после первой публикации новой страницы - повтор досбирает остальное
    graph.accounts[ACCOUNT][:0] = [image(n) for n in range(100, 103)]
    from app.integrations.context import RunContext

    real, calls = RunContext.ingest, {"n": 0}

    def flaky(self, pub, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("воркер остановлен")
        return real(self, pub, **kw)

    monkeypatch.setattr(RunContext, "ingest", flaky)
    with pytest.raises(RuntimeError):
        ig.CONNECTOR.collect(run_ctx("instagram"))
    monkeypatch.setattr(RunContext, "ingest", real)
    ig.CONNECTOR.collect(run_ctx("instagram"))
    with SessionLocal() as db:
        ids = db.scalars(select(models.IngestItem.external_id)).all()
    assert len(ids) == len(set(ids)) == 12
    assert accept.snapshot()["ads"] >= 12


def test_acceptance_media_survives_source(client, configured, graph, run_worker, accept, ocr_stub):
    three_items(graph)
    collect(client, run_worker)
    asset = assets(mid(1))[f"ig:{mid(1)}"].id
    graph.mock.clear()  # ссылки CDN Instagram больше не работают
    assert accept.media_ok(asset) == cdn_png("1.jpg")
    video_cover = assets(mid(2))[f"ig:{mid(2)}:cover"].id
    assert accept.media_ok(video_cover) == cdn_png("2-thumb.jpg")


def test_acceptance_not_confirmed_automatically(client, configured, graph, run_worker, accept, ocr_stub):
    graph.accounts[ACCOUNT] = [image(110, "Старт продаж ЖК Ульба Ривер, бронь")]
    configure(config={"accounts": [ACCOUNT]})
    collect(client, run_worker)
    ad_id = ad_of(mid(110)).id
    accept.assert_not_confirmed(ad_id)
    r = client.post(f"/api/ads/{ad_id}/hypotheses/accept", json={"author": AUTHOR, "complexId": "c3"})
    assert r.status_code == 200, r.text
    assert (r.json()["complexId"], r.json()["linkConfirmedBy"]) == ("c3", AUTHOR)


def test_acceptance_history_kept(client, configured, graph, run_worker, accept, ocr_stub):
    graph.accounts[ACCOUNT] = [image(120, "Старт продаж ЖК Ульба Ривер, бронь")]
    configure(config={"accounts": [ACCOUNT]})
    collect(client, run_worker)
    graph.accounts[ACCOUNT][0]["caption"] = "Старт продаж ЖК Ульба Ривер, бронь — скидка 5%"
    collect(client, run_worker)
    assert count(models.IngestVersion) == 2
    v = client.get(f"/api/ads/{ad_of(mid(120)).id}/versions").json()
    assert [x["text"] for x in v] == ["Старт продаж ЖК Ульба Ривер, бронь — скидка 5%",
                                      "Старт продаж ЖК Ульба Ривер, бронь"]


def test_acceptance_limit_no_endless_loop(client, configured, graph, run_worker, accept, ocr_stub):
    graph.accounts[ACCOUNT] = [image(130)]
    graph.page_errors[ACCOUNT] = lambda after: graph_error(80002, "There have been too many calls to this "
                                                                  "Instagram account")
    now = models.utcnow()
    configure(config={"accounts": [ACCOUNT]}, enabled=True, status="ready", checked_at=now)
    run_worker(now=now)
    assert len(graph.bd_calls()) == 1
    c = connector(client)
    assert c["status"] == "rate_limited"
    graph.mock.calls.clear()
    for minutes in (5, 30, 55):  # такты воркера до next_run_at - без обращений к Graph API
        run_worker(now=now + timedelta(minutes=minutes))
    assert not graph.graph_calls()
    graph.page_errors.clear()
    run_worker(now=now + timedelta(minutes=61))
    assert len(graph.bd_calls()) == 1 and connector(client)["status"] == "working"


# ---------- 15. ответы Graph API, которые нельзя разобрать как есть ----------

def test_graph_odd_values_and_errors(client, configured, http_mock, run_ctx, ig):
    """Infinity/NaN в теле и заголовках(json.loads их принимает) не роняют разбор; 341 - пауза по лимиту; тело,
    которое не распаковалось, - повторяемая ошибка источника; токен с управляющим символом - ошибка настроек."""
    from types import SimpleNamespace

    from app.settings import get_settings

    usage = {"X-App-Usage": '{"call_count": 1e999, "total_time": 12}',
             "X-Business-Use-Case-Usage": json.dumps({BUSINESS_ID: [{"call_count": 5,
                                                                      "estimated_time_to_regain_access": 1e999}]})}
    assert ig.usage_pct(usage) == 12 and ig.regain_seconds(usage) is None
    g = ig.graph_error({"error": {"code": float("inf"), "error_subcode": float("nan"), "message": "x"}})
    assert (g.code, g.subcode) == (0, None)
    assert ig._ts(10 ** 20) is None and ig._ts("0") is None
    limited = ig.map_error(ig.GraphError(341, None, "Application limit reached", False, 400))
    assert isinstance(limited, ig.RateLimited) and limited.retry_after == 3600
    http_mock.on("GET", GRAPH + "me/permissions", content=b"not gzip", headers={"Content-Encoding": "gzip"})
    with ig._client(run_ctx("instagram"), get_settings(), TOKEN) as c, pytest.raises(ig.SourceError) as e:
        ig.graph_get(c, "me/permissions", {}, get_settings())
    assert e.value.retryable and "DecodingError" in e.value.detail
    with pytest.raises(ig.BadConfig):
        ig._token(SimpleNamespace(meta_user_access_token=SimpleNamespace(get=lambda: "EAAfake\x7ftoken")))


def test_cached_ig_user_bound_to_token_and_page(client, env, http_mock, graph, run_ctx, ig):
    """Найденный прежде Instagram(state.igUserId) используется, только пока не сменились токен, META_PAGE_ID и
    META_BUSINESS_ID: иначе сбор шёл бы от прежнего аккаунта до следующей проверки."""
    from app.settings import get_settings

    other = "17841400000000777"
    env(META_USER_ACCESS_TOKEN=TOKEN, META_PAGE_ID=PAGE_ID)
    http_mock.on("GET", GRAPH + "me/accounts", json={"data": [
        {"id": "999", "name": "Другая", "instagram_business_account": {"id": other}},
        {"id": PAGE_ID, "name": "SAQ Qurylys", "instagram_business_account": {"id": IG_ID}}]})

    def resolve(state: dict) -> tuple[str, int]:
        ctx = run_ctx("instagram", state=state)
        with ig._client(ctx, get_settings(), TOKEN) as c:
            found = ig._Run(ctx, get_settings(), c, []).ig_user_id()
        return found, sum(r.url.path.endswith("/me/accounts") for r in graph.graph_calls())

    source = ig._ig_source(get_settings())
    assert TOKEN not in source and PAGE_ID in source
    assert resolve({"igUserId": other, "igUserFor": source}) == (other, 0)  # кэш того же токена и Страницы
    assert resolve({"igUserId": other}) == (IG_ID, 1)  # кэш без привязки(до этой проверки) - поиск заново
    assert collector().state["igUserFor"] == source
    env(META_PAGE_ID="999")
    assert resolve(dict(collector().state)) == (other, 2)  # Страница сменилась - поиск заново


def test_permalink_only_safe_instagram_links(ig):
    """Ссылка на публикацию - только https на instagram.com и без символов, которые приём отбросил бы; иначе ссылка
    на профиль(а не пустая)."""
    profile = f"https://www.instagram.com/{ACCOUNT}/"
    assert ig._permalink("https://www.instagram.com/p/C1x/", ACCOUNT) == "https://www.instagram.com/p/C1x/"
    for bad in ('https://www.instagram.com/p/C1x/"onmouseover=x', "https://www.instagram.com/p/<b>",
                "https://instagram.com@evil.example/p/1", "http://www.instagram.com/p/C1x/",
                "https://evil.example/p/1", "https://www.instagram.com/" + "x" * 600, None, 5):
        assert ig._permalink(bad, ACCOUNT) == profile, bad
