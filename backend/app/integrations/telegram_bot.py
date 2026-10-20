"""Telegram-бот заявок жителей(отдельный проект saq-telegram-bot) - приёмник POST /api/intake/telegram
(integration-spec §12.3, план §7.2).

Контракт адаптера saq-telegram-bot не сверен - проверить по integration/telegram_intake.py, когда проект бота будет
доступен(contract_verified = False). Ниже - контракт, который реализует этот приёмник.

Доступ: сервисный токен SERVICE_API_TOKEN(тот же секрет, что у бота) в заголовке "Authorization: Bearer <токен>" или
"X-API-Key: <токен>". Токен проверяется до разбора тела: нет секрета на сервере - 503, нет заголовка - 401, неверный -
403. Отказы(401/403, а также 409/413/415/422 по заявке) и сбои сервера при сохранении(500, 507) записываются в
состояние подключения: бот с неверным токеном выглядит как "нет доступа", отклонённая или не сохранённая заявка - как
"ошибка", а не как "ожидает заявок". Успешный опрос статусов с верным токеном снимает прежнее "нет доступа".

POST /api/intake/telegram - заявка жителя:
- application/json - тело TelegramIntakeIn(до 1 МБ, без файлов);
- multipart/form-data - поле payload(строка JSON TelegramIntakeIn, до 1 МБ) и повторяющееся поле files(до 10 файлов,
  каждый до 20 МБ; изображения JPEG, PNG, WEBP, GIF не больше MEDIA_MAX_IMAGE_MB, видео MP4 или MOV). Тип файла
  определяется по содержимому; один неподходящий файл - отказ всей заявки(413/415/422), ничего не сохраняется.

  {"externalId": "3f2b6c1e-9d4a-4c7b-8e21-5a0f6d9c7b10",  // ID заявки от бота, 1-128 символов: буквы, цифры,
                                             //   . : @ / + = -. Повторная отправка - тот же ID. Лучше непрозрачный
                                             //   ID(UUID, который бот хранит для заявки), а не "{chat_id}:{message_id}":
                                             //   Telegram ID жителя попал бы в запись публикации, адреса статуса и
                                             //   журналы, а не только в заявку(applicant)
   "submittedAt": "2026-10-17T09:30:00Z",   // когда житель отправил заявку(с часовым поясом), необязательно
   "platform": "instagram",                 // instagram|telegram|tiktok|krisha|olx|facebook|other, по умолчанию other
   "account": "@developer",                 // аккаунт рекламодателя со слов жителя(до 255), необязательно
   "url": "https://…",                      // ссылка на публикацию(http/https, до 512, без логина и пароля,
                                             //   \\ < > " `), необязательно; иначе - 422 с пояснением
   "text": "…",                             // текст объявления(до 100 000 символов, хранится до 20 000)
   "comment": "…",                          // слова жителя(до 4000) - только в заявке, не в публикации
   "applicant": {"telegramUserId": "…", "chatId": "…", "username": "…", "name": "…", "phone": "…"},
   "media": [{"key": "…", "fileName": "…", "mime": "…"}]}   // описание files по порядку(до 10), необязательно

  Ключ i-го файла - media[i].key(например, file_unique_id Telegram: другой файл - другой ключ), иначе ключ по
  содержимому "tgbot:sha256:<sha256 файла>": замена фото под тем же externalId - новая версия, тот же файл - без
  изменений, одинаковые файлы без описания - один файл. Описанный в media файл, которого нет в files, - "бот не
  передал файл"(виден у публикации). Повторная доставка без files и без media оставляет файлы прежней версии. Часть
  files без имени файла(filename) и лишние поля формы - 422. Поля решений аналитика(status, complexId,
  linkConfirmed…, source, origin, id …) и неизвестные поля - 422. Ответ TelegramIntakeOut: новая заявка - 201
  "created", повторная доставка - 200 "unchanged"(копия не создаётся), изменённое содержимое - 200 "updated"(новая
  версия, история хранится). Файл, не попавший в БД(заявка отклонена или не сохранена), удаляется с диска.

GET /api/intake/telegram/submissions/{externalId} - статус заявки(SubmissionStatusOut); ID может содержать "/".
GET /api/intake/telegram/updates?since=<ISO с поясом>&limit=1..500 - заявки, статус которых менялся(или которые
получены) строго позже since, по времени изменения: бот сообщает жителю, что проверка начата или завершена. updatedAt -
UTC с микросекундами: следующий опрос - с since = updatedAt последнего полученного элемента(решение, записанное в
момент опроса, может прийти с временем чуть раньше since - надёжнее брать since с запасом в несколько секунд и не
сообщать жителю повторно тот же(externalId, status)).
Статусы: received "Заявка получена и ждёт проверки аналитиком", in_review "Заявка на проверке", confirmed "Проверка
завершена: признаки нарушения подтверждены аналитиком", dismissed "Проверка завершена: нарушение не подтверждено".

Хранение: заявка → единый приём(ingest: пространство "tgbot", подключение telegram_bot, публикация source = bot,
origin = collected, статус "new", без привязки к ЖК) и анализ в общей очереди. Сведения о заявителе(applicant) и его
комментарий - только в bot_submissions: ни в публикации, ни в снимке приёма(raw), ни в журнале, ни в ответах API их
нет. Повторная доставка дополняет их, а не стирает: отсутствующие или пустые поля заявителя остаются прежними,
комментарий заменяется только непустым. Аккаунт и ссылка, указанные жителем, хранятся у публикации как его слова и в
сопоставлении с ЖК не участвуют(D20).
"""
import hashlib
import logging
import re
from datetime import datetime, timedelta, UTC
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BeforeValidator, ConfigDict, Field, StringConstraints, ValidationError, field_validator, \
    model_validator
