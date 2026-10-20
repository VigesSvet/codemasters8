"""Общая запись публикации(integration-spec §7.1, §16.2 test_ads_service): ручной ввод, собранная, заявка бота."""
import os
import re
from datetime import datetime, timedelta, UTC

import pytest

from conftest import png_bytes

from app import ads_service, media, models, schemas, world
from app.ads_service import BOT, COLLECTOR, AdExists, ComplexNotFound
from app.ai.matching import build_catalog
from app.db import SessionLocal
from app.risk_model import ALMATY

AUTHOR = "Омарова Ж."
TEXT = "Старт продаж ЖК Ульба Ривер, бронь"


def _events(db, ad_id):
    E = models.AuditEvent
    return [(e.kind, e.who, e.what, e.complex_id) for e in db.query(E).filter(E.ad_id == ad_id).order_by(E.id)]


def test_manual_path_same_as_route(client):
    with SessionLocal() as db:
        ad = ads_service.create_ad(db, {"platform": "instagram", "account": "@x", "text": TEXT, "author": AUTHOR},
                                   actor=ads_service.analyst(AUTHOR))
        db.commit()
        assert re.fullmatch(r"m[0-9a-f]{8}", ad.id) and (ad.origin, ad.source, ad.status) == ("analyst", "manual", "new")
        assert ad.complex_id == "c3" and ad.match_confidence > 0 and ad.link_confirmed_by is None
        assert [e[:2] for e in _events(db, ad.id)] == [("system", AUTHOR), ("ai", "Система")]
        assert _events(db, ad.id)[0][2] == "Публикация добавлена вручную"
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", ad.date)
    # тот же ввод через POST /api/ads - то же поведение
    r = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": TEXT, "author": AUTHOR}).json()
    assert (r["complexId"], r["origin"], r["matchConfidence"]) == ("c3", "analyst", ad.match_confidence)
    assert "collectedAt" not in r and "media" not in r and "processing" not in r  # JSON ручной публикации не изменился


def test_manual_errors(client):
    with SessionLocal() as db:
        with pytest.raises(AdExists) as e:
            ads_service.create_ad(db, {"id": "a1", "platform": "instagram", "account": "@x", "text": "t"},
                                  actor=ads_service.analyst(None))
        assert e.value.ad_id == "a1"
        with pytest.raises(ComplexNotFound) as e:
            ads_service.create_ad(db, {"platform": "instagram", "account": "@x", "text": "t", "complex_id": "nope"},
                                  actor=ads_service.analyst(AUTHOR))
        assert e.value.complex_id == "nope"
        assert db.query(models.Ad).filter(models.Ad.origin == "analyst").count() == 0
    assert client.post("/api/ads", json={"platform": "x", "account": "@x", "text": "t", "complexId": "nope"}).status_code == 404
    assert ads_service.analyst("  ").who == "  " and ads_service.analyst(None).who == "Система"


def test_collected_path(client):
    at = datetime(2026, 10, 16, 8, 8, 10, tzinfo=UTC)
    fields = {"platform": "tiktok", "account": "@tumar\x00group", "url": "javascript:alert(1)",
              "text": "ЖК Ульба Ривер\x00 " + "слово " * 6000, "date": ads_service.local_minutes(
                  datetime(2026, 10, 9, 11, 10, 23, tzinfo=UTC)),
              # решения аналитика и происхождение из внешнего ввода не принимаются
              "status": "confirmed", "complex_id": "c3", "match_confidence": 0.99, "link_confirmed": True,
              "link_confirmed_by": "Хакер", "assignee": "Хакер", "terms_review": "accepted", "rejected_links": ["c1"],
              "origin": "demo", "source": "manual", "id": "evil1", "decision_rev": 99, "unknown_field": 1}
    with SessionLocal() as db:
        ad = ads_service.create_ad(db, fields, actor=COLLECTOR, at=at, note="приём публикаций API")
        db.commit()
        assert re.fullmatch(r"[0-9a-f]{32}", ad.id)
        assert (ad.origin, ad.source, ad.status, ad.complex_id, ad.match_confidence) == ("collected", "parser", "new",
                                                                                         None, 0)
        assert (ad.link_confirmed_by, ad.assignee, ad.terms_review, ad.rejected_links, ad.decision_rev) == (
            None, None, None, [], 0)
        assert ad.account == "@tumargroup" and "\x00" not in ad.text and len(ad.text) <= 20000
        assert ad.url == "" and ad.date == "2026-10-09T16:10"
        assert models.as_utc(ad.collected_at) == at
        assert _events(db, ad.id) == [("system", "Система", "Публикация собрана автоматически (TikTok) · приём публикаций "
                                                            "API", None)]
        e = db.query(models.AuditEvent).filter(models.AuditEvent.ad_id == ad.id).one()
        assert models.as_utc(e.at) == at
        # без даты - местное время получения; без платформы - "other"
        ad2 = ads_service.create_ad(db, {"text": "x"}, actor=COLLECTOR, at=at)
        assert (ad2.platform, ad2.date, ad2.account, ad2.url) == ("other", "2026-10-16T13:08", "", "")
        with pytest.raises(AdExists):
            ads_service.create_ad(db, {"text": "x"}, actor=COLLECTOR, ad_id=ad.id)


