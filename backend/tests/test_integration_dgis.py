"""2ГИС - геокодер(integration-spec §12.5, §16.3 test_integration_dgis, план §11): кандидаты с точностью, подсказка
"рекомендуется" без автопринятия, кэш и квота до запроса, коды ответов 2ГИС, ключ не утекает, решения аналитика
(принять / перенести координаты / отклонить), уборка кэша, проверка подключения через воркер. Без сети: ответы 2ГИС -
синтетические(точный дом и "8/3а" с точностью number), через http_mock."""
import logging
import re
import threading
from datetime import timedelta, UTC

import httpx
import pytest
from sqlalchemy import func, select

from conftest import FAKE_ADMIN_TOKEN

from app import models
from app.db import SessionLocal

KEY = "FAKEDGIS-0123456789abcdefgh"
AUTHOR = "Омарова Ж."
EXACT_ID = "70030077103568943"
NUMBER_ID = "70030077103568999"
EXACT_LAT, EXACT_LON = 49.88873, 82.610583
CONTROL = "Казахстан, Усть-Каменогорск, Увалиева 8/3"


def _item(id_: str, name: str, building: str, precision: str, lat: float, lon: float, *,
          purpose: str = "Жилой дом", city: str = "Усть-Каменогорск") -> dict:
    """Элемент items[] как у 2ГИС(поля по FIELDS + приходящие по умолчанию); данные синтетические."""
    return {"id": id_, "type": "building", "name": name, "full_name": f"{city}, {name}", "address_name": building,
            "purpose_name": purpose, "full_address_name": f"{city}, {building}",
            "point": {"lat": lat, "lon": lon},
            "address": {"building_id": id_, "components": [{"type": "street_number", "street": building}]},
            "adm_div": [{"type": "country", "name": "Казахстан"}, {"type": "region", "name": "ВКО"},
                        {"type": "city", "name": city}],
            "search_attributes": {"dgis_address_details": {"precision": precision}}}


EXACT = _item(EXACT_ID, "ЖК Asyl Tas", "Увалиева, 8/3", "exact", EXACT_LAT, EXACT_LON)
NUMBER = _item(NUMBER_ID, "Увалиева, 8/3а", "Увалиева, 8/3а", "number", 49.88901, 82.61102, purpose="Киоск")


def body(items: list, code: int = 200) -> dict:
    return {"meta": {"api_version": "3.0.0", "code": code, "issue_date": "20261016"},
            "result": {"items": items, "total": len(items)}}


def error_body(code: int, message: str, type_: str = "error") -> dict:
    return {"meta": {"api_version": "3.0.0", "code": code, "error": {"type": type_, "message": message}}}


def admin(**kw) -> dict:
    return {"X-Admin-Token": FAKE_ADMIN_TOKEN, **kw}


@pytest.fixture
def dgis():
    from app.integrations import dgis as module

    return module


@pytest.fixture
def geo(env, http_mock, dgis):
    """Ключ 2ГИС и администратор заданы; 2ГИС отвечает двумя кандидатами. Возвращает http_mock."""
    env(DGIS_API_KEY=KEY, SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    http_mock.on("GET", dgis.GEOCODE, json=body([EXACT, NUMBER]))
    return http_mock


def geo_calls(mock) -> list:
    return [r for r in mock.calls if r.url.host == "catalog.api.2gis.com"]


def post_check(client, target_id: str = "c4", kind: str = "complex", *, headers=None, **kw):
    return client.post("/api/geo/checks", json={"targetKind": kind, "targetId": target_id, "author": AUTHOR, **kw},
                       headers=headers)


def complex_point(cid: str) -> tuple:
    with SessionLocal() as db:
        c = db.get(models.Complex, cid)
        return c.lat, c.lng


def audit_rows(contains: str) -> list:
    with SessionLocal() as db:
        A = models.AuditEvent
        return [(a.kind, a.who, a.what, a.complex_id) for a in db.scalars(select(A).where(A.what.contains(contains)))]


def counter(name_prefix: str) -> int:
    with SessionLocal() as db:
        Q = models.QuotaCounter
        return int(db.scalar(select(func.coalesce(func.sum(Q.count), 0)).where(Q.name.startswith(name_prefix))) or 0)


def geo_rows() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(models.GeoCheck))


# ---------- 1. геокодер: кандидаты, точность, подсказка ----------

def test_geocode_candidates_precision_and_recommended(geo, dgis):
    from app.integrations import http

    with http.client(base_url=dgis.BASE) as c:
        cands = dgis.geocode(CONTROL, client=c, key=KEY)
    assert [(x.id, x.precision, x.type, x.city) for x in cands] == [
        (EXACT_ID, "exact", "building", "Усть-Каменогорск"), (NUMBER_ID, "number", "building", "Усть-Каменогорск")]
    first = cands[0]
    assert (first.name, first.full_name, first.address_name, first.purpose, first.lat, first.lon, first.building_id) \
        == ("ЖК Asyl Tas", "Усть-Каменогорск, ЖК Asyl Tas", "Усть-Каменогорск, Увалиева, 8/3", "Жилой дом", EXACT_LAT,
            EXACT_LON, EXACT_ID)
    assert dgis.recommended(cands) == EXACT_ID  # единственный точный дом в городе
    (req,) = geo_calls(geo)
    params = req.url.params
    assert (params["type"], params["fields"], params["locale"], params["page_size"], params["q"], params["key"]) == (
        "building", dgis.FIELDS, "ru_KZ", "10", CONTROL, KEY)
    assert dgis.FIELDS == ("items.point,items.address,items.adm_div,items.full_address_name,"
                           "items.search_attributes.dgis_address_details")
    assert "authorization" not in {k.lower() for k in req.headers}


def test_recommended_is_only_a_single_exact_building(dgis):
    def cand(id_, precision, *, city="Усть-Каменогорск", type_="building"):
        return dgis.Candidate(id=id_, name=id_, full_name=None, address_name=None, purpose=None, type=type_,
                              precision=precision, lat=49.9, lon=82.6, city=city)

    assert dgis.recommended([cand("a", "exact"), cand("b", "number")]) == "a"
    assert dgis.recommended([cand("a", "exact"), cand("b", "exact")]) is None  # два точных - выбирает аналитик
    assert dgis.recommended([cand("a", "number"), cand("b", "near")]) is None
    assert dgis.recommended([cand("a", "exact", city="Семей")]) is None
    assert dgis.recommended([cand("a", "exact", type_="street")]) is None
    assert dgis.recommended([]) is None
    assert dgis.distance_m(EXACT_LAT, EXACT_LON, EXACT_LAT, EXACT_LON) == 0
    assert abs(dgis.distance_m(49.0, 82.6, 50.0, 82.6) - 111_195) < 50  # градус широты ≈ 111,2 км


