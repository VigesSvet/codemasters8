import json

from app.risk import score_complex
from app.risk_model import SHARED, VERSION, T

AS_OF = "2026-10-15"  # дата контрольных значений(как в shared/testdata/risk_golden.json)


def _risk(c, cid):
    return c.get(f"/api/complexes/{cid}/risk", params={"asOf": AS_OF}).json()


def _f(r, code):
    return next(f for f in r["factors"] if f["code"] == code)


def test_reference_data(client):
    assert client.get("/api/health").json()["ok"]
    cs = client.get("/api/complexes").json()
    assert len(cs) == 14
    c1 = next(c for c in cs if c["id"] == "c1")
    assert c1["readiness"]["declared"]["value"] == 60 and c1["readiness"]["confirmed"]["source"]
    assert c1["basis"]["source"] and c1["basis"]["checkedAt"]
    assert client.get("/api/complexes/c14").json()["readiness"] == {"declared": None, "confirmed": None}
    assert len(client.get("/api/developers").json()) == 7

    aps = client.get("/api/appeals").json()
    assert len(aps) == 5
    assert {"id", "complexId", "collective", "applicants", "status", "verifiedAt", "subject", "source"} <= set(aps[0])
    assert [a["id"] for a in client.get("/api/appeals", params={"complexId": "c4"}).json()] == ["ap1"]


def test_traffic_light_and_control_cases(client):
    st = client.get("/api/stats", params={"asOf": AS_OF}).json()
    assert st["zones"] == {"red": 3, "yellow": 3, "green": 8}
    assert st["ads"] == 12 and st["signals"] == {"new": 0, "confirmed": 0, "returned": 0}
    assert {c["id"] for c in client.get("/api/complexes", params={"zone": "red", "asOf": AS_OF}).json()} == {"c2", "c4", "c11"}

    # Кейс B: красная 70, R1 + R2 подтверждены
    r = _risk(client, "c2")
    assert (r["score"], r["zone"]) == (70, "red") and _f(r, "R1")["state"] == _f(r, "R2")["state"] == "hit"
    assert r["modelVersion"] == VERSION and len(r["observations"]) == 3
    assert any(i["kind"] == "ad" and i["quote"] and i["marker"] for i in _f(r, "R2")["items"])

    # Кейс C: гарантия ЕО есть, МИО нет → это не R1
    r = _risk(client, "c10")
    assert _f(r, "R1")["state"] == "clear" and _f(r, "R1")["reason"] == "ok_guarantee_no_mio" and r["zone"] == "green"

    # Кейс A: нет сведений об основании → 0 баллов за R1 и "нужна проверка"
    r = _risk(client, "c14")
    assert _f(r, "R1")["state"] == "unknown" and r["needsCheck"] and "R1" in r["summary"]

    # 60 баллов без пары R1+R2 - жёлтая
    r = _risk(client, "c8")
    assert (r["score"], r["zone"]) == (60, "yellow")


def test_risk_endpoints_agree_with_engine(client):
    rows = client.get("/api/risk", params={"asOf": AS_OF}).json()
    assert len(rows) == 14
    for row in rows:
        one = _risk(client, row["complexId"])
        assert {k: v for k, v in row.items() if k != "complexId"} == one

    # API(ORM → camelCase) считает так же, как движок на исходном сиде
    seed = json.loads((SHARED / "testdata" / "demo_seed.json").read_text(encoding="utf-8"))
    devs = {d["id"]: d for d in seed["developers"]}
    by_id = {r["complexId"]: r for r in rows}
    for c in seed["complexes"]:
        want = score_complex(c, devs.get(c["developerId"]), seed["ads"], seed["appeals"], AS_OF)
        assert {k: v for k, v in by_id[c["id"]].items() if k != "complexId"} == want, c["id"]

    m = client.get("/api/risk/model").json()
    assert m["version"] == VERSION and [f["weight"] for f in m["factors"]] == [35, 25, 10, 15, 10, 5]
    assert client.get("/api/risk", params={"asOf": "15.10.2026"}).status_code == 422


