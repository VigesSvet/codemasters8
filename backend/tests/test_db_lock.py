"""Блокировка строки для чтения-изменения-записи(db.get_for_update, complex_or_404(lock=True)) и на SQLite: две
одновременные записи JSON-поля ЖК не теряют друг друга, второй запрос ждёт commit первого."""
import threading

import pytest
from sqlalchemy import select

from app import models
from app.db import SessionLocal, get_for_update
from app.verification import complex_or_404

WAIT = 10


def _complex_id() -> str:
    with SessionLocal() as db:
        return db.scalar(select(models.Complex.id).order_by(models.Complex.id).limit(1))


def _race(cid: str, write) -> list[str]:
    """Сессия A берёт строку под блокировку и дописывает ключ "a", но не фиксирует, пока сессия B не попыталась взять
    ту же строку; B дописывает ключ "b". Возвращает порядок событий."""
    order: list[str] = []
    a_locked, a_go, b_started = threading.Event(), threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def a():
        try:
            with SessionLocal() as db:
                write(db, cid, "a")
                a_locked.set()
                a_go.wait(WAIT)
                order.append("a commit")
                db.commit()
        except BaseException as e:  # noqa: BLE001 - ошибка потока - в основной поток теста
            errors.append(e)
            a_locked.set()

    def b():
        try:
            with SessionLocal() as db:
                b_started.set()
                write(db, cid, "b", lambda: order.append("b read"))
                db.commit()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    ta = threading.Thread(target=a)
    ta.start()
    assert a_locked.wait(WAIT)
    tb = threading.Thread(target=b)
    tb.start()
    assert b_started.wait(WAIT)
    tb.join(0.5)  # B ждёт блокировку A: пока A не зафиксировал, B строку не прочитал
    a_go.set()
    ta.join(WAIT)
    tb.join(WAIT)
    assert not errors, errors
    return order


def _basis(db, cid, key, on_read=None):
    c = complex_or_404(db, cid, lock=True)
    if on_read:
        on_read()
    c.basis = {**(c.basis or {}), f"race_{key}": "yes"}


def test_complex_lock_no_lost_update(client):
    """Две сверки основания одновременно(как POST /api/complexes/{id}/basis): в basis - обе."""
    cid = _complex_id()
    order = _race(cid, _basis)
    assert order == ["a commit", "b read"]
    with SessionLocal() as db:
        basis = db.get(models.Complex, cid).basis
    assert basis["race_a"] == basis["race_b"] == "yes"


def test_lock_released_on_rollback_and_missing_row(client):
    """Блокировка держится до конца транзакции: rollback(сессия закрыта без commit) её снимает; нет строки - None."""
    cid = _complex_id()
    with SessionLocal() as db:
        assert get_for_update(db, models.Complex, "нет-такого-жк") is None
        c = get_for_update(db, models.Complex, cid)
        c.basis = {**(c.basis or {}), "race_lost": "yes"}
    done = threading.Event()

    def other():
        with SessionLocal() as db:
            get_for_update(db, models.Complex, cid)
            db.commit()
        done.set()

    threading.Thread(target=other).start()
    assert done.wait(WAIT)
    with SessionLocal() as db:
        assert "race_lost" not in (db.get(models.Complex, cid).basis or {})


def test_get_for_update_composite_key_rejected(client):
    from sqlalchemy import Column, Integer, MetaData, Table
    from sqlalchemy.orm import registry

    reg = registry(metadata=MetaData())

    class Pair:
        pass

    reg.map_imperatively(Pair, Table("pair", reg.metadata, Column("x", Integer, primary_key=True),
                                     Column("y", Integer, primary_key=True)))
    with SessionLocal() as db:
        if db.get_bind().dialect.name != "sqlite":
            pytest.skip("проверка только для SQLite: на PostgreSQL блокирует SELECT … FOR UPDATE")
        with pytest.raises(TypeError):
            get_for_update(db, Pair, (1, 2))
