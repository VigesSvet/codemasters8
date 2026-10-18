import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, ValidationInfo,
                      field_validator, model_serializer, model_validator)
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
# source — запись открытого источника, collected — собрано сборщиком или прислано жителем через бота
Origin = Literal["demo", "analyst", "source", "collected"]
SourceCode = Literal["homeportal", "homeportal_section", "ashyq"]
FundingMethod = Literal["guarantee", "mio", "bvu"]
TermsReview = Literal["accepted", "rejected"]
TermKind = Literal["booking", "deposit", "down_payment", "installment", "ddu", "direct_payment", "price", "investment"]
ReasonFeature = Literal["name", "translit", "address", "account", "phone", "history", "district"]
# Темы внутреннего запроса сведений по ЖК (POST /api/complexes/{id}/requests)
InfoTopic = Literal["basis", "readiness", "appeals", "list", "other"]
# Сбор публикаций (integration-spec §10–§11): площадки приёма через API, статусы подключений и запусков
IntakePlatform = Literal["instagram", "telegram", "tiktok", "krisha", "olx", "facebook"]
ConnectorStatus = Literal["not_configured", "unchecked", "checking", "queued", "running", "ready", "working", "partial",
                          "waiting", "off", "error", "expired", "denied", "access_check", "rate_limited"]
RunStatus = Literal["queued", "running", "succeeded", "partial", "failed", "access_check", "rate_limited",
                    "not_configured", "cancelled"]
SYSTEM_AUTHOR = "Система"  # автор автоматических записей журнала; человеку это имя не дать (HumanName)

NUL_MSG = "недопустимый символ NUL (\\0)"


def no_nul(v: Any) -> Any:
    """NUL (\\x00) в тексте — 422: PostgreSQL не хранит его в text/varchar, иначе была бы ошибка БД (500)."""
    if isinstance(v, str) and "\x00" in v:
        raise PydanticCustomError("value_error", NUL_MSG)
    return v


# Обязательная строка: после strip не пустая, иначе 422
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128), AfterValidator(no_nul)]


def _not_system(v: str) -> str:
    if v.strip().casefold() == SYSTEM_AUTHOR.casefold():
        raise PydanticCustomError("value_error", "Имя «Система» зарезервировано за автоматическими записями")
    return v


# Автор действия на новых маршрутах (подключения, геопроверки, OLX): «Система» — только у автоматических записей
HumanName = Annotated[Name, AfterValidator(_not_system)]
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


class MediaRefOut(Camel):
    """Файл публикации в хранилище: адрес /api/media/{id} (только загруженные изображения и видео текущей версии)."""

    id: str
    kind: Literal["image", "video"]
    mime: Optional[str] = None
    url: str = ""
    role: Optional[str] = None


class ProcessingOut(Camel):
    """Состояние анализа собранной публикации: очередь, выполняется, готово, не выполнен (этап и причина)."""

    status: Literal["queued", "running", "done", "failed"]
    version: int
    analyzed_version: Optional[int] = None
    stage: Optional[str] = None
    error: Optional[str] = None


# Анализ ИИ (Claude, app.llm): виды признаков продажи и уверенность в кандидате ЖК — как в схеме ответа модели
LlmSignalType = Literal["sale", "presale", "booking", "installment", "down_payment", "ddu", "price", "discount",
                        "keys_handover", "other"]
LlmConfidence = Literal["low", "medium", "high"]


class LlmSignalOut(Camel):
    type: LlmSignalType
    quote: str  # дословно из текста публикации или OCR (сверено на сервере)
    source: Literal["text", "ocr"] = "text"


class LlmAmountOut(Camel):
    value: float
    currency: str
    kind: str
    quote: str
    source: Literal["text", "ocr"] = "text"


class LlmCandidateOut(Camel):
    """Кандидат ЖК от модели — гипотеза: привязку делает только аналитик."""

    complex_id: str
    complex_name: str  # из справочника, отправленного модели, — не из ответа модели
    confidence: LlmConfidence
    quote: str
    source: Literal["text", "ocr"] = "text"