from pydantic_core import PydanticCustomError
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from .. import auth, ingest, media, models
from ..ads_service import BOT
from ..db import SessionLocal, get_db
from ..schemas import AD_TEXT_MAX, DECISION_KEYS, Camel, is_http_url, is_safe_link, strip_ctrl
from .base import Connector, MediaIn, Publication
from .intake_api import intake_status

log = logging.getLogger("saq.integrations.telegram_bot")

CODE = "telegram_bot"
NAMESPACE = "tgbot"  # пространство дедупликации заявок(не площадка публикации)
NOTE = "заявка жителя"
PAYLOAD_MAX = 1024 * 1024  # JSON заявки(тело application/json или поле payload)
MAX_FILES = media.BOT_MAX_FILES  # поле схемы "media" затеняет модуль media в теле класса
TEXT_MAX = 100000
COMMENT_MAX = 4000
FUTURE_SKEW = timedelta(minutes=5)
BOT_TYPES = frozenset(media.IMAGE_TYPES | {"video/mp4", "video/quicktime"})
NO_ACCOUNT = "не указан (заявка через бота)"
NOT_FOUND = "Заявка не найдена"
WRONG_TYPE = "Нужен JSON или multipart/form-data"
NO_FILE = "Бот не передал файл"
BAD_TYPE = "неподдерживаемый тип — нужен JPEG, PNG, WEBP, GIF, MP4 или MOV"
NO_FILENAME = "Файлы передаются в поле files с именем файла (filename)"
EXTRA_FIELD = "Лишние поля формы: нужны только payload и files"
TOKEN_REJECTS = (401, 403)  # отказы по сервисному токену("нет доступа"), остальные - отказы по заявке("ошибка")

SubmissionStatus = Literal["received", "in_review", "confirmed", "dismissed"]
SUBMISSION_LABELS = {"received": "Заявка получена и ждёт проверки аналитиком", "in_review": "Заявка на проверке",
                     "confirmed": "Проверка завершена: признаки нарушения подтверждены аналитиком",
                     "dismissed": "Проверка завершена: нарушение не подтверждено"}
_BY_AD_STATUS = {"new": "received", "needs_check": "received", "in_review": "in_review", "confirmed": "confirmed",
                 "dismissed": "dismissed"}
BotPlatform = Literal["instagram", "telegram", "tiktok", "krisha", "olx", "facebook", "other"]
NOT_A_LINK = "url: нужна ссылка http(s):// или пустая строка"
UNSAFE_LINK = ("url: ссылка не принимается — уберите из неё логин и пароль, кавычки и символы \\ < > ` и проверьте "
               "номер порта")


# ---------- схемы ----------

def _as_text(v: Any) -> Any:
    """ID Telegram приходят числами(Bot API) - принимаются и как строки; управляющие символы удаляются."""
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    return strip_ctrl(v)


def _short(n: int) -> Any:
    return Optional[Annotated[str, BeforeValidator(_as_text), StringConstraints(strip_whitespace=True, max_length=n)]]


class TelegramApplicantIn(Camel):
    """Заявитель - персональные данные: хранятся только в bot_submissions."""

    model_config = ConfigDict(extra="ignore")

    telegram_user_id: _short(32) = None
    chat_id: _short(32) = None
    username: _short(64) = None
    name: _short(128) = None
    phone: _short(32) = None


class TelegramMediaIn(Camel):
    """Описание i-го файла из поля files: стабильный ключ(например, file_unique_id Telegram)."""

    key: Annotated[str, BeforeValidator(_as_text), StringConstraints(strip_whitespace=True, min_length=1,
                                                                     max_length=255)]
    file_name: _short(255) = None
    mime: _short(64) = None


