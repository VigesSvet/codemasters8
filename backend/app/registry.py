"""Открытые данные(T-26…T-31): записи источников в БД, объекты, перечень источников, импорт xlsx/csv.

Отдельный слой, не смешанный с демо-ЖК: у записей и объектов нет риска, зон, "нужна проверка" и сигналов - это сведения
источника на момент сбора, а не выводы системы(ТЗ §7 "Достоверность", "Безопасность"). Сигнал по ключу объекта - 404.
Карточки ЖК по объектам реестра("r-<ключ>", origin "registry") создаёт app.demo - риск по ним не оценивается до
проверенных сведений(risk.assessed).
"""
from datetime import datetime
from typing import Any
from collections.abc import Iterable

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import importer, models, schemas
from .db import get_db
from .models import iso

router = APIRouter(tags=["sources"])

Obj = dict[str, Any]
SYSTEM = "Система"
DEMO = {"code": "demo", "name": "Демонстрационный набор (вымышленные ЖК)", "url": None,
        "note": "Вымышленные ЖК, застройщики, публикации и обращения для сценариев проверки (ТЗ §5). Только на них "
                "считаются риск, зоны и сигналы; совпадений с реальными организациями нет."}
NOT_SCORED = ("Объект открытых данных: риск, зона и сигналы для сведений из источников не рассчитываются — сигнал "
              "формируется только по карточке ЖК (раздел «Новостройки»)")


def _snake(key: str) -> str:
    return "".join("_" + ch.lower() if ch.isupper() else ch for ch in key)


# поле записи(camelCase, как в importer) → столбец SourceRecord
COLUMNS = {f: _snake(f) for f in importer.FIELDS}


def _record_dict(r: models.SourceRecord) -> Obj:
    return schemas.SourceRecordOut.model_validate(r).model_dump(by_alias=True)


def records(db: Session, key: str | None = None) -> list[Obj]:
    R = models.SourceRecord
    q = select(R).order_by(R.key, R.row, R.id)
    if key is not None:
        q = q.where(R.key == key)
    return [_record_dict(r) for r in db.scalars(q)]


def is_object(db: Session, key: str) -> bool:
    return db.scalar(select(models.SourceRecord.id).where(models.SourceRecord.key == key).limit(1)) is not None


def not_found(db: Session, cid: str, detail: str = "ЖК не найден") -> HTTPException:
    """404 для id, которого нет среди ЖК демо-реестра; для ключа объекта открытых данных - с объяснением(T-31)."""
    return HTTPException(404, NOT_SCORED if is_object(db, cid) else detail)


def _sorted(rs: Iterable[Obj]) -> list[Obj]:
    order = {s: i for i, s in enumerate(importer.SOURCE_ORDER)}
    return sorted(rs, key=lambda r: (order.get(r["source"], 9), r.get("row") or 0, r["id"]))


# ---------- загрузка записей: сид и импорт ----------

def _values(rec: Obj) -> Obj:
    out = {col: rec.get(f) for f, col in COLUMNS.items()}
    out["collected_at"] = datetime.fromisoformat(rec["collectedAt"]) if rec.get("collectedAt") else None
    return out


def _same(row: models.SourceRecord, values: Obj) -> bool:
    for col, v in values.items():
        cur = getattr(row, col)
        if col == "collected_at":
            if iso(cur) != iso(v):
                return False
        elif cur != v:
            return False
    return True


def upsert(db: Session, recs: Iterable[Obj], file: str | None, at: datetime) -> dict[str, int]:
    """Дедупликация по(source, sourceRecordId): новые - вставка, изменившиеся - обновление, прочие без изменений."""
    R = models.SourceRecord
    recs = list(recs)
    have = {(r.source, r.source_record_id): r for r in db.scalars(select(R))}
    n = {"created": 0, "updated": 0, "unchanged": 0}
    for rec in recs:
        values = _values(rec)
        meta = {"import_file": (file or "")[:255] or None, "row": rec.get("row"), "imported_at": at}
        row = have.get((rec["source"], rec["sourceRecordId"]))
        if row is None:
            db.add(R(origin="source", **values, **meta))
            n["created"] += 1
        elif _same(row, values):
            n["unchanged"] += 1
        else:
            for col, v in {**values, **meta}.items():
                setattr(row, col, v)
            n["updated"] += 1
    db.flush()
    return n


