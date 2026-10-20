"""Приём публикаций(integration-spec §5, §7.3, §7.4, §16.2 test_intake, §16.5 столбец A): доступ по токенам, единый
приём ingest, маршрут POST /api/intake/publications(422, 413, 409, статус подключения), загрузка файлов по ссылкам
заданием анализа и приёмка по §14 плана."""
import hashlib
import json
import re
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from conftest import FAKE_ADMIN_TOKEN, mp4_bytes, png_bytes

from app import auth, ingest, media, models
from app.ads_service import BOT, COLLECTOR
from app.db import SessionLocal
from app.integrations.base import MediaIn, Publication

INGEST_TOKEN = "t-ingest-abcdefghijklmnop0123"
SERVICE_TOKEN = "t-service-qrstuvwxyz98765432"
AUTHOR = "Омарова Ж."


# ---------- доступ ----------

@pytest.fixture
def guarded():
    app = FastAPI()

    @app.get("/admin")
    def admin(a: auth.Admin = Depends(auth.require_admin)):
        return {"fp": a.fp}

    @app.get("/ingest", dependencies=[Depends(auth.require_ingest)])
    def ingest_route():
        return {"ok": True}

    @app.get("/service", dependencies=[Depends(auth.require_service)])
    def service_route():
        return {"ok": True}

    return TestClient(app)


@pytest.fixture
def rejects(monkeypatch):
    """Подменный service.record_reject: список(code, status, detail)."""
    import app.integrations as pkg

    got = []
    fake = types.ModuleType("app.integrations.service")
    fake.record_reject = lambda code, status, detail: got.append((code, status, detail))
    monkeypatch.setitem(sys.modules, "app.integrations.service", fake)
    monkeypatch.setattr(pkg, "service", fake, raising=False)
    return got


def test_auth_not_configured_is_503(guarded, env, rejects):
    env(SAQ_ADMIN_TOKEN=None, INTEGRATION_INGEST_TOKEN=None, SERVICE_API_TOKEN=None)
    for path, detail in (("/admin", auth.ADMIN_UNSET), ("/ingest", auth.INGEST_UNSET), ("/service", auth.SERVICE_UNSET)):
        r = guarded.get(path, headers={"Authorization": "Bearer whatever-token-value-123456"})
        assert (r.status_code, r.json()["detail"]) == (503, detail), path
    # слабый токен на сервере - то же, что не задан
    env(INTEGRATION_INGEST_TOKEN="short")
    assert guarded.get("/ingest", headers={"Authorization": "Bearer short"}).status_code == 503
    # токен администратора, совпадающий с сервисным, не действует
    env(SAQ_ADMIN_TOKEN=SERVICE_TOKEN, SERVICE_API_TOKEN=SERVICE_TOKEN)
    assert guarded.get("/admin", headers={"X-Admin-Token": SERVICE_TOKEN}).status_code == 503
    assert rejects == []  # "не настроено" - не отказ клиента


def test_auth_matrix(guarded, env, rejects):
    env(SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN, INTEGRATION_INGEST_TOKEN=INGEST_TOKEN, SERVICE_API_TOKEN=SERVICE_TOKEN)
    # администратор: X-Admin-Token или Bearer
    r = guarded.get("/admin")
    assert (r.status_code, r.json()["detail"], r.headers["www-authenticate"]) == (401, auth.ADMIN_MISSING, "Bearer")
    assert guarded.get("/admin", headers={"X-Admin-Token": "wrong"}).json()["detail"] == auth.ADMIN_WRONG
    assert guarded.get("/admin", headers={"X-Admin-Token": "wrong"}).status_code == 403
    r = guarded.get("/admin", headers={"X-Admin-Token": FAKE_ADMIN_TOKEN})
    assert r.json() == {"fp": hashlib.sha256(FAKE_ADMIN_TOKEN.encode()).hexdigest()[:12]}
    assert guarded.get("/admin", headers={"authorization": f"bearer {FAKE_ADMIN_TOKEN}"}).status_code == 200
    assert guarded.get("/admin", headers={"Authorization": f"Basic {FAKE_ADMIN_TOKEN}"}).status_code == 401
    assert guarded.get("/admin", headers={"X-Admin-Token": INGEST_TOKEN}).status_code == 403  # чужой токен
    # приём публикаций: только Authorization: Bearer
    assert guarded.get("/ingest").status_code == 401
    assert guarded.get("/ingest", headers={"X-API-Key": INGEST_TOKEN}).status_code == 401
    assert guarded.get("/ingest", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert guarded.get("/ingest", headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}).status_code == 403
    assert guarded.get("/ingest", headers={"Authorization": f"Bearer {INGEST_TOKEN}"}).status_code == 200
    # бот заявок: Bearer или X-API-Key
    assert guarded.get("/service", headers={"X-API-Key": SERVICE_TOKEN}).status_code == 200
    assert guarded.get("/service", headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}).status_code == 200
    r = guarded.get("/service", headers={"X-API-Key": "nope"})
    assert (r.status_code, r.json()["detail"]) == (403, auth.SERVICE_WRONG)
    assert guarded.get("/service").json()["detail"] == auth.SERVICE_MISSING
    # отказы клиента приёма и бота записываются(без токена); отказы администратору - нет
    assert rejects == [("intake", 401, auth.INGEST_MISSING), ("intake", 401, auth.INGEST_MISSING),
                       ("intake", 403, auth.INGEST_WRONG), ("intake", 403, auth.INGEST_WRONG),
                       ("telegram_bot", 403, auth.SERVICE_WRONG), ("telegram_bot", 401, auth.SERVICE_MISSING)]
    assert not any(t in json.dumps(rejects, ensure_ascii=False) for t in (INGEST_TOKEN, SERVICE_TOKEN, "nope"))


