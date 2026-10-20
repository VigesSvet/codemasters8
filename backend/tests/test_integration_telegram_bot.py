"""Telegram-бот заявок: доступ по сервисному токену, заявка JSON и multipart, повторная доставка и версии, заявитель
отдельно от публикации, статус заявки для бота, статус подключения и приёмочные проверки. Модуль подключения
импортируется внутри тестов."""
import json
from datetime import datetime, timedelta, timezone, UTC

import pytest
from sqlalchemy import func, select

from conftest import mp4_bytes, png_bytes

URL = "/api/intake/telegram"
SERVICE_TOKEN = "t-service-qrstuvwxyz98765432"
WRONG_TOKEN = "t-wrong-token-0123456789abcdef"
KEY = {"X-API-Key": SERVICE_TOKEN}
BEARER = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
AUTHOR = "Омарова Ж."
EID = "123456789:42"
APPLICANT = {"telegramUserId": 987654321, "chatId": "987654321", "username": "resident_ukg",
             "name": "Иванова Мария Петровна", "phone": "+77011234567"}
COMMENT = "Видела объявление у подъезда, звонила — просят бронь"
PII = ("987654321", "resident_ukg", "Иванова Мария", "+77011234567", "77011234567", "у подъезда")
ALMATY = timezone(timedelta(hours=5))


@pytest.fixture
def bot(env):
    """Сервисный токен на сервере и модуль подключения(импорт - внутри теста)."""
    env(SERVICE_API_TOKEN=SERVICE_TOKEN)
    from app.integrations import telegram_bot

    return telegram_bot


def body(**kw) -> dict:
    data = {"externalId": EID, "submittedAt": "2026-10-09T11:10:23Z", "platform": "instagram",
            "account": "@altai.build", "url": "https://www.instagram.com/p/DAbc123/",
            "text": "Старт продаж ЖК Ульба Ривер, бронь от 100 000 ₸", "comment": COMMENT, "applicant": APPLICANT}
    return {k: v for k, v in {**data, **kw}.items() if v is not None}


def post(client, data: dict, headers=None):
    return client.post(URL, headers=KEY if headers is None else headers, json=data)


def post_files(client, data: dict, files: list, headers=None, field: str = "files"):
    parts = [(field, (name, content, mime)) for name, content, mime in files]
    return client.post(URL, headers=KEY if headers is None else headers, data={"payload": json.dumps(data)},
                       files=parts)


def count(model, *where) -> int:
    from app.db import SessionLocal

    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def connector(client) -> dict:
    r = client.get("/api/sources/connectors/telegram_bot")
    assert r.status_code == 200, r.text
    return r.json()


def stored_files() -> list:
    from app import media

    return sorted(p for p in media.root().rglob("*") if p.is_file() and ".tmp" not in p.parts)


def snapshot() -> tuple:
    from app import models

    return (count(models.Ad), count(models.IngestItem), count(models.IngestVersion), count(models.MediaAsset),
            count(models.BotSubmission), len(stored_files()))


# ---------- 1. доступ ----------

def test_auth_matrix_and_rejections_visible(client, env):
    from app import auth

    r = post(client, body())
    assert (r.status_code, r.json()["detail"]) == (503, auth.SERVICE_UNSET)
    assert client.get(f"{URL}/submissions/{EID}", headers=KEY).status_code == 503
    c = connector(client)
    assert (c["status"], c["missing"], c["contractVerified"]) == ("not_configured", ["SERVICE_API_TOKEN"], False)

    env(SERVICE_API_TOKEN=SERVICE_TOKEN)
    r = post(client, body(), headers={})
    assert (r.status_code, r.json()["detail"], r.headers["www-authenticate"]) == (401, auth.SERVICE_MISSING, "Bearer")
    # токен проверяется до разбора тела: испорченный multipart без токена - 401, а не 400/422
    r = client.post(URL, content=b"--xyz\r\ngarbage", headers={"Content-Type": "multipart/form-data; boundary=xyz"})
    assert r.status_code == 401
    assert client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 401
    assert client.get(f"{URL}/updates", params={"since": "2026-10-17T00:00:00Z"}).status_code == 401
    assert client.get(f"{URL}/submissions/{EID}").status_code == 401

    r = post(client, body(), headers={"X-API-Key": WRONG_TOKEN})
    assert (r.status_code, r.json()["detail"]) == (403, auth.SERVICE_WRONG)
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("denied", "нет доступа")
    assert c["statusDetail"].startswith("Бот обращается с неверным сервисным токеном (последняя попытка ")
    assert post(client, body(), headers={"Authorization": f"Bearer {WRONG_TOKEN}"}).status_code == 403

    # X-API-Key и Bearer - оба принимаются; успех новее отказа - "принимает заявки"
    assert post(client, body()).status_code == 201
    assert post(client, body(), headers=BEARER).status_code == 200
    assert post(client, body(), headers={"authorization": f"bearer {SERVICE_TOKEN}"}).status_code == 200
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("working", "принимает заявки")

    # отклонённая заявка(422) новее успеха - "ошибка" с причиной
    r = post(client, body(externalId=None))
    assert r.status_code == 422
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("error", "ошибка")
    assert c["statusDetail"].startswith("Последняя заявка отклонена: body.externalId")

    text = client.get("/api/sources/connectors").text
    assert SERVICE_TOKEN not in text and WRONG_TOKEN not in text


