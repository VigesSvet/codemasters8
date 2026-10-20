"""Предупреждения жителям(страница "Жителям"): аналитик готовит текст по данным ЖК, другой сотрудник в режиме
администратора согласует, воркер отправляет его через Telegram Bot API в канал ГАСК и подписчикам района.

    GET  /api/alerts?complexId=&limit=        предупреждения, новые первыми(без chat_id подписчиков)
    GET  /api/alerts/audience?district=       кому уйдёт: канал, подписчики района и "всех районов", готова ли отправка
    POST /api/alerts                          текст → "на согласовании"(автор - вошедший сотрудник)
    POST /api/alerts/check                    проверить бота и канал в Telegram(getMe, getChat, getChatMember)
    POST /api/alerts/{id}/approve             согласовать и отправить: токен администратора, согласующий - не автор
    POST /api/alerts/{id}/reject              отклонить(токен администратора), комментарий по желанию
    POST /api/alerts/{id}/retry               отправить доставки, оставшиеся "ожидает"(токен администратора)

Подписки регистрирует бот заявок(saq-telegram-bot) с тем же сервисным токеном, что и заявки(SERVICE_API_TOKEN):

    GET    /api/intake/telegram/districts                 районы ЖК - для клавиатуры бота
    PUT    /api/intake/telegram/subscriptions/{chatId}    {"districts": [...]} - заменить; [] или ["*"] - все районы
    GET    /api/intake/telegram/subscriptions/{chatId}    текущая подписка(404 - подписки нет)
    DELETE /api/intake/telegram/subscriptions/{chatId}    отписка(идемпотентно, 204)

Хранятся только chat_id и район - ни имени, ни username, ни телефона. У доставки подписчику chat_id стирается после
итога(остаётся счётчик); житель заблокировал бота или чат не найден - подписка снимается.

Согласование: предупреждение "на согласовании"(одно на ЖК) согласует сотрудник с токеном администратора, и это не
автор текста(сравнение имён без регистра) - так сообщение жителям никогда не уходит решением одного человека.
Автоматически система жителям ничего не отправляет.

Отправка(задание воркера "alert", jobs.HANDLERS): при согласовании получатели фиксируются - доставки "ожидает" в канал
TG_ALERTS_CHANNEL и подписчикам района ЖК(и "всех районов"). Воркер берёт их по одной условным UPDATE "ожидает →
отправляется", вызывает sendMessage(BOT_TOKEN, HTML) и записывает итог; не больше SEND_RATE сообщений в секунду, за
одно выполнение - BATCH доставок(дальше - продолжение тем же заданием). 429 - отсрочка на retry_after без расхода
попытки; сеть, тайм-аут, 5xx - повтор задания с паузой; Telegram не принял BOT_TOKEN - задание завершается ошибкой,
доставки остаются "ожидает"("Повторить отправку" после исправления). Доставка, оставшаяся "отправляется" после сбоя
воркера, повторно не отправляется("результат неизвестен"): одно и то же предупреждение не придёт жителю дважды.
Этот сервис бот API только отправляет: getUpdates и webhook не вызываются, long polling бота заявок с тем же токеном
не прерывается.
"""
import html
import logging
import re
import threading
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator, ConfigDict, Field, StringConstraints
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth, jobs, models, registry
from .ads_service import _event
from .db import SessionLocal, get_db
from .integrations import http
from .integrations.base import local_time
from .logs import redact
from .schemas import Camel, HumanName, IsoTime, OptNote, no_nul
from .settings import Settings, get_settings

log = logging.getLogger("saq.alerts")

router = APIRouter(tags=["alerts"])

API_BASE = "https://api.telegram.org"
TIMEOUT_S = 15.0
SEND_RATE = 20.0  # сообщений в секунду: у Bot API предел - около 30 в секунду разным чатам
BATCH = 200  # доставок за одно выполнение задания - дальше продолжение(отсрочка 0 с), очередь не занята надолго
MAX_ATTEMPTS = 5
TITLE_MAX = 80
TEXT_MAX = 240  # как в форме "Предупредить жителей"(frontend/src/pages/Residents.tsx)
ALL = "*"  # подписка на все районы
SYSTEM = "Система"
SIGNATURE = "ГАСК ВКО · SAQ QURYLYS"
_CHANNEL = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{4,31}|-100\d{5,15})$")
_TME = re.compile(r"^(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})/?$", re.I)

AlertStatus = Literal["pending", "rejected", "sending", "sent", "partial", "failed"]
Template = Literal["basis", "idle", "ok"]
STATUS_LABELS = {"pending": "На согласовании", "rejected": "Отклонено", "sending": "Отправляется",
                 "sent": "Отправлено", "partial": "Отправлено частично", "failed": "Не отправлено"}
TEMPLATE_LABELS = {"basis": "Проверьте основание", "idle": "Стройка стоит", "ok": "Есть в перечне"}

NOT_FOUND = "Предупреждение не найдено"
# Тексты для человека - без имён переменных: страница "Жителям" показывает их и администратору
NOT_CONFIGURED = "Отправка не настроена: на сервере не задан токен бота заявок"
NOBODY = "Некому отправить: канал ГАСК не задан, подписчиков района в боте нет"
SAME_PERSON = "Согласует другой сотрудник: автор предупреждения не может согласовать его сам"
NO_SUBSCRIPTION = "Подписки нет"
TOKEN_REJECTED = "Telegram не принял токен бота (HTTP {code}) — проверьте его в настройках сервера"
BAD_CHANNEL = "Канал ГАСК указан неверно: нужен @имя публичного канала или его ID вида -100…"
UNKNOWN = ("Результат неизвестен: отправка прервана сбоем воркера — сообщение могло дойти, повторно не отправляется, "
           "чтобы житель не получил его дважды")
