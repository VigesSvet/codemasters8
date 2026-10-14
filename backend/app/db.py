import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# PostgreSQL по умолчанию (docker compose). Для быстрого запуска без БД: DATABASE_URL=sqlite:///./saq.db
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://saq:saq@localhost:5432/saq")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
