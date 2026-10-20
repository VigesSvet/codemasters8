"""2ГИС - геокодер(Catalog API 3.0 · items/geocode).

Это геоданные, а не сбор рекламы: по адресу ЖК, объекта открытых данных или объекта из рекламы(app.discovery - по
"ЖК <название>" и по адресу из рекламы) 2ГИС возвращает кандидатов(дом, город, корпус), выбирает аналитик. Первый
результат вслепую не берётся: "рекомендуется" - только единственный точный дом в городе, и это подсказка, а не выбор -
ничего не принимается автоматически. Координаты ЖК меняются только явным
решением аналитика("перенести координаты") с записью "было → стало" в журнал; сведения открытых данных не меняются.

К 2ГИС обращаются только по кнопке "Сверить адрес": открытие карточки читает сохранённые сверки. Кандидаты хранятся
DGIS_CACHE_TTL_DAYS дней(повторный запрос того же адреса - из кэша, без обращения к 2ГИС и без расхода квоты), затем
уборка воркера удаляет их, решение аналитика остаётся. Квота учитывается до запроса: по отпечатку ключа за период
(DGIS_QUOTA_PERIOD_START) и за сутки по времени Усть-Каменогорска(DGIS_DAILY_LIMIT). Локальный счётчик - оценка, точный
расход - в кабинете 2ГИС.

Ключ 2ГИС передаётся только параметром key(заголовка API не принимает): ссылка запроса не пишется в журнал и в тексты
ошибок, ключ зарегистрирован как секрет и маскируется везде. Порядок координат: у 2ГИС {lat, lon}, у ЖК {lat, lng}.

Сбой не выглядит пустой выдачей: "адрес не найден" - только явный ответ 2ГИС "ничего не найдено"(код 404) или пустой
список; ответ без result.items, с кандидатами, ни один из которых не разобран, или не-JSON 404 шлюза - ошибка формата
(502, в кэш не попадает). Одновременные "Сверить адрес" по одному объекту не дублируют запрос: сверка берёт короткую
блокировку объекта(строка worker_leases "dgis-check:…"), кэш и интервал 10 минут перепроверяются под ней. Ограничение
частоты от 2ГИС(429 или "лимит" по ключу) запоминается: подключение - "пауза: лимит запросов" до срока Retry-After,
новые сверки до него к 2ГИС не обращаются и квоту не расходуют.
"""
import hashlib
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, Literal
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import AfterValidator, BeforeValidator, StringConstraints
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..db import SessionLocal, get_db
from ..logs import register_secret
from ..risk_model import ALMATY
from ..settings import Settings, get_settings
from . import http
from .base import (AuthExpired, CheckResult, Connector, ConnectorError, NotConfigured, RateLimited, SourceError,
                   local_time, when_text)

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("saq.integrations.dgis")

BASE = "https://catalog.api.2gis.com"
GEOCODE = f"{BASE}/3.0/items/geocode"
# Поля как у контрольного запроса с двумя кандидатами; purpose_name, full_name, address_name, type приходят и так.
# Точность адреса - items[].search_attributes.dgis_address_details.precision(exact | number | near | street | …)
FIELDS = "items.point,items.address,items.adm_div,items.full_address_name,items.search_attributes.dgis_address_details"
CONTROL_QUERY = "Казахстан, Усть-Каменогорск, Увалиева 8/3"
CITY = "Усть-Каменогорск"
PROVIDER = "2gis"
PAGE_SIZE = 10
TIMEOUT_S = 15.0
QUERY_MAX = 200
REPEAT_S = 600  # новый запрос по тому же объекту - не чаще раза в 10 минут(кроме "Обновить запрос" администратора)
LOCK_TTL_S = 120  # блокировка сверки объекта: дольше любого запроса к 2ГИС; после сбоя процесса истекает сама
PAUSE_DEFAULT_S = 60.0  # 2ГИС ограничил частоту без Retry-After
EARTH_M = 6371008.8

NOT_CONFIGURED = "2ГИС не настроен на сервере (DGIS_API_KEY)"
KEY_REJECTED = "Ключ 2ГИС отклонён"
KEY_REJECTED_ROUTE = "Ключ 2ГИС отклонён — проверьте DGIS_API_KEY"
PROVIDER_LIMITED = "2ГИС ограничил частоту запросов — повторите позже"
PROVIDER_DOWN = "2ГИС не ответил — повторите позже"
UNKNOWN_FORMAT = "2ГИС вернул ответ в неизвестном формате"
BUSY = "Сверку этого объекта уже выполняют — повторите через минуту"
COMPLEX_NOT_FOUND = "ЖК не найден"
OBJECT_NOT_FOUND = "Объект открытых данных не найден"
CHECK_NOT_FOUND = "Сверка не найдена"
NO_CANDIDATE = "Этого кандидата в сверке нет"
STALE = "Кандидаты устарели — выполните сверку заново"
REGISTRY_READONLY = "Сведения открытого источника не изменяются — координаты 2ГИС сохраняются как сверка"
DISCOVERED_READONLY = ("У объекта из рекламы нет карточки ЖК — координаты кандидата переносятся при создании карточки "
                       "(create-complex, candidateId)")
POINT_CHANGED = "Координаты ЖК изменились после сверки — обновите карточку и выполните сверку заново"
NO_ADDRESS = "У объекта нет адреса — укажите запрос для сверки"
NOTHING_TO_REJECT = "2ГИС не нашёл адрес — отклонять нечего"
STATUS_TEXT = {"candidates": "ждёт решения", "not_found": "адрес не найден", "accepted": "принята",
               "rejected": "отклонена"}

