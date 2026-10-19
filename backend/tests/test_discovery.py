"""Объекты из рекламы и «Проверка по рекламе»: извлечение Claude v2 (упомянутые ЖК, стадия объекта) и сверка с
материалом, дозапрос по новой версии промпта с сохранением истории, нормализация названий и сопоставление с ЖК и
открытым реестром, упоминания и счётчики, сверка 2ГИС, решения аналитика (создать карточку, привязать, отклонить),
импорт ЖК по рекламе, правила оценки ЖК и застройщиков, сводка /api/stats.

Сети нет: Claude — поддельный Anthropic API (как в test_llm), 2ГИС — http_mock (как в test_integration_dgis)."""
import json
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

import demo_seed
from conftest import FAKE_ADMIN_TOKEN
from test_integration_dgis import EXACT, EXACT_ID, EXACT_LAT, EXACT_LON, NUMBER, body as dgis_body
from test_llm import KEY, OK, api, key, message, pub, rows, take  # noqa: F401 — фикстуры api, key

from app import ads_service, demo, discovery, ingest, llm, models, screening
from app.db import SessionLocal

AUTHOR = "Омарова Ж."
DGIS_KEY = "FAKEDGIS-0123456789abcdefgh"
ADMIN = {"X-Admin-Token": FAKE_ADMIN_TOKEN}
NOVA_TEXT = ("Старт продаж в ЖК «Nova Park» на ул. Абая, 10! Бронь квартиры 100 000 ₸, сдача в 2027 году. "
             "Также квартиры в ЖК Ульба Ривер")


# ---------- помощники ----------

def live(env) -> None:
    """Боевой режим: аналитики, открытый реестр и 15 карточек ЖК из него, без демо-набора (demo_seed.live_start)."""
    env()
    demo_seed.live()


def result(*, signals=(), candidates=(), mentions=(), stage=None) -> dict:
    """Проверенный результат Claude (как после llm.verify)."""
    return {"saleSignals": [{"type": t, "quote": q, "source": "text"} for t, q in signals], "amounts": [],
            "complexCandidates": [{"complexId": c, "complexName": c, "confidence": conf, "quote": c, "source": "text"}
                                  for c, conf in candidates],
            "mentionedComplexes": [{"name": n, "address": a, "quote": q, "source": "text"} for n, a, q in mentions],
            "objectStage": {"value": stage[0], "quote": stage[1], "source": "text"} if stage
            else {"value": "unknown", "quote": "", "source": None},
            "uncertainties": [], "summary": "тест"}


def claude_ad(text: str, res: dict, *, account: str = "@seller.uka", days_ago: int = 1, bot: bool = False) -> str:
    """Собранная публикация с готовым ответом Claude (без воркера) и учётом упоминаний, как после этапа llm."""
    when = models.utcnow() - timedelta(days=days_ago)
    with SessionLocal() as db:
        ad = ads_service.create_ad(db, {"platform": "instagram", "account": account, "text": text,
                                        "url": f"https://www.instagram.com/p/{uuid.uuid4().hex[:10]}/"},
                                   actor=ads_service.BOT if bot else ads_service.COLLECTOR, at=when)
        db.add(models.LlmAnalysis(ad_id=ad.id, content_hash=uuid.uuid4().hex * 2, model="claude-sonnet-5-5",
                                  served_model="claude-sonnet-5-5", prompt_version=llm.PROMPT_VERSION, status="done",
                                  result=res, created_at=when, updated_at=when))
        db.commit()
        aid = ad.id
    discovery.record_result(aid, res, when)
    return aid


def objects(**where) -> list[models.DiscoveredObject]:
    with SessionLocal() as db:
        O = models.DiscoveredObject
        return list(db.scalars(select(O).where(*[getattr(O, k) == v for k, v in where.items()]).order_by(O.id)))


def mentions(oid: int) -> list[models.DiscoveredMention]:
    with SessionLocal() as db:
        M = models.DiscoveredMention
        return list(db.scalars(select(M).where(M.object_id == oid).order_by(M.id)))


def scr(client, cid: str, **params) -> dict:
    r = client.get(f"/api/complexes/{cid}", params=params)
    assert r.status_code == 200, r.text
    return r.json()["screening"]


def dev(client, did: str) -> dict:
    return next(d for d in client.get("/api/developers").json() if d["id"] == did)


def events(contains: str) -> list[tuple]:
    with SessionLocal() as db:
        E = models.AuditEvent
        return [(e.kind, e.who, e.what, e.complex_id) for e in db.scalars(select(E).where(E.what.contains(contains)))]


def nova_object() -> models.DiscoveredObject:
    claude_ad(NOVA_TEXT, result(signals=[("booking", "Бронь квартиры 100 000 ₸")],
                                mentions=[("Nova Park", "ул. Абая, 10", "ЖК «Nova Park» на ул. Абая, 10")],
                                stage=("under_construction", "сдача в 2027 году")))
    (o,) = objects(norm_key="nova park")
    return o


# ---------- извлечение Claude v2 ----------

