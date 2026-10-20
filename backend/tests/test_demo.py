"""Демо-набор прежних версий(app.demo): удаление из БД, флаг "демо удалены", старт без демо-набора(demo_seed.live -
прежний SAQ_DEMO_DATA=0; переменная больше не читается), карточки ЖК из открытого реестра(идемпотентный импорт, правки
аналитика не перезаписываются), честный риск по ним
("риск не оценивается" до проверенных сведений) и справочник ЖК для правил и Claude."""
import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

import demo_seed

from app import ads_service, ai, demo, importer, llm, models, settings, world
from app.db import SessionLocal, engine
from app.risk_model import MODEL
from app.seed import init_db, reset

AUTHOR = "Нестерова А."
SEED_DATA = demo_seed.DATA  # прежний демо-набор - только для тестов(shared/testdata/demo_seed.json)
REG = importer.load_registry()
REG_OBJECTS = REG["objects"]  # объекты с координатами - 12 реальных ЖК
REG_ALL = REG["objects"] + REG["withoutCoords"]  # и 3 без координат: карточки ЖК - по всем 15


def _count(model, *where) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _flag() -> str | None:
    with SessionLocal() as db:
        m = db.get(models.Meta, demo.FLAG)
        return m.value if m else None


def _collected(text_: str, **kw) -> str:
    """Собранная публикация(как от сборщика): без привязки, статус "new"."""
    with SessionLocal() as db:
        ad = ads_service.create_ad(db, {"platform": "telegram", "account": "@kvartiry_uka", "text": text_,
                                        "url": f"https://t.me/kvartiry_uka/{uuid.uuid4().int % 10000}", **kw},
                                   actor=ads_service.COLLECTOR)
        db.commit()
        return ad.id


def _purge(capsys, *args) -> tuple[int, str]:
    code = demo.main(["purge", *args])
    return code, capsys.readouterr().out