def test_geocode_skips_broken_items_and_duplicates(geo, dgis):
    from app.integrations import http

    broken = [{"id": "1", "point": {"lat": "x", "lon": 82.6}}, {"name": "без id", "point": {"lat": 49, "lon": 82}},
              {"id": "2", "point": {"lat": 95.0, "lon": 82.6}}, "не объект", EXACT, EXACT]
    geo.on("GET", dgis.GEOCODE, json=body(broken))
    with http.client(base_url=dgis.BASE) as c:
        assert [x.id for x in dgis.geocode(CONTROL, client=c, key=KEY)] == [EXACT_ID]


def test_geocode_ignores_non_scalar_fields(geo, dgis):
    """Поля-объекты и списки вместо строк - не repr в карточке: id-объект - кандидат пропускается, name-объект -
    имя берётся из адреса."""
    from app.integrations import http

    odd = {**EXACT, "id": {"x": 1}}
    named = {**NUMBER, "name": {"ru": "x"}, "purpose_name": ["Киоск"], "type": True}
    geo.on("GET", dgis.GEOCODE, json=body([odd, named]))
    with http.client(base_url=dgis.BASE) as c:
        (cand,) = dgis.geocode(CONTROL, client=c, key=KEY)
    assert (cand.id, cand.name, cand.purpose, cand.type) == (NUMBER_ID, "Усть-Каменогорск, Увалиева, 8/3а", None,
                                                             None)


@pytest.mark.parametrize("status, payload, headers, exc, detail, retryable, retry_after", [
    (200, error_body(400, "Parameter q is invalid"), None, "SourceError",
     "2ГИС отклонил запрос: Parameter q is invalid", False, None),
    (400, error_body(400, "Bad request"), None, "SourceError", "2ГИС отклонил запрос: Bad request", False, None),
    # message-объект - не repr в тексте ошибки, а тип ошибки
    (400, error_body(400, {"ru": "x"}, "paramsInvalid"), None, "SourceError", "2ГИС отклонил запрос: paramsInvalid",
     False, None),
    (401, error_body(401, "Unauthorized"), None, "AuthExpired", "Ключ 2ГИС отклонён", False, None),
    (403, error_body(403, "Key is not valid"), None, "AuthExpired", "Ключ 2ГИС отклонён", False, None),
    (403, error_body(403, "Request limit exceeded for key"), {"Retry-After": "120"}, "RateLimited", None, False, 120),
    (403, error_body(403, "Forbidden", "requestsLimitReached"), None, "RateLimited", None, False, 3600),
    (429, None, {"Retry-After": "30"}, "RateLimited", None, False, 30),
    (500, None, None, "SourceError", "2ГИС не ответил (код 500)", True, None),
    (200, error_body(503, "Service unavailable"), None, "SourceError", "2ГИС не ответил (код 503)", True, None),
    (408, None, None, "SourceError", "2ГИС не ответил (код 408)", True, None),
])
def test_geocode_error_mapping(geo, dgis, status, payload, headers, exc, detail, retryable, retry_after):
    from app.integrations import base, http

    if payload is not None:
        geo.on("GET", dgis.GEOCODE, status=status, json=payload, headers=headers)
    else:
        geo.on("GET", dgis.GEOCODE, status=status, content=b"<html>error</html>", headers=headers)
    with http.client(base_url=dgis.BASE) as c, pytest.raises(getattr(base, exc)) as ei:
        dgis.geocode(CONTROL, client=c, key=KEY)
    e = ei.value
    if detail is not None:
        assert e.detail == detail
    if exc == "SourceError":
        assert e.retryable is retryable
    if retry_after is not None:
        assert e.retry_after == retry_after


def test_geocode_not_found_and_network(geo, dgis):
    from app.integrations import base, http

    geo.on("GET", dgis.GEOCODE, status=404, json=error_body(404, "Results not found", "itemNotFound"))
    with http.client(base_url=dgis.BASE) as c:
        assert dgis.geocode(CONTROL, client=c, key=KEY) == []

    def boom(request):
        raise httpx.ConnectError("connection refused", request=request)

    geo.on("GET", dgis.GEOCODE, handler=boom)
    with http.client(base_url=dgis.BASE) as c, pytest.raises(base.SourceError) as ei:
        dgis.geocode(CONTROL, client=c, key=KEY)
    assert ei.value.retryable is True and KEY not in ei.value.detail

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    geo.on("GET", dgis.GEOCODE, handler=slow)
    with http.client(base_url=dgis.BASE) as c, pytest.raises(base.SourceError) as ei:
        dgis.geocode(CONTROL, client=c, key=KEY)
    assert (ei.value.detail, ei.value.retryable) == ("2ГИС не ответил вовремя", True)

    # явное "ничего не найдено" с HTTP 200(код - в meta) и пустой список кандидатов - тоже []
    geo.on("GET", dgis.GEOCODE, json=error_body(404, "Results not found", "itemNotFound"))
    with http.client(base_url=dgis.BASE) as c:
        assert dgis.geocode(CONTROL, client=c, key=KEY) == []
    geo.on("GET", dgis.GEOCODE, json=body([]))
    with http.client(base_url=dgis.BASE) as c:
        assert dgis.geocode(CONTROL, client=c, key=KEY) == []


NO_POINT = {"id": EXACT_ID, "type": "building", "name": "ЖК Asyl Tas",
            "geometry": {"centroid": "POINT(82.610583 49.88873)"}}  # point не пришёл(формат или поля ключа)
UNKNOWN = "2ГИС вернул ответ в неизвестном формате"
MALFORMED = [  # (HTTP-статус, тело JSON или None - HTML, ожидаемый текст ошибки)
    (200, {}, UNKNOWN),
    (200, {"meta": {"code": 200}}, UNKNOWN),
    (200, {"meta": {"code": 200}, "result": {"total": 1}}, UNKNOWN),
    (200, {"meta": {"code": 200}, "result": {"items": {"0": EXACT}}}, UNKNOWN),
    (200, body([NO_POINT]), f"{UNKNOWN}: у кандидатов нет ID или координат"),
    (404, None, f"{UNKNOWN} (код 404)"),
    (404, {"detail": "Not Found"}, f"{UNKNOWN} (код 404)"),
]