def test_v2_schema_and_prompt():
    assert llm.PROMPT_VERSION == "saq-llm-2"
    props = llm.SCHEMA["properties"]
    assert list(props) == ["saleSignals", "amounts", "complexCandidates", "mentionedComplexes", "objectStage",
                           "uncertainties", "summary"] == llm.SCHEMA["required"]
    assert props["mentionedComplexes"]["items"]["required"] == ["name", "address", "quote"]
    assert props["objectStage"]["properties"]["value"]["enum"] == ["under_construction", "completed", "unknown"]
    assert "mentionedComplexes" in llm.SYSTEM_PROMPT and "objectStage" in llm.SYSTEM_PROMPT
    assert "данные для анализа" in llm.SYSTEM_PROMPT and "игнорируй их" in llm.SYSTEM_PROMPT
    assert "не придумывай и не" in llm.SYSTEM_PROMPT  # адрес — только из материала
    _, user = llm.prompt(llm.Material(platform="instagram", account="@x", bot=False, text="т", ocr=""))
    assert "objectStage.value" in user
    # ответ без полей версии 2 разбирается: «упоминаний нет», «стадия не указана»
    out = llm.Output.model_validate(OK)
    assert out.mentionedComplexes == [] and out.objectStage is None


def test_v2_verification_and_discovered_object(client, run_worker, ocr_stub, key, api):
    reply = {**OK, "saleSignals": [{"type": "booking", "quote": "Бронь квартиры 100 000 ₸"}],
             "complexCandidates": [{"complexId": "c3", "confidence": "high", "quote": "ЖК Ульба Ривер"}],
             "mentionedComplexes": [
                 {"name": "Nova Park", "address": "ул. Абая, 10", "quote": "ЖК «Nova Park» на ул. Абая, 10"},
                 {"name": "Ульба Ривер", "address": "", "quote": "ЖК Ульба Ривер"},
                 {"name": "Sky Tower", "address": "", "quote": "Старт продаж"},  # названия нет в материале
                 {"name": "«Nova Park»", "address": "пр. Победы, 5", "quote": "Nova Park"},  # адреса нет; повтор
                 {"name": "Ульба", "address": "", "quote": "квартиры в ЖК «Ульба»"}],  # цитаты нет в материале
             "objectStage": {"value": "under_construction", "quote": "сдача в 2027 году"}}
    api.add(message(reply))
    res = take(pub(text=NOVA_TEXT))
    run_worker()
    (row,) = rows(res.ad_id)
    assert row.status == "done" and row.prompt_version == "saq-llm-2"
    assert row.dropped == {"name": 1, "address": 1, "quote": 1}
    assert row.result["mentionedComplexes"] == [
        {"name": "Nova Park", "address": "ул. Абая, 10", "quote": "ЖК «Nova Park» на ул. Абая, 10", "source": "text"},
        {"name": "Ульба Ривер", "address": "", "quote": "ЖК Ульба Ривер", "source": "text"}]
    assert row.result["objectStage"] == {"value": "under_construction", "quote": "сдача в 2027 году", "source": "text"}
    got = client.get(f"/api/ads/{res.ad_id}").json()["llm"]
    assert [m["name"] for m in got["mentionedComplexes"]] == ["Nova Park", "Ульба Ривер"]
    assert got["objectStage"]["value"] == "under_construction"
    ev = next(e for e in client.get(f"/api/ads/{res.ad_id}/audit").json() if e["what"].startswith("ИИ (Claude)"))
    assert "упомянуты ЖК — «Nova Park», «Ульба Ривер»" in ev["what"]
    assert "стадия объекта — строится «сдача в 2027 году»" in ev["what"]
    # «Ульба Ривер» — ЖК справочника: объекта из рекламы нет; «Nova Park» — новый объект
    (o,) = objects()
    assert (o.name, o.norm_key, o.status, o.ads_count, o.accounts, o.address_hint) == (
        "Nova Park", "nova park", "new", 1, ["@someone.kz"], "ул. Абая, 10")
    (m,) = mentions(o.id)
    assert (m.ad_id, m.quote, m.source, m.stage, m.stage_quote) == (
        res.ad_id, "ЖК «Nova Park» на ул. Абая, 10", "parser", "under_construction", "сдача в 2027 году")
    lst = client.get("/api/discovered").json()
    assert [x["name"] for x in lst] == ["Nova Park"]
    item = lst[0]
    assert item["quotes"][0]["adId"] == res.ad_id and item["stageHints"]["underConstruction"] == 1
    assert item["saleSignals"]["concerning"] == 1 and item["saleSignals"]["types"] == {"booking": 1}
    assert item["screening"]["status"] == "suspect" and item["sources"] == {"parser": 1}


def test_v2_unverified_stage_is_unknown(client, run_worker, ocr_stub, key, api):
    api.add(message({**OK, "objectStage": {"value": "completed", "quote": "дом сдан в 2020 году"}}))
    res = take(pub())
    run_worker()
    (row,) = rows(res.ad_id)
    assert row.result["objectStage"] == {"value": "unknown", "quote": "", "source": None}
    assert row.dropped == {"stage": 1}