def test_manual_ad_and_status_journal(client):
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@test",
                                       "text": "Старт продаж ЖК Алтай Резиденс, бронь"}).json()
    # ИИ нашёл название ЖК → привязка-гипотеза с уверенностью ИИ; фактом она не становится: R1 объекта в оценку не входит
    assert ad["complexId"] == "c2" and ad["matchConfidence"] == 0.8 and ad["linkConfirmedBy"] is None
    assert T["lowConfidence"] <= ad["matchConfidence"] and "linkConfirmed" not in T and "autoLinkConfidence" not in T
    s = client.get(f"/api/ads/{ad['id']}/score").json()
    assert (s["score"], s["zone"], s["needsCheck"]) == (35, "yellow", True)
    assert s["complexId"] == "c2" and s["matchConfidence"] == 0.8
    assert [f["code"] for f in s["factors"]] == ["R1", "R2", "R3"] and "бронь" in s["signals"]["funds"]
    assert (_f(s, "R1")["state"], _f(s, "R1")["reason"]) == ("unknown", "R1_link_unconfirmed")

    # аналитик подтвердил привязку → факт: R1 объекта входит в оценку, кейс B - красная 70
    r = client.patch(f"/api/ads/{ad['id']}/complex", json={"complexId": "c2", "author": "Тест"})
    assert r.status_code == 200 and (r.json()["complexId"], r.json()["matchConfidence"]) == ("c2", 1)
    assert r.json()["linkConfirmedBy"] == "Тест" and r.json()["linkConfirmedAt"].endswith("+00:00")
    s = client.get(f"/api/ads/{ad['id']}/score").json()
    assert (s["score"], s["zone"], s["needsCheck"], s["matchConfidence"]) == (70, "red", False, 1)

    r = client.patch(f"/api/ads/{ad['id']}/status", json={"status": "confirmed", "author": "Тест", "assignee": "Тест",
                                                           "comment": "Сверено с картотекой ГАСК"}).json()
    assert r["status"] == "confirmed" and r["assignee"] == "Тест"
    log = client.get(f"/api/ads/{ad['id']}/audit").json()
    assert [(e["kind"], e["who"]) for e in log] == [("system", "Система"), ("ai", "Система"), ("link", "Тест"),
                                                    ("status", "Тест")]
    assert log[0]["what"] == "Публикация добавлена вручную"
    assert log[1]["what"] == "Гипотеза ИИ: ЖК «Алтай Резиденс», уверенность 80% (название) — нужна проверка аналитиком"
    assert log[2]["what"] == "Привязка к ЖК «Алтай Резиденс» подтверждена аналитиком"
    assert log[3]["what"] == ("Статус: «Новая» → «Нарушение подтверждено» · ответственный: Тест. "
                              "Основание: Сверено с картотекой ГАСК")
    assert all(e["at"].endswith("+00:00") and e["adId"] == ad["id"] and e["complexId"] == "c2" for e in log)

    # без признаков ЖК - без привязки, в журнале - что ЖК не определён
    ad = client.post("/api/ads", json={"platform": "olx", "account": "@x", "text": "Продаю гараж"}).json()
    assert ad["complexId"] is None and ad["matchConfidence"] == 0 and ad["linkConfirmedBy"] is None
    assert [(e["kind"], e["what"]) for e in client.get(f"/api/ads/{ad['id']}/audit").json()][-1] == (
        "ai", "ИИ: ЖК не определён — нужна ручная привязка")
    assert client.post("/api/ads", json={"platform": "olx", "account": "@x", "text": "x",
                                         "matchConfidence": 1.5}).status_code == 422


