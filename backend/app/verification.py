"""Сверка сведений аналитиком по карточке ЖК: основание привлечения средств (гарантия ЕО / разрешение МИО / БВУ),
опубликованный перечень ГАСК (O1), готовность (R5), обращения граждан (R4) и «обращений нет — сверено», снимки площадки
(R6). Это действия аналитика, а не администратора: автор — имя вошедшего сотрудника (HumanName, «Система» — 422),
каждая запись — событие журнала объекта вида «данные» от его имени (кто, что, когда, источник). Риск (app.risk) и
«Проверка по рекламе» (app.screening) пересчитываются из этих фактов при следующем чтении — ответ маршрута уже их несёт.

Где хранится (поля ЖК, JSON):
- basis[документ] — «yes» / «no» / «unknown»; basis[guaranteeRef|mioRef|bank] — номер и дата документа (или банк) от
  аналитика, вместо справки реестра «… Аналитиком не проверено»; basis.sources[документ] — {source, url, checkedAt,
  checkedBy, ref?, comment?}; общие basis.source / basis.checkedAt — последняя сверка (их читают интерфейс, журнал сида,
  PDF). «Нет сведений» снимает сверку документа (sources[документ] удаляется);
- in_published_list + published_list_check {value, source, url, checkedAt, checkedBy, comment?};
- readiness[declared|confirmed] — {value, source, date, by, at, comment?}; подтверждённая — и progress (готовность, %);
- appeals (таблица) — status pending (внесено, ждёт проверки) → verified | rejected; appeals_check {source, checkedAt,
  checkedBy, comment?} — «обращений нет — сверено» (R4 у карточек реестра и рекламы: без неё пустой список — «нужна
  проверка», а не «обращений нет»);
- photos — {date, src: /api/media/{id}, source, activity: есть | нет | нет сведений, note?, mediaId, by, at}; файл — в
  хранилище (app.media) записью MediaAsset (origin_ref «site», без публикации; ручные загрузки «upload» уборка не
  трогает), те же пределы, что у скриншота (10 МБ, только изображения, 50 файлов / 200 МБ в час на все ручные загрузки).
  У ЖК не из демо-набора idle_days пересчитывается по снимкам: от первого до последнего снимка хвостовой серии без
  активности или с низкой (снимки «нет сведений» серию не прерывают); последний снимок с активностью — 0.

Запись ЖК — под блокировкой строки (SELECT … FOR UPDATE на PostgreSQL): две одновременные сверки разных документов не
теряют друг друга. Повтор той же сверки без комментария журнал не засоряет.
"""
import math
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import media, models, registry, schemas, screening
from .db import get_db
from .risk_model import MODEL, T

router = APIRouter(tags=["verification"])

Obj = Dict[str, Any]
DOCS = {d["key"]: d for d in MODEL["documents"]}  # guarantee | mio | bvu → {label, refField}
DOC_NAMES = {"guarantee": "гарантия ЕО (КЖК)", "mio": "разрешение МИО", "bvu": "участие банка второго уровня"}
TRI = {"yes": "есть", "no": "нет", "unknown": "нет сведений"}
ACTIVITY = {"active": "есть", "idle": "нет", "unknown": "нет сведений"}  # активность снимка → как в снимках сида
READINESS = {"declared": "Заявленная", "confirmed": "Подтверждённая"}
SITE = "site"  # origin_ref и role файла снимка площадки
APPEAL_NOT_FOUND = "Обращение не найдено"


# ---------- помощники ----------

def _fmt(d: Any) -> str:
    s = d.isoformat() if isinstance(d, date) else str(d or "")
    return f"{s[8:10]}.{s[5:7]}.{s[:4]}" if len(s) >= 10 else s


def _num(x: float) -> Any:
    return int(x) if float(x).is_integer() else x


def _then(text: str, comment: Optional[str]) -> str:
    return f"{text}{'' if text.endswith('.') else '.'} Комментарий: {comment}" if comment else text


def _complex(db: Session, cid: str, *, lock: bool = False) -> models.Complex:
    """ЖК (для записи — под блокировкой строки и с перечитанными полями); ключ объекта открытых данных — 404 с
    объяснением, как у остальных маршрутов ЖК."""
    c = db.get(models.Complex, cid, with_for_update=lock or None, populate_existing=lock)
    if c is None:
        raise registry.not_found(db, cid)
    return c