# ---------- 2. заявка JSON; заявитель - только в bot_submissions ----------

def test_json_submission_applicant_private(client, bot):
    from app import models
    from app.db import SessionLocal

    r = post(client, body())
    assert r.status_code == 201, r.text
    out = r.json()
    assert out == {"externalId": EID, "adId": out["adId"], "status": "created", "submissionStatus": "received",
                   "statusLabel": "Заявка получена и ждёт проверки аналитиком"}
    ad_id = out["adId"]
    assert len(ad_id) == 32
    ad = client.get(f"/api/ads/{ad_id}").json()
    assert (ad["platform"], ad["source"], ad["origin"], ad["status"], ad["complexId"]) == (
        "instagram", "bot", "collected", "new", None)
    assert (ad["account"], ad["url"]) == ("@altai.build", "https://www.instagram.com/p/DAbc123/")
    assert ad["date"] == "2026-10-09T16:10"  # submittedAt → местное время
    assert ad["processing"]["status"] == "queued"

    with SessionLocal() as db:
        sub = db.scalars(select(models.BotSubmission)).one()
        assert (sub.external_id, sub.ad_id, sub.comment) == (EID, ad_id, COMMENT)
        assert sub.applicant == {**APPLICANT, "telegramUserId": "987654321"}
        item = db.get(models.IngestItem, sub.item_id)
        assert (item.platform, item.collector, item.ad_id, item.external_id) == ("tgbot", "telegram_bot", ad_id, EID)
        raws = json.dumps([item.raw] + [v.raw for v in db.scalars(select(models.IngestVersion))], ensure_ascii=False)
        events = [e.what for e in db.scalars(select(models.AuditEvent).where(models.AuditEvent.ad_id == ad_id))]
        assert db.scalar(select(func.count()).select_from(models.Job).where(
            models.Job.kind == "analyze", models.Job.ad_id == ad_id)) == 1
    assert events == ["Публикация прислана жителем через Telegram-бота (Instagram) · заявка жителя"]
    exposed = [r.text, raws, json.dumps(events, ensure_ascii=False), client.get("/api/ads").text,
               client.get(f"/api/ads/{ad_id}").text, client.get("/api/audit").text,
               client.get(f"/api/audit?adId={ad_id}").text, client.get("/api/sources/connectors").text,
               client.get(f"{URL}/submissions/{EID}", headers=KEY).text]
    for value in PII:
        assert not any(value in t for t in exposed), value

    # без площадки и аккаунта - "Другая площадка", аккаунт "не указан"; без заявителя - пустой
    r = post(client, {"externalId": "555:1", "text": "Квартиры в рассрочку"})
    assert r.status_code == 201, r.text
    ad = client.get(f"/api/ads/{r.json()['adId']}").json()
    assert (ad["platform"], ad["account"], ad["url"]) == ("other", "не указан (заявка через бота)", "")
    with SessionLocal() as db:
        sub = db.scalar(select(models.BotSubmission).where(models.BotSubmission.external_id == "555:1"))
        assert (sub.applicant, sub.comment) == ({}, None)


# ---------- 3. повторная доставка и новая версия ----------

def test_redelivery_unchanged_and_new_version(client, bot):
    from app import models

    ad_id = post(client, body()).json()["adId"]
    r = post(client, body(), headers=BEARER)
    assert (r.status_code, r.json()["status"], r.json()["adId"]) == (200, "unchanged", ad_id)
    assert (count(models.Ad, models.Ad.source == "bot"), count(models.BotSubmission), count(models.IngestVersion)) == \
        (1, 1, 1)
    # повторная доставка обновляет сведения заявки, но не публикацию
    r = post(client, body(comment="Уточнение: офис на Казахстан, 61"))
    assert r.json()["status"] == "unchanged"
    r = post(client, body(text="Старт продаж ЖК Ульба Ривер, бронь от 150 000 ₸"))
    assert (r.status_code, r.json()["status"]) == (200, "updated")
    assert (count(models.Ad, models.Ad.source == "bot"), count(models.IngestVersion)) == (1, 2)
    v = client.get(f"/api/ads/{ad_id}/versions").json()
    assert [(x["version"], x["text"]) for x in v] == [(2, "Старт продаж ЖК Ульба Ривер, бронь от 150 000 ₸"),
                                                      (1, "Старт продаж ЖК Ульба Ривер, бронь от 100 000 ₸")]
    assert client.get(f"/api/ads/{ad_id}").json()["text"] == "Старт продаж ЖК Ульба Ривер, бронь от 150 000 ₸"


# ---------- 4. файлы ----------

