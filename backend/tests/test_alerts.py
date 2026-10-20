"""Предупреждения жителям (app.alerts): согласование другим сотрудником с токеном администратора, подписки от бота
заявок по сервисному токену, отправка воркером через Telegram Bot API (заглушка http_mock) — канал и подписчики,
лимит 429, сеть, неверный токен, повтор, прерванная отправка, отписка; chat_id и токен не попадают в ответы и журнал."""
import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from conftest import FAKE_ADMIN_TOKEN

BOT = "123456789:AAH-test_BotToken_0123456789abcdefXYZ"
SERVICE = "t-service-qrstuvwxyz98765432"
SVC = {"X-API-Key": SERVICE}
ADMIN = {"X-Admin-Token": FAKE_ADMIN_TOKEN}
AUTHOR, CHIEF = "Аналитик ДЭР", "Ведущий аналитик ДЭР"
SEND = re.compile(r"^https://api\.telegram\.org/bot[^/]+/sendMessage$")
DRAFT = {"complexId": "c1", "template": "basis", "title": "Проверьте основание перед оплатой",
         "text": "Перед бронью в ЖК «Ертіс Парк» попросите номер гарантии ЕО & сверьте его в ГАСК <до оплаты>.",
         "author": AUTHOR}


@pytest.fixture
def ready(env):
    """Сервер с токеном администратора, сервисным токеном бота, BOT_TOKEN и каналом ГАСК."""
    return env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN, SERVICE_API_TOKEN=SERVICE, BOT_TOKEN=BOT,
               TG_ALERTS_CHANNEL="@gask_vko_test")


def count(model, *where) -> int:
    from app.db import SessionLocal

    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def deliveries(alert_id: int) -> list:
    from app import models
    from app.db import SessionLocal

    D = models.AlertDelivery
    with SessionLocal() as db:
        return [(d.kind, d.target, d.status, d.error) for d in db.scalars(select(D).where(D.alert_id == alert_id)
                                                                          .order_by(D.id))]


def subscribe(client, chat: str, *districts):
    return client.put(f"/api/intake/telegram/subscriptions/{chat}", headers=SVC, json={"districts": list(districts)})


