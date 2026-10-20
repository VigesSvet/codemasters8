"""Обновление схемы БД на месте: цепочка шагов от базовой версии к models.SCHEMA_VERSION.

Правила для шагов: каждый шаг идемпотентен и только добавляет(новые таблицы, новые nullable-столбцы или столбцы с
константным server_default) или ослабляет ограничение(NOT NULL → NULL, relax_not_null) - ничего не удаляет и данные
не переписывает; исключение - перевод прежних строк в новое значение(вид событий журнала, STEP_DATA): то же действие
выполняет загрузка стартового набора прежней версии(app.snapshot). Новая версия схемы = новый шаг в конце STEPS.
Перед шагами файловая SQLite копируется(sqlite3 backup API) в "<db>.bak-<старая версия>-<UTC>"; резервную копию
PostgreSQL делает оператор(pg_dump, см. документацию). Неизвестная версия схемы обрабатывается в seed.init_db:
пересоздание таблиц - только если в базе нет собранных и внесённых данных(durable_rows).
"""
import logging
import os
import re
import sqlite3
from datetime import datetime, UTC
from pathlib import Path
from collections.abc import Callable

from sqlalchemy import Column, and_, column, func, insert, inspect, or_, select, table, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateTable

from . import models
from .db import Base, foreign_keys_off

log = logging.getLogger("saq")

BASELINE = "2026.10-sources-1"
NEW_TABLES = ("collectors", "collection_runs", "ingest_items", "ingest_versions", "media_assets", "jobs",
              "worker_leases", "oauth_states", "integration_tokens", "geo_checks", "bot_submissions", "quota_counters")
LLM_TABLES = ("llm_analyses",)  # 2026.10-llm-1
DISCOVERY_TABLES = ("discovered_objects", "discovered_mentions")  # 2026.10-discovery-1
DECISION_TABLES = ("screening_decisions",)  # 2026.10-decisions-1
ALERT_TABLES = ("resident_alerts", "alert_deliveries", "alert_subscriptions")  # 2026.10-alerts-1
ADVISORY_LOCK = 724001  # pg_advisory_xact_lock: два процесса не обновляют схему одновременно

Step = tuple[str, Callable[[Connection], None]]
_META = table("meta", column("key"), column("value"))
_AUDIT = table("audit_events", column("kind"), column("who"), column("what"))


class SchemaError(RuntimeError):
    """Схема БД неизвестной версии, а в базе есть данные, которые нельзя пересоздать из сида."""


def add_column(conn: Connection, table_name: str, col: Column) -> bool:
    """ALTER TABLE … ADD COLUMN, если столбца нет(inspect); только nullable или с константным server_default. Идемпотентно."""
    present = {c["name"] for c in inspect(conn).get_columns(table_name)}
    if col.name in present:
        return False
    # константа DEFAULT - литералом диалекта(экранирование SQLAlchemy); без скобок вокруг выражения - их SQLite в
    # ADD COLUMN не принимает(поэтому не get_column_specification)
    default = conn.dialect.ddl_compiler(conn.dialect, None).get_column_default_string(col)
    if not col.nullable and default is None:
        raise ValueError(f"Столбец {table_name}.{col.name}: добавить можно только nullable или с server_default")
    prep = conn.dialect.identifier_preparer
    ddl = f"ALTER TABLE {prep.quote(table_name)} ADD COLUMN {prep.quote(col.name)} {col.type.compile(dialect=conn.dialect)}"
    if default is not None:
        ddl += f" DEFAULT {default}"
    if not col.nullable:
        ddl += " NOT NULL"
    conn.execute(text(ddl))
    return True


def _intake_1(conn: Connection) -> None:
    """2026.10-intake-1: таблицы сбора публикаций и время получения публикации(ads.collected_at)."""
    for name in NEW_TABLES:
        Base.metadata.tables[name].create(conn, checkfirst=True)
    add_column(conn, "ads", models.Ad.__table__.c.collected_at)


