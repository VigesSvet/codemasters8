"""Схема БД и старт приложения: версия схемы в таблице meta(обновление на месте - app.migrate), пустая БД получает
стартовый набор shared/snapshot(app.snapshot - реальные данные мониторинга: ЖК, застройщики, собранная реклама с OCR и
анализом ИИ, объекты из рекламы, журнал), затем при каждом старте - карточки ЖК по открытому реестру
shared/registry_ust_kamenogorsk.json(app.demo.import_registry: идемпотентно, только дополняет пустое).

Демо-набора(вымышленных ЖК) в приложении нет: он - только у тестов(shared/testdata/demo_seed.json,
backend/tests/demo_seed.py), которые подставляют свой старт пустой БД через START. Демо-данные в БД прежних версий
удаляет python -m app.demo purge."""
import logging
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import migrate, models
from .db import Base, SessionLocal, engine as default_engine
from .settings import get_settings

log = logging.getLogger("saq")


def start(db: Session, *, src: Optional[Path] = None, files: bool = True) -> None:
    """Старт API после создания и обновления схемы(в сессии db, без commit). Пустая БД(snapshot.is_empty) - стартовый
    набор src(по умолчанию shared/snapshot; files=False - без файлов изображений); набора нет - только открытый
    реестр, с предупреждением. Затем карточки ЖК по открытому реестру(на свежем наборе - без изменений) и подсказка,
    если в БД остались демо-данные прежних версий."""
    from . import demo, snapshot

    src = Path(src) if src is not None else snapshot.DIR
    if snapshot.is_empty(db):
        if (src / snapshot.MANIFEST).is_file():
            rep = snapshot.load(db, src, files=files)
            log.warning("Пустая БД: загружен %s", rep.line())
        else:
            log.warning("Пустая БД, а стартового набора нет (%s): будут только открытый реестр и карточки ЖК из него, "
                        "аналитиков для входа нет. Верните каталог shared/snapshot (git) и перезапустите API или "
                        "загрузите набор: python -m app.snapshot import --dir <каталог>", src / snapshot.MANIFEST)
    rep = demo.import_registry(db)
    if rep.created or rep.updated:
        log.warning("Открытый реестр: %s", rep.line())
    if db.scalar(select(models.Ad.id).where(models.Ad.origin == "demo").limit(1)) is not None:
        log.warning("В БД остались демо-данные прежних версий (вымышленные ЖК и публикации) — удалите их: "
                    "python -m app.demo purge --dry-run, затем --yes")


# Старт пустой и существующей БД; тесты подставляют свой(демо-набор из shared/testdata - backend/tests/demo_seed.py)
START: Callable[[Session], None] = start


# Таблицы приложения прошлых версий схемы: удаляются при пересоздании вместе с текущими(Base.metadata)
LEGACY_TABLES = {"developers", "complexes", "ads", "audit_events", "appeals", "signals", "meta"}


def _app_tables(eng: Engine) -> set:
    """Таблицы приложения, которые есть в БД; чужие таблицы в той же базе не трогаем."""
    return (set(Base.metadata.tables) | LEGACY_TABLES) & set(inspect(eng).get_table_names())


def _version(eng: Engine, tables: set) -> Optional[str]:
    return _meta(eng, tables, "schema_version")


def _meta(eng: Engine, tables: set, key: str) -> Optional[str]:
    if "meta" not in tables:
        return None
    with SessionLocal(bind=eng) as db:
        try:
            m = db.get(models.Meta, key)
        except SQLAlchemyError:  # meta старой схемы другого вида - значения нет
            return None
        return m.value if m else None


def _drop_app_tables(eng: Engine) -> None:
    """DROP только таблиц приложения: по именам, зависимые раньше родительских(без reflect - старая схема
    может ссылаться на уже удалённые таблицы, а reflect всей БД задел бы чужие таблицы)."""
    present = _app_tables(eng)
    order = sorted(LEGACY_TABLES - set(Base.metadata.tables)) + [t.name for t in reversed(Base.metadata.sorted_tables)]
    with eng.begin() as conn:
        for name in order:
            if name in present:
                Table(name, MetaData()).drop(conn)


def init_db(eng: Engine = default_engine, start: Optional[Callable[[Session], None]] = None) -> None:
    """Нет таблиц приложения → таблицы + старт(START: пустая БД - стартовый набор). Известная прошлая версия схемы →
    обновление на месте с резервной копией(migrate.upgrade), данные сохраняются. Неизвестная версия(или нет meta) →
    таблицы приложения пересоздаются, но только если в базе нет собранных/внесённых данных(migrate.durable_rows); иначе
    SchemaError - пока не задан SAQ_ALLOW_SCHEMA_RESET=1. Посторонние таблицы в той же БД остаются как есть.
    start - свой старт вместо START(загрузка набора из другого каталога - app.snapshot)."""
    from . import demo

    tables = _app_tables(eng)
    flag = None  # флаг "демо удалены" переживает пересоздание таблиц
    if tables:
        ver = _version(eng, tables)
        if ver == models.SCHEMA_VERSION:
            pass
        elif migrate.chain_from(ver) is not None:
            migrate.upgrade(eng, ver)
        else:
            found = migrate.durable_rows(eng, tables)
            if found and not get_settings().allow_schema_reset:
                raise migrate.SchemaError(
                    f"Схема БД неизвестной версии ({ver or 'без версии'}), а в ней есть собранные/внесённые данные "
                    f"({', '.join(f'{k}: {v}' for k, v in found.items())}). Сделайте резервную копию и перенесите данные "
                    "вручную или запустите с SAQ_ALLOW_SCHEMA_RESET=1, чтобы пересоздать таблицы приложения")
            flag = _meta(eng, tables, demo.FLAG)
            log.warning("Схема БД устарела (%s, нужна %s): таблицы приложения пересоздаются — пустая БД получит "
                        "стартовый набор", ver or "без версии", models.SCHEMA_VERSION)
            _drop_app_tables(eng)
    Base.metadata.create_all(eng)
    with SessionLocal(bind=eng) as db:
        db.merge(models.Meta(key="schema_version", value=models.SCHEMA_VERSION))
        if flag:
            db.merge(models.Meta(key=demo.FLAG, value=flag))
        db.flush()
        (start or START)(db)
        db.commit()


def reset(eng: Engine = default_engine) -> None:
    """Таблицы приложения пересоздаются, затем старт пустой БД(тесты). Флаг "демо удалены" тоже удаляется."""
    _drop_app_tables(eng)
    init_db(eng)