_QUOTA_WORDS = re.compile(r"limit|quota|exceed|лимит|квот|превыш", re.I)
_CITY_PREFIX = re.compile(r"^\s*(?:г\.?\s*)?усть-каменогорск\s*,\s*", re.I)


def _now() -> datetime:
    """Текущее время маршрутов(тесты подменяют)."""
    return models.utcnow()


# ---------- геокодер ----------

@dataclass
class Candidate:
    """Кандидат адреса из 2ГИС. address_name - полный адрес с городом(full_address_name), если 2ГИС его дал."""
    id: str
    name: str
    full_name: str | None
    address_name: str | None
    purpose: str | None
    type: str | None
    precision: str | None
    lat: float
    lon: float
    city: str | None
    building_id: str | None = None

    def stored(self, *, distance: float | None, recommended: bool) -> dict:
        """Как кандидат хранится в geo_checks.candidates и отдаётся API(camelCase)."""
        return {"id": self.id, "name": self.name, "fullName": self.full_name, "addressName": self.address_name,
                "purpose": self.purpose, "type": self.type, "precision": self.precision, "lat": self.lat,
                "lon": self.lon, "city": self.city, "buildingId": self.building_id,
                "distanceM": round(distance, 1) if distance is not None else None, "recommended": recommended}


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _text(v: Any) -> str | None:
    """Строка или число из ответа 2ГИС → текст до 500 символов; пусто, объект, список или bool - None(не repr)."""
    if isinstance(v, bool) or not isinstance(v, str | int | float):
        return None
    return str(v).strip()[:500] or None