class TelegramIntakeIn(Camel):
    """Заявка жителя от бота. Решения аналитика не принимаются; неизвестные поля - 422."""

    model_config = ConfigDict(extra="forbid")

    external_id: Annotated[str, BeforeValidator(_as_text),
                           StringConstraints(strip_whitespace=True, min_length=1, max_length=128,
                                             pattern=r"^[\w.:@/+=-]+$")]
    submitted_at: datetime | None = None
    platform: Annotated[BotPlatform | None,
                        BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v)] = None
    account: Annotated[str, BeforeValidator(_as_text), StringConstraints(strip_whitespace=True, max_length=255)] | None = None
    url: Annotated[str, BeforeValidator(_as_text), StringConstraints(strip_whitespace=True, max_length=512)] | None = None
    text: Annotated[str, BeforeValidator(lambda v: "" if v is None else v), Field(max_length=TEXT_MAX)] = ""
    comment: Annotated[str, BeforeValidator(lambda v: v.replace("\x00", "") if isinstance(v, str) else v), StringConstraints(strip_whitespace=True, max_length=COMMENT_MAX)] | None = None
    applicant: TelegramApplicantIn | None = None
    media: Annotated[list[TelegramMediaIn], BeforeValidator(lambda v: [] if v is None else v),
                     Field(max_length=MAX_FILES)] = []

    @model_validator(mode="before")
    @classmethod
    def _no_decisions(cls, data: Any) -> Any:
        if isinstance(data, dict):
            found = [k for k in DECISION_KEYS if k in data]
            if found:
                raise PydanticCustomError("value_error", "поля решений аналитика в заявке не принимаются: {names}",
                                          {"names": ", ".join(found)})
        return data

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        """Ссылка, которую приём сохранит как есть: то, что ads_service.safe_url отбросил бы(не schemas.is_safe_link:
        учётные данные, неверный порт, \\ < > " `), - 422 с пояснением жителю, а не молча пустая ссылка у публикации."""
        if not v:
            return None
        if not is_http_url(v):
            raise PydanticCustomError("value_error", NOT_A_LINK)
        if not is_safe_link(v):
            raise PydanticCustomError("value_error", UNSAFE_LINK)
        return v

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        return v.replace("\x00", "")[:AD_TEXT_MAX]

    @field_validator("submitted_at")
    @classmethod
    def _submitted(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise PydanticCustomError("value_error",
                                      "submittedAt: укажите часовой пояс (например, 2026-10-17T09:30:00Z)")
        v = v.astimezone(UTC)
        if v > datetime.now(UTC) + FUTURE_SKEW:
            raise PydanticCustomError("value_error", "submittedAt в будущем — проверьте часы бота")
        return v

    @field_validator("media")
    @classmethod
    def _unique_keys(cls, v: list[TelegramMediaIn]) -> list[TelegramMediaIn]:
        keys = [m.key for m in v]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:
            raise PydanticCustomError("value_error", "media: ключи файлов повторяются: {keys}",
                                      {"keys": ", ".join(dup)})
        return v


def _precise(v: Any) -> Any:
    """Время изменения заявки - UTC с микросекундами: бот передаёт его обратно как since, и округление до секунд
    возвращало бы те же заявки при каждом опросе."""
    return models.as_utc(v).isoformat(timespec="microseconds") if isinstance(v, datetime) else v


PreciseTime = Annotated[str | None, BeforeValidator(_precise)]


class TelegramIntakeOut(Camel):
    external_id: str
    ad_id: str
    status: Literal["created", "updated", "unchanged"]
    submission_status: SubmissionStatus
    status_label: str


class SubmissionStatusOut(Camel):
    external_id: str
    ad_id: str | None = None
    status: SubmissionStatus
    status_label: str
    updated_at: PreciseTime = None


# ---------- отказы ----------

def _record(status: int, detail: str) -> None:
    from . import service  # лениво: реестр подключений загружается при импорте service

    service.record_reject(CODE, status, detail)


async def _reject(status: int, detail: str) -> HTTPException:
    """Отказ бота записывается в состояние подключения; возвращается исключение для raise."""
    await run_in_threadpool(_record, status, detail)
    return HTTPException(status, detail)


class NotSaved(RuntimeError):
    """Заявка не сохранена из-за ошибки БД. Текст - без параметров запроса: в них данные заявителя."""

    def __init__(self, cause: str):
        self.reason = f"ошибка базы данных ({cause})"
        super().__init__(f"Заявка не сохранена: {self.reason}")


def _failure(e: BaseException) -> str:
    """Пояснение сбоя сервера для статуса подключения: тип ошибки без её текста(в тексте могут быть пути и данные
    заявки), подробности - в журнале API."""
    reason = e.reason if isinstance(e, NotSaved) else f"ошибка сервера ({type(e).__name__})"
    return f"{reason} — заявка не сохранена, подробности в журнале API"


def _first_error(errors: list) -> str:
    err = errors[0] if errors else {}
    loc = ".".join(str(x) for x in err.get("loc", ()))
    return f"{loc}: {err.get('msg', '')}" if loc else str(err.get("msg", "ошибка проверки"))


async def _validate(data: Any, loc: tuple) -> TelegramIntakeIn:
    """JSON заявки → TelegramIntakeIn; ошибка - запись отказа и 422 в формате FastAPI(loc начинается с loc)."""
    try:
        return TelegramIntakeIn.model_validate_json(data)
    except ValidationError as e:
        # без "input": в ответ не возвращаются присланные значения(телефон, имя, комментарий жителя)
        errors = [{**err, "loc": (*loc, *err["loc"])} for err in e.errors(include_url=False, include_input=False)]
        await run_in_threadpool(_record, 422, _first_error(errors))
        raise RequestValidationError(errors) from None


async def _invalid(loc: tuple, msg: str) -> RequestValidationError:
    await run_in_threadpool(_record, 422, f"{'.'.join(loc)}: {msg}")
    return RequestValidationError([{"type": "value_error", "loc": loc, "msg": msg, "input": None}])


# ---------- чтение заявки ----------

async def _read_json(request: Request) -> bytes:
    """Тело application/json не больше PAYLOAD_MAX: по Content-Length сразу, без него - по мере чтения."""
    too_big = f"JSON заявки больше {PAYLOAD_MAX // media.MB} МБ — файлы передавайте через multipart/form-data"
    length = request.headers.get("content-length") or ""
    if length.isdigit() and int(length) > PAYLOAD_MAX:
        raise await _reject(413, too_big)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > PAYLOAD_MAX:
            raise await _reject(413, too_big)
        chunks.append(chunk)
    return b"".join(chunks)


def _check_file(n: int, data: bytes) -> None:
    """Проверка файла до записи(тип по сигнатуре, размер) - чтобы при отказе не сохранилось ничего."""
    if not data:
        raise media.MediaRejected(422, f"Файл №{n} пустой")
    mime = media.sniff(data[:media.HEAD])
    if mime not in BOT_TYPES:
        raise media.MediaRejected(415, f"Файл №{n}: {BAD_TYPE}")
    limit = min(media.BOT_MAX_FILE, media.limit_for(mime))
    if len(data) > limit:
        raise media.MediaRejected(413, f"Файл №{n} больше {limit / media.MB:g} МБ")


async def _read_multipart(request: Request) -> tuple[TelegramIntakeIn, list[bytes]]:
    """multipart/form-data: payload(JSON, ≤ 1 МБ) + files(≤ 10, каждый ≤ 20 МБ)."""
    try:
        form = await request.form(max_files=media.BOT_MAX_FILES, max_fields=2, max_part_size=PAYLOAD_MAX)
    except StarletteHTTPException as e:
        if e.status_code != 400:  # 413 от предела тела(UploadLimit) - как есть
            raise
        msg = str(e.detail or "")
        if "maximum size" in msg:  # предел - только у частей без filename: payload или файл, отправленный без имени
            raise await _reject(413, f"Поле payload больше {PAYLOAD_MAX // media.MB} МБ или файл передан без имени "
                                     "файла (filename)") from None
        if "Too many files" in msg:
            raise await _reject(422, f"Не больше {media.BOT_MAX_FILES} файлов в заявке") from None
        if "Too many fields" in msg:
            raise await _reject(422, EXTRA_FIELD) from None
        raise await _reject(422, "Неверный multipart/form-data") from None
    try:
        files: list[UploadFile] = []
        payloads = 0
        for name, value in form.multi_items():
            # часть без filename Starlette отдаёт строкой: файл в ней не сохранить - отказ, а не молчаливая потеря
            if name == "files":
                if not isinstance(value, UploadFile):
                    raise await _reject(422, NO_FILENAME)
                files.append(value)
            elif name == "payload":
                payloads += 1
            elif isinstance(value, UploadFile):
                raise await _reject(422, "Файлы передаются в поле files")
            else:
                raise await _reject(422, EXTRA_FIELD)
        if payloads > 1:
            raise await _reject(422, "Поле payload передано несколько раз")
        payload = form.get("payload")
        if isinstance(payload, UploadFile):  # JSON отправлен как файл - читается с тем же пределом
            payload = await payload.read(PAYLOAD_MAX + 1)
            if len(payload) > PAYLOAD_MAX:
                raise await _reject(413, f"Поле payload больше {PAYLOAD_MAX // media.MB} МБ")
        if payload is None or payload == "" or payload == b"":
            raise await _invalid(("body", "payload"), "Нужно поле payload — JSON заявки")
        body = await _validate(payload, ("body", "payload"))
        blobs: list[bytes] = []
        for n, f in enumerate(files, 1):
            data = await f.read(media.BOT_MAX_FILE + 1)
            try:
                _check_file(n, data)
            except media.MediaRejected as e:
                raise await _reject(e.status, e.detail) from None
            blobs.append(data)
        return body, blobs
    finally:
        await form.close()


# ---------- сохранение ----------

def content_key(data: bytes) -> str:
    """Ключ файла без описания в media - по содержимому: другой файл под тем же externalId(житель заменил фото в
    сообщении) - новая версия заявки, тот же файл - повторная доставка без изменений."""
    return f"{NAMESPACE}:sha256:{hashlib.sha256(data).hexdigest()}"


Planned = tuple[str, bytes | None]  # ключ, байты(None - не передан)


def _plan(body: TelegramIntakeIn, blobs: list[bytes]) -> list[Planned]:
    """Файлы заявки по порядку: (ключ, байты; None - описан в media, но не передан). Ключ - media[i].key, иначе
    content_key. Одинаковые файлы без описания - один файл. Проверка до записи файлов: при отказе не сохранится
    ничего."""
    described = {m.key for m in body.media}
    out: list[Planned] = []
    seen: set[str] = set()
    for i in range(max(len(body.media), len(blobs))):
        blob = blobs[i] if i < len(blobs) else None
        if i < len(body.media):
            out.append((body.media[i].key, blob))
            continue
        key = content_key(blob)  # файл без описания всегда передан: i < len(blobs)
        if key in described:
            raise media.MediaRejected(422, f"media: ключ {key} совпадает с ключом файла без описания")
        if key not in seen:
            seen.add(key)
            out.append((key, blob))
    return out


def _media(plan: list[Planned], stored: list[media.Stored | None]) -> list[MediaIn]:
    """Файлы заявки; описанный в media, но не переданный файл - "недоступен"(виден у публикации, а не потерян
    молча)."""
    out: list[MediaIn] = []
    for (key, _), st in zip(plan, stored, strict=True):
        if st is not None:
            out.append(MediaIn(key=key, kind="video" if st.mime in media.VIDEO_TYPES else "image", role="attachment",
                               stored=st, status="downloaded", origin_ref="bot"))
        else:
            out.append(MediaIn(key=key, role="attachment", status="unavailable", error=NO_FILE, origin_ref="bot"))
    return out


def _known_media(db: Session, external_id: str) -> list[MediaIn]:
    """Повторная доставка без files и без media: файлы прежней версии заявки остаются у публикации. Бот не передал их
    снова - это не удаление, и такая доставка не должна делать новую версию без фото."""
    k = ingest.known(db, *ingest.ident(NAMESPACE, external_id))
    if k is None or k.collector != CODE:
        return []
    return [MediaIn(key=key, role="attachment", origin_ref="bot") for key in k.media_keys]


def _settle_files(files: dict[str, tuple[str, bytes, bool]]) -> None:
    """Файлы этого запроса после транзакции(sha256 → (ключ хранилища, байты, записан этим запросом)). Записанный этим
    запросом файл, на который не ссылается ни одна запись media_assets(заявка не сохранена, отклонена или файл не
    понадобился), удаляется с диска: файлы жителей без записи в БД не найти и не удалить из интерфейса. Файл, на
    который ссылаются, но которого нет(его убрал параллельный неудачный запрос с теми же байтами), записывается снова.
    Никогда не бросает: исход заявки уже известен."""
    if not files:
        return
    try:
        A = models.MediaAsset
        with SessionLocal() as db:
            used = set(db.scalars(select(A.sha256).where(A.sha256.in_(list(files)))))
        for sha, (key, blob, created) in files.items():
            path = media.path_of(key)
            if sha not in used:
                if created:
                    path.unlink(missing_ok=True)
            elif not path.exists():
                media.store_bytes(blob, allowed=BOT_TYPES)
    except Exception as e:
        log.warning("Файлы заявки бота не проверены после приёма (лишние могли остаться на диске): %s",
                    type(e).__name__)


def _publication(body: TelegramIntakeIn, files: list[MediaIn], now: datetime) -> Publication:
    """Публичное содержимое заявки. Заявитель и его комментарий сюда не попадают."""
    return Publication(platform=body.platform or "other", namespace=NAMESPACE, external_id=body.external_id,
                       account=body.account or NO_ACCOUNT, url=body.url or "", text=body.text, collected_at=now,
                       published_at=body.submitted_at, media=files,
                       raw={"submittedAt": models.iso(body.submitted_at), "files": sum(m.stored is not None
                                                                                       for m in files)})


def _upsert_submission(db: Session, body: TelegramIntakeIn, res: ingest.IngestResult,
                       now: datetime) -> models.BotSubmission:
    """Заявка(заявитель, комментарий, связь с публикацией): INSERT … ON CONFLICT DO NOTHING и обновление.
    Повторная доставка дополняет сведения, а не стирает их: поля заявителя, которых в ней нет(или они пустые),
    остаются прежними; комментарий заменяется только непустым."""
    B = models.BotSubmission
    applicant = {k: v for k, v in (body.applicant.model_dump(by_alias=True) if body.applicant is not None
                                   else {}).items() if v}
    models.insert_ignore(db, B, {"external_id": body.external_id, "item_id": res.item_id, "ad_id": res.ad_id,
                                 "applicant": applicant, "comment": body.comment or None, "received_at": now,
                                 "updated_at": now}, ("external_id",))
    sub = db.scalar(select(B).where(B.external_id == body.external_id).with_for_update()
                    .execution_options(populate_existing=True))
    sub.item_id, sub.ad_id, sub.updated_at = res.item_id, res.ad_id, now
    merged = {**(sub.applicant or {}), **applicant}
    if merged != (sub.applicant or {}):
        sub.applicant = merged
    if body.comment:
        sub.comment = body.comment
    return sub


def _view(external_id: str, ad_id: str | None, ad_status: str | None, received_at: datetime | None,
          last_status_at: datetime | None) -> dict:
    status = _BY_AD_STATUS.get(ad_status or "", "received")
    times = [t for t in (models.as_utc(received_at), models.as_utc(last_status_at)) if t is not None]
    return {"external_id": external_id, "ad_id": ad_id, "status": status, "status_label": SUBMISSION_LABELS[status],
            "updated_at": max(times) if times else None}


def submission_view(db: Session, sub: models.BotSubmission) -> dict:
    """SubmissionStatusOut по заявке: статус - по статусу публикации; время - последнее изменение статуса аналитиком
    или получение заявки."""
    ad_status = last = None
    if sub.ad_id:
        ad_status = db.scalar(select(models.Ad.status).where(models.Ad.id == sub.ad_id))
        E = models.AuditEvent
        last = db.scalar(select(func.max(E.at)).where(E.ad_id == sub.ad_id, E.kind == "status"))
    return _view(sub.external_id, sub.ad_id, ad_status, sub.received_at, last)


def _submit_sync(body: TelegramIntakeIn, blobs: list[bytes]) -> tuple[ingest.IngestResult, dict | None]:
    """В пуле потоков: файлы → хранилище, заявка → единый приём + bot_submissions, commit. Файлы пишутся до
    транзакции(без долгой записи на диск внутри неё); записанные этим запросом и оставшиеся без записи в БД
    (отказ, сбой, файл не понадобился) удаляются - _settle_files."""
    plan = _plan(body, blobs)
    media.ensure_space(sum(len(b) for _, b in plan if b is not None))
    files: dict[str, tuple[str, bytes, bool]] = {}
    try:
        stored: list[media.Stored | None] = []
        for _, blob in plan:
            st = None
            if blob is not None:
                # created - файл создан этим запросом(media.store_bytes): удалить при неудаче можно только такой -
                # прежний файл мог только что записать другой запрос, ещё не успевший сохранить свою запись
                st = media.store_bytes(blob, allowed=BOT_TYPES)
                files.setdefault(st.sha256, (st.storage_key, blob, st.created))  # повтор байтов: признак первой записи
            stored.append(st)
        now = models.utcnow()
        with SessionLocal() as db:
            try:
                items = _media(plan, stored) if plan else _known_media(db, body.external_id)
                res = ingest.ingest_publication(db, _publication(body, items, now), collector=CODE, actor=BOT,
                                                note=NOTE)
                if res.status == "foreign":
                    db.rollback()
                    return res, None
                sub = _upsert_submission(db, body, res, now)
                view = submission_view(db, sub)
                db.commit()
                return res, view
            except SQLAlchemyError as e:
                # без цепочки исключений: текст ошибки БД содержит параметры запроса, а в них - данные заявителя;
                # прочие сбои откатывает закрытие сессии
                db.rollback()
                raise NotSaved(type(e).__name__) from None
    finally:
        _settle_files(files)


def _inline(schema: dict) -> dict:
    """JSON-схема без $defs/$ref - для requestBody в OpenAPI(тело разбирается вручную, после проверки токена)."""
    defs = schema.pop("$defs", {})

    def walk(v):
        if isinstance(v, dict):
            ref = v.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return walk(defs.get(ref.rsplit("/", 1)[-1], {}))
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(schema)


def _openapi() -> dict:
    schema = _inline(TelegramIntakeIn.model_json_schema(by_alias=True))
    form = {"type": "object", "required": ["payload"], "properties": {
        "payload": {"type": "string", "description": "JSON заявки (TelegramIntakeIn), до 1 МБ"},
        "files": {"type": "array", "maxItems": media.BOT_MAX_FILES,
                  "items": {"type": "string", "format": "binary"},
                  "description": "до 10 файлов по 20 МБ: JPEG, PNG, WEBP, GIF, MP4, MOV"}}}
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": schema},
                                                          "multipart/form-data": {"schema": form}}}}


