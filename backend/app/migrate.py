"""Обновление схемы БД на месте: цепочка шагов от базовой версии к models.SCHEMA_VERSION.

Правила для шагов: каждый шаг идемпотентен и только добавляет (новые таблицы, новые nullable-столбцы или столбцы с
константным server_default) — ничего не удаляет и не переписывает. Новая версия схемы = новый шаг в конце STEPS.
Перед шагами файловая SQLite копируется (sqlite3 backup API) в «<db>.bak-<старая версия>-<UTC>»; резервную копию
PostgreSQL делает оператор (pg_dump, см. документацию). Неизвестная версия схемы обрабатывается в seed.init_db:
пересоздание таблиц — только если в базе нет собранных и внесённых данных (durable_rows).
"""
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import Column, column, func, inspect, select, table, text
from sqlalchemy.engine import Connection, Engine

from . import models
from .db import Base

log = logging.getLogger("saq")

BASELINE = "2026.10-sources-1"
NEW_TABLES = ("collectors", "collection_runs", "ingest_items", "ingest_versions", "media_assets", "jobs",
              "worker_leases", "oauth_states", "integration_tokens", "geo_checks", "bot_submissions", "quota_counters")
ADVISORY_LOCK = 724001  # pg_advisory_xact_lock: два процесса не обновляют схему одновременно

Step = tuple[str, Callable[[Connection], None]]


class SchemaError(RuntimeError):
    """Схема БД неизвестной версии, а в базе есть данные, которые нельзя пересоздать из сида."""


def add_column(conn: Connection, table_name: str, col: Column) -> bool:
    """ALTER TABLE … ADD COLUMN, если столбца нет (inspect); только nullable или с константным server_default. Идемпотентно."""
    present = {c["name"] for c in inspect(conn).get_columns(table_name)}
    if col.name in present:
        return False
    if not col.nullable and col.server_default is None:
        raise ValueError(f"Столбец {table_name}.{col.name}: добавить можно только nullable или с server_default")
    prep = conn.dialect.identifier_preparer
    ddl = f"ALTER TABLE {prep.quote(table_name)} ADD COLUMN {prep.quote(col.name)} {col.type.compile(dialect=conn.dialect)}"
    if col.server_default is not None:
        arg = col.server_default.arg
        if isinstance(arg, str):
            ddl += " DEFAULT '" + arg.replace("'", "''") + "'"
        else:
            ddl += " DEFAULT " + str(arg.compile(dialect=conn.dialect, compile_kwargs={"literal_binds": True}))
    if not col.nullable:
        ddl += " NOT NULL"
    conn.execute(text(ddl))
    return True


def _intake_1(conn: Connection) -> None:
    """2026.10-intake-1: таблицы сбора публикаций и время получения публикации (ads.collected_at)."""
    for name in NEW_TABLES:
        Base.metadata.tables[name].create(conn, checkfirst=True)
    add_column(conn, "ads", models.Ad.__table__.c.collected_at)


STEPS: list[Step] = [("2026.10-intake-1", _intake_1)]
assert STEPS[-1][0] == models.SCHEMA_VERSION, "SCHEMA_VERSION должна совпадать с последним шагом migrate.STEPS"


def chain_from(version: Optional[str]) -> Optional[list[Step]]:
    """Шаги от version до текущей схемы; None — от этой версии обновления нет (неизвестная/старая)."""
    if version == BASELINE:
        return list(STEPS)
    names = [name for name, _ in STEPS]
    if version in names:
        return list(STEPS[names.index(version) + 1:])
    return None


def _sqlite_file(eng: Engine) -> Optional[Path]:
    if eng.dialect.name != "sqlite":
        return None
    db = eng.url.database
    if not db or db == ":memory:" or db.startswith("file:") or eng.url.query.get("mode") == "memory":
        return None
    path = Path(db)
    return path if path.is_file() else None