def test_multipart_files_stored_and_analysis_queued(client, bot):
    from app import models
    from app.db import SessionLocal

    a, b = png_bytes((10, 20, 30)), png_bytes((40, 50, 60))
    r = post_files(client, body(media=[{"key": "AgADxyz", "fileName": "photo.jpg", "mime": "image/jpeg"}]),
                   [("a.png", a, "image/png"), ("b.png", b, "image/png")])
    assert r.status_code == 201, r.text
    ad_id = r.json()["adId"]
    ad = client.get(f"/api/ads/{ad_id}").json()
    assert [m["kind"] for m in ad["media"]] == ["image", "image"]
    assert ad["image"] == ad["media"][0]["url"]
    assert client.get(ad["media"][1]["url"]).content == b
    with SessionLocal() as db:
        assets = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == ad_id)
                            .order_by(models.MediaAsset.position)).all()
        assert [(x.source_key, x.status, x.origin_ref, x.role, x.mime) for x in assets] == [
            ("AgADxyz", "downloaded", "bot", "attachment", "image/png"),
            (bot.content_key(b), "downloaded", "bot", "attachment", "image/png")]  # без описания - ключ по содержимому
        assert bot.content_key(b).startswith("tgbot:sha256:")
        assert all("photo.jpg" not in json.dumps(x.meta or {}) for x in assets)  # имя файла жителя не хранится
        job = db.scalars(select(models.Job).where(models.Job.ad_id == ad_id)).one()
        assert (job.kind, job.status) == ("analyze", "queued")

    # видео MP4 - kind video
    r = post_files(client, body(externalId="555:2"), [("v.mp4", mp4_bytes(), "video/mp4")])
    assert r.status_code == 201, r.text
    assert [m["kind"] for m in client.get(f"/api/ads/{r.json()['adId']}").json()["media"]] == ["video"]

    # описанный, но не переданный файл - "бот не передал файл", не молчаливая потеря
    r = post(client, body(externalId="555:3", media=[{"key": "AgADnofile"}]))
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        x = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == r.json()["adId"])).one()
        assert (x.source_key, x.status, x.error) == ("AgADnofile", "unavailable", "Бот не передал файл")
    # тот же файл в повторной доставке - загружается
    r = post_files(client, body(externalId="555:3", media=[{"key": "AgADnofile"}]),
                   [("c.png", png_bytes((70, 80, 90)), "image/png")])
    assert r.json()["status"] == "unchanged"
    with SessionLocal() as db:
        x = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == r.json()["adId"])).one()
        assert (x.status, x.mime) == ("downloaded", "image/png")


def test_multipart_rejections_save_nothing(client, bot):
    before = snapshot()
    png = ("a.png", png_bytes((1, 2, 3)), "image/png")
    # не изображение и не видео - 415, ни заявки, ни файлов(и первый, подходящий, файл тоже не сохранён)
    r = post_files(client, body(), [png, ("note.txt", b"plain text, not an image", "image/png")])
    assert (r.status_code, r.json()["detail"]) == (415, "Файл №2: неподдерживаемый тип — нужен JPEG, PNG, WEBP, GIF, "
                                                        "MP4 или MOV")
    assert snapshot() == before
    c = connector(client)
    assert (c["status"], c["statusDetail"]) == ("error", "Последняя заявка отклонена: Файл №2: неподдерживаемый тип — "
                                                         "нужен JPEG, PNG, WEBP, GIF, MP4 или MOV")
    # payload больше 1 МБ
    r = post_files(client, body(text="x" * 50, comment="я" * 10, applicant=None) | {"pad": "x" * (1024 * 1024)},
                   [png])
    assert (r.status_code, r.json()["detail"]) == (413, "Поле payload больше 1 МБ или файл передан без имени файла "
                                                        "(filename)")
    # JSON-тело больше 1 МБ
    r = client.post(URL, headers={**KEY, "Content-Type": "application/json"},
                    content=json.dumps(body(text="ж" * 100000)).encode() + b" " * (1024 * 1024))
    assert r.status_code == 413 and "multipart/form-data" in r.json()["detail"]
    # больше 10 файлов, файл не в поле files, нет payload, пустой файл
    r = post_files(client, body(), [("f.png", png_bytes((i, 0, 0)), "image/png") for i in range(11)])
    assert (r.status_code, r.json()["detail"]) == (422, "Не больше 10 файлов в заявке")
    r = post_files(client, body(), [png], field="file")
    assert (r.status_code, r.json()["detail"]) == (422, "Файлы передаются в поле files")
    r = client.post(URL, headers=KEY, files=[("files", png)])
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "payload"]
    r = post_files(client, body(), [("e.png", b"", "image/png")])
    assert (r.status_code, r.json()["detail"]) == (422, "Файл №1 пустой")
    # повтор ключа файла в media
    r = post_files(client, body(media=[{"key": "k"}, {"key": "k"}]), [png])
    assert r.status_code == 422
    # ошибка в payload - loc указывает на поле внутри payload
    r = post_files(client, body(platform="vk"), [png])
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "payload", "platform"]
    assert snapshot() == before


# ---------- 5. поля решений аналитика и проверка заявки ----------

