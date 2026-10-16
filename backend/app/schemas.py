from datetime import datetime
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, ValidationInfo,
                      field_validator, model_serializer)
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from .models import iso

Tri = Literal["yes", "no", "unknown"]
Zone = Literal["green", "yellow", "red"]
AdStatus = Literal["new", "needs_check", "in_review", "confirmed", "dismissed"]
AppealStatus = Literal["verified", "pending", "rejected"]
SignalStatus = Literal["new", "confirmed", "returned"]
FactorState = Literal["hit", "clear", "unknown"]
EvidenceKind = Literal["ad", "document", "photo", "appeal", "readiness", "deadline"]
AuditKind = Literal["system", "data", "status", "link", "comment", "request", "signal", "export", "ai"]
# Происхождение сведений (ТЗ §7 «Достоверность»): demo — вымышленный демо-набор, analyst — добавил аналитик,
# source — запись открытого источника
Origin = Literal["demo", "analyst", "source"]
SourceCode = Literal["homeportal", "homeportal_section", "ashyq"]
FundingMethod = Literal["guarantee", "mio", "bvu"]
TermsReview = Literal["accepted", "rejected"]
TermKind = Literal["booking", "deposit", "down_payment", "installment", "ddu", "direct_payment", "price", "investment"]
ReasonFeature = Literal["name", "translit", "address", "account", "phone", "history", "district"]
# Темы внутреннего запроса сведений по ЖК (POST /api/complexes/{id}/requests)
InfoTopic = Literal["basis", "readiness", "appeals", "list", "other"]

NUL_MSG = "недопустимый символ NUL (\\0)"


def no_nul(v: Any) -> Any:
    """NUL (\\x00) в тексте — 422: PostgreSQL не хранит его в text/varchar, иначе была бы ошибка БД (500)."""
    if isinstance(v, str) and "\x00" in v:
        raise PydanticCustomError("value_error", NUL_MSG)
    return v


# Обязательная строка: после strip не пустая, иначе 422
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128), AfterValidator(no_nul)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000), AfterValidator(no_nul)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000), AfterValidator(no_nul)]


def _blank_to_none(v: Any) -> Any:
    return None if isinstance(v, str) and not v.strip() else v


# Необязательные строки: пустая или из пробелов — то же, что не указана; длина — как у обязательных
OptName = Annotated[Optional[Name], BeforeValidator(_blank_to_none)]
OptComment = Annotated[Optional[Comment], BeforeValidator(_blank_to_none)]
OptNote = Annotated[Optional[Note], BeforeValidator(_blank_to_none)]
# Уверенность ИИ в привязке публикации к ЖК: 0…1, информативно — фактом привязку делает только подтверждение аналитика
Confidence = Annotated[float, Field(ge=0, le=1)]
# Текст объявления и распознанный скриншот: с запасом для любого поста, но с пределом — их анализирует ИИ (время анализа)
AD_TEXT_MAX = 20000
# Время из БД (datetime, в SQLite — наивное UTC) → ISO 8601 с UTC-смещением
IsoTime = Annotated[Optional[str], BeforeValidator(lambda v: iso(v) if isinstance(v, datetime) else v)]


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
    phones: List[str] = []
    origin: Origin = "demo"


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
    readiness: dict = {"declared": None, "confirmed": None}
    in_published_list: Tri
    cover: str
    photos: list
    idle_days: int
    origin: Origin = "demo"


class AppealOut(Camel):
    id: str
    complex_id: str
    date: str
    collective: bool
    applicants: int
    status: AppealStatus
    verified_at: Optional[str] = None
    subject: str
    source: str
    origin: Origin = "demo"


