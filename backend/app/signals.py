"""Сигнальная карточка(модуль 05): снимок доказательств при создании, решение аналитика, журнал, PDF.

Снимок(score, factors, evidence…) строится один раз в момент создания и дальше не пересчитывается:
карточка показывает, на каком основании сигнал был сформирован, даже если данные по ЖК потом изменились.
"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models, registry, schemas
from .db import get_db
from .pdf import render_signal
from .risk import ad_signals, by_date_desc
from .risk_model import MODEL
from .world import audit, basis_documents, iso, load_complex

router = APIRouter(prefix="/api/signals", tags=["signals"])

Obj = dict[str, Any]
# id сигнала в пути: в пределах INTEGER, иначе огромный id ронял запрос к БД(500) вместо 422/404
SignalId = Annotated[int, Path(ge=1, le=2_147_483_647, description="id сигнала")]
ZONE_NAMES = {z["zone"]: z["name"].lower() for z in MODEL["zones"]["items"]}
SIGNAL_AD_FIELDS = ("id", "platform", "account", "url", "date", "status", "matchConfidence", "linkConfirmedBy", "linkConfirmedAt",
                    "termsReview", "text", "ocr", "image", "price")


# ---------- снимок ----------

def snapshot(db: Session, cid: str) -> Obj | None:
    """Риск и доказательства по ЖК на текущий момент(T-11)."""
    w = load_complex(db, cid)
    if w is None:
        return None
    c = w.complexes[0]
    risk = w.risk(c)
    ads = [{**{k: a.get(k) for k in SIGNAL_AD_FIELDS}, "signals": ad_signals(a)}
           for a in by_date_desc([a for a in w.ads if a["status"] != "dismissed"])]
    return {
        "complex": c,
        "risk": risk,
        "evidence": {
            "complex": c,
            "developer": w.developers.get(c["developerId"]),
            "ads": ads,
            "appeals": by_date_desc([a for a in w.appeals if a["status"] != "rejected"]),
            "photos": c.get("photos") or [],
            "documents": basis_documents(c, risk),
        },
    }


# ---------- вывод ----------

def summary_out(s: models.Signal) -> Obj:
    return {"id": s.id, "code": s.code, "complexId": s.complex_id, "complexName": s.complex_name, "status": s.status,
            "score": s.score, "zone": s.zone, "needsCheck": s.needs_check, "hits": s.hits or [],
            "createdAt": iso(s.created_at), "createdBy": s.created_by, "decidedAt": iso(s.decided_at), "decidedBy": s.decided_by}


def journal(db: Session, sid: int) -> list[Obj]:
    return audit(db, models.AuditEvent.signal_id == sid)


def signal_out(db: Session, s: models.Signal) -> Obj:
    return {**summary_out(s), "summary": s.summary, "factors": s.factors, "observations": s.observations,
            "evidence": s.evidence, "modelVersion": s.model_version, "decisionComment": s.decision_comment,
            "fileName": s.file_name, "exportedAt": iso(s.exported_at), "journal": journal(db, s.id)}


def get_or_404(db: Session, sid: int) -> models.Signal:
    s = db.get(models.Signal, sid)
    if s is None:
        raise HTTPException(404, "Сигнал не найден")
    return s


def _open_signal(db: Session, cid: str) -> models.Signal | None:
    q = select(models.Signal).where(models.Signal.complex_id == cid, models.Signal.status == "new").limit(1)
    return db.scalar(q)


def _conflict(s: models.Signal) -> HTTPException:
    return HTTPException(409, {"message": f"По ЖК «{s.complex_name}» уже есть нерассмотренный сигнал {s.code}",
                               "signalId": s.id})


def _event(db: Session, s: models.Signal, who: str, what: str, kind: str = "signal") -> None:
    db.add(models.AuditEvent(signal_id=s.id, complex_id=s.complex_id, kind=kind, who=who, what=what, at=models.utcnow()))


_HUMAN_NAME = TypeAdapter(schemas.HumanName)


def _exporter(author: str | None) -> str:
    """Автор выгрузки PDF: пустой - "Аналитик", иначе проверяется как автор в формах(HumanName): NUL - 422, а не ошибка
    PostgreSQL(500), "Система" - 422(человек не пишет в журнал от имени системы). Параметр маршрута - обычная строка,
    проверка - здесь: пустой ?author=(фронтенд без входа) даёт подпись "Аналитик", а ошибка - 422 с loc
    ["query", "author"], как у проверки самого FastAPI."""
    who = " ".join((author or "").split())
    if not who:
        return "Аналитик"
    try:
        return _HUMAN_NAME.validate_python(who)
    except ValidationError as e:
        raise RequestValidationError([{**err, "loc": ("query", "author")} for err in e.errors(include_url=False)]) from None


def counts(db: Session) -> dict[str, int]:
    out = {"new": 0, "confirmed": 0, "returned": 0}
    for status, n in db.execute(select(models.Signal.status, func.count()).group_by(models.Signal.status)):
        out[status] = n
    return out


# ---------- эндпоинты ----------

@router.post("", response_model=schemas.SignalOut, response_model_by_alias=True, status_code=201,
             responses={422: {"description": "Риск по ЖК не оценивается — нет проверенных данных"}})
def create_signal(body: schemas.SignalCreateIn, db: Session = Depends(get_db)):
    snap = snapshot(db, body.complex_id)
    if snap is None:  # в т. ч. ключ объекта открытых данных: сигналы - только по карточкам ЖК(T-31)
        raise registry.not_found(db, body.complex_id)
    if not snap["risk"].get("assessed", True):  # карточка из открытого реестра без проверенных сведений
        raise HTTPException(422, f"{MODEL['notAssessed']['label']}: сигнал по ЖК «{snap['complex']['name']}» не создаётся. "
                                 "Нужны проверенные сведения — подтверждённая привязка публикации, сверенный документ "
                                 "основания, проверенное обращение или снимок площадки")
    existing = _open_signal(db, body.complex_id)
    if existing:
        raise _conflict(existing)
    c, r = snap["complex"], snap["risk"]
    s = models.Signal(
        complex_id=c["id"], complex_name=c["name"], status="new", score=r["score"], zone=r["zone"],
        needs_check=r["needsCheck"], hits=[f["code"] for f in r["factors"] if f["state"] == "hit"], summary=r["summary"],
        factors=r["factors"], observations=r["observations"], evidence=snap["evidence"], model_version=r["modelVersion"],
        created_by=body.author,
    )
    db.add(s)
    try:
        db.flush()
    except IntegrityError:  # параллельный запрос успел создать сигнал по тому же ЖК
        db.rollback()
        existing = _open_signal(db, body.complex_id)
        if existing:
            raise _conflict(existing) from None
        raise
    s.file_name = f"signal-{s.code}.pdf"
    _event(db, s, body.author, f"Сигнал создан: балл {s.score}, зона {ZONE_NAMES.get(s.zone, s.zone)}")
    db.commit()
    return signal_out(db, s)


@router.get("", response_model=list[schemas.SignalSummaryOut], response_model_by_alias=True)
def list_signals(status: schemas.SignalStatus | None = None, complex_id: str | None = Query(None, alias="complexId"),
                 db: Session = Depends(get_db)):
    q = select(models.Signal).order_by(models.Signal.id.desc())
    if status:
        q = q.where(models.Signal.status == status)
    if complex_id:
        q = q.where(models.Signal.complex_id == complex_id)
    return [summary_out(s) for s in db.scalars(q)]


@router.get("/{sid}", response_model=schemas.SignalOut, response_model_by_alias=True)
def get_signal(sid: SignalId, db: Session = Depends(get_db)):
    return signal_out(db, get_or_404(db, sid))


def decide(db: Session, sid: int, body: schemas.SignalDecisionIn, status: str, what: str) -> models.Signal:
    """Решение по сигналу - только из "new"(условный UPDATE: из двух одновременных решений второе получит 409)."""
    s = get_or_404(db, sid)
    now = models.utcnow()
    res = db.execute(update(models.Signal).where(models.Signal.id == sid, models.Signal.status == "new")
                     .values(status=status, decided_at=now, decided_by=body.author, decision_comment=body.comment)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        db.refresh(s)
        label = {"confirmed": "подтверждён", "returned": "возвращён на доработку"}.get(s.status, s.status)
        raise HTTPException(409, {"message": f"Сигнал {s.code} уже рассмотрен: {label}", "signalId": s.id})
    _event(db, s, body.author, f"{what}. {body.comment}")
    db.commit()
    db.refresh(s)
    return s


@router.post("/{sid}/confirm", response_model=schemas.SignalOut, response_model_by_alias=True)
def confirm_signal(sid: SignalId, body: schemas.SignalDecisionIn, db: Session = Depends(get_db)):
    return signal_out(db, decide(db, sid, body, "confirmed", "Сигнал подтверждён"))


@router.post("/{sid}/return", response_model=schemas.SignalOut, response_model_by_alias=True)
def return_signal(sid: SignalId, body: schemas.SignalDecisionIn, db: Session = Depends(get_db)):
    return signal_out(db, decide(db, sid, body, "returned", "Возвращён на доработку"))


@router.get("/{sid}/audit", response_model=list[schemas.AuditOut], response_model_by_alias=True)
def signal_audit(sid: SignalId, db: Session = Depends(get_db)):
    return journal(db, get_or_404(db, sid).id)


@router.get("/{sid}/pdf", response_class=Response,
            responses={200: {"content": {"application/pdf": {}}, "description": "Сигнальная карточка A4"}})
def signal_pdf(sid: SignalId, author: str | None = Query(None, max_length=128), db: Session = Depends(get_db)):
    """PDF сигнальной карточки; отмечает выгрузку(exportedAt) и пишет её в журнал. ?author= - кто выгрузил."""
    who = _exporter(author)
    s = get_or_404(db, sid)
    s.exported_at = models.utcnow()
    s.file_name = s.file_name or f"signal-{s.code}.pdf"
    _event(db, s, who, "Выгружен PDF", kind="export")
    db.flush()
    data = render_signal(signal_out(db, s), generated_at=s.exported_at)
    db.commit()
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{s.file_name}"'})