def _set_meta(value: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE meta SET value = :v WHERE key = 'schema_version'"), {"v": value})


def _sqlite_file() -> Path | None:
    return Path(engine.url.database) if engine.dialect.name == "sqlite" else None


# ---------- настройка ----------

def test_demo_setting_retired(env, caplog):
    """SAQ_DEMO_DATA прежних версий больше не читается: демо-набора в приложении нет, заданное значение - предупреждение."""
    assert "SAQ_DEMO_DATA" not in settings.ENV_NAMES and not hasattr(env(), "demo_data")
    with caplog.at_level(logging.WARNING, logger="saq.settings"):
        env(SAQ_DEMO_DATA="1")
    assert any(r.getMessage().startswith("SAQ_DEMO_DATA больше не используется") for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="saq.settings"):
        env(SAQ_DEMO_DATA="")  # пустое значение - как не задано
    assert not any("SAQ_DEMO_DATA" in r.getMessage() for r in caplog.records)


# ---------- удаление демо-набора ----------

def _mixed_db(client) -> dict:
    """Демо-БД с тем, что накопилось в работе: собранная публикация, привязанная аналитиком к демо-ЖК, внесённая
    аналитиком публикация с гипотезой о демо-ЖК, сигнал по демо-ЖК, анализы ИИ, запись приёма, файл, задание, проверка
    адреса и заявка бота по демо-публикации, подключение сбора и запуск."""
    out = {"collected": _collected("Продажа квартир, бронь 200 000 ₸, звоните")}
    r = client.patch(f"/api/ads/{out['collected']}/complex", json={"complexId": "c2", "author": AUTHOR})
    assert r.status_code == 200 and r.json()["linkConfirmedBy"] == AUTHOR
    r = client.post("/api/ads", json={"platform": "telegram", "account": "@x", "author": AUTHOR,
                                      "text": "Квартиры в ЖК «Алтай Резиденс», бронь 100 000 ₸"})
    assert r.status_code == 201 and r.json()["complexId"] == "c2" and not r.json().get("linkConfirmedBy")
    out["analyst"] = r.json()["id"]
    out["kept_collected"] = _collected("Сдаётся гараж")  # без привязки - не трогается
    r = client.post("/api/signals", json={"complexId": "c2", "author": AUTHOR})
    assert r.status_code == 201, r.text
    with SessionLocal() as db:
        item = models.IngestItem(platform="instagram", external_id="demo-1", collector="intake", ad_id="a2",
                                 content_hash="h" * 64)
        db.add(item)
        db.flush()
        db.add(models.IngestVersion(item_id=item.id, version=1, content_hash="h" * 64))
        video = models.MediaAsset(id=uuid.uuid4().hex, ad_id="a2", item_id=item.id, kind="video", status="downloaded")
        db.add(video)
        db.flush()
        db.add(models.MediaAsset(id=uuid.uuid4().hex, ad_id="a2", item_id=item.id, parent_id=video.id, kind="frame"))
        db.add(models.Job(kind="analyze", ad_id="a2", status="done"))
        db.add(models.BotSubmission(external_id="bot-demo-1", ad_id="a2", item_id=item.id))
        db.add(models.GeoCheck(target_kind="complex", target_id="c3", query="ул. Протозанова, 81",
                               query_norm="протозанова 81", status="candidates", requested_by=AUTHOR))
        for aid in ("a1", out["collected"]):
            db.add(models.LlmAnalysis(ad_id=aid, content_hash="c" * 64, model="claude-sonnet-5-5",
                                      prompt_version="saq-llm-1", status="done", result={"summary": "тест"}))
        db.add(models.Collector(code="krisha", enabled=True, updated_by="Администратор"))
        db.add(models.CollectionRun(collector="krisha", trigger="manual", status="succeeded"))
        db.add(models.QuotaCounter(name="anthropic-usd:2026-10-18", count=1234))
        db.commit()
    return out


def test_purge_removes_only_demo_rows(client, capsys):
    ids = _mixed_db(client)
    events_before = _count(models.AuditEvent)
    code, out = _purge(capsys, "--dry-run")
    assert code == 0 and "Пробный прогон" in out
    for line in ("публикации демо-набора: 12", "ЖК демо-набора: 14", "застройщики демо-набора: 7",
                 "обращения демо-набора: 5", "сигналы по демо-ЖК: 1", "записи приёма и их версии: 2",
                 "файлы демо-публикаций (строки): 2", "проверки адреса 2ГИС по демо-ЖК: 1",
                 "Привязка к демо-ЖК будет снята (публикации остаются): 2", "аналитики: 3", "записи открытого реестра: 103",
                 "будет создано 15"):
        assert line in out, line
    assert _count(models.AuditEvent) == events_before and _flag() is None  # пробный прогон ничего не меняет

    code, out = _purge(capsys)  # без --yes - отказ
    assert code == 1 and "повторите с --yes" in out and _count(models.Ad, models.Ad.origin == "demo") == 12

    code, out = _purge(capsys, "--yes")
    assert code == 0, out
    assert "Флаг demo_data = purged" in out
    # демо-набора нет
    for model in (models.Ad, models.Complex, models.Developer, models.Appeal):
        assert _count(model, model.origin == "demo") == 0, model.__tablename__
    assert _count(models.Signal) == 0
    assert _count(models.GeoCheck) == 0
    assert _count(models.IngestItem) == _count(models.IngestVersion) == _count(models.MediaAsset) == 0
    assert _count(models.Job, models.Job.ad_id == "a2") == _count(models.BotSubmission) == 0
    assert _count(models.LlmAnalysis, models.LlmAnalysis.ad_id == "a1") == 0
    # остались: аналитики, открытый реестр, собранные и внесённые публикации, их анализ ИИ, подключения, квоты
    assert _count(models.Analyst) == 3 and _count(models.SourceRecord) == 103
    assert _count(models.Ad) == 3 and {a["origin"] for a in client.get("/api/ads").json()} == {"collected", "analyst"}
    assert _count(models.LlmAnalysis, models.LlmAnalysis.ad_id == ids["collected"]) == 1
    assert _count(models.Collector) == _count(models.CollectionRun) == _count(models.QuotaCounter) == 1
    assert _count(models.Complex, models.Complex.origin == "registry") == len(REG_ALL) == 15
    # привязка к демо-ЖК снята - с событием в журнале публикации; прежние события публикации остаются(без ЖК)
    with SessionLocal() as db:
        for key in ("collected", "analyst"):
            ad = db.get(models.Ad, ids[key])
            assert (ad.complex_id, ad.link_confirmed_by, ad.link_confirmed_at, ad.match_confidence) == (None, None, None, 0)
    journal = client.get(f"/api/ads/{ids['collected']}/audit").json()
    assert journal[-1]["what"] == "Привязка снята: демо-ЖК «Алтай Резиденс» удалён" and journal[-1]["who"] == "Система"
    assert any(e["what"] == "Привязка к ЖК «Алтай Резиденс» подтверждена аналитиком" and e["complexId"] is None
               for e in journal)
    assert client.get(f"/api/ads/{ids['analyst']}/audit").json()[-1]["what"].startswith("Привязка снята: демо-ЖК")
    # журнал: демо-события удалены, событие удаления - одно, от "Системы"
    events = client.get("/api/audit", params={"limit": 500}).json()
    assert not any(e["adId"] in {a["id"] for a in SEED_DATA["ads"]} for e in events)
    assert not any(e["complexId"] in {c["id"] for c in SEED_DATA["complexes"]} for e in events)
    seed_audit = {(e["who"], e["what"]) for e in SEED_DATA["audit"]}
    assert not any((e["who"], e["what"]) in seed_audit for e in events)
    purged = [e for e in events if e["what"].startswith("Удалены демо-данные")]
    assert len(purged) == 1 and purged[0]["who"] == "Система" and purged[0]["kind"] == "data"
    assert "публикации демо-набора — 12" in purged[0]["what"] and "привязка к демо-ЖК снята" in purged[0]["what"]
    assert any(e["what"].startswith("Загружены сведения открытых источников") for e in events)  # реестр - остался
    assert _flag() == demo.PURGED
    # источник "демо-набор" больше не показывается
    assert [s["code"] for s in client.get("/api/sources").json()] == ["homeportal", "homeportal_section", "ashyq"]

    # повторный запуск ничего не удаляет и не пишет второе событие
    counts = {m.__tablename__: _count(m) for m in (models.Ad, models.Complex, models.Developer, models.AuditEvent)}
    code, out = _purge(capsys, "--yes")
    assert code == 0 and "удалять нечего" in out
    assert counts == {m.__tablename__: _count(m) for m in (models.Ad, models.Complex, models.Developer, models.AuditEvent)}


def test_purge_sqlite_backup(client, capsys):
    f = _sqlite_file()
    if f is None:
        pytest.skip("резервная копия делается только для файловой SQLite")
    before = set(f.parent.glob(f"{f.name}.bak-*"))
    code, _ = _purge(capsys, "--yes")
    assert code == 0
    new = set(f.parent.glob(f"{f.name}.bak-*")) - before
    assert len(new) == 1 and next(iter(new)).name.startswith(f"{f.name}.bak-demo-purge-")
    for x in new:
        x.unlink()


def test_purge_postgres_prints_pg_dump(client, capsys):
    """PostgreSQL: перед удалением - строка резервной копии pg_dump; без --yes - отказ, ничего не удалено."""
    if engine.dialect.name != "postgresql":
        pytest.skip("строка pg_dump — только для PostgreSQL")
    code = demo.purge(engine)
    out = capsys.readouterr().out
    assert code == 1 and demo.PG_DUMP in out and _count(models.Ad, models.Ad.origin == "demo") == 12


def test_purge_blocked_by_foreign_rows(client, capsys):
    """Обращение не из демо-набора по демо-ЖК - удалять нельзя ничего(без частичного удаления)."""
    with SessionLocal() as db:
        db.add(models.Appeal(id="ap-real", complex_id="c1", date="2026-10-01", subject="Жалоба", source="eOtinish",
                             origin="analyst"))
        db.commit()
    code, out = _purge(capsys, "--yes")
    assert code == 2 and "ap-real" in out
    assert _count(models.Ad, models.Ad.origin == "demo") == 12 and _flag() is None


def test_purge_requires_current_schema(client, capsys):
    _set_meta("2026.10-audit-1")
    code = demo.main(["purge", "--yes"])
    assert code == 2 and "сначала запустите API" in capsys.readouterr().err
    assert _count(models.Ad, models.Ad.origin == "demo") == 12


def test_no_reseed_after_purge(client, capsys, env):
    """Перезапуск после удаления: демо не возвращается - ни при той же схеме, ни при пересоздании таблиц(флаг переживает
    пересоздание; тестовый старт demo_seed.start его уважает)."""
    assert _purge(capsys, "--yes")[0] == 0
    init_db(engine)
    assert _count(models.Ad) == 0 and _count(models.Complex, models.Complex.origin == "demo") == 0
    _set_meta("2026.09-old")  # неизвестная версия схемы без собранных данных - таблицы пересоздаются
    init_db(engine)
    assert _flag() == demo.PURGED
    assert _count(models.Ad) == 0 and _count(models.Appeal) == 0
    assert _count(models.Complex) == _count(models.Complex, models.Complex.origin == "registry") == 15
    assert _count(models.Analyst) == 3 and _count(models.SourceRecord) == 103
    init_db(engine)
    assert _count(models.Complex, models.Complex.origin == "demo") == 0
    # демо возвращается только с новой пустой БД
    reset()
    assert _count(models.Complex, models.Complex.origin == "demo") == 14 and _flag() is None


# ---------- боевой режим на пустой БД ----------

def test_live_mode_fresh_db(client, env):
    demo_seed.live()
    assert _count(models.Analyst) == 3 and _count(models.SourceRecord) == 103
    assert _count(models.Ad) == _count(models.Appeal) == _count(models.Signal) == 0
    cxs = client.get("/api/complexes").json()
    assert len(cxs) == 15 and {c["origin"] for c in cxs} == {"registry"}
    assert {c["id"] for c in cxs} == {f"r-{o['key']}" for o in REG_ALL}
    assert {c["id"] for c in cxs if c["lat"] is None} == {f"r-{o['key']}" for o in REG["withoutCoords"]}
    devs = client.get("/api/developers").json()
    assert {d["origin"] for d in devs} == {"registry"}
    assert all(d["id"] == demo.developer_id(d["bin"] or None, d["name"]) for d in devs)
    assert len(devs) == len({demo.developer_id(o["developerBin"], o["developer"]) for o in REG_ALL}) == 13
    # журнал: загрузка реестра и создание каждой карточки - без демо-событий
    events = client.get("/api/audit", params={"limit": 500}).json()
    assert len(events) == 16 and all(e["who"] == "Система" for e in events)
    assert sum(e["what"].startswith("Карточка ЖК создана по открытому реестру") for e in events) == 15
    assert sum("координат в источнике нет" in e["what"] for e in events) == 3
    # аналитики для входа есть; риск ни по одной карточке не оценивается
    assert len(client.get("/api/analysts").json()) == 3
    risk = client.get("/api/risk").json()
    assert {r["assessed"] for r in risk} == {False} and {r["score"] for r in risk} == {0}
    stats = client.get("/api/stats").json()
    assert stats["zones"] == {"red": 0, "yellow": 0, "green": 0} and stats["notAssessed"] == 15
    assert client.get("/api/complexes", params={"zone": "green"}).json() == []
    # повторный старт ничего не добавляет
    init_db(engine)
    assert _count(models.Complex) == 15 and _count(models.AuditEvent) == 16


def test_registry_card_fields():
    """Поля карточки: название без "ЖК "…"", адрес без города, срок - только если записи согласны и объект строится,
    стадия и основание - "нет сведений", номер документа - справкой."""
    reset()
    with SessionLocal() as db:
        rep = demo.import_registry(db)
        db.commit()
        assert len(rep.created) == 15 and not rep.updated and not rep.skipped
        orda = db.get(models.Complex, "r-orda")
        assert (orda.name, orda.address, orda.origin) == ("Orda", "ул. Кокжал Барака, д. 19а", "registry")
        assert (orda.lat, orda.lng) == (49.890363, 82.618099)
        assert (orda.stage, orda.progress, orda.floors, orda.flats, orda.district) == ("", 0, 0, 0, "")
        assert (orda.start, orda.planned_end) == ("2024-10-17", "2027-02-28")
        assert {k: orda.basis[k] for k in ("guarantee", "mio", "bvu")} == {"guarantee": "unknown", "mio": "unknown",
                                                                          "bvu": "unknown"}
        assert "1383/МҚЗТ-С-331" in orda.basis["mioRef"] and "не проверено" in orda.basis["mioRef"]
        assert orda.developer_id == "rd-040140006326" and db.get(models.Developer, "rd-040140006326").name == "ТОО «СЕО GROUP»"
        assert db.get(models.Complex, "r-park-line").name == "PARK LINE"
        # "Завершён(фильтр источника)" - не "введён"; срок у завершённого по источнику объекта не переносится(R6)
        zhanym = db.get(models.Complex, "r-zhanym")
        assert (zhanym.name, zhanym.stage, zhanym.planned_end) == ("Жаным", "", "")
        # у "Золотого квадрата" записи расходятся в сроке(2026-07-31 и 2026-11-01) - срока нет
        assert db.get(models.Complex, "r-zolotoi-kvadrat").planned_end == ""
        # объекты без координат - тоже карточки(координаты - после сверки 2ГИС)
        ab = db.get(models.Complex, "r-ab-residence-2")
        assert (ab.name, ab.lat, ab.lng, ab.developer_id) == ("AB-Residence 2", None, None, "rd-191040019288")
        prestige = db.get(models.Complex, "r-prestizh-sparkle-diamond")
        assert prestige.name.startswith("Престиж (Sparkle Diamond)") and prestige.lat is None
        assert "ДС №1 к ДПГ-22-16-055/120" in prestige.basis["guaranteeRef"]
        diamond = db.get(models.Complex, "r-diamond")
        assert diamond.developer_id == demo.developer_id(None, "ВВД-Құрылыс") and diamond.developer_id.startswith("rd-")
        assert db.get(models.Developer, diamond.developer_id).bin == ""
        # демо-ЖК при этом не тронуты
        assert db.scalar(select(func.count()).select_from(models.Complex).where(models.Complex.origin == "demo")) == 14


def test_registry_import_idempotent_and_keeps_analyst_edits(client):
    with SessionLocal() as db:
        demo.import_registry(db)
        db.commit()
    events = _count(models.AuditEvent)
    with SessionLocal() as db:
        rep = demo.import_registry(db)
        db.commit()
    assert len(rep.unchanged) == 15 and not rep.created and not rep.updated and _count(models.AuditEvent) == events
    # аналитик перенёс координаты из 2ГИС и уточнил адрес; срок сдачи у карточки стёрт; источник изменил адрес
    with SessionLocal() as db:
        c = db.get(models.Complex, "r-satti-city")
        c.lat, c.lng, c.address, c.planned_end = 49.9, 82.6, "ул. Есенберлина, 84/1", ""
        c.basis = {"guarantee": "unknown", "mio": "yes", "bvu": "unknown", "source": "Картотека ГАСК", "checkedAt": "2026-10-17"}
        rec = db.scalar(select(models.SourceRecord).where(models.SourceRecord.key == "satti-city"))
        rec.address = "г. Усть-Каменогорск, ул. Есенберлина, д. 84, корпус 2"
        db.commit()
    with SessionLocal() as db:
        rep = demo.import_registry(db)
        db.commit()
        c = db.get(models.Complex, "r-satti-city")
        assert rep.updated == ["r-satti-city"]
        assert (c.lat, c.lng, c.address) == (49.9, 82.6, "ул. Есенберлина, 84/1")  # правки аналитика остались
        assert c.planned_end == "2027-01-30"  # пустое поле дополнено
        assert c.basis["mio"] == "yes" and c.basis["source"] == "Картотека ГАСК" and "mioRef" in c.basis
    journal = client.get("/api/complexes/r-satti-city/audit").json()
    assert journal[-1]["what"].startswith("Карточка ЖК дополнена по открытому реестру") and "плановое завершение" in \
        journal[-1]["what"]


def test_registry_import_skips_foreign_id(client):
    with SessionLocal() as db:
        db.add(models.Complex(id="r-orda", name="Чужой", developer_id="d1", address="", district="", lng=0, lat=0,
                              stage="каркас", progress=1, floors=1, flats=1, start="", planned_end="",
                              basis={"guarantee": "unknown", "mio": "unknown", "bvu": "unknown"}, cover="", origin="demo"))
        db.commit()
        rep = demo.import_registry(db)
        db.commit()
        assert any("r-orda" in s for s in rep.skipped) and db.get(models.Complex, "r-orda").name == "Чужой"


# ---------- честный риск по карточке из реестра ----------

def test_registry_risk_not_assessed_until_confirmed(client, env):
    demo_seed.live()
    r = client.get("/api/complexes/r-orda/risk").json()
    assert (r["assessed"], r["score"], r["summary"]) == (False, 0, MODEL["notAssessed"]["summary"])
    assert {o["code"]: o["state"] for o in r["observations"]}["O2"] == "unknown"
    s = client.post("/api/signals", json={"complexId": "r-orda", "author": AUTHOR})
    assert s.status_code == 422 and "Риск не оценивается" in s.json()["detail"]
    # гипотеза ИИ о привязке - ещё не проверенные сведения
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@orda.sale", "author": AUTHOR,
                                       "text": "Старт продаж в ЖК Orda! Бронь квартиры 300 000 ₸, рассрочка 0%"}).json()
    assert ad["complexId"] == "r-orda" and not ad.get("linkConfirmedBy")
    assert client.get("/api/complexes/r-orda/risk").json()["assessed"] is False
    # аналитик подтвердил привязку - риск считается обычным порядком, только по подтверждённым фактам
    assert client.patch(f"/api/ads/{ad['id']}/complex", json={"complexId": "r-orda", "author": AUTHOR}).status_code == 200
    r = client.get("/api/complexes/r-orda/risk").json()
    assert (r["assessed"], r["score"], r["zone"]) == (True, 35, "yellow")
    # R4 - "нужна проверка": обращения по карточке реестра не сверены(пустой список не значит "обращений нет")
    assert {f["code"]: f["state"] for f in r["factors"]} == {"R1": "unknown", "R2": "hit", "R3": "hit", "R4": "unknown",
                                                              "R5": "unknown", "R6": "unknown"}
    stats = client.get("/api/stats").json()
    assert stats["zones"]["yellow"] == 1 and stats["notAssessed"] == 14
    assert [c["id"] for c in client.get("/api/complexes", params={"zone": "yellow"}).json()] == ["r-orda"]
    assert client.post("/api/signals", json={"complexId": "r-orda", "author": AUTHOR}).status_code == 201
    # оценка публикации: R1 по ЖК реестра - "нет сведений", а не "основание есть"
    score = client.get(f"/api/ads/{ad['id']}/score").json()
    assert {f["code"]: f["state"] for f in score["factors"]}["R1"] == "unknown"