def _event(db: Session, who: str, what: str, cid: str) -> models.AuditEvent:
    e = models.AuditEvent(kind="data", who=who, what=what, at=models.utcnow(), complex_id=cid)
    db.add(e)
    return e


def _row(db: Session, c: models.Complex) -> Obj:
    """ЖК в форме GET /api/complexes/{id}: с основанием по открытым данным, «Проверкой по рекламе» и подсказками."""
    return screening.complex_rows(db, [c], screening.compute(db))[0]


def _commit_row(db: Session, cid: str) -> Obj:
    db.commit()
    return _row(db, _complex(db, cid))


def _check(source: Optional[str], url: Optional[str], checked: str, who: str, comment: Optional[str], **extra) -> Obj:
    out = {**extra, "source": source, "url": url, "checkedAt": checked, "checkedBy": who}
    if comment:
        out["comment"] = comment
    return out


def _src_text(source: str, url: Optional[str]) -> str:
    return f"источник: {source}" + (f" ({url})" if url else "")


# ---------- основание ----------

def doc_view(c: models.Complex, suggest: Optional[Obj]) -> List[Obj]:
    """Документы основания: значение, сверка (кто, когда, где), подсказка открытого реестра."""
    b = c.basis or {}
    own = b.get("sources") or {}
    out = []
    for key, d in DOCS.items():
        value = b.get(key, "unknown")
        p = screening.analyst_checks(b).get(key)
        rec = own.get(key) or {}
        out.append({"doc": key, "label": d["label"], "value": value, "ref": b.get(d["refField"]) or None,
                    "source": p["source"] if p else None, "url": p["url"] if p else None,
                    "checkedAt": p["checkedAt"] if p else None, "checkedBy": rec.get("checkedBy") if p else None,
                    "comment": rec.get("comment") if p else None, "checked": p is not None,
                    "suggestion": (suggest or {}).get(key)})
    return out


@router.get("/api/complexes/{cid}/basis", response_model=schemas.BasisViewOut, response_model_by_alias=True)
def basis_view(cid: str, db: Session = Depends(get_db)):
    """Основание ЖК по документам и перечень ГАСК: что сверено (кто, когда, где) и что называет открытый реестр
    (suggestion — номер и дата документа, источник, ссылка: «Подтвердить — совпадает с источником» в один клик)."""
    c = _complex(db, cid)
    key, rs = screening.registry_groups(db, [c])[c.id]
    docs = doc_view(c, screening.suggestions(rs) if rs else None)
    pl = c.published_list_check or {}
    listed = {"value": c.in_published_list or "unknown", "source": pl.get("source"), "url": pl.get("url"),
              "checkedAt": pl.get("checkedAt"), "checkedBy": pl.get("checkedBy"), "comment": pl.get("comment")}
    checked = sum(d["checked"] for d in docs) + int(listed["value"] in ("yes", "no") and bool(pl.get("source")))
    return {"complexId": c.id, "documents": docs, "publishedList": listed, "checked": checked, "total": len(docs) + 1}


@router.post("/api/complexes/{cid}/basis", response_model=schemas.ComplexListOut, response_model_by_alias=True)
def verify_basis(cid: str, body: schemas.BasisCheckIn, db: Session = Depends(get_db)):
    """Сверка документа основания аналитиком: «есть» / «нет» с источником (обязателен), ссылкой, номером и датой документа
    (или банком), датой сверки (по умолчанию сегодня, не позже сегодня); «нет сведений» — снять сверку. «Есть» по любому
    документу — основание в «Проверке по рекламе» (permitted, сверка аналитика); «нет» по гарантии ЕО и разрешению МИО —
    not_permitted и R1. Риск карточки реестра начинает оцениваться (assessed) с первого сверенного документа."""
    c = _complex(db, cid, lock=True)
    before = dict(c.basis or {})
    b = {**before, "sources": dict(before.get("sources") or {})}
    key, ref_field, name = body.doc, DOCS[body.doc]["refField"], DOC_NAMES[body.doc]
    when = (body.checked_at or schemas.today_local()).isoformat()
    if body.value == "unknown":
        b[key] = "unknown"
        b["sources"].pop(key, None)
        if body.ref is not None:
            b[ref_field] = body.ref
        what = f"Сверка основания снята: {name} — нет сведений"
        if not b["sources"] and "sources" not in before:
            del b["sources"]
    else:
        extra = {"ref": body.ref} if body.ref else {}
        b[key] = body.value
        b["sources"][key] = _check(body.source, body.source_url, when, body.author, body.comment, **extra)
        # номер документа от аналитика заменяет справку реестра «… Аналитиком не проверено» (пусто — номера нет);
        # повторный импорт реестра дополняет только отсутствующие поля и эту запись не вернёт
        b[ref_field] = body.ref or ""
        b["source"], b["checkedAt"] = body.source, when
        what = (f"Основание сверено: {name} — {TRI[body.value]}" + (f" ({body.ref})" if body.ref else "")
                + f", {_src_text(body.source, body.source_url)}, сверено {_fmt(when)}")
    if b == before and not body.comment:
        return _row(db, c)
    c.basis = b
    _event(db, body.author, _then(what, body.comment), c.id)
    return _commit_row(db, c.id)


