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

from . import ai, models, schemas, signals, world
from .ai import evaluate, ocr as ocr_engine
from .db import get_db
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


class UploadLimit:
    """POST /api/ocr: тело больше предела (файл 10 МБ + запас на разметку multipart) — 413 сразу, а не после того, как весь
    файл придёт по сети и ляжет во временный файл (FastAPI разбирает форму до вызова обработчика, и предела на размер
    файла у разбора нет). По Content-Length — не читая тело; без него (chunked) — по мере чтения. Точная проверка размера
    файла — в ocr.load."""

    PATHS = {"/api/ocr"}
    LIMIT = ocr_engine.MAX_BYTES + 64 * 1024

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] not in self.PATHS:
            return await self.app(scope, receive, send)
        length = next((v for k, v in scope["headers"] if k == b"content-length"), b"")
        if length.isdigit() and int(length) > self.LIMIT:
            return await JSONResponse({"detail": ocr_engine.TOO_BIG}, status_code=413)(scope, receive, send)
        got = 0

        async def counted():
            nonlocal got
            message = await receive()
            got += len(message.get("body", b"")) if message["type"] == "http.request" else 0
            if got > self.LIMIT:  # FastAPI пропускает HTTPException из разбора тела как есть → 413
                raise HTTPException(413, ocr_engine.TOO_BIG)
            return message

        await self.app(scope, counted, send)


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
app.add_middleware(UploadLimit)
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


def _pct(x: float) -> int:
    return int((x or 0) * 100 + 0.5)


REASON_NAMES = {"name": "название", "translit": "название в другом написании", "address": "адрес", "account": "аккаунт",
                "phone": "телефон застройщика", "history": "история аккаунта", "district": "район"}


def _why(h: dict) -> str:
    return ", ".join(dict.fromkeys(REASON_NAMES[r["feature"]] for r in h["reasons"]))


def _confirmed(ad: models.Ad) -> bool:
    """Привязка — факт: её подтвердил аналитик или подтверждено нарушение по публикации."""
    return bool(ad.link_confirmed_by) or ad.status == "confirmed"


@app.post("/api/ads", response_model=schemas.AdOut, response_model_by_alias=True, status_code=201)
def create_ad(body: schemas.AdIn, db: Session = Depends(get_db)):
    """Ручное добавление публикации. Без complexId ЖК подбирает ИИ: лучшая гипотеза с уверенностью от lowConfidence
    становится привязкой-гипотезой (в балл ЖК не входит до подтверждения аналитиком). С complexId и linkConfirmed —
    аналитик выбрал ЖК сам: привязка сразу факт."""
    data = body.model_dump()
    author = data.pop("author") or "Система"
    confirmed = data.pop("link_confirmed")
    data["id"] = data.get("id") or f"m{uuid.uuid4().hex[:8]}"
    if db.get(models.Ad, data["id"]):
        raise HTTPException(409, f"Публикация {data['id']} уже есть")
    # дата публикации — местное время без смещения, как в источниках (Усть-Каменогорск, UTC+5)
    data["date"] = data.get("date") or datetime.now(ALMATY).replace(tzinfo=None).isoformat(timespec="minutes")
    linked = _complex(db, data["complex_id"]) if data.get("complex_id") else None
    hyps = [] if confirmed else ai.all_hypotheses(world.catalog(db), data)
    best = hyps[0] if hyps else None
    hyp = None
    if confirmed:
        data.update(match_confidence=1.0, link_confirmed_by=author, link_confirmed_at=models.utcnow())
    elif linked:  # подсказка формы (выбранная гипотеза) — тоже гипотеза; уверенность — из анализа, если форма её не дала
        hyp = next((h for h in hyps if h["complexId"] == linked.id), None)
        if not data.get("match_confidence") and hyp:
            data["match_confidence"] = hyp["confidence"]
    elif best and best["confidence"] >= T["lowConfidence"]:
        linked, hyp = db.get(models.Complex, best["complexId"]), best
        data["complex_id"], data["match_confidence"] = linked.id, best["confidence"]
    ad = models.Ad(**data)
    db.add(ad)
    _event(db, "system", author, "Публикация добавлена вручную", ad_id=ad.id, complex_id=ad.complex_id)
    if confirmed:
        _event(db, "link", author, f"Привязка к ЖК «{linked.name}» подтверждена аналитиком", ad_id=ad.id, complex_id=linked.id)
    elif linked:
        why = _why(hyp) if hyp else "выбран в форме ручного ввода"
        _event(db, "ai", "Система", f"Гипотеза ИИ: ЖК «{linked.name}», уверенность {_pct(ad.match_confidence)}% ({why}) — "
               "нужна проверка аналитиком", ad_id=ad.id, complex_id=linked.id)
    else:
        what = "ИИ: ЖК не определён — нужна ручная привязка"
        if best:
            what += (f" (лучшая гипотеза — «{best['complexName']}», {_pct(best['confidence'])}%, ниже порога "
                     f"{_pct(T['lowConfidence'])}%)")
        _event(db, "ai", "Система", what, ad_id=ad.id)
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