def test_reject_recording_failure_does_not_change_response(guarded, env, monkeypatch, caplog):
    import app.integrations as pkg

    fake = types.ModuleType("app.integrations.service")

    def boom(*a):
        raise RuntimeError("БД недоступна")

    fake.record_reject = boom
    monkeypatch.setitem(sys.modules, "app.integrations.service", fake)
    monkeypatch.setattr(pkg, "service", fake, raising=False)
    env(INTEGRATION_INGEST_TOKEN=INGEST_TOKEN)
    assert guarded.get("/ingest", headers={"Authorization": "Bearer nope"}).status_code == 403
    assert "не записан" in caplog.text and INGEST_TOKEN not in caplog.text


# ---------- единый приём ----------

PUBLISHED = datetime(2026, 10, 9, 11, 10, 23, tzinfo=timezone.utc)
EID = "7694623823408188693"


def pub(**kw) -> Publication:
    data = {"platform": "tiktok", "external_id": EID, "account": "@tumargroup",
            "url": f"https://www.tiktok.com/@tumargroup/video/{EID}", "text": "Старт продаж ЖК Ульба Ривер, бронь",
            "collected_at": models.utcnow(), "published_at": PUBLISHED}
    return Publication(**{**data, **kw})


def run(p: Publication, *, collector: str = "intake", **kw) -> ingest.IngestResult:
    kw.setdefault("note", "приём публикаций API")
    with SessionLocal() as db:
        res = ingest.ingest_publication(db, p, collector=collector, **kw)
        db.commit()
        return res


def stored(color, key: str, **kw) -> MediaIn:
    return MediaIn(key=key, stored=media.store_bytes(png_bytes(color)), status="downloaded", **kw)


def count(model, *where) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def api_ad(client, ad_id: str) -> dict:
    return next(a for a in client.get("/api/ads").json() if a["id"] == ad_id)


def test_new_publication(client):
    at = models.utcnow().replace(microsecond=0)
    res = run(pub(collected_at=at, raw={"authorMeta": {"name": "tumargroup"}}),
              raw_extra={"_intake": {"sourceId": "my-runner", "claimedCollectedAt": "2026-10-16T08:08:10Z"}},
              external_run_id="abc123")
    assert (res.status, res.version, res.analysis_queued, res.owner) == ("created", 1, True, None)
    assert re.fullmatch(r"[0-9a-f]{32}", res.ad_id)
    with SessionLocal() as db:
        ad = db.get(models.Ad, res.ad_id)
        assert (ad.origin, ad.source, ad.status, ad.complex_id, ad.link_confirmed_by) == ("collected", "parser", "new",
                                                                                         None, None)
        assert (ad.platform, ad.date, models.as_utc(ad.collected_at)) == ("tiktok", "2026-10-09T16:10", at)
        item = db.get(models.IngestItem, res.item_id)
        assert (item.platform, item.external_id, item.collector, item.ad_id, item.version) == (
            "tiktok", EID, "intake", ad.id, 1)
        assert item.raw["_intake"]["sourceId"] == "my-runner" and item.raw["authorMeta"] == {"name": "tumargroup"}
        assert (item.external_run_id, item.analysis_status) == ("abc123", "queued")
        assert models.as_utc(item.published_at) == PUBLISHED
        v = db.scalars(select(models.IngestVersion).where(models.IngestVersion.item_id == item.id)).all()
        assert [(x.version, x.text, x.content_hash) for x in v] == [(1, ad.text, item.content_hash)]
        jobs = db.scalars(select(models.Job)).all()
        assert [(j.kind, j.status, j.ad_id, j.dedupe_key, j.content_version, j.priority) for j in jobs] == [
            ("analyze", "queued", ad.id, f"analyze:{ad.id}", 1, 0)]
        events = db.scalars(select(models.AuditEvent).where(models.AuditEvent.ad_id == ad.id)).all()
        assert [(e.kind, e.who, e.what, e.complex_id) for e in events] == [
            ("system", "Система", "Публикация собрана автоматически (TikTok) · приём публикаций API", None)]
    a = api_ad(client, res.ad_id)
    assert a["processing"] == {"status": "queued", "version": 1, "analyzedVersion": None, "stage": None, "error": None}
    assert a["origin"] == "collected" and a["collectedAt"] and "media" not in a


def test_repeat_is_unchanged(client):
    first = run(pub())
    later = models.utcnow() + timedelta(minutes=5)
    again = run(pub(collected_at=later, raw={"other": "snapshot"}, media=[]))  # raw и время в хеш не входят
    assert (again.status, again.ad_id, again.item_id, again.version, again.analysis_queued) == (
        "unchanged", first.ad_id, first.item_id, 1, False)
    assert count(models.Ad, models.Ad.origin == "collected") == 1
    assert count(models.IngestVersion) == 1 and count(models.Job) == 1
    with SessionLocal() as db:
        item = db.get(models.IngestItem, first.item_id)
        assert models.as_utc(item.collected_at) == later and models.as_utc(item.first_collected_at) < later
    # текст с другими переводами строк и пробелами по краям - тот же
    assert run(pub(text="  Старт продаж ЖК Ульба Ривер, бронь\r\n")).status == "unchanged"