def test_bot_actor_and_match_obj(client):
    with SessionLocal() as db:
        ad = ads_service.create_ad(db, {"platform": "instagram", "account": "@altai.build",
                                        "url": "https://instagram.com/p/x", "text": TEXT}, actor=BOT, note="заявка жителя")
        db.commit()
        assert (ad.origin, ad.source) == ("collected", "bot")
        assert _events(db, ad.id)[0][2] == "Публикация прислана жителем через Telegram-бота (Instagram) · заявка жителя"
        assert ads_service.match_obj(ad) == {"text": TEXT, "ocr": None, "account": None, "url": None}
        manual = db.get(models.Ad, "a1")
        assert ads_service.match_obj(manual)["account"] == manual.account


def test_bot_claims_not_in_account_history(client):
    with SessionLocal() as db:
        w = world.load(db)
    claim = {"id": "b1", "account": "@resident_claim", "complexId": "c1", "linkConfirmedBy": AUTHOR, "status": "new"}
    cat = build_catalog(w.complexes, w.developers.values(), [{**claim, "source": "bot"}])
    assert "resident_claim" not in cat.history
    cat = build_catalog(w.complexes, w.developers.values(), [{**claim, "source": "parser"}])
    assert cat.history["resident_claim"] == {"c1": 1}


def test_sanitize_and_safe_url():
    assert ads_service.safe_url("HTTPS://x.kz/a") == "HTTPS://x.kz/a"
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x", "https://x.kz/a b", "https://x/" + "a" * 600, None,
                # без хоста, "\"(браузер читает как "/" - другой хост, чем у urlsplit), незакодированные < > " `
                "https:///path", "https://", "https://good.kz\\@evil.kz/", 'https://x.kz/"onmouseover=1',
                "https://x.kz/<script>", "https://x.kz/`x`", "https://[::1/a", 12,
                # учётные данные(виден один хост, ведёт на другой), неверный порт
                "https://krisha.kz@evil.kz/a", "https://u:p@x.kz/", "https://x.kz:99999/", "https://x.kz:abc/",
                "https://x.kz:0/"):
        assert ads_service.safe_url(bad) == "", bad
    assert ads_service.safe_url("  https://krisha.kz/a/show/1?x=1#y \n") == "https://krisha.kz/a/show/1?x=1#y"
    for ok in ("https://www.tiktok.com/@ulba.river/video/1", "https://x.kz:8443/a?u=@b"):  # "@" вне хоста - можно
        assert ads_service.safe_url(ok) == ok
    out = ads_service.sanitize_collected({"text": "a\x00b\x07c\n\td", "price": " 58 194 600 ₸ ", "reach": -5,
                                          "account": "x" * 300, "status": "dismissed"})
    assert out == {"text": "abc\n\td", "price": "58 194 600 ₸", "reach": None, "account": "x" * 255}
    assert ads_service.sanitize_collected({"reach": True})["reach"] is None
    assert ads_service.sanitize_collected({"reach": 12})["reach"] == 12


def test_manual_image_attaches_upload(client):
    data = png_bytes((33, 44, 55))
    s = media.store_bytes(data)
    ids = [os.urandom(16).hex() for _ in range(3)]
    with SessionLocal() as db:
        for aid, origin, ad_id in zip(ids, ("upload", "upload", "intake"), (None, "a1", None), strict=True):
            db.add(models.MediaAsset(id=aid, kind="image", status="downloaded", origin_ref=origin, ad_id=ad_id,
                                     storage_key=s.storage_key, sha256=s.sha256, size=s.size, mime=s.mime))
        db.commit()
    free, taken, other = ids
    r = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "t", "image": f"/api/media/{free}"})
    assert r.status_code == 201
    ad = r.json()
    assert ad["image"] == f"/api/media/{free}"
    assert ad["media"] == [{"id": free, "kind": "image", "mime": "image/png", "url": f"/api/media/{free}", "role": None}]
    for ref in (taken, other):  # чужая(уже привязанная) и не ручная загрузка не забираются
        r = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "t",
                                          "image": f"/api/media/{ref}"}).json()
        assert "media" not in r
    with SessionLocal() as db:
        owners = {a.id: a.ad_id for a in db.query(models.MediaAsset)}
    assert owners == {free: ad["id"], taken: "a1", other: None}


