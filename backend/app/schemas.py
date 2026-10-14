from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

Tri = Literal["yes", "no", "unknown"]
AdStatus = Literal["new", "needs_check", "in_review", "confirmed", "dismissed"]


class Camel(BaseModel):
    """JSON в camelCase — совпадает с типами фронтенда (src/data/types.ts)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class DeveloperOut(Camel):
    id: str
    name: str
    bin: str
    director: str
    registered: str
    objects: int
    unfinished: int
    site: Optional[str] = None
    instagram: Optional[str] = None


class ComplexOut(Camel):
    id: str
    name: str
    developer_id: str
    address: str
    district: str
    lng: float
    lat: float
    stage: str
    progress: int
    floors: int
    flats: int
    start: str
    planned_end: str
    basis: dict
    in_published_list: Tri
    cover: str
    photos: list
    idle_days: int


class AdIn(Camel):
    id: Optional[str] = None
    platform: str
    account: str
    url: str = ""
    date: Optional[str] = None
    text: str
    ocr: Optional[str] = None
    image: Optional[str] = None
    complex_id: Optional[str] = None
    match_confidence: float = 0
    price: Optional[str] = None
    source: str = "manual"
    status: AdStatus = "new"


class AdOut(AdIn):
    id: str
    date: str
    assignee: Optional[str] = None
    reach: Optional[int] = None


class StatusIn(Camel):
    status: AdStatus
    assignee: Optional[str] = None
    comment: Optional[str] = None


class RiskFactor(Camel):
    code: str
    title: str
    points: int
    state: Literal["hit", "clear", "unknown"]
    evidence: str


class RiskOut(Camel):
    score: int
    zone: Literal["green", "yellow", "red"]
    needs_check: bool
    factors: List[RiskFactor]
    summary: str


class AdScoreOut(Camel):
    score: int
    zone: Literal["green", "yellow", "red"]
    sale: List[str]
    funds: List[str]
    safe: List[str]
    complex_id: Optional[str] = None
    match_confidence: float = 0


class AuditOut(Camel):
    at: str
    who: str
    what: str
