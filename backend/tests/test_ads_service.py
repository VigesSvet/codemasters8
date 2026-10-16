"""Общая запись публикации (integration-spec §7.1, §16.2 test_ads_service): ручной ввод, собранная, заявка бота."""
import os
import re
from datetime import datetime, timezone

import pytest

from conftest import png_bytes

from app import ads_service, media, models, world
from app.ads_service import BOT, COLLECTOR, AdExists, ComplexNotFound
from app.ai.matching import build_catalog
from app.db import SessionLocal

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
    # тот же ввод через POST /api/ads — то же поведение
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
    at = datetime(2026, 10, 16, 8, 8, 10, tzinfo=timezone.utc)
    fields = {"platform": "tiktok", "account": "@tumar\x00group", "url": "javascript:alert(1)",
              "text": "ЖК Ульба Ривер\x00 " + "слово " * 6000, "date": ads_service.local_minutes(
                  datetime(2026, 10, 9, 11, 10, 23, tzinfo=timezone.utc)),
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
        # без даты — местное время получения; без платформы — «other»
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
    for bad in ("javascript:alert(1)", "data:text/html,x", "ftp://x", "https://x.kz/a b", "https://x/" + "a" * 600, None):
        assert ads_service.safe_url(bad) == "", bad
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
        for aid, origin, ad_id in zip(ids, ("upload", "upload", "intake"), (None, "a1", None)):
            db.add(models.MediaAsset(id=aid, kind="image", status="downloaded", origin_ref=origin, ad_id=ad_id,
                                     storage_key=s.storage_key, sha256=s.sha256, size=s.size, mime=s.mime))
        db.commit()
    free, taken, other = ids
    r = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "t", "image": f"/api/media/{free}"})
    assert r.status_code == 201
    ad = r.json()
    assert ad["image"] == f"/api/media/{free}"
    assert ad["media"] == [{"id": free, "kind": "image", "mime": "image/png", "url": f"/api/media/{free}", "role": None}]
    for ref in (taken, other):  # чужая (уже привязанная) и не ручная загрузка не забираются
        r = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "t",
                                          "image": f"/api/media/{ref}"}).json()
        assert "media" not in r
    with SessionLocal() as db:
        owners = {a.id: a.ad_id for a in db.query(models.MediaAsset)}
    assert owners == {free: ad["id"], taken: "a1", other: None}
