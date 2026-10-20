from datetime import datetime, UTC
from typing import Optional
from collections.abc import Iterable

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text,
                        true)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .db import Base

# Версия схемы БД. Меняется только добавлением шага в migrate.STEPS(последний шаг = SCHEMA_VERSION): база известной
# прошлой версии обновляется на месте с резервной копией, неизвестной - пересоздаётся, только если в ней нет собранных и
# внесённых данных(см. seed.init_db, migrate).
SCHEMA_VERSION = "2026.10-alerts-1"

# JSON со слежением за изменениями верхнего уровня(row.state["x"] = 1 сохраняется при commit). Код всё равно
# переприсваивает(row.state = {**row.state, **patch}) и не меняет словари, полученные из другой сессии.
MutableJSONDict = MutableDict.as_mutable(JSON)
MutableJSONList = MutableList.as_mutable(JSON)


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime | None) -> datetime | None:
    """Наивное(SQLite) → UTC-aware; aware с любым смещением → .astimezone(timezone.utc).
    Каждое время, попадающее в SQL(WHERE, UPDATE, INSERT), проходит через as_utc: SQLite отбрасывает пояс связанного
    значения, и 12:00+05:00 сравнивалось бы как 12:00 UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def insert_ignore(db: Session, model, values: dict, conflict_cols: Iterable[str], where=None) -> bool:
    """INSERT … ON CONFLICT DO NOTHING(SQLite и PostgreSQL) - гонка двух вставок без SAVEPOINT(в pysqlite ранний
    SAVEPOINT становится внешней транзакцией). True - строка вставлена, False - такая уже есть(её перечитывают).
    where - условие частичного уникального индекса(index_where). Время приводится к UTC(as_utc)."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - приложение работает только с SQLite и PostgreSQL
        raise NotImplementedError(f"insert_ignore: диалект {dialect} не поддерживается")
    data = {k: as_utc(v) if isinstance(v, datetime) else v for k, v in values.items()}
    stmt = insert(model).values(**data).on_conflict_do_nothing(index_elements=list(conflict_cols), index_where=where)
    # preserve_rowcount: для INSERT SQLAlchemy 2.x иначе не сохраняет rowcount(на psycopg он был бы -1)
    return db.execute(stmt, execution_options={"preserve_rowcount": True}).rowcount == 1


def iso(dt: datetime | None) -> str | None:
    """ISO 8601 с UTC-смещением; наивные значения(SQLite) считаются UTC."""
    if dt is None:
        return None
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.isoformat(timespec="seconds")