def test_decision_fields_and_validation(client, bot):
    from app import models

    r = post(client, body(status="confirmed", complexId="c1"))
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "поля решений аналитика в заявке не принимаются: status, complexId"
    for bad, field in ((body(source="parser"), None), (body(linkConfirmed=True), None),
                       (body(unknown="x"), "unknown"), (body(submittedAt="2026-10-09T11:10:23"), "submittedAt"),
                       (body(url="javascript:alert(1)"), "url"), (body(externalId="a b"), "externalId"),
                       (body(externalId="x" * 129), "externalId"), (body(platform="vk"), "platform"),
                       (body(text="я" * 100001), "text"),
                       (body(submittedAt=(datetime.now(UTC) + timedelta(hours=1)).isoformat()), "submittedAt"),
                       (body(media=[{"key": str(i)} for i in range(11)]), "media")):
        r = post(client, bad)
        assert r.status_code == 422, (bad, r.text)
        if field:
            assert r.json()["detail"][0]["loc"][:2] == ["body", field], r.json()
    # ответ 422 не возвращает присланные значения: телефон и слова жителя в ответ не попадают
    r = post(client, body(applicant={**APPLICANT, "phone": "+77011234567" + "0" * 30}, comment=COMMENT * 100))
    assert r.status_code == 422 and len(r.json()["detail"]) == 2
    assert not any(v in r.text for v in PII)
    r = post_files(client, body(applicant={**APPLICANT, "name": "Иванова Мария " * 20}),
                   [("a.png", png_bytes((3, 3, 3)), "image/png")])
    assert r.status_code == 422 and not any(v in r.text for v in PII)
    r = client.post(URL, headers={**KEY, "Content-Type": "text/plain"}, content=b"hello")
    assert (r.status_code, r.json()["detail"]) == (415, "Нужен JSON или multipart/form-data")
    assert client.post(URL, headers=KEY, data={"payload": "{}"}).status_code == 415  # urlencoded - не multipart
    assert count(models.Ad, models.Ad.source == "bot") == 0
    # платформа в другом регистре и ID числом - принимаются; текст длиннее предела публикации обрезается
    r = post(client, body(externalId=777, platform=" TikTok ", text="ы" * 30000))
    assert r.status_code == 201, r.text
    assert r.json()["externalId"] == "777"
    ad = client.get(f"/api/ads/{r.json()['adId']}").json()
    assert ad["platform"] == "tiktok" and len(ad["text"]) <= 20000


# ---------- 6. статус заявки для бота ----------

def test_submission_status_and_updates(client, bot):
    eid = "-1001234567890/777"  # ID с "/" - маршрут {external_id:path}
    t_before = datetime.now(UTC) - timedelta(seconds=1)
    ad_id = post(client, body(externalId=eid)).json()["adId"]
    r = client.get(f"{URL}/submissions/{eid}", headers=KEY)
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["externalId"], got["adId"], got["status"], got["statusLabel"]) == (
        eid, ad_id, "received", "Заявка получена и ждёт проверки аналитиком")
    received = datetime.fromisoformat(got["updatedAt"])
    assert client.get(f"{URL}/submissions/nope:1", headers=KEY).json() == {"detail": "Заявка не найдена"}
    assert client.get(f"{URL}/submissions/nope:1", headers=KEY).status_code == 404

    t_mid = datetime.now(UTC)
    r = client.patch(f"/api/ads/{ad_id}/status", json={"status": "in_review", "author": AUTHOR})
    assert r.status_code == 200, r.text
    got = client.get(f"{URL}/submissions/{eid}", headers=BEARER).json()
    assert (got["status"], got["statusLabel"]) == ("in_review", "Заявка на проверке")
    assert datetime.fromisoformat(got["updatedAt"]) >= received

    since = t_mid.astimezone(ALMATY).isoformat()  # "+05:00"
    r = client.get(f"{URL}/updates", headers=KEY, params={"since": since})
    assert r.status_code == 200, r.text
    assert [(x["externalId"], x["status"]) for x in r.json()] == [(eid, "in_review")]
    # "+" без кодирования в query приходит пробелом - время всё равно понятно
    r = client.get(f"{URL}/updates?since={since}", headers=KEY)
    assert [x["externalId"] for x in r.json()] == [eid]
    # получена после since - тоже изменение; после последнего изменения - пусто
    other = post(client, body(externalId="555:9")).json()["adId"]
    r = client.get(f"{URL}/updates", headers=KEY, params={"since": t_before.isoformat()})
    assert [(x["externalId"], x["status"]) for x in r.json()] == [(eid, "in_review"), ("555:9", "received")]
    assert [x["externalId"] for x in client.get(f"{URL}/updates", headers=KEY,
                                                params={"since": t_before.isoformat(), "limit": 1}).json()] == [eid]
    last = max(datetime.fromisoformat(x["updatedAt"]) for x in r.json())
    assert client.get(f"{URL}/updates", headers=KEY, params={"since": last.isoformat()}).json() == []
    assert client.get(f"{URL}/updates", headers=KEY,
                      params={"since": "2026-10-17T12:00:00"}).status_code == 422  # без пояса
    assert client.get(f"{URL}/updates", headers=KEY, params={"since": "вчера"}).status_code == 422
    assert client.get(f"{URL}/updates", headers=KEY,
                      params={"since": since, "limit": 501}).status_code == 422

    # завершение проверки; повторная доставка показывает текущий статус
    r = client.patch(f"/api/ads/{other}/status", json={"status": "dismissed", "author": AUTHOR})
    assert r.status_code == 200
    assert client.get(f"{URL}/submissions/555:9", headers=KEY).json()["statusLabel"] == \
        "Проверка завершена: нарушение не подтверждено"
    client.patch(f"/api/ads/{ad_id}/status", json={"status": "confirmed", "author": AUTHOR,
                                                    "comment": "Нет разрешения МИО на привлечение средств"})
    r = post(client, body(externalId=eid))
    assert (r.json()["status"], r.json()["submissionStatus"], r.json()["statusLabel"]) == (
        "unchanged", "confirmed", "Проверка завершена: признаки нарушения подтверждены аналитиком")
    text = client.get(f"{URL}/updates", headers=KEY, params={"since": t_before.isoformat()}).text
    assert not any(v in text for v in PII)