@router.post("/api/complexes/{cid}/published-list", response_model=schemas.ComplexListOut, response_model_by_alias=True)
def verify_published_list(cid: str, body: schemas.PublishedListIn, db: Session = Depends(get_db)):
    """Сверка с опубликованным перечнем ГАСК (наблюдение O1, вне балла): есть / нет с источником; «нет сведений» — снять."""
    c = _complex(db, cid, lock=True)
    before = (c.in_published_list, c.published_list_check)
    when = (body.checked_at or schemas.today_local()).isoformat()
    if body.value == "unknown":
        value, check, what = "unknown", None, "Сверка с перечнем ГАСК снята: нет сведений"
    else:
        value = body.value
        check = _check(body.source, body.source_url, when, body.author, body.comment, value=body.value)
        what = (f"Перечень ГАСК сверен: объект {'есть' if body.value == 'yes' else 'отсутствует'} в опубликованном "
                f"перечне, {_src_text(body.source, body.source_url)}, сверено {_fmt(when)}")
    if (value, check) == before and not body.comment:
        return _row(db, c)
    c.in_published_list, c.published_list_check = value, check
    _event(db, body.author, _then(what, body.comment), c.id)
    return _commit_row(db, c.id)


# ---------- готовность ----------

@router.post("/api/complexes/{cid}/readiness", response_model=schemas.ComplexListOut, response_model_by_alias=True)
def set_readiness(cid: str, body: schemas.ReadinessIn, db: Session = Depends(get_db)):
    """Готовность (R5): заявленная (сайт, реклама застройщика) или подтверждённая (акт осмотра, фото-мониторинг), 0–100 %,
    с источником и датой. Подтверждённая с источником и датой — проверенные сведения (риск карточки реестра оценивается)
    и готовность ЖК (progress)."""
    c = _complex(db, cid, lock=True)
    r = {"declared": None, "confirmed": None, **(c.readiness or {})}
    value = _num(body.value)
    rec = {"value": value, "source": body.source, "date": body.date.isoformat(), "by": body.author,
           "at": models.iso(models.utcnow())}
    if body.comment:
        rec["comment"] = body.comment
    old = r.get(body.kind) or {}
    if (not body.comment and {k: old.get(k) for k in ("value", "source", "date")}
            == {k: rec[k] for k in ("value", "source", "date")}):
        return _row(db, c)
    r[body.kind] = rec
    c.readiness = r
    if body.kind == "confirmed":
        c.progress = int(math.floor(body.value + 0.5))
    what = f"{READINESS[body.kind]} готовность {value}% — {body.source}, на {_fmt(body.date)}"
    _event(db, body.author, _then(what, body.comment), c.id)
    return _commit_row(db, c.id)


# ---------- обращения ----------

def _applicants(n: int) -> str:
    tail, tens = n % 10, n % 100
    word = "заявитель" if tail == 1 and tens != 11 else "заявителя" if 2 <= tail <= 4 and not 12 <= tens <= 14 else "заявителей"
    return f"{n} {word}"


@router.post("/api/complexes/{cid}/appeals", response_model=schemas.AppealOut, response_model_by_alias=True,
             status_code=201)