def _llm_1(conn: Connection) -> None:
    """2026.10-llm-1: результаты анализа ИИ - таблица llm_analyses. Строка прежнего анализатора(только
    статус) в collectors не удаляется(шаги только добавляют): её кода в реестре нет, её никто не читает."""
    for name in LLM_TABLES:
        Base.metadata.tables[name].create(conn, checkfirst=True)


def relax_not_null(conn: Connection, table_name: str, names: tuple) -> bool:
    """Снять NOT NULL со столбцов names таблицы приложения(идемпотентно; True - что-то изменено). PostgreSQL -
    ALTER COLUMN … DROP NOT NULL. SQLite так не умеет: таблица пересобирается по текущей модели(CREATE … → INSERT …
    SELECT → DROP → RENAME, индексы - заново) в транзакции шага; проверка внешних ключей SQLite в upgrade
    выключена(db.foreign_keys_off), ссылки других таблиц остаются по имени. Столбцы, которых нет в модели, -
    ошибка(их данные иначе пропали бы)."""
    cols = {c["name"]: c for c in inspect(conn).get_columns(table_name)}
    need = [n for n in names if n in cols and not cols[n]["nullable"]]
    if not need:
        return False
    prep = conn.dialect.identifier_preparer
    if conn.dialect.name == "postgresql":
        for n in need:
            conn.execute(text(f"ALTER TABLE {prep.quote(table_name)} ALTER COLUMN {prep.quote(n)} DROP NOT NULL"))
        return True
    if conn.dialect.name != "sqlite":  # pragma: no cover - приложение работает только с SQLite и PostgreSQL
        raise NotImplementedError(f"relax_not_null: диалект {conn.dialect.name} не поддерживается")
    t = Base.metadata.tables[table_name]
    extra = sorted(set(cols) - set(t.c.keys()))
    if extra:
        raise RuntimeError(f"Таблица {table_name}: столбцы {extra} не описаны в модели — пересборка их потеряла бы")
    tmp = f"{table_name}__relax"
    head = f"CREATE TABLE {prep.quote(table_name)} "
    ddl = str(CreateTable(t).compile(dialect=conn.dialect)).strip()
    if not ddl.startswith(head):  # pragma: no cover - формат CREATE TABLE SQLAlchemy
        raise RuntimeError(f"Неожиданный CREATE TABLE для {table_name}")
    conn.execute(text(f"DROP TABLE IF EXISTS {prep.quote(tmp)}"))
    conn.execute(text(f"CREATE TABLE {prep.quote(tmp)} " + ddl[len(head):]))
    keep = [c.name for c in t.c if c.name in cols]
    src, dst = (table(n, *(column(c) for c in keep)) for n in (table_name, tmp))
    conn.execute(insert(dst).from_select(keep, select(*src.c)))
    conn.execute(text(f"DROP TABLE {prep.quote(table_name)}"))
    conn.execute(text(f"ALTER TABLE {prep.quote(tmp)} RENAME TO {prep.quote(table_name)}"))
    for idx in t.indexes:
        idx.create(conn)
    return True


def _discovery_1(conn: Connection) -> None:
    """2026.10-discovery-1: объекты из рекламы(discovered_objects, discovered_mentions) и ЖК без координат
    (complexes.lat/lng допускают NULL: объект реестра без координат, ЖК по рекламе - точка после сверки 2ГИС)."""
    for name in DISCOVERY_TABLES:
        Base.metadata.tables[name].create(conn, checkfirst=True)
    relax_not_null(conn, "complexes", ("lat", "lng"))


def _verification_1(conn: Connection) -> None:
    """2026.10-verification-1: сверка сведений аналитиком - перечень ГАСК и "обращений нет - сверено" у ЖК
    (complexes.published_list_check, appeals_check), кто внёс и кто проверил обращение(appeals.*)."""
    for name in ("published_list_check", "appeals_check"):
        add_column(conn, "complexes", models.Complex.__table__.c[name])
    for name in ("comment", "created_by", "created_at", "decided_by", "decision_comment"):
        add_column(conn, "appeals", models.Appeal.__table__.c[name])


