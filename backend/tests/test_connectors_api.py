"""API подключений (integration-spec §10.4–§10.8, §11.1, §5.3, §16.2 test_connectors_api): состояние без секретов,
права администратора, настройки, запуски, «Обновить всё», изоляция модулей, защита публичного адреса."""
import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import FAKE_ADMIN_TOKEN

from app import models
from app.db import SessionLocal
from app.integrations import registry, service
from app.integrations.base import ConfigError, Connector

AUTHOR = "Омарова Ж."
ORDER = ["instagram", "telegram", "tiktok", "krisha", "telegram_bot", "intake", "dgis", "anthropic", "olx"]
SECRETS = {"META_USER_ACCESS_TOKEN": "FAKEMETA-0123456789abcdefgh", "META_APP_SECRET": "FAKEAPPSECRET-0123456789",
           "APIFY_TOKEN": "FAKEAPIFY-0123456789abcdefgh", "DGIS_API_KEY": "FAKEDGIS-0123456789abcdefgh",
           "OLX_CLIENT_SECRET": "FAKEOLX-0123456789abcdefghij", "ANTHROPIC_API_KEY": "FAKEANTHROPIC-0123456789abcd",
           "TG_API_HASH": "FAKETGHASH-0123456789abcdefg", "TG_PHONE": "+77010000000",
           "SAQ_ADMIN_TOKEN": FAKE_ADMIN_TOKEN, "INTEGRATION_INGEST_TOKEN": "t-ingest-abcdefghijklmnop0123",
           "SERVICE_API_TOKEN": "t-service-qrstuvwxyz98765432"}
REDIRECT = "https://x.ngrok-free.dev/api/integrations/olx/callback"


def connectors(client) -> dict:
    r = client.get("/api/sources/connectors")
    assert r.status_code == 200, r.text
    return r.json()


def by_code(client) -> dict:
    return {c["code"]: c for c in connectors(client)["connectors"]}


def admin(**kw):
    return {"X-Admin-Token": FAKE_ADMIN_TOKEN, **kw}


# ---------- состояние ----------

def test_connectors_list_not_configured(client):
    body = connectors(client)
    assert [c["code"] for c in body["connectors"]] == ORDER
    assert body["adminConfigured"] is False and body["worker"] == {"alive": False, "lastSeenAt": None, "id": None}
    st = {c["code"]: c["status"] for c in body["connectors"]}
    assert st == {**{c: "not_configured" for c in ORDER}, "krisha": "unchecked"}
    c = {x["code"]: x for x in body["connectors"]}
    assert (c["instagram"]["missing"], c["instagram"]["statusDetail"]) == (
        ["META_USER_ACCESS_TOKEN"], "Нет на сервере: META_USER_ACCESS_TOKEN")
    assert c["telegram"]["missing"] == ["TG_API_ID", "TG_API_HASH", "TG_SESSION_PATH"]
    assert (c["olx"]["statusLabel"], c["olx"]["group"], c["olx"]["kind"]) == ("заявка отправлена", "limited", "oauth")
    assert (c["tiktok"]["paid"], c["telegram_bot"]["contractVerified"], c["krisha"]["intervalMin"]) == (True, False, 360)
    assert c["krisha"]["intervalBounds"] == [120, 10080] and c["dgis"]["intervalBounds"] is None
    assert c["dgis"]["capabilities"] == {"test": True, "collect": False, "toggle": False, "configure": False,
                                         "connect": False}
    assert c["olx"]["capabilities"]["connect"] is True and c["intake"]["capabilities"]["test"] is False
    assert "{TG_EDIT_WINDOW}" not in c["telegram"]["limitation"] and "последних 30 сообщений" in c["telegram"]["limitation"]
    assert not any(isinstance(x, registry.BrokenConnector) for x in registry.CONNECTORS.values())
    # подписи статусов, которые задают подключения
    assert registry.get("dgis").status_label("ready") == "готов"
    assert registry.get("olx").status_label("unchecked") == "не подключён"
    assert registry.get("olx").status_label("ready") == "подключено · ограничено"
    assert registry.get("telegram_bot").status_label("working") == "принимает заявки"
    assert registry.get("intake").status_label("working") == "принимает публикации"
    one = client.get("/api/sources/connectors/krisha").json()
    assert (one["code"], one["statusLabel"]) == ("krisha", "не проверен")
    assert client.get("/api/sources/connectors/nope").json() == {"detail": "Подключение не найдено"}