class Meta(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class Analyst(Base):
    """Демо-аналитик: вход без пароля, имя(name) пишется автором в журнал."""

    __tablename__ = "analysts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(255))
    short: Mapped[str] = mapped_column(String(8))
    role: Mapped[str] = mapped_column(String(255))


class Developer(Base):
    __tablename__ = "developers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    bin: Mapped[str] = mapped_column(String(12), index=True)
    director: Mapped[str] = mapped_column(String(255))
    registered: Mapped[str] = mapped_column(String(10))
    objects: Mapped[int] = mapped_column(Integer, default=0)
    unfinished: Mapped[int] = mapped_column(Integer, default=0)
    site: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instagram: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phones: Mapped[list] = mapped_column(JSON, default=list)  # телефоны отдела продаж - признак для сопоставления(ИИ)
    # demo - вымышленная организация демо-набора, registry - застройщик из открытого реестра(app.demo): только название и БИН,
    # advertised - застройщик, названный в рекламе ЖК, которого нет в открытом реестре(app.discovery): сведения рекламы
    origin: Mapped[str] = mapped_column(String(16), default="demo")

    complexes: Mapped[list["Complex"]] = relationship(back_populates="developer")


class Complex(Base):
    __tablename__ = "complexes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    developer_id: Mapped[str] = mapped_column(ForeignKey("developers.id"))
    address: Mapped[str] = mapped_column(String(255))
    district: Mapped[str] = mapped_column(String(64))
    # координат может не быть(объект реестра без координат, ЖК по рекламе): None - "нет координат", сверка - 2ГИС
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    stage: Mapped[str] = mapped_column(String(32))
    progress: Mapped[int] = mapped_column(Integer)
    floors: Mapped[int] = mapped_column(Integer)
    flats: Mapped[int] = mapped_column(Integer)
    start: Mapped[str] = mapped_column(String(10))
    planned_end: Mapped[str] = mapped_column(String(10))
    # {"guarantee": "yes|no|unknown", "guaranteeRef", "mio", "mioRef", "bvu", "bank", "source", "checkedAt",
    #  "sources": {документ: {"source", "url", "checkedAt", "checkedBy"?, "ref"?, "comment"?}}} - сверка по документам
    #  (app.verification пишет checkedBy - кто сверил; общие source/checkedAt - последняя сверка)
    basis: Mapped[dict] = mapped_column(JSON)
    # {"declared": {"value", "source", "date", "by"?, "at"?} | null, "confirmed": ... | null} - для R5
    readiness: Mapped[dict] = mapped_column(JSON, default=lambda: {"declared": None, "confirmed": None})
    in_published_list: Mapped[str] = mapped_column(String(8), default="unknown")
    cover: Mapped[str] = mapped_column(String(255))
    # [{"date", "src", "source", "activity", "note"?, …}]; снимок аналитика - ещё "mediaId", "by", "at"(app.verification)
    photos: Mapped[list] = mapped_column(JSON, default=list)
    idle_days: Mapped[int] = mapped_column(Integer, default=0)
    # demo - вымышленный ЖК демо-набора, registry - карточка из открытого реестра(app.demo): сведения источника, не проверены;
    # advertised - ЖК, известный только по рекламе: в открытом реестре его нет, карточку создал аналитик или импорт
    # (app.discovery) - сведения рекламы, не проверены
    origin: Mapped[str] = mapped_column(String(16), default="demo")
    # Сверка аналитиком(app.verification): перечень ГАСК(O1) - {"value", "source", "url", "checkedAt", "checkedBy",
    # "comment"?}; "обращений нет - сверено"(R4 карточек реестра и рекламы) - {"source", "checkedAt", "checkedBy",
    # "comment"?}. None - не сверялось
    published_list_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    appeals_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    developer: Mapped[Developer] = relationship(back_populates="complexes")


class Ad(Base):
    __tablename__ = "ads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    account: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(512), default="")
    date: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    ocr: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    complex_id: Mapped[str | None] = mapped_column(ForeignKey("complexes.id"), nullable=True)
    # уверенность ИИ в привязке к ЖК(информативно); фактом привязку делает подтверждение аналитика(link_confirmed_*)
    match_confidence: Mapped[float] = mapped_column(Float, default=0)
    link_confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    link_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_links: Mapped[list] = mapped_column(JSON, default=list)  # ЖК, гипотезы о которых аналитик отклонил
    # решение аналитика по условиям оплаты, найденным ИИ: accepted | rejected("это не предложение внести деньги" - вето R2)
    terms_review: Mapped[str | None] = mapped_column(String(16), nullable=True)
    terms_review_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    terms_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # номер версии решений аналитика по публикации(привязка, гипотезы ИИ, условия оплаты): решение пишется условным UPDATE
    # по прочитанному номеру - два одновременных решения не смешиваются в одно противоречивое состояние(main._decide)
    decision_rev: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # demo - из демо-набора, analyst - добавил аналитик, collected - собрано сборщиком или прислано через бота
    origin: Mapped[str] = mapped_column(String(16), default="demo")
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # время получения(UTC)

    # Файлы публикации(без FK - новые таблицы не ссылаются на старые, см. migrate): только загруженные изображения и
    # видео текущей версии, по порядку в публикации
    media: Mapped[list["MediaAsset"]] = relationship(
        primaryjoin="and_(foreign(MediaAsset.ad_id) == Ad.id, MediaAsset.status == 'downloaded', "
                    "MediaAsset.current == True, MediaAsset.kind.in_(('image', 'video')))",
        order_by="MediaAsset.position", lazy="selectin", viewonly=True)
    # Запись приёма(собранная публикация): версия содержимого и состояние анализа
    ingest: Mapped[Optional["IngestItem"]] = relationship(
        primaryjoin="foreign(IngestItem.ad_id) == Ad.id", uselist=False, lazy="selectin", viewonly=True)
    # Анализы ИИ(Claude) публикации: последний - по времени последнего использования результата
    llm_analyses: Mapped[list["LlmAnalysis"]] = relationship(
        primaryjoin="foreign(LlmAnalysis.ad_id) == Ad.id",
        order_by="(LlmAnalysis.updated_at.desc(), LlmAnalysis.id.desc())", lazy="selectin", viewonly=True)

    @property
    def processing(self) -> dict | None:
        """Состояние анализа собранной публикации(AdOut.processing); None - публикация не из приёма."""
        item = self.ingest
        return item.processing_view() if item is not None else None

    @property
    def llm(self) -> dict | None:
        """Последний анализ ИИ - гипотезы для аналитика(AdOut.llm); None - анализа не было."""
        rows = self.llm_analyses
        return rows[0].view() if rows else None


class Appeal(Base):
    """Обращение граждан по объекту - данные для R4."""

    __tablename__ = "appeals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    complex_id: Mapped[str] = mapped_column(ForeignKey("complexes.id"), index=True)
    date: Mapped[str] = mapped_column(String(32))
    collective: Mapped[bool] = mapped_column(Boolean, default=False)
    applicants: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # verified | pending | rejected
    verified_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(16), default="demo")
    # Внесено и проверено аналитиком(app.verification): кто и когда внёс, комментарий; кто решил и с каким основанием
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class Signal(Base):
    """Сигнальная карточка(модуль 05): снимок риска и доказательств на момент создания + решение аналитика."""

    __tablename__ = "signals"
    # Не больше одного нерассмотренного сигнала по ЖК - защита от гонки двух одновременных "Создать сигнал"
    __table_args__ = (Index("uq_signals_open_complex", "complex_id", unique=True,
                            sqlite_where=text("status = 'new'"), postgresql_where=text("status = 'new'")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complex_id: Mapped[str] = mapped_column(ForeignKey("complexes.id"), index=True)
    complex_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new | confirmed | returned
    score: Mapped[int] = mapped_column(Integer)
    zone: Mapped[str] = mapped_column(String(8))
    needs_check: Mapped[bool] = mapped_column(Boolean, default=False)
    hits: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text)
    factors: Mapped[list] = mapped_column(JSON, default=list)
    observations: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(128))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str] = mapped_column(String(64), default="")
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def code(self) -> str:
        return f"S-{self.id:04d}"


