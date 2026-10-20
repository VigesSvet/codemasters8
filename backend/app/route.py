"""Маршрут рассмотрения сигнала и сроки этапов(T-79).

Этапы: "Разбор аналитиком"(с создания или с возврата руководителем) → "На согласовании у руководителя"(аналитик
передал) → "Согласовать"(сигнал подтверждается той же логикой, что POST /confirm) или "Вернуть аналитику" с комментарием
(снова разбор, срок - заново). Прежние POST /confirm и /return работают как раньше и закрывают маршрут из любого этапа.

Без новых таблиц и колонок: текущий этап выводится из журнала сигнала(audit_events, kind = signal, тексты - префиксы
ниже) и статуса сигнала(confirmed / returned - маршрут закрыт). Сроки - рабочие дни(пн-пт, по времени Алматы, без
праздников): срок этапа - конец N-го рабочего дня после дня его начала(SLA). Согласует в пилоте роль "Ведущий аналитик"
(an2): учётная запись ищется по имени автора, роль - из таблицы analysts; сервер имя автора не проверяет(вход пилотный).
"""
from datetime import date, datetime, time, timedelta, UTC
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import models, schemas, signals
from .db import get_db
from .risk_model import ALMATY
from .signals import SignalId
from .world import iso

router = APIRouter(prefix="/api/signals", tags=["signals"])

Obj = dict[str, Any]

# ---------- константы маршрута(единственное место) ----------

SLA_DAYS: dict[str, int] = {"analysis": 2, "approval": 1}  # рабочих дней на этап
STAGE_LABELS: dict[str, str] = {
    "analysis": "Разбор аналитиком",
    "approval": "На согласовании у руководителя",
    "confirmed": "Подтверждён",
    "returned": "Возвращён на доработку",
}
APPROVER_ROLE = "Ведущий аналитик"  # пилот: роль an2 "Ведущий аналитик ДЭР" выступает руководителем
APPROVER_TEXT = "руководитель (в пилоте — Ведущий аналитик ДЭР)"
SUBMIT_WHAT = "Маршрут: передан на согласование руководителю"
BACK_WHAT = "Маршрут: возвращён аналитику"
APPROVE_WHAT = "Маршрут: согласовано руководителем, сигнал подтверждён"
APPROVE_DEFAULT = "Согласовано без замечаний"
COMMENT_SEP = ". Комментарий: "

Action = Literal["submit", "approve", "return"]


def clock() -> datetime:
    """Текущее время маршрута(UTC); тесты подменяют его, чтобы проверить сроки и просрочку."""
    return models.utcnow()


# ---------- сроки ----------

def due_at(start: datetime, days: int) -> datetime:
    """Конец(23:59:59 по Алматы) N-го рабочего дня после дня start; день начала не считается. Пн-Пт, без праздников."""
    d: date = models.as_utc(start).astimezone(ALMATY).date()
    n = 0
    while n < days:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return datetime.combine(d, time(23, 59, 59), ALMATY).astimezone(UTC)


def _dt(x: Any) -> datetime:
    return models.as_utc(x) if isinstance(x, datetime) else datetime.fromisoformat(str(x))


# ---------- этапы из журнала ----------

def _comment(what: str) -> str | None:
    return what.split(COMMENT_SEP, 1)[1] if COMMENT_SEP in what else None


def _what(prefix: str, comment: str | None) -> str:
    return prefix + (f"{COMMENT_SEP}{comment}" if comment else "")


def _stage(stage: str, at: datetime, who: str, comment: str | None = None) -> Obj:
    days = SLA_DAYS.get(stage)
    return {"stage": stage, "label": STAGE_LABELS[stage], "startedAt": at, "startedBy": who, "comment": comment,
            "slaDays": days, "dueAt": due_at(at, days) if days else None, "endedAt": None, "endedBy": None,
            "outcome": None, "outcomeComment": None, "overdue": False}


def build(s: models.Signal, events: list[Obj], now: datetime | None = None) -> Obj:
    """Этапы маршрута сигнала по его журналу(по возрастанию времени) и статусу; now - для просрочки текущего этапа."""
    now = models.as_utc(now) if now else clock()
    hist = [_stage("analysis", _dt(s.created_at), s.created_by)]

    def close(at: datetime, who: str, outcome: str, comment: str | None) -> None:
        cur = hist[-1]
        cur.update(endedAt=at, endedBy=who, outcome=outcome, outcomeComment=comment)

    for e in events:
        what = e.get("what") or ""
        if e.get("kind") != "signal":
            continue
        at = _dt(e["at"])
        if what.startswith(SUBMIT_WHAT) and hist[-1]["stage"] == "analysis":
            close(at, e["who"], "submitted", _comment(what))
            hist.append(_stage("approval", at, e["who"], _comment(what)))
        elif what.startswith(BACK_WHAT) and hist[-1]["stage"] == "approval":
            close(at, e["who"], "returned_to_analyst", _comment(what))
            hist.append(_stage("analysis", at, e["who"], _comment(what)))
    if s.status in ("confirmed", "returned"):
        at = _dt(s.decided_at) if s.decided_at else hist[-1]["startedAt"]
        who = s.decided_by or ""
        close(at, who, s.status, s.decision_comment)
        hist.append(_stage(s.status, at, who, s.decision_comment))
        hist[-1].update(endedAt=at)
    for h in hist:  # этап просрочен: закрыт позже срока или открыт и срок прошёл
        if h["dueAt"] is not None:
            h["overdue"] = (h["endedAt"] or now) > h["dueAt"]
    cur = hist[-1]
    open_ = s.status == "new"
    out = {
        "signalId": s.id, "code": s.code, "status": s.status, "stage": cur["stage"], "stageLabel": cur["label"],
        "since": iso(cur["startedAt"]), "dueAt": iso(cur["dueAt"]) if open_ else None,
        "slaDays": cur["slaDays"] if open_ else None, "overdue": bool(open_ and cur["overdue"]),
        "now": iso(now),
        "actions": (["submit"] if cur["stage"] == "analysis" else ["approve", "return"]) if open_ else [],
        "approver": APPROVER_TEXT, "sla": dict(SLA_DAYS),
        "stages": [{**h, "startedAt": iso(h["startedAt"]), "endedAt": iso(h["endedAt"]), "dueAt": iso(h["dueAt"])}
                   for h in hist],
    }
    return out