def _plural(n: int, one: str, few: str, many: str) -> str:
    tail, tens = n % 10, n % 100
    return one if tail == 1 and tens != 11 else few if 2 <= tail <= 4 and not 12 <= tens <= 14 else many


def seed(db: Session, data: Obj | None) -> models.AuditEvent | None:
    """Записи из shared/registry_ust_kamenogorsk.json(собраны импортёром) + событие журнала о загрузке."""
    if not data or not data.get("records"):
        return None
    meta = data.get("meta") or {}
    at = datetime.fromisoformat(meta["builtAt"]) if meta.get("builtAt") else models.utcnow()
    src = meta.get("builtFrom") or "shared/registry_ust_kamenogorsk.json"
    upsert(db, data["records"], src.rsplit("/", 1)[-1], at)
    n = len(data["records"])
    by = {s: k for s in importer.SOURCE_ORDER if (k := sum(r["source"] == s for r in data["records"]))}
    parts = "; ".join(f"{importer.SOURCES[s]['name']}: {k}" for s, k in by.items())
    collected = meta.get("collectedAt")
    what = (f"Загружены сведения открытых источников: {n} {_plural(n, 'запись', 'записи', 'записей')} ({parts}) из {src}"
            + (f", собраны {collected[8:10]}.{collected[5:7]}.{collected[:4]}" if collected else "")
            + ". Риск по ним не рассчитывается")
    e = models.AuditEvent(kind="data", who=SYSTEM, what=what, at=at)
    db.add(e)
    return e


# ---------- объекты ----------

def objects(db: Session) -> list[Obj]:
    return importer.group(records(db))


def _by_key(rs: Iterable[Obj]) -> dict[str, list[Obj]]:
    out: dict[str, list[Obj]] = {}
    for r in rs:
        out.setdefault(r["key"], []).append(r)
    return out


def registry(db: Session, q: str | None = None, with_coords: bool | None = None) -> Obj:
    rs = records(db)
    objs = importer.group(rs)
    if q and q.strip():
        by = _by_key(rs)
        objs = [o for o in objs if importer.matches(q, importer.search_text(o, by[o["key"]]))]
    out = importer.split(objs)
    return {"objects": out["objects"] if with_coords is not False else [],
            "withoutCoords": out["withoutCoords"] if with_coords is not True else []}


def sources(db: Session) -> list[Obj]:
    R = models.SourceRecord
    rows = db.execute(select(R.source, func.count(R.id), func.count(func.distinct(R.key)), func.max(R.collected_at),
                             func.max(R.provider_loaded_at)).group_by(R.source)).all()
    stat = {s: (n, k, c, p) for s, n, k, c, p in rows}
    out = [{"code": code, **importer.SOURCES[code], "records": stat[code][0], "objects": stat[code][1],
            "collectedAt": iso(stat[code][2]), "providerLoadedAt": stat[code][3]}
           for code in importer.SOURCE_ORDER if code in stat]
    count = lambda m: db.scalar(select(func.count()).select_from(m).where(m.origin == "demo"))
    counts = {"complexes": count(models.Complex), "developers": count(models.Developer), "ads": count(models.Ad),
              "appeals": count(models.Appeal)}
    if any(counts.values()):  # после python -m app.demo purge демо-набора нет - и строки о нём нет
        out.append({**DEMO, "records": counts["complexes"], "objects": counts["complexes"], "collectedAt": None,
                    "providerLoadedAt": None, "counts": counts})
    return out


# ---------- эндпоинты ----------

