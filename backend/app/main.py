import logging
import re
from contextlib import asynccontextmanager
from datetime import date
from typing import List, Optional, get_args

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import (ads_service, ai, decisions, discovery, importer, logs, media, models, registry, schemas, screening,
               signals, verification, world)
from .ads_service import REASON_NAMES, _event, _pct, _why  # noqa: F401 — прежние имена main (журнал, гипотезы)
from .ai import evaluate, ocr as ocr_engine
from .db import get_db
from .integrations import registry as connectors, routes as integration_routes, service as integration_service
from .risk_model import MODEL, T
from . import letters, route
from .seed import init_db
from .settings import get_settings
from .world import audit, audit_dict, iso

log = logging.getLogger("saq")


@asynccontextmanager
async def lifespan(_: FastAPI):
    logs.install()  # маскирование секретов в логах (обработчики uvicorn к этому моменту уже настроены)
    init_db()
    s = get_settings()
    ready = [code for code, conn in connectors.CONNECTORS.items() if not conn.missing(s)]
    broken = [code for code, conn in connectors.CONNECTORS.items() if isinstance(conn, connectors.BrokenConnector)]
    log.info("Подключений: %s, настроено на сервере: %s", len(connectors.CONNECTORS), ", ".join(ready) or "нет")
    if broken:
        log.error("Модули подключений не загрузились: %s — подробности выше в журнале", ", ".join(broken))
    if s.public_host:
        log.info("Публичный адрес %s: доступен только путь %s", s.public_host, s.public_path)
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
    """Загрузка файлов и тела приёма (POST /api/ocr — 10 МБ, POST /api/sources/import — 5 МБ, POST /api/media и снимок
    площадки POST /api/complexes/{id}/photos — 10 МБ, POST /api/intake/publications — 2 МБ, POST /api/intake/telegram —
    60 МБ): тело больше предела (файл + запас на
    разметку multipart) — 413 сразу, а не после того, как весь файл придёт по сети и ляжет во временный файл (FastAPI
    разбирает форму до вызова обработчика, и предела на размер файла у разбора нет). По Content-Length — не читая тело;
    без него (chunked) — по мере чтения. Точная проверка размера файла — в ocr.load, importer.parse и media.
    413 на приёме (/api/intake/*) записывается в состояние подключения (service.record_reject)."""

    LIMITS = {"/api/ocr": (ocr_engine.MAX_BYTES + 64 * 1024, ocr_engine.TOO_BIG),
              "/api/sources/import": (importer.MAX_BYTES + 64 * 1024, importer.TOO_BIG),
              "/api/media": (media.UPLOAD_MAX + 64 * 1024, "Файл больше 10 МБ"),
              "/api/intake/publications": (media.INTAKE_JSON_MAX, "Тело запроса больше 2 МБ"),
              "/api/intake/telegram": (media.BOT_MAX_BODY, "Заявка больше 60 МБ")}
    REJECTS = {"/api/intake/publications": "intake", "/api/intake/telegram": "telegram_bot"}
    PHOTO = re.compile(r"^/api/complexes/[^/]+/photos$")  # снимок площадки — предел как у скриншота

    def __init__(self, app):
        self.app = app

    def _limit(self, path: str):
        if path in self.LIMITS:
            return self.LIMITS[path]
        return self.LIMITS["/api/media"] if self.PHOTO.match(path) else None

    async def __call__(self, scope, receive, send):
        got_limit = self._limit(scope["path"]) if scope["type"] == "http" and scope["method"] == "POST" else None
        if got_limit is None:
            return await self.app(scope, receive, send)
        limit, too_big = got_limit
        reject = self.REJECTS.get(scope["path"])
        length = next((v for k, v in scope["headers"] if k == b"content-length"), b"")
        if length.isdigit() and int(length) > limit:
            if reject:
                await run_in_threadpool(integration_service.record_reject, reject, 413, too_big)
            return await JSONResponse({"detail": too_big}, status_code=413)(scope, receive, send)
        got = 0

        async def counted():
            nonlocal got
            message = await receive()
            got += len(message.get("body", b"")) if message["type"] == "http.request" else 0
            if got > limit:  # FastAPI пропускает HTTPException из разбора тела как есть → 413
                if reject:
                    await run_in_threadpool(integration_service.record_reject, reject, 413, too_big)
                raise HTTPException(413, too_big)
            return message

        await self.app(scope, counted, send)