@app.patch("/api/ads/{aid}/complex", response_model=schemas.AdOut, response_model_by_alias=True,
           responses={409: {"description": "Решение по публикации уже принял другой аналитик"}})
def set_complex(aid: str, body: schemas.AdComplexIn, db: Session = Depends(get_db)):
    """Привязка публикации к ЖК аналитиком: подтверждённая привязка — факт (кто и когда — linkConfirmedBy/At,
    уверенность 100%), null — снять привязку. Решение другого аналитика, принятое тем временем, — 409 (см. _decide)."""
    ad = _ad(db, aid)
    old = db.get(models.Complex, ad.complex_id) if ad.complex_id else None
    moved = None
    if body.complex_id is None:
        values = dict(complex_id=None, match_confidence=0, link_confirmed_by=None, link_confirmed_at=None)
        cid, what = old.id if old else None, "Привязка к ЖК снята"
    else:
        c = _complex(db, body.complex_id)
        values = dict(complex_id=c.id, match_confidence=1.0, link_confirmed_by=body.author, link_confirmed_at=models.utcnow(),
                      rejected_links=[x for x in ad.rejected_links or [] if x != c.id])
        cid = c.id
        if old and old.id != c.id:
            # прежний ЖК теряет доказательство (R2/R3) — в его журнале это видно с автором и временем, как и снятие привязки
            moved = f"Публикация отвязана от ЖК «{old.name}»: перепривязана к ЖК «{c.name}»"
            what = f"Привязка изменена: «{old.name}» → «{c.name}»"
        else:
            what = f"Привязка к ЖК «{c.name}» подтверждена аналитиком"
    _decide(db, ad, **values)
    if moved:
        _event(db, "link", body.author, moved, ad_id=aid, complex_id=old.id)
    _event(db, "link", body.author, what, ad_id=aid, complex_id=cid)
    db.commit()
    db.refresh(ad)
    return ad


# ---------- журнал (ТЗ §7 «Контроль решений», §4 «источник и время фиксации») ----------