class AuditEvent(Base):
    """Журнал: кто, что и когда сделал(ТЗ §7 "Контроль решений") и когда зафиксированы сведения из источников(§4)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id"), nullable=True, index=True)
    complex_id: Mapped[str | None] = mapped_column(ForeignKey("complexes.id"), nullable=True, index=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)
    # system - автоматическое, data - сведения из источника, status/link/comment - работа с публикацией,
    # request - запрос сведений по ЖК, signal/export - сигнальная карточка и её выгрузка, ai - гипотеза ИИ и решения
    # аналитика по ней, alert - предупреждение жителям: подготовка, согласование, итог отправки(schemas.AuditKind)
    kind: Mapped[str] = mapped_column(String(16), default="system", index=True)
    who: Mapped[str] = mapped_column(String(128))
    what: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SourceRecord(Base):
    """Запись открытого источника(T-26): нормализованные поля строки файла и провенанс - источник, раздел, ссылка, ID
    в источнике, время сбора, дата загрузки у поставщика, когда и из какого файла загружена. Это сведения источника, а не
    выводы системы: риск, зоны и сигналы для них не рассчитываются(T-31). Объект - группа записей по ключу(key)."""

    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("source", "source_record_id", name="uq_source_records_source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column("object_key", String(96), index=True)
    origin: Mapped[str] = mapped_column(String(16), default="source")
    # провенанс
    source: Mapped[str] = mapped_column(String(32), index=True)  # homeportal | homeportal_section | ashyq
    source_name: Mapped[str] = mapped_column(Text)  # "Источник" как в файле
    source_section: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_record_id: Mapped[str] = mapped_column(String(64))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_loaded_at: Mapped[str | None] = mapped_column(String(32), nullable=True)  # время поставщика, без пояса
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    import_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # поля строки
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    developer: Mapped[str | None] = mapped_column(Text, nullable=True)
    developer_bin: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    agent: Mapped[str | None] = mapped_column(Text, nullable=True)  # уполномоченная компания
    agent_bin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    funding_method: Mapped[str | None] = mapped_column(String(16), nullable=True)  # guarantee | mio | bvu
    funding_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    start: Mapped[str | None] = mapped_column(String(10), nullable=True)
    planned_end: Mapped[str | None] = mapped_column(String(10), nullable=True)
    act_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    act_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    readiness: Mapped[float | None] = mapped_column(Float, nullable=True)
    report_period: Mapped[str | None] = mapped_column(Text, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    cadastral: Mapped[str | None] = mapped_column(Text, nullable=True)
    site: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------- сбор публикаций(подключения, запуски, приём, файлы, задания воркера) ----------
# Новые таблицы не ссылаются на старые(ads, complexes, …) внешними ключами: публикация - по индексированному ads.id
# (String(32)), целостность - в сервисном слое. Так старые пути пересоздания схемы продолжают работать.

class Collector(Base):
    """Подключение(план: Source): одна строка на код подключения, создаётся лениво. Только ссылки на секреты, не они."""

    __tablename__ = "collectors"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # сбор по расписанию
    # {accounts, channels, profiles, searchUrls, cardUrls, resultsPerPage, downloadVideos}
    config: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None - период подключения по умолчанию
    status: Mapped[str] = mapped_column(String(24), default="unchecked")  # хранимый статус(integration-spec §10.4)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # несекретные сведения: account, expiresAt, scopes, keyFp, partial, stopped, stoppedCode, …
    facts: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    # курсоры и отчёты: каналы TG, IG seeding, кэш robots Krisha, report(от воркера), rejects
    state: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # подряд неудачных запусков
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)  # задано - строка невосстановима
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CollectionRun(Base):
    """Запуск сбора или проверки подключения("запуск №N"). Счётчики - атомарным UPDATE … SET x = x + :n."""

    __tablename__ = "collection_runs"
    __table_args__ = (Index("ix_collection_runs_collector_created", "collector", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    collector: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual | test
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # integration-spec §10.5
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)  # администратор или "Система"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items_seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_new: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_unchanged: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    items_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    media_saved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    media_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    external_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Run ID Apify
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # not_configured | auth_expired | access_denied | access_check | rate_limited | bad_config | source_error | internal
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # по-русски, замаскировано, ≤ 500 символов
    notes: Mapped[list] = mapped_column(MutableJSONList, default=list)  # [{at, level: info|warn|error, text}], ≤ 100


class IngestItem(Base):
    """Принятая публикация(план: IngestItem): внешний ID в пространстве площадки, владелец-подключение, хеш и версия
    содержимого, снимок ответа источника(без секретов) и состояние анализа(план: AnalysisJob)."""

    __tablename__ = "ingest_items"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_ingest_items_platform_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(16))  # пространство дедупликации: площадка; "tgbot" - заявки бота
    # TG "{channel_id}:{first_msg_id}"; IG - ID медиа; TikTok - ID поста; Krisha - ID объявления; бот - ID заявки
    external_id: Mapped[str] = mapped_column(String(128))
    collector: Mapped[str] = mapped_column(String(32), index=True)  # подключение-владелец; "intake" - приём через API
    ad_id: Mapped[str] = mapped_column(String(32), index=True)  # публикация(без FK)
    account: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # время источника
    first_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)  # последний раз видели
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    raw: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)  # очищенный снимок, ≤ 64 КБ
    external_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analyzed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analyzed_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # queued | running | done | failed
    # media | frames | speech | ocr | rules | llm | events - пока идёт; после сбоя - этап, на котором он случился
    analysis_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # замаскировано

    def processing_view(self) -> dict | None:
        """Состояние анализа для карточки публикации(AdOut.processing)."""
        if self.analysis_status not in ("queued", "running", "done", "failed"):
            return None
        return {"status": self.analysis_status, "version": self.version, "analyzed_version": self.analyzed_version,
                "stage": self.analysis_stage, "error": self.analysis_error}


class IngestVersion(Base):
    """Версия содержимого принятой публикации: что было в источнике при каждом изменении."""

    __tablename__ = "ingest_versions"
    __table_args__ = (UniqueConstraint("item_id", "version", name="uq_ingest_versions_item_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("ingest_items.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    account: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    media_keys: Mapped[list] = mapped_column(MutableJSONList, default=list)
    raw: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class MediaAsset(Base):
    """Файл публикации(план: MediaAsset): изображение, видео или кадр видео. Байты - в хранилище по SHA-256
    (media.py), здесь - ключ хранилища, MIME по сигнатуре, размер, хеш, источник загрузки и состояние."""

    __tablename__ = "media_assets"
    __table_args__ = (UniqueConstraint("item_id", "source_key", name="uq_media_assets_item_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex; адрес /api/media/{id}
    ad_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # без FK; None - не привязан
    item_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_items.id"), nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"), nullable=True)  # кадр → видео
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # порядок в публикации
    kind: Mapped[str] = mapped_column(String(16))  # image | video | frame
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)  # cover | slide | attachment
    # стабильный ключ источника: ig:{id}, tg:{ch}:{msg}:{file}, apify:{post}:video, krisha:{id}:{n}, …
    source_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())  # ключ есть в текущей версии
    origin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # без query, userinfo и токена бота
    # apify-kv:{store}/{key}, telegram:{ch}/{msg}, upload, intake, bot
    origin_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|downloaded|failed|unavailable|skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # по-русски, замаскировано
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    mime: Mapped[str | None] = mapped_column(String(64), nullable=True)  # по сигнатуре файла
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)  # ab/cd/<sha256>.<ext>
    frame_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # width/height/duration; src - адрес ожидающей загрузки из приёма(не выводится и не пишется в лог); retryAt;
    # speech - расшифровка речи видео(app.speech: status, text, engine, model, language, audioS, durationMs, detail)
    meta: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    ocr_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # done|empty|unavailable|failed|skipped
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ocr_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def url(self) -> str:
        return f"/api/media/{self.id}"

    @property
    def speech(self) -> dict | None:
        """Расшифровка речи видео(meta.speech, app.speech) для MediaRefOut; у изображений и кадров - None."""
        sp = (self.meta or {}).get("speech") if self.kind == "video" else None
        return sp if isinstance(sp, dict) and sp.get("status") else None


ACTIVE_JOB = "status IN ('queued', 'running')"


class Job(Base):
    """Задание воркера: collect(сбор), test(проверка подключения), analyze(анализ публикации, план: AnalysisJob).
    Не больше одного активного задания на dedupe_key(частичный уникальный индекс)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_after", "status", "run_after"),
                      Index("uq_jobs_active_dedupe", "dedupe_key", unique=True, sqlite_where=text(ACTIVE_JOB),
                            postgresql_where=text(ACTIVE_JOB)))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # collect | test | analyze
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed | cancelled
    # test 10, collect 5, analyze 0(повторный анализ ИИ 1, дозапрос Claude −1 - app.llm)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    collector: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("collection_runs.id"), nullable=True, index=True)
    ad_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    content_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # analyze: версия при постановке
    # collect:{code}, test:{code}, analyze:{ad_id}; у analyze обнуляется при захвате(новая версия ставит новое задание)
    dedupe_key: Mapped[str | None] = mapped_column(String(96), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # +1 при захвате
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # продлевает heartbeat
    # фазы: {"phase": "poll", "apifyRunId": …, "deadline": …, "deferrals": n}; analyze: {"stage": …}
    payload: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    result: Mapped[dict | None] = mapped_column(MutableJSONDict, nullable=True)  # итог; analyze: {"failedStage": …}
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)  # замаскировано
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkerLease(Base):
    """"scheduler" - лидерство планировщика; "worker:{id}" - пульс воркера; "dgis-check:{hash}" - короткая блокировка
    сверки адреса 2ГИС."""

    __tablename__ = "worker_leases"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    info: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)  # {"id", "pid", "version", "jobId"}