def test_changed_publication_new_version(client):
    first = run(pub(media=[stored((1, 0, 0), "k1", role="cover"), stored((2, 0, 0), "k2", role="slide")]))
    with SessionLocal() as db:
        ad = db.get(models.Ad, first.ad_id)
        k = {a.source_key: a.id for a in db.scalars(select(models.MediaAsset))}
        assert ad.image == f"/api/media/{k['k2']}"  # сначала не обложка
        rev0 = ad.decision_rev
        # кадр видео следует за своим файлом
        db.add(models.MediaAsset(id="f" * 32, ad_id=ad.id, item_id=first.item_id, parent_id=k["k2"], kind="frame",
                                 status="downloaded", frame_ms=0))
        db.commit()
    # решение аналитика по прежней версии
    r = client.post(f"/api/ads/{first.ad_id}/terms-review", json={"author": AUTHOR, "decision": "accepted"})
    assert r.status_code == 200
    stale = SessionLocal()
    stale_ad = stale.get(models.Ad, first.ad_id)  # аналитик открыл карточку до новой версии
    res = run(pub(text="Старт продаж ЖК Ульба Ривер — цены снижены", price="58 194 600 ₸",
                  media=[MediaIn(key="k1", fetch=lambda: pytest.fail("не загружать повторно"))]))
    assert (res.status, res.version, res.ad_id, res.analysis_queued) == ("updated", 2, first.ad_id, True)
    with SessionLocal() as db:
        ad = db.get(models.Ad, first.ad_id)
        assert ad.text == "Старт продаж ЖК Ульба Ривер — цены снижены" and ad.price == "58 194 600 ₸"
        assert ad.decision_rev == rev0 + 2  # решение аналитика(+1) и новая версия(+1)
        assert (ad.terms_review, ad.terms_review_by, ad.status) == ("accepted", AUTHOR, "new")  # решения сохранены
        assert ad.image == f"/api/media/{k['k1']}"  # прежнее изображение больше не в публикации
        cur = {a.source_key: a.current for a in db.scalars(select(models.MediaAsset))}
        assert cur == {"k1": True, "k2": False, None: False}
        what = db.scalars(select(models.AuditEvent.what).where(models.AuditEvent.ad_id == ad.id,
                                                               models.AuditEvent.kind == "system")).all()
        assert what[-1] == ("Публикация изменилась в источнике (TikTok): версия 2. Решения аналитика сохранены — "
                            "проверьте их по новой версии")
        job = db.scalars(select(models.Job)).one()  # задание ещё в очереди - то же, версия поднята
        assert (job.content_version, db.get(models.IngestItem, res.item_id).analysis_status) == (2, "queued")
    versions = client.get("/api/ads").json()
    assert [m["id"] for m in next(a for a in versions if a["id"] == first.ad_id)["media"]] == [k["k1"]]
    with SessionLocal() as db:
        rows = db.scalars(select(models.IngestVersion).where(models.IngestVersion.item_id == res.item_id)
                          .order_by(models.IngestVersion.version)).all()
        assert [(v.version, v.text, v.media_keys) for v in rows] == [
            (1, "Старт продаж ЖК Ульба Ривер, бронь", ["k1", "k2"]), (2, ad.text, ["k1"])]
    # решение, принятое по прежней версии, не записывается молча
    from fastapi import HTTPException

    from app import main, schemas

    with pytest.raises(HTTPException) as e:
        main.terms_review(first.ad_id, schemas.TermsReviewIn(author=AUTHOR, decision="rejected"), stale)
    assert e.value.status_code == 409
    stale.close()


def test_changed_without_decisions_has_short_event(client):
    first = run(pub())
    run(pub(text="Новый текст"))
    with SessionLocal() as db:
        what = db.scalars(select(models.AuditEvent.what).where(models.AuditEvent.ad_id == first.ad_id)
                          .order_by(models.AuditEvent.id)).all()
    assert what[-1] == "Публикация изменилась в источнике (TikTok): версия 2"


def test_foreign_owner_not_overwritten(client):
    first = run(pub(platform="instagram", external_id="17900000000000001"), collector="instagram")
    res = run(pub(platform="instagram", external_id="17900000000000001", text="подмена"))
    assert (res.status, res.owner, res.ad_id, res.analysis_queued) == ("foreign", "instagram", first.ad_id, False)
    with SessionLocal() as db:
        assert db.get(models.Ad, first.ad_id).text == "Старт продаж ЖК Ульба Ривер, бронь"
    assert count(models.IngestVersion) == 1


def test_intake_media_url_pending(client):
    url = "https://cdn.example.com/v/a.mp4?sig=SECRETSIG&expires=1"
    res = run(pub(media=[MediaIn(key=f"{EID}:video", kind="video", url=url)]))
    assert (res.media_pending, res.media_new) == (1, 0)
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset)).one()
        assert (a.status, a.kind, a.origin_ref, a.meta["src"]) == ("pending", "video", "intake", url)
        assert a.origin_url == "https://cdn.example.com/v/a.mp4" and db.get(models.Ad, res.ad_id).image is None
    assert "media" not in api_ad(client, res.ad_id) and "SECRETSIG" not in client.get("/api/ads").text
    # повтор с новой подписанной ссылкой того же файла - без новой версии, ссылка ожидающей загрузки обновлена
    again = run(pub(media=[MediaIn(key=f"{EID}:video", kind="video", url=url.replace("SECRETSIG", "NEWSIG"))]))
    assert (again.status, again.media_pending) == ("unchanged", 1)
    with SessionLocal() as db:
        assert "NEWSIG" in db.scalars(select(models.MediaAsset)).one().meta["src"]


def test_failed_media_retried_on_rerun(client):
    k = "tg:100:5"
    res = run(pub(platform="telegram", external_id="100:5", media=[
        MediaIn(key=k, status="failed", error="Сеть: тайм-аут"), MediaIn(key="tg:100:6", status="skipped",
                                                                         kind="video", error="Видео больше 50 МБ")]),
              collector="telegram")
    with SessionLocal() as db:
        a = {x.source_key: x for x in db.scalars(select(models.MediaAsset))}
        assert (a[k].status, a[k].attempts, a[k].error) == ("failed", 1, "Сеть: тайм-аут")
        assert (a["tg:100:6"].status, a["tg:100:6"].attempts) == ("skipped", 0)
        kn = ingest.known(db, "telegram", "100:5")
    assert kn.needs(k) and kn.failed_keys[k] == 1 and kn.media_keys == [k, "tg:100:6"]
    calls = []

    def fetch_ok():
        calls.append(1)
        return MediaIn(key="ignored", stored=media.store_bytes(png_bytes((5, 5, 5))), status="downloaded")

    p = ingest.resolve_media(pub(platform="telegram", external_id="100:5", media=[
        MediaIn(key=k, fetch=fetch_ok), MediaIn(key="tg:100:6", kind="video", status="skipped",
                                                error="Видео больше 50 МБ")]), kn)
    assert calls == [1] and p.media[0].key == k and p.media[0].fetch is None
    again = run(p, collector="telegram")
    assert (again.status, again.media_new, again.analysis_queued) == ("unchanged", 1, True)
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset).where(models.MediaAsset.source_key == k)).one()
        assert (a.status, a.attempts, a.error, a.mime) == ("downloaded", 2, None, "image/png")
        assert db.get(models.Ad, res.ad_id).image == f"/api/media/{a.id}"
        kn = ingest.known(db, "telegram", "100:5")
    assert not kn.needs(k) and k in kn.downloaded_keys
    # после трёх неудачных попыток файл больше не запрашивается
    kn.failed_keys["x"] = ingest.ATTEMPTS_MAX
    assert not kn.needs("x")


