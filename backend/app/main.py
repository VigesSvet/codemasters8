import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import List, Optional, get_args

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import models, schemas, signals, world
from .db import get_db
from .risk import match_complex
from .risk_model import ALMATY, MODEL, T
from .seed import init_db
from .world import audit, audit_dict, iso

log = logging.getLogger("saq")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


INTERNAL_ERROR = "Внутренняя ошибка сервера. Подробности — в журнале API."


class InternalErrors:
    """Необработанное исключение → 500 JSON {"detail": …} вместо обрыва соединения/текстовой страницы, трассировка — в лог.
    ASGI-обёртка стоит внутри CORS, поэтому и у ответа 500 есть CORS-заголовки (фронт видит detail, а не «CORS error»)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def tracked(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracked)
        except Exception:
            log.exception("Необработанная ошибка: %s %s", scope.get("method"), scope.get("path"))
            if started:  # ответ уже начат — заменить его нельзя
                raise
            await JSONResponse({"detail": INTERNAL_ERROR}, status_code=500)(scope, receive, send)


def no_nul_in_url(request: Request) -> None:
    """NUL (%00) в пути или параметрах запроса — 422 в стандартной форме. PostgreSQL не хранит NUL в тексте:
    без проверки id вида «c%00» доходил бы до БД и давал 500. Тела запросов проверяют схемы (schemas.no_nul)."""
    errors = [{"type": "value_error", "loc": (place, key), "msg": schemas.NUL_MSG, "input": value}
              for place, items in (("path", request.path_params.items()), ("query", request.query_params.multi_items()))
              for key, value in items if isinstance(value, str) and "\x00" in value]
    if errors:
        raise RequestValidationError(errors)


app = FastAPI(title="SAQ Qurylys API", version="0.3.0", lifespan=lifespan, dependencies=[Depends(no_nul_in_url)],
              description="Мониторинг рекламы долевого строительства и риск-скоринг ЖК (Усть-Каменогорск)")
app.add_middleware(InternalErrors)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Disposition"])

# Подписи для журнала
STATUS_NAMES = {"new": "Новая", "needs_check": "Нужна проверка", "in_review": "На проверке",
                "confirmed": "Нарушение подтверждено", "dismissed": "Отклонено"}
TOPICS = {"basis": "основание привлечения средств (гарантия ЕО / разрешение МИО / банк)",
          "readiness": "подтверждённая готовность", "appeals": "обращения граждан", "list": "перечень ГАСК", "other": "другое"}
AUDIT_KINDS = set(get_args(schemas.AuditKind))

AsOf = Query(None, alias="asOf", description="Дата расчёта YYYY-MM-DD (по умолчанию — сегодня)")


@app.get("/api/health")
def health():
    return {"ok": True, "time": iso(models.utcnow())}


# ---------- риск: /api/risk/model объявлен первым, до любых /api/risk/... ----------

@app.get("/api/risk/model")
def risk_model():
    """Описание модели риска — shared/risk_model.json (веса, условия, пороги, тексты)."""
    return MODEL


@app.get("/api/risk", response_model=List[schemas.RiskRowOut], response_model_by_alias=True)
def risk_all(as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    return world.risk_all(db, as_of)


# ---------- справочники ----------

@app.get("/api/analysts", response_model=List[schemas.AnalystOut], response_model_by_alias=True)
def analysts(db: Session = Depends(get_db)):
    """Демо-аналитики для входа без пароля (в эксплуатации — ЭЦП/SSO); name пишется автором в журнал."""
    return world.by_id(db.scalars(select(models.Analyst)))


@app.get("/api/developers", response_model=List[schemas.DeveloperOut], response_model_by_alias=True)
def developers(db: Session = Depends(get_db)):
    return world.by_id(db.scalars(select(models.Developer)))


@app.get("/api/complexes", response_model=List[schemas.ComplexOut], response_model_by_alias=True)
def complexes(zone: Optional[schemas.Zone] = None, as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    items = world.by_id(db.scalars(select(models.Complex)))
    if zone:
        keep = {r["complexId"] for r in world.risk_all(db, as_of) if r["zone"] == zone}
        items = [c for c in items if c.id in keep]
    return items


def _complex(db: Session, cid: str) -> models.Complex:
    c = db.get(models.Complex, cid)
    if not c:
        raise HTTPException(404, "ЖК не найден")
    return c


def _ad(db: Session, aid: str) -> models.Ad:
    ad = db.get(models.Ad, aid)
    if not ad:
        raise HTTPException(404, "Публикация не найдена")
    return ad


def _event(db: Session, kind: str, who: str, what: str, **refs) -> models.AuditEvent:
    e = models.AuditEvent(kind=kind, who=who, what=what, at=models.utcnow(), **refs)
    db.add(e)
    return e


def _then(text: str, more: str) -> str:
    """Следующее предложение без «..» после инициалов («ответственный: Омарова Ж.») — как statusLine во фронте (lib/audit.ts)."""
    return f"{text}{'' if text.endswith('.') else '.'} {more}"


@app.get("/api/complexes/{cid}", response_model=schemas.ComplexOut, response_model_by_alias=True)
def complex_one(cid: str, db: Session = Depends(get_db)):
    return _complex(db, cid)


@app.get("/api/complexes/{cid}/risk", response_model=schemas.RiskOut, response_model_by_alias=True)
def complex_risk(cid: str, as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    r = world.risk_for(db, cid, as_of)
    if r is None:
        raise HTTPException(404, "ЖК не найден")
    return r


@app.get("/api/appeals", response_model=List[schemas.AppealOut], response_model_by_alias=True)
def appeals(complex_id: Optional[str] = Query(None, alias="complexId"), db: Session = Depends(get_db)):
    q = select(models.Appeal).order_by(models.Appeal.date.desc(), models.Appeal.id)
    if complex_id:
        q = q.where(models.Appeal.complex_id == complex_id)
    return db.scalars(q).all()


# ---------- публикации ----------

@app.get("/api/ads", response_model=List[schemas.AdOut], response_model_by_alias=True)
def ads(status: Optional[str] = None, platform: Optional[str] = None, db: Session = Depends(get_db)):
    q = select(models.Ad).order_by(models.Ad.date.desc())
    if status:
        q = q.where(models.Ad.status == status)
    if platform:
        q = q.where(models.Ad.platform == platform)
    return db.scalars(q).all()


@app.post("/api/ads", response_model=schemas.AdOut, response_model_by_alias=True, status_code=201)
def create_ad(body: schemas.AdIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    author = data.pop("author") or "Система"
    data["id"] = data.get("id") or f"m{uuid.uuid4().hex[:8]}"
    if db.get(models.Ad, data["id"]):
        raise HTTPException(409, f"Публикация {data['id']} уже есть")
    # дата публикации — местное время без смещения, как в источниках (Усть-Каменогорск, UTC+5)
    data["date"] = data.get("date") or datetime.now(ALMATY).replace(tzinfo=None).isoformat(timespec="minutes")
    if data.get("complex_id"):
        linked = db.get(models.Complex, data["complex_id"])
        if linked is None:
            raise HTTPException(404, "ЖК не найден")
    else:
        linked = match_complex(f"{body.text} {body.ocr or ''}", db.scalars(select(models.Complex)).all())
        if linked:  # совпадение по названию — гипотеза (ниже порога факта), в балл ЖК не входит до подтверждения
            data["complex_id"], data["match_confidence"] = linked.id, T["autoLinkConfidence"]
    ad = models.Ad(**data)
    db.add(ad)
    _event(db, "system", author, "Публикация добавлена вручную", ad_id=ad.id, complex_id=ad.complex_id)
    # привязка ниже порога факта — и автоматическая, и подсказка формы ручного ввода — фиксируется как гипотеза
    if linked and ad.match_confidence < T["linkConfirmed"]:
        _event(db, "system", "Система", f"Автопривязка к ЖК «{linked.name}» по названию: уверенность "
               f"{int(ad.match_confidence * 100 + 0.5)}% — гипотеза, нужна проверка аналитиком", ad_id=ad.id, complex_id=linked.id)
    db.commit()
    return ad


@app.get("/api/ads/{aid}/score", response_model=schemas.AdScoreOut, response_model_by_alias=True)
def ad_score(aid: str, db: Session = Depends(get_db)):
    return world.ad_score(db, _ad(db, aid))


@app.patch("/api/ads/{aid}/status", response_model=schemas.AdOut, response_model_by_alias=True)
def set_status(aid: str, body: schemas.StatusIn, db: Session = Depends(get_db)):
    """Решение по публикации: кто, что и когда — в журнал (ТЗ §7). Повтор без изменений и без комментария журнал не засоряет.

    Запись — условным UPDATE по прочитанным статусу и ответственному (как решение по сигналу): если их успел изменить другой
    аналитик, 409, а не молчаливая перезапись чужого решения и событие с неверным прежним статусом. Работает и на SQLite,
    где SELECT … FOR UPDATE игнорируется."""
    ad = _ad(db, aid)
    old, old_assignee = ad.status, ad.assignee
    new_assignee = body.assignee if body.assignee and body.assignee != old_assignee else None
    if old == body.status and not new_assignee and not body.comment:
        return ad
    name = STATUS_NAMES.get
    what = (f"Статус: «{name(old, old)}» → «{name(body.status)}»" if old != body.status
            else f"Статус: «{name(old, old)}» без изменений")
    what += f" · ответственный: {new_assignee}" if new_assignee else ""
    if body.comment:
        what = _then(what, f"Основание: {body.comment}")
    A = models.Ad
    res = db.execute(update(A).where(A.id == aid, A.status == old, A.assignee == old_assignee)  # == None → IS NULL
                     .values(status=body.status, assignee=new_assignee or old_assignee)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        db.refresh(ad)
        now = f"Публикацию уже изменили: сейчас «{name(ad.status, ad.status)}»"
        now += f", ответственный — {ad.assignee}" if ad.assignee else ""
        raise HTTPException(409, _then(now, "Обновите страницу и повторите решение"))
    _event(db, "status", body.author, what, ad_id=aid, complex_id=ad.complex_id)
    db.commit()
    db.refresh(ad)
    return ad


@app.post("/api/ads/{aid}/comments", response_model=schemas.AuditOut, response_model_by_alias=True, status_code=201)
def add_comment(aid: str, body: schemas.CommentIn, db: Session = Depends(get_db)):
    ad = _ad(db, aid)
    e = _event(db, "comment", body.author, body.text, ad_id=aid, complex_id=ad.complex_id)
    db.commit()
    return audit_dict(e)


@app.patch("/api/ads/{aid}/complex", response_model=schemas.AdOut, response_model_by_alias=True)
def set_complex(aid: str, body: schemas.AdComplexIn, db: Session = Depends(get_db)):
    """Привязка публикации к ЖК аналитиком: подтверждённая привязка — факт (уверенность 100%), null — снять привязку."""
    ad = _ad(db, aid)
    old = db.get(models.Complex, ad.complex_id) if ad.complex_id else None
    if body.complex_id is None:
        ad.complex_id, ad.match_confidence = None, 0
        cid, what = old.id if old else None, "Привязка к ЖК снята"
    else:
        c = _complex(db, body.complex_id)
        ad.complex_id, ad.match_confidence = c.id, 1.0
        cid = c.id
        if old and old.id != c.id:
            # прежний ЖК теряет доказательство (R2/R3) — в его журнале это видно с автором и временем, как и снятие привязки
            _event(db, "link", body.author, f"Публикация отвязана от ЖК «{old.name}»: перепривязана к ЖК «{c.name}»",
                   ad_id=aid, complex_id=old.id)
            what = f"Привязка изменена: «{old.name}» → «{c.name}»"
        else:
            what = f"Привязка к ЖК «{c.name}» подтверждена аналитиком"
    _event(db, "link", body.author, what, ad_id=aid, complex_id=cid)
    db.commit()
    return ad


# ---------- журнал (ТЗ §7 «Контроль решений», §4 «источник и время фиксации») ----------

@app.get("/api/audit", response_model=List[schemas.AuditOut], response_model_by_alias=True)
def audit_all(ad_id: Optional[str] = Query(None, alias="adId"), complex_id: Optional[str] = Query(None, alias="complexId"),
              signal_id: Optional[int] = Query(None, alias="signalId", ge=1, le=2_147_483_647),
              kind: Optional[List[str]] = Query(None, description="Вид события; несколько — повтором параметра или через запятую"),
              limit: int = Query(200, ge=1, le=500, description="Последние N событий (отдаются по возрастанию времени)"),
              db: Session = Depends(get_db)):
    E = models.AuditEvent
    where = [col == v for col, v in ((E.ad_id, ad_id), (E.complex_id, complex_id), (E.signal_id, signal_id)) if v is not None]
    kinds = [k.strip() for part in kind or [] for k in part.split(",") if k.strip()]
    bad = [k for k in kinds if k not in AUDIT_KINDS]
    if bad:
        raise RequestValidationError([{"type": "literal_error", "loc": ("query", "kind"), "input": bad[0],
                                       "msg": f"Input should be one of: {', '.join(sorted(AUDIT_KINDS))}"}])
    if kinds:
        where.append(E.kind.in_(kinds))
    return audit(db, *where, limit=limit)


@app.get("/api/ads/{aid}/audit", response_model=List[schemas.AuditOut], response_model_by_alias=True)
def ad_audit(aid: str, db: Session = Depends(get_db)):
    return audit(db, models.AuditEvent.ad_id == _ad(db, aid).id)


@app.get("/api/complexes/{cid}/audit", response_model=List[schemas.AuditOut], response_model_by_alias=True)
def complex_audit(cid: str, db: Session = Depends(get_db)):
    """Журнал объекта: сведения и их источники, публикации по ЖК, сигналы и их выгрузка, запросы сведений."""
    return audit(db, models.AuditEvent.complex_id == _complex(db, cid).id)


@app.post("/api/complexes/{cid}/requests", response_model=schemas.AuditOut, response_model_by_alias=True, status_code=201)
def info_request(cid: str, body: schemas.InfoRequestIn, db: Session = Depends(get_db)):
    """Внутренний запрос сведений по ЖК: запись в журнале объекта; никуда не отправляется."""
    c = _complex(db, cid)
    what = "Запрос сведений: " + ", ".join(text for key, text in TOPICS.items() if key in body.topics)
    what += f". Комментарий: {body.comment}" if body.comment else ""
    e = _event(db, "request", body.author, what, complex_id=c.id)
    db.commit()
    return audit_dict(e)


@app.post("/api/ocr")
async def ocr(file: UploadFile = File(...)):
    """OCR скриншота объявления. Если установлен Tesseract (rus+kaz) — распознаём, иначе отдаём пустой результат."""
    raw = await file.read()
    try:
        import io

        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(io.BytesIO(raw)), lang="rus+kaz+eng")
        return {"text": text.strip(), "engine": "tesseract"}
    except Exception as e:  # noqa: BLE001
        return {"text": "", "engine": "none", "detail": str(e)[:200]}


@app.get("/api/stats")
def stats(as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    zones = {"red": 0, "yellow": 0, "green": 0}
    for r in world.risk_all(db, as_of):
        zones[r["zone"]] += 1
    ads_all = db.scalars(select(models.Ad)).all()
    return {"zones": zones, "queue": sum(a.status in ("new", "needs_check") for a in ads_all), "ads": len(ads_all),
            "signals": signals.counts(db)}


# ---------- сигналы (модуль 05) ----------

app.include_router(signals.router)