# ---------- 7. тело больше 60 МБ ----------

def test_body_over_60mb_rejected_and_recorded(client, bot):
    too_big = str(60 * 1024 * 1024 + 1)
    r = client.post(URL, headers={**KEY, "Content-Type": "application/json", "Content-Length": too_big}, content=b"{}")
    assert (r.status_code, r.json()["detail"]) == (413, "Заявка больше 60 МБ")
    c = connector(client)
    assert (c["status"], c["statusDetail"]) == ("error", "Последняя заявка отклонена: Заявка больше 60 МБ")


# ---------- 8. статус подключения ----------

def test_connector_status_lifecycle(client, env, admin_headers):
    from app import models
    from app.db import SessionLocal
    from app.integrations import registry, service

    c = connector(client)
    assert (c["status"], c["statusLabel"], c["contractVerified"], c["kind"], c["group"]) == (
        "not_configured", "не настроен", False, "intake", "intake")
    assert c["limitation"] == "Контракт адаптера saq-telegram-bot с этой версией API не сверен."
    assert c["capabilities"] == {"test": False, "collect": False, "toggle": False, "configure": False, "connect": False}
    env(SERVICE_API_TOKEN=SERVICE_TOKEN)
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["statusDetail"]) == ("waiting", "ожидает заявок",
                                                                   "Заявок через бота ещё не было")
    assert c["configured"] is True and c["secretRefs"] == ["env:SERVICE_API_TOKEN"]
    assert client.post("/api/sources/telegram_bot/test", headers=admin_headers,
                       json={"author": AUTHOR}).status_code == 409  # проверки нет - только приём
    post_files(client, body(), [("a.png", png_bytes((5, 5, 5)), "image/png")])
    c = connector(client)
    assert (c["status"], c["statusLabel"]) == ("working", "принимает заявки")
    assert c["statusDetail"].startswith("Последняя заявка: ")
    assert c["counts"] == {"items": 1, "ads": 1, "media": 1}
    assert registry.get("telegram_bot").contract_verified is False
    with SessionLocal() as db:  # через 8 дней без заявок - "ожидает заявок", время последней видно
        later = service.view(db, ["telegram_bot"], now=models.utcnow() + timedelta(days=8))[0]
    assert (later["status"], later["status_detail"][:17]) == ("waiting", "Последняя заявка:")
    assert registry.get("telegram_bot").computed_status(None, None, None) is None  # smoke без БД


def test_db_error_does_not_leak_applicant(client, bot, monkeypatch, caplog):
    from sqlalchemy.exc import IntegrityError

    def boom(db, pub, **kw):
        raise IntegrityError("INSERT INTO bot_submissions …", {"applicant": json.dumps(APPLICANT)}, Exception("x"))

    monkeypatch.setattr(bot.ingest, "ingest_publication", boom)
    r = post(client, body())
    assert r.status_code == 500
    assert "Заявка не сохранена: ошибка базы данных (IntegrityError)" in caplog.text
    assert not any(v in caplog.text for v in PII) and not any(v in r.text for v in PII)
    c = connector(client)  # сбой сохранения не выглядит как "ожидает заявок"
    assert (c["status"], c["statusDetail"]) == (
        "error", "Последняя заявка отклонена: ошибка базы данных (IntegrityError) — заявка не сохранена, подробности в "
                 "журнале API")
    assert not any(v in client.get("/api/sources/connectors").text for v in PII)


def test_url_dropped_by_ingest_rejected_with_reason(client, bot):
    """Регрессия: ссылку с учётными данными, неверным портом, \\ или кавычками бот принимал(^https?://\\S+$), а приём
    молча сохранял публикацию без ссылки. Теперь - 422 с пояснением жителю, ничего не сохраняется."""
    before = snapshot()
    for url in ("https://krisha.kz@evil.example/a", "https://user:pass@instagram.com/p/1", "https://instagram.com:0/p",
                "https://instagram.com:99999/p", "https://instagram.com\\@evil.example/", 'https://instagram.com/p"x',
                "https://instagram.com/<p>", "https://instagram.com/`p`"):
        r = post(client, body(url=url))
        assert r.status_code == 422, (url, r.text)
        err = r.json()["detail"][0]
        assert err["loc"][:2] == ["body", "url"] and err["msg"].endswith(bot.UNSAFE_LINK), url
    for url in ("https://", "ftp://instagram.com/p", "https://insta gram.com/p", "javascript:alert(1)"):
        r = post(client, body(url=url))
        assert r.status_code == 422 and r.json()["detail"][0]["msg"].endswith(bot.NOT_A_LINK), (url, r.text)
    assert snapshot() == before
    # годная ссылка(и в верхнем регистре схемы) - сохраняется как есть; пробелы по краям и управляющие символы -
    # убираются
    r = post(client, body(url="  HTTPS://www.instagram.com/p/\x01DAbc123/?igsh=1  "))
    assert r.status_code == 201, r.text
    assert client.get(f"/api/ads/{r.json()['adId']}").json()["url"] == "HTTPS://www.instagram.com/p/DAbc123/?igsh=1"