def _candidate(item: Any) -> Candidate | None:
    """Элемент items[] → кандидат; без ID или координат - пропускается."""
    cid = _text(item.get("id")) if isinstance(item, dict) else None
    if cid is None:
        return None
    point = item.get("point") if isinstance(item.get("point"), dict) else {}
    lat, lon = _num(point.get("lat")), _num(point.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    attrs = item.get("search_attributes") if isinstance(item.get("search_attributes"), dict) else {}
    details = attrs.get("dgis_address_details") if isinstance(attrs.get("dgis_address_details"), dict) else {}
    adm = item.get("adm_div") if isinstance(item.get("adm_div"), list) else []
    city = next((d.get("name") for d in adm if isinstance(d, dict) and d.get("type") == "city"), None)
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    addr = _text(item.get("full_address_name")) or _text(item.get("address_name"))
    return Candidate(id=cid[:64], name=_text(item.get("name")) or _text(item.get("building_name")) or addr
                     or "", full_name=_text(item.get("full_name")), address_name=addr,
                     purpose=_text(item.get("purpose_name")), type=_text(item.get("type")),
                     precision=_text(details.get("precision")), lat=lat, lon=lon, city=_text(city),
                     building_id=_text(address.get("building_id")))


def _code(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def geocode(query: str, *, client: httpx.Client, key: str) -> list[Candidate]:
    """Кандидаты адреса из 2ГИС. Код ответа - meta.code(если тело JSON), иначе HTTP-статус: 200 - кандидаты(пустой
    список result.items - []), 404 - [] только как явный ответ 2ГИС "ничего не найдено"(meta.code 404 или
    error.type itemNotFound), 400 - SourceError(не повторять), 401/403 - AuthExpired(403 про лимит/квоту -
    RateLimited), 429 - RateLimited, 408/5xx/сеть - SourceError(повторяемая). Сменившийся формат - ответ без
    result.items, кандидаты, ни один из которых не разобран, не-JSON 404 шлюза - SourceError(не повторять), а не
    "адрес не найден". Ссылка запроса(в ней ключ) не попадает ни в журнал, ни в тексты ошибок."""
    register_secret(key)
    params = {"key": key, "q": query, "fields": FIELDS, "type": "building", "locale": "ru_KZ",
              "page_size": str(PAGE_SIZE)}
    try:
        r = client.get(GEOCODE, params=params, headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        raise SourceError("2ГИС не ответил вовремя", retryable=True) from None
    except httpx.RequestError as e:
        raise SourceError(f"Сетевая ошибка при обращении к 2ГИС ({type(e).__name__})", retryable=True) from None
    except http.DownloadError as e:
        raise SourceError(e.detail, retryable=e.retryable) from None
    try:
        body = r.json()
    except ValueError:
        body = None
    meta = body.get("meta") if isinstance(body, dict) and isinstance(body.get("meta"), dict) else {}
    meta_code = _code(meta.get("code"))
    code = meta_code or r.status_code
    err = meta.get("error") if isinstance(meta.get("error"), dict) else {}
    message = (_text(err.get("message")) or "")[:200]
    err_type = (_text(err.get("type")) or "")[:64]
    if code == 200:
        result = body.get("result") if isinstance(body, dict) else None
        items = result.get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise SourceError(UNKNOWN_FORMAT, retryable=False)
        out: list[Candidate] = []
        for c in map(_candidate, items):
            if c is not None and all(c.id != o.id for o in out):
                out.append(c)
        if items and not out:  # кандидаты есть, но без ID или координат - формат сменился, а не "не найдено"
            log.warning("2ГИС: ни один из %d кандидатов не разобран (нет ID или координат)", len(items))
            raise SourceError(f"{UNKNOWN_FORMAT}: у кандидатов нет ID или координат", retryable=False)
        return out
    if code == 404:
        if meta_code == 404 or err_type == "itemNotFound":
            return []  # 2ГИС ответил "ничего не найдено"
        raise SourceError(f"{UNKNOWN_FORMAT} (код 404)", retryable=False)  # 404 не от API(шлюз, адрес API)
    if code == 400:
        raise SourceError(f"2ГИС отклонил запрос: {message or err_type or 'неверные параметры'}", retryable=False)
    if code in (401, 403):
        if _QUOTA_WORDS.search(f"{message} {err_type}"):
            raise RateLimited(http.retry_after(r.headers, 3600),
                              f"2ГИС: лимит запросов по ключу исчерпан ({message or err_type})")
        raise AuthExpired(KEY_REJECTED)
    if code == 429:
        raise RateLimited(http.retry_after(r.headers, 60), "2ГИС ограничил частоту запросов")
    if code == 408 or code >= 500:
        raise SourceError(f"2ГИС не ответил (код {code})", retryable=True)
    raise SourceError(f"2ГИС вернул неожиданный ответ (код {code})", retryable=False)


def _same_city(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip().casefold() == b.strip().casefold())


def recommended(cands: list[Candidate], *, city: str = CITY) -> str | None:
    """ID единственного точного дома(precision exact, тип building) в городе; несколько или ни одного - None
    (выбирает аналитик). Это подсказка, а не решение."""
    exact = [c for c in cands if c.precision == "exact" and (c.type or "building") == "building"
             and _same_city(c.city, city)]
    return exact[0].id if len(exact) == 1 else None


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по большому кругу, м(как haversineM на фронтенде)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(h)))


# ---------- квота ----------

class Quota(RateLimited):
    """Локальная квота 2ГИС исчерпана(до запроса к 2ГИС): маршрут - 429 с этим текстом, воркер - "пауза"."""

    def __init__(self, detail: str, *, retry_after: float):
        super().__init__(detail, retry_after=retry_after)


def quota_names(s: Settings, now: datetime) -> tuple[str, str]:
    """Счётчики квоты: ("dgis:{отпечаток ключа}:{начало периода|all}",
    "dgis-day:{отпечаток}:{дата в Усть-Каменогорске}")."""
    fp = s.key_fp("DGIS_API_KEY") or "none"
    start = s.dgis_quota_period_start.isoformat() if s.dgis_quota_period_start else "all"
    day = models.as_utc(now).astimezone(ALMATY).strftime("%Y-%m-%d")
    return f"dgis:{fp}:{start}", f"dgis-day:{fp}:{day}"


def _until_midnight(now: datetime) -> float:
    local = models.as_utc(now).astimezone(ALMATY)
    nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max((nxt - local).total_seconds(), 60.0)


def reserve(reserve_fn: Callable[[str, int], bool], s: Settings, now: datetime, *,
            used_fn: Callable[[str], int] | None = None,
            release_fn: Callable[[str], None] | None = None) -> None:
    """Учесть один запрос до обращения к 2ГИС: сначала счётчик периода, затем дневной. Исчерпан любой - Quota(запрос
    не выполняется); если дневной исчерпан после учёта периода, учёт периода возвращается(release_fn).
    reserve_fn(name, limit) - service.reserve_quota(маршруты) или ctx.reserve_quota(проверка подключения)."""
    period, day = quota_names(s, now)
    if not reserve_fn(period, int(s.dgis_quota_limit)):
        used = used_fn(period) if used_fn is not None else s.dgis_quota_limit
        raise Quota(f"Лимит запросов 2ГИС по ключу исчерпан ({used} из {s.dgis_quota_limit})", retry_after=86400)
    if not reserve_fn(day, int(s.dgis_daily_limit)):
        if release_fn is not None:
            release_fn(period)
        raise Quota(f"Дневной лимит сверок 2ГИС исчерпан ({s.dgis_daily_limit})", retry_after=_until_midnight(now))


def release(name: str) -> None:
    """Вернуть один учтённый запрос(запрос к 2ГИС не выполнялся) - своя короткая сессия."""
    Q = models.QuotaCounter
    with SessionLocal() as db:
        db.execute(update(Q).where(Q.name == name, Q.count > 0).values(count=Q.count - 1)
                   .execution_options(synchronize_session=False))
        db.commit()


def _used(name: str) -> int:
    from . import service

    with SessionLocal() as db:
        return service.quota_used(db, name)


# ---------- пауза после ограничения 2ГИС ----------
# collectors.state.pause = {until, keyFp, prev, prevDetail}: 2ГИС ограничил частоту запросов по ключу с этим отпечатком
# до until. Действует, пока хранимый статус - "пауза: лимит запросов"(удачная проверка подключения или смена ключа её
# снимают). Локальная квота(Quota) отметку не ставит: её счётчики и так не пропускают запрос.

def _pause_s(e: RateLimited) -> float:
    return float(e.retry_after or PAUSE_DEFAULT_S)


def _pause_of(r: models.Collector | None) -> dict | None:
    p = (r.state or {}).get("pause") if r is not None else None
    return p if isinstance(p, dict) else None


def _when(until: datetime, now: datetime) -> str:
    """"14:05", если сегодня по местному времени, иначе "17.10.2026 14:05"."""
    same_day = local_time(until, "%Y-%m-%d") == local_time(now, "%Y-%m-%d")
    return local_time(until) if same_day else when_text(until)


def paused_until(db: Session, s: Settings, now: datetime) -> datetime | None:
    """До какого времени к 2ГИС не обращаться(2ГИС ограничил частоту по текущему ключу); None - паузы нет."""
    from . import service

    r = service.row(db, CONNECTOR.code)
    p = _pause_of(r)
    if p is None or r.status != "rate_limited" or p.get("keyFp") != s.key_fp("DGIS_API_KEY"):
        return None
    try:
        until = models.as_utc(datetime.fromisoformat(str(p.get("until"))))
    except (TypeError, ValueError):
        return None
    return until if until > models.as_utc(now) else None


def _provider_limited(db: Session, s: Settings, now: datetime, e: RateLimited) -> None:
    """2ГИС ограничил частоту при сверке: подключение "пауза: лимит запросов" до now + Retry-After(видно на странице
    "Подключения"), отметка паузы - в состояние. Прежний статус запоминается: первая удачная сверка после паузы его
    возвращает. Сбой учёта не меняет ответ."""
    from . import service

    until = models.as_utc(now) + timedelta(seconds=_pause_s(e))
    try:
        r = service.row(db, CONNECTOR.code, create=True, lock=True)  # state - чтение-изменение-запись
        old = _pause_of(r) or {}
        prev, prev_detail = ((old.get("prev"), old.get("prevDetail")) if r.status == "rate_limited"
                             else (r.status, r.status_detail))
        r.state = {**(r.state or {}), "pause": {"until": models.iso(until), "keyFp": s.key_fp("DGIS_API_KEY"),
                                                "prev": prev, "prevDetail": prev_detail}}
        r.last_error, r.last_error_at, r.next_run_at = e.detail, models.as_utc(now), until
        service.transition(db, CONNECTOR.code, "rate_limited", f"{e.detail} — повтор после {_when(until, now)}",
                           at=now)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("Пауза 2ГИС не записана в состояние подключения", exc_info=True)


def _provider_back(db: Session, now: datetime) -> None:
    """Сверка прошла после паузы: отметка снимается, статус "пауза" возвращается к прежнему(в транзакции сверки)."""
    from . import service

    if _pause_of(service.row(db, CONNECTOR.code)) is None:
        return  # обычный случай - без блокировки строки подключения
    r = service.row(db, CONNECTOR.code, lock=True)  # state - чтение-изменение-запись, отметку перечитать
    p = _pause_of(r)
    if p is None:
        return
    r.state = {k: v for k, v in (r.state or {}).items() if k != "pause"}
    if r.status == "rate_limited":
        service.transition(db, CONNECTOR.code, str(p.get("prev") or "unchecked"), p.get("prevDetail"), at=now)


# ---------- блокировка сверки объекта ----------

def _lock_name(kind: str, target_id: str) -> str:
    return "dgis-check:" + hashlib.sha256(f"{kind}\n{target_id}".encode()).hexdigest()[:40]


def _lock(db: Session, name: str) -> str | None:
    """Взять блокировку сверки объекта(строка worker_leases): свободна или истекла - метка владельца, иначе None(идёт
    другая сверка этого объекта). Атомарно на SQLite и PostgreSQL: условный UPDATE истёкшей строки, иначе INSERT … ON
    CONFLICT DO NOTHING. Фиксирует транзакцию. Срок - по часам сервера, а не по _now()."""
    L = models.WorkerLease
    now = models.utcnow()
    holder = uuid.uuid4().hex
    values = {"holder": holder, "expires_at": models.as_utc(now + timedelta(seconds=LOCK_TTL_S)), "heartbeat_at": now}
    res = db.execute(update(L).where(L.name == name, L.expires_at < now).values(**values)
                     .execution_options(synchronize_session=False))
    ok = res.rowcount == 1 or models.insert_ignore(db, L, {"name": name, **values, "info": {"kind": "dgis-check"}},
                                                   ("name",))
    db.commit()
    return holder if ok else None


def _unlock(name: str, holder: str) -> None:
    """Снять свою блокировку - своя короткая сессия; не снялась - истечёт через LOCK_TTL_S."""
    L = models.WorkerLease
    try:
        with SessionLocal() as db:
            db.execute(delete(L).where(L.name == name, L.holder == holder)
                       .execution_options(synchronize_session=False))
            db.commit()
    except Exception:
        log.warning("Блокировка сверки 2ГИС не снята — истечёт сама", exc_info=True)


# ---------- подключение ----------

class DgisConnector(Connector):
    code = "dgis"
    kind = "geo"
    platform = None
    group = "geo"
    name = "2ГИС — геокодер"
    via = "Catalog API 3.0 · items/geocode"
    required_env = ("DGIS_API_KEY",)
    capabilities = {"test": True, "collect": False, "toggle": False, "configure": False, "connect": False}
    secret_refs = ("env:DGIS_API_KEY",)
    labels = {"ready": "готов"}
    limitation = "Только геоданные: кандидаты адреса для карточки ЖК, не сбор рекламы. Результаты кэшируются временно."

    def check(self, ctx: "RunContext") -> CheckResult:
        """Контрольный адрес(Увалиева 8/3): ok - 2ГИС вернул хотя бы одного кандидата. Запрос учитывается в квоте
        (проверка из командной строки - не учитывается). Ограничение частоты от 2ГИС ставит отметку паузы(воркер
        переводит подключение в "пауза: лимит запросов"): сверки адресов до её конца к 2ГИС не обращаются."""
        s = ctx.settings
        key = s.dgis_api_key.get()
        if not key:
            raise NotConfigured("Нет на сервере: DGIS_API_KEY")
        # проверка из командной строки: ctx.reserve_quota не учитывает запрос и всегда успешна - release не вызывается
        reserve(ctx.reserve_quota, s, ctx.now(), used_fn=ctx.quota_used, release_fn=release)
        try:
            with ctx.client(timeout=TIMEOUT_S, base_url=BASE) as c:
                cands = geocode(CONTROL_QUERY, client=c, key=key)
        except RateLimited as e:
            until = models.as_utc(ctx.now()) + timedelta(seconds=_pause_s(e))
            ctx.save_state(pause={"until": models.iso(until), "keyFp": s.key_fp("DGIS_API_KEY")})
            raise
        if isinstance(ctx.state.get("pause"), dict):
            ctx.save_state(pause=None)  # 2ГИС снова отвечает
        rec = recommended(cands)
        period, day = quota_names(s, ctx.now())
        facts = {"candidates": len(cands), "recommendedId": rec, "used": ctx.quota_used(period),
                 "usedToday": ctx.quota_used(day)}
        if not cands:
            return CheckResult(False, f"2ГИС не нашёл контрольный адрес «{CONTROL_QUERY}» — кандидатов нет", facts)
        best = next((c for c in cands if c.id == rec), None)
        if best is not None:
            where = best.address_name or best.full_name or best.name
            detail = f"Кандидатов: {len(cands)}; точный адрес — «{where}» ({best.name}, объект {best.id})"
        else:
            detail = f"Кандидатов: {len(cands)}; точного совпадения нет — выбор за аналитиком"
        return CheckResult(True, detail, facts)


CONNECTOR = DgisConnector()
router = APIRouter(tags=["integrations"])


# ---------- схемы API ----------

TargetKind = Literal["complex", "registry", "discovered"]
CheckStatus = Literal["candidates", "not_found", "accepted", "rejected"]


GeoQuery = Annotated[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_MAX),
                               AfterValidator(schemas.no_nul)] | None,
                     BeforeValidator(schemas.blank_to_none)]