def test_link_ad_to_complex(client):
    def link(aid, cid, author="Тест"):
        return client.patch(f"/api/ads/{aid}/complex", json={"complexId": cid, "author": author})

    def journal(aid):
        return [(e["who"], e["what"]) for e in client.get(f"/api/ads/{aid}/audit").json()]

    def risk_c1():
        return next(r for r in client.get("/api/risk", params={"asOf": AS_OF}).json() if r["complexId"] == "c1")

    # a12 без привязки; привязали к c1 → у c1 появились R2/R3(публикация с маркерами "вход от", "₸")
    assert (risk_c1()["score"], risk_c1()["zone"]) == (0, "green")
    r = link("a12", "c1", author="  Нестерова А.  ")
    assert r.status_code == 200 and (r.json()["complexId"], r.json()["matchConfidence"]) == ("c1", 1)
    r1 = risk_c1()
    assert (r1["score"], r1["zone"]) == (35, "yellow") and _f(r1, "R2")["state"] == _f(r1, "R3")["state"] == "hit"
    assert journal("a12")[-1] == ("Нестерова А.", "Привязка к ЖК «Ертіс Парк» подтверждена аналитиком")

    # смена ЖК
    r = link("a12", "c3")
    assert r.status_code == 200 and r.json()["complexId"] == "c3"
    assert journal("a12")[-1] == ("Тест", "Привязка изменена: «Ертіс Парк» → «Ульба Ривер»")
    assert risk_c1()["score"] == 0

    # гипотеза из сида(a9 → c12, 54%) подтверждается тем же вызовом
    assert _f(_risk(client, "c12"), "R3")["state"] == "unknown"
    assert link("a9", "c12").json()["linkConfirmedBy"] == "Тест"
    assert _f(_risk(client, "c12"), "R3")["state"] == "hit"

    # снятие привязки сбрасывает и подтверждение
    r = link("a12", None)
    assert r.status_code == 200 and (r.json()["complexId"], r.json()["matchConfidence"]) == (None, 0)
    assert (r.json()["linkConfirmedBy"], r.json()["linkConfirmedAt"]) == (None, None)
    assert journal("a12")[-1] == ("Тест", "Привязка к ЖК снята")
    assert client.get("/api/ads/a12/score").json()["complexId"] is None

    # ошибки: нет ЖК / публикации → 404, пустой автор, без complexId или пустой complexId → 422
    assert link("a12", "nope").status_code == 404
    assert link("nope", "c1").status_code == 404
    assert link("a12", "c1", author="   ").status_code == 422
    assert client.patch("/api/ads/a12/complex", json={"author": "Тест"}).status_code == 422
    assert link("a12", "").status_code == 422
    assert client.get("/api/ads/a12/score").json()["complexId"] is None  # после ошибок ничего не изменилось


APP_TABLES = ("audit_events", "signals", "appeals", "ads", "complexes", "developers", "analysts", "meta")


def test_init_db_keeps_foreign_tables(client):
    from sqlalchemy import inspect, text

    from app import models
    from app.db import engine
    from app.seed import init_db, reset

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE keep_me (id INTEGER PRIMARY KEY, note TEXT)"))
        conn.execute(text("INSERT INTO keep_me (id, note) VALUES (1, 'чужие данные')"))
    try:
        # старая версия схемы → таблицы приложения пересоздаются и пересеваются, посторонняя таблица остаётся
        client.post("/api/signals", json={"complexId": "c2", "author": "Тест"})
        with engine.begin() as conn:
            conn.execute(text("UPDATE meta SET value = 'old' WHERE key = 'schema_version'"))
        init_db(engine)
        assert client.get("/api/signals").json() == [] and len(client.get("/api/complexes").json()) == 14
        reset(engine)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT note FROM keep_me")).scalar() == "чужие данные"
            assert conn.execute(text("SELECT value FROM meta")).scalar() == models.SCHEMA_VERSION

        # в базе только посторонние таблицы → ничего не удаляется, таблицы приложения создаются и сеются
        with engine.begin() as conn:
            for t in APP_TABLES:
                conn.execute(text(f"DROP TABLE {t}"))
        init_db(engine)
        assert {"keep_me", "meta", "signals", "analysts"} <= set(inspect(engine).get_table_names())
        assert len(client.get("/api/complexes").json()) == 14

        # старая схема без meta, FK на уже удалённую таблицу(так бывает в SQLite) - всё равно пересоздаётся
        fk = " REFERENCES complexes(id)" if engine.dialect.name == "sqlite" else ""
        with engine.begin() as conn:
            for t in APP_TABLES:
                conn.execute(text(f"DROP TABLE {t}"))
            conn.execute(text(f"CREATE TABLE ads (id TEXT PRIMARY KEY, complex_id TEXT{fk})"))
        init_db(engine)
        assert len(client.get("/api/ads").json()) == 12
        with engine.connect() as conn:
            assert conn.execute(text("SELECT note FROM keep_me")).scalar() == "чужие данные"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS keep_me"))