def _respond(mock, dgis, status, payload):
    if payload is None:
        mock.on("GET", dgis.GEOCODE, status=status, content=b"<html>Not Found</html>")
    else:
        mock.on("GET", dgis.GEOCODE, status=status, json=payload)


@pytest.mark.parametrize("status, payload, detail", MALFORMED)
def test_geocode_unknown_format_is_failure(geo, dgis, status, payload, detail):
    """Сменившийся формат - ошибка, а не "2ГИС ничего не нашёл"(D10, план §14 "сбой не выглядит пустой выдачей")."""
    from app.integrations import base, http

    _respond(geo, dgis, status, payload)
    with http.client(base_url=dgis.BASE) as c, pytest.raises(base.SourceError) as ei:
        dgis.geocode(CONTROL, client=c, key=KEY)
    assert (ei.value.detail, ei.value.retryable) == (detail, False)


def test_quota_names_and_reserve(env, dgis):
    from datetime import datetime

    s = env(DGIS_API_KEY=KEY, DGIS_QUOTA_PERIOD_START="2026-10-16", DGIS_QUOTA_LIMIT=2, DGIS_DAILY_LIMIT=5)
    fp = s.key_fp("DGIS_API_KEY")
    # 20:30 UTC = 01:30 следующего дня в Усть-Каменогорске(UTC+5)
    now = datetime(2026, 10, 16, 20, 30, tzinfo=UTC)
    assert dgis.quota_names(s, now) == (f"dgis:{fp}:2026-10-16", f"dgis-day:{fp}:2026-10-17")
    assert KEY not in "".join(dgis.quota_names(s, now))
    s2 = env(DGIS_QUOTA_PERIOD_START=None)
    assert dgis.quota_names(s2, now)[0] == f"dgis:{fp}:all"

    counts: dict = {}

    def reserve_fn(name, limit):
        if counts.get(name, 0) >= limit:
            return False
        counts[name] = counts.get(name, 0) + 1
        return True

    released = []
    dgis.reserve(reserve_fn, s, now, release_fn=released.append)
    dgis.reserve(reserve_fn, s, now, release_fn=released.append)
    with pytest.raises(dgis.Quota) as ei:
        dgis.reserve(reserve_fn, s, now, used_fn=lambda n: counts[n], release_fn=released.append)
    assert ei.value.detail == "Лимит запросов 2ГИС по ключу исчерпан (2 из 2)" and ei.value.retry_after == 86400
    s3 = env(DGIS_QUOTA_LIMIT=10, DGIS_DAILY_LIMIT=2, DGIS_QUOTA_PERIOD_START="2026-10-16")
    with pytest.raises(dgis.Quota) as ei:
        dgis.reserve(reserve_fn, s3, now, release_fn=released.append)
    assert ei.value.detail == "Дневной лимит сверок 2ГИС исчерпан (2)"
    assert released == [f"dgis:{fp}:2026-10-16"]  # учёт периода возвращён: запрос не выполнялся
    assert 0 < ei.value.retry_after <= 86400


# ---------- 2. маршрут: кэш, force, повтор ----------

def test_check_route_cache_force_and_repeat(client, geo, dgis, monkeypatch):
    r = post_check(client)
    assert r.status_code == 201, r.text
    g = r.json()
    assert (g["targetKind"], g["targetId"], g["provider"], g["status"], g["cached"], g["purged"], g["requestedBy"]) == (
        "complex", "c4", "2gis", "candidates", False, False, AUTHOR)
    assert g["query"] == "Казахстан, Усть-Каменогорск, пр. Сатпаева, 14/2"
    assert [(c["id"], c["precision"], c["recommended"]) for c in g["candidates"]] == [
        (EXACT_ID, "exact", True), (NUMBER_ID, "number", False)]
    lat, lng = complex_point("c4")
    assert g["prevPoint"] == {"lat": lat, "lng": lng}
    assert abs(g["candidates"][0]["distanceM"] - dgis.distance_m(lat, lng, EXACT_LAT, EXACT_LON)) < 0.2
    assert g["chosenId"] is None and g["applied"] is False and g["decidedBy"] is None
    assert g["fetchedAt"] and g["expiresAt"]
    assert (geo_calls(geo)[0].url.params["q"]) == g["query"]
    assert audit_rows("Сверка адреса в 2ГИС") == [
        ("data", AUTHOR, f"Сверка адреса в 2ГИС: «{g['query']}» — кандидатов: 2", "c4")]
    st = client.get("/api/geo/status").json()
    assert (st["configured"], st["used"], st["usedToday"], st["limit"], st["dailyLimit"], st["cacheDays"]) == (
        True, 1, 1, 900, 50, 7)

    # повтор того же адреса - из кэша: без обращения к 2ГИС и без расхода квоты
    again = post_check(client)
    assert again.status_code == 200 and again.json()["cached"] is True and again.json()["id"] == g["id"]
    assert len(geo_calls(geo)) == 1 and counter("dgis:") == 1 and geo_rows() == 1
    same = post_check(client, query="  казахстан,  УСТЬ-КАМЕНОГОРСК, пр. Сатпаева, 14/2 ")
    assert same.status_code == 200 and same.json()["cached"] is True and len(geo_calls(geo)) == 1

    # "Обновить запрос" мимо кэша - только администратор
    assert client.post("/api/geo/checks", json={"targetKind": "complex", "targetId": "c4", "author": AUTHOR,
                                                "force": True}).status_code == 401
    assert client.post("/api/geo/checks", headers={"X-Admin-Token": "t-wrong-0123456789abcdefghij"},
                       json={"targetKind": "complex", "targetId": "c4", "author": AUTHOR,
                             "force": True}).status_code == 403
    assert len(geo_calls(geo)) == 1
    forced = client.post("/api/geo/checks", headers=admin(), json={"targetKind": "complex", "targetId": "c4",
                                                                   "author": AUTHOR, "force": True})
    assert forced.status_code == 201 and forced.json()["cached"] is False and forced.json()["id"] != g["id"]
    assert len(geo_calls(geo)) == 2 and counter("dgis:") == 2

    # новый запрос по тому же объекту в течение 10 минут - 429
    other = post_check(client, query="Казахстан, Усть-Каменогорск, Увалиева 8/3")
    assert (other.status_code, other.json()["detail"]) == (
        429, "Сверку этого объекта уже запрашивали — повторите через 10 мин")
    assert len(geo_calls(geo)) == 2
    later = dgis._now() + timedelta(minutes=11)
    monkeypatch.setattr(dgis, "_now", lambda: later)
    ok = post_check(client, query="Казахстан, Усть-Каменогорск, Увалиева 8/3")
    assert ok.status_code == 201 and len(geo_calls(geo)) == 3

    listed = client.get("/api/geo/checks", params={"targetKind": "complex", "targetId": "c4"}).json()
    assert [x["id"] for x in listed] == [ok.json()["id"], forced.json()["id"], g["id"]]
    assert all(x["cached"] is False for x in listed)
    assert client.get("/api/geo/checks", params={"targetKind": "registry", "targetId": "c4"}).json() == []