def _decisions_1(conn: Connection) -> None:
    """2026.10-decisions-1: решения аналитика по "Проверке по рекламе" - "Объект сдан" и "Проверено - замечаний нет"
    (таблица screening_decisions; история отменённых и заменённых решений остаётся)."""
    for name in DECISION_TABLES:
        Base.metadata.tables[name].create(conn, checkfirst=True)


def _alerts_1(conn: Connection) -> None:
    """2026.10-alerts-1: предупреждения жителям - тексты и согласование(resident_alerts), доставки в канал ГАСК и
    подписчикам(alert_deliveries), подписки жителей на районы от бота заявок(alert_subscriptions)."""
    for name in ALERT_TABLES:
        Base.metadata.tables[name].create(conn, checkfirst=True)


# События подключений, которые до 2026.10-audit-2 писались видом data: (автор или None - любой, шаблон LIKE текста).
# Шаблоны - по строкам integrations.service.transition, service.patch и olx.CONNECTED_AUDIT / DISCONNECTED_AUDIT
# (с 1d55e6d не менялись); "_" и "%" в тексте шаблона - подстановочные знаки LIKE
LEGACY_CONNECTOR_EVENTS: tuple[tuple[str | None, str], ...] = (
    ("Система", "Источник «%»: %"),
    (None, "Настройки источника «%» изменены: %"),
    (None, "OLX: подключён аккаунт % (OAuth). Доступны только ресурсы этого аккаунта — чужие объявления так не "
           "собираются"),
    (None, "OLX: аккаунт % отключён — токены удалены с сервера"),
)
_LEGACY_RX = tuple((who, re.compile("".join({"%": ".*", "_": "."}.get(ch) or re.escape(ch) for ch in like), re.S))
                   for who, like in LEGACY_CONNECTOR_EVENTS)


def legacy_connector_event(kind: str | None, who: str | None, what: str | None) -> bool:
    """Событие подключения, записанное до 2026.10-audit-2 видом data(LEGACY_CONNECTOR_EVENTS) - то же условие, что
    у connector_events, для строк вне БД(выгрузка набора)."""
    return kind == "data" and any((w is None or who == w) and rx.fullmatch(what or "") for w, rx in _LEGACY_RX)


def connector_events(conn: Connection) -> int:
    """События подключений вида data(LEGACY_CONNECTOR_EVENTS) → вид connector: в их тексте - аккаунты, списки
    источников, модель и лимиты, имя владельца аккаунта OLX - сведения администратора. Идемпотентно; число строк."""
    A = _AUDIT
    match = [and_(A.c.what.like(like), *([A.c.who == who] if who is not None else []))
             for who, like in LEGACY_CONNECTOR_EVENTS]
    return conn.execute(update(A).where(A.c.kind == "data", or_(*match)).values(kind="connector")).rowcount or 0


def _audit_2(conn: Connection) -> None:
    """2026.10-audit-2: события подключений в журнале - свой вид connector(прежние data - connector_events)."""
    connector_events(conn)


STEPS: list[Step] = [("2026.10-intake-1", _intake_1), ("2026.10-llm-1", _llm_1), ("2026.10-discovery-1", _discovery_1),
                     ("2026.10-verification-1", _verification_1), ("2026.10-decisions-1", _decisions_1),
                     ("2026.10-alerts-1", _alerts_1), ("2026.10-audit-2", _audit_2)]
# Версии схемы до migrate(блоки C и D плана): база такой версии - неизвестная(seed.init_db пересоздаёт таблицы), имена
# шагам не давать - иначе её схема считалась бы текущей или обновилась бы не теми шагами
RETIRED = ("2026.10-audit-1", "2026.10-ai-2")
# Таблицы, которые создаёт шаг: набор(app.snapshot) прежней версии схемы их не содержит - загружаются пустыми
STEP_TABLES: dict[str, tuple] = {"2026.10-intake-1": NEW_TABLES, "2026.10-llm-1": LLM_TABLES,
                                 "2026.10-discovery-1": DISCOVERY_TABLES, "2026.10-decisions-1": DECISION_TABLES,
                                 "2026.10-alerts-1": ALERT_TABLES}