def test_not_found(client):
    assert client.get("/api/complexes/nope").status_code == 404
    assert client.get("/api/complexes/nope/risk").status_code == 404
    assert client.get("/api/ads/nope/score").status_code == 404
    assert client.patch("/api/ads/nope/status", json={"status": "in_review", "author": "Тест"}).status_code == 404
    assert client.get("/api/ads/nope/audit").status_code == 404
    assert client.patch("/api/ads/nope/complex", json={"complexId": "c1", "author": "Тест"}).status_code == 404


def test_manual_ad_with_suggested_link_is_hypothesis(client):
    def log(aid):
        return [(e["kind"], e["who"], e["what"]) for e in client.get(f"/api/ads/{aid}/audit").json()]

    # форма передаёт выбранную гипотезу ИИ - это гипотеза(в журнале kind ai), даже с высокой уверенностью
    for conf in (0.6, 1):
        body = {"platform": "instagram", "account": "@form", "complexId": "c3", "matchConfidence": conf,
                "text": "Старт продаж ЖК Ульба Ривер, бронь"}
        ad = client.post("/api/ads", json=body).json()
        assert (ad["complexId"], ad["matchConfidence"], ad["linkConfirmedBy"]) == ("c3", conf, None)
        assert log(ad["id"])[-1] == ("ai", "Система", f"Гипотеза ИИ: ЖК «Ульба Ривер», уверенность {int(conf * 100)}% "
                                                      "(название) — нужна проверка аналитиком")
    # без уверенности от формы - берётся уверенность анализа; ЖК, которого ИИ не предлагал, - "выбран в форме"
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@form", "text": "ЖК Ульба Ривер",
                                       "complexId": "c3"}).json()
    assert ad["matchConfidence"] == 0.8
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@form", "text": "Старт продаж, бронь",
                                       "complexId": "c7"}).json()
    assert ad["matchConfidence"] == 0 and "(выбран в форме ручного ввода)" in log(ad["id"])[-1][2]
    # аналитик выбрал ЖК сам(linkConfirmed) - привязка сразу факт, с автором и временем
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@form", "text": "Старт продаж, бронь",
                                       "complexId": "c3", "linkConfirmed": True, "author": "Тест"}).json()
    assert (ad["matchConfidence"], ad["linkConfirmedBy"]) == (1, "Тест") and ad["linkConfirmedAt"].endswith("+00:00")
    assert log(ad["id"]) == [("system", "Тест", "Публикация добавлена вручную"),
                             ("link", "Тест", "Привязка к ЖК «Ульба Ривер» подтверждена аналитиком")]
    assert _f(client.get(f"/api/ads/{ad['id']}/score").json(), "R1")["reason"] != "R1_link_unconfirmed"
    # подтвердить можно только выбранный ЖК и только от имени автора
    for bad in ({"complexId": "c3", "linkConfirmed": True}, {"linkConfirmed": True, "author": "Тест"}):
        r = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "т", **bad})
        assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "linkConfirmed", bad
    assert client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "т", "complexId": "nope"}).status_code == 404


def test_stats_counts_and_unlink_without_link(client):
    ads = client.get("/api/ads").json()
    st = client.get("/api/stats", params={"asOf": AS_OF}).json()
    assert st["ads"] == len(ads) and st["queue"] == sum(a["status"] in ("new", "needs_check") for a in ads)

    # снять привязку, которой нет, - без изменений и без записи в журнале
    assert client.get("/api/ads/a12").json()["complexId"] is None
    before = client.get("/api/ads/a12/audit").json()
    r = client.patch("/api/ads/a12/complex", json={"complexId": None, "author": "Тест"})
    assert r.status_code == 200 and r.json()["complexId"] is None
    assert client.get("/api/ads/a12/audit").json() == before