TargetId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=96),
                     AfterValidator(schemas.no_nul)]
CandidateId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
                        AfterValidator(schemas.no_nul)]


class GeoCandidateOut(schemas.Camel):
    id: str
    name: str = ""
    full_name: str | None = None
    address_name: str | None = None
    purpose: str | None = None
    type: str | None = None
    precision: str | None = None
    lat: float
    lon: float
    city: str | None = None
    distance_m: float | None = None  # от точки в карточке на момент сверки
    recommended: bool = False  # подсказка: единственный точный дом в городе; не выбор


class GeoPointOut(schemas.Camel):
    lat: float
    lng: float


class GeoCheckOut(schemas.Camel):
    id: int
    target_kind: TargetKind
    target_id: str
    provider: str
    query: str
    status: CheckStatus
    candidates: list[GeoCandidateOut] = []
    fetched_at: schemas.IsoTime = None
    cached: bool = False  # ответ из кэша - 2ГИС повторно не запрашивался
    purged: bool = False  # кандидаты удалены по сроку хранения
    requested_by: str | None = None
    chosen_id: str | None = None
    chosen: GeoCandidateOut | None = None  # выбранный кандидат(остаётся и после удаления кандидатов по сроку)
    applied: bool = False
    prev_point: GeoPointOut | None = None  # точка в карточке до решения
    decided_by: str | None = None
    decided_at: schemas.IsoTime = None
    comment: str | None = None
    expires_at: schemas.IsoTime = None