# Перевод прежних строк, который шаг делает с данными: загрузка набора прежней версии выполняет его после вставки строк
STEP_DATA: dict[str, Callable[[Connection], object]] = {"2026.10-audit-2": connector_events}
# не assert - проверка не должна пропадать при python -O
if STEPS[-1][0] != models.SCHEMA_VERSION:  # pragma: no cover - ошибка разработчика при добавлении шага
    raise RuntimeError("SCHEMA_VERSION должна совпадать с последним шагом migrate.STEPS")
if not set(STEP_TABLES) | set(STEP_DATA) <= {name for name, _ in STEPS}:  # pragma: no cover
    raise RuntimeError("migrate.STEP_TABLES / STEP_DATA: шаг не из migrate.STEPS")
if {name for name, _ in STEPS} & set(RETIRED):  # pragma: no cover
    raise RuntimeError("migrate.STEPS: имя версии схемы до migrate(RETIRED) - база той версии считалась бы обновлённой")


def chain_from(version: str | None) -> list[Step] | None:
    """Шаги от version до текущей схемы; None - от этой версии обновления нет(неизвестная/старая)."""
    if version == BASELINE:
        return list(STEPS)
    names = [name for name, _ in STEPS]
    if version in names:
        return list(STEPS[names.index(version) + 1:])
    return None


def tables_after(version: str | None) -> set:
    """Таблицы, которые добавили шаги после version(version - из цепочки; иначе - пусто): в данных этой версии их нет."""
    return {t for name, _ in chain_from(version) or [] for t in STEP_TABLES.get(name, ())}


def data_after(version: str | None) -> list[Callable[[Connection], object]]:
    """Переводы данных шагов после version(STEP_DATA) по порядку: строки набора этой версии их ещё не прошли."""
    return [STEP_DATA[name] for name, _ in chain_from(version) or [] if name in STEP_DATA]


def _sqlite_file(eng: Engine) -> Path | None:
    if eng.dialect.name != "sqlite":
        return None
    db = eng.url.database
    if not db or db == ":memory:" or db.startswith("file:") or eng.url.query.get("mode") == "memory":
        return None
    path = Path(db)
    return path if path.is_file() else None


def _reserve(base: Path) -> Path:
    """Создать пустой файл base(или base-2, base-3, …, если занято) атомарно(O_EXCL - два процесса не пишут в одну
    копию) и только для владельца(0600): в копии - вся БД, в том числе токены подключений."""
    dest, n = base, 1
    while True:
        try:
            os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            n += 1
            dest = base.with_name(f"{base.name}-{n}")
        else:
            return dest


def backup_sqlite(eng: Engine, old_version: str | None, reason: str = "перед обновлением схемы") -> Path | None:
    """Файловая SQLite: копия sqlite3 backup API(согласована при WAL) в "<db>.bak-<old>-<UTC YYYYmmddTHHMMSSZ>";
    :memory: и PostgreSQL → None. Лог: "Резервная копия БД перед обновлением схемы: {path}"(reason - другой повод,
    например удаление демо-данных)."""
    src_path = _sqlite_file(eng)
    if src_path is None:
        return None
    tag = re.sub(r"[^A-Za-z0-9._-]", "_", old_version or "none")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = _reserve(src_path.with_name(f"{src_path.name}.bak-{tag}-{stamp}"))
    try:
        src = sqlite3.connect(str(src_path))
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except BaseException:
        dest.unlink(missing_ok=True)  # недописанная копия не должна выглядеть как резервная
        raise
    log.warning("Резервная копия БД %s: %s", reason, dest)
    return dest


def _current_version(conn: Connection) -> str | None:
    return conn.execute(select(_META.c.value).where(_META.c.key == "schema_version")).scalar()