# ---------- 9. сбой сервера виден; файлы без записи в БД не остаются ----------

def test_server_failure_visible_and_files_removed(client, bot, monkeypatch):
    from app import media

    before, real_store, real_ingest = snapshot(), media.store_bytes, bot.ingest.ingest_publication
    files = [("a.png", png_bytes((131, 0, 0)), "image/png"), ("b.png", png_bytes((132, 0, 0)), "image/png")]
    calls = []

    def second_fails(data, **kw):  # первый файл уже на диске, на втором - сбой диска
        calls.append(1)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real_store(data, **kw)

    monkeypatch.setattr(media, "store_bytes", second_fails)
    r = post_files(client, body(), files)
    assert r.status_code == 500
    assert snapshot() == before  # ни заявки, ни первого файла на диске
    c = connector(client)
    assert (c["status"], c["statusLabel"], c["statusDetail"]) == (
        "error", "ошибка", "Последняя заявка отклонена: ошибка сервера (OSError) — заявка не сохранена, подробности в "
                           "журнале API")
    monkeypatch.setattr(media, "store_bytes", real_store)

    def boom(db, pub, **kw):  # файлы записаны, приём упал - транзакция откатана, файлы убраны
        raise RuntimeError("Публикацию одновременно меняют — новая версия не записана, повторите приём")

    monkeypatch.setattr(bot.ingest, "ingest_publication", boom)
    assert post_files(client, body(), files).status_code == 500
    assert snapshot() == before
    # файл, который уже был в хранилище до запроса(его мог только что записать другой запрос), не удаляется
    other = media.store_bytes(png_bytes((133, 0, 0)))
    assert post_files(client, body(), [("c.png", png_bytes((133, 0, 0)), "image/png")]).status_code == 500
    assert media.path_of(other.storage_key).exists()
    media.path_of(other.storage_key).unlink()
    monkeypatch.setattr(bot.ingest, "ingest_publication", real_ingest)

    # принятая заявка новее сбоя - "принимает заявки"; файлы на месте
    r = post_files(client, body(), files)
    assert r.status_code == 201, r.text
    ad = client.get(f"/api/ads/{r.json()['adId']}").json()
    assert [client.get(m["url"]).content for m in ad["media"]] == [f[1] for f in files]
    assert connector(client)["status"] == "working"


def test_file_written_by_parallel_request_not_removed(client, bot, monkeypatch):
    """Регрессия гонки exists()/запись: параллельный запрос с теми же байтами записал файл между проверкой и записью
    этого - неудача этого запроса его файл не удаляет(на него сошлётся запись параллельного)."""
    import hashlib
    import os
    from pathlib import Path

    from app import media

    data, real_link = png_bytes((134, 0, 0)), os.link

    def parallel(src, dst, **kw):
        Path(dst).write_bytes(data)
        return real_link(src, dst, **kw)

    def boom(db, pub, **kw):
        raise RuntimeError("сбой приёма")

    monkeypatch.setattr(media.os, "link", parallel)
    monkeypatch.setattr(bot.ingest, "ingest_publication", boom)
    assert post_files(client, body(), [("a.png", data, "image/png")]).status_code == 500
    assert media.path_of(media.storage_key(hashlib.sha256(data).hexdigest(), "image/png")).read_bytes() == data


def test_file_removed_by_parallel_failure_is_restored(client, bot, monkeypatch):
    """Параллельный неудачный запрос с теми же байтами удалил файл до commit этого - сохранённая заявка записывает файл
    снова, ссылка на него не битая."""
    from app import media

    data, real = png_bytes((141, 0, 0)), bot.ingest.ingest_publication

    def racing(db, pub, **kw):
        for m in pub.media:
            if m.stored is not None:
                media.path_of(m.stored.storage_key).unlink()
        return real(db, pub, **kw)

    monkeypatch.setattr(bot.ingest, "ingest_publication", racing)
    r = post_files(client, body(), [("a.png", data, "image/png")])
    assert r.status_code == 201, r.text
    ad = client.get(f"/api/ads/{r.json()['adId']}").json()
    assert client.get(ad["media"][0]["url"]).content == data


# ---------- 10. ключи файлов по содержимому; повторная доставка без файлов ----------