class AdIn(Camel):
    """Ручное добавление публикации. author — кто добавил (в журнал); без него — «Система».
    Длины строк — как у столбцов БД: на PostgreSQL более длинное значение было бы ошибкой БД (500), а не понятным 422."""

    id: Annotated[Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]],
                  BeforeValidator(_blank_to_none)] = None
    platform: str = Field(max_length=16)
    account: str = Field(max_length=255)
    url: str = Field("", max_length=512)
    date: Optional[str] = Field(None, max_length=32)
    text: str = Field(max_length=AD_TEXT_MAX)
    ocr: Optional[str] = Field(None, max_length=AD_TEXT_MAX)
    image: Optional[str] = Field(None, max_length=512)
    complex_id: Optional[str] = Field(None, max_length=32)
    match_confidence: Confidence = 0
    price: Optional[str] = Field(None, max_length=64)
    source: str = Field("manual", max_length=16)
    # Только статусы до решения: «На проверке», «Нарушение подтверждено», «Отклонено» — через PATCH /api/ads/{id}/status
    # (автор, основание, событие в журнале). Иначе подтверждённая при создании публикация давала бы R2/R3 без следа решения.
    status: Literal["new", "needs_check"] = "new"
    author: OptName = None
    # true — аналитик сам выбрал ЖК в форме: привязка сразу факт (linkConfirmedBy = author); иначе — гипотеза ИИ
    link_confirmed: bool = False

    @field_validator("*")
    @classmethod
    def _no_nul(cls, v: Any) -> Any:
        return no_nul(v)

    @field_validator("link_confirmed")
    @classmethod
    def _confirmed_needs_author(cls, v: bool, info: ValidationInfo) -> bool:
        if v and not info.data.get("complex_id"):
            raise PydanticCustomError("value_error", "подтвердить можно только выбранный ЖК (complexId)")
        if v and not info.data.get("author"):
            raise PydanticCustomError("value_error", "для подтверждённой привязки укажите автора (author)")
        return v


class AdOut(Camel):
    id: str
    platform: str
    account: str
    url: str = ""
    date: str
    text: str
    ocr: Optional[str] = None
    image: Optional[str] = None
    complex_id: Optional[str] = None
    match_confidence: float = 0
    link_confirmed_by: Optional[str] = None
    link_confirmed_at: IsoTime = None
    rejected_links: Annotated[List[str], BeforeValidator(lambda v: v or [])] = []
    terms_review: Optional[TermsReview] = None
    terms_review_by: Optional[str] = None
    terms_review_at: IsoTime = None
    price: Optional[str] = None
    source: str = "manual"
    status: AdStatus = "new"
    assignee: Optional[str] = None
    reach: Optional[int] = None
    origin: Origin = "demo"


class AdComplexIn(Camel):
    """Привязка публикации к ЖК аналитиком: complexId — ЖК (привязка — факт, подтвердил author) или null — снять привязку."""

    complex_id: Optional[Name]
    author: Name


