"""Схема БД: обновление на месте(migrate), защита собранных данных, новые таблицы, JSON со слежением, помощники
моделей(as_utc, insert_ignore) и новые поля схем API(AdOut, HumanName, приём публикаций)."""
import re
import sqlite3 as sqlite3_module
import uuid
from datetime import datetime, timedelta, timezone, UTC
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, Integer, String, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app import migrate, models, schemas
from app.db import Base, SessionLocal, engine, foreign_keys_off, make_engine
from app.seed import init_db

AD = {"platform": "instagram", "account": "@test_dev", "text": "Старт продаж, бронь 100 000 ₸", "author": "Тест"}


def _ad(client, aid: str):
    """Публикация из списка GET /api/ads(отдельного GET /api/ads/{id} в API нет)."""
    return next((a for a in client.get("/api/ads").json() if a["id"] == aid), None)


def _meta(conn) -> str:
    return conn.execute(text("SELECT value FROM meta WHERE key = 'schema_version'")).scalar()


def _set_meta(value: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE meta SET value = :v WHERE key = 'schema_version'"), {"v": value})


# Столбцы шага 2026.10-verification-1(сверка сведений аналитиком)
VERIFICATION_COLUMNS = {"complexes": ("published_list_check", "appeals_check"),
                        "appeals": ("comment", "created_by", "created_at", "decided_by", "decision_comment")}


def _drop_verification_columns() -> None:
    with engine.begin() as conn:
        for table_name, names in VERIFICATION_COLUMNS.items():
            for name in names:
                conn.execute(text(f"ALTER TABLE {table_name} DROP COLUMN {name}"))


def _has_verification_columns() -> bool:
    return all(set(names) <= {c["name"] for c in inspect(engine).get_columns(t)} for t, names in VERIFICATION_COLUMNS.items())


def _to_baseline() -> None:
    """База как у версии 2026.10-sources-1: без 12 новых таблиц, llm_analyses, таблиц объектов из рекламы, решений
    аналитика, предупреждений жителям, ads.collected_at и столбцов сверки аналитиком."""
    with engine.begin() as conn:
        for t in reversed(Base.metadata.sorted_tables):
            if t.name in migrate.NEW_TABLES + migrate.LLM_TABLES + migrate.DISCOVERY_TABLES + migrate.DECISION_TABLES \
                    + migrate.ALERT_TABLES:
                t.drop(conn)
        conn.execute(text("ALTER TABLE ads DROP COLUMN collected_at"))
    _drop_verification_columns()
    _set_meta(migrate.BASELINE)


def _sqlite_file() -> Path | None:
    return Path(engine.url.database) if engine.dialect.name == "sqlite" else None


def _backups() -> set:
    f = _sqlite_file()
    return set(f.parent.glob(f"{f.name}.bak-*")) if f else set()


def test_fresh_db(tmp_path):
    assert models.SCHEMA_VERSION == migrate.STEPS[-1][0] == "2026.10-audit-2"
    eng = make_engine(f"sqlite:///{tmp_path}/fresh.db")
    try:
        init_db(eng)
        tables = set(inspect(eng).get_table_names())
        assert set(migrate.NEW_TABLES + migrate.LLM_TABLES + migrate.DISCOVERY_TABLES + migrate.DECISION_TABLES
                   + migrate.ALERT_TABLES) <= tables
        with eng.connect() as conn:
            assert _meta(conn) == models.SCHEMA_VERSION
            assert conn.execute(text("SELECT count(*) FROM ads")).scalar() == 12
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 30000
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert not list(tmp_path.glob("fresh.db.bak-*"))  # новой базе резервная копия не нужна
    finally:
        eng.dispose()


def test_sqlite_foreign_keys_enforced_and_off_only_inside_helper(tmp_path):
    """SQLite проверяет внешние ключи, как PostgreSQL; foreign_keys_off выключает проверку только внутри блока - и после
    ошибки соединение уходит в пул с проверкой."""
    eng = make_engine(f"sqlite:///{tmp_path}/fk.db", pool_size=1, max_overflow=0)
    try:
        with eng.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE p (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE ch (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES p (id))")
            conn.exec_driver_sql("INSERT INTO p VALUES (1)")
            conn.exec_driver_sql("INSERT INTO ch VALUES (1, 1)")
        with pytest.raises(IntegrityError), eng.begin() as conn:
            conn.exec_driver_sql("INSERT INTO ch VALUES (2, 99)")
        with pytest.raises(IntegrityError), eng.begin() as conn:
            conn.exec_driver_sql("DELETE FROM p")
        with pytest.raises(RuntimeError), foreign_keys_off(eng) as conn, conn.begin():
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0
            conn.exec_driver_sql("DELETE FROM p")
            raise RuntimeError("шаг упал")
        with eng.connect() as conn:  # то же соединение пула(pool_size=1): проверка снова включена, удаление откатилось
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert conn.exec_driver_sql("SELECT count(*) FROM p").scalar() == 1
    finally:
        eng.dispose()


def test_baseline_upgrade_keeps_data(client):
    created = client.post("/api/ads", json=AD)
    assert created.status_code == 201
    aid = created.json()["id"]
    client.patch("/api/ads/a1/status", json={"status": "in_review", "author": "Тест"})
    _to_baseline()
    assert "collected_at" not in {c["name"] for c in inspect(engine).get_columns("ads")}
    before = _backups()

    init_db(engine)

    assert set(migrate.NEW_TABLES + migrate.LLM_TABLES + migrate.DECISION_TABLES + migrate.ALERT_TABLES) \
        <= set(inspect(engine).get_table_names())
    assert "collected_at" in {c["name"] for c in inspect(engine).get_columns("ads")}
    assert _has_verification_columns()
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    assert _ad(client, aid)["origin"] == "analyst"
    assert _ad(client, "a1")["status"] == "in_review"  # не пересеяно
    assert len(client.get("/api/ads").json()) == 13
    if _sqlite_file():
        new = _backups() - before
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-sources-1-")
        for f in new:
            f.unlink()

    # повторный запуск - ничего не меняется
    init_db(engine)
    assert len(client.get("/api/ads").json()) == 13
    assert _backups() == before


def test_intake_upgrade_adds_llm_table_and_keeps_data(client):
    """2026.10-intake-1 → 2026.10-llm-1: таблица llm_analyses добавляется на месте, собранные публикации, файлы,
    решения аналитика и строка прежнего анализатора в collectors(кода нет в реестре) сохраняются."""
    with SessionLocal() as db:
        aid = _collected_ad(db)
        db.add(models.Collector(code="openai", status="off", status_detail="только статус"))  # строка прежней версии
        db.commit()
    client.patch("/api/ads/a1/status", json={"status": "in_review", "author": "Тест"})
    with engine.begin() as conn:
        models.LlmAnalysis.__table__.drop(conn)
    _set_meta("2026.10-intake-1")
    assert "llm_analyses" not in inspect(engine).get_table_names()
    assert migrate.chain_from("2026.10-intake-1") == migrate.STEPS[-6:]
    before = _backups()

    init_db(engine)

    assert "llm_analyses" in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    got = _ad(client, aid)
    assert got["origin"] == "collected" and got["processing"]["status"] == "queued" and "llm" not in got
    assert _ad(client, "a1")["status"] == "in_review"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(models.MediaAsset)) == 5
        assert db.get(models.Collector, "openai") is not None  # шаги только добавляют
        db.add(models.LlmAnalysis(ad_id=aid, content_hash="h" * 64, model="claude-opus-5-5",
                                  prompt_version="saq-llm-1", status="done", result={"summary": "тест"}))
        db.commit()
    assert _ad(client, aid)["llm"]["summary"] == "тест"
    # страница подключений и воркер чужую строку не читают
    codes = [c["code"] for c in client.get("/api/sources/connectors").json()["connectors"]]
    assert "openai" not in codes and "anthropic" in codes
    if _sqlite_file():
        new = _backups() - before
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-intake-1-")
        for f in new:
            f.unlink()
    with SessionLocal() as db:  # оплаченные результаты - невосстановимые данные
        assert migrate.durable_rows(engine, set(inspect(engine).get_table_names()))["llm_analyses"] == 1


def _coords_not_null() -> None:
    """complexes.lat/lng - NOT NULL, как до 2026.10-discovery-1(SQLite - пересборкой таблицы по прежнему DDL)."""
    with foreign_keys_off(engine) as conn, conn.begin():  # DROP TABLE complexes - на него ссылаются другие таблицы
        if conn.dialect.name == "postgresql":
            conn.execute(text("ALTER TABLE complexes ALTER COLUMN lat SET NOT NULL"))
            conn.execute(text("ALTER TABLE complexes ALTER COLUMN lng SET NOT NULL"))
            return
        ddl = conn.execute(text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'complexes'")).scalar()
        old = re.sub(r"\b(lat|lng) FLOAT\b(?! NOT NULL)", r"\1 FLOAT NOT NULL", ddl)
        conn.exec_driver_sql(old.replace("CREATE TABLE complexes", "CREATE TABLE complexes_old", 1))
        conn.exec_driver_sql("INSERT INTO complexes_old SELECT * FROM complexes")
        conn.exec_driver_sql("DROP TABLE complexes")
        conn.exec_driver_sql("ALTER TABLE complexes_old RENAME TO complexes")
        conn.exec_driver_sql("CREATE INDEX ix_complexes_name ON complexes (name)")


def _nullable(table_name: str) -> dict:
    return {c["name"]: c["nullable"] for c in inspect(engine).get_columns(table_name)}


def test_discovery_upgrade_keeps_data_and_allows_missing_coords(client):
    """2026.10-llm-1 → 2026.10-discovery-1: таблицы объектов из рекламы добавляются, у complexes.lat/lng снимается
    NOT NULL(SQLite - пересборкой таблицы в транзакции шага), ЖК, публикации, решения аналитика и ответы Claude прежней
    версии промпта сохраняются и показываются."""
    with SessionLocal() as db:
        aid = _collected_ad(db, media=False)
        db.add(models.LlmAnalysis(ad_id=aid, content_hash="h" * 64, model="claude-sonnet-5-5", served_model="x",
                                  prompt_version="saq-llm-1", status="done", result={"summary": "ответ v1"}))
        db.commit()
    assert client.patch("/api/ads/a1/complex", json={"complexId": "c3", "author": "Тест"}).status_code == 200
    before_cx = {c["id"]: (c["name"], c["lat"], c["lng"], c["developerId"]) for c in client.get("/api/complexes").json()}
    with engine.begin() as conn:
        for name in reversed(migrate.DISCOVERY_TABLES):
            Base.metadata.tables[name].drop(conn)
    _coords_not_null()
    assert _nullable("complexes")["lat"] is False and _nullable("complexes")["lng"] is False
    _set_meta("2026.10-llm-1")
    assert migrate.chain_from("2026.10-llm-1") == migrate.STEPS[-5:]
    before = _backups()

    init_db(engine)

    assert set(migrate.DISCOVERY_TABLES) <= set(inspect(engine).get_table_names())
    assert _nullable("complexes")["lat"] is True and _nullable("complexes")["lng"] is True
    assert "ix_complexes_name" in {i["name"] for i in inspect(engine).get_indexes("complexes")}
    if engine.dialect.name == "sqlite":  # проверка внешних ключей снова включена, ссылки на ЖК целы
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    after_cx = {c["id"]: (c["name"], c["lat"], c["lng"], c["developerId"]) for c in client.get("/api/complexes").json()}
    assert after_cx == before_cx
    a1 = _ad(client, "a1")
    assert (a1["complexId"], a1["linkConfirmedBy"]) == ("c3", "Тест")
    got = _ad(client, aid)["llm"]  # ответ прежней версии промпта - как был
    assert (got["promptVersion"], got["summary"], got["mentionedComplexes"], got["objectStage"]) == (
        "saq-llm-1", "ответ v1", [], None)
    with SessionLocal() as db:  # ЖК без координат теперь можно сохранить
        db.add(models.Complex(id="nc1", name="Без координат", developer_id="d1", address="", district="", lat=None,
                              lng=None, stage="", progress=0, floors=0, flats=0, start="", planned_end="", basis={},
                              cover="", origin="advertised"))
        db.add(models.DiscoveredObject(norm_key="test", name="Тест"))
        db.commit()
    assert client.get("/api/complexes/nc1").json()["lat"] is None
    assert migrate.durable_rows(engine, set(inspect(engine).get_table_names()))["discovered_objects"] == 1
    with engine.begin() as conn:
        assert migrate.relax_not_null(conn, "complexes", ("lat", "lng")) is False  # идемпотентно
    if _sqlite_file():
        new = _backups() - before
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-llm-1-")
        for f in new:
            f.unlink()


def test_verification_upgrade_adds_columns_and_keeps_data(client):
    """2026.10-discovery-1 → 2026.10-verification-1: у ЖК - сверка перечня ГАСК и "обращений нет - сверено", у
    обращений - кто внёс и кто проверил; ЖК, основание, обращения и журнал сохраняются, сверка сразу работает."""
    before_cx = {c["id"]: (c["basis"], c["inPublishedList"]) for c in client.get("/api/complexes").json()}
    before_ap = client.get("/api/appeals").json()
    _drop_verification_columns()
    assert not _has_verification_columns()
    _set_meta("2026.10-discovery-1")
    assert migrate.chain_from("2026.10-discovery-1") == migrate.STEPS[-4:]
    before = _backups()

    init_db(engine)

    assert _has_verification_columns()
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    cxs = client.get("/api/complexes").json()
    assert {c["id"]: (c["basis"], c["inPublishedList"]) for c in cxs} == before_cx
    assert all(c["publishedListCheck"] is None and c["appealsCheck"] is None for c in cxs)
    assert [{k: v for k, v in a.items() if k in before_ap[0]} for a in client.get("/api/appeals").json()] == before_ap
    r = client.post("/api/complexes/c14/appeals-check", json={"author": "Тест", "source": "ДЭР ВКО"})
    assert r.status_code == 200 and r.json()["appealsCheck"]["checkedBy"] == "Тест"
    ap = client.post("/api/complexes/c14/appeals", json={"author": "Тест", "date": "2026-10-01", "subject": "Тема",
                                                         "source": "eOtinish"})
    assert ap.status_code == 201 and ap.json()["createdBy"] == "Тест"
    # сверка аналитика - невосстановимые данные: пересоздание схемы её бы стёрло
    assert migrate.durable_rows(engine, set(inspect(engine).get_table_names())) == {"appeals": 1, "audit_events": 2}
    with engine.begin() as conn:
        assert migrate.add_column(conn, "appeals", models.Appeal.__table__.c.created_by) is False  # идемпотентно
    if _sqlite_file():
        new = _backups() - before
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-discovery-1-")
        for f in new:
            f.unlink()


def test_decisions_upgrade_adds_table_and_keeps_data(client):
    """2026.10-verification-1 → 2026.10-decisions-1: таблица решений аналитика по "Проверке по рекламе" добавляется на
    месте; ЖК, стадии, оценки и журнал сохраняются, решения сразу работают и защищены от пересоздания схемы."""
    before = {c["id"]: (c["stage"], c["screening"]["status"]) for c in client.get("/api/complexes").json()}
    with engine.begin() as conn:
        models.ScreeningDecision.__table__.drop(conn)
    _set_meta("2026.10-verification-1")
    assert "screening_decisions" not in inspect(engine).get_table_names()
    assert migrate.chain_from("2026.10-verification-1") == migrate.STEPS[-3:]
    assert migrate.tables_after("2026.10-verification-1") == {"screening_decisions", *migrate.ALERT_TABLES}
    assert "screening_decisions" in migrate.tables_after("2026.10-discovery-1")
    assert migrate.tables_after(models.SCHEMA_VERSION) == set() == migrate.tables_after("old")
    backups = _backups()

    init_db(engine)

    assert "screening_decisions" in inspect(engine).get_table_names()
    idx = {i["name"]: i for i in inspect(engine).get_indexes("screening_decisions")}
    assert idx["uq_screening_decisions_active_complex"]["unique"]
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    cxs = client.get("/api/complexes").json()
    assert {c["id"]: (c["stage"], c["screening"]["status"]) for c in cxs} == before
    assert all(c["decision"] is None and c["screening"]["variant"] is None for c in cxs)
    r = client.post("/api/complexes/c2/commissioned", json={"author": "Тест", "actDate": "2026-09-01", "source": "ЕГКН"})
    assert r.status_code == 200 and r.json()["decision"]["kind"] == "commissioned"
    assert migrate.durable_rows(engine, set(inspect(engine).get_table_names()))["screening_decisions"] == 1
    with engine.begin() as conn:
        migrate._decisions_1(conn)  # идемпотентно
    if _sqlite_file():
        new = _backups() - backups
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-verification-1-")
        for f in new:
            f.unlink()


def test_alerts_upgrade_adds_tables_and_keeps_data(client):
    """2026.10-decisions-1 → 2026.10-alerts-1: таблицы предупреждений жителям, доставок и подписок добавляются на месте;
    ЖК и журнал сохраняются, предупреждения сразу работают и защищены от пересоздания схемы."""
    events = len(client.get("/api/complexes/c1/audit").json())
    with engine.begin() as conn:
        for t in reversed(Base.metadata.sorted_tables):
            if t.name in migrate.ALERT_TABLES:
                t.drop(conn)
    _set_meta("2026.10-decisions-1")
    assert not set(migrate.ALERT_TABLES) & set(inspect(engine).get_table_names())
    assert migrate.chain_from("2026.10-decisions-1") == migrate.STEPS[-2:]
    assert migrate.tables_after("2026.10-decisions-1") == set(migrate.ALERT_TABLES)
    backups = _backups()

    init_db(engine)

    assert set(migrate.ALERT_TABLES) <= set(inspect(engine).get_table_names())
    idx = {i["name"]: i for i in inspect(engine).get_indexes("resident_alerts")}
    assert idx["uq_resident_alerts_pending_complex"]["unique"]
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    assert len(client.get("/api/complexes/c1/audit").json()) == events
    r = client.post("/api/alerts", json={"complexId": "c1", "template": "basis", "title": "Проверьте основание",
                                         "text": "Сверьте номер гарантии ЕО в ГАСК.", "author": "Тест"})
    assert r.status_code == 201, r.text
    assert migrate.durable_rows(engine, set(inspect(engine).get_table_names()))["resident_alerts"] == 1
    with engine.begin() as conn:
        migrate._alerts_1(conn)  # идемпотентно
    if _sqlite_file():
        new = _backups() - backups
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-decisions-1-")
        for f in new:
            f.unlink()


# События журнала вида data из набора shared/snapshot(2026.10-discovery-1, id 14-20, 189, 1; аккаунт оператора
# заменён) и пограничные: автор, текст, событие ли подключения
LEGACY_EVENTS = (
    ("Администратор", "Настройки источника «Instagram» изменены: аккаунты: +@an.groups, +@tumar.oskemen", True),
    ("Администратор", "Настройки источника «TikTok» изменены: профили: +zolotoi_kvadrat.kz, +elitstroy.uka; загрузка "
                      "видео: да; сбор включён", True),
    ("Администратор", "Настройки источника «Krisha.kz» изменены: URL поиска: +https://krisha.kz/prodazha/kvartiry/"
                      "ust-kamenogorsk/?das[novostroiki]=1; сбор включён", True),
    ("Система", "Источник «Instagram»: готов к сбору. Подключено: @operator.test. Токен действует до 15.12.2026", True),
    ("Система", "Источник «TikTok»: готов к сбору. Токен Apify действует (аккаунт operator.test); Actor "
                "clockworks~tiktok-scraper доступен. Лимит на запуск $0.5 (минимум Actor $0.5), дневной лимит $1. "
                "Платный запуск при проверке не выполнялся", True),
    ("Система", "Источник «Krisha.kz»: готов к сбору. Выдача и карточки объявлений доступны (объявлений в выдаче: 39, "
                "разобрано на странице: 20)", True),
    ("Система", "Источник «Анализатор ИИ (Anthropic)»: готов. Ключ принят, модель claude-sonnet-5-5 доступна; уровень "
                "размышления medium, бюджет $5.00 в день", True),
    ("Система", "Источник «Krisha.kz»: ошибка", True),  # статус без пояснения
    ("Система", "Сбор «Instagram»: новых 168, изменено 0, без изменений 0 (запуск №5)", False),  # счётчики - открыты
    ("Система", "Загружены сведения открытых источников: 103 записи (Homeportal — карточки ЖК: 16) из "
                "docs/Усть-Каменогорск_объекты.xlsx, собраны 15.10", False),
    ("Омарова Ж.", "Источник «Instagram»: готов", False),  # такой текст пишет только "Система"
    ("Омарова Ж.", "Настройки источника «Krisha.kz»", False),  # без ": " после "изменены"
)


def test_audit_upgrade_moves_connector_events(client):
    """2026.10-alerts-1 → 2026.10-audit-2: события подключений вида data(статус с пояснением проверки, настройки,
    аккаунт OLX) получают вид connector, остальные события и виды не меняются; условие SQL и legacy_connector_event -
    одно и то же."""
    from app.integrations import olx

    label = "Иван Петров (id 42)"
    rows = [*((who, what, hit) for who, what, hit in LEGACY_EVENTS),
            ("Администратор", olx.CONNECTED_AUDIT.format(label=label), True),
            ("Омарова Ж.", olx.DISCONNECTED_AUDIT.format(label=label), True)]
    with SessionLocal() as db:
        ids = []
        for who, what, _ in rows:
            e = models.AuditEvent(kind="data", who=who, what=what)
            db.add(e)
            db.flush()
            ids.append(e.id)
        other = models.AuditEvent(kind="comment", who="Тест", what="Настройки источника «Instagram» изменены: период: 60 "
                                                                    "мин")
        db.add(other)
        db.commit()
        other_id = other.id
    for who, what, hit in rows:
        assert migrate.legacy_connector_event("data", who, what) is hit, what
        assert not migrate.legacy_connector_event("comment", who, what)
    _set_meta("2026.10-alerts-1")
    assert migrate.chain_from("2026.10-alerts-1") == migrate.STEPS[-1:]
    assert migrate.data_after("2026.10-alerts-1") == [migrate.connector_events] == migrate.data_after(migrate.BASELINE)
    assert migrate.data_after(models.SCHEMA_VERSION) == [] == migrate.data_after("old")
    backups = _backups()

    init_db(engine)

    with SessionLocal() as db:
        kinds = [db.get(models.AuditEvent, i).kind for i in ids]
        assert db.get(models.AuditEvent, other_id).kind == "comment"
    assert kinds == ["connector" if hit else "data" for *_, hit in rows]
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION
    with engine.begin() as conn:
        assert migrate.connector_events(conn) == 0  # идемпотентно
    if _sqlite_file():
        new = _backups() - backups
        assert len(new) == 1 and next(iter(new)).name.startswith(f"{_sqlite_file().name}.bak-2026.10-alerts-1-")
        for f in new:
            f.unlink()


def test_unknown_version_with_durable_rows(client, env):
    aid = client.post("/api/ads", json=AD).json()["id"]
    _set_meta("old")
    with pytest.raises(migrate.SchemaError) as e:
        init_db(engine)
    assert "SAQ_ALLOW_SCHEMA_RESET" in str(e.value) and "ads: 1" in str(e.value)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM ads WHERE id = :id"), {"id": aid}).scalar() == 1  # не удалено
    _set_meta("old")
    env(SAQ_ALLOW_SCHEMA_RESET="1")
    init_db(engine)
    assert _ad(client, aid) is None
    assert len(client.get("/api/ads").json()) == 12
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION


def test_unknown_version_without_durable_rows_reseeds(client):
    client.patch("/api/ads/a1/status", json={"status": "in_review", "author": "Тест"})
    _set_meta("old")
    init_db(engine)
    assert _ad(client, "a1")["status"] == "new"


@pytest.mark.parametrize("version", migrate.RETIRED)
def test_retired_version_is_unknown(client, version):
    """Версия схемы до migrate(блок C - "2026.10-audit-1", блок D) - неизвестная: таблицы пересоздаются, а не
    считаются текущими из-за совпадения имени с новым шагом."""
    assert version != models.SCHEMA_VERSION and migrate.chain_from(version) is None
    client.patch("/api/ads/a1/status", json={"status": "in_review", "author": "Тест"})
    _set_meta(version)
    init_db(engine)
    assert _ad(client, "a1")["status"] == "new"
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION


def test_durable_rows(client):
    tables = set(inspect(engine).get_table_names())
    assert migrate.durable_rows(engine, tables) == {}
    with SessionLocal() as db:
        db.add(models.Collector(code="instagram"))  # строка без updated_by восстановима
        db.commit()
    assert migrate.durable_rows(engine, tables) == {}
    with SessionLocal() as db:
        db.get(models.Collector, "instagram").updated_by = "Администратор"
        db.add(models.GeoCheck(target_kind="complex", target_id="c1", query="ул. Тест", query_norm="ул тест",
                               status="candidates", requested_by="Тест"))
        db.commit()
    assert migrate.durable_rows(engine, tables) == {"collectors": 1, "geo_checks": 1}
    with SessionLocal() as db:
        db.add(models.AlertSubscription(chat_id="100500", district="*"))  # подписку жителя из сида не восстановить
        db.commit()
    assert migrate.durable_rows(engine, tables)["alert_subscriptions"] == 1
    assert migrate.durable_rows(engine, {"ads"}) == {}  # только перечисленные таблицы


def test_chain_from_and_add_column(client):
    assert migrate.chain_from(migrate.BASELINE) == migrate.STEPS
    assert migrate.chain_from(models.SCHEMA_VERSION) == []
    assert migrate.chain_from("old") is None and migrate.chain_from(None) is None
    with engine.begin() as conn:
        assert migrate.add_column(conn, "ads", models.Ad.__table__.c.collected_at) is False  # уже есть
        assert migrate.add_column(conn, "quota_counters", Column("extra_n", Integer, server_default="0", nullable=False))
        assert migrate.add_column(conn, "quota_counters", Column("extra_n", Integer, server_default="0")) is False
        with pytest.raises(ValueError):
            migrate.add_column(conn, "quota_counters", Column("bad", Integer, nullable=False))
    assert migrate.backup_sqlite(make_engine("sqlite://"), "x") is None


def test_upgrade_rechecks_version_and_rolls_back(client, monkeypatch):
    _to_baseline()

    def broken(conn):
        conn.execute(text("CREATE TABLE half_done (id INTEGER PRIMARY KEY)"))
        raise RuntimeError("сбой шага")

    monkeypatch.setattr(migrate, "STEPS", [("2026.10-intake-1", broken)])
    with pytest.raises(RuntimeError):
        migrate.upgrade(engine, migrate.BASELINE)
    with engine.connect() as conn:
        assert _meta(conn) == migrate.BASELINE  # версия не сдвинулась
    assert "half_done" not in inspect(engine).get_table_names()  # шаги - в одной транзакции
    monkeypatch.undo()
    for f in _backups():
        f.unlink()
    init_db(engine)
    with engine.connect() as conn:
        assert _meta(conn) == models.SCHEMA_VERSION


# ---------- модели ----------

def test_json_mutation_tracked(client):
    with SessionLocal() as db:
        db.add(models.Collector(code="tiktok"))
        db.add(models.CollectionRun(id=900, collector="tiktok", trigger="manual"))
        db.commit()
    with SessionLocal() as db:
        row = db.get(models.Collector, "tiktok")
        assert row.state == {} and row.config == {} and row.failures == 0 and row.enabled is False
        run = db.get(models.CollectionRun, 900)
        row.state["x"] = 1
        row.facts["keyFp"] = "abc"
        run.notes.append({"level": "info", "text": "старт"})
        db.commit()
    with SessionLocal() as db:
        assert db.get(models.Collector, "tiktok").state == {"x": 1}
        assert db.get(models.Collector, "tiktok").facts == {"keyFp": "abc"}
        assert db.get(models.CollectionRun, 900).notes == [{"level": "info", "text": "старт"}]


def test_as_utc():
    almaty = timezone(timedelta(hours=5))
    assert models.as_utc(None) is None
    assert models.as_utc(datetime(2026, 10, 16, 12, 0)) == datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    got = models.as_utc(datetime(2026, 10, 16, 12, 0, tzinfo=almaty))
    assert got.tzinfo == UTC and got.hour == 7


def test_insert_ignore_and_partial_unique_index(client):
    with SessionLocal() as db:
        vals = {"name": "dgis:abc:all", "count": 0, "updated_at": datetime(2026, 10, 16, 12, tzinfo=timezone(
            timedelta(hours=5)))}
        assert models.insert_ignore(db, models.QuotaCounter, vals, ["name"]) is True
        assert models.insert_ignore(db, models.QuotaCounter, {**vals, "count": 5}, ["name"]) is False
        db.commit()
        row = db.get(models.QuotaCounter, "dgis:abc:all")
        assert row.count == 0 and models.as_utc(row.updated_at).hour == 7
        # не больше одного активного задания на dedupe_key; завершённое не мешает новому
        where = text(models.ACTIVE_JOB)
        job = {"kind": "collect", "dedupe_key": "collect:krisha", "status": "queued"}
        assert models.insert_ignore(db, models.Job, job, ["dedupe_key"], where=where) is True
        assert models.insert_ignore(db, models.Job, job, ["dedupe_key"], where=where) is False
        db.execute(models.Job.__table__.update().values(status="done"))
        assert models.insert_ignore(db, models.Job, job, ["dedupe_key"], where=where) is True
        db.commit()
        jobs = db.scalars(select(models.Job).order_by(models.Job.id)).all()
        assert [j.status for j in jobs] == ["done", "queued"]
        assert jobs[1].payload == {} and jobs[1].attempts == 0 and jobs[1].max_attempts == 5 and jobs[1].run_after


def _collected_ad(db, *, media=True) -> str:
    aid = uuid.uuid4().hex
    now = models.utcnow()
    db.add(models.Ad(id=aid, platform="tiktok", account="@tumargroup", url="", date="2026-10-16T13:00", text="Продажи",
                     source="parser", origin="collected", collected_at=now))
    db.flush()
    item = models.IngestItem(platform="tiktok", external_id="7694623823408188693", collector="intake", ad_id=aid,
                             content_hash="h" * 64, analysis_status="queued")
    db.add(item)
    db.flush()
    if media:
        for i, (kind, status, current) in enumerate([("image", "downloaded", True), ("video", "downloaded", True),
                                                      ("image", "pending", True), ("image", "downloaded", False),
                                                      ("frame", "downloaded", True)]):
            db.add(models.MediaAsset(id=uuid.uuid4().hex, ad_id=aid, item_id=item.id, position=10 - i, kind=kind,
                                     status=status, current=current, source_key=f"k{i}", mime="image/png"))
    db.commit()
    return aid


def test_ad_out_for_collected_and_demo_ads(client):
    demo = _ad(client, "a1")
    assert not {"collectedAt", "media", "processing"} & set(demo)  # прежний JSON демо-публикаций
    assert demo["origin"] == "demo"
    with SessionLocal() as db:
        aid = _collected_ad(db)
    got = _ad(client, aid)
    assert got["origin"] == "collected" and got["collectedAt"].endswith("+00:00")
    assert got["processing"] == {"status": "queued", "version": 1, "analyzedVersion": None, "stage": None,
                                 "error": None}
    # только загруженные изображения и видео текущей версии, по позиции
    assert [m["kind"] for m in got["media"]] == ["video", "image"]
    assert all(m["url"] == f"/api/media/{m['id']}" for m in got["media"])
    from app import world

    with SessionLocal() as db:
        d = world.ad_dict(db.get(models.Ad, aid))
        assert d["collectedAt"] and len(d["media"]) == 2
        plain = schemas.AdOut.model_validate(db.get(models.Ad, "a1")).model_dump()
        assert "collected_at" not in plain and "media" not in plain and "processing" not in plain


def test_human_name_and_connector_schemas():
    assert schemas.AdminActionIn(author=" Омарова Ж. ").author == "Омарова Ж."
    for name in ("Система", " система ", "СИСТЕМА"):
        with pytest.raises(ValidationError) as e:
            schemas.AdminActionIn(author=name)
        assert "Имя «Система» зарезервировано за автоматическими записями" in str(e.value)
    with pytest.raises(ValidationError):
        schemas.ConnectorPatchIn(author="Тест", config={"unknown": 1})
    with pytest.raises(ValidationError):
        schemas.ConnectorPatchIn(author="Тест", interval_min=1)
    body = schemas.ConnectorPatchIn.model_validate({"author": "Тест", "config": {"searchUrls": ["https://krisha.kz/x"],
                                                                                   "resultsPerPage": 5}})
    assert body.config.search_urls == ["https://krisha.kz/x"] and body.config.results_per_page == 5
    assert __import__("app.pdf", fromlist=["PLATFORMS"]).PLATFORMS["other"] == "Другая площадка"


def _intake(**kw):
    return schemas.IntakePublicationIn.model_validate({"platform": "tiktok", "externalId": "7694623823408188693", **kw})


def test_intake_publication_schema():
    b = _intake(text="a\x00b" + "x" * 30000, account="@x\x00y", publishedAt="2026-10-09T11:10:23.000Z")
    assert b.text.startswith("ab") and len(b.text) == schemas.AD_TEXT_MAX and b.account == "@xy"
    assert b.published_at == datetime(2026, 10, 9, 11, 10, 23, tzinfo=UTC)
    tg = schemas.IntakePublicationIn.model_validate({"platform": "telegram", "externalId": "-1001234567890:5"})
    assert tg.external_id == "1234567890:5"

    def err(**kw) -> str:
        with pytest.raises(ValidationError) as e:
            schemas.IntakePublicationIn.model_validate({"platform": "tiktok", "externalId": "1", **kw})
        return str(e.value)

    assert "externalId для telegram: ожидается" in err(platform="telegram", externalId="abc")
    assert "externalId для krisha" in err(platform="krisha", externalId="12a")
    assert "поля решений аналитика при приёме публикаций не принимаются: status, complexId" in err(
        status="confirmed", complexId="c1")
    assert "extra_forbidden" in err(unknown=1) or "Extra inputs" in err(unknown=1)
    assert "publishedAt: укажите часовой пояс" in err(publishedAt="2026-10-09T11:10:23")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    assert "collectedAt в будущем" in err(collectedAt=future)
    assert "collectedAt старше 30 дней" in err(collectedAt=old)
    assert "media.url: нужна ссылка https://" in err(media=[{"key": "a", "url": "http://x/y.png"}])
    assert "ключи файлов повторяются: a" in err(media=[{"key": "a", "url": "https://x/1"}, {"key": "a", "url": "https://x/2"}])
    assert err(media=[{"key": f"k{i}", "url": f"https://x/{i}"} for i in range(21)])
    assert "url: нужна ссылка http(s)://" in err(url="javascript:alert(1)")
    assert err(sourceId="Bad Id")
    out = schemas.IntakeResultOut(status="created", ad_id="a" * 32, item_id=1, version=1, media_queued=2,
                                  analysis_queued=True).model_dump(by_alias=True)
    assert out == {"status": "created", "adId": "a" * 32, "itemId": 1, "version": 1, "mediaQueued": 2,
                   "analysisQueued": True}


def test_add_column_default_literal(client):
    """DEFAULT добавляемого столбца - литерал диалекта: кавычка в строке экранируется, а не обрывает DDL."""
    with SessionLocal() as db:
        db.add(models.QuotaCounter(name="q:literal"))
        db.commit()
    with engine.begin() as conn:
        col = Column("extra_note", String(32), server_default="it's; DROP TABLE ads --", nullable=False)
        assert migrate.add_column(conn, "quota_counters", col) is True
        got = conn.execute(text("SELECT extra_note FROM quota_counters WHERE name = 'q:literal'")).scalar()
    assert got == "it's; DROP TABLE ads --"
    assert "ads" in inspect(engine).get_table_names()


def test_upgrade_logs_version_read_under_lock(client, caplog):
    """Версию, от которой идут шаги, upgrade перечитывает под блокировкой: в журнал - она, а не устаревшая из вызова."""
    with engine.begin() as conn:
        for t in reversed(Base.metadata.sorted_tables):
            if t.name in migrate.ALERT_TABLES:
                t.drop(conn)
    _set_meta("2026.10-decisions-1")
    before = _backups()
    with caplog.at_level("WARNING", logger="saq"):
        migrate.upgrade(engine, migrate.BASELINE)
    assert set(migrate.ALERT_TABLES) <= set(inspect(engine).get_table_names())
    assert f"Схема БД обновлена: 2026.10-decisions-1 → {models.SCHEMA_VERSION}" in caplog.text
    for f in _backups() - before:
        f.unlink()


def test_backup_sqlite_owner_only_and_unique(tmp_path, monkeypatch):
    eng = make_engine(f"sqlite:///{tmp_path}/b.db")
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)"))
            conn.execute(text("INSERT INTO t (v) VALUES ('секрет')"))
        first = migrate.backup_sqlite(eng, "v/1")
        second = migrate.backup_sqlite(eng, "v/1")  # та же секунда - другое имя, первая копия не перезаписана
        assert first != second and first.name.startswith("b.db.bak-v_1-")
        for f in (first, second):
            assert f.stat().st_mode & 0o777 == 0o600  # в копии вся БД, в том числе токены подключений
            with make_engine(f"sqlite:///{f}").connect() as conn:
                assert conn.execute(text("SELECT v FROM t")).scalar() == "секрет"

        class Boom(sqlite3_module.Connection):
            def backup(self, *a, **kw):
                raise sqlite3_module.OperationalError("диск полон")

        monkeypatch.setattr(migrate.sqlite3, "connect", Boom)
        with pytest.raises(sqlite3_module.OperationalError):
            migrate.backup_sqlite(eng, "v2")
        assert not list(tmp_path.glob("b.db.bak-v2-*"))  # недописанная копия удалена
    finally:
        eng.dispose()