def _manual(client, **kw):
    return client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "Продаю гараж", **kw})


def _rejected(r, field: str) -> str:
    assert r.status_code == 422, r.text
    (d,) = r.json()["detail"]
    assert d["loc"] == ["body", field], d
    return d["msg"]


def test_manual_url_and_image_are_safe_links(client):
    """Ручной ввод открыт без входа: ссылка и изображение показываются и открываются во фронтенде и PDF, поэтому
    javascript:, data:, подмена хоста через учётные данные и адрес не из хранилища - 422(у собранных - safe_url → "")."""
    count = len(client.get("/api/ads").json())
    for bad in ("javascript:alert(1)", "data:text/html,x", "https://krisha.kz@evil.kz/a", "https://", "ftp://x.kz/a",
                "https://x.kz/a b", "https://x.kz:0/", "https://good.kz\\@evil.kz/", "https://a\x01b"):
        assert _rejected(_manual(client, url=bad), "url").startswith("нужна ссылка http(s)://"), bad
    assert _rejected(_manual(client, url="https://x/" + "a" * 600), "url")  # длиннее столбца БД
    for bad in ("javascript:alert(1)", "data:image/png;base64,AAAA", "blob:http://localhost/1", "/api/media/../x",
                "/api/media/" + "A" * 32, "/img/a.png", "https://u:p@cdn.kz/a.png"):
        assert _rejected(_manual(client, image=bad), "image").startswith("нужен загруженный скриншот"), bad
    assert len(client.get("/api/ads").json()) == count  # ничего не записано
    r = _manual(client, url="  HTTPS://instagram.com/p/x?u=@b  ", image=" https://cdn.example.com/a.png ")
    assert r.status_code == 201, r.text
    assert (r.json()["url"], r.json()["image"]) == ("HTTPS://instagram.com/p/x?u=@b", "https://cdn.example.com/a.png")
    r = _manual(client, url="", image="  ")
    assert r.status_code == 201 and r.json()["url"] == "" and r.json().get("image") is None
    ref = "/api/media/" + os.urandom(16).hex()  # не загруженный файл - адрес допустим, но ничего не привязывает
    r = _manual(client, image=ref)
    assert r.status_code == 201 and r.json()["image"] == ref and "media" not in r.json()


def test_manual_date_normalized_like_collected(client):
    """Дата ручного ввода - как у собранных: момент → местное время "ГГГГ-ММ-ДДTЧЧ:ММ", день - днём; с 2000 года и не
    позже суток вперёд: дата из будущего держала бы решение аналитика по ЖК снятым бесконечно."""
    now = datetime.now(UTC).replace(microsecond=0)
    today = ads_service.local_minutes(now)[:10]
    cases = {
        # фронтенд(new Date().toISOString()): UTC с миллисекундами
        now.isoformat(timespec="milliseconds").replace("+00:00", "Z"): ads_service.local_minutes(now),
        "2026-10-12T10:00:30": "2026-10-12T10:00",  # без пояса - уже местное время
        "2026-10-12 10:00": "2026-10-12T10:00",
        "2026-10-12T10:00+0300": "2026-10-12T12:00",
        "2026-10-12T05:00:00.5-00:00": "2026-10-12T10:00",
        f" {today} ": today,
        "2000-01-01": "2000-01-01",
        (now + timedelta(hours=20)).isoformat(): ads_service.local_minutes(now + timedelta(hours=20)),
    }
    for given, stored in cases.items():
        r = _manual(client, date=given)
        assert r.status_code == 201, (given, r.text)
        assert r.json()["date"] == stored, given
    for empty in ("", "  ", None):  # не указана - время добавления
        r = _manual(client, date=empty)
        assert r.status_code == 201 and r.json()["date"][:10] == today, empty
    for bad in ("12.10.2026", "2026-02-30", "2026-10-12T25:00", "2026-10-12T10", "2026-W42-1", "20261012",
                "٢٠٢٦-١٠-١٢", "2026-10-12T10:00+24:00", "сегодня"):
        assert _rejected(_manual(client, date=bad), "date") == schemas.AD_DATE_MSG, bad
    for bad in ("1999-12-31", "0001-01-01T00:00+05:00", "2000-01-01T00:00+05:01"):
        assert _rejected(_manual(client, date=bad), "date") == "дата публикации раньше 2000 года", bad
    for bad in ("9999-12-31", "9999-12-31T23:59-12:00", (now + timedelta(days=2)).isoformat(),
                (datetime.now(ALMATY).date() + timedelta(days=2)).isoformat()):
        assert _rejected(_manual(client, date=bad), "date") == "дата публикации в будущем — проверьте дату", bad
    assert _rejected(_manual(client, date=20261012), "date")