class LlmOut(Camel):
    """Последний анализ ИИ (Claude) публикации. Всё — гипотезы для аналитика: статус, привязку и вердикт модель не
    задаёт. failed / refused / skipped — «анализ ИИ не выполнен», а не «риск отсутствует»."""

    status: Literal["done", "refused", "failed", "skipped"]
    provider: Literal["anthropic"] = "anthropic"
    model: str
    served_model: Optional[str] = None
    prompt_version: str
    content_version: Optional[int] = None
    sale_signals: List[LlmSignalOut] = []
    amounts: List[LlmAmountOut] = []
    complex_candidates: List[LlmCandidateOut] = []
    uncertainties: List[str] = []
    summary: Optional[str] = None
    dropped: Dict[str, int] = {}  # отброшено сервером: цитаты нет в тексте, ЖК нет в справочнике, сумма не число…
    error: Optional[str] = None
    retry_at: IsoTime = None  # failed с retry_at — ждёт повтора
    attempts: int = 0
    refusal_category: Optional[str] = None
    fallback: bool = False  # ответила резервная модель (серверный fallback после отказа)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    created_at: IsoTime = None
    updated_at: IsoTime = None


# Поля AdOut, которых нет у демо- и ручных публикаций: пустые не выводятся — их JSON (и world.ad_dict) не меняется
_AD_OPTIONAL = {"collectedAt": None, "collected_at": None, "media": [], "processing": None, "llm": None}


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
    collected_at: IsoTime = None  # время получения сборщиком/приёмом (UTC), не время публикации
    media: Annotated[List[MediaRefOut], BeforeValidator(lambda v: list(v or []))] = []
    processing: Optional[ProcessingOut] = None  # только у публикаций из приёма (Ad.ingest)
    llm: Optional[LlmOut] = None  # последний анализ ИИ (Claude) — гипотезы; нет — анализа не было

    @model_serializer(mode="wrap")
    def _out(self, handler) -> Dict[str, Any]:
        return {k: v for k, v in handler(self).items() if not (k in _AD_OPTIONAL and v == _AD_OPTIONAL[k])}


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


# ---------- сбор публикаций: подключения, запуски, приём (integration-spec §11.2) ----------

class AdVersionOut(Camel):
    version: int
    collected_at: IsoTime = None
    text: str
    price: Optional[str] = None
    media_keys: List[str] = []
    current: bool


class MediaUploadOut(Camel):
    id: str
    url: str
    mime: str
    size: int


ConfigItem = Annotated[str, StringConstraints(max_length=255), AfterValidator(no_nul)]
ConfigUrl = Annotated[str, StringConstraints(max_length=512), AfterValidator(no_nul)]


class ConnectorConfigIn(Camel):
    """Списки источников подключения; точную проверку и нормализацию делает connector.validate_config."""

    model_config = ConfigDict(extra="forbid")

    accounts: Optional[List[ConfigItem]] = Field(None, max_length=50)
    channels: Optional[List[ConfigItem]] = Field(None, max_length=50)
    profiles: Optional[List[ConfigItem]] = Field(None, max_length=20)
    search_urls: Optional[List[ConfigUrl]] = Field(None, max_length=10)
    card_urls: Optional[List[ConfigUrl]] = Field(None, max_length=10)
    results_per_page: Optional[int] = Field(None, ge=1, le=50)
    download_videos: Optional[bool] = None


class ConnectorPatchIn(Camel):
    author: HumanName
    enabled: Optional[bool] = None
    interval_min: Optional[int] = Field(None, ge=5, le=10080)
    config: Optional[ConnectorConfigIn] = None


class AdminActionIn(Camel):
    author: HumanName


class RunCountsOut(Camel):
    seen: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    media_saved: int = 0
    media_failed: int = 0


class RunNoteOut(Camel):
    at: str
    level: Literal["info", "warn", "error"]
    text: str


class RunOut(Camel):
    id: int
    source: str
    trigger: Literal["schedule", "manual", "test"]
    status: RunStatus
    status_label: str
    requested_by: Optional[str] = None
    created_at: IsoTime = None
    started_at: IsoTime = None
    finished_at: IsoTime = None
    counts: RunCountsOut
    external_run_id: Optional[str] = None
    cost_usd: Optional[float] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    notes: List[RunNoteOut] = []
    retry_at: IsoTime = None


class ConnectorCountsOut(Camel):
    items: int = 0
    ads: int = 0
    media: int = 0