# ---------- маршруты ----------

router = APIRouter(tags=["integrations"])


@router.post("/api/intake/telegram", status_code=201, response_model=TelegramIntakeOut, response_model_by_alias=True,
             openapi_extra=_openapi(),
             responses={200: {"description": "Повторная доставка (без изменений) или новая версия заявки"},
                        401: {"description": "Нет сервисного токена"}, 403: {"description": "Неверный токен"},
                        409: {"description": "Заявка с этим ID принята другим источником"},
                        413: {"description": "Заявка или файл больше предела"},
                        415: {"description": "Не JSON/multipart или неподдерживаемый файл"},
                        503: {"description": "Приём заявок не настроен"}})
async def intake(request: Request) -> JSONResponse:
    """Заявка жителя от бота(Authorization: Bearer или X-API-Key = SERVICE_API_TOKEN): JSON или multipart с файлами.
    Новая - 201, повторная доставка или изменённая - 200. Анализ ставится в общую очередь."""
    # первой строкой, до разбора тела; в пуле потоков: отказ записывается в БД и не должен держать цикл событий
    await run_in_threadpool(auth.require_service, request)
    try:
        ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype == "application/json" or (ctype.startswith("application/") and ctype.endswith("+json")):
            body, blobs = await _validate(await _read_json(request), ("body",)), []
        elif ctype == "multipart/form-data":
            body, blobs = await _read_multipart(request)
        else:
            raise await _reject(415, WRONG_TYPE)
        res, view = await run_in_threadpool(_submit_sync, body, blobs)
    except media.MediaRejected as e:
        raise await _reject(e.status, e.detail) from None
    except (StarletteHTTPException, RequestValidationError, ClientDisconnect):
        raise  # отказ уже записан(или бот сам оборвал запрос)
    except Exception as e:
        # сбой сервера - тоже в статусе подключения("ошибка"): иначе он выглядел бы как "ожидает заявок"
        await run_in_threadpool(_record, 500, _failure(e))
        raise
    if res.status == "foreign":
        from . import registry

        owner = registry.find(res.owner or "")
        name = owner.name if owner is not None else (res.owner or "другой источник")
        raise await _reject(409, f"Публикация собрана другим источником ({name}) — обновление отклонено")
    from . import service  # лениво, как в _record

    service.reset_reject_throttle(CODE)  # заявка принята: следующий отказ запишется сразу
    out = TelegramIntakeOut(external_id=body.external_id, ad_id=res.ad_id, status=res.status,
                            submission_status=view["status"], status_label=view["status_label"])
    return JSONResponse(out.model_dump(mode="json", by_alias=True), status_code=201 if res.status == "created" else 200)