def upgrade(eng: Engine, version: str | None) -> None:
    """backup_sqlite → шаги в одной транзакции(SQLite - без проверки внешних ключей, db.foreign_keys_off); на
    PostgreSQL первым запросом pg_advisory_xact_lock(724001); затем UPDATE meta schema_version.
    Лог: "Схема БД обновлена: {old} → {new}, данные сохранены"."""
    steps = chain_from(version)
    if steps is None:
        raise SchemaError(f"Нет шагов обновления схемы БД от версии {version or 'без версии'}")
    backup_sqlite(eng, version)
    # SQLite: пересборка таблицы(relax_not_null) удаляет родителя, на которого ссылаются другие таблицы
    with foreign_keys_off(eng) as conn, conn.begin():
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK})
        elif conn.dialect.name == "sqlite":
            # pysqlite не открывает транзакцию перед DDL - явный BEGIN делает шаги атомарными и берёт блокировку записи
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        now_ver = _current_version(conn)
        if now_ver == models.SCHEMA_VERSION:  # другой процесс уже обновил, пока мы ждали блокировку
            return
        steps = chain_from(now_ver)
        if steps is None:  # версию сменили, пока мы ждали блокировку, и обновления от неё нет
            raise SchemaError(f"Нет шагов обновления схемы БД от версии {now_ver or 'без версии'}")
        for _name, step in steps:
            step(conn)
        conn.execute(_META.update().where(_META.c.key == "schema_version").values(value=models.SCHEMA_VERSION))
    log.warning("Схема БД обновлена: %s → %s, данные сохранены", now_ver, models.SCHEMA_VERSION)


DURABLE_TABLES = ("ingest_items", "media_assets", "integration_tokens", "bot_submissions", "geo_checks", "llm_analyses",
                  "discovered_objects", "screening_decisions", "resident_alerts", "alert_subscriptions")
# Таблицы, где невосстановимы только некоторые строки - кортежи(таблица, нужные столбцы, условие по ним)
DURABLE_WHERE: tuple[tuple[str, tuple[str, ...], Callable], ...] = (
    ("collectors", ("updated_by",), lambda t: t.c.updated_by.is_not(None)),
    ("ads", ("origin",), lambda t: t.c.origin.in_(("collected", "analyst"))),
    ("appeals", ("origin",), lambda t: t.c.origin == "analyst"),
    # сверка сведений аналитиком хранится в полях ЖК(основание, готовность, перечень) - её след - события "данные"
    ("audit_events", ("kind", "who"), lambda t: and_(t.c.kind == "data", t.c.who != "Система")),
)


def durable_rows(eng: Engine, present_tables: set) -> dict[str, int]:
    """Ненулевые счётчики невосстановимых данных: ingest_items, media_assets, integration_tokens, bot_submissions,
    geo_checks, llm_analyses(оплаченные результаты анализа ИИ), discovered_objects(объекты из рекламы и решения по ним),
    screening_decisions(решения аналитика "сдан" / "проверено - замечаний нет"), resident_alerts(предупреждения жителям
    и кто их согласовал), alert_subscriptions(подписки жителей от бота); collectors с updated_by IS NOT NULL; ads с
    origin IN('collected','analyst'); appeals с origin = 'analyst'
    и audit_events вида data не от "Системы"(сверка сведений аналитиком, app.verification) - только если таблица/столбец
    есть."""
    out: dict[str, int] = {}
    insp = inspect(eng)

    def cols(name: str) -> set:
        try:
            return {c["name"] for c in insp.get_columns(name)}
        except SQLAlchemyError:  # таблицу удалили после списка present_tables
            return set()

    with eng.connect() as conn:
        def count(t, *where) -> None:
            n = conn.execute(select(func.count()).select_from(t).where(*where)).scalar() or 0
            if n:
                out[t.name] = int(n)

        for name in DURABLE_TABLES:
            if name in present_tables:
                count(table(name))
        for name, need, where in DURABLE_WHERE:
            if name in present_tables and set(need) <= cols(name):
                t = table(name, *(column(c) for c in need))
                count(t, where(t))
    return out