def backup_sqlite(eng: Engine, old_version: Optional[str]) -> Optional[Path]:
    """Файловая SQLite: копия sqlite3 backup API (согласована при WAL) в «<db>.bak-<old>-<UTC YYYYmmddTHHMMSSZ>»;
    :memory: и PostgreSQL → None. Лог: «Резервная копия БД перед обновлением схемы: {path}»."""
    src_path = _sqlite_file(eng)
    if src_path is None:
        return None
    tag = re.sub(r"[^A-Za-z0-9._-]", "_", old_version or "none")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = src_path.with_name(f"{src_path.name}.bak-{tag}-{stamp}")
    n = 1
    while dest.exists():
        n += 1
        dest = src_path.with_name(f"{src_path.name}.bak-{tag}-{stamp}-{n}")
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    log.warning("Резервная копия БД перед обновлением схемы: %s", dest)
    return dest


def _current_version(conn: Connection) -> Optional[str]:
    meta = table("meta", column("key"), column("value"))
    return conn.execute(select(meta.c.value).where(meta.c.key == "schema_version")).scalar()


def upgrade(eng: Engine, version: Optional[str]) -> None:
    """backup_sqlite → шаги в одной транзакции (eng.begin()); на PostgreSQL первым запросом pg_advisory_xact_lock(724001);
    затем UPDATE meta schema_version. Лог: «Схема БД обновлена: {old} → {new}, данные сохранены»."""
    steps = chain_from(version)
    if steps is None:
        raise SchemaError(f"Нет шагов обновления схемы БД от версии {version or 'без версии'}")
    backup_sqlite(eng, version)
    with eng.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK})
        elif conn.dialect.name == "sqlite":
            # pysqlite не открывает транзакцию перед DDL — явный BEGIN делает шаги атомарными и берёт блокировку записи
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        now_ver = _current_version(conn)
        if now_ver == models.SCHEMA_VERSION:  # другой процесс уже обновил, пока мы ждали блокировку
            return
        steps = chain_from(now_ver)
        if steps is None:  # версию сменили, пока мы ждали блокировку, и обновления от неё нет
            raise SchemaError(f"Нет шагов обновления схемы БД от версии {now_ver or 'без версии'}")
        for _name, step in steps:
            step(conn)
        meta = table("meta", column("key"), column("value"))
        conn.execute(meta.update().where(meta.c.key == "schema_version").values(value=models.SCHEMA_VERSION))
    log.warning("Схема БД обновлена: %s → %s, данные сохранены", version, models.SCHEMA_VERSION)


DURABLE_TABLES = ("ingest_items", "media_assets", "integration_tokens", "bot_submissions", "geo_checks")


def durable_rows(eng: Engine, present_tables: set) -> dict[str, int]:
    """Ненулевые счётчики невосстановимых данных: ingest_items, media_assets, integration_tokens, bot_submissions,
    geo_checks; collectors с updated_by IS NOT NULL; ads с origin IN ('collected','analyst') — только если таблица/столбец есть."""
    out: dict[str, int] = {}
    insp = inspect(eng)

    def cols(name: str) -> set:
        try:
            return {c["name"] for c in insp.get_columns(name)}
        except Exception:
            return set()

    with eng.connect() as conn:
        for name in DURABLE_TABLES:
            if name in present_tables:
                n = conn.execute(select(func.count()).select_from(table(name))).scalar() or 0
                if n:
                    out[name] = int(n)
        if "collectors" in present_tables and "updated_by" in cols("collectors"):
            t = table("collectors", column("updated_by"))
            n = conn.execute(select(func.count()).select_from(t).where(t.c.updated_by.is_not(None))).scalar() or 0
            if n:
                out["collectors"] = int(n)
        if "ads" in present_tables and "origin" in cols("ads"):
            t = table("ads", column("origin"))
            n = conn.execute(select(func.count()).select_from(t)
                             .where(t.c.origin.in_(("collected", "analyst")))).scalar() or 0
            if n:
                out["ads"] = int(n)
    return out