def test_force_without_admin_token_on_server(client, env, http_mock, dgis):
    env(DGIS_API_KEY=KEY, SAQ_ADMIN_TOKEN=None)
    r = client.post("/api/geo/checks", headers=admin(), json={"targetKind": "complex", "targetId": "c4",
                                                              "author": AUTHOR, "force": True})
    assert r.status_code == 503 and http_mock.calls == []


def test_check_targets_and_validation(client, geo, dgis):
    r = post_check(client, "nope")
    assert (r.status_code, r.json()["detail"]) == (404, "ЖК не найден")
    r = post_check(client, "nope", kind="registry")
    assert (r.status_code, r.json()["detail"]) == (404, "Объект открытых данных не найден")
    assert post_check(client, kind="nothing").status_code == 422
    assert post_check(client, query="x" * 201).status_code == 422
    assert client.post("/api/geo/checks", json={"targetKind": "complex", "targetId": "c4",
                                                "author": "Система"}).status_code == 422
    # объект открытых данных: "г. Усть-Каменогорск" из адреса не повторяется
    r = post_check(client, "park-line", kind="registry")
    assert r.status_code == 201, r.text
    assert r.json()["query"] == "Казахстан, Усть-Каменогорск, пр. И. Есенберлина, д. 56"
    # объект без координат: расстояние не считается
    r = post_check(client, "prestizh-sparkle-diamond", kind="registry")
    assert r.status_code == 201 and r.json()["prevPoint"] is None
    assert [c["distanceM"] for c in r.json()["candidates"]] == [None, None]
    assert len(geo_calls(geo)) == 2
    # объект из рекламы: id - только ASCII-цифры("²".isdigit() - True, int("²") - ошибка; было 500)
    for tid in ("²", "١", "abc", "9" * 12):
        r = post_check(client, tid, kind="discovered", query=CONTROL)
        assert (r.status_code, r.json()["detail"]) == (404, "Объект из рекламы не найден"), tid
    assert len(geo_calls(geo)) == 2


# ---------- 3. квота, "не настроен", ответы 2ГИС ----------

def test_period_and_daily_quota(client, geo, env, dgis):
    env(DGIS_QUOTA_LIMIT=1)
    assert post_check(client, "c1").status_code == 201
    r = post_check(client, "c2")
    assert (r.status_code, r.json()["detail"]) == (429, "Лимит запросов 2ГИС по ключу исчерпан (1 из 1)")
    assert len(geo_calls(geo)) == 1
    env(DGIS_QUOTA_LIMIT=900, DGIS_DAILY_LIMIT=1)
    r = post_check(client, "c2")
    assert (r.status_code, r.json()["detail"]) == (429, "Дневной лимит сверок 2ГИС исчерпан (1)")
    assert len(geo_calls(geo)) == 1
    st = client.get("/api/geo/status").json()
    assert (st["used"], st["usedToday"], st["dailyLimit"]) == (1, 1, 1)  # учёт периода возвращён


def test_not_configured(client, env, http_mock, dgis):
    env(DGIS_API_KEY=None)
    r = post_check(client)
    assert (r.status_code, r.json()["detail"]) == (503, "2ГИС не настроен на сервере (DGIS_API_KEY)")
    st = client.get("/api/geo/status").json()
    assert (st["configured"], st["used"], st["usedToday"]) == (False, 0, 0)
    assert http_mock.calls == []


@pytest.mark.parametrize("status, payload, code, detail", [
    (200, error_body(400, "Parameter q is invalid"), 502, "2ГИС отклонил запрос: Parameter q is invalid"),
    (403, error_body(403, "Key is not valid"), 503, "Ключ 2ГИС отклонён — проверьте DGIS_API_KEY"),
    (401, error_body(401, "Unauthorized"), 503, "Ключ 2ГИС отклонён — проверьте DGIS_API_KEY"),
    (403, error_body(403, "Daily request limit exceeded"), 429, "2ГИС ограничил частоту запросов — повторите позже"),
    (429, error_body(429, "Too many requests"), 429, "2ГИС ограничил частоту запросов — повторите позже"),
    (500, None, 502, "2ГИС не ответил — повторите позже"),
])
def test_route_provider_errors(client, geo, dgis, status, payload, code, detail):
    if payload is None:
        geo.on("GET", dgis.GEOCODE, status=status, content=b"Internal Server Error")
    else:
        geo.on("GET", dgis.GEOCODE, status=status, json=payload)
    r = post_check(client)
    assert (r.status_code, r.json()["detail"]) == (code, detail)
    assert geo_rows() == 0  # сбой - не "адрес не найден" и не пустая сверка
    assert counter("dgis:") == 1  # запрос к 2ГИС выполнен - учтён
    conn = client.get("/api/sources/connectors/dgis").json()
    if code == 503:  # ключ отклонён - видно на странице "Подключения"
        assert (conn["status"], conn["statusDetail"]) == ("expired", "Ключ 2ГИС отклонён")
    elif code == 429:  # ограничение 2ГИС - пауза, видна на странице "Подключения"
        assert conn["status"] == "rate_limited" and "повтор после" in conn["statusDetail"]
    else:
        assert conn["status"] not in ("expired", "rate_limited")


def test_route_not_found(client, geo, dgis):
    geo.on("GET", dgis.GEOCODE, status=404, json=error_body(404, "Results not found", "itemNotFound"))
    r = post_check(client)
    assert r.status_code == 201
    g = r.json()
    assert (g["status"], g["candidates"]) == ("not_found", [])
    assert audit_rows("Сверка адреса в 2ГИС")[0][2].endswith("кандидатов: 0")
    r = client.post(f"/api/geo/checks/{g['id']}/reject", json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "2ГИС не нашёл адрес — отклонять нечего")
    assert post_check(client).json()["cached"] is True  # и отрицательный ответ берётся из кэша