def test_connectors_configured_without_secret_values(client, env):
    env(**SECRETS, TG_API_ID="12345", OLX_CLIENT_ID="olx-client-id", OLX_REDIRECT_URI=REDIRECT)
    text = client.get("/api/sources/connectors").text
    for name, value in SECRETS.items():
        assert value not in text, name
    body = connectors(client)
    st = {c["code"]: (c["status"], c["statusLabel"]) for c in body["connectors"]}
    assert body["adminConfigured"] is True
    assert st["instagram"] == ("unchecked", "не проверен") and st["tiktok"][0] == "unchecked"
    assert st["telegram"] == ("not_configured", "не настроен")  # ключи есть, сессии нет
    assert st["anthropic"] == ("unchecked", "не проверен")  # ключ есть, проверки ещё не было
    assert st["intake"] == ("waiting", "ожидает публикаций")
    assert st["telegram_bot"] == ("waiting", "ожидает заявок")  # токен есть, заявок ещё не было
    assert st["olx"] == ("unchecked", "не подключён") and st["dgis"] == ("unchecked", "не проверен")
    c = {x["code"]: x for x in body["connectors"]}
    assert c["telegram"]["statusDetail"].startswith("Нет авторизованной сессии Telegram: выполните вход")
    assert c["instagram"]["secretRefs"] == ["env:META_USER_ACCESS_TOKEN"] and c["instagram"]["configured"] is True


def test_admin_check(client, env):
    assert client.get("/api/admin/check").status_code == 503
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    assert client.get("/api/admin/check").status_code == 401
    assert client.get("/api/admin/check", headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert client.get("/api/admin/check", headers=admin()).json() == {"ok": True}
    assert client.get("/api/admin/check", headers={"Authorization": f"Bearer {FAKE_ADMIN_TOKEN}"}).status_code == 200
    assert connectors(client)["adminConfigured"] is True
    env(SERVICE_API_TOKEN=FAKE_ADMIN_TOKEN)  # совпадает с сервисным — администратор не настроен
    assert client.get("/api/admin/check", headers=admin()).status_code == 503
    assert connectors(client)["adminConfigured"] is False


# ---------- настройки ----------

class StrictIg(Connector):
    """Instagram с проверкой формата аккаунта (как настоящий модуль)."""
    code = "instagram"
    kind = "collector"
    platform = "instagram"
    group = "collectors"
    name = "Instagram"
    via = "тест"
    required_env = ("META_USER_ACCESS_TOKEN",)
    config_fields = ("accounts",)
    default_interval_min = 60
    interval_bounds = (30, 1440)

    def normalize_item(self, field_name, value):
        v = value.lstrip("@").lower()
        if not re.fullmatch(r"[a-z0-9._]{1,30}", v):
            raise ConfigError(field_name, f"Неверный аккаунт Instagram: {value}", value)
        return "@" + v


def test_patch_auth_and_validation(client, env, monkeypatch):
    body = {"author": AUTHOR, "config": {"accounts": ["@tumar.group.astana"]}}
    assert client.patch("/api/sources/instagram", json=body).status_code == 503
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    assert client.patch("/api/sources/instagram", json=body).status_code == 401
    assert client.patch("/api/sources/instagram", json=body, headers={"X-Admin-Token": "x"}).status_code == 403
    r = client.patch("/api/sources/instagram", headers=admin(),
                     json={"author": AUTHOR, "config": {"accounts": [" @tumar.group.astana ", "", "@tumar.group.astana",
                                                                     "@altai.build"]}})
    assert r.status_code == 200, r.text
    assert r.json()["config"] == {"accounts": ["@tumar.group.astana", "@altai.build"]}
    with SessionLocal() as db:
        what = db.scalars(select(models.AuditEvent).where(models.AuditEvent.what.like("Настройки источника%"))).all()
        assert [(e.kind, e.who, e.what) for e in what] == [
            ("data", AUTHOR, "Настройки источника «Instagram» изменены: аккаунты: +@tumar.group.astana, +@altai.build")]
        assert service.row(db, "instagram").updated_by == AUTHOR
    # «Система» — зарезервированное имя
    r = client.patch("/api/sources/instagram", headers=admin(), json={"author": "система", "enabled": False})
    assert r.status_code == 422 and "зарезервировано" in r.text
    # формат элемента проверяет подключение → 422 с полем config
    monkeypatch.setitem(registry.CONNECTORS, "instagram", StrictIg())
    r = client.patch("/api/sources/instagram", headers=admin(),
                     json={"author": AUTHOR, "config": {"accounts": ["https://evil.example/@x y"]}})
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"] == ["body", "config", "accounts"] and err["msg"].startswith("Неверный аккаунт Instagram")
    # параметра, которого у подключения нет, — 422; неизвестного поля схемы — 422
    assert client.patch("/api/sources/instagram", headers=admin(),
                        json={"author": AUTHOR, "config": {"channels": ["x"]}}).status_code == 422
    assert client.patch("/api/sources/instagram", headers=admin(),
                        json={"author": AUTHOR, "config": {"token": "x"}}).status_code == 422
    # включить ненастроенное — 409 с именами переменных
    r = client.patch("/api/sources/instagram", headers=admin(), json={"author": AUTHOR, "enabled": True})
    assert (r.status_code, r.json()["detail"]) == (409, "Сначала настройте подключение на сервере: "
                                                        "META_USER_ACCESS_TOKEN")
    # период вне пределов подключения; код не из списка подключений
    r = client.patch("/api/sources/krisha", headers=admin(), json={"author": AUTHOR, "intervalMin": 30})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "intervalMin"]
    assert client.patch("/api/sources/import", headers=admin(), json={"author": AUTHOR}).status_code == 422
    assert client.patch("/api/sources/dgis", headers=admin(), json={"author": AUTHOR, "enabled": True}).status_code \
        == 409


