import os

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# PostgreSQL по умолчанию (docker compose). Для быстрого запуска без БД: DATABASE_URL=sqlite:///./saq.db
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://saq:saq@localhost:5432/saq")

# SQLite: API и воркер пишут в один файл — ожидание блокировки вместо мгновенного «database is locked», WAL — чтение
# не ждёт записи. Значение читается при каждом новом соединении (тесты могут уменьшить его)
BUSY_TIMEOUT_MS = 30000


def _is_memory(url) -> bool:
    db = url.database
    return not db or db == ":memory:" or url.query.get("mode") == "memory" or db.startswith("file::memory:")


def make_engine(url: str, **kwargs) -> Engine:
    """Engine приложения. SQLite: timeout соединения 30 с, PRAGMA busy_timeout и journal_mode=WAL (кроме :memory:);
    PostgreSQL — как есть."""
    u = make_url(url)
    if u.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True, **kwargs)
    connect_args = {"timeout": 30, **kwargs.pop("connect_args", {})}
    eng = create_engine(url, pool_pre_ping=True, connect_args=connect_args, **kwargs)
    memory = _is_memory(u)

    @event.listens_for(eng, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            if not memory:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")
        finally:
            cur.close()

    return eng


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