def test_file_keys_follow_content(client, bot):
    from app import models

    a, b = png_bytes((151, 0, 0)), png_bytes((152, 0, 0))
    r = post_files(client, body(), [("a.png", a, "image/png"), ("a2.png", a, "image/png")])
    assert r.status_code == 201, r.text
    ad_id = r.json()["adId"]
    ad = client.get(f"/api/ads/{ad_id}").json()
    assert len(ad["media"]) == 1  # один и тот же файл дважды - один файл
    assert post_files(client, body(), [("a.png", a, "image/png")]).json()["status"] == "unchanged"

    # повторная доставка без files и без media(JSON) - файлы прежней версии остаются, новой версии нет
    r = post(client, body())
    assert (r.status_code, r.json()["status"]) == (200, "unchanged")
    assert client.get(f"/api/ads/{ad_id}").json()["image"] == ad["image"]
    # изменилась только подпись - новая версия, фото по-прежнему у публикации
    r = post(client, body(text="Старт продаж, бронь от 150 000 ₸"))
    assert r.json()["status"] == "updated"
    now = client.get(f"/api/ads/{ad_id}").json()
    assert (now["image"], [m["id"] for m in now["media"]]) == (ad["image"], [m["id"] for m in ad["media"]])
    assert [x["mediaKeys"] for x in client.get(f"/api/ads/{ad_id}/versions").json()] == [[bot.content_key(a)]] * 2

    # житель заменил фото в сообщении(тот же externalId) - новая версия с новым файлом, прежний - в истории
    r = post_files(client, body(text="Старт продаж, бронь от 150 000 ₸"), [("b.png", b, "image/png")])
    assert (r.status_code, r.json()["status"]) == (200, "updated")
    now = client.get(f"/api/ads/{ad_id}").json()
    assert len(now["media"]) == 1 and client.get(now["media"][0]["url"]).content == b
    assert now["image"] == now["media"][0]["url"]
    assert count(models.MediaAsset, models.MediaAsset.ad_id == ad_id) == 2
    assert count(models.IngestVersion) == 3

    # тот же ключ media с другими байтами(бот нарушил контракт ключа) - без изменений, лишний файл не остаётся на диске
    eid = "555:7"
    assert post_files(client, body(externalId=eid, media=[{"key": "AgADsame"}]),
                      [("c.png", png_bytes((153, 0, 0)), "image/png")]).status_code == 201
    on_disk = stored_files()
    r = post_files(client, body(externalId=eid, media=[{"key": "AgADsame"}]),
                   [("d.png", png_bytes((154, 0, 0)), "image/png")])
    assert r.json()["status"] == "unchanged"
    assert stored_files() == on_disk


# ---------- 11. часть files без имени файла, лишние поля формы ----------

def _multipart(*parts: tuple) -> tuple[bytes, dict]:
    """Тело multipart вручную: части(name, filename или None, content-type или None, bytes)."""
    out = b""
    for name, filename, ctype, data in parts:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else "")
        out += b"--bnd123\r\nContent-Disposition: " + disp.encode() + b"\r\n"
        out += (f"Content-Type: {ctype}\r\n".encode() if ctype else b"") + b"\r\n" + data + b"\r\n"
    return out + b"--bnd123--\r\n", {**KEY, "Content-Type": "multipart/form-data; boundary=bnd123"}


def test_multipart_file_without_filename_rejected(client, bot):
    before = snapshot()
    payload = ("payload", None, None, json.dumps(body()).encode())
    # curl -F "files=<a.png" - файл без filename: Starlette отдаёт его строкой, молча терять его нельзя
    content, headers = _multipart(payload, ("files", None, "image/png", png_bytes((161, 0, 0))))
    r = client.post(URL, headers=headers, content=content)
    assert (r.status_code, r.json()["detail"]) == (422, "Файлы передаются в поле files с именем файла (filename)")
    c = connector(client)
    assert (c["status"], c["statusDetail"]) == ("error", "Последняя заявка отклонена: Файлы передаются в поле files с "
                                                         "именем файла (filename)")
    # такой же файл больше 1 МБ - 413 с подсказкой про filename
    content, headers = _multipart(payload, ("files", None, "image/png", b"\x89PNG" + b"0" * (1024 * 1024)))
    r = client.post(URL, headers=headers, content=content)
    assert r.status_code == 413 and "без имени файла (filename)" in r.json()["detail"]
    # лишнее текстовое поле и payload дважды - не игнорируются молча
    content, headers = _multipart(payload, ("caption", None, None, "Подпись".encode()))
    r = client.post(URL, headers=headers, content=content)
    assert (r.status_code, r.json()["detail"]) == (422, "Лишние поля формы: нужны только payload и files")
    content, headers = _multipart(payload, payload)
    r = client.post(URL, headers=headers, content=content)
    assert (r.status_code, r.json()["detail"]) == (422, "Поле payload передано несколько раз")
    assert snapshot() == before
    # с filename - принимается
    content, headers = _multipart(payload, ("files", "a.png", "image/png", png_bytes((161, 0, 0))))
    r = client.post(URL, headers=headers, content=content)
    assert r.status_code == 201, r.text
    assert len(client.get(f"/api/ads/{r.json()['adId']}").json()["media"]) == 1


# ---------- 12. "нет доступа" снимается верным токеном; заявитель при повторе не стирается ----------

