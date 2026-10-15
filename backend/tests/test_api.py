import json

from app.risk import score_complex
from app.risk_model import SHARED, VERSION, T

AS_OF = "2026-10-15"  # дата контрольных значений (как в shared/risk_golden.json)


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

    # Кейс A: нет сведений об основании → 0 баллов за R1 и «нужна проверка»
    r = _risk(client, "c14")
    assert _f(r, "R1")["state"] == "unknown" and r["needsCheck"] and "R1" in r["summary"]

    # 60 баллов без пары R1+R2 — жёлтая
    r = _risk(client, "c8")
    assert (r["score"], r["zone"]) == (60, "yellow")


def test_risk_endpoints_agree_with_engine(client):
    rows = client.get("/api/risk", params={"asOf": AS_OF}).json()
    assert len(rows) == 14
    for row in rows:
        one = _risk(client, row["complexId"])
        assert {k: v for k, v in row.items() if k != "complexId"} == one

    # API (ORM → camelCase) считает так же, как движок на исходном сиде
    seed = json.loads((SHARED / "seed_data.json").read_text(encoding="utf-8"))
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
    # совпадение по названию — гипотеза (ниже порога факта): R1 объекта в оценку публикации не входит
    assert ad["complexId"] == "c2" and ad["matchConfidence"] == T["autoLinkConfidence"] == 0.6
    assert T["autoLinkConfidence"] < T["linkConfirmed"]
    s = client.get(f"/api/ads/{ad['id']}/score").json()
    assert (s["score"], s["zone"], s["needsCheck"]) == (35, "yellow", True)
    assert s["complexId"] == "c2" and s["matchConfidence"] == 0.6
    assert [f["code"] for f in s["factors"]] == ["R1", "R2", "R3"] and "бронь" in s["signals"]["funds"]
    assert (_f(s, "R1")["state"], _f(s, "R1")["reason"]) == ("unknown", "R1_link_unconfirmed")

    # аналитик подтвердил привязку → факт: R1 объекта входит в оценку, кейс B — красная 70
    r = client.patch(f"/api/ads/{ad['id']}/complex", json={"complexId": "c2", "author": "Тест"})
    assert r.status_code == 200 and (r.json()["complexId"], r.json()["matchConfidence"]) == ("c2", 1)
    s = client.get(f"/api/ads/{ad['id']}/score").json()
    assert (s["score"], s["zone"], s["needsCheck"], s["matchConfidence"]) == (70, "red", False, 1)

    r = client.patch(f"/api/ads/{ad['id']}/status", json={"status": "confirmed", "author": "Тест", "assignee": "Тест",
                                                           "comment": "Сверено с картотекой ГАСК"}).json()
    assert r["status"] == "confirmed" and r["assignee"] == "Тест"
    log = client.get(f"/api/ads/{ad['id']}/audit").json()
    assert [(e["kind"], e["who"]) for e in log] == [("system", "Система"), ("system", "Система"), ("link", "Тест"),
                                                    ("status", "Тест")]
    assert log[0]["what"] == "Публикация добавлена вручную"
    assert "Автопривязка к ЖК «Алтай Резиденс»" in log[1]["what"] and "гипотеза" in log[1]["what"]
    assert log[2]["what"] == "Привязка к ЖК «Алтай Резиденс» подтверждена аналитиком"
    assert log[3]["what"] == ("Статус: «Новая» → «Нарушение подтверждено» · ответственный: Тест. "
                              "Основание: Сверено с картотекой ГАСК")
    assert all(e["at"].endswith("+00:00") and e["adId"] == ad["id"] and e["complexId"] == "c2" for e in log)

    # без совпадения по названию — без привязки
    ad = client.post("/api/ads", json={"platform": "olx", "account": "@x", "text": "Продаю гараж"}).json()
    assert ad["complexId"] is None and ad["matchConfidence"] == 0
    assert client.post("/api/ads", json={"platform": "olx", "account": "@x", "text": "x",
                                         "matchConfidence": 1.5}).status_code == 422


def test_link_ad_to_complex(client):
    def link(aid, cid, author="Тест"):
        return client.patch(f"/api/ads/{aid}/complex", json={"complexId": cid, "author": author})

    def journal(aid):
        return [(e["who"], e["what"]) for e in client.get(f"/api/ads/{aid}/audit").json()]

    def risk_c1():
        return next(r for r in client.get("/api/risk", params={"asOf": AS_OF}).json() if r["complexId"] == "c1")

    # a12 без привязки; привязали к c1 → у c1 появились R2/R3 (публикация с маркерами «вход от», «₸»)
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

    # гипотеза из сида (a9 → c12, 54%) подтверждается тем же вызовом
    assert _f(_risk(client, "c12"), "R3")["state"] == "unknown"
    link("a9", "c12")
    assert _f(_risk(client, "c12"), "R3")["state"] == "hit"

    # снятие привязки
    r = link("a12", None)
    assert r.status_code == 200 and (r.json()["complexId"], r.json()["matchConfidence"]) == (None, 0)
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

        # старая схема без meta, FK на уже удалённую таблицу (так бывает в SQLite) — всё равно пересоздаётся
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
    # форма ручного ввода сама передаёт подсказку по названию с уверенностью ниже порога — это гипотеза, и она в журнале
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@form", "text": "Старт продаж ЖК Ульба Ривер, бронь",
                                       "complexId": "c3", "matchConfidence": T["autoLinkConfidence"]}).json()
    assert ad["complexId"] == "c3"
    assert any("гипотеза" in e["what"] for e in client.get(f"/api/ads/{ad['id']}/audit").json())
    # выбор ЖК вручную (уверенность 1) — не гипотеза
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@form", "text": "Старт продаж, бронь",
                                       "complexId": "c3", "matchConfidence": 1}).json()
    assert not any("гипотеза" in e["what"] for e in client.get(f"/api/ads/{ad['id']}/audit").json())
    assert client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "т", "complexId": "nope"}).status_code == 404