@router.get("/api/intake/telegram/submissions/{external_id:path}", response_model=SubmissionStatusOut,
            response_model_by_alias=True, dependencies=[Depends(auth.require_service)],
            responses={404: {"description": NOT_FOUND}})
def submission(external_id: str, db: Session = Depends(get_db)):
    """Статус заявки для бота(житель узнаёт, что заявка на проверке или проверка завершена). Персональных данных
    в ответе нет."""
    note_token_ok(db)
    sub = db.scalar(select(models.BotSubmission).where(models.BotSubmission.external_id == external_id.strip()))
    if sub is None:
        raise HTTPException(404, NOT_FOUND)
    return submission_view(db, sub)


def _since(value: str) -> datetime:
    """since - ISO 8601 с часовым поясом. "+05:00", переданное в query без кодирования, приходит как " 05:00"."""
    s = re.sub(r" (\d{2}:?\d{2})$", r"+\1", value.strip())
    try:
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s[-1:] in ("Z", "z") else s)
    except ValueError:
        dt = None
    if dt is None or dt.tzinfo is None:
        raise RequestValidationError([{
            "type": "value_error", "loc": ("query", "since"), "input": value,
            "msg": "since: нужны дата и время ISO 8601 с часовым поясом, например 2026-10-17T12:00:00+05:00"}])
    return models.as_utc(dt)


