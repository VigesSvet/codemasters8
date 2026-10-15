from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

# Меняется при любой правке таблиц: демо-база со старой схемой пересоздаётся при старте (см. seed.init_db).
SCHEMA_VERSION = "2026.10-audit-1"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Meta(Base):
    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))


class Analyst(Base):
    """Демо-аналитик: вход без пароля, имя (name) пишется автором в журнал."""

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
    site: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    instagram: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    complexes: Mapped[list["Complex"]] = relationship(back_populates="developer")


class Complex(Base):
    __tablename__ = "complexes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    developer_id: Mapped[str] = mapped_column(ForeignKey("developers.id"))
    address: Mapped[str] = mapped_column(String(255))
    district: Mapped[str] = mapped_column(String(64))
    lng: Mapped[float] = mapped_column(Float)
    lat: Mapped[float] = mapped_column(Float)
    stage: Mapped[str] = mapped_column(String(32))
    progress: Mapped[int] = mapped_column(Integer)
    floors: Mapped[int] = mapped_column(Integer)
    flats: Mapped[int] = mapped_column(Integer)
    start: Mapped[str] = mapped_column(String(10))
    planned_end: Mapped[str] = mapped_column(String(10))
    # {"guarantee": "yes|no|unknown", "guaranteeRef", "mio", "mioRef", "bvu", "bank", "source", "checkedAt"}
    basis: Mapped[dict] = mapped_column(JSON)
    # {"declared": {"value", "source", "date"} | null, "confirmed": ... | null} — для R5
    readiness: Mapped[dict] = mapped_column(JSON, default=lambda: {"declared": None, "confirmed": None})
    in_published_list: Mapped[str] = mapped_column(String(8), default="unknown")
    cover: Mapped[str] = mapped_column(String(255))
    photos: Mapped[list] = mapped_column(JSON, default=list)
    idle_days: Mapped[int] = mapped_column(Integer, default=0)

    developer: Mapped[Developer] = relationship(back_populates="complexes")


class Ad(Base):
    __tablename__ = "ads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    account: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(512), default="")
    date: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    ocr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    complex_id: Mapped[Optional[str]] = mapped_column(ForeignKey("complexes.id"), nullable=True)
    match_confidence: Mapped[float] = mapped_column(Float, default=0)
    price: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    assignee: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reach: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class Appeal(Base):
    """Обращение граждан по объекту — данные для R4."""

    __tablename__ = "appeals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    complex_id: Mapped[str] = mapped_column(ForeignKey("complexes.id"), index=True)
    date: Mapped[str] = mapped_column(String(32))
    collective: Mapped[bool] = mapped_column(Boolean, default=False)
    applicants: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # verified | pending | rejected
    verified_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    subject: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(255))


class Signal(Base):
    """Сигнальная карточка (модуль 05): снимок риска и доказательств на момент создания + решение аналитика."""

    __tablename__ = "signals"
    # Не больше одного нерассмотренного сигнала по ЖК — защита от гонки двух одновременных «Создать сигнал»
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
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    decision_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[str] = mapped_column(String(64), default="")
    exported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def code(self) -> str:
        return f"S-{self.id:04d}"


class AuditEvent(Base):
    """Журнал: кто, что и когда сделал (ТЗ §7 «Контроль решений») и когда зафиксированы сведения из источников (§4)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads.id"), nullable=True, index=True)
    complex_id: Mapped[Optional[str]] = mapped_column(ForeignKey("complexes.id"), nullable=True, index=True)
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)
    # system — автоматическое, data — сведения из источника, status/link/comment — работа с публикацией,
    # request — запрос сведений по ЖК, signal/export — сигнальная карточка и её выгрузка (schemas.AuditKind)
    kind: Mapped[str] = mapped_column(String(16), default="system", index=True)
    who: Mapped[str] = mapped_column(String(128))
    what: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