def test_resolve_media_failures():
    def boom():
        raise RuntimeError("HTTP 403 https://cdn.example.com/a.jpg?token=SECRETTOKEN")

    p = ingest.resolve_media(pub(media=[MediaIn(key="a", fetch=boom, role="cover"),
                                        MediaIn(key="b", fetch=lambda: "not a MediaIn")]), None)
    a, b = p.media
    assert (a.status, a.key, a.role, a.fetch) == ("failed", "a", "cover", None)
    assert "SECRETTOKEN" not in a.error and b.status == "failed"


def test_bot_namespace(client):
    res = run(pub(platform="instagram", external_id="555:77", namespace="tgbot", account="@altai.build"),
              collector="telegram_bot", actor=BOT, note="заявка жителя")
    with SessionLocal() as db:
        item = db.get(models.IngestItem, res.item_id)
        ad = db.get(models.Ad, res.ad_id)
        assert (item.platform, item.collector, ad.platform, ad.source, ad.origin) == (
            "tgbot", "telegram_bot", "instagram", "bot", "collected")
        assert db.scalars(select(models.AuditEvent.what).where(models.AuditEvent.ad_id == ad.id)).one() == \
            "Публикация прислана жителем через Telegram-бота (Instagram) · заявка жителя"
    # то же внешнее ID в пространстве площадки - другая запись
    assert run(pub(platform="instagram", external_id="555:77"), collector="instagram").status == "created"