def test_prompt_v2_backfill_resends_and_keeps_history(client, run_worker, ocr_stub, key, api, monkeypatch):
    monkeypatch.setattr(llm, "PROMPT_VERSION", "saq-llm-1")
    api.add(message())
    aid = take(pub()).ad_id
    run_worker()
    (old,) = rows(aid)
    assert old.prompt_version == "saq-llm-1" and old.status == "done"
    monkeypatch.setattr(llm, "PROMPT_VERSION", "saq-llm-2")
    now = models.utcnow() + timedelta(minutes=30)
    with SessionLocal() as db:
        assert llm.due(db, aid, now)  # ответа по новой версии нет — публикация ждёт дозапроса
        picked = llm.backfill(db, now)
        db.commit()
    assert picked == [aid]
    v2 = {**OK, "mentionedComplexes": [{"name": "Ульба Ривер", "address": "", "quote": "ЖК Ульба Ривер"}]}
    api.add(message(v2))
    assert run_worker(now=now) >= 1
    assert len(api.messages) == 2
    first, new = rows(aid)
    assert first.id == old.id and first.prompt_version == "saq-llm-1" and first.result == old.result  # история
    assert (new.prompt_version, new.status) == ("saq-llm-2", "done")
    got = client.get(f"/api/ads/{aid}").json()["llm"]
    assert got["promptVersion"] == "saq-llm-2" and got["mentionedComplexes"][0]["name"] == "Ульба Ривер"
    with SessionLocal() as db:
        assert llm.backfill(db, now + timedelta(days=1)) == []  # по новой версии ответ есть — больше не отправляется


# ---------- названия и сопоставление ----------

@pytest.mark.parametrize("raw, shown, key_", [
    ("ЖК «Satti City»", "Satti City", "sati siti"),
    ("Сатти Сити", "Сатти Сити", "sati siti"),
    ("ЖК бизнес-класса «Nova Park» 🔥", "Nova Park", "nova park"),
    ("Жилой комплекс \"AB-Residence 2\"", "AB-Residence 2", "ab residens 2"),
    ("МЖК PARK LINE", "PARK LINE", "park lain"),
    ("Ёлки", "Ёлки", "elki"),
])
def test_clean_name_and_key(raw, shown, key_):
    assert discovery.clean_name(raw) == shown and discovery.norm_key(raw) == key_


def test_same_name_rules():
    k = discovery.name_keys
    assert discovery.same(k("Satti City"), k("Сатти Сити"))
    assert discovery.same(k("AB-Residence"), k("AB Residence 2"))  # та же основа без номера очереди
    assert discovery.same(k("Жаным, блок Б"), k("Жаным"))
    assert discovery.same(k("Парк Лайн"), k("PARK LINE"))
    assert not discovery.same(k("Asyl Tau"), k("Asyltas")) and not discovery.same(k("Asylym"), k("Asyltas"))
    assert [v.key for v in discovery.variants("Престиж (Sparkle Diamond) (ГС: Инвестор)")] == [
        "prestish", "sparkl diamond"]


def test_mentions_match_registry_and_complexes(client, env):
    live(env)
    names = ["PARK LINE", "Сатти Сити", "Престиж", "AB Residence", "AB-Residence 2", "Diamond", "Sparkle Diamond",
             "Жаным, блок Б", "Nova Park"]
    claude_ad("Подборка новостроек", result(mentions=[(n, "", "Подборка") for n in names]))
    (o,) = objects()  # всё, кроме Nova Park, есть в открытом реестре (в том числе объекты без координат)
    assert o.name == "Nova Park"


def test_mentions_idempotent_counts_and_bot(client, env):
    live(env)
    res1 = result(mentions=[("Nova Park", "", "ЖК Nova Park"), ("Nova  park", "", "Nova park")])
    a1 = claude_ad("ЖК Nova Park, Nova park", res1, account="@nova.sale")
    discovery.record_result(a1, res1, models.utcnow())  # повторный учёт того же ответа ничего не добавляет
    (o,) = objects()
    assert o.ads_count == 1 and len(mentions(o.id)) == 1 and o.accounts == ["@nova.sale"]
    a2 = claude_ad("Нова Парк — бронь", result(mentions=[("Нова Парк", "", "Нова Парк")]), account="@resident",
                   bot=True)
    (o,) = objects()
    assert o.ads_count == 2 and o.accounts == ["@nova.sale"]  # аккаунт из заявки жителя — не аккаунт рекламы
    assert "Нова Парк" in o.aliases
    assert {m.ad_id: m.source for m in mentions(o.id)} == {a1: "parser", a2: "bot"}
    item = client.get("/api/discovered", params={"status": "new"}).json()[0]
    assert item["sources"] == {"parser": 1, "bot": 1} and item["adsCount"] == 2
    # новый ответ без упоминания — упоминание публикации снимается, счётчик пересчитан
    discovery.record_result(a1, result(), models.utcnow())
    (o,) = objects()
    assert o.ads_count == 1 and [m.ad_id for m in mentions(o.id)] == [a2]
    assert client.get("/api/discovered", params={"status": "linked"}).json() == []


# ---------- 2ГИС ----------