def draft(client, **kw) -> dict:
    r = client.post("/api/alerts", json={**DRAFT, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def approve(client, aid: int, who: str = CHIEF, headers=None):
    return client.post(f"/api/alerts/{aid}/approve", headers=ADMIN if headers is None else headers,
                       json={"author": who})


def ok(message_id: int = 7):
    import httpx

    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def tg_error(status: int, description: str, **params):
    import httpx

    body = {"ok": False, "error_code": status, "description": description}
    if params:
        body["parameters"] = params
    return httpx.Response(status, json=body)


def alert(client, aid: int) -> dict:
    return next(a for a in client.get("/api/alerts").json() if a["id"] == aid)


def journal(client, cid: str = "c1") -> list:
    """События «жителям» журнала ЖК, новые первыми (журнал отдаётся по возрастанию)."""
    return [e for e in client.get(f"/api/complexes/{cid}/audit").json() if e["kind"] == "alert"][::-1]


# ---------- текст и согласование ----------

def test_draft_pending_one_per_complex_and_validation(client, ready):
    a = draft(client)
    assert (a["status"], a["statusLabel"], a["code"], a["district"]) == ("pending", "На согласовании", "A-0001",
                                                                          "Ульбинский")
    assert (a["createdBy"], a["decidedBy"], a["channel"], a["subscribers"]["total"]) == (AUTHOR, None, None, 0)
    ev = journal(client)
    assert len(ev) == 1 and ev[0]["who"] == AUTHOR and "A-0001 подготовлено и ждёт согласования" in ev[0]["what"]
    # второе по тому же ЖК — 409 со ссылкой на первое; по другому ЖК — можно
    r = client.post("/api/alerts", json=DRAFT)
    assert r.status_code == 409 and r.json()["detail"]["alertId"] == a["id"]
    assert draft(client, complexId="c2")["district"] == "Центр"
    for bad in ({"author": "Система"}, {"author": " "}, {"text": "x" * 241}, {"title": ""}, {"template": "custom"},
                {"status": "sent"}):
        assert client.post("/api/alerts", json={**DRAFT, "complexId": "c3", **bad}).status_code == 422, bad
    assert client.post("/api/alerts", json={**DRAFT, "complexId": "nope"}).status_code == 404
    assert [x["complexId"] for x in client.get("/api/alerts").json()] == ["c2", "c1"]
    assert [x["complexId"] for x in client.get("/api/alerts?complexId=c1").json()] == ["c1"]


def test_approve_needs_admin_other_person_bot_and_recipients(client, env):
    from app import models

    env(SERVICE_API_TOKEN=SERVICE)
    a = draft(client)
    assert approve(client, a["id"]).status_code == 503  # администрирование не настроено
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    assert approve(client, a["id"], headers={}).status_code == 401
    assert approve(client, a["id"], headers={"X-Admin-Token": "t-wrong-0123456789abcdefghij"}).status_code == 403
    r = approve(client, a["id"], who=AUTHOR.upper())
    assert r.status_code == 409 and "другой сотрудник" in r.json()["detail"]
    r = approve(client, a["id"])
    assert r.status_code == 503 and "BOT_TOKEN" in r.json()["detail"]
    env(BOT_TOKEN=BOT)
    r = approve(client, a["id"])
    assert r.status_code == 409 and "Некому отправить" in r.json()["detail"]
    env(TG_ALERTS_CHANNEL="https://t.me/bad")  # неверное имя канала — пояснение в отказе
    r = approve(client, a["id"])
    assert r.status_code == 409 and "TG_ALERTS_CHANNEL" in r.json()["detail"]
    assert alert(client, a["id"])["status"] == "pending"
    assert count(models.Job) == 0


def test_approve_fixes_recipients_and_queues_job(client, ready):
    from app import models

    assert subscribe(client, "1001", "Ульбинский").status_code == 200
    assert subscribe(client, "1002").status_code == 200  # все районы
    assert subscribe(client, "1003", "Центр").status_code == 200  # другой район — не получит
    a = draft(client)
    r = approve(client, a["id"])
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["status"], got["decidedBy"], got["channel"]["target"], got["channel"]["status"]) == (
        "sending", CHIEF, "@gask_vko_test", "pending")
    assert got["subscribers"] == {"total": 2, "sent": 0, "failed": 0, "pending": 2, "skipped": 0}
    assert [d[:3] for d in deliveries(a["id"])] == [("channel", "@gask_vko_test", "pending"),
                                                     ("subscriber", "1001", "pending"),
                                                     ("subscriber", "1002", "pending")]
    assert count(models.Job, models.Job.kind == "alert", models.Job.status == "queued") == 1
    assert "канал @gask_vko_test и подписчики района «Ульбинский» в боте (2)" in journal(client)[0]["what"]
    assert approve(client, a["id"]).status_code == 409  # повторное согласование не создаёт вторую рассылку
    assert count(models.AlertDelivery) == 3
    # новая подписка после согласования в эту рассылку не попадает
    subscribe(client, "1004", "Ульбинский")
    assert len(deliveries(a["id"])) == 3
    # в ответах нет chat_id
    assert "1001" not in json.dumps(client.get("/api/alerts").json())


def test_reject_then_new_draft(client, ready):
    a = draft(client)
    assert client.post(f"/api/alerts/{a['id']}/reject", json={"author": CHIEF}).status_code == 401
    r = client.post(f"/api/alerts/{a['id']}/reject", headers=ADMIN, json={"author": AUTHOR, "comment": "Не тот ЖК"})
    assert r.status_code == 200 and (r.json()["status"], r.json()["decisionComment"]) == ("rejected", "Не тот ЖК")
    assert approve(client, a["id"]).status_code == 409
    assert "отклонено — жителям не отправлено. Комментарий: Не тот ЖК" in journal(client)[0]["what"]
    assert draft(client)["status"] == "pending"


# ---------- подписки (бот заявок) ----------

def test_subscriptions_api_for_bot(client, env):
    from app import models

    url = "/api/intake/telegram/subscriptions/42"
    assert client.put(url, headers=SVC, json={}).status_code == 503  # сервисный токен не настроен
    env(SERVICE_API_TOKEN=SERVICE)
    assert client.put(url, json={}).status_code == 401
    assert client.put(url, headers={"X-API-Key": "t-wrong-token-0123456789abcdef"}, json={}).status_code == 403
    got = client.get("/api/intake/telegram/districts", headers=SVC).json()
    assert {"district": "Ульбинский", "complexes": count(models.Complex, models.Complex.district == "Ульбинский")} in got
    assert [x["complexes"] for x in got] == sorted((x["complexes"] for x in got), reverse=True)
    assert all(x["district"] for x in got)  # ЖК без района в списке нет
    assert client.get(url, headers=SVC).status_code == 404
    r = client.put(url, headers=SVC, json={"districts": ["ульбинский", "Центр"]})
    assert r.status_code == 200 and r.json() == {"chatId": "42", "districts": ["Ульбинский", "Центр"],
                                                 "everywhere": False, "status": "created"}
    assert client.put(url, headers=SVC, json={"districts": ["Центр", "Ульбинский"]}).json()["status"] == "unchanged"
    r = client.put(url, headers=SVC, json={"districts": ["Центр", "*"]})
    assert r.json() == {"chatId": "42", "districts": [], "everywhere": True, "status": "updated"}
    r = client.put(url, headers=SVC, json={"districts": ["Марс"]})
    assert r.status_code == 422 and "Неизвестный район «Марс»" in r.text and "Ульбинский" in r.text
    assert client.put(url, headers=SVC, json={"districts": [], "name": "Иван"}).status_code == 422
    assert client.put("/api/intake/telegram/subscriptions/abc", headers=SVC, json={}).status_code == 422
    assert client.get(url, headers=SVC).json()["everywhere"] is True
    assert client.delete(url, headers=SVC).status_code == 204
    assert client.delete(url, headers=SVC).status_code == 204  # повтор — тоже 204
    assert client.get(url, headers=SVC).status_code == 404


def test_audience_counts_and_readiness(client, env):
    r = client.get("/api/alerts/audience?district=Ульбинский").json()
    assert (r["configured"], r["adminConfigured"], r["channel"], r["subscribers"]) == (False, False, None, 0)
    assert "BOT_TOKEN" in r["problem"] and r["workerAlive"] is False
    env(SERVICE_API_TOKEN=SERVICE, BOT_TOKEN=BOT, SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN, TG_ALERTS_CHANNEL="t.me/gask_vko_test")
    subscribe(client, "1", "Ульбинский")
    subscribe(client, "2")
    subscribe(client, "3", "Центр", "Ульбинский")
    r = client.get("/api/alerts/audience?district=Ульбинский").json()
    assert (r["configured"], r["adminConfigured"], r["problem"], r["channel"]) == (True, True, None, "@gask_vko_test")
    assert (r["subscribers"], r["everywhere"]) == (3, 1)
    assert {"district": "Центр", "subscribers": 1} in r["districts"]
    assert client.get("/api/alerts/audience").json()["subscribers"] == 1  # без района — только «все районы»
    env(TG_ALERTS_CHANNEL="gask")
    r = client.get("/api/alerts/audience").json()
    assert r["channel"] is None and "TG_ALERTS_CHANNEL" in r["channelProblem"]


# ---------- отправка ----------

def test_worker_sends_to_channel_and_subscribers(client, ready, http_mock, run_worker):
    from app import models

    subscribe(client, "1001", "Ульбинский")
    subscribe(client, "1002")
    a = draft(client)
    approve(client, a["id"])
    sent = []

    def reply(request):
        body = json.loads(request.content)
        sent.append(body)
        if body["chat_id"] == "1002":
            return tg_error(403, "Forbidden: bot was blocked by the user")
        return ok(55 if body["chat_id"] == "@gask_vko_test" else 9)

    http_mock.on("POST", SEND, handler=reply)
    assert run_worker() >= 1

    assert [b["chat_id"] for b in sent] == ["@gask_vko_test", "1001", "1002"]
    text = sent[0]["text"]
    assert text.startswith("<b>Проверьте основание перед оплатой</b>\n")
    assert "&amp; сверьте его в ГАСК &lt;до оплаты&gt;." in text and text.endswith("\n\n#Ульбинский · ГАСК ВКО · SAQ QURYLYS")
    assert sent[0]["parse_mode"] == "HTML" and sent[0]["link_preview_options"] == {"is_disabled": True}
    got = alert(client, a["id"])
    assert (got["status"], got["statusLabel"]) == ("partial", "Отправлено частично")
    assert got["channel"] == {"target": "@gask_vko_test", "status": "sent", "error": None,
                              "sentAt": got["channel"]["sentAt"], "link": "https://t.me/gask_vko_test/55"}
    assert got["subscribers"] == {"total": 2, "sent": 1, "failed": 1, "pending": 0, "skipped": 0}
    assert got["errors"] == [{"text": "Житель заблокировал бота — подписка снята", "count": 1}]
    assert got["sentAt"] and got["lastError"] == "Житель заблокировал бота — подписка снята"
    # chat_id подписчиков после итога не хранится; заблокировавший бота — без подписки
    assert [d[1] for d in deliveries(a["id"])] == ["@gask_vko_test", None, None]
    assert count(models.AlertSubscription, models.AlertSubscription.chat_id == "1002") == 0
    assert count(models.Job, models.Job.kind == "alert", models.Job.status == "done") == 1
    last = journal(client)[0]
    assert last["who"] == "Система" and "отправлено частично — канал @gask_vko_test: опубликовано; подписчики: " \
                                      "доставлено 1 из 2, не доставлено 1" in last["what"]
    assert BOT not in json.dumps(client.get("/api/alerts").json())


def test_rate_limit_defers_without_spending_attempt(client, ready, http_mock, run_worker):
    from app import models
    from app.db import SessionLocal

    a = draft(client)
    approve(client, a["id"])
    http_mock.on("POST", SEND, handler=lambda r: ok())
    http_mock.on("POST", SEND, handler=lambda r: tg_error(429, "Too Many Requests: retry after 7", retry_after=7),
                 times=1)
    run_worker()
    with SessionLocal() as db:
        job = db.scalar(select(models.Job).where(models.Job.kind == "alert"))
        assert (job.status, job.attempts) == ("queued", 0)
        wait = (models.as_utc(job.run_after) - models.utcnow()).total_seconds()
        assert 5 < wait <= 7
    got = alert(client, a["id"])
    assert got["status"] == "sending" and "ограничил частоту" in got["lastError"]
    assert deliveries(a["id"])[0][2] == "pending"
    run_worker(now=models.utcnow() + timedelta(seconds=10))
    got = alert(client, a["id"])
    assert (got["status"], got["lastError"], got["channel"]["status"]) == ("sent", None, "sent")


def test_network_error_retries_then_sends(client, ready, http_mock, run_worker):
    import httpx

    from app import models
    from app.db import SessionLocal

    subscribe(client, "1001", "Ульбинский")
    a = draft(client)
    approve(client, a["id"])
    http_mock.on("POST", SEND, handler=lambda r: ok())
    http_mock.on("POST", SEND, handler=lambda r: (_ for _ in ()).throw(httpx.ConnectError("boom", request=r)),
                 times=1)
    run_worker()
    with SessionLocal() as db:
        job = db.scalar(select(models.Job).where(models.Job.kind == "alert"))
        assert (job.status, job.attempts) == ("queued", 1) and "Сетевая ошибка" in job.last_error
    got = alert(client, a["id"])
    assert got["status"] == "sending" and "повтор 2 из 5" in got["lastError"]
    assert [d[2] for d in deliveries(a["id"])] == ["pending", "pending"]
    run_worker(now=models.utcnow() + timedelta(hours=2))
    got = alert(client, a["id"])
    assert (got["status"], got["subscribers"]["sent"], got["channel"]["status"]) == ("sent", 1, "sent")


def test_rejected_token_keeps_pending_and_retry_sends(client, ready, http_mock, run_worker, caplog):
    import httpx

    subscribe(client, "1001")
    a = draft(client)
    approve(client, a["id"])
    http_mock.on("POST", SEND, handler=lambda r: httpx.Response(401, json={"ok": False, "error_code": 401,
                                                                           "description": "Unauthorized"}))
    run_worker()
    got = alert(client, a["id"])
    assert got["status"] == "failed" and "Telegram не принял BOT_TOKEN (HTTP 401)" in got["lastError"]
    assert [d[2] for d in deliveries(a["id"])] == ["pending", "pending"]
    assert "Причина: Telegram не принял BOT_TOKEN" in journal(client)[0]["what"]
    # повтор — только администратор; после исправления токена доставки уходят
    assert client.post(f"/api/alerts/{a['id']}/retry", json={"author": CHIEF}).status_code == 401
    r = client.post(f"/api/alerts/{a['id']}/retry", headers=ADMIN, json={"author": CHIEF})
    assert r.status_code == 200 and r.json()["status"] == "sending"
    assert client.post(f"/api/alerts/{a['id']}/retry", headers=ADMIN, json={"author": CHIEF}).status_code == 409
    http_mock.on("POST", SEND, handler=lambda r: ok())
    run_worker()
    got = alert(client, a["id"])
    assert (got["status"], got["subscribers"]["sent"]) == ("sent", 1)
    assert client.post(f"/api/alerts/{a['id']}/retry", headers=ADMIN, json={"author": CHIEF}).status_code == 409
    assert BOT not in caplog.text and BOT.split(":")[1] not in caplog.text


def test_interrupted_delivery_is_not_sent_twice(client, ready, http_mock, run_worker):
    from app import models
    from app.db import SessionLocal

    subscribe(client, "1001", "Ульбинский")
    a = draft(client)
    approve(client, a["id"])
    with SessionLocal() as db:  # воркер упал, успев взять доставку в канал
        d = db.scalar(select(models.AlertDelivery).where(models.AlertDelivery.kind == "channel"))
        d.status = "sending"
        db.commit()
    http_mock.on("POST", SEND, handler=lambda r: ok())
    run_worker()
    assert [c.url.path.endswith("sendMessage") for c in http_mock.calls] == [True]  # только подписчику
    got = alert(client, a["id"])
    assert (got["status"], got["channel"]["status"]) == ("partial", "failed")
    assert got["channel"]["error"].startswith("Результат неизвестен")


def test_unsubscribe_before_send_skips_delivery(client, ready, http_mock, run_worker):
    subscribe(client, "1001", "Ульбинский")
    subscribe(client, "1002", "Ульбинский")
    a = draft(client)
    approve(client, a["id"])
    assert client.delete("/api/intake/telegram/subscriptions/1001", headers=SVC).status_code == 204
    http_mock.on("POST", SEND, handler=lambda r: ok())
    run_worker()
    got = alert(client, a["id"])
    assert got["status"] == "sent"
    assert got["subscribers"] == {"total": 2, "sent": 1, "failed": 0, "pending": 0, "skipped": 1}
    assert "отписались 1" in journal(client)[0]["what"]


def test_missing_bot_token_in_worker_fails_alert(client, ready, env, run_worker):
    a = draft(client)
    approve(client, a["id"])
    env(BOT_TOKEN=None)
    run_worker()
    got = alert(client, a["id"])
    assert got["status"] == "failed" and "BOT_TOKEN" in got["lastError"]
    assert deliveries(a["id"])[0][2] == "pending"
    r = client.post(f"/api/alerts/{a['id']}/retry", headers=ADMIN, json={"author": CHIEF})
    assert r.status_code == 503


def test_abandoned_job_finalizes_alert(client, ready):
    from app import jobs, models
    from app.db import SessionLocal

    a = draft(client)
    approve(client, a["id"])
    with SessionLocal() as db:
        job = db.scalar(select(models.Job).where(models.Job.kind == "alert"))
        job.status, job.locked_by, job.locked_at, job.attempts = "running", "dead", models.utcnow(), job.max_attempts
        db.scalar(select(models.AlertDelivery)).status = "sending"
        db.commit()
        assert jobs.recover_stale(db, models.utcnow() + timedelta(hours=2), 60) == 1
        db.commit()
    got = alert(client, a["id"])
    assert got["status"] == "failed" and got["channel"]["error"].startswith("Результат неизвестен")
    assert "воркер перестал отвечать" in got["lastError"]


# ---------- проверка бота ----------

def test_check_bot_and_channel(client, ready, http_mock):
    base = f"https://api.telegram.org/bot{BOT}"
    assert client.post("/api/alerts/check").status_code == 401
    http_mock.on("POST", f"{base}/getMe", json={"ok": True, "result": {"id": 77, "username": "saq_vko_bot"}})
    http_mock.on("POST", f"{base}/getChat", json={"ok": True, "result": {"id": -1001, "title": "ГАСК ВКО"}})
    http_mock.on("POST", f"{base}/getChatMember", json={"ok": True, "result": {"status": "administrator",
                                                                              "can_post_messages": True}})
    r = client.post("/api/alerts/check", headers=ADMIN).json()
    assert r == {"ok": True, "detail": "@saq_vko_bot — администратор канала «ГАСК ВКО» (@gask_vko_test) и может "
                                       "публиковать", "bot": "@saq_vko_bot", "channel": "@gask_vko_test",
                 "channelTitle": "ГАСК ВКО"}
    assert json.loads(http_mock.calls[-1].content) == {"chat_id": "@gask_vko_test", "user_id": 77}
    http_mock.on("POST", f"{base}/getChatMember", json={"ok": True, "result": {"status": "left"}})
    r = client.post("/api/alerts/check", headers=ADMIN).json()
    assert r["ok"] is False and "сделайте его администратором" in r["detail"]
    http_mock.on("POST", f"{base}/getChat", handler=lambda r: tg_error(400, "Bad Request: chat not found"))
    r = client.post("/api/alerts/check", headers=ADMIN).json()
    assert r["ok"] is False and "Канал @gask_vko_test не найден" in r["detail"]
    http_mock.on("POST", f"{base}/getMe", handler=lambda r: tg_error(401, "Unauthorized"))
    r = client.post("/api/alerts/check", headers=ADMIN).json()
    assert r["ok"] is False and "не принял BOT_TOKEN" in r["detail"] and BOT not in json.dumps(r)
    assert not any("sendMessage" in str(c.url) for c in http_mock.calls)  # проверка ничего не отправляет


def test_render_and_hashtag():
    from app import alerts, models

    assert alerts.hashtag("Северо-запад") == "#Северо_запад" and alerts.hashtag("") == ""
    a = models.ResidentAlert(title="A <b>", text="x & y", district="")
    assert alerts.render(a) == "<b>A &lt;b&gt;</b>\nx &amp; y\n\nГАСК ВКО · SAQ QURYLYS"
    assert alerts.chat_reason("subscriber", "1", "Forbidden: user is deactivated") == (
        "Аккаунт жителя удалён — подписка снята", True)
    assert alerts.chat_reason("subscriber", "1", "Bad Request: message is too long")[1] is False