class ConnectorCapsOut(Camel):
    test: bool
    collect: bool
    toggle: bool
    configure: bool
    connect: bool


class ConnectorOut(Camel):
    code: str
    kind: Literal["collector", "intake", "geo", "oauth", "analyzer"]
    group: Literal["collectors", "intake", "geo", "analyzer", "limited"]
    platform: Optional[str] = None
    name: str
    via: str
    status: ConnectorStatus
    status_label: str
    status_detail: Optional[str] = None
    enabled: bool
    configured: bool
    missing: List[str] = []
    secret_refs: List[str] = []
    config: Dict[str, Any] = {}
    config_fields: List[str] = []
    options: List[str] = []
    interval_min: Optional[int] = None
    interval_bounds: Optional[List[int]] = None
    next_run_at: IsoTime = None
    last_success_at: IsoTime = None
    last_error: Optional[str] = None
    last_error_at: IsoTime = None
    checked_at: IsoTime = None
    facts: Dict[str, Any] = {}
    counts: ConnectorCountsOut
    last_run: Optional[RunOut] = None
    active_run_id: Optional[int] = None
    capabilities: ConnectorCapsOut
    limitation: Optional[str] = None
    contract_verified: Optional[bool] = None
    paid: bool = False
    stopped: bool = False


class WorkerOut(Camel):
    alive: bool
    last_seen_at: IsoTime = None
    id: Optional[str] = None


class ConnectorsOut(Camel):
    connectors: List[ConnectorOut]
    worker: WorkerOut
    admin_configured: bool


class CollectAllItemOut(Camel):
    code: str
    run_id: Optional[int] = None
    reason: Optional[str] = None


class CollectAllOut(Camel):
    queued: List[CollectAllItemOut]
    skipped: List[CollectAllItemOut]


# ---------- приём публикаций через API (POST /api/intake/publications, integration-spec §7.4) ----------

INTAKE_TEXT_MAX = 100000  # принимается до 100 000 символов, хранится до AD_TEXT_MAX
INTAKE_MEDIA_MAX = 20
INTAKE_PAST_DAYS = 30
INTAKE_FUTURE_MIN = 5
# Поля решений аналитика: через приём публикаций не передаются (решения — только у аналитика)
DECISION_KEYS = ("status", "assignee", "complexId", "complex_id", "matchConfidence", "linkConfirmed", "linkConfirmedBy",
                 "linkConfirmedAt", "termsReview", "termsReviewBy", "termsReviewAt", "rejectedLinks", "origin", "source",
                 "id")
# Канонический externalId по площадке: (шаблон, описание формата в ответе 422)
EXTERNAL_ID_FORMATS = {
    "telegram": (r"^-?\d{1,20}:\d{1,12}$", "«{ID канала}:{ID первого сообщения}», например 1234567890:15"),
    "instagram": (r"^\d{1,32}$", "числовой ID медиа Graph API"),
    "tiktok": (r"^\d{1,32}$", "числовой ID публикации"),
    "krisha": (r"^\d{1,16}$", "числовой ID объявления"),
    "olx": (r"^[\w.:@+=-]{1,128}$", "до 128 символов: буквы, цифры и . : @ + = -"),
    "facebook": (r"^[\w.:@+=-]{1,128}$", "до 128 символов: буквы, цифры и . : @ + = -"),
}
_HTTP_URL = re.compile(r"^https?://\S+$", re.I)
_HTTPS_URL = re.compile(r"^https://\S+$", re.I)
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def normalize_external_id(platform: str, value: str) -> str:
    """Проверка формата externalId; Telegram в форме Bot API (-100XXXXXXXXXX) → положительный ID канала Telethon."""
    pattern, fmt = EXTERNAL_ID_FORMATS[platform]
    if not re.match(pattern, value):
        raise PydanticCustomError("value_error", "externalId для {platform}: ожидается {format}",
                                  {"platform": platform, "format": fmt})
    if platform == "telegram":
        channel, msg = value.split(":", 1)
        if channel.startswith("-100") and len(channel) > 4:
            channel = channel[4:]
        channel = channel.lstrip("-").lstrip("0") or "0"
        value = f"{channel}:{int(msg)}"
    return value