def test_failure_rolls_back_everything(client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("очередь недоступна")

    monkeypatch.setattr(ingest, "enqueue_analysis", boom)
    before = (count(models.Ad), count(models.IngestItem), count(models.MediaAsset), count(models.AuditEvent))
    with SessionLocal() as db:
        with pytest.raises(RuntimeError):
            ingest.ingest_publication(db, pub(media=[stored((6, 6, 6), "k")]), collector="intake")
        db.rollback()
    assert (count(models.Ad), count(models.IngestItem), count(models.MediaAsset), count(models.AuditEvent)) == before
    assert count(models.IngestVersion) == 0


def test_video_and_cover_choice(client):
    res = run(pub(media=[MediaIn(key="v", kind="image", stored=media.store_bytes(mp4_bytes()), status="downloaded"),
                         stored((7, 7, 7), "cover", role="cover")]))
    with SessionLocal() as db:
        a = {x.source_key: x for x in db.scalars(select(models.MediaAsset))}
        assert (a["v"].kind, a["v"].mime) == ("video", "video/mp4")  # вид - по сигнатуре сохранённого файла
        assert db.get(models.Ad, res.ad_id).image == f"/api/media/{a['cover'].id}"  # единственное изображение - обложка
    kinds = [m["kind"] for m in api_ad(client, res.ad_id)["media"]]
    assert kinds == ["video", "image"]


# ---------- очистка снимка источника и хеш ----------

def test_scrub_raw(env):
    env(INTEGRATION_INGEST_TOKEN=INGEST_TOKEN)
    raw = {"id": "1", "accessToken": "a", "x-api-key": "b", "sessionId": "c", "phone": "+7 777", "email": "x@y.kz",
           "authorMeta": {"name": "tumargroup", "avatar": "https://p16.tiktokcdn.com/a.jpg?x-expires=1&x-signature=SIG"},
           "nested": [{"authorization": "Bearer zzz", "ok": 1, "appToken": "FAKE-APP-TOKEN"}],
           "user": {"id": 5}, "username": "keep", "note": f"token {INGEST_TOKEN} leaked", "when": PUBLISHED,
           "nan": float("nan"), "blob": b"\x00\x01"}
    out = ingest.scrub(raw)
    assert set(out) == {"id", "authorMeta", "nested", "username", "note", "when", "nan", "blob"}
    assert out["authorMeta"] == {"name": "tumargroup", "avatar": "https://p16.tiktokcdn.com/a.jpg"}
    assert out["nested"] == [{"ok": 1}] and out["note"] == "token *** leaked"
    assert (out["when"], out["nan"], out["blob"]) == (PUBLISHED.isoformat(), None, "<2 байт>")
    big = ingest.scrub({"a": "x" * (ingest.RAW_MAX + 10), "b": 1})
    assert big == {"truncated": True, "keys": ["a", "b"]}
    # маскирование не зависит от размера тела: строка обрезается до RAW_MAX до маскирования(регрессия: 2 МБ "x-x-…"
    # маскировались часами и занимали поток пула)
    import time

    t = time.perf_counter()
    assert ingest.scrub({"a": "x-" * 1_000_000, "b": ["a." * 500_000]}) == {"truncated": True, "keys": ["a", "b"]}
    assert time.perf_counter() - t < 2
    assert ingest.scrub(None) == {} and ingest.scrub([1, 2]) == {"value": [1, 2]}


def test_content_hash_rules():
    base = pub(media=[MediaIn(key="a"), MediaIn(key="b")], extra={"title": "ЖК"})
    h = ingest.content_hash(base)
    same = [pub(media=[MediaIn(key="b"), MediaIn(key="a")], extra={"title": "ЖК"}, raw={"x": 1},
                collected_at=models.utcnow() + timedelta(days=1)),
            pub(media=[MediaIn(key="a"), MediaIn(key="b"), MediaIn(key="a")], extra={"title": "ЖК"},
                text="Старт продаж ЖК Ульба Ривер, бронь\r\n")]
    assert all(ingest.content_hash(p) == h for p in same)
    for diff in (pub(media=[MediaIn(key="a")], extra={"title": "ЖК"}), pub(media=base.media, extra={"title": "ЖК 2"}),
                 pub(media=base.media, extra={"title": "ЖК"}, price="1 ₸"), pub(media=base.media, extra={"title": "ЖК"},
                                                                                 account="@other")):
        assert ingest.content_hash(diff) != h
    assert ingest.normalize_text("a\x00b\r\nc\x07 ") == "ab\nc" and ingest.normalize_text(None) == ""
    assert len(ingest.normalize_text("слово " * 6000)) <= 20000


def test_bad_external_id(client):
    for eid in ("", "x" * 129):
        with SessionLocal() as db, pytest.raises(ValueError):
            ingest.ingest_publication(db, pub(external_id=eid), collector="intake")


def test_collector_actor_default(client):
    res = run(pub(platform="krisha", external_id="1009924986", account="krisha.kz / ASYLTAS", url="javascript:x",
                  extra={"title": "3-комнатная квартира"}), collector="krisha", actor=COLLECTOR,
              note="сборщик «Krisha.kz», запуск №7")
    with SessionLocal() as db:
        ad = db.get(models.Ad, res.ad_id)
        assert (ad.url, ad.account) == ("", "krisha.kz / ASYLTAS")
        assert db.scalars(select(models.AuditEvent.what).where(models.AuditEvent.ad_id == ad.id)).one() == \
            "Публикация собрана автоматически (Krisha.kz) · сборщик «Krisha.kz», запуск №7"
        v = db.scalars(select(models.IngestVersion)).one()
        assert v.extra == {"title": "3-комнатная квартира"}


# ---------- маршрут POST /api/intake/publications(§7.4) ----------

INTAKE = "/api/intake/publications"
BEARER = {"Authorization": f"Bearer {INGEST_TOKEN}"}


def body(**kw) -> dict:
    data = {"platform": "tiktok", "externalId": EID, "account": "@tumargroup",
            "url": f"https://www.tiktok.com/@tumargroup/video/{EID}", "publishedAt": "2026-10-09T11:10:23.000Z",
            "text": "Старт продаж ЖК Ульба Ривер, бронь", "sourceId": "my-runner", "externalRunId": "abc123"}
    return {**data, **kw}


@pytest.fixture
def intake_on(env):
    env(INTEGRATION_INGEST_TOKEN=INGEST_TOKEN)


def intake_status(client) -> dict:
    return client.get("/api/sources/connectors/intake").json()


def test_intake_auth(client, env):
    assert client.post(INTAKE, json=body(), headers=BEARER).json()["detail"] == auth.INGEST_UNSET
    assert intake_status(client)["status"] == "not_configured"
    env(INTEGRATION_INGEST_TOKEN=INGEST_TOKEN)
    assert intake_status(client)["status"] == "waiting"
    r = client.post(INTAKE, json=body())
    assert (r.status_code, r.headers["www-authenticate"]) == (401, "Bearer")
    assert client.post(INTAKE, content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 401
    r = client.post(INTAKE, json=body(), headers={"Authorization": "Bearer t-wrong-token-0123456789abcdef"})
    assert (r.status_code, r.json()["detail"]) == (403, auth.INGEST_WRONG)
    c = intake_status(client)
    assert (c["status"], c["statusLabel"]) == ("denied", "нет доступа")
    assert c["statusDetail"].startswith("Клиент приёма обращается с неверным токеном (последняя попытка ")
    assert INGEST_TOKEN not in client.get("/api/sources/connectors").text
    assert client.post(INTAKE, json=body(), headers=BEARER).status_code == 201
    c = intake_status(client)
    assert (c["status"], c["statusLabel"]) == ("working", "принимает публикации")
    assert c["statusDetail"].startswith("Последняя публикация: ") and c["counts"]["items"] == 1


def test_intake_create_repeat_update(client, intake_on):
    r = client.post(INTAKE, json=body(raw={"authorMeta": {"name": "tumargroup"}, "accessToken": "zzz"}),
                    headers=BEARER)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["status"], out["version"], out["mediaQueued"], out["analysisQueued"]) == ("created", 1, 0, True)
    ad_id = out["adId"]
    a = client.get(f"/api/ads/{ad_id}").json()
    assert (a["origin"], a["source"], a["status"], a["platform"], a["date"]) == ("collected", "parser", "new", "tiktok",
                                                                                  "2026-10-09T16:10")
    assert a["processing"]["status"] == "queued" and a["collectedAt"]
    with SessionLocal() as db:
        item = db.get(models.IngestItem, out["itemId"])
        assert (item.collector, item.raw["_intake"]["sourceId"], item.raw["_intake"]["externalRunId"]) == (
            "intake", "my-runner", "abc123")
        assert item.raw["authorMeta"] == {"name": "tumargroup"} and "accessToken" not in item.raw
        assert db.scalar(select(func.count()).select_from(models.Job)) == 1
    assert client.post(INTAKE, json=body(), headers=BEARER).json()["status"] == "unchanged"
    again = client.post(INTAKE, json=body(), headers=BEARER)
    assert (again.status_code, again.json()["adId"], again.json()["analysisQueued"]) == (200, ad_id, False)
    # решение аналитика по прежней версии сохраняется, новая версия - история и событие
    assert client.post(f"/api/ads/{ad_id}/terms-review", json={"author": AUTHOR, "decision": "accepted"}).status_code \
        == 200
    rev = client.get(f"/api/ads/{ad_id}").json()
    r = client.post(INTAKE, json=body(text="Старт продаж ЖК Ульба Ривер — цены снижены"), headers=BEARER)
    assert (r.status_code, r.json()["status"], r.json()["version"]) == (200, "updated", 2)
    a = client.get(f"/api/ads/{ad_id}").json()
    assert a["text"] == "Старт продаж ЖК Ульба Ривер — цены снижены" and a["termsReview"] == "accepted"
    assert a["processing"]["version"] == 2 and rev["termsReview"] == "accepted"
    with SessionLocal() as db:
        assert db.get(models.Ad, ad_id).decision_rev == 2
    what = [e["what"] for e in client.get(f"/api/ads/{ad_id}/audit").json()]
    assert ("Публикация изменилась в источнике (TikTok): версия 2. Решения аналитика сохранены — проверьте их по новой "
            "версии") in what
    versions = client.get(f"/api/ads/{ad_id}/versions").json()
    assert [(v["version"], v["current"], v["text"]) for v in versions] == [
        (2, True, "Старт продаж ЖК Ульба Ривер — цены снижены"), (1, False, "Старт продаж ЖК Ульба Ривер, бронь")]
    assert client.get("/api/ads/a1/versions").json() == []  # демо-публикация без записи приёма
    assert client.get("/api/ads/nope/versions").json() == {"detail": "Публикация не найдена"}


def test_intake_foreign_owner_409(client, intake_on):
    run(pub(platform="instagram", external_id="17900000000000001"), collector="instagram")
    r = client.post(INTAKE, json=body(platform="instagram", externalId="17900000000000001"), headers=BEARER)
    assert (r.status_code, r.json()["detail"]) == (409, "Публикация собрана другим источником (Instagram) — "
                                                        "обновление отклонено")


def test_intake_reject_after_success_recorded_at_once(client, intake_on):
    """Отказ, успех, снова отказ в пределах 5 с: последний отказ записан(успех снимает ограничение частоты записи),
    статус - "ошибка", а не "принимает публикации"."""
    assert client.post(INTAKE, json=body(publishedAt="2026-10-09T11:10:23"), headers=BEARER).status_code == 422
    assert intake_status(client)["status"] == "error"
    assert client.post(INTAKE, json=body(), headers=BEARER).status_code == 201
    assert intake_status(client)["status"] == "working"
    assert client.post(INTAKE, json=body(publishedAt="2026-10-09T11:10:23"), headers=BEARER).status_code == 422
    c = intake_status(client)
    assert c["status"] == "error" and c["statusDetail"].startswith("Последняя публикация отклонена: publishedAt")
    with SessionLocal() as db:
        assert db.get(models.Collector, "intake").state["rejects"]["count"] == 2


def test_bot_reject_after_success_recorded_at_once(client, env):
    """То же у бота заявок(/api/intake/telegram): принятая заявка снимает ограничение частоты записи отказов."""
    env(SERVICE_API_TOKEN=SERVICE_TOKEN)
    key = {"X-API-Key": SERVICE_TOKEN}
    good = {"externalId": "123456789:77", "platform": "instagram", "text": "Старт продаж ЖК Ульба Ривер, бронь"}
    bad = {**good, "status": "confirmed"}  # решения аналитика в заявке - 422

    def status() -> str:
        return client.get("/api/sources/connectors/telegram_bot").json()["status"]

    assert client.post("/api/intake/telegram", json=bad, headers=key).status_code == 422
    assert status() == "error"
    assert client.post("/api/intake/telegram", json=good, headers=key).status_code == 201
    assert status() == "working"
    assert client.post("/api/intake/telegram", json=bad, headers=key).status_code == 422
    assert status() == "error"


def test_intake_conflict_and_server_failure_visible(client, intake_on, monkeypatch):
    """409(публикация другого источника) и сбой сервера при приёме видны в статусе подключения, а не "ожидает"."""
    run(pub(platform="instagram", external_id="17900000000000002"), collector="instagram")
    r = client.post(INTAKE, json=body(platform="instagram", externalId="17900000000000002"), headers=BEARER)
    assert r.status_code == 409
    c = intake_status(client)
    assert (c["status"], c["statusDetail"]) == ("error", "Последняя публикация отклонена: Публикация собрана другим "
                                                         "источником (Instagram) — обновление отклонено")

    def boom(*a, **kw):
        raise RuntimeError("сбой записи")

    monkeypatch.setattr(ingest, "ingest_publication", boom)
    assert client.post(INTAKE, json=body(), headers=BEARER).status_code == 500
    c = intake_status(client)
    assert (c["status"], c["statusDetail"]) == ("error", "Последняя публикация отклонена: ошибка сервера "
                                                         "(RuntimeError) — публикация не сохранена, подробности в "
                                                         "журнале API")
    with SessionLocal() as db:
        assert db.get(models.Collector, "intake").state["rejects"]["lastCode"] == 500
        assert db.scalar(select(func.count()).select_from(models.IngestItem).where(
            models.IngestItem.collector == "intake")) == 0


def test_intake_auth_runs_off_event_loop(client, intake_on, monkeypatch):
    """Проверка токена приёма(и запись отказа в БД) - в пуле потоков, не в цикле событий."""
    import asyncio

    real, where = auth.require_ingest, []

    def spy(request):
        try:
            asyncio.get_running_loop()
            where.append("loop")
        except RuntimeError:
            where.append("thread")
        return real(request)

    monkeypatch.setattr(auth, "require_ingest", spy)
    assert client.post(INTAKE, json=body()).status_code == 401
    assert client.post(INTAKE, json=body(), headers=BEARER).status_code == 201
    assert where == ["thread", "thread"]


def test_intake_validation(client, intake_on):
    def post(**kw):
        return client.post(INTAKE, json=body(**kw), headers=BEARER)

    r = client.post(INTAKE, json={**body(), "status": "confirmed", "complexId": "c1"}, headers=BEARER)
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"].endswith("поля решений аналитика при приёме публикаций не принимаются: "
                                                 "status, complexId")
    assert client.post(INTAKE, json={**body(), "foo": 1}, headers=BEARER).status_code == 422
    r = post(platform="telegram", externalId="abc")
    assert r.status_code == 422 and "externalId для telegram" in r.text and r.json()["detail"][0]["loc"][0] == "body"
    assert post(publishedAt="2026-10-09T11:10:23").status_code == 422
    future = (models.utcnow() + timedelta(hours=1)).isoformat()
    old = (models.utcnow() - timedelta(days=31)).isoformat()
    assert post(collectedAt=future).status_code == 422 and post(collectedAt=old).status_code == 422
    assert post(media=[{"key": "a", "url": "http://cdn.example.com/a.png"}]).status_code == 422
    many = [{"key": f"k{i}", "url": f"https://cdn.example.com/{i}.png"} for i in range(21)]
    assert post(media=many).status_code == 422
    c = intake_status(client)
    assert c["status"] == "error" and c["statusDetail"].startswith("Последняя публикация отклонена: ")
    r = client.post(INTAKE, content=b"x" * (media.INTAKE_JSON_MAX + 1),
                    headers={**BEARER, "Content-Type": "application/json"})
    assert (r.status_code, r.json()["detail"]) == (413, "Тело запроса больше 2 МБ")
    with SessionLocal() as db:
        rej = db.get(models.Collector, "intake").state["rejects"]
    assert (rej["lastCode"], rej["lastDetail"]) == (413, "Тело запроса больше 2 МБ")
    # Telegram в форме Bot API → положительный ID канала; заявленное время - в raw, collectedAt - время приёма
    claimed = (models.utcnow() - timedelta(days=2)).replace(microsecond=0)
    r = post(platform="telegram", externalId="-1001234567890:5", url="https://t.me/channel/5",
             collectedAt=claimed.isoformat())
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        item = db.get(models.IngestItem, r.json()["itemId"])
        ad = db.get(models.Ad, item.ad_id)
        assert item.external_id == "1234567890:5"
        assert item.raw["_intake"]["claimedCollectedAt"] == models.iso(claimed)
        assert models.as_utc(ad.collected_at) > models.utcnow() - timedelta(minutes=5)


