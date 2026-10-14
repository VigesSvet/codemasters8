import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas
from .db import Base, SessionLocal, engine, get_db
from .risk import match_complex, score_ad, score_complex

app = FastAPI(title="SAQ Qurylys API", version="0.1.0",
              description="Мониторинг рекламы долевого строительства и риск-скоринг ЖК (Усть-Каменогорск)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SEED = Path(__file__).with_name("seed_data.json")


def _snake(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        out["".join("_" + ch.lower() if ch.isupper() else ch for ch in k)] = v
    return out


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.scalar(select(models.Developer).limit(1)):
            return
        data = json.loads(SEED.read_text(encoding="utf-8"))
        db.add_all(models.Developer(**_snake(d)) for d in data["developers"])
        db.flush()
        for c in data["complexes"]:
            c = _snake(c)
            db.add(models.Complex(**c))
        db.flush()
        db.add_all(models.Ad(**_snake(a)) for a in data["ads"])
        db.commit()


@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}


@app.get("/api/developers", response_model=List[schemas.DeveloperOut], response_model_by_alias=True)
def developers(db: Session = Depends(get_db)):
    return db.scalars(select(models.Developer)).all()


@app.get("/api/complexes", response_model=List[schemas.ComplexOut], response_model_by_alias=True)
def complexes(zone: Optional[str] = None, db: Session = Depends(get_db)):
    items = db.scalars(select(models.Complex)).all()
    if zone:
        ads = db.scalars(select(models.Ad)).all()
        items = [c for c in items if score_complex(c, ads)["zone"] == zone]
    return items


@app.get("/api/complexes/{cid}", response_model=schemas.ComplexOut, response_model_by_alias=True)
def complex_one(cid: str, db: Session = Depends(get_db)):
    c = db.get(models.Complex, cid)
    if not c:
        raise HTTPException(404, "ЖК не найден")
    return c


@app.get("/api/complexes/{cid}/risk", response_model=schemas.RiskOut, response_model_by_alias=True)
def complex_risk(cid: str, db: Session = Depends(get_db)):
    c = db.get(models.Complex, cid)
    if not c:
        raise HTTPException(404, "ЖК не найден")
    return score_complex(c, db.scalars(select(models.Ad)).all())


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
    data["id"] = data.get("id") or f"m{uuid.uuid4().hex[:8]}"
    data["date"] = data.get("date") or datetime.now().isoformat(timespec="minutes")
    if not data.get("complex_id"):
        c = match_complex(f"{body.text} {body.ocr or ''}", db.scalars(select(models.Complex)).all())
        if c:
            data["complex_id"], data["match_confidence"] = c.id, 0.8
    ad = models.Ad(**data)
    db.add(ad)
    db.add(models.AuditEvent(ad_id=ad.id, who="Система", what="Публикация добавлена вручную"))
    db.commit()
    return ad


@app.get("/api/ads/{aid}/score", response_model=schemas.AdScoreOut, response_model_by_alias=True)
def ad_score(aid: str, db: Session = Depends(get_db)):
    ad = db.get(models.Ad, aid)
    if not ad:
        raise HTTPException(404, "Публикация не найдена")
    c = db.get(models.Complex, ad.complex_id) if ad.complex_id else None
    return {**score_ad(ad, c, db.scalars(select(models.Ad)).all()), "complex_id": ad.complex_id, "match_confidence": ad.match_confidence}


@app.patch("/api/ads/{aid}/status", response_model=schemas.AdOut, response_model_by_alias=True)
def set_status(aid: str, body: schemas.StatusIn, db: Session = Depends(get_db)):
    ad = db.get(models.Ad, aid)
    if not ad:
        raise HTTPException(404, "Публикация не найдена")
    ad.status = body.status
    if body.assignee:
        ad.assignee = body.assignee
    db.add(models.AuditEvent(ad_id=aid, who=body.assignee or "Аналитик", what=f"Статус → {body.status}. {body.comment or ''}".strip()))
    db.commit()
    return ad


@app.get("/api/ads/{aid}/audit", response_model=List[schemas.AuditOut], response_model_by_alias=True)
def ad_audit(aid: str, db: Session = Depends(get_db)):
    rows = db.scalars(select(models.AuditEvent).where(models.AuditEvent.ad_id == aid).order_by(models.AuditEvent.at)).all()
    return [{"at": r.at.isoformat(timespec="minutes"), "who": r.who, "what": r.what} for r in rows]


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
def stats(db: Session = Depends(get_db)):
    ads_all = db.scalars(select(models.Ad)).all()
    zones = {"red": 0, "yellow": 0, "green": 0}
    for c in db.scalars(select(models.Complex)).all():
        zones[score_complex(c, ads_all)["zone"]] += 1
    return {"zones": zones, "queue": sum(a.status in ("new", "needs_check") for a in ads_all), "ads": len(ads_all)}
