import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event, inspect, update
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# PostgreSQL по умолчанию(docker compose). Для быстрого запуска без БД: DATABASE_URL=sqlite:///./saq.db
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://saq:saq@localhost:5432/saq")

# SQLite: API и воркер пишут в один файл - ожидание блокировки вместо мгновенного "database is locked", WAL - чтение
# не ждёт записи. Значение читается при каждом новом соединении(тесты могут уменьшить его)
BUSY_TIMEOUT_MS = 30000


def _is_memory(url) -> bool:
    db = url.database
    return not db or db == ":memory:" or url.query.get("mode") == "memory" or db.startswith("file::memory:")


def make_engine(url: str, **kwargs) -> Engine:
    """Engine приложения. SQLite: timeout соединения 30 с, PRAGMA busy_timeout, foreign_keys=ON(внешние ключи
    проверяются, как в PostgreSQL) и journal_mode=WAL(кроме :memory:);
    PostgreSQL - с проверкой соединения перед выдачей из пула(pool_pre_ping: перезапуск БД не даёт 500 на первом
    запросе после него). У файла SQLite обрывов соединения нет - лишний SELECT 1 на каждую сессию не нужен."""
    u = make_url(url)
    if u.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True, **kwargs)
    connect_args = {"timeout": 30, **kwargs.pop("connect_args", {})}
    eng = create_engine(url, connect_args=connect_args, **kwargs)
    memory = _is_memory(u)

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            if not memory:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()

    return eng


@contextmanager
def foreign_keys_off(eng: Engine) -> Iterator[Connection]:
    """Соединение без проверки внешних ключей SQLite - для удаления и пересборки таблиц(DROP TABLE родителя с
    живыми ссылками иначе отклоняется). PRAGMA foreign_keys действует только вне транзакции: она выполняется до
    первой транзакции вызывающего(with conn.begin()), а после - снова ON, и соединение возвращается в пул с
    проверкой. Не SQLite - обычное соединение."""
    with eng.connect() as conn:
        if conn.dialect.name != "sqlite":
            yield conn
            return
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()  # незавершённая транзакция вызывающего не должна поглотить PRAGMA
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()


def get_for_update[M](db: Session, entity: type[M], key: Any) -> M | None:
    """Строка по первичному ключу под блокировкой до конца транзакции, с перечитанными полями(populate_existing) -
    для чтения-изменения-записи(JSON-поля ЖК, решения аналитика): SELECT … FOR UPDATE.

    SQLite FOR UPDATE не поддерживает, а pysqlite начинает транзакцию лениво - перед первым изменением: чтение шло бы
    вне транзакции, и из двух одновременных записей осталась бы только последняя. Поэтому на SQLite сначала пустое
    изменение этой строки(UPDATE … SET pk = pk): оно открывает транзакцию и берёт блокировку записи БД - параллельный
    запрос ждёт(busy_timeout) commit или rollback первого, а чтение после неё видит уже записанное. Остальные транзакции
    начинаются лениво, как прежде - чтение API не ждёт записи воркера."""
    if db.get_bind().dialect.name == "sqlite":
        pk = inspect(entity).primary_key
        if len(pk) != 1:
            raise TypeError(f"{entity.__name__}: ожидается первичный ключ из одного столбца")
        db.execute(update(pk[0].table).where(pk[0] == key).values({pk[0].name: pk[0]}))
    return db.get(entity, key, with_for_update=True, populate_existing=True)


engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