class GeoStatusOut(schemas.Camel):
    configured: bool
    used: int
    limit: int | None = None
    period_start: str | None = None
    used_today: int
    daily_limit: int | None = None
    cache_days: int


class GeoCheckIn(schemas.Camel):
    target_kind: TargetKind
    target_id: TargetId
    query: GeoQuery = None  # не указан - "Казахстан, Усть-Каменогорск, {адрес объекта}"
    author: schemas.HumanName
    force: bool = False  # "Обновить запрос" мимо кэша - только администратор


class GeoAcceptIn(schemas.Camel):
    candidate_id: CandidateId
    author: schemas.HumanName
    apply: bool = False  # перенести координаты в карточку ЖК(только для ЖК)
    comment: schemas.OptComment = None


class GeoRejectIn(schemas.Camel):
    author: schemas.HumanName
    comment: schemas.OptComment = None


# ---------- объект сверки ----------

@dataclass
class _Target:
    kind: str
    id: str
    name: str
    address: str | None
    point: dict | None  # {lat, lng} в карточке или None
    raw: tuple | None = None  # (lat, lng) ЖК как в БД - для условного UPDATE; у объекта открытых данных - None

    @property
    def complex_id(self) -> str | None:
        return self.id if self.kind == "complex" else None


def _point(lat: Any, lng: Any) -> dict | None:
    la, lo = _num(lat), _num(lng)
    if la is None or lo is None or (la == 0 and lo == 0):
        return None
    return {"lat": la, "lng": lo}