def add_appeal(cid: str, body: schemas.AppealIn, db: Session = Depends(get_db)):
    """Обращение граждан, внесённое аналитиком: ждёт проверки (status pending — «на проверке»). Коллективное
    непроверенное — R4 «нужна проверка»; проверенное (POST /api/appeals/{id}/verify) — R4."""
    c = _complex(db, cid)
    a = models.Appeal(id=f"ap{uuid.uuid4().hex[:12]}", complex_id=c.id, date=body.date.isoformat(),
                      collective=body.collective, applicants=body.applicants, status="pending", verified_at=None,
                      subject=body.subject, source=body.source, origin="analyst", comment=body.comment,
                      created_by=body.author, created_at=models.utcnow())
    db.add(a)
    head = (f"Внесено коллективное обращение ({_applicants(body.applicants)})" if body.collective
            else f"Внесено обращение ({_applicants(body.applicants)})")
    _event(db, body.author, _then(f"{head} от {_fmt(body.date)}: {body.subject} — {body.source}. Ожидает проверки",
                                  body.comment), c.id)
    db.commit()
    db.refresh(a)
    return a


def _decide_appeal(aid: str, status: str, body: schemas.AppealDecisionIn, db: Session) -> models.Appeal:
    a = db.get(models.Appeal, aid)
    if a is None:
        raise HTTPException(404, APPEAL_NOT_FOUND)
    if a.status == status and not body.comment:
        return a
    old = a.status
    A = models.Appeal
    today = schemas.today_local().isoformat()
    res = db.execute(update(A).where(A.id == aid, A.status == old)
                     .values(status=status, verified_at=today, decided_by=body.author, decision_comment=body.comment)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:  # решение уже принял другой аналитик
        db.rollback()
        db.refresh(a)
        names = {"verified": "подтверждено", "rejected": "отклонено", "pending": "на проверке"}
        raise HTTPException(409, f"Обращение уже изменили: сейчас «{names.get(a.status, a.status)}»"
                                 f"{f' ({a.decided_by})' if a.decided_by else ''}. Обновите страницу и повторите решение")
    what = ("Обращение проверено: подтверждено" if status == "verified" else "Обращение отклонено после проверки")
    if old in ("verified", "rejected") and old != status:
        what += " (решение изменено)"
    _event(db, body.author, _then(f"{what} — {a.subject}", body.comment), a.complex_id)
    db.commit()
    db.refresh(a)
    return a


@router.post("/api/appeals/{aid}/verify", response_model=schemas.AppealOut, response_model_by_alias=True,
             responses={404: {"description": APPEAL_NOT_FOUND}, 409: {"description": "Решение уже принял другой аналитик"}})
def verify_appeal(aid: str, body: schemas.AppealDecisionIn, db: Session = Depends(get_db)):
    """Обращение проверено и подтверждено: коллективное даёт R4; любое проверенное — проверенные сведения (риск
    карточки реестра оценивается)."""
    return _decide_appeal(aid, "verified", body, db)


@router.post("/api/appeals/{aid}/reject", response_model=schemas.AppealOut, response_model_by_alias=True,
             responses={404: {"description": APPEAL_NOT_FOUND}, 409: {"description": "Решение уже принял другой аналитик"}})
def reject_appeal(aid: str, body: schemas.AppealDecisionIn, db: Session = Depends(get_db)):
    """Обращение отклонено после проверки: в R4 не учитывается."""
    return _decide_appeal(aid, "rejected", body, db)


@router.post("/api/complexes/{cid}/appeals-check", response_model=schemas.ComplexListOut, response_model_by_alias=True)
def appeals_check(cid: str, body: schemas.AppealsCheckIn, db: Session = Depends(get_db)):
    """«Обращений нет — сверено»: где проверено (ДЭР, ГАСК, eOtinish) и когда. У карточки реестра и рекламы R4 без неё —
    «нужна проверка», с ней — «коллективных обращений нет» (если внесённых непроверенных или проверенных нет)."""
    c = _complex(db, cid, lock=True)
    when = (body.checked_at or schemas.today_local()).isoformat()
    check = {"source": body.source, "checkedAt": when, "checkedBy": body.author}
    if body.comment:
        check["comment"] = body.comment
    old = c.appeals_check or {}
    if not body.comment and {k: old.get(k) for k in check} == check:
        return _row(db, c)
    c.appeals_check = check
    _event(db, body.author, _then(f"Обращений по объекту нет — сверено: источник: {body.source}, сверено {_fmt(when)}",
                                  body.comment), c.id)
    return _commit_row(db, c.id)


# ---------- снимки площадки ----------

def idle_days(photos: List[Obj]) -> int:
    """Простой по снимкам: от первого до последнего снимка хвостовой серии (по дате) без активности или с низкой;
    снимки «нет сведений» серию не прерывают; последний информативный снимок с активностью — 0."""
    run: List[str] = []
    for p in sorted((p for p in photos if p.get("date")), key=lambda p: p["date"], reverse=True):
        act = p.get("activity")
        if act in T["idleActivities"]:
            run.append(p["date"])
        elif act == "есть":
            break
    if len(run) < 2:
        return 0
    return (date.fromisoformat(run[0][:10]) - date.fromisoformat(run[-1][:10])).days


def _form_errors(e: ValidationError) -> RequestValidationError:
    return RequestValidationError([{**err, "loc": ("body", *err["loc"])} for err in e.errors(include_url=False)])


@router.post("/api/complexes/{cid}/photos", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             status_code=201,
             responses={413: {"description": "Файл больше 10 МБ"}, 415: {"description": "Не изображение"},
                        429: {"description": "Слишком много загрузок"}, 507: {"description": "Мало места"}})
def add_photo(cid: str, file: UploadFile = File(...), author: Optional[str] = Form(None),
              date_: Optional[str] = Form(None, alias="date"), source: Optional[str] = Form(None),
              activity: Optional[str] = Form(None), note: Optional[str] = Form(None), db: Session = Depends(get_db)):
    """Снимок площадки (multipart: file, author, date, source, activity: active | idle | unknown, note): файл — в
    хранилище (те же пределы, что у скриншота: 10 МБ, JPEG / PNG / WEBP / GIF по сигнатуре, часовой лимит ручных
    загрузок), запись — в снимках ЖК (src /api/media/{id}). Датированный снимок с источником — проверенные сведения (риск
    карточки реестра оценивается); серия снимков без активности подтверждает простой (R6)."""
    fields = {"author": author, "date": date_, "source": source, "activity": activity, "note": note}
    try:  # поля формы проверяет схема — все ошибки сразу и по-русски, как у JSON-маршрутов; пустое поле — «не указано»
        body = schemas.SitePhotoIn.model_validate({k: v for k, v in fields.items() if v is not None})
    except ValidationError as e:
        raise _form_errors(e) from None
    _complex(db, cid)
    data = file.file.read(media.UPLOAD_MAX + 1)
    if len(data) > media.UPLOAD_MAX:
        raise HTTPException(413, f"Файл больше {media.UPLOAD_MAX // media.MB} МБ")
    now = models.utcnow()
    try:
        media.check_upload_budget(db, now, len(data))
        st = media.store_bytes(data, allowed=media.IMAGE_TYPES)
    except media.MediaRejected as e:
        raise HTTPException(e.status, e.detail) from None
    asset = models.MediaAsset(id=uuid.uuid4().hex, ad_id=None, item_id=None, kind="image", role=SITE, origin_ref=SITE,
                              status="downloaded", attempts=1, mime=st.mime, size=st.size, sha256=st.sha256,
                              storage_key=st.storage_key, meta={"complexId": cid}, created_at=now, downloaded_at=now)
    db.add(asset)
    c = _complex(db, cid, lock=True)
    photo = {"date": body.date.isoformat(), "src": asset.url, "source": body.source, "activity": ACTIVITY[body.activity],
             "mediaId": asset.id, "by": body.author, "at": models.iso(now)}
    if body.note:
        photo["note"] = body.note
    photos = sorted([*(c.photos or []), photo], key=lambda p: p.get("date") or "")
    c.photos = photos
    what = f"Снимок площадки от {_fmt(body.date)} ({body.source}): активность — {photo['activity']}"
    if c.origin != "demo":
        idle = idle_days(photos)
        if idle != (c.idle_days or 0):
            what += f"; простой по снимкам — {idle} дн." if idle else "; простоя по снимкам нет"
        c.idle_days = idle
    _event(db, body.author, f"{what}. {body.note}" if body.note else what, c.id)
    return _commit_row(db, c.id)
