"""API подключений и приёма публикаций(integration-spec §11.1, §7.4).

Чтение(состояние подключений, запуски, версии публикации, файлы) токена не требует и секретов не содержит; состояние
подключений и запуски без токена администратора - урезанные(service.public_*): посетитель видит только статусы,
расписание и счётчики. Настройка
подключений, ручной запуск и повторный анализ ИИ(платный запрос к Claude) - только администратору(SAQ_ADMIN_TOKEN,
проверяет сервер). Приём публикаций - по отдельному токену INTEGRATION_INGEST_TOKEN; токен проверяется ДО разбора тела
запроса, отказы записываются в состояние подключения "Приём публикаций API". Сбор, проверки и анализ в запросе не
выполняются: запрос ставит задание воркеру(202).
"""
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth, ingest, jobs, llm, media, models, schemas
from ..ads_service import COLLECTOR, _event
from ..db import SessionLocal, get_db
from ..settings import get_settings
from . import registry, service
from .base import ConfigError, MediaIn, Publication

log = logging.getLogger("saq.integrations")

router = APIRouter(tags=["integrations"])

RUN_NOT_FOUND = "Запуск не найден"
AD_NOT_FOUND = "Публикация не найдена"
FILE_NOT_FOUND = "Файл не найден"
REANALYZE_NOT_COLLECTED = "Повторный анализ доступен для собранных публикаций"
REANALYZE_EMPTY = "Нечего анализировать: у публикации нет текста и OCR"
INTAKE_NOTE = "приём публикаций API"
MEDIA_ID = Path(pattern=r"^[0-9a-f]{32}$", description="ID файла (32 шестнадцатеричных символа)")


def _connector(code: str):
    try:
        return registry.get(code)
    except KeyError:
        raise HTTPException(404, registry.NOT_FOUND) from None


def _rejected(e: service.Rejected) -> HTTPException:
    return HTTPException(e.status, e.detail)


def _invalid(loc: tuple, msg: str, value: Any) -> RequestValidationError:
    return RequestValidationError([{"type": "value_error", "loc": loc, "msg": msg, "input": value}])


# ---------- подключения ----------

@router.get("/api/sources/connectors", response_model=schemas.ConnectorsOut, response_model_by_alias=True)
def connectors(request: Request, db: Session = Depends(get_db)):
    """Подключения в порядке страницы: фактический статус, настройки, счётчики, последний запуск; жив ли воркер.
    Без токена администратора - только статусы, расписание и счётчики(admin = false)."""
    now = models.utcnow()
    admin = auth.is_admin(request)
    items, worker = service.view(db, now=now), service.worker_info(db, now)
    if not admin:
        items, worker = [service.public_connector(c) for c in items], service.public_worker(worker)
    return {"connectors": items, "worker": worker, "admin_configured": bool(get_settings().admin_token),
            "admin": admin}


@router.get("/api/sources/connectors/{code}", response_model=schemas.ConnectorOut, response_model_by_alias=True)
def connector_one(code: str, request: Request, db: Session = Depends(get_db)):
    _connector(code)
    c = service.view(db, [code])[0]
    return c if auth.is_admin(request) else service.public_connector(c)


@router.post("/api/sources/collect-all", response_model=schemas.CollectAllOut, response_model_by_alias=True)
def collect_all(body: schemas.AdminActionIn, admin: auth.Admin = Depends(auth.require_admin),
                db: Session = Depends(get_db)):
    """"Обновить всё": сбор для включённых, настроенных и проверенных сборщиков; остальные - с причиной пропуска."""
    out = service.collect_all(db, body.author, models.utcnow())
    db.commit()
    return out


@router.patch("/api/sources/{code}", response_model=schemas.ConnectorOut, response_model_by_alias=True,
              responses={409: {"description": "Подключение не настроено или действие недоступно"}})
def patch_connector(code: registry.ConnectorCode, body: schemas.ConnectorPatchIn,
                    admin: auth.Admin = Depends(auth.require_admin), db: Session = Depends(get_db)):
    """Настройки подключения(администратор): сбор по расписанию, период, списки аккаунтов/каналов/URL."""
    _connector(code)
    try:
        service.patch(db, code, body)
    except service.Rejected as e:
        db.rollback()
        raise _rejected(e) from None
    except service.Invalid as e:
        db.rollback()
        raise _invalid(e.loc, e.msg, e.value) from None
    except ConfigError as e:
        db.rollback()
        raise _invalid(("body", "config", e.field), e.msg, e.value) from None
    db.commit()
    return service.view(db, [code])[0]


def _request(code: str, trigger: str, author: str, db: Session) -> JSONResponse:
    _connector(code)
    try:
        run, created = service.request_run(db, code, trigger=trigger, who=author)
    except service.Rejected as e:
        db.rollback()
        raise _rejected(e) from None
    db.commit()
    body = schemas.RunOut.model_validate(service.run_out(db, run)).model_dump(mode="json", by_alias=True)
    return JSONResponse(body, status_code=202 if created else 200)