@pytest.mark.parametrize("status, payload, detail", MALFORMED)
def test_route_unknown_format_is_not_cached_as_not_found(client, geo, dgis, run_worker, status, payload, detail):
    """Ответ неизвестного формата - 502, без записи сверки и без кэша: повтор снова идёт в 2ГИС; проверка
    подключения - ошибка формата, а не "2ГИС не нашёл контрольный адрес"."""
    _respond(geo, dgis, status, payload)
    for n in (1, 2):
        r = post_check(client)
        assert (r.status_code, r.json()["detail"]) == (502, detail)
        assert geo_rows() == 0 and len(geo_calls(geo)) == n
    assert audit_rows("Сверка адреса в 2ГИС") == []
    run = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR}).json()
    run_worker()
    got = client.get(f"/api/collection-runs/{run['id']}").json()
    assert (got["status"], got["error"]) == ("failed", detail)
    assert client.get("/api/sources/connectors/dgis").json()["status"] == "error"


# ---------- 3а. одновременные сверки и пауза после ограничения 2ГИС ----------

def lock_rows() -> list:
    with SessionLocal() as db:
        L = models.WorkerLease
        return list(db.scalars(select(L.name).where(L.name.startswith("dgis-check:"))))


def test_concurrent_checks_of_one_target_do_not_duplicate(client, geo, dgis, monkeypatch):
    """Две вкладки(или два аналитика) одновременно нажали "Сверить адрес" по одному ЖК: к 2ГИС - один запрос, одна
    запись, одна единица квоты; вторая сверка - 429, другой объект не ждёт. После - ответ из кэша."""
    real = dgis.geocode
    inside, go = threading.Event(), threading.Event()
    queries: list = []

    def slow(query, *, client, key):
        queries.append(query)
        if len(queries) == 1:  # первая сверка "ждёт ответа 2ГИС"
            inside.set()
            assert go.wait(10)
        return real(query, client=client, key=key)

    monkeypatch.setattr(dgis, "geocode", slow)
    first: dict = {}
    th = threading.Thread(target=lambda: first.setdefault("r", post_check(client)))
    th.start()
    try:
        assert inside.wait(10)
        for kw in ({}, {"query": CONTROL}):  # тот же запрос и другой запрос по тому же объекту
            r = post_check(client, **kw)
            assert (r.status_code, r.json()["detail"]) == (429, "Сверку этого объекта уже выполняют — повторите через "
                                                                "минуту")
        assert post_check(client, "c3").status_code == 201  # блокировка - по объекту
    finally:
        go.set()
        th.join(10)
    assert first["r"].status_code == 201, first["r"].text
    assert len(queries) == 2 and len(geo_calls(geo)) == 2 and counter("dgis:") == 2 and geo_rows() == 2
    again = post_check(client)
    assert again.status_code == 200 and again.json()["id"] == first["r"].json()["id"]
    assert lock_rows() == []  # блокировки сняты


def test_check_lock_is_released_and_expires(client, geo, dgis):
    """Блокировка другой сверки - 429 без запроса и квоты; блокировка упавшего процесса истекает; после ошибки
    2ГИС блокировка снимается."""
    name = dgis._lock_name("complex", "c4")
    now = models.utcnow()
    with SessionLocal() as db:
        db.add(models.WorkerLease(name=name, holder="другой-процесс", expires_at=now + timedelta(seconds=60),
                                  heartbeat_at=now, info={}))
        db.commit()
    r = post_check(client)
    assert (r.status_code, r.json()["detail"]) == (429, "Сверку этого объекта уже выполняют — повторите через минуту")
    assert geo_calls(geo) == [] and counter("dgis:") == 0
    with SessionLocal() as db:
        db.get(models.WorkerLease, name).expires_at = now - timedelta(seconds=1)
        db.commit()
    assert post_check(client).status_code == 201
    geo.on("GET", dgis.GEOCODE, status=500, content=b"Internal Server Error")
    assert post_check(client, "c3").status_code == 502
    assert lock_rows() == []
    assert len(dgis._lock_name("registry", "x" * 96)) <= 64  # имя аренды укладывается в String(64)


def test_provider_limit_pauses_route_checks(client, geo, dgis, run_worker, monkeypatch):
    """2ГИС ограничил частоту при сверке: подключение "пауза: лимит запросов" до срока Retry-After, сверки до него -
    429 без обращения к 2ГИС и без расхода квоты(кэш отвечает); после паузы удачная сверка возвращает прежний
    статус."""
    client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
    run_worker()
    ready = client.get("/api/sources/connectors/dgis").json()
    assert ready["status"] == "ready"
    c1 = post_check(client, "c1").json()
    calls, used = len(geo_calls(geo)), counter("dgis:")

    geo.on("GET", dgis.GEOCODE, status=403, json=error_body(403, "Request limit exceeded"),
           headers={"Retry-After": "1800"})
    r = post_check(client, "c4")
    assert (r.status_code, r.json()["detail"]) == (429, "2ГИС ограничил частоту запросов — повторите позже")
    conn = client.get("/api/sources/connectors/dgis").json()
    assert (conn["status"], conn["statusLabel"]) == ("rate_limited", "пауза: лимит запросов")
    assert conn["statusDetail"].startswith("2ГИС: лимит запросов по ключу исчерпан (Request limit exceeded) — повтор "
                                           "после ")
    assert conn["nextRunAt"] and conn["lastError"]
    assert len(audit_rows("Источник «2ГИС — геокодер»: пауза: лимит запросов")) == 1

    for cid in ("c4", "c3"):
        r = post_check(client, cid)
        assert r.status_code == 429
        assert r.json()["detail"].startswith("2ГИС ограничил частоту запросов — повторите после ")
    forced = client.post("/api/geo/checks", headers=admin(), json={"targetKind": "complex", "targetId": "c1",
                                                                   "author": AUTHOR, "force": True})
    assert forced.status_code == 429  # "Обновить запрос" администратора тоже ждёт
    cached = post_check(client, "c1")
    assert cached.status_code == 200 and cached.json()["id"] == c1["id"]  # кэш отвечает и во время паузы
    assert len(geo_calls(geo)) == calls + 1 and counter("dgis:") == used + 1
    assert client.get("/api/geo/status").json()["used"] == used + 1

    geo.on("GET", dgis.GEOCODE, json=body([EXACT, NUMBER]))
    later = dgis._now() + timedelta(minutes=31)
    monkeypatch.setattr(dgis, "_now", lambda: later)
    assert post_check(client, "c3").status_code == 201
    conn = client.get("/api/sources/connectors/dgis").json()
    assert (conn["status"], conn["statusDetail"]) == ("ready", ready["statusDetail"])
    assert len(audit_rows("Источник «2ГИС — геокодер»: готов")) == 2
    with SessionLocal() as db:
        assert "pause" not in db.get(models.Collector, "dgis").state