def _target(db: Session, kind: str, tid: str) -> _Target:
    """ЖК(models.Complex), объект открытых данных(ключ записей источников) или объект из рекламы(id); нет - 404."""
    if kind == "complex":
        c = db.get(models.Complex, tid)
        if c is None:
            raise HTTPException(404, COMPLEX_NOT_FOUND)
        return _Target(kind, c.id, c.name, c.address, _point(c.lat, c.lng), (c.lat, c.lng))
    if kind == "discovered":
        # только ASCII-цифры: "²".isdigit() - True, а int("²") - ValueError(было 500)
        o = db.get(models.DiscoveredObject, int(tid)) if tid.isascii() and tid.isdigit() and len(tid) < 10 else None
        if o is None:
            raise HTTPException(404, "Объект из рекламы не найден")
        return _Target(kind, str(o.id), o.name, o.address_hint, None)
    from .. import importer
    from .. import registry as opendata

    recs = opendata.records(db, tid)
    if not recs:
        raise HTTPException(404, OBJECT_NOT_FOUND)
    obj = importer.group(recs)[0]
    return _Target(kind, tid, obj.get("name") or tid, obj.get("address"), _point(obj.get("lat"), obj.get("lng")))


def _moved(before: dict | None, now: dict | None) -> bool:
    """Точка в карточке изменилась после сверки(обе "нет координат" - не изменилась)."""
    def key(p: dict | None) -> tuple | None:
        return (_num(p.get("lat")), _num(p.get("lng"))) if p else None
    return key(before) != key(now)


def default_query(address: str) -> str:
    """"Казахстан, Усть-Каменогорск, {адрес}"(без повтора "г. Усть-Каменогорск" из адреса открытых данных)."""
    return f"Казахстан, {CITY}, {_CITY_PREFIX.sub('', address.strip())}"


def norm_query(query: str) -> str:
    """Ключ кэша: без учёта регистра и лишних пробелов."""
    return " ".join(query.casefold().split())[:255]


def _audit(db: Session, who: str, what: str, *, complex_id: str | None, at: datetime) -> None:
    """Событие журнала(data) от имени аналитика; complex_id - только у сверки ЖК(внешний ключ на complexes)."""
    db.add(models.AuditEvent(kind="data", who=who, what=what, at=models.as_utc(at), complex_id=complex_id))


def check_out(g: models.GeoCheck, s: Settings, *, cached: bool = False) -> dict:
    fetched = models.as_utc(g.fetched_at)
    purged = g.purged_at is not None
    return {"id": g.id, "target_kind": g.target_kind, "target_id": g.target_id, "provider": g.provider,
            "query": g.query, "status": g.status, "candidates": list(g.candidates or []), "fetched_at": fetched,
            "cached": cached, "purged": purged, "requested_by": g.requested_by, "chosen_id": g.chosen_id,
            "chosen": dict(g.chosen) if g.chosen else None, "applied": bool(g.applied),
            "prev_point": dict(g.prev_point) if g.prev_point else None, "decided_by": g.decided_by,
            "decided_at": g.decided_at, "comment": g.comment,
            "expires_at": fetched + timedelta(days=s.dgis_cache_ttl_days) if fetched and not purged else None}


def _decided(g: models.GeoCheck) -> HTTPException:
    if g.status == "not_found":
        return HTTPException(409, NOTHING_TO_REJECT)
    who = f" ({g.decided_by})" if g.decided_by else ""
    return HTTPException(409, f"Сверку уже решили: {STATUS_TEXT.get(g.status, g.status)}{who}")


def _lost_race(db: Session, check_id: int) -> HTTPException:
    """Условный UPDATE решения не прошёл: сверку одновременно решили, очистили по сроку или удалили. Транзакция
    откатывается, ответ - по свежему состоянию строки."""
    db.rollback()
    g = db.get(models.GeoCheck, check_id, populate_existing=True)
    if g is None:
        return HTTPException(404, CHECK_NOT_FOUND)
    if g.status == "candidates" and g.purged_at is not None:
        return HTTPException(409, STALE)
    return _decided(g)


def _key_rejected(db: Session, now: datetime) -> None:
    """Ключ отклонён при сверке: подключение "истёк доступ"(видно на странице "Подключения"), пока администратор не
    проверит его заново. Сбой учёта не меняет ответ."""
    from . import service

    try:
        r = service.row(db, CONNECTOR.code, create=True)
        r.last_error, r.last_error_at = KEY_REJECTED, models.as_utc(now)
        service.transition(db, CONNECTOR.code, "expired", KEY_REJECTED, at=now)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("Статус подключения 2ГИС не обновлён после отказа ключа", exc_info=True)


# ---------- маршруты ----------

@router.get("/api/geo/status", response_model=GeoStatusOut, response_model_by_alias=True)
def geo_status(db: Session = Depends(get_db)):
    """Квота и срок кэша 2ГИС(без обращения к 2ГИС)."""
    from . import service

    s = get_settings()
    configured = not s.missing("DGIS_API_KEY")
    used = used_today = 0
    if configured:
        period, day = quota_names(s, _now())
        used, used_today = service.quota_used(db, period), service.quota_used(db, day)
    return {"configured": configured, "used": used, "limit": s.dgis_quota_limit,
            "period_start": s.dgis_quota_period_start.isoformat() if s.dgis_quota_period_start else None,
            "used_today": used_today, "daily_limit": s.dgis_daily_limit, "cache_days": s.dgis_cache_ttl_days}