@router.post("/api/sources/{code}/test", response_model=schemas.RunOut, response_model_by_alias=True,
             status_code=202, responses={200: {"description": "Проверка уже в очереди или идёт"}})
def test_connector(code: registry.ConnectorCode, body: schemas.AdminActionIn,
                   admin: auth.Admin = Depends(auth.require_admin), db: Session = Depends(get_db)):
    """"Проверить подключение"(администратор): задание воркеру; ответ - запуск(202) или уже активный(200)."""
    return _request(code, "test", body.author, db)


@router.post("/api/sources/{code}/collect", response_model=schemas.RunOut, response_model_by_alias=True,
             status_code=202, responses={200: {"description": "Сбор уже в очереди или идёт"}})
def collect_connector(code: registry.ConnectorCode, body: schemas.AdminActionIn,
                      admin: auth.Admin = Depends(auth.require_admin), db: Session = Depends(get_db)):
    """"Собрать сейчас"(администратор; можно и после остановки по ошибке - это явное действие)."""
    return _request(code, "manual", body.author, db)


# ---------- запуски ----------

@router.get("/api/collection-runs", response_model=list[schemas.RunOut], response_model_by_alias=True)
def runs(request: Request, source: str | None = Query(None, max_length=32),
         limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    """Запуски сбора и проверок, новые первыми; без токена администратора - без автора, ошибок, заметок и стоимости."""
    R = models.CollectionRun
    q = select(R).order_by(R.id.desc()).limit(limit)
    if source:
        q = q.where(R.collector == source)
    out = [service.run_out(db, r) for r in db.scalars(q)]
    return out if auth.is_admin(request) else [service.public_run(r) for r in out]


@router.get("/api/collection-runs/{run_id}", response_model=schemas.RunOut, response_model_by_alias=True)
def run_one(request: Request, run_id: int = Path(ge=1, le=2_147_483_647), db: Session = Depends(get_db)):
    run = db.get(models.CollectionRun, run_id)
    if run is None:
        raise HTTPException(404, RUN_NOT_FOUND)
    out = service.run_out(db, run)
    return out if auth.is_admin(request) else service.public_run(out)


# ---------- приём публикаций(§7.4) ----------

def _intake_schema() -> dict:
    """JSON-схема тела для документации OpenAPI(тело разбирается вручную - после проверки токена)."""
    schema = schemas.IntakePublicationIn.model_json_schema(by_alias=True)
    defs = schema.pop("$defs", {})

    def inline(v):
        if isinstance(v, dict):
            ref = v.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return inline(defs.get(ref.rsplit("/", 1)[-1], {}))
            return {k: inline(x) for k, x in v.items()}
        if isinstance(v, list):
            return [inline(x) for x in v]
        return v
    return inline(schema)


def _first_error(e: ValidationError) -> str:
    err = e.errors(include_url=False)[0] if e.errors() else {}
    loc = ".".join(str(x) for x in err.get("loc", ()))
    return f"{loc}: {err.get('msg', '')}" if loc else str(err.get("msg", "ошибка проверки"))


def _intake_sync(body: schemas.IntakePublicationIn) -> ingest.IngestResult:
    """Работа с БД - в пуле потоков: публикация → единый приём(collector = intake), commit."""
    now = models.utcnow()
    pub = Publication(platform=body.platform, external_id=body.external_id, account=body.account, url=body.url,
                      text=body.text, collected_at=now, published_at=body.published_at,
                      media=[MediaIn(key=m.key, kind=m.kind, url=m.url, origin_ref="intake") for m in body.media],
                      price=body.price, reach=body.reach, raw=dict(body.raw or {}))
    raw_extra = {"_intake": {"sourceId": body.source_id, "claimedCollectedAt": models.iso(body.collected_at),
                             "externalRunId": body.external_run_id}}
    with SessionLocal() as db:
        try:
            res = ingest.ingest_publication(db, pub, collector="intake", actor=COLLECTOR, note=INTAKE_NOTE,
                                            external_run_id=body.external_run_id, raw_extra=raw_extra)
            if res.status == "foreign":
                db.rollback()
            else:
                db.commit()
        except BaseException:
            db.rollback()
            raise
    return res


@router.post("/api/intake/publications", status_code=201, response_model=schemas.IntakeResultOut,
             response_model_by_alias=True,
             openapi_extra={"requestBody": {"required": True,
                                            "content": {"application/json": {"schema": _intake_schema()}}}},
             responses={200: {"description": "Публикация уже была (без изменений или новая версия)"},
                        401: {"description": "Нет токена приёма"}, 403: {"description": "Неверный токен"},
                        409: {"description": "Публикация собрана другим источником"},
                        413: {"description": "Тело больше 2 МБ"}, 503: {"description": "Приём не настроен"}})
async def intake(request: Request) -> JSONResponse:
    """Нормализованная публикация от внешнего сборщика(Authorization: Bearer INTEGRATION_INGEST_TOKEN). Новая - 201,
    повтор или новая версия - 200. Файлы по ссылкам загружает задание анализа, не этот запрос."""
    # до разбора тела; в пуле потоков: отказ записывается в БД и не должен держать цикл событий
    await run_in_threadpool(auth.require_ingest, request)
    data = await request.body()
    try:
        body = schemas.IntakePublicationIn.model_validate_json(data)
    except ValidationError as e:
        await run_in_threadpool(service.record_reject, "intake", 422, _first_error(e))
        raise RequestValidationError([{**err, "loc": ("body", *err["loc"])}
                                      for err in e.errors(include_url=False)]) from None
    try:
        res = await run_in_threadpool(_intake_sync, body)
    except Exception as e:
        # сбой сервера - тоже в статусе подключения("ошибка"): иначе он выглядел бы как "ожидает публикаций"
        await run_in_threadpool(service.record_reject, "intake", 500,
                                f"ошибка сервера ({type(e).__name__}) — публикация не сохранена, подробности в журнале "
                                "API")
        raise
    if res.status == "foreign":
        owner = registry.find(res.owner or "")
        name = owner.name if owner is not None else (res.owner or "другой источник")
        detail = f"Публикация собрана другим источником ({name}) — обновление отклонено"
        await run_in_threadpool(service.record_reject, "intake", 409, detail)
        raise HTTPException(409, detail)
    service.reset_reject_throttle("intake")
    out = schemas.IntakeResultOut(status=res.status, ad_id=res.ad_id, item_id=res.item_id, version=res.version,
                                  media_queued=res.media_pending, analysis_queued=res.analysis_queued)
    return JSONResponse(out.model_dump(mode="json", by_alias=True), status_code=201 if res.status == "created" else 200)


# ---------- версии публикации ----------

@router.get("/api/ads/{aid}/versions", response_model=list[schemas.AdVersionOut], response_model_by_alias=True)
def ad_versions(aid: str, db: Session = Depends(get_db)):
    """Версии содержимого собранной публикации в источнике, новые первыми; у ручных и демо-публикаций - []."""
    if db.get(models.Ad, aid) is None:
        raise HTTPException(404, AD_NOT_FOUND)
    item = db.scalar(select(models.IngestItem).where(models.IngestItem.ad_id == aid))
    if item is None:
        return []
    V = models.IngestVersion
    rows = db.scalars(select(V).where(V.item_id == item.id).order_by(V.version.desc()))
    return [{"version": v.version, "collected_at": v.collected_at, "text": v.text, "price": v.price,
             "media_keys": list(v.media_keys or []), "current": v.version == item.version} for v in rows]


# ---------- анализ ИИ: "Проанализировать ещё раз" ----------

def _active_analysis(db: Session, aid: str) -> models.Job | None:
    """Задание анализа публикации, которое выполняется(у него dedupe_key уже обнулён), иначе - в очереди; None - нет."""
    J = models.Job
    found = list(db.scalars(select(J).where(J.kind == "analyze", J.ad_id == aid, J.status.in_(jobs.ACTIVE))
                            .order_by(J.id.desc())))
    return next((j for j in found if j.status == "running"), found[0] if found else None)


def _ad_response(ad: models.Ad, status: int) -> JSONResponse:
    body = schemas.AdOut.model_validate(ad).model_dump(mode="json", by_alias=True)
    return JSONResponse(body, status_code=status)


@router.post("/api/ads/{aid}/reanalyze", response_model=schemas.AdOut, response_model_by_alias=True, status_code=202,
             responses={200: {"description": "Анализ публикации уже идёт или повторный анализ уже в очереди"},
                        401: {"description": "Нет токена администратора"},
                        403: {"description": "Неверный токен администратора"},
                        404: {"description": AD_NOT_FOUND},
                        409: {"description": "Публикация не собрана, нечего анализировать, ключ Anthropic отклонён, бюджет "
                                             "0 или дневной бюджет исчерпан — задание не ставится"},
                        503: {"description": "Администрирование или анализ ИИ не настроены на сервере"}})
def reanalyze(aid: str, body: schemas.AdminActionIn, admin: auth.Admin = Depends(auth.require_admin),
              db: Session = Depends(get_db)):
    """"Проанализировать ещё раз"(администратор: запрос к Claude расходует дневной бюджет анализа ИИ). Задание анализа
    с повторным запросом, даже если этот материал этой моделью уже проанализирован: новая строка результата, прежняя
    остаётся в истории. Ответ - публикация(processing - состояние анализа): 202 - задание поставлено или задание в
    очереди стало повторным анализом; 200 - анализ уже идёт или повторный анализ уже в очереди(без дубля и без
    события). Бюджета на запрос не хватает - 409 без задания. В журнал - "Запрошен повторный анализ ИИ" от автора."""
    ad = db.get(models.Ad, aid)
    if ad is None:
        raise HTTPException(404, AD_NOT_FOUND)
    item = db.scalar(select(models.IngestItem).where(models.IngestItem.ad_id == aid))
    if item is None:
        raise HTTPException(409, REANALYZE_NOT_COLLECTED)
    s = get_settings()
    problem = llm.unavailable(db, s)
    if problem is not None:
        raise HTTPException(*problem)
    if not ((ad.text or "").strip() or (ad.ocr or "").strip()):
        raise HTTPException(409, REANALYZE_EMPTY)
    job = _active_analysis(db, aid)
    if job is not None and (job.status == "running" or (job.payload or {}).get("force")):
        return _ad_response(ad, 200)
    now = models.utcnow()
    over = llm.budget_problem(db, s, ad, now)
    if over:
        raise HTTPException(409, over)
    payload = {"force": True, "requestedBy": body.author}
    created = False
    if job is None:
        job, created = jobs.enqueue(db, "analyze", ad_id=aid, content_version=item.version,
                                    dedupe_key=f"analyze:{aid}", payload=payload, priority=llm.REANALYZE_PRIORITY)
    if not created:  # в очереди повтор загрузки или запроса, новая версия, дозапрос - теперь это повторный анализ
        job.payload = {**(job.payload or {}), **payload}
        job.priority = max(job.priority or 0, llm.REANALYZE_PRIORITY)
        if models.as_utc(job.run_after) > now:
            job.run_after = now
    item.analysis_status, item.analysis_stage, item.analysis_error = "queued", None, None
    _event(db, "ai", body.author, f"Запрошен повторный анализ ИИ (модель {s.anthropic_model}): запрос "
                                  "расходует дневной бюджет, прежний результат сохранится в истории", at=now, ad_id=aid)
    db.commit()
    db.refresh(ad)
    return _ad_response(ad, 202)


# ---------- файлы ----------

@router.get("/api/media/{asset_id}", response_class=FileResponse,
            responses={200: {"description": "Файл (изображение или видео)"}, 404: {"description": FILE_NOT_FOUND}})
def media_file(asset_id: str = MEDIA_ID, db: Session = Depends(get_db)):
    """Сохранённый файл публикации. Только загруженные файлы; тип - по сигнатуре; браузер не угадывает тип(nosniff)."""
    a = db.get(models.MediaAsset, asset_id)
    if a is None or a.status != "downloaded" or not a.storage_key:
        raise HTTPException(404, FILE_NOT_FOUND)
    try:
        path = media.path_of(a.storage_key)
    except ValueError:
        raise HTTPException(404, FILE_NOT_FOUND) from None
    if not path.is_file():
        raise HTTPException(404, FILE_NOT_FOUND)
    return FileResponse(path, media_type=a.mime or "application/octet-stream",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400",
                                 "Content-Disposition": "inline"})