@router.get("/api/sources", response_model=list[schemas.SourceInfoOut], response_model_by_alias=True)
def list_sources(db: Session = Depends(get_db)):
    """Источники данных: что загружено, сколько записей, когда собрано; демо-набор - отдельной строкой.
    Автоматической синхронизации с реестрами нет - записи появляются только импортом файла."""
    return sources(db)


@router.get("/api/registry", response_model=schemas.RegistryOut, response_model_by_alias=True)
def list_registry(q: str | None = Query(None, max_length=200, description="Поиск: название, адрес, застройщик, БИН, "
                                                                               "номер документа, кадастровый номер"),
                  with_coords: bool | None = Query(None, alias="withCoords",
                                                      description="true — только с координатами, false — только без"),
                  db: Session = Depends(get_db)):
    """Объекты открытых данных(группы записей источников). Риска, зон и сигналов нет - это сведения источников."""
    return registry(db, q, with_coords)


@router.get("/api/registry/{key}", response_model=schemas.RegistryItemOut, response_model_by_alias=True)
def registry_item(key: str, db: Session = Depends(get_db)):
    rs = records(db, key)
    if not rs:
        raise HTTPException(404, "Объект открытых данных не найден")
    return {"object": importer.group(rs)[0], "records": _sorted(rs)}


def _author(*values: str | None) -> str:
    for v in values:
        if v is not None and "\x00" in v:
            raise RequestValidationError([{"type": "value_error", "loc": ("body", "author"), "msg": schemas.NUL_MSG,
                                           "input": v}])
        t = " ".join((v or "").split())
        if t:
            return t[:128]
    return SYSTEM


@router.post("/api/sources/import", response_model=schemas.ImportReportOut, response_model_by_alias=True,
             responses={413: {"description": "Файл больше 5 МБ"}, 415: {"description": "Не .xlsx и не .csv"},
                        422: {"description": "Файл повреждён или не в формате набора"}})
def import_sources(file: UploadFile = File(...), author: str | None = Form(None, max_length=512),
                   author_q: str | None = Query(None, alias="author", max_length=512), db: Session = Depends(get_db)):
    """Импорт xlsx/csv(формат docs/Усть-Каменогорск_объекты.xlsx, ≤ 5 МБ): тот же парсер, что у python -m app.importer;
    дедупликация по(источник, ID в источнике) - новые вставляются, изменившиеся обновляются. Отчёт - в ответе,
    событие - в журнале(kind data). Это загрузка файла, а не синхронизация с реестром."""
    who = _author(author, author_q)
    raw = file.file.read(importer.MAX_BYTES + 1)
    try:
        parsed = importer.parse(raw, file.filename or "", file.content_type)
    except importer.ImportFailed as e:
        raise HTTPException(e.status, e.detail) from None
    now = models.utcnow()
    try:
        n = upsert(db, parsed.records, parsed.file, now)
    except IntegrityError:  # параллельный импорт тех же записей
        db.rollback()
        raise HTTPException(409, "Эти записи сейчас импортирует другой запрос — повторите импорт через минуту") from None
    skipped = parsed.skipped + parsed.duplicates
    what = (f"Импорт: {n['created']} новых, {n['updated']} обновлено, {n['unchanged']} без изменений, {skipped} пропущено "
            f"— {parsed.file}")
    e = models.AuditEvent(kind="data", who=who, what=what, at=now)
    db.add(e)
    db.flush()
    objs = objects(db)
    total = db.scalar(select(func.count(models.SourceRecord.id)))
    db.commit()
    return {"file": parsed.file, "format": parsed.format, "read": parsed.read, **n, "skipped": parsed.skipped,
            "duplicates": parsed.duplicates, "errors": parsed.errors, "warnings": parsed.warnings, "records": total,
            "objects": len(objs), "withoutCoords": sum(o["lat"] is None for o in objs),
            "sources": {s: k for s in importer.SOURCE_ORDER if (k := sum(r["source"] == s for r in parsed.records))},
            "eventId": e.id}