@router.get("/api/geo/checks", response_model=list[GeoCheckOut], response_model_by_alias=True)
def geo_checks(target_kind: TargetKind | None = Query(None, alias="targetKind"),
               target_id: str | None = Query(None, alias="targetId", max_length=96),
               limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Сохранённые сверки объекта, новые первыми - без обращения к 2ГИС."""
    G = models.GeoCheck
    q = select(G)
    if target_kind is not None:
        q = q.where(G.target_kind == target_kind)
    if target_id is not None:
        q = q.where(G.target_id == target_id)
    s = get_settings()
    return [check_out(g, s) for g in db.scalars(q.order_by(G.fetched_at.desc(), G.id.desc()).limit(limit))]


@router.post("/api/geo/checks", response_model=GeoCheckOut, response_model_by_alias=True, status_code=201,
             responses={200: {"description": "Ответ из кэша (cached)"}, 429: {"description": "Лимит запросов"},
                        502: {"description": "2ГИС не ответил или отклонил запрос"},
                        503: {"description": "2ГИС не настроен или ключ отклонён"}})
def geo_check(body: GeoCheckIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """"Сверить адрес": кандидаты из кэша(200, cached) или новый запрос к 2ГИС(201). force - мимо кэша, только
    администратор. Ничего не принимается автоматически. Новый запрос - под блокировкой объекта: вторая одновременная
    сверка того же объекта - 429, кэш и интервал 10 минут перепроверяются после взятия блокировки."""
    now = _now()
    s = get_settings()
    if body.force:
        auth.require_admin(request)
    t = _target(db, body.target_kind, body.target_id)
    query = body.query or (default_query(t.address) if t.address and t.address.strip() else None)
    if not query:
        raise HTTPException(422, NO_ADDRESS)
    qnorm = norm_query(query)
    if not body.force:
        hit = _cached(db, t, qnorm, s, now)
        if hit is not None:
            response.status_code = 200
            return check_out(hit, s, cached=True)
    key = s.dgis_api_key.get()
    if not key:
        raise HTTPException(503, NOT_CONFIGURED)
    db.commit()  # чтение закончено; дальше - запись блокировки(SQLite: без перехода чтения в запись)
    lock = _lock_name(t.kind, t.id)
    holder = _lock(db, lock)
    if holder is None:
        raise HTTPException(429, BUSY)
    try:
        return _check_locked(db, body, t, query, qnorm, key, s, now, response)
    finally:
        _unlock(lock, holder)


def _cached(db: Session, t: _Target, qnorm: str, s: Settings, now: datetime) -> models.GeoCheck | None:
    """Кэш: последняя сверка того же объекта и запроса моложе DGIS_CACHE_TTL_DAYS с неудалёнными кандидатами. Нет, а
    объект сверяли меньше 10 минут назад(другим запросом) - 429. У объекта из рекламы два запроса(название и адрес):
    интервал считается по каждому запросу."""
    G = models.GeoCheck
    same_query = (G.target_kind == t.kind, G.target_id == t.id, G.query_norm == qnorm)
    hit = db.scalar(select(G).where(*same_query, G.purged_at.is_(None),
                                    G.fetched_at >= models.as_utc(now - timedelta(days=s.dgis_cache_ttl_days)))
                    .order_by(G.fetched_at.desc(), G.id.desc()).limit(1))
    if hit is not None:
        return hit
    repeat = same_query if t.kind == "discovered" else same_query[:2]
    last = db.scalar(select(G.fetched_at).where(*repeat).order_by(G.fetched_at.desc()).limit(1))
    wait = (models.as_utc(last) + timedelta(seconds=REPEAT_S) - models.as_utc(now)).total_seconds() \
        if last is not None else 0
    if wait > 0:
        raise HTTPException(429, f"Сверку этого объекта уже запрашивали — повторите через "
                                 f"{max(1, math.ceil(wait / 60))} мин")
    return None


def _check_locked(db: Session, body: GeoCheckIn, t: _Target, query: str, qnorm: str, key: str, s: Settings,
                  now: datetime, response: Response) -> dict:
    """Сверка под блокировкой объекта: перепроверка кэша и интервала, пауза 2ГИС, квота, запрос, запись."""
    from . import service

    if not body.force:
        hit = _cached(db, t, qnorm, s, now)  # пока ждали блокировку, сверку могла выполнить другая вкладка
        if hit is not None:
            response.status_code = 200
            return check_out(hit, s, cached=True)
    until = paused_until(db, s, now)
    if until is not None:
        raise HTTPException(429, f"2ГИС ограничил частоту запросов — повторите после {_when(until, now)}")
    db.commit()  # чтение закончено: квота и сеть - без открытой транзакции
    try:
        reserve(lambda name, limit: service.reserve_quota(name, limit, now), s, now, used_fn=_used,
                release_fn=release)
    except Quota as e:
        raise HTTPException(429, e.detail) from None
    try:
        with http.client(timeout=TIMEOUT_S, base_url=BASE) as client:
            cands = geocode(query, client=client, key=key)
    except AuthExpired:
        log.warning("2ГИС: ключ отклонён при сверке адреса")
        _key_rejected(db, now)
        raise HTTPException(503, KEY_REJECTED_ROUTE) from None
    except RateLimited as e:
        log.warning("2ГИС: %s", e.detail)
        _provider_limited(db, s, now, e)
        raise HTTPException(429, PROVIDER_LIMITED) from None
    except ConnectorError as e:
        log.warning("2ГИС: сверка адреса не выполнена: %s", e.detail)
        raise HTTPException(502, PROVIDER_DOWN if e.retryable else e.detail) from None
    rec = recommended(cands)
    p = t.point
    stored = [c.stored(distance=distance_m(p["lat"], p["lng"], c.lat, c.lon) if p else None,
                       recommended=c.id == rec) for c in cands]
    G = models.GeoCheck
    g = G(target_kind=t.kind, target_id=t.id, provider=PROVIDER, query=query, query_norm=qnorm, candidates=stored,
          status="candidates" if stored else "not_found", applied=False, prev_point=p, requested_by=body.author,
          fetched_at=models.as_utc(now))
    db.add(g)
    where = f" (объект из рекламы «{t.name}»)" if t.kind == "discovered" else ""
    _audit(db, body.author, f"Сверка адреса в 2ГИС{where}: «{query}» — кандидатов: {len(stored)}",
           complex_id=t.complex_id, at=now)
    _provider_back(db, now)
    db.commit()
    return check_out(g, s)


@router.post("/api/geo/checks/{check_id}/accept", response_model=GeoCheckOut, response_model_by_alias=True,
             responses={409: {"description": "Сверку уже решили, кандидаты устарели или координаты изменились"}})
def geo_accept(body: GeoAcceptIn, check_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """Аналитик принимает кандидата. apply - перенести координаты в карточку ЖК(условным UPDATE: если координаты
    изменились после сверки - 409), "было → стало" - в журнал. Объект открытых данных не меняется."""
    s = get_settings()
    now = _now()
    G = models.GeoCheck
    g = db.get(G, check_id)
    if g is None:
        raise HTTPException(404, CHECK_NOT_FOUND)
    if g.status in ("accepted", "rejected"):  # решение важнее срока кэша: не "выполните сверку заново"
        raise _decided(g)
    if g.purged_at is not None:
        raise HTTPException(409, STALE)
    # у "адрес не найден" кандидатов нет - 422 ниже
    cand = next((dict(c) for c in g.candidates or [] if str(c.get("id")) == body.candidate_id), None)
    if cand is None:
        raise HTTPException(422, NO_CANDIDATE)
    if body.apply and g.target_kind != "complex":
        raise HTTPException(422, DISCOVERED_READONLY if g.target_kind == "discovered" else REGISTRY_READONLY)
    t = _target(db, g.target_kind, g.target_id)
    cur = t.point
    lat2, lon2 = _num(cand.get("lat")), _num(cand.get("lon"))
    if body.apply and (t.raw is None or lat2 is None or lon2 is None or _moved(g.prev_point, cur)):
        raise HTTPException(409, POINT_CHANGED)
    res = db.execute(update(G).where(G.id == g.id, G.status == "candidates", G.purged_at.is_(None))
                     .values(status="accepted", chosen_id=str(cand["id"]), chosen=cand, applied=body.apply,
                             prev_point=cur, decided_by=body.author, decided_at=models.as_utc(now),
                             comment=body.comment)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise _lost_race(db, check_id)
    full = cand.get("fullName") or cand.get("name") or cand.get("addressName") or cand["id"]
    if body.apply:
        C = models.Complex
        old_lat, old_lng = t.raw
        moved = db.execute(update(C).where(C.id == t.id, C.lat == old_lat, C.lng == old_lng)
                           .values(lat=lat2, lng=lon2).execution_options(synchronize_session=False))
        if moved.rowcount != 1:
            db.rollback()
            raise HTTPException(409, POINT_CHANGED)
        was = f"{old_lat:.6f}, {old_lng:.6f}" if old_lat is not None and old_lng is not None else "нет координат"
        what = (f"Координаты ЖК «{t.name}» уточнены по 2ГИС после проверки аналитиком: было {was} → стало "
                f"{lat2:.6f}, {lon2:.6f} (объект 2ГИС {cand['id']}, «{full}»)")
    else:
        what = f"Сверка адреса 2ГИС принята: «{full}» (объект {cand['id']}); координаты в карточке не изменены"
    if body.comment:
        what += f". Комментарий: {body.comment}"
    _audit(db, body.author, what, complex_id=t.complex_id, at=now)
    db.commit()
    return check_out(db.get(G, check_id, populate_existing=True), s)


@router.post("/api/geo/checks/{check_id}/reject", response_model=GeoCheckOut, response_model_by_alias=True,
             responses={409: {"description": "Сверку уже решили"}})
def geo_reject(body: GeoRejectIn, check_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """"Отклонить всех": ни один кандидат не подходит; координаты в карточке не меняются."""
    s = get_settings()
    now = _now()
    G = models.GeoCheck
    g = db.get(G, check_id)
    if g is None:
        raise HTTPException(404, CHECK_NOT_FOUND)
    if g.status != "candidates":
        raise _decided(g)
    res = db.execute(update(G).where(G.id == g.id, G.status == "candidates")
                     .values(status="rejected", decided_by=body.author, decided_at=models.as_utc(now),
                             comment=body.comment)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise _lost_race(db, check_id)
    complex_id = g.target_id if g.target_kind == "complex" and db.get(models.Complex, g.target_id) else None
    what = f"Кандидаты 2ГИС отклонены: «{g.query}» — координаты в карточке не изменены"
    if body.comment:
        what += f". Комментарий: {body.comment}"
    _audit(db, body.author, what, complex_id=complex_id, at=now)
    db.commit()
    return check_out(db.get(G, check_id, populate_existing=True), s)