def test_token_reject_cleared_by_successful_poll(client, bot):
    since = {"since": "2026-10-17T00:00:00Z"}
    assert post(client, body()).status_code == 201
    assert client.get(f"{URL}/updates", headers={"X-API-Key": WRONG_TOKEN}, params=since).status_code == 403
    assert connector(client)["status"] == "denied"
    # бот исправлен: опрос с верным токеном - не "нет доступа", хотя новых заявок нет
    assert client.get(f"{URL}/updates", headers=KEY, params=since).status_code == 200
    c = connector(client)
    assert (c["status"], c["statusDetail"][:17]) == ("working", "Последняя заявка:")
    assert client.get(f"{URL}/submissions/{EID}", headers=BEARER).status_code == 200  # повторная отметка не нужна
    assert connector(client)["status"] == "working"
    # снова неверный токен(запрос без токена) - снова "нет доступа"; верный опрос статуса заявки его снимает
    assert client.get(f"{URL}/submissions/{EID}").status_code == 401
    assert connector(client)["status"] == "denied"
    assert client.get(f"{URL}/submissions/nope:1", headers=KEY).status_code == 404
    assert connector(client)["status"] == "working"
    # опрос с верным токеном не скрывает отклонённую заявку: "ошибка" остаётся
    assert post(client, body(platform="vk")).status_code == 422
    assert client.get(f"{URL}/updates", headers=KEY, params=since).status_code == 200
    assert connector(client)["status"] == "error"


def test_redelivery_keeps_applicant_and_comment(client, bot):
    from app import models
    from app.db import SessionLocal

    assert post(client, body()).status_code == 201
    # повторная доставка: заявитель без телефона, комментарий null - прежние сведения не стираются
    r = post(client, body(applicant={"telegramUserId": 987654321, "username": "", "name": "Иванова М."}) |
             {"comment": None})
    assert r.json()["status"] == "unchanged"
    with SessionLocal() as db:
        sub = db.scalars(select(models.BotSubmission)).one()
        assert sub.applicant == {**APPLICANT, "telegramUserId": "987654321", "name": "Иванова М."}
        assert sub.comment == COMMENT
    # новый непустой комментарий - заменяет прежний
    post(client, body(comment="Уточнение: офис на Казахстан, 61"))
    with SessionLocal() as db:
        assert db.scalars(select(models.BotSubmission)).one().comment == "Уточнение: офис на Казахстан, 61"


def test_auth_runs_off_event_loop(client, bot, monkeypatch):
    """Проверка токена пишет отказ в БД - она выполняется в пуле потоков, а не в цикле событий."""
    import asyncio

    from app import auth

    real, where = auth.require_service, []

    def spy(request):
        try:
            asyncio.get_running_loop()
            where.append("loop")
        except RuntimeError:
            where.append("thread")
        return real(request)

    monkeypatch.setattr(auth, "require_service", spy)
    assert post(client, body(), headers={"X-API-Key": WRONG_TOKEN}).status_code == 403
    assert post(client, body()).status_code == 201
    assert where == ["thread", "thread"]


# ---------- приёмочные проверки подключения ----------

def test_acceptance_repeat_no_duplicate(client, bot, run_worker, accept, ocr_stub, http_mock):
    files = [("a.png", png_bytes((11, 0, 0)), "image/png"), ("b.png", png_bytes((12, 0, 0)), "image/png")]
    assert post_files(client, body(), files).status_code == 201
    run_worker()
    before, stored = accept.snapshot(), stored_files()
    r = post_files(client, body(), files)
    assert (r.status_code, r.json()["status"]) == (200, "unchanged")
    run_worker()
    assert accept.snapshot() == before and stored_files() == stored
    assert http_mock.calls == []  # файлы пришли от бота - внешних загрузок нет


def test_acceptance_failure_not_empty(client, bot, accept):
    r = post(client, body(), headers={"Authorization": f"Bearer {WRONG_TOKEN}"})
    assert r.status_code == 403
    assert accept.assert_failure_visible("telegram_bot")["status"] == "denied"
    assert post(client, body(platform="vk")).status_code == 422
    c = accept.assert_failure_visible("telegram_bot")
    assert c["status"] == "error" and c["statusDetail"].startswith("Последняя заявка отклонена: body.platform")


def test_acceptance_media_survives_source(client, bot, accept):
    data = png_bytes((21, 0, 0))
    ad_id = post_files(client, body(), [("a.png", data, "image/png")]).json()["adId"]
    asset = client.get(f"/api/ads/{ad_id}").json()["media"][0]["id"]
    assert accept.media_ok(asset) == data  # файл в хранилище сервиса, не ссылка на Telegram


def test_acceptance_not_confirmed_automatically(client, bot, run_worker, accept, ocr_stub):
    ad_id = post(client, body()).json()["adId"]  # текст называет демо-ЖК "Ульба Ривер"
    run_worker()
    accept.assert_not_confirmed(ad_id)
    r = client.post(f"/api/ads/{ad_id}/hypotheses/accept", json={"author": AUTHOR, "complexId": "c3"})
    assert r.status_code == 200, r.text
    assert (r.json()["complexId"], r.json()["linkConfirmedBy"]) == ("c3", AUTHOR)


def test_acceptance_history_kept(client, bot):
    from app import models

    ad_id = post(client, body()).json()["adId"]
    assert post(client, body(text="Изменённый текст заявки")).json()["status"] == "updated"
    assert count(models.IngestVersion) == 2
    v = client.get(f"/api/ads/{ad_id}/versions").json()
    assert [x["text"] for x in v] == ["Изменённый текст заявки", "Старт продаж ЖК Ульба Ривер, бронь от 100 000 ₸"]
    assert [x["current"] for x in v] == [True, False]