def test_body_limit_for_every_request(client, monkeypatch):
    """Тело любого запроса - не больше UploadLimit.DEFAULT: по Content-Length - 413 сразу, без него(chunked) - по мере
    чтения; запрос в пределах - как раньше."""
    from app.main import UploadLimit

    monkeypatch.setattr(UploadLimit, "DEFAULT", (300, "Тело запроса больше 16 МБ"))
    big = {"platform": "olx", "account": "@x", "text": "т" * 400}
    r = client.post("/api/ads", json=big)
    assert (r.status_code, r.json()) == (413, {"detail": "Тело запроса больше 16 МБ"})
    raw = json.dumps(big).encode()
    r = client.post("/api/ads", content=iter([raw[:200], raw[200:]]), headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()) == (413, {"detail": "Тело запроса больше 16 МБ"})
    assert client.post("/api/ads", json={"platform": "olx", "account": "@x", "text": "Продаю гараж"}).status_code == 201
    assert len(client.get("/api/ads").json()) == 13  # отклонённые не записаны
    # свои пределы загрузок важнее общего
    assert UploadLimit(None)._limit("POST", "/api/ocr")[0] == UploadLimit.LIMITS["/api/ocr"][0]
    assert UploadLimit(None)._limit("POST", "/api/intake/telegram")[2] == "telegram_bot"
    assert UploadLimit(None)._limit("PUT", "/api/ocr") == (300, "Тело запроса больше 16 МБ", None)


def test_public_host_guard_checks_every_forwarded_value(client, env):
    """Регрессия: проверялось только первое значение X-Forwarded-Host - прокси, дописавший свой заголовок или значение
    после присланного клиентом, пропускал бы API через туннель."""
    # evil.example и other.example - имена интерфейса: иначе их отклонил бы HostAllowlist(400) раньше этой проверки
    env(OLX_REDIRECT_URI="https://x.ngrok-free.dev/api/integrations/olx/callback",
        SAQ_WEB_HOSTS="testserver,evil.example,other.example")
    for headers in ([("X-Forwarded-Host", "evil.example"), ("X-Forwarded-Host", "x.ngrok-free.dev")],
                    {"X-Forwarded-Host": "evil.example, X.ngrok-free.dev:443"},
                    {"Forwarded": 'for=1.2.3.4;host="x.ngrok-free.dev";proto=https'},
                    {"Forwarded": "for=1.2.3.4, for=5.6.7.8;host=x.ngrok-free.dev."}):
        r = client.get("/api/ads", headers=headers)
        assert (r.status_code, r.json()) == (404, {"detail": "Не найдено"}), headers
    assert client.get("/api/ads", headers={"X-Forwarded-Host": "evil.example, [::1]:8000"}).status_code == 200
    assert client.get("/api/ads", headers={"Forwarded": "for=1.2.3.4;host=other.example"}).status_code == 200


