from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


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
    # {"guarantee": "yes|no|unknown", "guaranteeRef": ..., "mio": ..., "bvu": ..., "bank": ...}
    basis: Mapped[dict] = mapped_column(JSON)
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


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[Optional[str]] = mapped_column(ForeignKey("ads.id"), nullable=True, index=True)
    complex_id: Mapped[Optional[str]] = mapped_column(ForeignKey("complexes.id"), nullable=True)
    who: Mapped[str] = mapped_column(String(128))
    what: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