# ---------- файлы по ссылкам: загрузка заданием анализа ----------

def test_intake_media_downloaded_by_worker(client, intake_on, run_worker, http_mock, ocr_stub):
    a, b = png_bytes((31, 0, 0)), png_bytes((32, 0, 0))
    http_mock.on("GET", "https://cdn.example.com/a.png", content=a)
    http_mock.on("GET", "https://cdn.example.com/b.png", content=b)
    r = client.post(INTAKE, headers=BEARER, json=body(media=[
        {"key": "a", "url": "https://cdn.example.com/a.png?sig=SECRETSIG"},
        {"key": "b", "url": "https://cdn.example.com/b.png"}]))
    out = r.json()
    assert (r.status_code, out["mediaQueued"]) == (201, 2)
    run_worker()
    ad = client.get(f"/api/ads/{out['adId']}").json()
    assert [m["kind"] for m in ad["media"]] == ["image", "image"] and ad["image"] == ad["media"][0]["url"]
    assert ad["processing"]["status"] == "done" and "SECRETSIG" not in client.get("/api/ads").text
    assert client.get(ad["media"][1]["url"]).content == b
    # новая версия без файла "a" - он больше не в публикации
    r = client.post(INTAKE, headers=BEARER, json=body(text="Новый текст", media=[
        {"key": "b", "url": "https://cdn.example.com/b.png"}]))
    assert r.json()["status"] == "updated"
    ad = client.get(f"/api/ads/{out['adId']}").json()
    assert [m["url"] for m in ad["media"]] == [ad["image"]] and client.get(ad["image"]).content == b