@pytest.fixture
def dgis_on(env, http_mock):
    from app.integrations import dgis

    env(DGIS_API_KEY=DGIS_KEY, SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    http_mock.on("GET", dgis.GEOCODE, json=dgis_body([EXACT, NUMBER]))
    return http_mock


def geo_calls(mock) -> list:
    return [r for r in mock.calls if r.url.host == "catalog.api.2gis.com"]


def test_geo_lookup_cache_quota_and_admin(client, env, http_mock):
    live(env)
    o = nova_object()
    r = client.post(f"/api/discovered/{o.id}/geo", json={"author": AUTHOR})
    assert r.status_code == 503  # 2ГИС не настроен
    from app.integrations import dgis

    env(DGIS_API_KEY=DGIS_KEY, SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    http_mock.on("GET", dgis.GEOCODE, json=dgis_body([EXACT, NUMBER]))
    r = client.post(f"/api/discovered/{o.id}/geo", json={"author": AUTHOR})
    assert r.status_code == 201, r.text
    checks = r.json()["checks"]
    assert [c["query"] for c in checks] == ["Казахстан, Усть-Каменогорск, ЖК Nova Park",
                                            "Казахстан, Усть-Каменогорск, ул. Абая, 10"]
    assert all(c["targetKind"] == "discovered" and c["status"] == "candidates" for c in checks)
    assert len(geo_calls(http_mock)) == 2 and client.get("/api/geo/status").json()["used"] == 2
    assert next(c for c in checks[0]["candidates"] if c["id"] == EXACT_ID)["recommended"] is True  # подсказка
    again = client.post(f"/api/discovered/{o.id}/geo", json={"author": AUTHOR})
    assert again.status_code == 200 and all(c["cached"] for c in again.json()["checks"])
    assert len(geo_calls(http_mock)) == 2  # кэш — без запроса и без квоты
    assert client.post(f"/api/discovered/{o.id}/geo", json={"author": AUTHOR, "force": True}).status_code == 401
    forced = client.post(f"/api/discovered/{o.id}/geo", json={"author": AUTHOR, "force": True}, headers=ADMIN)
    assert forced.status_code == 201 and len(geo_calls(http_mock)) == 4
    got = client.get(f"/api/discovered/{o.id}").json()
    assert len(got["geo"]) == 2 and {g["status"] for g in got["geo"]} == {"candidates"}
    # координаты кандидата в «объект из рекламы» не переносятся — только при создании карточки
    gid = checks[0]["id"]
    r = client.post(f"/api/geo/checks/{gid}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR, "apply": True})
    assert r.status_code == 422 and "create-complex" in r.json()["detail"]
    assert any("объект из рекламы «Nova Park»" in e[2] for e in events("Сверка адреса в 2ГИС"))
    assert client.post("/api/discovered/999999/geo", json={"author": AUTHOR}).status_code == 404


def test_registry_card_without_coords_gets_point_from_2gis(client, env, dgis_on):
    live(env)
    r = client.post("/api/geo/checks", json={"targetKind": "complex", "targetId": "r-ab-residence-2",
                                             "author": AUTHOR})
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    assert all(c["distanceM"] is None for c in r.json()["candidates"])  # точки в карточке нет
    r = client.post(f"/api/geo/checks/{gid}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR, "apply": True})
    assert r.status_code == 200, r.text
    c = client.get("/api/complexes/r-ab-residence-2").json()
    assert (c["lat"], c["lng"]) == (EXACT_LAT, EXACT_LON)
    assert any("было нет координат → стало" in e[2] for e in events("Координаты ЖК «AB-Residence 2»"))


# ---------- решения аналитика ----------

def test_create_complex_from_candidate_reruns_rules_without_claude(client, run_worker, ocr_stub, env, api,
                                                                   http_mock):
    from app.integrations import dgis

    env(ANTHROPIC_API_KEY=KEY, DGIS_API_KEY=DGIS_KEY, SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    demo_seed.live()
    http_mock.on("GET", dgis.GEOCODE, json=dgis_body([EXACT, NUMBER]))
    reply = {**OK, "saleSignals": [{"type": "booking", "quote": "Бронь квартиры 100 000 ₸"}], "complexCandidates": [],
             "mentionedComplexes": [{"name": "Nova Park", "address": "ул. Абая, 10",
                                     "quote": "ЖК «Nova Park» на ул. Абая, 10"}],
             "objectStage": {"value": "under_construction", "quote": "сдача в 2027 году"}}
    api.add(message(reply))
    aid = take(pub(text=NOVA_TEXT)).ad_id
    run_worker()
    (o,) = objects()
    assert o.status == "new" and len(api.messages) == 1
    assert client.post(f"/api/discovered/{o.id}/geo", json={"author": AUTHOR}).status_code == 201
    bad = {"author": AUTHOR, "name": "Nova Park"}
    assert client.post(f"/api/discovered/{o.id}/create-complex", json={**bad, "author": "Система"}).status_code == 422
    assert client.post(f"/api/discovered/{o.id}/create-complex", json={**bad, "lat": 49.9}).status_code == 422
    assert client.post(f"/api/discovered/{o.id}/create-complex",
                       json={**bad, "candidateId": "нет-такого"}).status_code == 422
    r = client.post(f"/api/discovered/{o.id}/create-complex", json={**bad, "name": "ЖК «Satti City»"})
    assert r.status_code == 409 and "в открытом реестре" in r.json()["detail"]  # такой ЖК уже есть

    r = client.post(f"/api/discovered/{o.id}/create-complex",
                    json={"author": AUTHOR, "name": "ЖК «Nova Park»", "address": "ул. Абая, 10",
                          "developerName": "ТОО «Nova Build»", "candidateId": EXACT_ID})
    assert r.status_code == 201, r.text
    c = r.json()
    cid = c["id"]
    assert (cid, c["name"], c["origin"], c["lat"], c["lng"]) == ("a-nova-park", "Nova Park", "advertised", EXACT_LAT,
                                                                 EXACT_LON)
    d = dev(client, c["developerId"])
    assert d["origin"] == "advertised" and d["name"] == "ТОО «Nova Build»"
    assert c["permit"]["status"] == "unknown" and "только по рекламе" in c["permit"]["text"]
    assert c["advert"]["discoveredIds"] == [o.id] and c["advert"]["accounts"] == ["@someone.kz"]
    (o,) = objects()
    assert (o.status, o.complex_id, o.decided_by) == ("linked", cid, AUTHOR)
    with SessionLocal() as db:
        G = models.GeoCheck
        checks = list(db.scalars(select(G).where(G.target_kind == "discovered", G.target_id == str(o.id))))
        assert sorted((g.status, g.chosen_id, g.applied, g.decided_by) for g in checks) == [
            ("accepted", EXACT_ID, True, AUTHOR), ("candidates", None, False, None)]  # выбранная сверка — принята
        job = db.scalars(select(models.Job).where(models.Job.ad_id == aid, models.Job.status == "queued")).one()
        assert job.payload["rulesOnly"] is True
    (ev,) = events("Карточка ЖК создана по рекламе")
    assert ev[0] == "data" and ev[1] == AUTHOR and ev[3] == cid and "нет в открытом реестре" in ev[2]
    assert f"кандидат 2ГИС {EXACT_ID}" in ev[2]
    assert client.get(f"/api/complexes/{cid}/risk").json()["assessed"] is False  # формальный риск — не оценивается
    # пересчёт правил: без запроса к Claude, гипотеза — новый ЖК
    assert run_worker() == 1
    assert len(api.messages) == 1
    with SessionLocal() as db:
        job = db.scalars(select(models.Job).where(models.Job.ad_id == aid).order_by(models.Job.id.desc())).first()
        assert job.result["rulesOnly"] is True and job.result["hypotheses"][0]["complexId"] == cid
    hyp = [e for e in client.get(f"/api/ads/{aid}/audit").json() if "пересчёт правил" in e["what"]]
    assert len(hyp) == 1 and hyp[0]["complexId"] == cid and hyp[0]["what"].startswith("Гипотеза ИИ: ЖК «Nova Park»")
    s = scr(client, cid)
    assert s["status"] == "suspect" and s["basis"] == "hypothesis" and s["counts"]["concerning"] == 1
    assert any(x.get("adId") == aid and x.get("quote") == "Бронь квартиры 100 000 ₸" and x["signal"] == "booking"
               for x in s["reasons"])
    assert any("строится — «сдача в 2027 году»" in x["text"] for x in s["reasons"])
    assert dev(client, c["developerId"])["screening"]["status"] == "suspect"
    # повтор — объект уже привязан
    r = client.post(f"/api/discovered/{o.id}/create-complex", json={"author": AUTHOR, "name": "Nova Park 2"})
    assert r.status_code == 409


def test_link_and_dismiss(client, env):
    live(env)
    claude_ad("Квартиры в ЖК Гранд Орда, бронь", result(signals=[("booking", "бронь")],
                                                       mentions=[("Гранд Орда", "", "ЖК Гранд Орда")]))
    claude_ad("Подборка: Sky Hills", result(mentions=[("Sky Hills", "", "Sky Hills")]))
    grand, sky = objects()
    assert client.post(f"/api/discovered/{grand.id}/link", json={"author": AUTHOR, "complexId": "nope"}).status_code \
        == 404
    assert client.post(f"/api/discovered/{grand.id}/link", json={"complexId": "r-orda"}).status_code == 422
    r = client.post(f"/api/discovered/{grand.id}/link", json={"author": AUTHOR, "complexId": "r-orda"})
    assert r.status_code == 200 and (r.json()["status"], r.json()["complexName"]) == ("linked", "Orda")
    assert client.post(f"/api/discovered/{grand.id}/link", json={"author": AUTHOR, "complexId": "r-orda"}).json()[
        "status"] == "linked"  # повтор
    (ev,) = events("привязан к ЖК «Orda»")
    assert ev[:2] == ("data", AUTHOR) and ev[3] == "r-orda"
    s = scr(client, "r-orda")  # реклама объекта — реклама этого ЖК (основание отнесения видно)
    assert s["status"] == "ok" and s["counts"]["ads"] == 1 and s["counts"]["concerning"] == 1
    assert client.get("/api/complexes/r-orda").json()["advert"]["aliases"] == ["Гранд Орда"]
    assert client.post(f"/api/discovered/{sky.id}/dismiss", json={"author": AUTHOR}).status_code == 422
    r = client.post(f"/api/discovered/{sky.id}/dismiss", json={"author": AUTHOR, "reason": "Подборка, не ЖК"})
    assert r.status_code == 200 and (r.json()["status"], r.json()["note"]) == ("dismissed", "Подборка, не ЖК")
    assert [x["id"] for x in client.get("/api/discovered", params={"status": "dismissed"}).json()] == [sky.id]
    (ev,) = events("отклонён аналитиком")
    assert ev[:2] == ("data", AUTHOR) and "Подборка, не ЖК" in ev[2]
    # новое упоминание отклонённого объекта учитывается, решение остаётся
    claude_ad("Sky Hills снова", result(mentions=[("Sky Hills", "", "Sky Hills")]))
    (_, sky2) = objects()
    assert (sky2.status, sky2.ads_count) == ("dismissed", 2)
    stats = client.get("/api/stats").json()["screening"]
    assert stats["discovered"] == {"new": 0, "linked": 1, "dismissed": 1}


# ---------- импорт ЖК по рекламе ----------

ITEMS = [{"name": "ЖК «Nova Park»", "aliases": ["Нова Парк", "Nova Park Residence"], "address": "ул. Абая, 10",
          "lat": 49.95, "lng": 82.6, "developerName": "ТОО «Nova Build»", "accounts": ["@novapark.uka"],
          "sourceUrls": ["https://www.instagram.com/p/NOVA1/", "javascript:alert(1)"], "note": "Instagram, октябрь"},
         {"name": "Sky City", "accounts": ["@skycity.kz"]},
         {"name": "Сатти Сити"}]


def test_import_admin_idempotent_and_screening(client, env, admin_headers, tmp_path, capsys):
    live(env)
    body = {"author": AUTHOR, "note": "разбор рекламы 18.10", "items": ITEMS}
    assert client.post("/api/discovered/import", json=body).status_code == 401
    assert client.post("/api/discovered/import", json=body, headers={"X-Admin-Token": "x" * 30}).status_code == 403
    r = client.post("/api/discovered/import", json=body, headers=admin_headers)
    assert r.status_code == 200, r.text
    rep = r.json()
    nova = "a-nova-park"
    assert len(rep["created"]) == 2 and rep["created"][0] == nova and not rep["unchanged"]
    assert rep["skipped"] == ["Сатти Сити: есть в открытом реестре — «Satti City» (r-satti-city)"]
    assert len(rep["developersCreated"]) == 2 and discovery.UNKNOWN_DEV in rep["developersCreated"]
    sky = rep["created"][1]
    c = client.get(f"/api/complexes/{nova}").json()
    assert (c["origin"], c["address"], c["lat"], c["lng"]) == ("advertised", "ул. Абая, 10", 49.95, 82.6)
    assert c["advert"]["sourceUrls"] == ["https://www.instagram.com/p/NOVA1/"]  # только http(s)
    assert set(c["advert"]["aliases"]) >= {"Nova Park", "Нова Парк", "Nova Park Residence"}
    assert client.get(f"/api/complexes/{sky}").json()["lat"] is None
    created = [e for e in events("Карточка ЖК создана по рекламе (импорт: разбор рекламы 18.10)")]
    assert len(created) == 2 and all(e[0] == "data" and e[1] == AUTHOR for e in created)
    assert any("координат нет — сверьте адрес в 2ГИС" in e[2] for e in created)
    # без рекламы карточка по рекламе — «Требует проверки», застройщик «не установлен» — с пояснением
    s = scr(client, nova)
    assert s["status"] == "check" and any("публикации к ЖК пока не отнесены" in x["text"] for x in s["reasons"])
    placeholder = dev(client, discovery.UNKNOWN_DEV)["screening"]
    assert placeholder["status"] == "check" and "не установлен" in placeholder["reasons"][0]["text"]
    # повтор — без изменений; новый псевдоним — дополнение
    again = client.post("/api/discovered/import", json=body, headers=admin_headers).json()
    assert again["created"] == [] and set(again["unchanged"]) == {nova, sky} and len(again["skipped"]) == 1
    more = {**body, "items": [{"name": "Нова Парк", "aliases": ["Nova Park Oskemen"]}]}
    upd = client.post("/api/discovered/import", json=more, headers=admin_headers).json()
    assert upd["updated"] == [nova]
    # реклама с другим написанием относится к карточке через объект из рекламы
    aid = claude_ad("Нова Парк — бронь квартиры", result(signals=[("booking", "бронь квартиры")],
                                                         mentions=[("Нова Парк", "", "Нова Парк")]))
    s = scr(client, nova)
    assert s["status"] == "suspect" and s["counts"]["ads"] == 1
    assert any(x.get("adId") == aid and "привязано к ЖК аналитиком" in x["text"] for x in s["reasons"])
    # CLI — тот же импорт из файла
    f = tmp_path / "advertised.json"
    f.write_text(json.dumps({"note": "файл", "items": ITEMS + [{"name": "Grand Tower"}]}, ensure_ascii=False),
                 encoding="utf-8")
    assert demo.main(["add-advertised", "--file", str(f), "--author", "Тест"]) == 0
    out = capsys.readouterr().out
    assert "создано 1" in out and "без изменений 2" in out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"name": "X", "lat": 1}]), encoding="utf-8")
    assert demo.main(["add-advertised", "--file", str(bad)]) == 2


# ---------- правила оценки ----------

def test_screening_baseline_open_data(client, env):
    live(env)
    cxs = {c["id"]: c for c in client.get("/api/complexes").json()}
    ab = cxs["r-ab-residence-2"]
    assert ab["permit"]["status"] == "not_permitted" and ab["permit"]["source"] == "homeportal_section"
    assert "Без разрешения на дольщиков" in ab["permit"]["text"] and "не юридический вывод" in ab["permit"]["text"]
    assert ab["screening"]["status"] == "check" and ab["screening"]["label"] == "Требует проверки"
    assert ab["screening"]["reasons"][1]["text"] == "Рекламы по объекту не найдено"
    assert cxs["r-prestizh-sparkle-diamond"]["permit"]["source"] == "homeportal"
    assert cxs["r-diamond"]["permit"]["source"] == "ashyq"
    assert cxs["r-zhanym"]["permit"]["registryStage"] == "completed"
    assert {c["screening"]["status"] for i, c in cxs.items() if i != "r-ab-residence-2"} == {"ok"}
    assert all(c["screening"]["disclaimer"] == screening.DISCLAIMER for c in cxs.values())
    assert cxs["r-orda"]["screening"]["label"] == "В реестре — замечаний нет"
    assert dev(client, "rd-191040019288")["screening"]["status"] == "check"
    assert dev(client, "rd-040140006326")["screening"]["status"] == "ok"
    st = client.get("/api/stats").json()["screening"]
    assert st["complexes"] == {"ok": 14, "check": 1, "suspect": 0, "unknown": 0}
    assert st["developers"] == {"ok": 12, "check": 1, "suspect": 0, "unknown": 0} and st["windowDays"] == 120
    only = client.get("/api/complexes", params={"screening": "check"}).json()
    assert [c["id"] for c in only] == ["r-ab-residence-2"]


def test_screening_suspect_recency_stage_confirmed_and_veto(client, env):
    live(env)
    ab = "r-ab-residence-2"
    sig = [("booking", "Бронь квартиры 200 000 ₸"), ("price", "от 250 000 ₸/м²")]
    aid = claude_ad("Старт продаж в AB-Residence 2! Бронь квартиры 200 000 ₸, от 250 000 ₸/м²",
                    result(signals=sig, candidates=[(ab, "high")]))
    s = scr(client, ab)
    assert (s["status"], s["label"], s["basis"]) == ("suspect", "Подозрение: продажа без разрешения", "hypothesis")
    assert s["counts"]["ads"] == 1 and s["counts"]["concerning"] == 1 and s["inRegistry"] is False
    r = next(x for x in s["reasons"] if x.get("adId") == aid)
    assert r["quote"] == "Бронь квартиры 200 000 ₸" and r["signal"] == "booking" and "Claude" in r["text"]
    assert any("сдача дома не подтверждена" in x["text"] for x in s["reasons"])
    d = dev(client, "rd-191040019288")["screening"]
    assert d["status"] == "suspect" and d["worst"][0] == {"complexId": ab, "name": "AB-Residence 2",
                                                          "status": "suspect", "label": s["label"]}
    assert d["counts"] == {"complexes": 1, "ok": 0, "check": 0, "suspect": 1, "unknown": 0}
    assert client.get("/api/stats").json()["screening"]["complexes"]["suspect"] == 1
    # окно свежести
    env(SAQ_SCREENING_DAYS="30")
    later = (date.today() + timedelta(days=45)).isoformat()
    s = scr(client, ab, asOf=later)
    assert s["status"] == "check" and s["counts"]["concerning"] == 0 and s["counts"]["windowDays"] == 30
    env(SAQ_SCREENING_DAYS=None)
    # подтверждённая аналитиком привязка — основание «confirmed»
    assert client.patch(f"/api/ads/{aid}/complex", json={"complexId": ab, "author": AUTHOR}).status_code == 200
    assert scr(client, ab)["basis"] == "confirmed"
    # «это не предложение внести деньги» — признаки публикации не учитываются
    r = client.post(f"/api/ads/{aid}/terms-review", json={"decision": "rejected", "author": AUTHOR})
    assert r.status_code == 200, r.text
    assert scr(client, ab)["status"] == "check"
    # публикация, отклонённая аналитиком, не учитывается вовсе
    client.patch(f"/api/ads/{aid}/status", json={"status": "dismissed", "author": AUTHOR})
    assert scr(client, ab)["counts"]["ads"] == 0
    # статус и привязки публикации оценка не меняла
    with SessionLocal() as db:
        ad = db.get(models.Ad, aid)
        assert (ad.status, ad.complex_id, ad.link_confirmed_by) == ("dismissed", ab, AUTHOR)


def test_screening_completed_stage_lowers_suspicion(client, env):
    live(env)
    ab = "r-ab-residence-2"
    claude_ad("AB-Residence 2: дом сдан, ключи сразу! Рассрочка 0%",
              result(signals=[("installment", "Рассрочка 0%")], candidates=[(ab, "medium")],
                     stage=("completed", "дом сдан, ключи сразу")))
    s = scr(client, ab)
    assert s["status"] == "check"
    assert any("сданном доме законна" in x["text"] and x.get("quote") == "дом сдан, ключи сразу"
               for x in s["reasons"])
    claude_ad("AB-Residence 2 — бронь, сдача в 2028", result(signals=[("booking", "бронь")], candidates=[(ab, "high")],
                                                            stage=("under_construction", "сдача в 2028")))
    s = scr(client, ab)
    assert s["status"] == "suspect" and any("строится — «сдача в 2028»" in x["text"] for x in s["reasons"])
    # кандидат Claude низкой уверенности не относит публикацию к ЖК
    claude_ad("Orda?", result(signals=[("booking", "бронь")], candidates=[("r-orda", "low")]))
    assert scr(client, "r-orda")["counts"]["ads"] == 0


def test_screening_registry_completed_conflict(client, env):
    live(env)
    claude_ad("Жаным — старт продаж новой очереди! Бронь 100 000 ₸",
              result(signals=[("presale", "старт продаж новой очереди"), ("booking", "Бронь 100 000 ₸")],
                     candidates=[("r-zhanym", "high")], stage=("under_construction", "старт продаж новой очереди")))
    s = scr(client, "r-zhanym")  # источник: «Завершён», реклама — строящаяся очередь
    assert s["status"] == "check" and s["inRegistry"] is True
    assert any("основание может не распространяться на новую очередь" in x["text"] for x in s["reasons"])
    claude_ad("Orda — старт продаж! Бронь 100 000 ₸",
              result(signals=[("presale", "старт продаж")], candidates=[("r-orda", "high")]))
    s = scr(client, "r-orda")  # строится по источнику и есть основание — замечаний нет
    assert s["status"] == "ok" and s["counts"]["concerning"] == 1


def test_screening_unknown_and_empty_developer(client, env):
    live(env)
    with SessionLocal() as db:
        db.add(models.Developer(id="dx", name="ТОО «Пустой»", bin="", director="", registered="", objects=0,
                                unfinished=0, phones=[], origin="demo"))
        db.add(models.Developer(id="dy", name="ТОО «Без данных»", bin="", director="", registered="", objects=0,
                                unfinished=0, phones=[], origin="demo"))
        db.add(models.Complex(id="cx", name="Тихий двор", developer_id="dy", address="", district="", lat=None,
                              lng=None, stage="", progress=0, floors=0, flats=0, start="", planned_end="",
                              basis={"guarantee": "unknown", "mio": "unknown", "bvu": "unknown"}, cover="",
                              origin="demo"))
        db.commit()
    s = scr(client, "cx")
    assert (s["status"], s["label"]) == ("unknown", "Нет данных для оценки")
    assert dev(client, "dx")["screening"]["status"] == "unknown"
    assert dev(client, "dy")["screening"]["status"] == "check"


def test_screening_demo_rules_fallback(client):
    """Демо-набор: основание — сверка (basis «есть/нет»), признаки — словарь модели риска (ответов Claude нет)."""
    cxs = {c["id"]: c for c in client.get("/api/complexes").json()}
    assert cxs["c1"]["permit"]["verified"] is True and cxs["c1"]["screening"]["status"] == "ok"
    c2 = cxs["c2"]["screening"]  # основания нет по сверке, реклама с бронью
    assert c2["status"] == "suspect" and any(x.get("signal") == "booking" for x in c2["reasons"])
    assert cxs["c2"]["permit"]["status"] == "not_permitted"


# ---------- очередь анализа: пересчёт правил ----------

def test_rules_only_job_upgrades_to_full_analysis(client, run_worker, ocr_stub):
    aid = take(pub()).ad_id
    run_worker()
    with SessionLocal() as db:
        assert discovery.rerun_rules(db, [aid]) == [aid]
        db.commit()
        J = models.Job
        (job,) = db.scalars(select(J).where(J.ad_id == aid, J.status == "queued"))
        assert job.payload == {"rulesOnly": True, "reason": discovery.RULES_REASON}
        ingest.enqueue_analysis(db, aid, payload={"backfill": True}, priority=llm.BACKFILL_PRIORITY)
        db.commit()
        db.refresh(job)
        assert job.payload == {"backfill": True}  # обычный анализ поглотил пересчёт правил


# ---------- схема ----------

def test_discovered_tables_and_counts(client):
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(models.DiscoveredObject)) == 0
    assert client.get("/api/discovered").json() == []
    assert client.get("/api/discovered/1").status_code == 404