def _strip_ctrl(v: Any) -> Any:
    """NUL и управляющие символы (кроме перевода строки и табуляции) удаляются: текст источника не отклоняется из-за них."""
    return _CTRL.sub("", v) if isinstance(v, str) else v


def _aware(v: Optional[datetime], name: str) -> Optional[datetime]:
    if v is not None and v.tzinfo is None:
        raise PydanticCustomError("value_error", "{name}: укажите часовой пояс (например, 2026-10-09T11:10:23Z)",
                                  {"name": name})
    return v.astimezone(timezone.utc) if v is not None else None


class IntakeMediaIn(Camel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255), AfterValidator(no_nul)]
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048), AfterValidator(no_nul)]
    kind: Literal["image", "video"] = "image"
    mime: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=64), AfterValidator(no_nul)]] = None

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not _HTTPS_URL.match(v):
            raise PydanticCustomError("value_error", "media.url: нужна ссылка https://")
        return v


class IntakePublicationIn(Camel):
    """Нормализованная публикация от внешнего сборщика. Решения аналитика не принимаются; медиа — только https,
    загружаются позже анализом; collectedAt — заявленное клиентом время (хранится как заявлено, в пределах 30 дней)."""

    model_config = ConfigDict(extra="forbid")

    platform: IntakePlatform
    external_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    account: Annotated[str, BeforeValidator(_strip_ctrl), StringConstraints(strip_whitespace=True, max_length=255)] = ""
    url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=512), AfterValidator(no_nul)] = ""
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    text: Annotated[str, Field(max_length=INTAKE_TEXT_MAX)] = ""
    price: Optional[Annotated[str, BeforeValidator(_strip_ctrl), StringConstraints(strip_whitespace=True,
                                                                                    max_length=64)]] = None
    reach: Optional[int] = Field(None, ge=0)
    media: List[IntakeMediaIn] = Field([], max_length=INTAKE_MEDIA_MAX)
    source_id: Optional[Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,32}$")]] = None
    external_run_id: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
                                        AfterValidator(no_nul)]] = None
    raw: Dict[str, Any] = {}

    @model_validator(mode="before")
    @classmethod
    def _no_decisions(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found = [k for k in DECISION_KEYS if k in data]
            if found:
                raise PydanticCustomError("value_error",
                                          "поля решений аналитика при приёме публикаций не принимаются: {names}",
                                          {"names": ", ".join(found)})
        return data

    @field_validator("external_id")
    @classmethod
    def _external_id(cls, v: str, info: ValidationInfo) -> str:
        platform = info.data.get("platform")
        return normalize_external_id(platform, v) if platform in EXTERNAL_ID_FORMATS else v

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if v and not _HTTP_URL.match(v):
            raise PydanticCustomError("value_error", "url: нужна ссылка http(s):// или пустая строка")
        return v

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.replace("\x00", "")
        return v[:AD_TEXT_MAX]

    @field_validator("published_at")
    @classmethod
    def _published(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _aware(v, "publishedAt")

    @field_validator("collected_at")
    @classmethod
    def _collected(cls, v: Optional[datetime]) -> Optional[datetime]:
        v = _aware(v, "collectedAt")
        if v is not None:
            now = datetime.now(timezone.utc)
            if v > now + timedelta(minutes=INTAKE_FUTURE_MIN):
                raise PydanticCustomError("value_error", "collectedAt в будущем — проверьте часы сборщика")
            if v < now - timedelta(days=INTAKE_PAST_DAYS):
                raise PydanticCustomError("value_error", "collectedAt старше {days} дней", {"days": INTAKE_PAST_DAYS})
        return v

    @field_validator("media")
    @classmethod
    def _unique_keys(cls, v: List[IntakeMediaIn]) -> List[IntakeMediaIn]:
        seen, dup = set(), []
        for m in v:
            if m.key in seen and m.key not in dup:
                dup.append(m.key)
            seen.add(m.key)
        if dup:
            raise PydanticCustomError("value_error", "media: ключи файлов повторяются: {keys}", {"keys": ", ".join(dup)})
        return v


class IntakeResultOut(Camel):
    status: Literal["created", "updated", "unchanged"]
    ad_id: str
    item_id: int
    version: int
    media_queued: int = 0
    analysis_queued: bool = False