def test_check_limit_pauses_route_until_check_or_new_key(client, geo, dgis, env, run_worker):
    """Ограничение 2ГИС при проверке подключения тоже останавливает сверки; удачная проверка или новый ключ снимают
    паузу. Исчерпанная локальная квота паузу 2ГИС не ставит: после увеличения лимита сверка идёт сразу."""
    def check():
        client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
        run_worker()
        return client.get("/api/sources/connectors/dgis").json()["status"]

    geo.on("GET", dgis.GEOCODE, status=429, json=error_body(429, "Too many requests"), headers={"Retry-After": "600"},
           times=1)
    assert check() == "rate_limited"
    r = post_check(client, "c4")
    assert r.status_code == 429 and r.json()["detail"].startswith("2ГИС ограничил частоту запросов — повторите после")
    assert len(geo_calls(geo)) == 1
    assert check() == "ready"
    assert post_check(client, "c4").status_code == 201

    env(DGIS_QUOTA_LIMIT=0)
    assert check() == "rate_limited"  # локальная квота: пауза воркера, но не отметка 2ГИС
    env(DGIS_QUOTA_LIMIT=900)
    assert post_check(client, "c2").status_code == 201

    geo.on("GET", dgis.GEOCODE, status=429, json=error_body(429, "Too many requests"), headers={"Retry-After": "600"},
           times=1)
    assert check() == "rate_limited"
    assert post_check(client, "c3").status_code == 429
    env(DGIS_API_KEY="FAKEDGIS-other-0123456789abcd")
    assert post_check(client, "c3").status_code == 201  # пауза - по ключу


# ---------- 4. ключ не утекает ----------

def test_key_never_leaks(client, geo, dgis, caplog, run_worker):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")
    geo.on("GET", dgis.GEOCODE, status=200, json=error_body(400, f"Invalid parameter key={KEY} ({KEY})"))
    r = post_check(client)
    assert r.status_code == 502 and KEY not in r.text and "***" in r.json()["detail"]
    calls = geo_calls(geo)
    assert calls and all(c.url.params["key"] == KEY for c in calls)  # ключ - только в запросе к 2ГИС
    run = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR}).json()
    assert run_worker() >= 1
    got = client.get(f"/api/collection-runs/{run['id']}")
    assert got.json()["status"] == "failed" and KEY not in got.text
    connectors = client.get("/api/sources/connectors")
    assert KEY not in connectors.text and "dgis" in connectors.text
    for path in ("/api/geo/checks", "/api/geo/status", "/api/audit"):
        assert KEY not in client.get(path).text
    assert KEY not in caplog.text
    assert not re.search(r"key=(?!\*\*\*)[A-Za-z0-9-]{8,}", caplog.text)
    texts = []
    with SessionLocal() as db:
        for m in (models.CollectionRun, models.Collector, models.AuditEvent, models.Job, models.QuotaCounter,
                  models.GeoCheck):
            texts += [repr({k: v for k, v in obj.__dict__.items() if not k.startswith("_")})
                      for obj in db.scalars(select(m))]
    assert texts and KEY not in "".join(texts)


# ---------- 5. решения аналитика ----------

def test_accept_without_and_with_apply(client, geo, dgis):
    before = complex_point("c4")
    g = post_check(client, "c4").json()
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["status"], a["chosenId"], a["applied"], a["decidedBy"]) == ("accepted", EXACT_ID, False, AUTHOR)
    assert a["decidedAt"] and a["prevPoint"] == {"lat": before[0], "lng": before[1]}
    assert complex_point("c4") == before  # координаты не изменены
    assert audit_rows("Сверка адреса 2ГИС принята") == [
        ("data", AUTHOR, f"Сверка адреса 2ГИС принята: «Усть-Каменогорск, ЖК Asyl Tas» (объект {EXACT_ID}); "
                         "координаты в карточке не изменены", "c4")]
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": "Петров А."})
    assert (r.status_code, r.json()["detail"]) == (409, f"Сверку уже решили: принята ({AUTHOR})")
    r = client.post(f"/api/geo/checks/{g['id']}/reject", json={"author": "Петров А."})
    assert (r.status_code, r.json()["detail"]) == (409, f"Сверку уже решили: принята ({AUTHOR})")

    # перенос координат в карточку ЖК: условный UPDATE, "было → стало" в журнал
    old = complex_point("c3")
    g3 = post_check(client, "c3").json()
    r = client.post(f"/api/geo/checks/{g3['id']}/accept", json={"candidateId": "nope", "author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (422, "Этого кандидата в сверке нет")
    r = client.post(f"/api/geo/checks/{g3['id']}/accept",
                    json={"candidateId": NUMBER_ID, "author": AUTHOR, "apply": True, "comment": "сверено на месте"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["status"], a["chosenId"], a["applied"], a["comment"]) == ("accepted", NUMBER_ID, True,
                                                                        "сверено на месте")
    assert a["prevPoint"] == {"lat": old[0], "lng": old[1]}
    assert complex_point("c3") == (49.88901, 82.61102)
    (row,) = audit_rows("уточнены по 2ГИС")
    assert row == ("data", AUTHOR, f"Координаты ЖК «Ульба Ривер» уточнены по 2ГИС после проверки аналитиком: было "
                                   f"{old[0]:.6f}, {old[1]:.6f} → стало 49.889010, 82.611020 (объект 2ГИС {NUMBER_ID}, "
                                   f"«Усть-Каменогорск, Увалиева, 8/3а»). Комментарий: сверено на месте", "c3")
    assert client.get("/api/complexes/c3").json()["lat"] == 49.88901


def test_accept_point_changed_meanwhile(client, geo, dgis):
    g = post_check(client, "c2").json()
    with SessionLocal() as db:
        db.get(models.Complex, "c2").lat = 49.95
        db.commit()
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR,
                                                               "apply": True})
    assert (r.status_code, r.json()["detail"]) == (
        409, "Координаты ЖК изменились после сверки — обновите карточку и выполните сверку заново")
    assert complex_point("c2")[0] == 49.95
    assert client.get("/api/geo/checks", params={"targetId": "c2"}).json()[0]["status"] == "candidates"
    assert audit_rows("уточнены по 2ГИС") == []


def test_accept_race_on_coordinates_rolls_back(client, geo, dgis, monkeypatch):
    """Координаты поменялись между чтением и UPDATE: решение не сохраняется, 409."""
    g = post_check(client, "c2").json()
    real = dgis._target

    def target(db, kind, tid):
        t = real(db, kind, tid)
        db.get(models.Complex, tid).lng = 82.0  # "другой аналитик" успел изменить координаты
        db.flush()
        return t

    monkeypatch.setattr(dgis, "_target", target)
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR,
                                                               "apply": True})
    assert r.status_code == 409
    with SessionLocal() as db:
        assert db.get(models.GeoCheck, g["id"]).status == "candidates"