def test_enable_queues_test_and_disable_cancels(client, env):
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    r = client.patch("/api/sources/krisha", headers=admin(), json={"author": AUTHOR, "enabled": True,
                                                                   "intervalMin": 720})
    assert r.status_code == 200, r.text
    c = r.json()
    assert (c["enabled"], c["intervalMin"], c["status"], c["activeRunId"] is not None) == (True, 720, "checking", True)
    with SessionLocal() as db:
        job = db.scalars(select(models.Job).where(models.Job.dedupe_key == "test:krisha")).one()
        assert (job.kind, job.status, job.max_attempts, job.priority) == ("test", "queued", 1, 10)
        assert db.scalars(select(models.AuditEvent.what).where(models.AuditEvent.what.like("Настройки%"))).one() == \
            "Настройки источника «Krisha.kz» изменены: период: 720 мин; сбор включён"
    collect = client.post("/api/sources/krisha/collect", headers=admin(), json={"author": AUTHOR})
    assert collect.status_code == 202
    r = client.patch("/api/sources/krisha", headers=admin(), json={"author": AUTHOR, "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    run = client.get(f"/api/collection-runs/{collect.json()['id']}").json()
    assert (run["status"], run["statusLabel"]) == ("cancelled", "отменён")


def test_disable_keeps_deferred_paid_run(client, env):
    """Платный сбор ждёт между фазами (Deferred: задание снова «в очереди», внешний запуск Apify уже идёт): выключение
    его не отменяет — иначе оплаченный запуск не дочитать и стоимость не записать. Новых по расписанию не будет."""
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN, APIFY_TOKEN=SECRETS["APIFY_TOKEN"])
    with SessionLocal() as db:
        r = service.row(db, "tiktok", create=True)
        r.enabled, r.status = True, "working"
        run, _ = service.request_run(db, "tiktok", trigger="schedule", who="Система")
        db.commit()
        run_id = run.id
    with SessionLocal() as db:
        db.get(models.CollectionRun, run_id).external_run_id = "apifyRun0001"
        job = db.scalars(select(models.Job).where(models.Job.run_id == run_id)).one()
        job.payload = {"phase": "poll", "apifyRunId": "apifyRun0001"}
        db.commit()
    r = client.patch("/api/sources/tiktok", headers=admin(), json={"author": AUTHOR, "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    with SessionLocal() as db:
        job = db.scalars(select(models.Job).where(models.Job.run_id == run_id)).one()
        assert job.status == "queued" and db.get(models.CollectionRun, run_id).status == "queued"
    # без внешнего запуска — отменяется, как обычно
    r = client.patch("/api/sources/tiktok", headers=admin(), json={"author": AUTHOR, "enabled": True})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        db.get(models.CollectionRun, run_id).external_run_id = None
        db.commit()
    client.patch("/api/sources/tiktok", headers=admin(), json={"author": AUTHOR, "enabled": False})
    assert client.get(f"/api/collection-runs/{run_id}").json()["status"] == "cancelled"


# ---------- вид для посетителя ----------

KRISHA_URL = "https://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/"
DETAIL = "Сайт закрыл выдачу проверкой доступа"
FAILURE = "krisha.kz ответил 503 на /prodazha/kvartiry"
NOTE = "страница 2: 503 от сервера krisha.kz"


def _detailed() -> int:
    """Подключение и запуск со всеми подробностями (пояснение, ошибка, настройки, факты, автор, стоимость, заметки) и
    живой воркер с ID — то, что посетитель видеть не должен."""
    now = models.utcnow()
    with SessionLocal() as db:
        r = service.row(db, "krisha", create=True)
        r.status, r.status_detail, r.last_error, r.last_error_at = "error", DETAIL, FAILURE, now
        r.config, r.facts = {"searchUrls": [KRISHA_URL]}, {"account": "@saq-monitor", "keyFp": "fp0123456789"}
        run = models.CollectionRun(collector="krisha", trigger="manual", status="failed", requested_by=AUTHOR,
                                   created_at=now, finished_at=now, external_run_id="extRun0001", cost_usd=0.12,
                                   error_code="http_503", error=FAILURE,
                                   notes=[{"at": models.iso(now), "level": "error", "text": NOTE}])
        db.add(run)
        db.add(models.WorkerLease(name="worker:host-01", holder="host-01", expires_at=now + timedelta(seconds=60),
                                  heartbeat_at=now, info={"id": "host-01"}))
        db.commit()
        return run.id


@pytest.mark.visitor
def test_visitor_sees_only_status_and_counts(client, env):
    """Без токена администратора (и с неверным): статусы, расписание и счётчики есть; способа подключения, имён
    ключей, списков источников, пояснений, ошибок, фактов, автора, стоимости, заметок и ID воркера нет."""
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    run_id = _detailed()
    for headers in ({}, {"X-Admin-Token": "t-wrong-0123456789abcdef0123"}):
        r = client.get("/api/sources/connectors", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["admin"], body["adminConfigured"]) == (False, True)
        assert body["worker"]["alive"] is True and body["worker"]["id"] is None
        c = {x["code"]: x for x in body["connectors"]}
        k = c["krisha"]
        assert (k["status"], k["statusLabel"], k["intervalMin"], k["counts"]) == (
            "error", "ошибка", 360, {"items": 0, "ads": 0, "media": 0})
        assert (k["via"], k["statusDetail"], k["lastError"], k["lastErrorAt"], k["config"], k["facts"]) == (
            "", None, None, None, {}, {})
        run = k["lastRun"]
        assert (run["id"], run["status"], run["trigger"]) == (run_id, "failed", "manual")
        assert (run["requestedBy"], run["externalRunId"], run["costUsd"], run["errorCode"], run["error"],
                run["notes"]) == (None, None, None, None, None, [])
        assert c["instagram"]["configured"] is False
        assert all(x["missing"] == [] and x["secretRefs"] == [] and x["via"] == "" for x in body["connectors"])
        one = client.get("/api/sources/connectors/krisha", headers=headers).json()
        runs = client.get("/api/collection-runs", headers=headers).json()
        got = client.get(f"/api/collection-runs/{run_id}", headers=headers).json()
        assert one["statusDetail"] is None and one["lastRun"]["notes"] == []
        assert [x["id"] for x in runs] == [run_id] and runs[0]["error"] is None and got["costUsd"] is None
        text = json.dumps([body, one, runs, got], ensure_ascii=False)
        for hidden in (DETAIL, FAILURE, NOTE, KRISHA_URL, "@saq-monitor", "fp0123456789", "extRun0001", "host-01",
                       AUTHOR, "META_USER_ACCESS_TOKEN", "APIFY_TOKEN", "TG_API_HASH", "ANTHROPIC_MODEL",
                       "/api/intake"):
            assert hidden not in text, hidden


@pytest.mark.visitor
def test_admin_token_gets_full_view(client, env):
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    run_id = _detailed()
    body = client.get("/api/sources/connectors", headers=admin()).json()
    assert body["admin"] is True and body["worker"]["id"] == "host-01"
    k = next(x for x in body["connectors"] if x["code"] == "krisha")
    assert (k["statusDetail"], k["lastError"], k["config"]["searchUrls"], k["facts"]["account"]) == (
        DETAIL, FAILURE, [KRISHA_URL], "@saq-monitor")
    assert k["via"] and (k["lastRun"]["requestedBy"], k["lastRun"]["costUsd"]) == (AUTHOR, 0.12)
    ig = next(x for x in body["connectors"] if x["code"] == "instagram")
    assert ig["missing"] == ["META_USER_ACCESS_TOKEN"]
    got = client.get(f"/api/collection-runs/{run_id}", headers={"Authorization": f"Bearer {FAKE_ADMIN_TOKEN}"})
    assert got.json()["notes"][0]["text"] == NOTE
    # администрирование не настроено — подробностей нет никому
    env(SAQ_ADMIN_TOKEN=None)
    body = client.get("/api/sources/connectors", headers=admin()).json()
    assert (body["admin"], body["adminConfigured"]) == (False, False)


# ---------- запуски ----------

def test_test_collect_and_runs(client, env):
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    assert client.post("/api/sources/krisha/test", json={"author": AUTHOR}).status_code == 401
    r = client.post("/api/sources/krisha/test", headers=admin(), json={"author": AUTHOR})
    assert r.status_code == 202, r.text
    run = r.json()
    assert (run["source"], run["trigger"], run["status"], run["statusLabel"], run["requestedBy"]) == (
        "krisha", "test", "queued", "в очереди", AUTHOR)
    assert run["counts"] == {"seen": 0, "new": 0, "updated": 0, "unchanged": 0, "failed": 0, "mediaSaved": 0,
                             "mediaFailed": 0}
    again = client.post("/api/sources/krisha/test", headers=admin(), json={"author": AUTHOR})
    assert (again.status_code, again.json()["id"]) == (200, run["id"])
    with SessionLocal() as db:
        job = db.scalars(select(models.Job).where(models.Job.run_id == run["id"])).one()
        assert (job.kind, job.collector, job.status) == ("test", "krisha", "queued")
    assert client.get("/api/sources/connectors/krisha").json()["status"] == "checking"
    r = client.post("/api/sources/dgis/collect", headers=admin(), json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "У этого подключения нет сбора публикаций")
    r = client.post("/api/sources/instagram/test", headers=admin(), json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "Сначала настройте подключение на сервере: "
                                                        "META_USER_ACCESS_TOKEN")
    assert client.post("/api/sources/intake/test", headers=admin(), json={"author": AUTHOR}).status_code == 409
    assert client.post("/api/sources/krisha/test", headers=admin(), json={"author": "Система"}).status_code == 422
    got = client.get(f"/api/collection-runs/{run['id']}").json()
    assert got["id"] == run["id"] and got["statusLabel"] == "в очереди"
    assert client.get("/api/collection-runs/999999").json() == {"detail": "Запуск не найден"}
    listed = client.get("/api/collection-runs", params={"source": "krisha", "limit": 5}).json()
    assert [x["id"] for x in listed] == [run["id"]]
    assert client.get("/api/collection-runs", params={"limit": 0}).status_code == 422


def test_collect_all(client, env, monkeypatch):
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN, APIFY_TOKEN=SECRETS["APIFY_TOKEN"])
    with SessionLocal() as db:
        for code, values in (("krisha", {"enabled": True, "status": "working"}),
                             ("tiktok", {"enabled": True, "status": "unchecked"})):
            r = service.row(db, code, create=True)
            for k, v in values.items():
                setattr(r, k, v)
        db.commit()
    r = client.post("/api/sources/collect-all", headers=admin(), json={"author": AUTHOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [q["code"] for q in body["queued"]] == ["krisha"] and body["queued"][0]["runId"]
    reasons = {s["code"]: s["reason"] for s in body["skipped"]}
    assert reasons == {"instagram": "выключен", "telegram": "выключен",
                       "tiktok": "не проверен — сначала «Проверить подключение»"}
    again = client.post("/api/sources/collect-all", headers=admin(), json={"author": AUTHOR}).json()
    assert {s["code"]: s["reason"] for s in again["skipped"]}["krisha"] == "уже идёт"
    assert client.post("/api/sources/collect-all", json={"author": AUTHOR}).status_code == 401


# ---------- изоляция модулей ----------

def test_broken_module_isolated(client, monkeypatch):
    import importlib

    def importer(name):
        if name.endswith(".tiktok"):
            raise SyntaxError("half-written connector")
        return importlib.import_module(name)

    conns, routers = registry.load(importer)
    assert isinstance(conns["tiktok"], registry.BrokenConnector)
    assert not any(isinstance(c, registry.BrokenConnector) for k, c in conns.items() if k != "tiktok")
    assert len(routers) == len(registry.routers())
    monkeypatch.setitem(registry.CONNECTORS, "tiktok", conns["tiktok"])
    c = client.get("/api/sources/connectors/tiktok").json()
    assert (c["status"], c["statusDetail"], c["name"]) == ("error", registry.BROKEN, "TikTok")
    assert c["capabilities"]["test"] is False and connectors(client)["connectors"][3]["code"] == "krisha"


def test_not_connector_object_is_broken():
    import types

    fake = types.ModuleType("x")
    fake.CONNECTOR = object()
    conns, _ = registry.load(lambda name: fake if name.endswith(".dgis") else __import__("importlib").import_module(name))
    assert isinstance(conns["dgis"], registry.BrokenConnector) and conns["dgis"].kind == "geo"


# ---------- публичный адрес (§5.3) ----------

def test_not_configured_409_names_real_problem(client, env):
    """Регрессия: у Telegram без сессии 409 советовал задать TG_SESSION_PATH (её задаёт compose) — нужен вход, и
    подсказку о входе подключение уже знает (missing_detail)."""
    from app.settings import get_settings

    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN, TG_API_ID="123456", TG_API_HASH=SECRETS["TG_API_HASH"])
    conn = registry.get("telegram")
    with SessionLocal() as db:
        assert conn.missing(get_settings(), service.row(db, "telegram")) == ["TG_SESSION_PATH"]
        hint = conn.missing_detail(get_settings(), service.row(db, "telegram"))
    assert hint and "TG_SESSION_PATH" not in hint
    for r in (client.post("/api/sources/telegram/test", headers=admin(), json={"author": AUTHOR}),
              client.patch("/api/sources/telegram", headers=admin(), json={"author": AUTHOR, "enabled": True})):
        assert (r.status_code, r.json()["detail"]) == (409, hint)
    env(TG_API_ID=None)  # нет переменной окружения — по-прежнему её имя
    r = client.post("/api/sources/telegram/test", headers=admin(), json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "Сначала настройте подключение на сервере: TG_API_ID")


def test_public_host_guard(client, env):
    env(OLX_REDIRECT_URI=REDIRECT)
    host = {"Host": "x.ngrok-free.dev"}
    for path in ("/api/ads", "/docs", "/api/sources/connectors", "/api/health"):
        r = client.get(path, headers=host)
        assert (r.status_code, r.json()) == (404, {"detail": "Не найдено"}), path
    r = client.get("/api/integrations/olx/callback", headers=host)
    assert r.status_code != 404 or r.json() != {"detail": "Не найдено"}
    assert client.get("/api/ads", headers={"X-Forwarded-Host": "X.ngrok-free.dev:443"}).status_code == 404
    assert client.get("/api/ads", headers={"Host": "x.ngrok-free.dev:443"}).status_code == 404
    # регрессия: хост с конечной точкой — тот же хост, API через туннель не открывается
    for headers in ({"Host": "x.ngrok-free.dev."}, {"Host": "X.ngrok-free.dev.:443"},
                    {"X-Forwarded-Host": "x.ngrok-free.dev."}):
        for path in ("/api/ads", "/docs", "/openapi.json", "/api/health"):
            assert client.get(path, headers=headers).status_code == 404, (headers, path)
    assert client.get("/api/ads").status_code == 200  # обычный адрес — как раньше
    env(OLX_REDIRECT_URI=None)
    assert client.get("/api/ads", headers=host).status_code == 200


def test_open_data_sources_unchanged(client):
    r = client.get("/api/sources")
    assert r.status_code == 200
    codes = [s["code"] for s in r.json()]
    assert "homeportal" in codes and not set(codes) & set(ORDER)  # открытые данные, не подключения


def test_reject_recording_throttled(client, env):
    env(INTEGRATION_INGEST_TOKEN=SECRETS["INTEGRATION_INGEST_TOKEN"])
    for _ in range(3):
        service.record_reject("intake", 403, "Неверный токен приёма публикаций")
    service.record_reject("intake", 422, "text: слишком длинный " + "x" * 400)
    with SessionLocal() as db:
        rej = service.row(db, "intake").state["rejects"]
    assert (rej["count"], rej["lastCode"]) == (2, 422) and len(rej["lastDetail"]) <= 200
    c = client.get("/api/sources/connectors/intake").json()
    assert c["status"] == "error" and c["statusDetail"].startswith("Последняя публикация отклонена: text:")