def test_intake_media_retry_unavailable_and_budget(client, intake_on, run_worker, http_mock, ocr_stub, monkeypatch):
    png = png_bytes((41, 0, 0))
    http_mock.on("GET", "https://cdn.example.com/r.png", content=png)
    http_mock.on("GET", "https://cdn.example.com/r.png", status=503, times=1)
    http_mock.on("GET", "https://cdn.example.com/gone.png", status=404)
    r = client.post(INTAKE, headers=BEARER, json=body(media=[{"key": "r", "url": "https://cdn.example.com/r.png"},
                                                             {"key": "g", "url": "https://cdn.example.com/gone.png"}]))
    ad_id = r.json()["adId"]
    now = models.utcnow()
    run_worker(now=now)
    with SessionLocal() as db:
        x = {m.source_key: m for m in db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == ad_id))}
        assert (x["r"].status, x["r"].attempts, x["g"].status) == ("pending", 1, "unavailable")
        assert x["r"].meta["retryAt"] and x["r"].meta["src"] == "https://cdn.example.com/r.png"
        assert "HTTP 503" in x["r"].error
    assert client.get(f"/api/ads/{ad_id}").json()["processing"]["status"] == "queued"
    run_worker(now=now + timedelta(minutes=5))
    with SessionLocal() as db:
        got = db.scalars(select(models.MediaAsset).where(models.MediaAsset.source_key == "r")).one()
        assert (got.status, got.attempts, "src" in got.meta) == ("downloaded", 2, False)
    assert client.get(f"/api/ads/{ad_id}").json()["processing"]["status"] == "done"
    # объём файлов публикации исчерпан - остальные "пропущены"
    monkeypatch.setattr(media, "PUBLICATION_BYTES_MAX", len(png) + 10)
    http_mock.on("GET", "https://cdn.example.com/c.png", content=png_bytes((42, 0, 0)))
    http_mock.on("GET", "https://cdn.example.com/d.png", content=png_bytes((43, 0, 0)))
    r = client.post(INTAKE, headers=BEARER, json=body(externalId="7694623823408188694", media=[
        {"key": "c", "url": "https://cdn.example.com/c.png"}, {"key": "d", "url": "https://cdn.example.com/d.png"}]))
    run_worker(now=now + timedelta(minutes=6))
    with SessionLocal() as db:
        x = {m.source_key: m for m in db.scalars(select(models.MediaAsset)
                                                 .where(models.MediaAsset.ad_id == r.json()["adId"]))}
    assert (x["c"].status, x["d"].status, x["d"].error) == ("downloaded", "skipped",
                                                            "Превышен объём файлов публикации (400 МБ)")