@router.post("/api/media", response_model=schemas.MediaUploadOut, response_model_by_alias=True, status_code=201,
             responses={413: {"description": "Файл больше 10 МБ"}, 415: {"description": "Не изображение"},
                        429: {"description": "Слишком много загрузок"}, 507: {"description": "Мало места"}})
def media_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Скриншот из формы ручного ввода → хранилище. Привязывается к публикации, когда POST /api/ads указывает
    image "/api/media/{id}"; непривязанный удаляется через сутки."""
    data = file.file.read(media.UPLOAD_MAX + 1)
    if len(data) > media.UPLOAD_MAX:
        raise HTTPException(413, f"Файл больше {media.UPLOAD_MAX // media.MB} МБ")
    now = models.utcnow()
    try:
        media.check_upload_budget(db, now, len(data))
        st = media.store_bytes(data, allowed=media.IMAGE_TYPES)
    except media.MediaRejected as e:
        raise HTTPException(e.status, e.detail) from None
    a = models.MediaAsset(id=uuid.uuid4().hex, ad_id=None, item_id=None, kind="image", role="attachment",
                          origin_ref="upload", status="downloaded", attempts=1, mime=st.mime, size=st.size,
                          sha256=st.sha256, storage_key=st.storage_key, meta={}, created_at=now, downloaded_at=now)
    db.add(a)
    db.commit()
    return {"id": a.id, "url": a.url, "mime": st.mime, "size": st.size}


# ---------- администратор ----------

@router.get("/api/admin/check")
def admin_check(admin: auth.Admin = Depends(auth.require_admin)):
    """Проверка введённого токена администратора(страница "Подключения")."""
    return {"ok": True}