@app.get("/api/audit", response_model=List[schemas.AuditOut], response_model_by_alias=True)
def audit_all(ad_id: Optional[str] = Query(None, alias="adId"), complex_id: Optional[str] = Query(None, alias="complexId"),
              signal_id: Optional[int] = Query(None, alias="signalId", ge=1, le=2_147_483_647),
              kind: Optional[List[str]] = Query(None, description="Вид события; несколько — повтором параметра или через запятую"),
              limit: int = Query(200, ge=1, le=500, description="Последние N событий (отдаются по возрастанию времени)"),
              human: bool = Query(False, description="Только действия людей: без событий «Системы» и импорта данных"),
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
    if human:  # «Последние действия» в сводке: системные события не вытесняют решения аналитиков
        where.append(E.who != "Система")
        if not kinds:
            where.append(E.kind.notin_(("system", "data")))
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


# ---------- ИИ: анализ публикаций, гипотезы и решения аналитика (ТЗ §6) ----------

@app.post("/api/analyze", response_model=schemas.AnalysisOut, response_model_by_alias=True)
def analyze(body: schemas.AnalyzeIn, db: Session = Depends(get_db)):
    """Анализ текста и распознанного скриншота без записи в БД (форма ручного ввода): условия оплаты, гипотезы ЖК."""
    return ai.analyze(world.catalog(db), body.text, body.ocr, body.account, body.platform, body.url)


def _ad_obj(ad: models.Ad) -> dict:
    return {"text": ad.text, "ocr": ad.ocr, "account": ad.account, "url": ad.url}


def _conf(h: dict) -> str:
    return f"{_pct(h['confidence'])}%" + (", низкая уверенность" if h["low"] else "")


def _drifted(ad: models.Ad, h: dict) -> bool:
    """Уверенность, сохранённая при сборе или добавлении публикации, и уверенность текущего анализа — заметно разные
    (на 5 п. п. и больше) или по разные стороны порога «низкая уверенность»."""
    stored = ad.match_confidence or 0
    return abs(h["confidence"] - stored) >= 0.05 or h["low"] != (stored < T["lowConfidence"])


def _link_note(db: Session, cat, ad: models.Ad, shown: List[dict]) -> Optional[str]:
    """Текущая привязка — гипотеза, а анализ её не поддерживает (не в топе или признаков нет) или оценивает иначе, чем
    при сборе (у одной привязки — одна уверенность: расхождение объясняется прямо, ТЗ §6 «неуверенные выводы маркируются»)."""
    if not ad.complex_id or _confirmed(ad):
        return None
    name = _complex(db, ad.complex_id).name
    stored = f"{_pct(ad.match_confidence)}%"
    h = next((h for h in shown if h["complexId"] == ad.complex_id), None)
    if h:
        if not _drifted(ad, h):
            return None
        return (f"Текущая привязка к ЖК «{name}» — гипотеза с уверенностью {stored} при сборе; по текущему анализу — "
                f"{_conf(h)}. При подтверждении сохранится уверенность текущего анализа")
    h = next((h for h in ai.all_hypotheses(cat, _ad_obj(ad)) if h["complexId"] == ad.complex_id), None)
    if h:
        return (f"Текущая привязка к ЖК «{name}» (гипотеза, {stored} при сборе) — не лучшая гипотеза ИИ: уверенность по "
                f"текущему анализу {_conf(h)}")
    return (f"Текущая привязка к ЖК «{name}» (гипотеза, {stored}) анализом не подтверждается: "
            "признаков этого ЖК в тексте мало — проверьте привязку")


@app.get("/api/ads/{aid}/analysis", response_model=schemas.AdAnalysisOut, response_model_by_alias=True)
def ad_analysis(aid: str, db: Session = Depends(get_db)):
    """Анализ сохранённой публикации: без отклонённых аналитиком гипотез, с решениями по привязке и условиям оплаты."""
    ad = _ad(db, aid)
    cat = world.catalog(db, exclude_ad=aid)
    res = ai.analyze(cat, ad.text, ad.ocr, ad.account, ad.platform, ad.url, rejected=ad.rejected_links or [])
    note = _link_note(db, cat, ad, res["hypotheses"])
    if note:
        res["note"] = ". ".join(x for x in (res["note"], note) if x)
    return {**res, "termsReview": ad.terms_review, "termsReviewBy": ad.terms_review_by, "termsReviewAt": iso(ad.terms_review_at),
            "linkConfirmedBy": ad.link_confirmed_by, "linkConfirmedAt": iso(ad.link_confirmed_at),
            "rejectedLinks": list(ad.rejected_links or [])}


def _state(db: Session, ad: models.Ad) -> str:
    """Текущее решение по публикации — для ответа 409."""
    c = db.get(models.Complex, ad.complex_id) if ad.complex_id else None
    if not c:
        link = "ЖК не привязан"
    elif ad.link_confirmed_by:
        link = f"привязка к ЖК «{c.name}» подтверждена ({ad.link_confirmed_by})"
    else:
        link = f"привязка к ЖК «{c.name}» — " + ("факт (нарушение подтверждено)" if ad.status == "confirmed" else "гипотеза ИИ")
    terms = {"accepted": "условия оплаты подтверждены", "rejected": "условия оплаты отклонены"}.get(ad.terms_review)
    return link + (f", {terms} ({ad.terms_review_by})" if terms else "")


def _decide(db: Session, ad: models.Ad, *guards, **values) -> None:
    """Запись решения аналитика по публикации (привязка, гипотезы ИИ, условия оплаты) — условным UPDATE по номеру версии
    решений, прочитанному вместе с публикацией (как PATCH /status и решение по сигналу). Если другой аналитик успел решить
    раньше, 409: иначе два решения смешались бы в состояние, которого не давало ни одно (например, «факт привязки» без
    ЖК), а журнал противоречил бы состоянию. События журнала добавляются после успешной записи."""
    A = models.Ad
    res = db.execute(update(A).where(A.id == ad.id, A.decision_rev == ad.decision_rev, *guards)
                     .values(decision_rev=A.decision_rev + 1, **values).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        db.refresh(ad)
        raise HTTPException(409, _then(f"Публикацию уже изменили: сейчас {_state(db, ad)}",
                                       "Обновите страницу и повторите решение"))


@app.post("/api/ads/{aid}/hypotheses/accept", response_model=schemas.AdOut, response_model_by_alias=True,
          responses={409: {"description": "Решение по публикации уже принял другой аналитик"},
                     422: {"description": "Этот ЖК ИИ не предлагал"}})
def accept_hypothesis(aid: str, body: schemas.HypothesisIn, db: Session = Depends(get_db)):
    """Аналитик принимает гипотезу ИИ: привязка к ЖК становится фактом (linkConfirmedBy/At), уверенность — этой гипотезы
    по текущему анализу. Текущая привязка-гипотеза, которую анализ уже не находит, подтверждается с уверенностью при сборе.
    ЖК, которого ИИ не предлагал, — 422: это ручная привязка (PATCH /api/ads/{id}/complex), а не гипотеза ИИ."""
    ad, c = _ad(db, aid), _complex(db, body.complex_id)
    if ad.complex_id == c.id and ad.link_confirmed_by:
        return ad  # уже принята — журнал не засоряем
    h = next((h for h in ai.all_hypotheses(world.catalog(db, exclude_ad=aid), _ad_obj(ad)) if h["complexId"] == c.id), None)
    current = ad.complex_id == c.id
    if h is None and not current:
        raise HTTPException(422, f"ЖК «{c.name}» ИИ для этой публикации не предлагал — выберите ЖК вручную (ручная привязка "
                                 "аналитиком)")
    if h is None:
        conf = f"уверенность при сборе {_pct(ad.match_confidence)}%; текущий анализ эту привязку не поддерживает"
    else:
        conf = (f"уверенность {_pct(h['confidence'])}%" + (" — низкая" if h["low"] else "")
                + (f"; при сборе — {_pct(ad.match_confidence)}%" if current and _drifted(ad, h) else ""))
    old = db.get(models.Complex, ad.complex_id) if ad.complex_id and not current else None
    _decide(db, ad, complex_id=c.id, link_confirmed_by=body.author, link_confirmed_at=models.utcnow(),
            match_confidence=h["confidence"] if h else ad.match_confidence,
            rejected_links=[x for x in ad.rejected_links or [] if x != c.id])
    if old:
        _event(db, "link", body.author, f"Публикация отвязана от ЖК «{old.name}»: принята гипотеза ИИ — ЖК «{c.name}»",
               ad_id=aid, complex_id=old.id)
    _event(db, "ai", body.author, f"Гипотеза ИИ принята: ЖК «{c.name}» ({conf})", ad_id=aid, complex_id=c.id)
    db.commit()
    db.refresh(ad)
    return ad


@app.post("/api/ads/{aid}/hypotheses/reject", response_model=schemas.AdOut, response_model_by_alias=True,
          responses={409: {"description": "Привязка уже подтверждена или решение принял другой аналитик"}})
def reject_hypothesis(aid: str, body: schemas.HypothesisRejectIn, db: Session = Depends(get_db)):
    """Аналитик отклоняет гипотезу ИИ: ЖК больше не предлагается; если это текущая привязка-гипотеза — она снимается.
    Подтверждённую привязку так не отменить (409): её снимает PATCH /api/ads/{id}/complex."""
    ad, c = _ad(db, aid), _complex(db, body.complex_id)
    if ad.complex_id == c.id and _confirmed(ad):
        who = f" ({ad.link_confirmed_by})" if ad.link_confirmed_by else ""
        raise HTTPException(409, f"Привязка к ЖК «{c.name}» уже подтверждена аналитиком{who} — это факт, а не гипотеза. "
                                 "Чтобы отменить её, снимите привязку")
    unlink = ad.complex_id == c.id
    rejected = list(ad.rejected_links or [])
    if c.id in rejected and not unlink and not body.comment:
        return ad
    values = {"rejected_links": rejected + ([c.id] if c.id not in rejected else [])}
    if unlink:
        values.update(complex_id=None, match_confidence=0, link_confirmed_by=None, link_confirmed_at=None)
    # статус тоже: «нарушение подтверждено», поставленное тем временем, делает привязку фактом
    _decide(db, ad, models.Ad.status == ad.status, **values)
    what = f"Гипотеза ИИ отклонена: ЖК «{c.name}»" + (", привязка снята" if unlink else "")
    _event(db, "ai", body.author, _then(what, f"Комментарий: {body.comment}") if body.comment else what,
           ad_id=aid, complex_id=c.id)
    db.commit()
    db.refresh(ad)
    return ad


TERMS_DECISIONS = {"accepted": "Условия оплаты (ИИ) подтверждены аналитиком",
                   "rejected": "Аналитик отклонил: это не предложение внести деньги"}


@app.post("/api/ads/{aid}/terms-review", response_model=schemas.AdOut, response_model_by_alias=True,
          responses={409: {"description": "Решение по публикации уже принял другой аналитик"}})
def terms_review(aid: str, body: schemas.TermsReviewIn, db: Session = Depends(get_db)):
    """Решение аналитика по условиям оплаты, найденным ИИ. rejected — публикация не даёт R2 ни ЖК, ни в своей оценке;
    accepted — пометка «подтверждено аналитиком». Повтор с другим решением меняет его (всё — в журнале)."""
    ad = _ad(db, aid)
    if ad.terms_review == body.decision and not body.comment:
        return ad
    _decide(db, ad, terms_review=body.decision, terms_review_by=body.author, terms_review_at=models.utcnow())
    what = TERMS_DECISIONS[body.decision]
    _event(db, "ai", body.author, _then(what, f"Комментарий: {body.comment}") if body.comment else what,
           ad_id=aid, complex_id=ad.complex_id)
    db.commit()
    db.refresh(ad)
    return ad


@app.get("/api/ocr/status", response_model=schemas.OcrStatusOut, response_model_by_alias=True)
def ocr_status():
    """Доступен ли OCR на сервере: Tesseract, версия, языки."""
    return ocr_engine.status()


@app.post("/api/ocr", response_model=schemas.OcrOut, response_model_by_alias=True,
          responses={413: {"description": "Файл больше 10 МБ"}, 415: {"description": "Не изображение"},
                     422: {"description": "Файл повреждён"}})
def ocr(file: UploadFile = File(...)):
    """OCR скриншота объявления (Tesseract, rus+kaz+eng) с предобработкой. Нет Tesseract — 200 с engine 'none'."""
    raw = file.file.read(ocr_engine.MAX_BYTES + 1)
    try:
        return ocr_engine.recognize(raw, file.content_type)
    except ocr_engine.OcrError as e:
        raise HTTPException(e.status, e.detail) from None


@app.get("/api/ai/eval", response_model=schemas.AiEvalOut, response_model_by_alias=True)
def ai_eval():
    """Проверка ИИ на новых данных (shared/ai_cases.json): ожидаемые условия оплаты и ЖК против результата движка."""
    return evaluate.run()


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