def test_intake_resend_heals_failed_media(client, intake_on, run_worker, http_mock, ocr_stub):
    """Регрессия: файл, не загрузившийся по ссылке приёма(например, истёкшая подписанная ссылка → 404), повторная
    отправка с рабочей ссылкой не чинила - новая ссылка отбрасывалась, анализ не ставился. Теперь файл снова ждёт
    загрузки(всего не больше ATTEMPTS_MAX попыток)."""
    png = png_bytes((51, 0, 0))
    http_mock.on("GET", "https://cdn.example.com/old.png", status=404)
    http_mock.on("GET", "https://cdn.example.com/new.png", content=png)
    first = client.post(INTAKE, headers=BEARER, json=body(media=[
        {"key": "a", "url": "https://cdn.example.com/old.png?sig=a"}])).json()
    run_worker()
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == first["adId"])).one()
        assert (a.status, a.attempts) == ("unavailable", 1)
    r = client.post(INTAKE, headers=BEARER, json=body(media=[
        {"key": "a", "url": "https://cdn.example.com/new.png?sig=b"}]))
    out = r.json()
    assert (r.status_code, out["status"], out["mediaQueued"], out["analysisQueued"]) == (200, "unchanged", 1, True)
    run_worker()
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == first["adId"])).one()
        assert (a.status, a.attempts, a.error, "src" in a.meta) == ("downloaded", 2, None, False)
    ad = client.get(f"/api/ads/{first['adId']}").json()
    assert ad["image"] == f"/api/media/{a.id}" and client.get(ad["image"]).content == png
    # попытки исчерпаны - повторная отправка файл больше не загружает
    with SessionLocal() as db:
        db.execute(update(models.MediaAsset).where(models.MediaAsset.id == a.id)
                   .values(status="failed", attempts=ingest.ATTEMPTS_MAX, storage_key=None))
        db.commit()
    out = client.post(INTAKE, headers=BEARER, json=body(media=[
        {"key": "a", "url": "https://cdn.example.com/new.png?sig=c"}])).json()
    assert (out["status"], out["mediaQueued"], out["analysisQueued"]) == ("unchanged", 0, False)
    assert get_asset(a.id).status == "failed"


def get_asset(asset_id: str) -> models.MediaAsset:
    with SessionLocal() as db:
        return db.get(models.MediaAsset, asset_id)


# ---------- приёмка по §14 плана(integration-spec §16.5, столбец A) ----------

def test_acceptance_repeat_no_duplicate(client, intake_on, run_worker, http_mock, accept, ocr_stub):
    http_mock.on("GET", "https://cdn.example.com/a.png", content=png_bytes((51, 0, 0)))
    req = body(media=[{"key": "a", "url": "https://cdn.example.com/a.png"}])
    assert client.post(INTAKE, headers=BEARER, json=req).status_code == 201
    run_worker()
    before, calls = accept.snapshot(), len(http_mock.calls)
    assert client.post(INTAKE, headers=BEARER, json=req).json()["status"] == "unchanged"
    run_worker()
    assert accept.snapshot() == before and len(http_mock.calls) == calls


def test_acceptance_failure_not_empty(client, intake_on, accept):
    r = client.post(INTAKE, headers={"Authorization": "Bearer t-wrong-token-0123456789abcdef"}, json=body())
    assert r.status_code == 403
    c = accept.assert_failure_visible("intake")
    assert c["status"] == "denied"
    assert client.post(INTAKE, headers=BEARER, json=body(externalId="x")).status_code == 422
    assert accept.assert_failure_visible("intake")["status"] == "error"


def test_acceptance_media_survives_source(client, intake_on, run_worker, http_mock, accept, ocr_stub):
    data = png_bytes((61, 0, 0))
    http_mock.on("GET", "https://cdn.example.com/a.png", content=data)
    ad_id = client.post(INTAKE, headers=BEARER, json=body(media=[{"key": "a", "url": "https://cdn.example.com/a.png"}]))\
        .json()["adId"]
    run_worker()
    http_mock.clear()  # исходной ссылки больше нет
    asset = client.get(f"/api/ads/{ad_id}").json()["media"][0]["id"]
    assert accept.media_ok(asset) == data


def test_acceptance_not_confirmed_automatically(client, intake_on, run_worker, accept, ocr_stub):
    ad_id = client.post(INTAKE, headers=BEARER, json=body()).json()["adId"]  # текст называет демо-ЖК "Ульба Ривер"
    run_worker()
    accept.assert_not_confirmed(ad_id)
    r = client.post(f"/api/ads/{ad_id}/hypotheses/accept", json={"author": AUTHOR, "complexId": "c3"})
    assert r.status_code == 200, r.text
    assert (r.json()["complexId"], r.json()["linkConfirmedBy"]) == ("c3", AUTHOR)


def test_acceptance_history_kept(client, intake_on):
    ad_id = client.post(INTAKE, headers=BEARER, json=body()).json()["adId"]
    client.post(INTAKE, headers=BEARER, json=body(text="Изменённый текст", price="58 194 600 ₸"))
    assert count(models.IngestVersion) == 2
    v = client.get(f"/api/ads/{ad_id}/versions").json()
    assert [x["text"] for x in v] == ["Изменённый текст", "Старт продаж ЖК Ульба Ривер, бронь"]
    assert v[0]["price"] == "58 194 600 ₸"