class PublicHostGuard:
    """Публичный адрес OLX Callback (туннель ngrok, OLX_REDIRECT_URI): через его хост доступен только путь обратного
    вызова, остальное — 404 «Не найдено» (API наружу не открывается). Проверяются и Host, и X-Forwarded-Host; хост
    сравнивается без порта, регистра и конечной точки («x.ngrok-free.dev.» — тот же хост, как в http.host_allowed).
    Самая внешняя обёртка приложения."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _hosts(scope) -> set:
        out = set()
        for key in (b"x-forwarded-host", b"host"):
            value = next((v for k, v in scope.get("headers") or [] if k == key), None)
            if value:
                first = value.decode("latin-1").split(",", 1)[0].strip()
                out.add(first.split(":")[0].lower().rstrip("."))
        return out

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            s = get_settings()
            if s.public_host and s.public_host in self._hosts(scope) and scope.get("path") != s.public_path:
                return await JSONResponse({"detail": "Не найдено"}, status_code=404)(scope, receive, send)
        return await self.app(scope, receive, send)


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
app.add_middleware(PublicHostGuard)  # последней — самая внешняя

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
    """Аналитики для входа без пароля (пилот; в эксплуатации — ЭЦП/SSO); name пишется автором в журнал."""
    return world.by_id(db.scalars(select(models.Analyst)))


@app.get("/api/developers", response_model=List[schemas.DeveloperListOut], response_model_by_alias=True)
def developers(as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    """Застройщики с «Проверкой по рекламе» (screening): худшая оценка их ЖК — предварительная, не вывод о нарушении."""
    r = screening.compute(db, as_of)
    return screening.developer_rows(world.by_id(db.scalars(select(models.Developer))), r)


@app.get("/api/complexes", response_model=List[schemas.ComplexListOut], response_model_by_alias=True)
def complexes(zone: Optional[schemas.Zone] = None, screening_status: Optional[schemas.ScreeningStatus] = Query(
                  None, alias="screening", description="Только ЖК с этой оценкой «Проверка по рекламе»"),
              as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    """ЖК с основанием по открытым данным (permit), «Проверкой по рекламе» (screening) и объектами из рекламы (advert)."""
    items = world.by_id(db.scalars(select(models.Complex)))
    if zone:  # ЖК, риск по которому не оценивается (реестр без проверенных сведений), ни в одну зону не входит
        keep = {r["complexId"] for r in world.risk_all(db, as_of) if r["assessed"] and r["zone"] == zone}
        items = [c for c in items if c.id in keep]
    r = screening.compute(db, as_of)
    if screening_status:
        items = [c for c in items if r.complexes[c.id]["status"] == screening_status]
    return screening.complex_rows(db, items, r)


def _complex(db: Session, cid: str) -> models.Complex:
    """ЖК (демо-набора или карточка из открытого реестра); ключ объекта открытых данных — 404 с объяснением (риск и
    сигналы для записей источников не считаются)."""
    c = db.get(models.Complex, cid)
    if not c:
        raise registry.not_found(db, cid)
    return c


def _ad(db: Session, aid: str) -> models.Ad:
    ad = db.get(models.Ad, aid)
    if not ad:
        raise HTTPException(404, "Публикация не найдена")
    return ad


def _then(text: str, more: str) -> str:
    """Следующее предложение без «..» после инициалов («ответственный: Омарова Ж.») — как statusLine во фронте (lib/audit.ts)."""
    return f"{text}{'' if text.endswith('.') else '.'} {more}"


@app.get("/api/complexes/{cid}", response_model=schemas.ComplexListOut, response_model_by_alias=True)
def complex_one(cid: str, as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    c = _complex(db, cid)
    return screening.complex_rows(db, [c], screening.compute(db, as_of))[0]


@app.get("/api/complexes/{cid}/risk", response_model=schemas.RiskOut, response_model_by_alias=True)
def complex_risk(cid: str, as_of: Optional[date] = AsOf, db: Session = Depends(get_db)):
    r = world.risk_for(db, cid, as_of)
    if r is None:
        raise registry.not_found(db, cid)
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
    """Публикации, новые первыми; при одинаковой дате — по id (порядок одинаков на SQLite и PostgreSQL — офлайн-копия
    набора, app.snapshot, совпадает с ответом API)."""
    q = select(models.Ad).order_by(models.Ad.date.desc(), models.Ad.id)
    if status:
        q = q.where(models.Ad.status == status)
    if platform:
        q = q.where(models.Ad.platform == platform)
    return db.scalars(q).all()


@app.get("/api/ads/{aid}", response_model=schemas.AdOut, response_model_by_alias=True)
def ad_one(aid: str, db: Session = Depends(get_db)):
    """Одна публикация (в том числе состояние анализа собранной — processing)."""
    return _ad(db, aid)


def _confirmed(ad: models.Ad) -> bool:
    """Привязка — факт: её подтвердил аналитик или подтверждено нарушение по публикации."""
    return bool(ad.link_confirmed_by) or ad.status == "confirmed"


@app.post("/api/ads", response_model=schemas.AdOut, response_model_by_alias=True, status_code=201)
def create_ad(body: schemas.AdIn, db: Session = Depends(get_db)):
    """Ручное добавление публикации. Без complexId ЖК подбирает ИИ: лучшая гипотеза с уверенностью от lowConfidence
    становится привязкой-гипотезой (в балл ЖК не входит до подтверждения аналитиком). С complexId и linkConfirmed —
    аналитик выбрал ЖК сам: привязка сразу факт. Общая запись публикации — ads_service.create_ad (её же вызывают
    сборщики, приём публикаций и бот заявок); image «/api/media/{id}» привязывает загруженный формой скриншот."""
    data = body.model_dump()
    try:
        ad = ads_service.create_ad(db, data, actor=ads_service.analyst(data.get("author")))
    except ads_service.AdExists as e:
        raise HTTPException(409, f"Публикация {e.ad_id} уже есть") from None
    except ads_service.ComplexNotFound as e:
        raise registry.not_found(db, e.complex_id) from None
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
    """Публикация для сопоставления с ЖК; у заявки жителя аккаунт и ссылка не учитываются (ads_service.match_obj)."""
    return ads_service.match_obj(ad)


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
    m = _ad_obj(ad)
    res = ai.analyze(cat, ad.text, ad.ocr, m["account"], ad.platform, m["url"], rejected=ad.rejected_links or [])
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
    not_assessed = 0
    for r in world.risk_all(db, as_of):
        if r["assessed"]:
            zones[r["zone"]] += 1
        else:  # карточка из открытого реестра без проверенных сведений — не «зелёная», а не оценивается
            not_assessed += 1
    ads_all = db.scalars(select(models.Ad)).all()
    return {"zones": zones, "notAssessed": not_assessed, "queue": sum(a.status in ("new", "needs_check") for a in ads_all),
            "ads": len(ads_all), "signals": signals.counts(db),
            "screening": screening.summary(screening.compute(db, as_of), db)}


# ---------- сигналы (модуль 05); маршрут рассмотрения — раньше /{sid}, иначе /routes ушёл бы в него (T-79) ----------

app.include_router(route.router)
app.include_router(signals.router)

# ---------- проект запроса сведений в ЖАО / Единому оператору / ГАСК (T-78) ----------

app.include_router(letters.router)

# ---------- открытые данные: источники, объекты, импорт (без риска и сигналов) ----------

app.include_router(registry.router)

# ---------- объекты из рекламы: ЖК, которых нет в справочнике и реестре; «Проверка по рекламе» — app.screening ----------

app.include_router(discovery.router)

# ---------- сверка сведений аналитиком: основание, перечень ГАСК, готовность, обращения, снимки площадки ----------

app.include_router(verification.router)

# ---------- решение аналитика по «Проверке по рекламе»: «Объект сдан», «Проверено — замечаний нет», отмена ----------

app.include_router(decisions.router)

# ---------- сбор публикаций: подключения, запуски, приём, файлы; маршруты модулей подключений ----------

app.include_router(integration_routes.router)
for _router in connectors.routers():
    app.include_router(_router)