class StatusIn(Camel):
    """Смена статуса публикации: автор обязателен; для «Нарушение подтверждено» — и основание решения (comment)."""

    status: AdStatus
    author: Name
    assignee: OptName = None
    comment: OptComment = Field(None, validate_default=True)

    @field_validator("comment")
    @classmethod
    def _basis_for_confirmed(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        if v is None and info.data.get("status") == "confirmed":
            raise PydanticCustomError("value_error", "для подтверждения нарушения укажите основание решения")
        return v


class CommentIn(Camel):
    author: Name
    text: Note


class InfoRequestIn(Camel):
    """Внутренний запрос сведений по ЖК: только запись в журнале объекта, во внешние органы не отправляется."""

    author: Name
    topics: List[InfoTopic] = Field(min_length=1, max_length=5)
    comment: OptNote = None


class AnalystOut(Camel):
    id: str
    name: str
    full_name: str
    short: str
    role: str


# ---------- риск ----------

class EvidenceItemOut(Camel):
    kind: EvidenceKind
    ref: Optional[str] = None
    label: Optional[str] = None
    quote: Optional[str] = None
    quote_from: Optional[Literal["text", "ocr"]] = None
    marker: Optional[str] = None
    source: Optional[str] = None
    date: Optional[str] = None
    url: Optional[str] = None
    value: Optional[Union[int, float, str]] = None

    @model_serializer(mode="wrap")
    def _drop_empty(self, handler) -> Dict[str, Any]:
        # как в движке: отсутствующие поля не выводятся (EvidenceItem в types.ts — все поля необязательные)
        return {k: v for k, v in handler(self).items() if v is not None}


class RiskFactorOut(Camel):
    code: str
    title: str
    points: int
    state: FactorState
    reason: str
    evidence: str
    items: List[EvidenceItemOut] = []


class ObservationOut(Camel):
    code: str
    title: str
    state: Literal["ok", "flag", "unknown"]
    text: str


class RiskOut(Camel):
    score: int
    zone: Zone
    needs_check: bool
    factors: List[RiskFactorOut]
    observations: List[ObservationOut]
    summary: str
    model_version: str


class RiskRowOut(RiskOut):
    complex_id: str


class AdSignalsOut(Camel):
    sale: List[str]
    funds: List[str]
    safe: List[str]


class AdScoreOut(Camel):
    score: int
    zone: Zone
    needs_check: bool
    factors: List[RiskFactorOut]
    signals: AdSignalsOut
    complex_id: Optional[str] = None
    match_confidence: float = 0


class AuditOut(Camel):
    id: int
    at: str
    who: str
    what: str
    kind: AuditKind
    ad_id: Optional[str] = None
    complex_id: Optional[str] = None
    signal_id: Optional[int] = None


# ---------- сигналы (модуль 05) ----------

class SignalAdOut(Camel):
    id: str
    platform: str
    account: str
    url: str
    date: str
    status: AdStatus
    match_confidence: float
    link_confirmed_by: Optional[str] = None
    link_confirmed_at: Optional[str] = None
    terms_review: Optional[TermsReview] = None
    text: str
    ocr: Optional[str] = None
    image: Optional[str] = None
    price: Optional[str] = None
    signals: AdSignalsOut


class SignalEvidenceOut(Camel):
    complex: ComplexOut
    developer: Optional[DeveloperOut] = None
    ads: List[SignalAdOut] = []
    appeals: List[AppealOut] = []
    photos: list = []
    documents: List[EvidenceItemOut] = []


class SignalSummaryOut(Camel):
    id: int
    code: str
    complex_id: str
    complex_name: str
    status: SignalStatus
    score: int
    zone: Zone
    needs_check: bool
    hits: List[str]
    created_at: str
    created_by: str
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None


class SignalOut(SignalSummaryOut):
    summary: str
    factors: List[RiskFactorOut]
    observations: List[ObservationOut]
    evidence: SignalEvidenceOut
    model_version: str
    decision_comment: Optional[str] = None
    file_name: str
    exported_at: Optional[str] = None
    journal: List[AuditOut] = []


class SignalCreateIn(Camel):
    complex_id: Name
    author: Name


class SignalDecisionIn(Camel):
    author: Name
    comment: Comment


# ---------- ИИ-анализ публикаций (ТЗ §6) ----------

def _drop_none(handler, self) -> Dict[str, Any]:
    return {k: v for k, v in handler(self).items() if v is not None}


class AnalyzeIn(Camel):
    """Что анализировать: текст объявления, распознанный скриншот, аккаунт, площадка, ссылка. В БД ничего не пишется."""

    text: str = Field("", max_length=AD_TEXT_MAX)
    ocr: Optional[str] = Field(None, max_length=AD_TEXT_MAX)
    account: Optional[str] = Field(None, max_length=255)
    platform: Optional[str] = Field(None, max_length=16)
    url: Optional[str] = Field(None, max_length=512)

    @field_validator("*")
    @classmethod
    def _no_nul(cls, v: Any) -> Any:
        return no_nul(v)


class TermOut(Camel):
    """Условие оплаты — гипотеза ИИ: вид, значения, дословная цитата, источник (текст или скриншот), уверенность."""

    kind: TermKind
    label: str
    amount: Optional[Union[int, float]] = None
    currency: Optional[Literal["KZT", "USD", "RUB"]] = None
    unit: Optional[Literal["m2"]] = None
    percent: Optional[Union[int, float]] = None
    months: Optional[Union[int, float]] = None
    quote: str
    from_: Literal["text", "ocr"] = Field(alias="from")
    confidence: float
    low: bool
    money: bool

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        return _drop_none(handler, self)


class ReasonOut(Camel):
    feature: ReasonFeature
    detail: str
    weight: float


class LinkHypothesisOut(Camel):
    complex_id: str
    complex_name: str
    confidence: float
    low: bool
    reasons: List[ReasonOut]
    quote: Optional[str] = None

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        return _drop_none(handler, self)


class AnalysisOut(Camel):
    engine: str
    terms: List[TermOut]
    hypotheses: List[LinkHypothesisOut]
    signals: AdSignalsOut
    phones: List[str]
    addresses: List[str]
    note: Optional[str] = None


class AdAnalysisOut(AnalysisOut):
    """Анализ сохранённой публикации: без отклонённых гипотез и с решениями аналитика."""

    terms_review: Optional[TermsReview] = None
    terms_review_by: Optional[str] = None
    terms_review_at: Optional[str] = None
    link_confirmed_by: Optional[str] = None
    link_confirmed_at: Optional[str] = None
    rejected_links: List[str] = []


class HypothesisIn(Camel):
    author: Name
    complex_id: Name


class HypothesisRejectIn(HypothesisIn):
    comment: OptNote = None


class TermsReviewIn(Camel):
    """Решение аналитика по условиям оплаты, найденным ИИ: rejected — «это не предложение внести деньги» (R2 не учитывается)."""

    author: Name
    decision: TermsReview
    comment: OptNote = None


class OcrOut(Camel):
    text: str
    engine: Literal["tesseract", "none"]
    confidence: Optional[float] = None
    languages: List[str] = []
    duration_ms: int
    version: Optional[str] = None
    preprocess: List[str] = []
    detail: Optional[str] = None

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        return _drop_none(handler, self)


class OcrStatusOut(Camel):
    available: bool
    engine: Literal["tesseract", "none"]
    version: Optional[str] = None
    languages: List[str] = []
    detail: Optional[str] = None

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        return _drop_none(handler, self)


class AiMetricsOut(Camel):
    terms_found: int
    terms_expected: int
    terms_precision: float
    link_top1: float
    link_cases: int
    false_positives: int


class AiEvalOut(Camel):
    engine: str
    cases: List[Dict[str, Any]]
    metrics: AiMetricsOut


# ---------- открытые данные (T-26…T-31): сведения источников, без риска и сигналов ----------

class SourceRecordOut(Camel):
    """Запись открытого источника: нормализованные поля строки и провенанс. Полей риска нет (T-31)."""

    id: int
    key: str
    origin: Literal["source"] = "source"
    source: SourceCode
    source_name: str
    source_section: Optional[str] = None
    source_url: Optional[str] = None
    source_record_id: str
    collected_at: IsoTime = None
    provider_loaded_at: Optional[str] = None
    imported_at: IsoTime = None
    import_file: Optional[str] = None
    row: Optional[int] = None
    name: str
    address: Optional[str] = None
    developer: Optional[str] = None
    developer_bin: Optional[str] = None
    agent: Optional[str] = None
    agent_bin: Optional[str] = None
    stage: Optional[str] = None
    funding_method: Optional[FundingMethod] = None
    funding_text: Optional[str] = None
    doc_number: Optional[str] = None
    doc_date: Optional[str] = None
    start: Optional[str] = None
    planned_end: Optional[str] = None
    act_number: Optional[str] = None
    act_date: Optional[str] = None
    readiness: Optional[float] = None
    report_period: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    cadastral: Optional[str] = None
    site: Optional[str] = None
    note: Optional[str] = None


class RegistryFlagsOut(Camel):
    source_section: Optional[str] = None  # раздел источника — как у источника, без оценки
    coords_far_km: Optional[int] = None  # координаты источника далеко от Усть-Каменогорска (км от центра)

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        return _drop_none(handler, self)


class RegistryObjectOut(Camel):
    """Объект открытых данных — группа записей источников по нормализованному названию. Риска, зоны, сигналов нет."""

    key: str
    origin: Literal["source"] = "source"
    name: str
    address: Optional[str] = None
    developer: Optional[str] = None
    developer_bin: Optional[str] = None
    agent: Optional[str] = None
    funding_methods: List[FundingMethod] = []
    stage: Optional[str] = None
    planned_end: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    records: int
    sources: List[SourceCode]
    collected_at: Optional[str] = None
    flags: RegistryFlagsOut = RegistryFlagsOut()


class RegistryOut(Camel):
    objects: List[RegistryObjectOut]
    without_coords: List[RegistryObjectOut]


class RegistryItemOut(Camel):
    object: RegistryObjectOut
    records: List[SourceRecordOut]


class DemoCountsOut(Camel):
    complexes: int
    developers: int
    ads: int
    appeals: int


class SourceInfoOut(Camel):
    code: Literal["homeportal", "homeportal_section", "ashyq", "demo"]
    name: str
    url: Optional[str] = None
    records: int
    objects: int
    collected_at: Optional[str] = None
    provider_loaded_at: Optional[str] = None
    note: str
    counts: Optional[DemoCountsOut] = None

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        out = handler(self)
        return out if out.get("counts") is not None else {k: v for k, v in out.items() if k != "counts"}


class ImportReportOut(Camel):
    file: str
    format: Literal["xlsx", "csv"]
    read: int
    created: int
    updated: int
    unchanged: int
    skipped: int
    duplicates: int
    errors: List[str] = []
    warnings: List[str] = []
    records: int
    objects: int
    without_coords: int
    sources: Dict[str, int] = {}
    event_id: int