def test_accept_lost_race_answers_by_fresh_row(client, geo, dgis, monkeypatch):
    """Сверку очистили по сроку или удалили между чтением и UPDATE: 409 "устарели" или 404, а не "уже решили: ждёт
    решения" и не 500."""
    from sqlalchemy import delete, update

    G = models.GeoCheck
    g2, g3 = post_check(client, "c2").json(), post_check(client, "c3").json()
    real = dgis._target

    def purging(db, kind, tid):
        t = real(db, kind, tid)
        db.execute(update(G).where(G.id == g2["id"]).values(candidates=[], purged_at=models.utcnow()))
        db.commit()  # "уборка воркера" успела раньше
        return t

    monkeypatch.setattr(dgis, "_target", purging)
    r = client.post(f"/api/geo/checks/{g2['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "Кандидаты устарели — выполните сверку заново")

    def deleting(db, kind, tid):
        t = real(db, kind, tid)
        db.execute(delete(G).where(G.id == g3["id"]))
        db.commit()
        return t

    monkeypatch.setattr(dgis, "_target", deleting)
    r = client.post(f"/api/geo/checks/{g3['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (404, "Сверка не найдена")
    assert audit_rows("Сверка адреса 2ГИС принята") == []


def test_accept_decided_check_after_purge_reports_decision(client, geo, dgis):
    """Решённая сверка после уборки кэша - "уже решили", а не "выполните сверку заново"; у "адрес не найден" - нет
    кандидата(422)."""
    from app import worker

    g = post_check(client, "c4").json()
    client.post(f"/api/geo/checks/{g['id']}/reject", json={"author": AUTHOR})
    with SessionLocal() as db:
        worker.housekeeping(db, dgis._now() + timedelta(days=8))
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, f"Сверку уже решили: отклонена ({AUTHOR})")

    geo.on("GET", dgis.GEOCODE, json=body([]))
    nf = post_check(client, "c3").json()
    assert nf["status"] == "not_found"
    r = client.post(f"/api/geo/checks/{nf['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (422, "Этого кандидата в сверке нет")


def test_registry_target_never_modified(client, geo, dgis):
    from app import registry as opendata

    with SessionLocal() as db:
        before = opendata.records(db, "park-line")
    g = post_check(client, "park-line", kind="registry").json()
    assert audit_rows("Сверка адреса в 2ГИС")[0][3] is None
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR,
                                                               "apply": True})
    assert (r.status_code, r.json()["detail"]) == (
        422, "Сведения открытого источника не изменяются — координаты 2ГИС сохраняются как сверка")
    r = client.post(f"/api/geo/checks/{g['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert r.status_code == 200 and r.json()["applied"] is False
    (row,) = audit_rows("Сверка адреса 2ГИС принята")
    assert row[3] is None  # complex_id - только у сверки ЖК
    with SessionLocal() as db:
        assert opendata.records(db, "park-line") == before


# ---------- 6. отклонение и уборка кэша ----------

def test_reject_and_purge(client, geo, dgis, monkeypatch):
    from app import worker

    g = post_check(client, "c1").json()
    r = client.post(f"/api/geo/checks/{g['id']}/reject", json={"author": AUTHOR, "comment": "ни один не подходит"})
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["decidedBy"], r.json()["chosenId"]) == ("rejected", AUTHOR, None)
    (row,) = audit_rows("Кандидаты 2ГИС отклонены")
    assert row == ("data", AUTHOR, f"Кандидаты 2ГИС отклонены: «{g['query']}» — координаты в карточке не изменены. "
                                   "Комментарий: ни один не подходит", "c1")
    r = client.post(f"/api/geo/checks/{g['id']}/reject", json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, f"Сверку уже решили: отклонена ({AUTHOR})")

    g4 = post_check(client, "c4").json()
    client.post(f"/api/geo/checks/{g4['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    g3 = post_check(client, "c3").json()  # ждёт решения
    later = dgis._now() + timedelta(days=8)
    with SessionLocal() as db:
        worker.housekeeping(db, later)
    listed = {x["id"]: x for x in client.get("/api/geo/checks").json()}
    for x in listed.values():
        assert (x["candidates"], x["purged"], x["expiresAt"]) == ([], True, None)
    assert (listed[g4["id"]]["status"], listed[g4["id"]]["chosenId"], listed[g4["id"]]["decidedBy"]) == (
        "accepted", EXACT_ID, AUTHOR)  # решение остаётся
    assert listed[g4["id"]]["chosen"]["id"] == EXACT_ID
    assert listed[g["id"]]["status"] == "rejected"
    r = client.post(f"/api/geo/checks/{g3['id']}/accept", json={"candidateId": EXACT_ID, "author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "Кандидаты устарели — выполните сверку заново")
    # после уборки - новый запрос к 2ГИС, не кэш
    calls = len(geo_calls(geo))
    monkeypatch.setattr(dgis, "_now", lambda: later)
    r = post_check(client, "c3")
    assert r.status_code == 201 and r.json()["cached"] is False and len(geo_calls(geo)) == calls + 1


def test_cache_expires_by_ttl_without_housekeeping(client, geo, env, dgis, monkeypatch):
    env(DGIS_CACHE_TTL_DAYS=1)
    g = post_check(client).json()
    assert g["expiresAt"]
    later = dgis._now() + timedelta(days=1, minutes=1)
    monkeypatch.setattr(dgis, "_now", lambda: later)
    r = post_check(client)
    assert r.status_code == 201 and r.json()["id"] != g["id"] and len(geo_calls(geo)) == 2


# ---------- 7. проверка подключения ----------

def test_connector_check_via_worker(client, geo, dgis, run_worker):
    conn = client.get("/api/sources/connectors/dgis").json()
    assert (conn["kind"], conn["group"], conn["status"], conn["capabilities"]["test"],
            conn["capabilities"]["collect"]) == ("geo", "geo", "unchecked", True, False)
    r = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
    assert r.status_code == 202, r.text
    assert run_worker() >= 1
    run = client.get(f"/api/collection-runs/{r.json()['id']}").json()
    assert run["status"] == "succeeded"
    conn = client.get("/api/sources/connectors/dgis").json()
    assert (conn["status"], conn["statusLabel"]) == ("ready", "готов")
    assert conn["statusDetail"] == (f"Кандидатов: 2; точный адрес — «Усть-Каменогорск, Увалиева, 8/3» (ЖК Asyl Tas, "
                                    f"объект {EXACT_ID})")
    assert (conn["facts"]["candidates"], conn["facts"]["recommendedId"], conn["facts"]["used"]) == (2, EXACT_ID, 1)
    (req,) = geo_calls(geo)
    assert req.url.params["q"] == CONTROL
    assert client.get("/api/geo/status").json()["used"] == 1  # проверка учитывается в квоте

    # 2ГИС не нашёл контрольный адрес - проверка не прошла(ok=False → ошибка запуска)
    geo.on("GET", dgis.GEOCODE, json=body([]))
    r = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
    assert run_worker() >= 1
    run = client.get(f"/api/collection-runs/{r.json()['id']}").json()
    assert (run["status"], run["error"]) == (
        "failed", "2ГИС не нашёл контрольный адрес «Казахстан, Усть-Каменогорск, Увалиева 8/3» — кандидатов нет")
    conn = client.get("/api/sources/connectors/dgis").json()
    assert conn["status"] == "error"


def test_connector_check_variants(client, geo, dgis, run_worker):
    # ключ отклонён → "истёк доступ"; затем удачная проверка возвращает "готов"
    geo.on("GET", dgis.GEOCODE, status=401, json=error_body(401, "Unauthorized"), times=1)
    client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
    run_worker()
    assert client.get("/api/sources/connectors/dgis").json()["status"] == "expired"
    client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
    run_worker()
    assert client.get("/api/sources/connectors/dgis").json()["status"] == "ready"


def test_connector_check_without_exact(geo, dgis):
    from app.integrations.context import RunContext

    geo.on("GET", dgis.GEOCODE, json=body([NUMBER]))
    res = dgis.CONNECTOR.check(RunContext.ephemeral("dgis"))
    assert res.ok is True and res.facts["recommendedId"] is None
    assert res.detail == "Кандидатов: 1; точного совпадения нет — выбор за аналитиком"


def test_connector_not_configured(client, env, http_mock, dgis):
    env(DGIS_API_KEY=None, SAQ_ADMIN_TOKEN=FAKE_ADMIN_TOKEN)
    conn = client.get("/api/sources/connectors/dgis").json()
    assert (conn["status"], conn["missing"]) == ("not_configured", ["DGIS_API_KEY"])
    r = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (409, "Сначала настройте подключение на сервере: DGIS_API_KEY")
    assert http_mock.calls == []


def test_smoke_is_not_counted(client, geo, dgis):
    from app.integrations import smoke

    out = smoke.check_one("dgis")
    assert out.startswith("dgis: готов — Кандидатов: 2; точный адрес — ")
    assert "(запрос не учтён в счётчике квоты)" in out and KEY not in out
    assert counter("dgis") == 0 and len(geo_calls(geo)) == 1


# ---------- приёмка по §14 плана(применимое к геосервису) ----------

def test_acceptance_repeat_does_not_duplicate(client, geo, dgis):
    """"повтор не создаёт дубликат": повторная сверка того же адреса - та же запись из кэша, без запроса к 2ГИС."""
    first = post_check(client).json()
    for _ in range(3):
        again = post_check(client)
        assert again.status_code == 200 and again.json()["id"] == first["id"]
    assert geo_rows() == 1 and len(geo_calls(geo)) == 1 and counter("dgis:") == 1
    assert len(audit_rows("Сверка адреса в 2ГИС")) == 1


def test_acceptance_failure_is_not_empty(client, geo, dgis, run_worker, accept):
    """"сбой не выглядит пустой выдачей": ошибка 2ГИС - не "адрес не найден" и не "готов"."""
    geo.on("GET", dgis.GEOCODE, status=200, json=error_body(400, "Parameter fields is invalid"))
    r = post_check(client)
    assert r.status_code == 502 and geo_rows() == 0
    run = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR}).json()
    run_worker()
    c = accept.assert_failure_visible("dgis", run["id"])
    assert c["status"] == "error" and "2ГИС отклонил запрос" in (c["statusDetail"] or c["lastError"])


def test_acceptance_not_confirmed_automatically(client, geo, dgis):
    """"новая публикация не получает подтверждение автоматически" - для геосервиса: рекомендованный кандидат не
    принимается сам, координаты ЖК не меняются без решения аналитика."""
    before = complex_point("c4")
    g = post_check(client).json()
    assert any(c["recommended"] for c in g["candidates"])
    assert (g["status"], g["chosenId"], g["applied"], g["decidedBy"]) == ("candidates", None, False, None)
    assert complex_point("c4") == before
    assert audit_rows("уточнены по 2ГИС") == [] and audit_rows("принята") == []


def test_acceptance_limit_stops_calls(client, geo, env, dgis, run_worker):
    """"превышение лимита не вызывает бесконечный цикл": квота исчерпана - к 2ГИС не обращаемся ни из маршрута, ни из
    проверки воркера; ограничение 2ГИС - пауза, а не повтор."""
    env(DGIS_QUOTA_LIMIT=0)
    for cid in ("c1", "c2", "c3"):
        assert post_check(client, cid).status_code == 429
    run = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR}).json()
    run_worker()
    assert client.get(f"/api/collection-runs/{run['id']}").json()["status"] == "rate_limited"
    assert client.get("/api/sources/connectors/dgis").json()["status"] == "rate_limited"
    assert geo_calls(geo) == []
    env(DGIS_QUOTA_LIMIT=900)
    geo.on("GET", dgis.GEOCODE, status=429, json=error_body(429, "Too many requests"))
    run = client.post("/api/sources/dgis/test", headers=admin(), json={"author": AUTHOR}).json()
    run_worker()
    run_worker()
    assert client.get(f"/api/collection-runs/{run['id']}").json()["status"] == "rate_limited"
    assert len(geo_calls(geo)) == 1