def test_upload_limit_records_413_only_with_valid_token(client, env, monkeypatch):
    """Регрессия: 413 на приёме записывался до проверки токена - любой анонимный запрос с большим телом переводил
    приём публикаций или бот заявок в "ошибку". Теперь записывается только отказ запросу с верным токеном этого приёма;
    401/403(auth._record) записываются, как раньше."""
    from app.integrations import service
    from app.main import UploadLimit

    ingest, svc = "t-ingest-0123456789abcdef0123", "t-service-0123456789abcdef012"
    env(INTEGRATION_INGEST_TOKEN=ingest, SERVICE_API_TOKEN=svc)
    recorded = []
    monkeypatch.setattr(service, "record_reject", lambda *a: recorded.append(a))
    intake, bot = "/api/intake/publications", "/api/intake/telegram"
    assert set(UploadLimit.REJECTS.values()) == set(UploadLimit.TRUSTED)  # иначе KeyError → 500 вместо 413

    def post(path, headers):
        too_big = str(UploadLimit.LIMITS[path][0] + 1)
        return client.post(path, content=b"{}", headers={**headers, "Content-Type": "application/json",
                                                           "Content-Length": too_big})

    for path, headers in ((intake, {}), (intake, {"Authorization": "Bearer t-wrong-0123456789abcdef0123"}),
                          (intake, {"Authorization": f"Bearer {svc}"}),  # токен другого приёма
                          (bot, {}), (bot, {"X-API-Key": ingest}), (bot, {"Authorization": "Basic " + svc})):
        assert post(path, headers).status_code == 413, (path, headers)
    assert recorded == []
    assert post(intake, {"Authorization": f"bearer  {ingest} "}).status_code == 413
    assert post(bot, {"X-API-Key": svc}).status_code == 413
    assert post(bot, {"Authorization": f"Bearer {svc}"}).status_code == 413
    assert recorded == [("intake", 413, "Тело запроса больше 2 МБ"), ("telegram_bot", 413, "Заявка больше 60 МБ"),
                        ("telegram_bot", 413, "Заявка больше 60 МБ")]
    # без Content-Length(chunked): предел превышен по мере чтения - тоже только с верным токеном
    recorded.clear()
    monkeypatch.setitem(UploadLimit.LIMITS, intake, (100, "Тело запроса больше 2 МБ"))
    for headers in ({}, {"Authorization": f"Bearer {ingest}"}):
        r = client.post(intake, content=iter([b"[" + b" " * 80, b" " * 80 + b"]"]),
                        headers={**headers, "Content-Type": "application/json"})
        assert r.status_code in (401, 413), headers
    assert [x for x in recorded if x[1] == 413] == [("intake", 413, "Тело запроса больше 2 МБ")]
    # приём не настроен(503 у самого маршрута) - записывать нечего
    recorded.clear()
    env(INTEGRATION_INGEST_TOKEN=None)
    assert post(intake, {"Authorization": f"Bearer {ingest}"}).status_code == 413 and recorded == []


def test_token_checks_without_reject(env):
    """auth.is_ingest/is_service: те же заголовки, что у require_ingest/require_service, без отказа и записи."""
    from starlette.requests import Request

    from app import auth

    ingest, svc = "t-ingest-0123456789abcdef0123", "t-service-0123456789abcdef012"

    def req(**headers):
        return Request({"type": "http", "headers": [(k.lower().replace("_", "-").encode(), v.encode())
                                                    for k, v in headers.items()]})

    env(INTEGRATION_INGEST_TOKEN=ingest, SERVICE_API_TOKEN=svc)
    assert auth.is_ingest(req(Authorization=f"Bearer {ingest}"))
    assert not auth.is_ingest(req(X_API_Key=ingest)) and not auth.is_ingest(req())
    assert auth.is_service(req(Authorization=f"Bearer {svc}")) and auth.is_service(req(X_API_Key=svc))
    assert not auth.is_service(req(Authorization=f"Bearer {ingest}")) and not auth.is_service(req(X_API_Key=""))
    env(SERVICE_API_TOKEN=None)
    assert not auth.is_service(req(X_API_Key=svc))


def test_public_host_guard_methods_and_headers(client, env):
    """Через публичный хост путь обратного вызова OLX открыт только для GET и HEAD; ответ 404 - с nosniff и CSP,
    запрещающей любое содержимое и встраивание."""
    callback = "/api/integrations/olx/callback"
    env(OLX_REDIRECT_URI=f"https://x.ngrok-free.dev{callback}")
    host = {"Host": "x.ngrok-free.dev"}
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        r = client.request(method, callback, headers=host)
        assert (r.status_code, r.json()) == (404, {"detail": "Не найдено"}), method
    for r in (client.post(callback, headers=host), client.get("/api/ads", headers=host)):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    r = client.get(callback, headers=host)  # страница обратного вызова(state не передан - 400), не 404 guard'а
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/html")
    assert client.head(callback, headers=host).status_code != 404
    # обычный адрес - как раньше: другие методы и заголовки ответа не меняются
    assert client.post("/api/ads", json={"platform": "olx", "account": "@x", "text": "Продаю гараж"}).status_code == 201
    assert "content-security-policy" not in client.get("/api/nothing").headers