UNSUBSCRIBED = "Житель отписался до отправки"

AlertId = Annotated[int, Path(ge=1, le=2_147_483_647, description="id предупреждения")]
ChatId = Annotated[str, Path(pattern=r"^-?\d{1,20}$", description="chat_id Telegram")]


# ---------- схемы ----------

def _text(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=n), AfterValidator(no_nul)]


class AlertIn(Camel):
    """Предупреждение на согласование: ЖК, шаблон, с которого начат текст, заголовок и текст, автор."""

    model_config = ConfigDict(extra="forbid")

    complex_id: _text(32)
    template: Template
    title: _text(TITLE_MAX)
    text: _text(TEXT_MAX)
    author: HumanName


class AlertDecisionIn(Camel):
    """Согласование, отклонение или повтор отправки: кто(вошедший сотрудник) и комментарий."""

    model_config = ConfigDict(extra="forbid")

    author: HumanName
    comment: OptNote = None


class DeliveryCountsOut(Camel):
    total: int = 0
    sent: int = 0
    failed: int = 0
    pending: int = 0
    skipped: int = 0


class ChannelDeliveryOut(Camel):
    target: str
    status: str
    error: str | None = None
    sent_at: IsoTime = None
    link: str | None = None  # сообщение в публичном канале: https://t.me/<канал>/<id>


class ErrorCountOut(Camel):
    text: str
    count: int


class AlertOut(Camel):
    id: int
    code: str
    complex_id: str
    complex_name: str
    district: str
    template: str
    title: str
    text: str
    status: AlertStatus
    status_label: str
    created_by: str
    created_at: IsoTime = None
    decided_by: str | None = None
    decided_at: IsoTime = None
    decision_comment: str | None = None
    sent_at: IsoTime = None
    last_error: str | None = None
    channel: ChannelDeliveryOut | None = None
    subscribers: DeliveryCountsOut = Field(default_factory=DeliveryCountsOut)
    errors: list[ErrorCountOut] = []  # почему не доставлено подписчикам - до трёх причин


class DistrictCountOut(Camel):
    district: str
    subscribers: int


class AudienceOut(Camel):
    """Кому уйдёт предупреждение по району и готова ли отправка(без chat_id)."""

    configured: bool  # BOT_TOKEN задан
    admin_configured: bool  # SAQ_ADMIN_TOKEN задан - согласовать есть кому
    worker_alive: bool
    problem: str | None = None
    channel: str | None = None
    channel_problem: str | None = None
    district: str = ""
    subscribers: int = 0  # подписчики района и "всех районов" - получат предупреждение по ЖК этого района
    everywhere: int = 0
    districts: list[DistrictCountOut] = []


class CheckOut(Camel):
    ok: bool
    detail: str
    bot: str | None = None
    channel: str | None = None
    channel_title: str | None = None


District = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64), AfterValidator(no_nul)]


class SubscriptionIn(Camel):
    """Районы подписки: названия районов ЖК(GET /api/intake/telegram/districts) без учёта регистра; [] или "*" -
    все районы."""

    model_config = ConfigDict(extra="forbid")

    districts: Annotated[list[District], Field(max_length=50)] = []


class SubscriptionOut(Camel):
    chat_id: str
    districts: list[str]
    everywhere: bool
    status: Literal["created", "updated", "unchanged"] | None = None


class DistrictOut(Camel):
    district: str
    complexes: int


# ---------- канал и сообщение ----------

def channel(s: Settings) -> tuple[str | None, str | None]:
    """Канал ГАСК из TG_ALERTS_CHANNEL: (канал, None); не задан - (None, None); неверный - (None, пояснение).
    Ссылка t.me/<имя> принимается как @<имя>."""
    raw = (s.tg_alerts_channel or "").strip()
    if not raw:
        return None, None
    m = _TME.match(raw)
    if m:
        raw = "@" + m.group(1)
    if not _CHANNEL.match(raw):
        return None, BAD_CHANNEL
    return raw, None


def hashtag(district: str) -> str:
    """"Северо-запад" → "#Северо_запад": хэштег района, по которому житель находит предупреждения своего района в
    канале; без района - пусто."""
    tag = re.sub(r"\W+", "_", district or "").strip("_")
    return f"#{tag}" if tag else ""


def render(a: models.ResidentAlert) -> str:
    """Текст сообщения Telegram(parse_mode HTML): заголовок жирным, текст, хэштег района и подпись."""
    foot = " · ".join(x for x in (hashtag(a.district), SIGNATURE) if x)
    esc = lambda v: html.escape(v or "", quote=False)
    return f"<b>{esc(a.title)}</b>\n{esc(a.text)}\n\n{esc(foot)}"


# ---------- Bot API ----------

class _Limited(Exception):
    def __init__(self, seconds: float):
        self.retry_after = max(float(seconds), 1.0)
        super().__init__(f"Telegram ограничил частоту отправки — продолжение через {self.retry_after:.0f} с")
        self.detail = str(self)


