"""Решение аналитика по "Проверке по рекламе"(app.screening): сданные ЖК, которые всё ещё рекламируются, и другие
случаи "Требует проверки", которые аналитик проверил и закрыл.

    POST   /api/complexes/{id}/commissioned          "Объект сдан": акт № (необязателен), дата акта, источник, ссылка
    DELETE /api/complexes/{id}/commissioned          отменить отметку "сдан"(причина обязательна)
    POST   /api/complexes/{id}/screening-decision    {decision: "no_issues"} "Проверено - замечаний нет": что проверено,
                                                     источник, ссылка
    DELETE /api/complexes/{id}/screening-decision    отменить действующее решение любого вида(причина обязательна)
    GET    /api/complexes/{id}/screening-decisions   история решений по ЖК: действующее, отменённые, заменённые

Это действия аналитика(как сверка, app.verification): автор - имя вошедшего сотрудника(HumanName, "Система" - 422),
каждое - событие журнала объекта вида "данные" от его имени. Решение хранится строкой screening_decisions: действующее
(active) - одно на ЖК(частичный уникальный индекс; запись - под блокировкой строки ЖК), новое решение заменяет прежнее
(superseded), отмена - cancelled; строки не удаляются - это история. "Сдан" ставит стадию карточки "введён"(риск: R1,
R2, R3, R5, R6 - "объект введён", как у любого введённого ЖК); отмена или замена возвращает прежнюю стадию(prev_stage).
Как решение влияет на оценку и когда снимается новой рекламой - app.screening. Ответ - ЖК в форме GET
/api/complexes/{id} с пересчитанной оценкой и полем decision.
"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas, screening
from .db import get_db
from .risk_model import BUILT
from .verification import _complex, _event, _fmt, _row, _src_text, _then

router = APIRouter(tags=["decisions"])

Obj = dict[str, Any]
NO_DECISION = "Решения аналитика по этому ЖК нет — оценка и так автоматическая"
NOT_COMMISSIONED = "Действующее решение — «Проверено — замечаний нет», а не отметка «сдан»: отмените его через решение по проверке"
STAGE_NONE = "не указана"
D = models.ScreeningDecision


def _active(db: Session, cid: str) -> models.ScreeningDecision | None:
    return db.scalar(select(D).where(D.complex_id == cid, D.status == "active").order_by(D.id.desc()).limit(1))


def _state(db: Session, cid: str) -> Obj | None:
    """Решение ЖК так, как его видит оценка: действует или снято новой рекламой(reopened)."""
    return screening.compute(db).decisions.get(cid)


def _close(row: models.ScreeningDecision, status: str, who: str, reason: str) -> None:
    row.status, row.closed_by, row.closed_at, row.close_reason = status, who, models.utcnow(), reason


def _label(row: models.ScreeningDecision) -> str:
    return screening.DECISION_LABELS[row.kind]


def _stage_name(stage: str | None) -> str:
    return f"«{stage}»" if stage else STAGE_NONE


def _same(row: models.ScreeningDecision, values: Obj) -> bool:
    return all(getattr(row, k) == v for k, v in values.items())


def _record(db: Session, c: models.Complex, kind: str, values: Obj, who: str, what: str,
            comment: str | None) -> Obj:
    """Новое решение(прежнее действующее - заменено); у "сдан" - стадия "введён", у прочих - возврат стадии, которую
    поставила прежняя отметка "сдан". Повтор действующего(не снятого рекламой) решения без комментария - без записи."""
    cur = _active(db, c.id)
    if cur is not None and cur.kind == kind and not comment and _same(cur, values):
        st = _state(db, c.id)
        if st is not None and st["status"] == "active":
            return _row(db, c)
    notes: list[str] = []
    prev = c.stage
    if cur is not None:
        if cur.kind == "commissioned":
            prev = cur.prev_stage or ""  # стадия до прежней отметки "сдан"
        _close(cur, "superseded", who, f"заменено решением «{screening.DECISION_LABELS[kind]}»")
        notes.append(f"прежнее решение «{_label(cur)}» от {_fmt(screening._local_minutes(cur.created_at))} заменено")
        db.flush()  # прежнее решение закрыто до вставки нового - частичный уникальный индекс "одно действующее"
    row = D(complex_id=c.id, kind=kind, status="active", created_by=who, created_at=models.utcnow(),
            prev_stage=prev if kind == "commissioned" else None, comment=comment, **values)
    db.add(row)
    new_stage = BUILT if kind == "commissioned" else prev
    if new_stage != c.stage:
        notes.append(f"стадия карточки: {_stage_name(c.stage)} → {_stage_name(new_stage)}")
        c.stage = new_stage
    _event(db, who, _then(what + (f"; {'; '.join(notes)}" if notes else ""), comment), c.id)
    db.commit()
    return _row(db, _complex(db, c.id))


@router.post("/api/complexes/{cid}/commissioned", response_model=schemas.ComplexListOut, response_model_by_alias=True)
def mark_commissioned(cid: str, body: schemas.CommissionedIn, db: Session = Depends(get_db)):
    """"Объект сдан": ввод в эксплуатацию подтверждён - акт № (необязателен), дата акта(обязательна, не позже сегодня),
    источник, ссылка, комментарий. Оценка - ok "Дом сдан - замечаний нет"(продажа квартир в сданном доме законна), пока
    новая реклама со стартом продаж или строящейся очередью не снимет решение; стадия карточки - "введён"."""
    c = _complex(db, cid, lock=True)
    act = body.act_date.isoformat()
    values = {"act_number": body.act_number, "act_date": act, "source": body.source, "source_url": body.source_url,
              "reason": None}
    what = (f"Объект сдан — ввод в эксплуатацию подтверждён: {screening.act_text(body.act_number, act)}, "
            f"{_src_text(body.source, body.source_url)}")
    return _record(db, c, "commissioned", values, body.author, what, body.comment)


@router.post("/api/complexes/{cid}/screening-decision", response_model=schemas.ComplexListOut,
             response_model_by_alias=True)
def decide(cid: str, body: schemas.ScreeningDecisionIn, db: Session = Depends(get_db)):
    """"Проверено - замечаний нет": что проверено(не короче 10 символов), источник, ссылка, комментарий. Оценка - ok
    "Проверено - замечаний нет", пока новая реклама с признаком приёма денег не снимет решение."""
    c = _complex(db, cid, lock=True)
    values = {"act_number": None, "act_date": None, "source": body.source, "source_url": body.source_url,
              "reason": body.reason}
    what = (f"Проверка по рекламе: проверено — замечаний нет. Что проверено: {body.reason.rstrip('.')}; "
            f"{_src_text(body.source, body.source_url)}")
    return _record(db, c, body.decision, values, body.author, what, body.comment)


def _cancel(db: Session, cid: str, body: schemas.DecisionCancelIn, only: str | None = None) -> Obj:
    c = _complex(db, cid, lock=True)
    cur = _active(db, c.id)
    if cur is None:
        raise HTTPException(404, NO_DECISION)
    if only is not None and cur.kind != only:
        raise HTTPException(409, NOT_COMMISSIONED)
    st = _state(db, c.id)
    reopened = st is not None and st["status"] == "reopened"
    _close(cur, "cancelled", body.author, body.reason)
    what = (f"Решение аналитика отменено: «{_label(cur)}» от {_fmt(screening._local_minutes(cur.created_at))} "
            f"({cur.created_by}{', уже снято новой рекламой' if reopened else ''}). Причина: {body.reason.rstrip('.')}. "
            "Оценка — автоматическая")
    if cur.kind == "commissioned":
        back = cur.prev_stage or ""
        if back != c.stage:
            what += f"; стадия карточки: {_stage_name(c.stage)} → {_stage_name(back)}"
            c.stage = back
    _event(db, body.author, what, c.id)
    db.commit()
    return _row(db, _complex(db, c.id))


@router.delete("/api/complexes/{cid}/screening-decision", response_model=schemas.ComplexListOut,
               response_model_by_alias=True, responses={404: {"description": NO_DECISION}})
def cancel_decision(cid: str, body: schemas.DecisionCancelIn, db: Session = Depends(get_db)):
    """Отменить действующее решение(любого вида): оценка снова автоматическая; у "сдан" - прежняя стадия карточки."""
    return _cancel(db, cid, body)


@router.delete("/api/complexes/{cid}/commissioned", response_model=schemas.ComplexListOut, response_model_by_alias=True,
               responses={404: {"description": NO_DECISION}, 409: {"description": NOT_COMMISSIONED}})
def cancel_commissioned(cid: str, body: schemas.DecisionCancelIn, db: Session = Depends(get_db)):
    """Отменить отметку "сдан": стадия карточки - прежняя, оценка - автоматическая."""
    return _cancel(db, cid, body, only="commissioned")


@router.get("/api/complexes/{cid}/screening-decisions", response_model=list[schemas.DecisionRecordOut],
            response_model_by_alias=True)
def history(cid: str, db: Session = Depends(get_db)):
    """История решений по ЖК, новые первыми: действующее, заменённые и отменённые(кто, когда, почему)."""
    _complex(db, cid)
    return list(db.scalars(select(D).where(D.complex_id == cid).order_by(D.id.desc())))