def test_cors_follows_settings(client, env):
    """CORS по SAQ_CORS_ORIGINS: по умолчанию выключен - чужая страница не читает ответы открытых эндпоинтов;
    список - только эти адреса(и предварительный запрос OPTIONS); "*" - любой адрес."""
    other = {"Origin": "https://evil.example"}
    r = client.get("/api/health", headers=other)
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in client.options(
        "/api/ads", headers={**other, "Access-Control-Request-Method": "POST"}).headers

    env(SAQ_CORS_ORIGINS="https://SAQ.example.kz/, http://localhost:5180")
    ok = {"Origin": "https://saq.example.kz"}
    assert client.get("/api/health", headers=ok).headers["access-control-allow-origin"] == "https://saq.example.kz"
    assert "access-control-allow-origin" not in client.get("/api/health", headers=other).headers
    pre = client.options("/api/ads", headers={**ok, "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "content-type"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "https://saq.example.kz"
    assert client.options("/api/ads", headers={**other, "Access-Control-Request-Method": "POST"}).status_code == 400

    env(SAQ_CORS_ORIGINS="*")
    assert client.get("/api/health", headers=other).headers["access-control-allow-origin"] == "*"


# ---------- имя сервера(подмена DNS) и запросы со страниц других сайтов(CSRF) ----------

def test_host_allowlist_rejects_unknown_names(client, env):
    """Подмена DNS(DNS rebinding): страница rebind.attacker.tld:8000, имя которой переведено на 127.0.0.1, для браузера -
    тот же источник, что и API. API отвечает только своим именам: localhost и *.localhost, IP-адресам, сервису api,
    именам интерфейса(SAQ_WEB_HOSTS, ".домен" - с поддоменами) и публичному хосту OLX; проверяются и X-Forwarded-Host,
    и host= из Forwarded. Другое имя - 400 до обработчика."""
    from app.main import HostAllowlist

    def get(path="/api/health", **headers):
        return client.get(path, headers=headers)

    for host in ("localhost:8000", "LOCALHOST.", "saq.localhost:5180", "127.0.0.1:8000", "[::1]:8000",
                 "192.168.1.5:5180", "100.101.102.103", "api:8000", "testserver"):
        assert get(Host=host).status_code == 200, host
    for headers in ({"Host": "rebind.attacker.tld:8000"}, {"Host": "localhost.evil.example"},
                    {"Host": "localhost", "X-Forwarded-Host": "127.0.0.1, evil.example"},
                    {"Host": "localhost", "Forwarded": "for=1.2.3.4;host=evil.example"}):
        r = get("/api/ads", **headers)
        assert (r.status_code, r.json()) == (400, {"detail": HostAllowlist.REJECTED}), headers
        assert r.headers["x-content-type-options"] == "nosniff"
    # запись по чужому имени тоже не доходит до обработчика
    r = client.patch("/api/ads/a1/status", json={"status": "dismissed", "author": "Омарова Ж."},
                     headers={"Host": "rebind.attacker.tld:8000"})
    assert r.status_code == 400 and client.get("/api/ads/a1").json()["status"] != "dismissed"

    env(SAQ_WEB_HOSTS="saq-box, .tailnet.ts.net.")
    for host in ("saq-box:5180", "SAQ-BOX", "tailnet.ts.net", "pc.tailnet.ts.net:5180"):
        assert get(Host=host).status_code == 200, host
    for host in ("testserver", "eviltailnet.ts.net", "saq-box.evil.example"):
        assert get(Host=host).status_code == 400, host

    # публичный хост OLX Callback - не 400: дальше его ограничивает PublicHostGuard
    callback = "/api/integrations/olx/callback"
    env(OLX_REDIRECT_URI=f"https://x.ngrok-free.dev{callback}")
    r = get(callback, Host="x.ngrok-free.dev")  # страница обратного вызова(state не передан - 400 в HTML)
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/html")
    assert get("/api/ads", Host="x.ngrok-free.dev").status_code == 404


def test_cross_site_writes_rejected(client, env):
    """CSRF: POST/PUT/PATCH/DELETE со страницы другого сайта - 403 до чтения тела(формы multipart браузер шлёт без
    OPTIONS). Разрешены: свой источник(Sec-Fetch-Site same-origin или none; без него - Origin с хостом и портом из Host,
    как через прокси Vite), адреса SAQ_CORS_ORIGINS и запросы без Origin и Sec-Fetch-Site(клиент приёма, бот, curl)."""
    from app.main import CrossSiteGuard

    author = "Омарова Ж."
    evil = {"Origin": "https://evil.example"}

    def forbidden(r):
        assert (r.status_code, r.json()) == (403, {"detail": CrossSiteGuard.REJECTED}), r.text
        assert r.headers["x-content-type-options"] == "nosniff"

    def comment(**headers):
        return client.post("/api/ads/a1/comments", json={"author": author, "text": "ok"}, headers=headers)

    before = client.get("/api/audit", params={"limit": 500}).json()
    csv = ("reg.csv", "source,sourceRecordId\nx,1\n".encode(), "text/csv")
    forbidden(client.post("/api/sources/import", files={"file": csv}, data={"author": author}, headers=evil))
    forbidden(client.post("/api/complexes/c2/photos", files={"file": ("p.png", b"\x89PNG", "image/png")},
                          data={"date": "2026-10-01", "activity": "idle"}, headers=evil))
    forbidden(client.patch("/api/ads/a1/status", json={"status": "dismissed", "author": author}, headers=evil))
    for method in ("PUT", "DELETE"):  # проверка - до маршрута: метод и путь не важны
        forbidden(client.request(method, "/api/ads/a1", headers=evil))
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:3000"},
                    {"Origin": "http://localhost:3000", "Host": "localhost:5180"}, {"Origin": "null"},
                    {"Origin": "http://[x"},
                    # браузер сам ставит Sec-Fetch-Site: совпадение Origin с Host его не отменяет
                    {"Origin": "http://testserver", "Sec-Fetch-Site": "cross-site"}):
        forbidden(comment(**headers))
    # тело отклонённого запроса не читается: 403, а не 413 предела загрузки
    big = ("reg.csv", b"x" * (6 * 1024 * 1024), "text/csv")
    forbidden(client.post("/api/sources/import", files={"file": big}, headers=evil))
    assert client.get("/api/audit", params={"limit": 500}).json() == before  # ничего не записано
    assert client.get("/api/ads/a1").json()["status"] != "dismissed"

    for headers in ({}, {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:5180", "Host": "localhost:5180"},
                    {"Origin": "http://localhost:5180", "Host": "localhost:5180"},  # прокси Vite, старый браузер
                    {"Origin": "https://testserver"},  # прокси с TLS: схема не сравнивается
                    {"Sec-Fetch-Site": "none"}):
        assert comment(**headers).status_code == 201, headers
    assert client.get("/api/ads", headers=evil).status_code == 200  # чтение - забота CORS, не этой проверки

    env(SAQ_CORS_ORIGINS="https://saq.example.kz")
    assert comment(Origin="https://saq.example.kz", **{"Sec-Fetch-Site": "cross-site"}).status_code == 201
    forbidden(comment(**evil))
    env(SAQ_CORS_ORIGINS="*")  # любой адрес - как раньше
    assert comment(**evil).status_code == 201


def test_guards_wrap_upload_limit():
    """Порядок обёрток: имя сервера - самая внешняя, проверка CSRF - снаружи предела тела(UploadLimit)."""
    from app import main

    assert [m.cls for m in main.app.user_middleware] == [
        main.HostAllowlist, main.PublicHostGuard, main.CrossSiteGuard, main.SettingsCors, main.InternalErrors,
        main.UploadLimit]