def test_demo_risk_unchanged(client):
    """Демо-ЖК(без происхождения "registry") оцениваются как раньше - кейсы A/B/C."""
    risk = {r["complexId"]: r for r in client.get("/api/risk").json()}
    assert {r["assessed"] for r in risk.values()} == {True}
    assert [(risk[c]["score"], risk[c]["zone"]) for c in ("c14", "c2", "c10")] == [(35, "yellow"), (70, "red"), (10, "green")]
    assert "notAssessed" in client.get("/api/stats").json() and client.get("/api/stats").json()["notAssessed"] == 0


# ---------- справочник ЖК: правила и Claude ----------

@pytest.mark.parametrize("text_, cid, developer", [
    ("Квартиры в ЖК Orda на Кокжал Барака, бронь от 500 000 ₸", "r-orda", "ТОО «СЕО GROUP»"),
    ("Старт продаж! ЖК «Satti City», рассрочка 0% на 12 месяцев", "r-satti-city", 'ТОО "СТ-77"'),
    ("Сатти Сити — квартиры от застройщика, первый взнос 30%", "r-satti-city", 'ТОО "СТ-77"'),
])
def test_collected_ad_matches_registry_complex(client, env, text_, cid, developer):
    demo_seed.live()
    aid = _collected(text_)
    with SessionLocal() as db:
        ad = db.get(models.Ad, aid)
        hyps = ai.all_hypotheses(world.catalog(db, exclude_ad=aid), ads_service.match_obj(ad))
        assert hyps and hyps[0]["complexId"] == cid, hyps[:2]
        mat = llm.build_material(db, ad, hyps)
    assert len(mat.catalog) == 15 and mat.catalog[0]["id"] == cid and mat.catalog[0]["developer"] == developer
    assert {c["id"] for c in mat.catalog} == {f"r-{o['key']}" for o in REG_ALL}
    _, user = llm.prompt(mat)
    assert f"{cid} | " in user


def test_registry_seed_without_file(client, env, monkeypatch, tmp_path):
    """Нет файла реестра - боевой режим стартует: аналитики есть, карточек нет."""
    monkeypatch.setattr(importer, "REGISTRY", tmp_path / "none.json")
    demo_seed.live()
    assert _count(models.Analyst) == 3 and _count(models.Complex) == 0 and _count(models.SourceRecord) == 0
    assert [s["code"] for s in client.get("/api/sources").json()] == []