def summary(full: Obj) -> Obj:
    return {k: full[k] for k in ("signalId", "code", "status", "stage", "stageLabel", "since", "dueAt", "slaDays",
                                 "overdue")}


def is_approver(db: Session, author: str) -> bool:
    """Согласует только учётная запись с ролью "Ведущий аналитик…"(пилот, an2); неизвестное имя - нет."""
    a = db.scalar(select(models.Analyst).where(models.Analyst.name == author).limit(1))
    return bool(a and a.role.startswith(APPROVER_ROLE))


def route_of(db: Session, s: models.Signal, now: datetime | None = None) -> Obj:
    return build(s, signals.journal(db, s.id), now)


# ---------- эндпоинты ----------

class RouteActionIn(schemas.Camel):
    action: Action
    author: schemas.HumanName
    comment: schemas.OptNote = None

    @model_validator(mode="after")
    def _return_needs_comment(self):
        if self.action == "return" and not self.comment:
            raise ValueError("для возврата аналитику нужен комментарий: что доработать")
        return self


@router.get("/routes")
def list_routes(db: Session = Depends(get_db)):
    """Маршрут всех сигналов кратко(этап, срок, просрочка) - для колонки "Срок" реестра и панели статистики."""
    sigs = list(db.scalars(select(models.Signal).order_by(models.Signal.id.desc())))
    E = models.AuditEvent
    by: dict[int, list[Obj]] = {}
    for e in db.scalars(select(E).where(E.signal_id.is_not(None), E.kind == "signal").order_by(E.at, E.id)):
        by.setdefault(e.signal_id, []).append({"at": e.at, "who": e.who, "what": e.what, "kind": e.kind})
    now = clock()
    return [summary(build(s, by.get(s.id, []), now)) for s in sigs]


@router.get("/{sid}/route")
def get_route(sid: SignalId, db: Session = Depends(get_db)):
    return route_of(db, signals._get(db, sid))


@router.post("/{sid}/route", responses={403: {"description": "Согласует только руководитель"},
                                         409: {"description": "Переход невозможен на текущем этапе"}})
def route_action(sid: SignalId, body: RouteActionIn, db: Session = Depends(get_db)):
    """submit - аналитик передаёт на согласование; approve - руководитель согласует(сигнал подтверждается, как POST
    /confirm); return - руководитель возвращает аналитику с комментарием(сигнал остаётся new, срок разбора - заново)."""
    # блокировка строки сигнала до чтения журнала: два одновременных перехода не пройдут оба
    locked = db.execute(update(models.Signal).where(models.Signal.id == sid, models.Signal.status == "new")
                        .values(status="new").execution_options(synchronize_session=False)).rowcount == 1
    s = signals._get(db, sid)
    if body.action in ("approve", "return") and not is_approver(db, body.author):
        db.rollback()
        raise HTTPException(403, f"Согласует или возвращает аналитику только {APPROVER_TEXT}")
    if not locked:
        db.rollback()
        db.refresh(s)
        raise HTTPException(409, f"Сигнал {s.code} уже рассмотрен: {STAGE_LABELS.get(s.status, s.status).lower()}")
    cur = route_of(db, s)
    need = "analysis" if body.action == "submit" else "approval"
    if cur["stage"] != need:
        db.rollback()
        verb = {"submit": "передать на согласование", "approve": "согласовать", "return": "вернуть аналитику"}[body.action]
        raise HTTPException(409, f"Сигнал {s.code} сейчас на этапе «{cur['stageLabel']}» — {verb} нельзя")
    if body.action == "approve":
        signals._decide(db, sid, schemas.SignalDecisionIn(author=body.author, comment=body.comment or APPROVE_DEFAULT),
                        "confirmed", APPROVE_WHAT)
    else:
        what = _what(SUBMIT_WHAT if body.action == "submit" else BACK_WHAT, body.comment)
        db.add(models.AuditEvent(signal_id=s.id, complex_id=s.complex_id, kind="signal", who=body.author, what=what,
                                 at=clock()))
        db.commit()
    db.refresh(s)
    return route_of(db, s)