@router.get("/api/intake/telegram/updates", response_model=list[SubmissionStatusOut], response_model_by_alias=True,
            dependencies=[Depends(auth.require_service)])
def updates(since: str = Query(..., max_length=64, description="ISO 8601 с часовым поясом; изменения строго позже"),
            limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Заявки, статус которых аналитик менял(или которые получены) после since - по времени изменения."""
    at = _since(since)
    note_token_ok(db)
    B, E, A = models.BotSubmission, models.AuditEvent, models.Ad
    ev = (select(E.ad_id.label("ad_id"), func.max(E.at).label("last"))
          .where(E.kind == "status", E.ad_id.in_(select(B.ad_id).where(B.ad_id.is_not(None))))
          .group_by(E.ad_id).subquery())
    changed = case((ev.c.last > B.received_at, ev.c.last), else_=B.received_at)
    q = (select(B.external_id, B.ad_id, A.status, B.received_at, ev.c.last)
         .outerjoin(ev, ev.c.ad_id == B.ad_id).outerjoin(A, A.id == B.ad_id)
         .where(or_(B.received_at > at, ev.c.last > at))
         .order_by(changed, B.id).limit(limit))
    return [_view(*r) for r in db.execute(q)]


# ---------- подключение ----------

def _token_ok_at(r: models.Collector | None) -> datetime | None:
    """Когда бот последний раз обратился с верным токеном после отказа по токену(state.tokenOkAt) или None."""
    at = ((r.state or {}) if r is not None else {}).get("tokenOkAt")
    try:
        return models.as_utc(datetime.fromisoformat(str(at))) if at else None
    except ValueError:
        return None


def note_token_ok(db: Session) -> None:
    """Запрос бота с верным токеном(опрос статусов): записанный раньше отказ 401/403 больше не показывается как "нет
    доступа", даже если новых заявок нет. Пишется только когда отказ по токену новее прежней отметки - регулярные
    опросы БД не нагружают. Никогда не бросает: учёт не меняет ответ боту."""
    from . import service

    try:
        r = service.row(db, CODE)
        rej = service.last_reject(r)
        if rej is None or rej["code"] not in TOKEN_REJECTS:
            return
        ok = _token_ok_at(r)
        if ok is not None and ok >= rej["at"]:
            return
        r = db.scalar(select(models.Collector).where(models.Collector.code == CODE).with_for_update()
                      .execution_options(populate_existing=True))
        r.state = {**(r.state or {}), "tokenOkAt": models.utcnow().isoformat()}
        db.commit()
    except Exception as e:
        db.rollback()
        log.warning("Успешное обращение бота не записано в состояние подключения: %s", type(e).__name__)


class TelegramBotConnector(Connector):
    code = CODE
    kind = "intake"
    platform = None
    group = "intake"
    name = "Telegram-бот заявок"
    via = "отдельный проект saq-telegram-bot → POST /api/intake/telegram"
    required_env = ("SERVICE_API_TOKEN",)
    capabilities = {"test": False, "collect": False, "toggle": False, "configure": False, "connect": False}
    secret_refs = ("env:SERVICE_API_TOKEN",)
    labels = {"working": "принимает заявки"}
    contract_verified = False
    limitation = "Контракт адаптера saq-telegram-bot с этой версией API не сверен."

    def computed_status(self, db, row, now: datetime) -> tuple[str, str] | None:
        """Живой статус приёмника: отказ боту новее последней принятой заявки - "нет доступа"(401/403) или "ошибка"
        (409/413/415/422, сбой сервера 500/507); заявка за 7 дней - "принимает заявки"; иначе - "ожидает заявок".
        Отказ по токену, после которого бот обращался с верным токеном, не показывается. Проверки и сбора нет."""
        if db is None:  # проверка из командной строки - без БД
            return None
        from . import service

        last = models.as_utc(db.scalar(select(func.max(models.BotSubmission.updated_at))))
        rej = service.last_reject(row)
        if rej is not None and rej["code"] in TOKEN_REJECTS:
            ok = _token_ok_at(row)
            if ok is not None and ok >= rej["at"]:
                row = None  # intake_status берёт из строки только последний отказ - он устарел
        return intake_status(row, now, last_at=last,
                             denied="Бот обращается с неверным сервисным токеном (последняя попытка {when})",
                             rejected="Последняя заявка отклонена: {detail}", working="Последняя заявка: {when}",
                             never="Заявок через бота ещё не было")


CONNECTOR = TelegramBotConnector()