class OAuthState(Base):
    """Одноразовый state OAuth(OLX): хранится только SHA-256, привязан к отпечатку токена администратора."""

    __tablename__ = "oauth_states"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(128))
    admin_fp: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationToken(Base):
    """Токены OAuth-подключения(OLX). Хранятся без шифрования(см. документацию); ни одна схема *Out их не выводит."""

    __tablename__ = "integration_tokens"

    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)  # без e-mail и телефона
    obtained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rev: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # атомарное обновление токена


class GeoCheck(Base):
    """Проверка адреса через геосервис(2ГИС): кандидаты(временный кэш) и решение аналитика. Сведения открытых
    данных не меняются; координаты ЖК - только явным выбором с записью "было → стало" в журнал."""

    __tablename__ = "geo_checks"
    __table_args__ = (Index("ix_geo_checks_target", "target_kind", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_kind: Mapped[str] = mapped_column(String(16))  # complex | registry | discovered(объект из рекламы)
    target_id: Mapped[str] = mapped_column(String(96))
    provider: Mapped[str] = mapped_column(String(16), default="2gis")
    query: Mapped[str] = mapped_column(Text)
    query_norm: Mapped[str] = mapped_column(String(255), index=True)
    candidates: Mapped[list] = mapped_column(MutableJSONList, default=list)
    status: Mapped[str] = mapped_column(String(16))  # candidates | not_found | accepted | rejected
    chosen_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chosen: Mapped[dict | None] = mapped_column(MutableJSONDict, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    prev_point: Mapped[dict | None] = mapped_column(MutableJSONDict, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # кэш удалён
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class BotSubmission(Base):
    """Заявка жителя через Telegram-бота: сведения о заявителе хранятся отдельно от публичного содержимого."""

    __tablename__ = "bot_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_items.id"), nullable=True)
    ad_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    applicant: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)  # telegramUserId, chatId, username, name, phone
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LlmAnalysis(Base):
    """Анализ публикации языковой моделью(Claude, этап "llm" задания анализа, app.llm). Одна строка на(публикация,
    хеш отправленного материала, модель, версия промпта): тот же материал повторно не отправляется. Результат -
    проверенные гипотезы(цитаты сверены с отправленным текстом, ЖК - из отправленного списка); решений аналитика
    (статус, привязка, подтверждение, вердикт) он не содержит и не задаёт.

    status: done - результат есть; refused - модель отказалась(это не "риск отсутствует"); failed - не выполнен
    (retry_at задан - ждёт повтора); skipped - не отправлялся(бюджет, ключ отклонён, нечего анализировать).

    История: после "Проанализировать ещё раз" прежний ответ остаётся строкой с производным content_hash
    (llm.archived_hash), исходный хеш материала - в meta.materialHash, время замены - meta.supersededAt."""

    __tablename__ = "llm_analyses"
    __table_args__ = (UniqueConstraint("ad_id", "content_hash", "model", "prompt_version", name="uq_llm_analyses_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[str] = mapped_column(String(32), index=True)  # публикация(без FK)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # запись приёма(без FK)
    content_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # версия публикации в источнике
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 отправленного материала
    model: Mapped[str] = mapped_column(String(64))  # запрошенная модель
    served_model: Mapped[str | None] = mapped_column(String(64), nullable=True)  # ответила(резервная - при отказе)
    prompt_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # done | refused | failed | skipped
    result: Mapped[dict | None] = mapped_column(MutableJSONDict, nullable=True)
    dropped: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)  # отброшено при проверке: {причина: n}
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # по-русски, замаскировано
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # request-id Anthropic(в API нет)
    # stopReason, refusalCategory, fallback, effort, catalogSize, skipCode; у строки истории - materialHash, supersededAt
    meta: Mapped[dict] = mapped_column(MutableJSONDict, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def view(self) -> dict:
        """AdOut.llm: результат как гипотеза, без request-id и без сырых ответов модели."""
        res = dict(self.result or {})
        meta = dict(self.meta or {})
        return {"status": self.status, "provider": "anthropic", "model": self.model, "served_model": self.served_model,
                "prompt_version": self.prompt_version, "content_version": self.content_version,
                "sale_signals": list(res.get("saleSignals") or []), "amounts": list(res.get("amounts") or []),
                "complex_candidates": list(res.get("complexCandidates") or []),
                "mentioned_complexes": list(res.get("mentionedComplexes") or []),
                "object_stage": res.get("objectStage") or None,
                "legal_refs": list(res.get("legalRefs") or []),
                "uncertainties": list(res.get("uncertainties") or []), "summary": res.get("summary"),
                "dropped": dict(self.dropped or {}), "error": self.error, "retry_at": self.retry_at,
                "attempts": self.attempts or 0, "refusal_category": meta.get("refusalCategory"),
                "fallback": bool(meta.get("fallback")), "input_tokens": self.input_tokens or 0,
                "output_tokens": self.output_tokens or 0, "cost_usd": round(float(self.cost_usd or 0), 6),
                "created_at": self.created_at, "updated_at": self.updated_at}


class QuotaCounter(Base):
    """Атомарные счётчики расхода квот(dgis:{keyFp}:{periodStart|all}, dgis-day:{keyFp}:{YYYY-MM-DD}) и дневного
    бюджета анализа ИИ(anthropic-usd:{YYYY-MM-DD} - в микродолларах, день по Усть-Каменогорску)."""

    __tablename__ = "quota_counters"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    limit_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiscoveredObject(Base):
    """Объект из рекламы(app.discovery): ЖК, проект или очередь, которые реклама называет, а в справочнике ЖК и в открытом
    реестре их нет. Появляется по ответу Claude(mentionedComplexes) или импортом; решение - за аналитиком: создать
    карточку ЖК(origin "advertised"), привязать к существующему ЖК(название - его другое написание) или отклонить.

    norm_key - ключ сравнения названия(без "ЖК", кавычек и регистра, латиница ≈ кириллица: discovery.name_key).
    status: new - ждёт решения; linked - привязан к ЖК complex_id(его упоминания - реклама этого ЖК); dismissed -
    отклонён(note - причина). Счётчики(ads_count, accounts, first_seen, last_seen) пересчитываются по упоминаниям."""

    __tablename__ = "discovered_objects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    norm_key: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(MutableJSONList, default=list)  # другие написания из рекламы и импорта
    address_hint: Mapped[str | None] = mapped_column(Text, nullable=True)  # адрес дословно из рекламы
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new | linked | dismissed
    complex_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # ЖК(без FK)
    origin: Mapped[str] = mapped_column(String(16), default="ads", server_default="ads")  # ads(Claude) | import
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ads_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    accounts: Mapped[list] = mapped_column(MutableJSONList, default=list)  # аккаунты рекламы(без заявок жителей)
    source_urls: Mapped[list] = mapped_column(MutableJSONList, default=list)  # ссылки на рекламу из импорта
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # причина отклонения или примечание импорта
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiscoveredMention(Base):
    """Упоминание объекта из рекламы в публикации: дословная цитата и адрес из проверенного ответа Claude(одна строка на
    объект и публикацию - повторный анализ её обновляет), источник публикации(parser | bot | manual) и стадия объекта
    по этой публикации(objectStage)."""

    __tablename__ = "discovered_mentions"
    __table_args__ = (UniqueConstraint("object_id", "ad_id", name="uq_discovered_mentions_object_ad"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("discovered_objects.id"), index=True)
    ad_id: Mapped[str] = mapped_column(String(32), index=True)  # публикация(без FK)
    name: Mapped[str] = mapped_column(String(255), default="")  # название, как его записал ответ модели
    quote: Mapped[str] = mapped_column(Text, default="")
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="parser")
    stage: Mapped[str | None] = mapped_column(String(24), nullable=True)  # under_construction | completed | unknown
    stage_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScreeningDecision(Base):
    """Решение аналитика по "Проверке по рекламе"(app.decisions): "Объект сдан" - ввод в эксплуатацию подтверждён(акт,
    дата акта, источник) - или "Проверено - замечаний нет"(причина, источник). Пока решение действует, оценка ЖК -
    "без замечаний" с этим основанием(app.screening); новая реклама после решения с признаком приёма денег снимает его
    автоматически(это вычисляется при чтении и не хранится - reopened в ответе API).

    status: active - действует(не больше одного на ЖК: частичный уникальный индекс); cancelled - отменено аналитиком
    (closed_* - кто, когда, причина); superseded - заменено новым решением. Отменённые и заменённые остаются историей.
    prev_stage - стадия карточки до отметки "сдан"(отметка ставит "введён"; отмена и замена её возвращают)."""

    __tablename__ = "screening_decisions"
    __table_args__ = (Index("uq_screening_decisions_active_complex", "complex_id", unique=True,
                            sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complex_id: Mapped[str] = mapped_column(String(32), index=True)  # ЖК(без FK - новые таблицы не ссылаются на старые)
    kind: Mapped[str] = mapped_column(String(16))  # commissioned | no_issues
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | cancelled | superseded
    act_number: Mapped[str | None] = mapped_column(String(128), nullable=True)  # номер акта ввода(необязателен)
    act_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # дата акта, YYYY-MM-DD
    source: Mapped[str] = mapped_column(String(255))  # где подтверждено: ЕГКН, акт приёмки, 2ГИС, ответ акимата / ГАСК…
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # "проверено - замечаний нет": что проверено
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    prev_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ResidentAlert(Base):
    """Предупреждение жителям(app.alerts, страница "Жителям"): текст по данным ЖК готовит аналитик, согласует другой
    сотрудник в режиме администратора, отправляет воркер - в канал ГАСК и подписчикам района в Telegram-боте.

    status: pending - на согласовании(не больше одного на ЖК: частичный уникальный индекс); rejected - отклонено;
    sending - согласовано, идёт отправка; sent - доставлено всем; partial - части получателей не доставлено; failed - не
    доставлено никому(last_error - почему). Получатели фиксируются при согласовании(alert_deliveries)."""

    __tablename__ = "resident_alerts"
    __table_args__ = (Index("uq_resident_alerts_pending_complex", "complex_id", unique=True,
                            sqlite_where=text("status = 'pending'"), postgresql_where=text("status = 'pending'")),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    complex_id: Mapped[str] = mapped_column(String(32), index=True)  # ЖК(без FK - новые таблицы не ссылаются на старые)
    complex_name: Mapped[str] = mapped_column(String(255))
    district: Mapped[str] = mapped_column(String(64), default="")  # район ЖК на момент создания: кому из подписчиков
    template: Mapped[str] = mapped_column(String(16))  # basis | idle | ok - шаблон, с которого начат текст
    title: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)  # согласовал или отклонил
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # отправка завершена
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def code(self) -> str:
        return f"A-{self.id:04d}"


class AlertDelivery(Base):
    """Доставка предупреждения одному получателю: канал ГАСК(kind = channel, target - @имя или ID канала) или подписчик
    (kind = subscriber, target - chat_id; после итога стирается - остаётся только счётчик).

    status: pending - ждёт отправки; sending - отправляется(осталось после сбоя воркера - результат неизвестен,
    повторно не отправляется); sent; failed(error - почему); skipped - житель отписался до отправки."""

    __tablename__ = "alert_deliveries"
    __table_args__ = (Index("ix_alert_deliveries_alert_status", "alert_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("resident_alerts.id"))
    kind: Mapped[str] = mapped_column(String(16))  # channel | subscriber
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # сообщение в Telegram(у канала - ссылка)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlertSubscription(Base):
    """Подписка жителя на предупреждения района - её регистрирует бот заявок(saq-telegram-bot) по сервисному токену.
    Только chat_id и район ЖК(district = "*" - все районы): ни имени, ни username, ни телефона."""

    __tablename__ = "alert_subscriptions"
    __table_args__ = (UniqueConstraint("chat_id", "district", name="uq_alert_subscriptions_chat_district"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(32), index=True)
    district: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
