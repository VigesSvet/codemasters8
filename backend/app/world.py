"""«Мир» для движка риска: ORM → camelCase-словари в форме API (как shared/seed_data.json и types.ts).

Движок (app.risk) работает только со словарями; здесь — загрузка данных из БД и вызовы score_complex / score_ad.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas
from .ai.matching import Catalog, build_catalog
from .models import iso
from .risk import score_ad, score_complex

Obj = Dict[str, Any]


def by_id(rows):
    """Естественный порядок id: c1, c2, …, c10 (как в сиде), а не c1, c10, c11."""
    return sorted(rows, key=lambda r: (len(r.id), r.id))


def _out(schema, row) -> Obj:
    return schema.model_validate(row).model_dump(by_alias=True)


def complex_dict(c: models.Complex) -> Obj:
    return _out(schemas.ComplexOut, c)


def developer_dict(d: Optional[models.Developer]) -> Optional[Obj]:
    return _out(schemas.DeveloperOut, d) if d is not None else None


def ad_dict(a: models.Ad) -> Obj:
    return _out(schemas.AdOut, a)


def appeal_dict(a: models.Appeal) -> Obj:
    return _out(schemas.AppealOut, a)


@dataclass
class World:
    complexes: List[Obj]
    developers: Dict[str, Obj]
    ads: List[Obj]
    appeals: List[Obj]

    def risk(self, c: Obj, as_of: Optional[date] = None) -> Obj:
        return score_complex(c, self.developers.get(c["developerId"]), self.ads, self.appeals, as_of)


def load(db: Session) -> World:
    return World(
        complexes=[complex_dict(c) for c in by_id(db.scalars(select(models.Complex)))],
        developers={d.id: developer_dict(d) for d in db.scalars(select(models.Developer))},
        ads=[ad_dict(a) for a in db.scalars(select(models.Ad))],
        appeals=[appeal_dict(a) for a in db.scalars(select(models.Appeal))],
    )


def load_complex(db: Session, cid: str) -> Optional[World]:
    """Мир одного ЖК: сам объект, застройщик, его публикации и обращения (движок всё равно фильтрует по complexId)."""
    c = db.get(models.Complex, cid)
    if c is None:
        return None
    d = db.get(models.Developer, c.developer_id)
    return World(
        complexes=[complex_dict(c)],
        developers={d.id: developer_dict(d)} if d else {},
        ads=[ad_dict(a) for a in db.scalars(select(models.Ad).where(models.Ad.complex_id == cid))],
        appeals=[appeal_dict(a) for a in db.scalars(select(models.Appeal).where(models.Appeal.complex_id == cid))],
    )


def risk_for(db: Session, cid: str, as_of: Optional[date] = None) -> Optional[Obj]:
    """Риск одного ЖК или None, если ЖК нет."""
    w = load_complex(db, cid)
    return w.risk(w.complexes[0], as_of) if w else None


def risk_all(db: Session, as_of: Optional[date] = None) -> List[Obj]:
    """[{complexId, ...RiskResult}] по всем ЖК — одним запросом для списков на фронте."""
    w = load(db)
    return [{"complexId": c["id"], **w.risk(c, as_of)} for c in w.complexes]


def catalog(db: Session, exclude_ad: Optional[str] = None) -> Catalog:
    """Справочник для ИИ-сопоставления из БД: ЖК, застройщики, история аккаунтов (подтверждённые привязки, кроме exclude_ad)."""
    w = load(db)
    return build_catalog(w.complexes, w.developers.values(), w.ads, exclude_ad)


def ad_score(db: Session, ad: models.Ad) -> Obj:
    c = db.get(models.Complex, ad.complex_id) if ad.complex_id else None
    return {**score_ad(ad_dict(ad), complex_dict(c) if c else None),
            "complexId": ad.complex_id, "matchConfidence": ad.match_confidence}


# ---------- журнал ----------

def audit_dict(e: models.AuditEvent) -> Obj:
    return {"id": e.id, "at": iso(e.at), "who": e.who, "what": e.what, "kind": e.kind,
            "adId": e.ad_id, "complexId": e.complex_id, "signalId": e.signal_id}


def audit(db: Session, *where, limit: Optional[int] = None) -> List[Obj]:
    """События журнала по возрастанию времени (at, id); limit — последние N, отданные тоже по возрастанию."""
    E = models.AuditEvent
    q = select(E).where(*where)
    if limit is None:
        rows = list(db.scalars(q.order_by(E.at, E.id)))
    else:
        rows = list(db.scalars(q.order_by(E.at.desc(), E.id.desc()).limit(limit)))[::-1]
    return [audit_dict(e) for e in rows]