class _Transient(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class _TokenRejected(Exception):
    def __init__(self, code: int):
        self.detail = TOKEN_REJECTED.format(code=code)
        super().__init__(self.detail)


class _ChatRejected(Exception):
    """Получатель или сообщение отклонены(4xx, кроме 401/404/429): description - текст Telegram."""

    def __init__(self, code: int, description: str):
        self.code, self.description = code, description
        super().__init__(f"HTTP {code}: {description}")


def call(cli: httpx.Client, token: str, method: str, payload: dict) -> Any:
    """Метод Bot API → result. 429 - _Limited(retry_after из ответа или заголовка); 401/404 - _TokenRejected(токен
    не принят: неверный или отозван); сеть, тайм-аут, 5xx, ответ не Bot API - _Transient; прочие 4xx - _ChatRejected.
    Токен - только в пути запроса; в тексты ошибок он не попадает(журналы маскируют "/bot<токен>")."""
    try:
        r = cli.post(f"/bot{token}/{method}", json=payload)
    except httpx.TimeoutException:
        raise _Transient("Telegram не ответил вовремя") from None
    except httpx.TransportError as e:
        raise _Transient(f"Сетевая ошибка при обращении к Telegram ({type(e).__name__})") from None
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        if r.status_code in (401, 404):
            raise _TokenRejected(r.status_code)
        if r.status_code == 429:
            raise _Limited(http.retry_after(r.headers, 30))
        raise _Transient(f"Telegram ответил не в формате Bot API (HTTP {r.status_code})")
    if r.status_code == 200 and body.get("ok") is True:
        return body.get("result")
    code = body.get("error_code") if isinstance(body.get("error_code"), int) else r.status_code
    params = body.get("parameters") if isinstance(body.get("parameters"), dict) else {}
    if code == 429:
        retry = params.get("retry_after")
        raise _Limited(float(retry) if isinstance(retry, (int, float)) else http.retry_after(r.headers, 30))
    if code in (401, 404):
        raise _TokenRejected(code)
    if code >= 500 or code < 400:
        raise _Transient(f"Сбой серверов Telegram (HTTP {code})")
    raise _ChatRejected(code, redact(str(body.get("description") or f"HTTP {code}"), limit=300) or "")


def chat_reason(kind: str, target: str | None, description: str) -> tuple[str, bool]:
    """Отказ Telegram по получателю → (пояснение по-русски, снять подписку). Подписка снимается, когда писать этому
    жителю больше нельзя: заблокировал бота, удалил аккаунт, чат не найден, не начинал диалог с ботом."""
    d = (description or "").lower()
    if kind == "channel":
        if "chat not found" in d:
            return f"Канал {target} не найден — проверьте адрес канала в настройках сервера", False
        if any(x in d for x in ("not a member", "not enough rights", "administrator rights", "have no rights",
                                "can't send messages", "kicked")):
            return (f"Бот не может публиковать в канале {target}: сделайте его администратором с правом публикации",
                    False)
        return f"Telegram не принял сообщение в канал: {description}", False
    if "blocked by the user" in d:
        return "Житель заблокировал бота — подписка снята", True
    if "user is deactivated" in d:
        return "Аккаунт жителя удалён — подписка снята", True
    if "chat not found" in d or "can't initiate conversation" in d:
        return "Чат с жителем недоступен — подписка снята", True
    if "kicked" in d or "not a member" in d:
        return "Бота удалили из чата — подписка снята", True
    return f"Telegram не принял сообщение: {description}", False


def send(cli: httpx.Client, token: str, target: str, text: str) -> int | None:
    """sendMessage → message_id(или None, если Telegram его не вернул)."""
    res = call(cli, token, "sendMessage", {"chat_id": target, "text": text, "parse_mode": "HTML",
                                           "link_preview_options": {"is_disabled": True}})
    mid = res.get("message_id") if isinstance(res, dict) else None
    return mid if isinstance(mid, int) and 0 < mid < 2_147_483_647 else None


def _client() -> httpx.Client:
    return http.client(base_url=API_BASE, timeout=TIMEOUT_S)


# ---------- получатели и счётчики ----------

def _districts(db: Session) -> list[tuple[str, int]]:
    """Районы ЖК(непустые) и сколько ЖК в каждом - по убыванию."""
    C = models.Complex
    rows = db.execute(select(C.district, func.count()).where(C.district != "").group_by(C.district)).all()
    return sorted(((d, int(n)) for d, n in rows), key=lambda x: (-x[1], x[0]))


def _keys(district: str) -> list[str]:
    return [ALL, district] if district else [ALL]


def _subscribers(db: Session, district: str) -> list[str]:
    """chat_id подписчиков, которым уйдёт предупреждение по ЖК района district: подписанные на район и на все районы."""
    S = models.AlertSubscription
    return sorted(set(db.scalars(select(S.chat_id).where(S.district.in_(_keys(district))))))


def _audience_count(db: Session, district: str) -> int:
    S = models.AlertSubscription
    return int(db.scalar(select(func.count(func.distinct(S.chat_id))).where(S.district.in_(_keys(district)))) or 0)


def _tally(db: Session, ids: list[int]) -> dict[int, dict]:
    """Доставки по предупреждениям: {id: {"channel": строка доставки в канал или None, "subscribers": счётчики,
    "errors": причины недоставки подписчикам}}."""
    D = models.AlertDelivery
    out: dict[int, dict] = {i: {"channel": None, "subscribers": DeliveryCountsOut().model_dump(), "errors": []}
                            for i in ids}
    if not ids:
        return out
    for aid, status, n in db.execute(select(D.alert_id, D.status, func.count())
                                     .where(D.alert_id.in_(ids), D.kind == "subscriber")
                                     .group_by(D.alert_id, D.status)):
        c = out[aid]["subscribers"]
        key = "pending" if status == "sending" else status
        if key in c:
            c[key] += int(n)
        c["total"] += int(n)
    for row in db.scalars(select(D).where(D.alert_id.in_(ids), D.kind == "channel").order_by(D.id)):
        out[row.alert_id]["channel"] = row
    errs = db.execute(select(D.alert_id, D.error, func.count()).where(
        D.alert_id.in_(ids), D.kind == "subscriber", D.status == "failed", D.error.is_not(None))
        .group_by(D.alert_id, D.error))
    for aid, text_, n in errs:
        out[aid]["errors"].append({"text": text_, "count": int(n)})
    for v in out.values():
        v["errors"] = sorted(v["errors"], key=lambda e: (-e["count"], e["text"]))[:3]
    return out


def _channel_out(row: models.AlertDelivery | None) -> dict | None:
    if row is None:
        return None
    link = None
    if row.status == "sent" and row.message_id and (row.target or "").startswith("@"):
        link = f"https://t.me/{row.target[1:]}/{row.message_id}"
    status = "pending" if row.status == "sending" else row.status
    return {"target": row.target or "", "status": status, "error": row.error, "sent_at": row.sent_at, "link": link}


def _out(a: models.ResidentAlert, t: dict) -> dict:
    return {"id": a.id, "code": a.code, "complex_id": a.complex_id, "complex_name": a.complex_name,
            "district": a.district or "", "template": a.template, "title": a.title, "text": a.text, "status": a.status,
            "status_label": STATUS_LABELS.get(a.status, a.status), "created_by": a.created_by,
            "created_at": a.created_at, "decided_by": a.decided_by, "decided_at": a.decided_at,
            "decision_comment": a.decision_comment, "sent_at": a.sent_at, "last_error": a.last_error,
            "channel": _channel_out(t["channel"]), "subscribers": t["subscribers"], "errors": t["errors"]}


def alert_out(db: Session, a: models.ResidentAlert) -> dict:
    return _out(a, _tally(db, [a.id])[a.id])


def _get(db: Session, aid: int) -> models.ResidentAlert:
    a = db.get(models.ResidentAlert, aid, populate_existing=True)
    if a is None:
        raise HTTPException(404, NOT_FOUND)
    return a


def _pending(db: Session, cid: str) -> models.ResidentAlert | None:
    A = models.ResidentAlert
    return db.scalar(select(A).where(A.complex_id == cid, A.status == "pending").limit(1))


def _conflict(a: models.ResidentAlert) -> HTTPException:
    return HTTPException(409, {"message": f"По ЖК «{a.complex_name}» уже есть предупреждение на согласовании "
                                          f"({a.code}) — согласуйте или отклоните его", "alertId": a.id})


def _already(a: models.ResidentAlert) -> HTTPException:
    return HTTPException(409, f"Предупреждение {a.code} уже в статусе «{STATUS_LABELS.get(a.status, a.status)}»")


def _move(db: Session, a: models.ResidentAlert, frm: tuple, to: str, **values) -> None:
    """Смена статуса условным UPDATE: два одновременных согласования не создадут две рассылки - второе получит 409."""
    A = models.ResidentAlert
    res = db.execute(update(A).where(A.id == a.id, A.status.in_(frm)).values(status=to, **values)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        db.rollback()
        raise _already(_get(db, a.id))
    db.refresh(a)


def _then(text: str, comment: str | None) -> str:
    return f"{text}{'' if text.endswith('.') else '.'} Комментарий: {comment}" if comment else text


def _whom(ch: str | None, subs: int, district: str) -> str:
    where = f"района «{district}»" if district else "всех районов"
    parts = ([f"канал {ch}"] if ch else []) + ([f"подписчики {where} в боте ({subs})"] if subs else [])
    return " и ".join(parts)


def _ready(s: Settings) -> None:
    if not s.bot_token:
        raise HTTPException(503, NOT_CONFIGURED)


# ---------- маршруты: сотрудники ----------

@router.get("/api/alerts", response_model=list[AlertOut], response_model_by_alias=True)
def list_alerts(complex_id: str | None = Query(None, alias="complexId", max_length=32),
                limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Предупреждения, новые первыми; по ЖК - complexId. Получатели - только счётчики, без chat_id."""
    A = models.ResidentAlert
    q = select(A).order_by(A.id.desc()).limit(limit)
    if complex_id:
        q = q.where(A.complex_id == complex_id)
    rows = list(db.scalars(q))
    t = _tally(db, [a.id for a in rows])
    return [_out(a, t[a.id]) for a in rows]


@router.get("/api/alerts/audience", response_model=AudienceOut, response_model_by_alias=True)
def audience(district: str = Query("", max_length=64), db: Session = Depends(get_db)):
    """Кому уйдёт предупреждение по ЖК района district и готова ли отправка: BOT_TOKEN, канал, токен администратора
    (согласовать), воркер. Только счётчики подписчиков."""
    from .integrations import service

    s = get_settings()
    ch, problem = channel(s)
    S = models.AlertSubscription
    per = dict(db.execute(select(S.district, func.count(func.distinct(S.chat_id))).group_by(S.district)).all())
    district = district.strip()
    return {"configured": bool(s.bot_token), "admin_configured": bool(s.admin_token),
            "worker_alive": bool(service.worker_info(db)["alive"]), "problem": None if s.bot_token else NOT_CONFIGURED,
            "channel": ch, "channel_problem": problem, "district": district,
            "subscribers": _audience_count(db, district), "everywhere": int(per.get(ALL, 0)),
            "districts": [{"district": d, "subscribers": int(per.get(d, 0))} for d, _ in _districts(db)]}


@router.post("/api/alerts", response_model=AlertOut, response_model_by_alias=True, status_code=201,
             responses={404: {"description": "ЖК не найден"}, 409: {"description": "Уже есть на согласовании"}})
def create(body: AlertIn, db: Session = Depends(get_db)):
    """Предупреждение "на согласовании" по ЖК: район - из карточки ЖК. Не больше одного на согласовании по ЖК."""
    c = db.get(models.Complex, body.complex_id)
    if c is None:
        raise registry.not_found(db, body.complex_id)
    cur = _pending(db, c.id)
    if cur is not None:
        raise _conflict(cur)
    a = models.ResidentAlert(complex_id=c.id, complex_name=c.name, district=(c.district or "").strip(),
                             template=body.template, title=body.title, text=body.text, status="pending",
                             created_by=body.author, created_at=models.utcnow())
    db.add(a)
    try:
        db.flush()
    except IntegrityError:  # параллельный запрос успел создать предупреждение по тому же ЖК
        db.rollback()
        cur = _pending(db, c.id)
        if cur is not None:
            raise _conflict(cur) from None
        raise
    where = f"район «{a.district}»" if a.district else "район не указан"
    _event(db, "alert", body.author, f"Предупреждение жителям {a.code} подготовлено и ждёт согласования: «{a.title}» "
                                     f"(шаблон «{TEMPLATE_LABELS[a.template]}», {where})", complex_id=c.id)
    db.commit()
    return alert_out(db, a)


@router.post("/api/alerts/check", response_model=CheckOut, response_model_by_alias=True,
             dependencies=[Depends(auth.require_admin)], responses={503: {"description": NOT_CONFIGURED}})
def check():
    """Проверить бота и канал(токен администратора): getMe - токен принят; getChat и getChatMember - канал есть и бот
    в нём администратор с правом публикации. Сообщений не отправляет."""
    s = get_settings()
    _ready(s)
    token = s.bot_token.get()
    ch, problem = channel(s)
    out: dict = {"ok": False, "detail": "", "bot": None, "channel": ch, "channel_title": None}
    with _client() as cli:
        try:
            me = call(cli, token, "getMe", {})
        except _ChatRejected as e:
            out["detail"] = f"Telegram отклонил запрос: {e.description}"
            return out
        except (_Limited, _Transient, _TokenRejected) as e:
            out["detail"] = e.detail
            return out
        me = me if isinstance(me, dict) else {}
        bot = f"@{me['username']}" if me.get("username") else "бот"
        out["bot"] = bot if me.get("username") else None
        if problem:
            out["detail"] = f"{bot}: токен принят. {problem}"
            return out
        if ch is None:
            out["ok"] = True
            out["detail"] = (f"{bot}: токен принят. Канал ГАСК не задан — предупреждения получат только "
                             "подписчики бота")
            return out
        try:
            chat = call(cli, token, "getChat", {"chat_id": ch})
            member = call(cli, token, "getChatMember", {"chat_id": ch, "user_id": me.get("id")})
        except _ChatRejected as e:
            out["detail"] = f"{bot}: токен принят. {chat_reason('channel', ch, e.description)[0]}"
            return out
        except (_Limited, _Transient, _TokenRejected) as e:
            out["detail"] = f"{bot}: {e.detail}"
            return out
    chat = chat if isinstance(chat, dict) else {}
    member = member if isinstance(member, dict) else {}
    title = str(chat.get("title") or ch)
    out["channel_title"] = title
    can_post = member.get("status") == "creator" or (member.get("status") == "administrator"
                                                      and member.get("can_post_messages") is not False)
    out["ok"] = can_post
    out["detail"] = (f"{bot} — администратор канала «{title}» ({ch}) и может публиковать" if can_post else
                     f"{bot} не может публиковать в канале «{title}» ({ch}): сделайте его администратором с правом "
                     "публикации сообщений")
    return out


@router.post("/api/alerts/{aid}/approve", response_model=AlertOut, response_model_by_alias=True,
             dependencies=[Depends(auth.require_admin)],
             responses={409: {"description": "Не на согласовании, согласует автор или некому отправить"},
                        503: {"description": NOT_CONFIGURED}})
def approve(aid: AlertId, body: AlertDecisionIn, db: Session = Depends(get_db)):
    """Согласовать и отправить(токен администратора; согласующий - не автор): получатели фиксируются(канал ГАСК и
    подписчики района на этот момент), отправку выполняет воркер."""
    a = _get(db, aid)
    if a.status != "pending":
        raise _already(a)
    if body.author.strip().casefold() == (a.created_by or "").strip().casefold():
        raise HTTPException(409, SAME_PERSON)
    s = get_settings()
    _ready(s)
    ch, problem = channel(s)
    subs = _subscribers(db, a.district)
    if ch is None and not subs:
        raise HTTPException(409, f"{NOBODY}. {problem}" if problem else NOBODY)
    now = models.utcnow()
    _move(db, a, ("pending",), "sending", decided_by=body.author, decided_at=now, decision_comment=body.comment,
          last_error=None)
    D = models.AlertDelivery
    if ch is not None:
        db.add(D(alert_id=a.id, kind="channel", target=ch, status="pending", updated_at=now))
    for chat_id in subs:
        db.add(D(alert_id=a.id, kind="subscriber", target=chat_id, status="pending", updated_at=now))
    jobs.enqueue(db, "alert", dedupe_key=f"alert:{a.id}", payload={"alertId": a.id}, max_attempts=MAX_ATTEMPTS)
    what = f"Предупреждение жителям {a.code} согласовано и поставлено в отправку: {_whom(ch, len(subs), a.district)}"
    _event(db, "alert", body.author, _then(what, body.comment), complex_id=a.complex_id)
    db.commit()
    return alert_out(db, a)


@router.post("/api/alerts/{aid}/reject", response_model=AlertOut, response_model_by_alias=True,
             dependencies=[Depends(auth.require_admin)], responses={409: {"description": "Не на согласовании"}})
def reject(aid: AlertId, body: AlertDecisionIn, db: Session = Depends(get_db)):
    """Отклонить(токен администратора): жителям ничего не уходит; по ЖК можно подготовить новое предупреждение."""
    a = _get(db, aid)
    _move(db, a, ("pending",), "rejected", decided_by=body.author, decided_at=models.utcnow(),
          decision_comment=body.comment)
    _event(db, "alert", body.author, _then(f"Предупреждение жителям {a.code} отклонено — жителям не отправлено",
                                           body.comment), complex_id=a.complex_id)
    db.commit()
    return alert_out(db, a)


@router.post("/api/alerts/{aid}/retry", response_model=AlertOut, response_model_by_alias=True,
             dependencies=[Depends(auth.require_admin)],
             responses={409: {"description": "Нечего повторять"}, 503: {"description": NOT_CONFIGURED}})
def retry(aid: AlertId, body: AlertDecisionIn, db: Session = Depends(get_db)):
    """Повторить отправку(токен администратора): только доставки "ожидает" - оставшиеся после неверного BOT_TOKEN или
    исчерпанных повторов. Доставленные, отклонённые Telegram и с неизвестным результатом повторно не отправляются."""
    a = _get(db, aid)
    if a.status not in ("failed", "partial"):
        raise HTTPException(409, f"Повторять нечего: предупреждение {a.code} в статусе "
                                 f"«{STATUS_LABELS.get(a.status, a.status)}»")
    D = models.AlertDelivery
    left = int(db.scalar(select(func.count()).select_from(D).where(D.alert_id == a.id, D.status == "pending")) or 0)
    if not left:
        raise HTTPException(409, f"Повторять нечего: у предупреждения {a.code} нет доставок, ожидающих отправки")
    _ready(get_settings())
    _move(db, a, ("failed", "partial"), "sending", last_error=None, sent_at=None)
    jobs.enqueue(db, "alert", dedupe_key=f"alert:{a.id}", payload={"alertId": a.id}, max_attempts=MAX_ATTEMPTS)
    _event(db, "alert", body.author, _then(f"Повторная отправка предупреждения жителям {a.code}: ожидают доставки "
                                           f"{left}", body.comment), complex_id=a.complex_id)
    db.commit()
    return alert_out(db, a)


# ---------- маршруты: бот заявок(подписки) ----------

def _bot_ok(db: Session) -> None:
    """Обращение бота с верным токеном снимает прежнее "нет доступа" у подключения "Telegram-бот заявок"."""
    from .integrations import telegram_bot

    telegram_bot._token_ok(db)


def _subscription(db: Session, chat_id: str) -> list[str]:
    S = models.AlertSubscription
    return sorted(db.scalars(select(S.district).where(S.chat_id == chat_id)))


def _sub_out(chat_id: str, districts: list[str], status: str | None = None) -> dict:
    everywhere = ALL in districts
    return {"chat_id": chat_id, "districts": [] if everywhere else districts, "everywhere": everywhere,
            "status": status}


@router.get("/api/intake/telegram/districts", response_model=list[DistrictOut], response_model_by_alias=True,
            dependencies=[Depends(auth.require_service)])
def districts(db: Session = Depends(get_db)):
    """Районы ЖК для выбора подписки в боте(по числу ЖК). "*" - все районы - в списке нет, его принимает PUT."""
    out = [{"district": d, "complexes": n} for d, n in _districts(db)]
    _bot_ok(db)
    return out


@router.put("/api/intake/telegram/subscriptions/{chat_id}", response_model=SubscriptionOut,
            response_model_by_alias=True, dependencies=[Depends(auth.require_service)])
def put_subscription(chat_id: ChatId, body: SubscriptionIn, db: Session = Depends(get_db)):
    """Подписка жителя(chat_id) на районы - заменяет прежнюю: [] или "*" - все районы. Неизвестный район - 422 со
    списком районов. Повтор той же подписки - unchanged."""
    known = {d.casefold(): d for d, _ in _districts(db)}
    want: set[str] = set()
    for raw in body.districts:
        if raw == ALL:
            want = {ALL}
            break
        d = known.get(raw.casefold())
        if d is None:
            names = ", ".join(sorted(known.values())) or "районов у ЖК нет"
            raise RequestValidationError([{"type": "value_error", "loc": ("body", "districts"), "input": None,
                                           "msg": f"Неизвестный район «{raw}». Районы: {names}; «*» — все районы"}])
        want.add(d)
    want = want or {ALL}
    have = set(_subscription(db, chat_id))
    S = models.AlertSubscription
    if have == want:
        status = "unchanged"
    else:
        db.execute(delete(S).where(S.chat_id == chat_id, S.district.not_in(want))
                   .execution_options(synchronize_session=False))
        now = models.utcnow()
        for d in sorted(want - have):
            models.insert_ignore(db, S, {"chat_id": chat_id, "district": d, "created_at": now},
                                 ("chat_id", "district"))
        status = "updated" if have else "created"
    db.commit()
    _bot_ok(db)
    return _sub_out(chat_id, sorted(want), status)


@router.get("/api/intake/telegram/subscriptions/{chat_id}", response_model=SubscriptionOut,
            response_model_by_alias=True, dependencies=[Depends(auth.require_service)],
            responses={404: {"description": NO_SUBSCRIPTION}})
def get_subscription(chat_id: ChatId, db: Session = Depends(get_db)):
    got = _subscription(db, chat_id)
    _bot_ok(db)
    if not got:
        raise HTTPException(404, NO_SUBSCRIPTION)
    return _sub_out(chat_id, got)


@router.delete("/api/intake/telegram/subscriptions/{chat_id}", status_code=204,
               dependencies=[Depends(auth.require_service)])
def delete_subscription(chat_id: ChatId, db: Session = Depends(get_db)):
    """Отписка(повтор - тоже 204): подписка удаляется, доставки этому жителю, ещё ожидающие отправки, - "отписался"."""
    S, D = models.AlertSubscription, models.AlertDelivery
    db.execute(delete(S).where(S.chat_id == chat_id).execution_options(synchronize_session=False))
    db.execute(update(D).where(D.kind == "subscriber", D.target == chat_id, D.status == "pending")
               .values(status="skipped", target=None, error=UNSUBSCRIBED, updated_at=models.utcnow())
               .execution_options(synchronize_session=False))
    db.commit()
    _bot_ok(db)
    return Response(status_code=204)


# ---------- отправка(задание воркера "alert") ----------

def _settle_interrupted(db: Session, alert_id: int, now: datetime) -> int:
    """Доставки "отправляется", оставшиеся после сбоя воркера, → "не доставлено: результат неизвестен"(без commit).
    Повторно они не отправляются: Telegram мог принять сообщение до сбоя."""
    D = models.AlertDelivery
    res = db.execute(update(D).where(D.alert_id == alert_id, D.status == "sending")
                     .values(status="failed", error=UNKNOWN, target=case((D.kind == "subscriber", None),
                                                                         else_=D.target), updated_at=now)
                     .execution_options(synchronize_session=False))
    return res.rowcount or 0


def _claim(alert_id: int, now: datetime) -> tuple[int, str, str] | None:
    """Следующая доставка "ожидает" → "отправляется"(условный UPDATE, своя транзакция): (id, вид, получатель) или
    None."""
    D = models.AlertDelivery
    with SessionLocal() as db:
        for _ in range(3):
            row = db.execute(select(D.id, D.kind, D.target).where(D.alert_id == alert_id, D.status == "pending")
                             .order_by(D.id).limit(1)).first()
            if row is None:
                return None
            res = db.execute(update(D).where(D.id == row.id, D.status == "pending")
                             .values(status="sending", attempts=D.attempts + 1, updated_at=now)
                             .execution_options(synchronize_session=False))
            db.commit()
            if res.rowcount == 1:
                return row.id, row.kind, row.target or ""
    return None


def _settle(did: int, now: datetime, **values) -> None:
    """Итог доставки, пока она "отправляется"(своя транзакция)."""
    D = models.AlertDelivery
    with SessionLocal() as db:
        db.execute(update(D).where(D.id == did, D.status == "sending").values(updated_at=now, **values)
                   .execution_options(synchronize_session=False))
        db.commit()


def _sent(did: int, kind: str, message_id: int | None, now: datetime) -> None:
    extra = {"target": None} if kind == "subscriber" else {}
    _settle(did, now, status="sent", sent_at=now, message_id=message_id, error=None, **extra)


def _rejected(did: int, kind: str, target: str, e: _ChatRejected, now: datetime) -> None:
    reason, unsubscribe = chat_reason(kind, target, e.description)
    extra = {"target": None} if kind == "subscriber" else {}
    _settle(did, now, status="failed", error=reason, **extra)
    if unsubscribe and kind == "subscriber" and target:
        S = models.AlertSubscription
        with SessionLocal() as db:
            db.execute(delete(S).where(S.chat_id == target).execution_options(synchronize_session=False))
            db.commit()


def _release(did: int, now: datetime) -> None:
    """Доставка не выполнена не по вине получателя(лимит, сеть, токен) - снова "ожидает"."""
    _settle(did, now, status="pending")


def _summary(t: dict) -> str:
    c, ch = t["subscribers"], t["channel"]
    parts = []
    if ch is not None:
        state = {"sent": "опубликовано", "failed": "не опубликовано", "skipped": "пропущено"}.get(ch.status, "ожидает")
        parts.append(f"канал {ch.target}: {state}")
    if c["total"]:
        n = c["total"] - c["skipped"]
        part = f"подписчики: доставлено {c['sent']} из {n}"
        if c["failed"]:
            part += f", не доставлено {c['failed']}"
        if c["pending"]:
            part += f", ожидают {c['pending']}"
        if c["skipped"]:
            part += f", отписались {c['skipped']}"
        parts.append(part)
    return "; ".join(parts) or "получателей нет"


def finalize(db: Session, a: models.ResidentAlert, now: datetime, error: str | None = None) -> None:
    """Итог отправки по доставкам(без commit): всем - "отправлено", части - "частично", никому - "не отправлено";
    событие журнала ЖК от "Системы". error - почему отправка остановлена(доставки "ожидает" остаются для повтора)."""
    t = _tally(db, [a.id])[a.id]
    c, ch = t["subscribers"], t["channel"]
    sent = c["sent"] + (ch is not None and ch.status == "sent")
    missed = c["failed"] + c["pending"] + (ch is not None and ch.status in ("failed", "pending", "sending"))
    if sent and not missed:
        status = "sent"
    elif sent:
        status = "partial"
    else:
        status = "failed"
        error = error or (None if missed else UNSUBSCRIBED)
    if error is None and status != "sent":
        first = (ch.error if ch is not None and ch.status == "failed" else None) or next(
            (e["text"] for e in t["errors"]), None)
        error = first
    a.status, a.sent_at, a.last_error = status, now if sent else None, redact(error) if error else None
    what = f"Предупреждение жителям {a.code}: {STATUS_LABELS[status].lower()} — {_summary(t)}"
    if a.last_error and status != "sent":
        what += f". Причина: {a.last_error}"
    _event(db, "alert", SYSTEM, what, at=now, complex_id=a.complex_id)


def abandoned(db: Session, job: models.Job, reason: str, now: datetime) -> None:
    """Задание брошено окончательно(jobs._abandoned: воркер упал или завис, попытки исчерпаны) - в той же сессии."""
    a = db.get(models.ResidentAlert, _alert_id(job.payload))
    if a is None or a.status != "sending":
        return
    _settle_interrupted(db, a.id, now)
    finalize(db, a, now, reason)


def _alert_id(payload: dict | None) -> int:
    try:
        return int((payload or {}).get("alertId") or 0)
    except (TypeError, ValueError):
        return 0


def _outcome(info, apply) -> None:
    """Итог задания - только пока оно у этого захвата(jobs.fence); apply(db, job, alert, now)."""
    with SessionLocal() as db:
        job = jobs.fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        now = info.now()
        a = db.get(models.ResidentAlert, _alert_id(info.payload), populate_existing=True)
        apply(db, job, a, now)
        db.commit()


def _give_up(info, detail: str) -> None:
    def apply(db, job, a, now):
        jobs.fail(db, job, detail)
        if a is not None and a.status == "sending":
            finalize(db, a, now, detail)
    _outcome(info, apply)


def _later(info, seconds: float, detail: str | None = None) -> None:
    """Продолжить тем же заданием через seconds с(без расхода попытки): лимит Telegram, очередная партия, остановка
    воркера."""
    def apply(db, job, a, now):
        if not jobs.defer(db, job, seconds, None, now=now):
            jobs.fail(db, job, "Отправка откладывалась слишком много раз — остановлена")
            if a is not None and a.status == "sending":
                finalize(db, a, now, "Отправка откладывалась слишком много раз — остановлена")
            return
        if a is not None and detail:
            a.last_error = f"{detail} (после {local_time(job.run_after)})"
    _outcome(info, apply)


def _retry(info, detail: str) -> None:
    def apply(db, job, a, now):
        if jobs.retry(db, job, detail, now=now):
            if a is not None:
                a.last_error = (f"{detail} — повтор {job.attempts + 1} из {job.max_attempts} после "
                                f"{local_time(job.run_after)}")
            return
        jobs.fail(db, job, detail)
        if a is not None and a.status == "sending":
            finalize(db, a, now, detail)
    _outcome(info, apply)


def _done(info, sent: int) -> None:
    def apply(db, job, a, now):
        jobs.complete(db, job, {"sent": sent})
        if a is not None and a.status == "sending":
            finalize(db, a, now)
    _outcome(info, apply)


def run_job(info, *, now: datetime, stop: threading.Event | None = None) -> None:
    """Задание "alert": доставки "ожидает" по одной(см. описание модуля). Итог задания и предупреждения - только пока
    задание у этого захвата(jobs.fence)."""
    alert_id = _alert_id(info.payload)
    with SessionLocal() as db:
        a = db.get(models.ResidentAlert, alert_id) if alert_id else None
        if a is None or a.status != "sending":
            db.rollback()
            text = None
        else:
            text = render(a)
            if _settle_interrupted(db, a.id, info.now()):
                log.warning("Предупреждение %s: доставки прерванной отправки отмечены «результат неизвестен»", a.code)
            db.commit()
    if text is None:
        def apply(db, job, a, now):
            jobs.complete(db, job, {"skipped": "предупреждение не найдено или не отправляется"})
        _outcome(info, apply)
        return
    token = get_settings().bot_token.get()
    if not token:
        _give_up(info, NOT_CONFIGURED)
        return
    done = 0
    with _client() as cli:
        while True:
            if (stop is not None and stop.is_set()) or done >= BATCH:
                _later(info, 0.0)
                return
            got = _claim(alert_id, info.now())
            if got is None:
                break
            did, kind, target = got
            try:
                mid = send(cli, token, target, text)
            except _Limited as e:
                _release(did, info.now())
                _later(info, e.retry_after, e.detail)
                return
            except _Transient as e:
                _release(did, info.now())
                log.warning("Предупреждение №%s: %s", alert_id, e.detail)
                _retry(info, e.detail)
                return
            except _TokenRejected as e:
                _release(did, info.now())
                log.warning("Предупреждение №%s: %s", alert_id, e.detail)
                _give_up(info, e.detail)
                return
            except _ChatRejected as e:
                _rejected(did, kind, target, e, info.now())
            except Exception:
                _release(did, info.now())
                log.exception("Предупреждение №%s: доставка не выполнена", alert_id)
                _retry(info, "Внутренняя ошибка отправки — подробности в журнале воркера")
                return
            else:
                _sent(did, kind, mid, info.now())
            done += 1
            http.SLEEP(1.0 / SEND_RATE)
    _done(info, done)
