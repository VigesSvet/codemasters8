"""Задания воркера в таблице jobs: сбор(collect), проверка подключения(test), анализ публикации(analyze), отправка
предупреждения жителям(alert, app.alerts). Не больше одного активного задания на dedupe_key(частичный уникальный
индекс): гонка двух постановок решается INSERT … ON CONFLICT DO NOTHING и повторным чтением, без SAVEPOINT.

Захват: на PostgreSQL - SELECT … FOR UPDATE SKIP LOCKED, на SQLite - условный UPDATE по прочитанному id(повтор до трёх
раз). Анализ публикации, по которой уже идёт анализ, не захватывается; у захваченного анализа dedupe_key обнуляется -
новая версия публикации ставит новое задание, пока идёт прежнее.

execute: сессия S1(задание и запуск - "выполняется") фиксируется и закрывается ДО обработчика - сеть, OCR и ffmpeg идут
без открытой транзакции записи; итог применяется в новой сессии. Итог обработчика определяет статус запуска и подключения
(docs/ARCHITECTURE.md, "Очередь заданий и воркер"): сбой никогда не выглядит как "нет новых", платный сбор после ошибки останавливается до ручной проверки.
Обработчики импортируют реестр, сервис, контекст и анализ лениво: реестр подключений загружается при импорте
service, ранний импорт дал бы цикл.

Итог пишется, только если задание всё ещё у того же захвата(fence: выполняется, тот же воркер, то же число попыток):
задание, которое recover_stale или requeue_own отдали другому воркеру, прежний исполнитель уже не перезапишет. Время
итога(повтор, отсрочка, пауза частоты, следующий сбор) - момент итога, а не захвата: долгий запуск не делает паузу
прошедшей ещё до записи. Брошенное задание(попытки исчерпаны в recover_stale / requeue_own) получает тот же итог
ошибки, что и обычный сбой: подключение - "ошибка", платный сбор остановлен, анализ - событие "не выполнен".
Исключение, вышедшее из обработчика мимо его итогов, - повтор с паузой или тот же итог брошенного задания(_crashed).
"""
import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, not_, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from . import models
from .db import SessionLocal
# модуль-лист подключений(без реестра и сервиса): общий потолок паузы от источника
from .integrations.base import pause_seconds
from .logs import redact

log = logging.getLogger("saq.jobs")

PRIORITY = {"test": 10, "alert": 8, "collect": 5, "analyze": 0}
SOFT_DEADLINE_S = 1200  # RunContext.time_left(): мягкий срок одного выполнения задания
ACTIVE = ("queued", "running")
MAX_DEFERRALS = 240  # отсрочек одного задания(Deferred), дальше - ошибка
MAX_CONTINUATIONS = 10_000  # плановых продолжений одного задания(defer planned=True), дальше - ошибка
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
CLAIM_TRIES = 3
SYSTEM = "Система"
INTERNAL = "Внутренняя ошибка сборщика — подробности в журнале воркера"
PAID_STOP = "Платный сбор остановлен после ошибки — запустите проверку вручную"
CRASHED = "Задание прервано внутренней ошибкой воркера — подробности в журнале воркера"
ANALYSIS_DB = "Ошибка базы данных при анализе — подробности в журнале воркера"
ANALYSIS_FAILED = "Внутренняя ошибка анализа — подробности в журнале воркера"
SCHEDULABLE = frozenset({"ready", "working", "partial", "error", "rate_limited"})
DETAIL_MAX = 500
MONOTONIC: Callable[[], float] = time.monotonic  # время выполнения задания(тесты подменяют)


# ---------- постановка ----------

def active(db: Session, dedupe_key: str) -> models.Job | None:
    """Активное(в очереди или выполняется) задание с этим ключом."""
    J = models.Job
    return db.scalar(select(J).where(J.dedupe_key == dedupe_key, J.status.in_(ACTIVE)).order_by(J.id.desc()).limit(1))


def enqueue(db: Session, kind: str, *, dedupe_key: str | None = None, collector: str | None = None,
            run_id: int | None = None, ad_id: str | None = None, content_version: int | None = None,
            payload: dict | None = None, run_after: datetime | None = None, priority: int | None = None,
            max_attempts: int = 5) -> tuple[models.Job, bool]:
    """Задание в очередь(в сессии db, без commit). С dedupe_key: активное задание с тем же ключом уже есть -
    возвращается оно(created=False); для analyze его content_version поднимается до новой версии.
    Возвращает(задание, создано ли)."""
    now = models.utcnow()
    values = {"kind": kind, "status": "queued", "priority": PRIORITY.get(kind, 0) if priority is None else priority,
              "collector": collector, "run_id": run_id, "ad_id": ad_id, "content_version": content_version,
              "dedupe_key": dedupe_key, "attempts": 0, "max_attempts": max_attempts,
              "run_after": models.as_utc(run_after) or now, "payload": dict(payload or {}), "created_at": now}
    if dedupe_key is None:
        job = models.Job(**values)
        db.add(job)
        db.flush()
        return job, True
    for _ in range(3):  # активное задание могло завершиться между INSERT и SELECT - вставка пройдёт со второго раза
        created = models.insert_ignore(db, models.Job, values, ("dedupe_key",), where=text(models.ACTIVE_JOB))
        job = active(db, dedupe_key)
        if job is None:
            continue
        newer = content_version is not None and (job.content_version or 0) < content_version
        if not created and kind == "analyze" and newer:
            job.content_version = content_version
        return job, created
    raise RuntimeError(f"Задание {dedupe_key} не поставлено в очередь: активное задание меняется слишком часто")


# ---------- захват ----------

def _claimable(now: datetime):
    """Условие "можно захватить": в очереди, срок наступил; анализ - только если по этой публикации анализ не идёт."""
    J, R = models.Job, aliased(models.Job)
    busy = exists().where(R.kind == "analyze", R.status == "running", R.ad_id == J.ad_id, R.id != J.id)
    return and_(J.status == "queued", J.run_after <= models.as_utc(now), not_(and_(J.kind == "analyze", busy)))


def _ad_lock_key(ad_id: str) -> int:
    """Ключ рекомендательной блокировки PostgreSQL "анализ публикации ad_id"(64 бита со знаком)."""
    return int.from_bytes(hashlib.sha256(f"analyze:{ad_id}".encode()).digest()[:8], "big", signed=True)


def _try_claim(db: Session, job_id: int, worker_id: str, now: datetime) -> bool:
    """Условный UPDATE одного задания(в сессии db, без commit): True - захвачено этим воркером.
    Анализ на PostgreSQL: сначала блокировка "анализ этой публикации" до конца транзакции - два воркера, захватывающие
    одновременно два задания одной публикации, идут по очереди, и условие "анализ не идёт" второго(новый снимок
    оператора UPDATE в READ COMMITTED) видит зафиксированный захват первого. На SQLite запись и так последовательна."""
    J = models.Job
    found = db.execute(select(J.kind, J.ad_id).where(J.id == job_id)).one_or_none()
    kind, ad_id = (found[0], found[1]) if found is not None else (None, None)
    values: dict[str, Any] = {"status": "running", "locked_by": worker_id, "locked_at": models.as_utc(now),
                              "attempts": J.attempts + 1}
    if kind == "analyze":
        values["dedupe_key"] = None  # новая версия публикации может поставить своё задание, пока идёт это
        if ad_id and db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ad_lock_key(ad_id)})
    res = db.execute(update(J).where(J.id == job_id, _claimable(now)).values(**values)
                     .execution_options(synchronize_session=False))
    return res.rowcount == 1


def claim(db: Session, worker_id: str, now: datetime) -> models.Job | None:
    """Следующее задание(приоритет, срок, id) → "выполняется" у этого воркера; фиксирует транзакцию. None - нечего
    делать. PostgreSQL: FOR UPDATE SKIP LOCKED; SQLite: условный UPDATE, при гонке - до трёх попыток."""
    J = models.Job
    pg = db.get_bind().dialect.name == "postgresql"
    for _ in range(CLAIM_TRIES):
        q = select(J.id).where(_claimable(now)).order_by(J.priority.desc(), J.run_after, J.id).limit(1)
        if pg:
            q = q.with_for_update(skip_locked=True, of=J)
        job_id = db.scalar(q)
        if job_id is None:
            db.rollback()
            return None
        if _try_claim(db, job_id, worker_id, now):
            db.commit()
            return db.get(J, job_id, populate_existing=True)
        db.rollback()
    return None


# ---------- итоги ----------

def backoff(attempts: int) -> float:
    """Пауза перед повтором: 60 · 2^(попытка−1) с, не больше часа, ±20 %."""
    return min(BACKOFF_BASE_S * 2 ** max(attempts - 1, 0), BACKOFF_MAX_S) * random.uniform(0.8, 1.2)  # noqa: S311


def complete(db: Session, job: models.Job, result: dict | None = None) -> None:
    job.status, job.finished_at, job.locked_by = "done", models.utcnow(), None
    if result is not None:
        job.result = dict(result)


def fail(db: Session, job: models.Job, error: str | None) -> None:
    job.status, job.finished_at, job.locked_by = "failed", models.utcnow(), None
    job.last_error = redact(error)


def cancel(db: Session, job: models.Job, *, now: datetime | None = None) -> bool:
    """Отменить задание, которое ещё ждёт в очереди(в сессии db, без commit): условный UPDATE "ещё в очереди" -
    задание, которое воркер успел захватить, не становится "отменённым" посреди выполнения. False - задание уже
    выполняется или завершено, ничего не изменено."""
    J = models.Job
    finished = models.as_utc(now) or models.utcnow()
    res = db.execute(update(J).where(J.id == job.id, J.status == "queued")
                     .values(status="cancelled", finished_at=finished, locked_by=None)
                     .execution_options(synchronize_session=False))
    db.expire(job)  # строка изменена мимо ORM(или её изменил другой процесс) - следующее чтение свежее
    return res.rowcount == 1


def retry(db: Session, job: models.Job, error: str | None, *, now: datetime) -> bool:
    """Повтор с паузой(backoff): задание снова в очереди. False - попытки исчерпаны(вызывающий завершает ошибкой)."""
    job.last_error = redact(error)
    if (job.attempts or 0) >= (job.max_attempts or 1):
        return False
    job.status, job.locked_by = "queued", None
    job.run_after = models.as_utc(now) + timedelta(seconds=backoff(job.attempts or 1))
    return True


def defer(db: Session, job: models.Job, seconds: float, payload_update: dict | None, *, now: datetime,
          planned: bool = False) -> bool:
    """Отсрочка без расхода попытки(ожидание внешнего запуска): в очередь через seconds с, payload дополняется.
    planned - плановое продолжение после сделанной части работы(очередная партия): считается не в MAX_DEFERRALS, а в
    MAX_CONTINUATIONS. Вызывающий передаёт planned=True, только если это выполнение продвинуло работу - тогда
    продолжений не больше, чем частей работы, а потолок - защита от ошибки вызывающего.
    False - отсрочек или продолжений больше предела: задание дальше не откладывается(вызывающий завершает ошибкой)."""
    payload = {**(job.payload or {}), **(payload_update or {})}
    counter, limit = ("continuations", MAX_CONTINUATIONS) if planned else ("deferrals", MAX_DEFERRALS)
    payload[counter] = int(payload.get(counter) or 0) + 1
    job.payload = payload
    if payload[counter] > limit:
        return False
    job.status, job.locked_by = "queued", None
    job.attempts = max((job.attempts or 0) - 1, 0)
    job.run_after = models.as_utc(now) + timedelta(seconds=pause_seconds(seconds, 0.0))
    return True


def fence(db: Session, job_id: int, worker_id: str | None, attempts: int | None, *,
          what: str = "итог не записан") -> models.Job | None:
    """Задание всё ещё у этого захвата(выполняется, тот же воркер, то же число попыток)? Условный UPDATE-"замок"
    (на PostgreSQL блокирует строку до конца транзакции, на SQLite берёт запись) и чтение свежей строки; None -
    задание уже вернули в очередь, завершили или отдали другому воркеру: итог этого исполнителя не записывается.
    worker_id None - без проверки(прямой вызов)."""
    J = models.Job
    if worker_id is None:
        return db.get(J, job_id)
    res = db.execute(update(J).where(J.id == job_id, J.status == "running", J.locked_by == worker_id,
                                     J.attempts == (attempts or 0)).values(locked_by=worker_id)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:  # без отката: в той же транзакции могут быть чужие изменения(такт лидера)
        log.warning("Задание №%s уже не у воркера %s (возвращено в очередь или выполнено другим) — %s",
                    job_id, worker_id, what)
        return None
    return db.get(J, job_id, populate_existing=True)


def _requeue(db: Session, job_id: int, locked_by: str | None, attempts: int | None, now: datetime, reason: str, *,
             stale_before: datetime | None = None) -> str | None:
    """Задание, которое перестал выполнять воркер(захват locked_by + attempts, прочитанный до вызова): снова в
    очередь(попытка уже израсходована) или итог ошибки, если попыток больше нет. Возвращает новый статус; None -
    задание тем временем завершилось или его уже забрали(условный UPDATE по прочитанному захвату не совпал).
    stale_before - задание брошено, только если блокировка так и не продлена до этого момента: пульс воркера мог
    продлить её между чтением и "замком" fence."""
    job = fence(db, job_id, locked_by, attempts, what="повторно не возвращается")
    if job is None:
        return None
    if stale_before is not None and job.locked_at is not None and models.as_utc(job.locked_at) >= stale_before:
        return None
    if (job.attempts or 0) >= (job.max_attempts or 1):
        _abandoned(db, job, now, reason)
        return "failed"
    job.status, job.locked_by, job.last_error = "queued", None, reason
    job.run_after = models.as_utc(now)
    return "queued"


def _abandoned(db: Session, job: models.Job, now: datetime, reason: str) -> None:
    """Задание брошено окончательно(попытки исчерпаны, воркер упал или завис). Сбор и проверка - итог как у сбоя без
    повтора: запуск "ошибка", подключение "ошибка" с паузой, платный сбор остановлен - иначе расписание сразу
    начало бы новый платный запуск. Анализ - "не выполнен" с событием в журнале публикации. Предупреждение
    жителям - итог отправки по доставкам(app.alerts.abandoned): "не отправлено" или "частично" с причиной."""
    if job.kind in ("collect", "test"):
        from .integrations import registry

        try:
            conn = registry.get(job.collector or "")
        except KeyError:
            conn = None
        _failure_outcome(db, job, conn, _Failure("internal", "failed", "error", reason), now, retry_ok=False)
        return
    if job.kind == "analyze":
        _analysis_failed(db, job, reason, now)
    else:
        fail(db, job, reason)
    if job.kind == "alert":
        from . import alerts

        alerts.abandoned(db, job, reason, now)
    elif job.run_id is not None:
        from .integrations import service

        service.finish_run(db, job.run_id, "failed", error_code="internal", error=reason, now=now)


def _requeue_each(db: Session, rows: list[models.Job], now: datetime, reason: str, *,
                  stale_before: datetime | None = None) -> list[tuple[int, str]]:
    """_requeue по каждому заданию своей транзакцией(commit): сбой итога одного задания(не БД) - откат, запись в
    журнал и следующее задание, иначе одно задание навсегда держало бы возврат остальных. Ошибка БД - выше.
    Возвращает(id, вид) возвращённых в очередь или завершённых ошибкой заданий."""
    # захват - снимок до первого commit/rollback: откат истекает объекты сессии, и перечитанный захват другого
    # воркера(задание тем временем вернули и захватили снова) прошёл бы fence
    claims = [(job.id, job.kind, job.locked_by, job.attempts) for job in rows]
    out = []
    for job_id, kind, locked_by, attempts in claims:
        try:
            done = _requeue(db, job_id, locked_by, attempts, now, reason, stale_before=stale_before) is not None
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            log.exception("Задание №%s: итог брошенного задания не записан", job_id)
            continue
        if done:
            out.append((job_id, kind))
    return out


def recover_stale(db: Session, now: datetime, lease_s: int) -> int:
    """Выполняемые задания без продления блокировки дольше lease_s(воркер упал или завис) → снова в очередь(или
    итог ошибки, если попытки исчерпаны). Фиксирует транзакцию после каждого задания."""
    J = models.Job
    cutoff = models.as_utc(now) - timedelta(seconds=lease_s)
    q = select(J).where(J.status == "running", or_(J.locked_at.is_(None), J.locked_at < cutoff))
    rows = db.scalars(q.execution_options(populate_existing=True)).all()
    done = _requeue_each(db, list(rows), now, "Задание прервано: воркер перестал отвечать", stale_before=cutoff)
    for job_id, kind in done:
        log.warning("Задание №%s (%s) не продлевалось дольше %s с — возвращено в очередь или завершено ошибкой",
                    job_id, kind, lease_s)
    return len(done)


def requeue_own(db: Session, worker_id: str) -> int:
    """Старт воркера: его же задания в статусе "выполняется"(прошлый процесс с этим SAQ_WORKER_ID упал) → в очередь
    (или итог ошибки, если попытки исчерпаны). Только когда аренда worker:{id} уже у этого
    процесса(worker.wait_for_worker_id): иначе это задания живого процесса с тем же ID. Фиксирует транзакцию после
    каждого задания."""
    J = models.Job
    q = select(J).where(J.status == "running", J.locked_by == worker_id).execution_options(populate_existing=True)
    rows = db.scalars(q).all()
    return len(_requeue_each(db, list(rows), models.utcnow(), "Задание прервано перезапуском воркера"))


# ---------- выполнение ----------

@dataclass
class JobInfo:
    """Снимок задания на момент запуска: обработчик работает без открытой сессии. worker_id и attempts - захват,
    по которому итог проверяется(fence); base + прошедшее с started - время итога(now())."""
    id: int
    kind: str
    collector: str | None
    run_id: int | None
    ad_id: str | None
    attempts: int
    max_attempts: int
    payload: dict = field(default_factory=dict)
    trigger: str = "manual"
    worker_id: str | None = None
    base: datetime | None = None
    started: float = field(default_factory=lambda: MONOTONIC())  # lambda: тесты подменяют jobs.MONOTONIC

    def now(self) -> datetime:
        """Текущее время задания(UTC): время, переданное воркером(захват), плюс прошедшее с начала выполнения."""
        base = self.base if self.base is not None else models.utcnow()
        return base + timedelta(seconds=MONOTONIC() - self.started)


def execute(job_id: int, *, worker_id: str, now: datetime | None = None,
            stop: threading.Event | None = None) -> None:
    """Выполнить захваченное задание. S1(задание и запуск - "выполняется") фиксируется до обработчика; итог -
    в новой сессии и только пока задание у этого захвата. Обработчик упал мимо своих итогов - повтор или итог
    брошенного задания(_crashed); наружу выходят только ошибки БД при записи итога."""
    started = MONOTONIC()
    now = models.as_utc(now) or models.utcnow()
    with SessionLocal() as db:
        job = db.get(models.Job, job_id)
        if job is None or job.status != "running" or job.locked_by != worker_id:
            return
        trigger = "manual"
        if job.run_id is not None:
            from .integrations import service

            run = service.start_run(db, job.run_id, now)
            trigger = run.trigger if run is not None else trigger
        info = JobInfo(job.id, job.kind, job.collector, job.run_id, job.ad_id, job.attempts or 0,
                       job.max_attempts or 1, dict(job.payload or {}), trigger, worker_id=worker_id, base=now,
                       started=started)
        db.commit()
    handler = HANDLERS.get(info.kind)
    if handler is None:
        with SessionLocal() as db:
            job = fence(db, info.id, info.worker_id, info.attempts)
            if job is not None:
                fail(db, job, f"Неизвестный вид задания: {info.kind}")
                db.commit()
        return
    try:
        handler(info, now=now, stop=stop)
    except Exception:
        log.exception("Задание №%s (%s): необработанная ошибка обработчика", info.id, info.kind)
        _crashed(info)


def _crashed(info: JobInfo) -> None:
    """Исключение вышло из обработчика(ошибка в записи итога, сбой БД): повтор с паузой или итог брошенного задания
    (_abandoned) - иначе задание оставалось бы "выполняется" до recover_stale и вернулось бы без паузы."""
    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        now = info.now()
        if retry(db, job, CRASHED, now=now):
            if job.kind == "analyze":
                _analysis_requeued(db, job.ad_id)
        else:
            _abandoned(db, job, now, CRASHED)
        db.commit()


# ---------- сбор и проверка подключения ----------

@dataclass
class _Failure:
    """Ошибка подключения для итога: код ошибки запуска, статус запуска и подключения, текст, повторять ли."""
    error_code: str
    run_status: str
    status: str | None  # None - статус подключения не меняется(вычисляется)
    detail: str
    retryable: bool = False
    exc: BaseException | None = None


def _failure(e: BaseException) -> _Failure:
    from .integrations import base, http

    if isinstance(e, base.NotConfigured):
        return _Failure("not_configured", "not_configured", "not_configured", e.detail)
    if isinstance(e, base.AuthExpired):
        return _Failure("auth_expired", "failed", "expired", e.detail)
    if isinstance(e, base.AccessDenied):
        return _Failure("access_denied", "failed", "denied", e.detail)
    if isinstance(e, base.AccessBlocked):
        return _Failure("access_check", "access_check", "access_check", e.detail)
    if isinstance(e, base.RateLimited):
        return _Failure("rate_limited", "rate_limited", "rate_limited", e.detail, exc=e)
    if isinstance(e, base.BadConfig):
        return _Failure("bad_config", "failed", "error", e.detail)
    if isinstance(e, base.ConnectorError):
        return _Failure(e.code or "source_error", "failed", "error", e.detail, retryable=bool(e.retryable))
    if isinstance(e, http.DownloadError):  # клиент подключения отклонил запрос(чужой хост) или сеть
        return _Failure("source_error", "failed", "error", e.detail, retryable=e.retryable)
    import httpx

    if isinstance(e, httpx.TimeoutException):
        return _Failure("source_error", "failed", "error", "Источник не ответил вовремя", retryable=True)
    if isinstance(e, httpx.TransportError):
        return _Failure("source_error", "failed", "error", f"Сетевая ошибка при обращении к источнику "
                                                           f"({type(e).__name__})", retryable=True)
    return _Failure("internal", "failed", "error", INTERNAL, retryable=True)


def _interval_s(conn, row) -> float:
    return float((row.interval_min if row is not None and row.interval_min else conn.default_interval_min) or 60) * 60


def _connector_job(info: JobInfo, *, now: datetime, stop: threading.Event | None, test: bool) -> None:
    from .integrations import base, registry, service
    from .integrations.context import RunContext
    from .settings import get_settings

    code = info.collector or ""
    try:
        conn = registry.get(code)
    except KeyError:
        _finish(info, None, _Failure("internal", "failed", None, f"Подключение «{code}» не найдено"))
        return
    s = get_settings()
    with SessionLocal() as db:
        row = service.row(db, code, create=True)
        config, state = dict(row.config or {}), dict(row.state or {})
        run = db.get(models.CollectionRun, info.run_id) if info.run_id is not None else None
        ext = run.external_run_id if run is not None else None
        db.commit()
    missing = conn.missing(s, row)
    if missing:  # ключей нет на сервере: статус "не настроен" вычисляется, хранимый статус не меняется
        detail = conn.missing_detail(s, row) or f"Нет на сервере: {', '.join(missing)}"
        _finish(info, conn, _Failure("not_configured", "not_configured", None, detail))
        return
    owner = (info.worker_id, info.attempts) if info.worker_id is not None else None
    ctx = RunContext(code, conn, settings=s, run_id=info.run_id, job_id=info.id, trigger=info.trigger, config=config,
                     state=state, payload=dict(info.payload), now=now, stop=stop, external_run_id=ext, owner=owner)
    try:
        if test:
            check = conn.check(ctx)
            if not isinstance(check, base.CheckResult):
                raise TypeError("check() вернул не CheckResult")
            if not check.ok:
                raise base.SourceError(check.detail or "Проверка подключения не прошла", retryable=False)
            _test_ok(info, conn, check)
        else:
            conn.collect(ctx)
            _collect_ok(info, conn, ctx)
    except base.Deferred as d:
        _deferred(info, conn, d)
    except Exception as e:
        f = _failure(e)
        if f.error_code == "internal":
            log.exception("Подключение %s: необработанная ошибка в задании №%s", code, info.id)
        else:
            log.warning("Подключение %s, задание №%s: %s", code, info.id, f.detail)
        _finish(info, conn, f)


def _note(db: Session, run_id: int | None, text_: str, level: str = "info", *, now: datetime) -> None:
    if run_id is None:
        return
    run = db.get(models.CollectionRun, run_id)
    if run is None:
        return
    notes = list(run.notes or [])
    notes.append({"at": models.iso(now), "level": level, "text": redact(text_)})
    run.notes = notes[-100:]


def _deferred(info: JobInfo, conn, d) -> None:
    from .integrations.base import local_time

    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        now = info.now()
        before = info.payload.get("phase")  # фаза на начало запуска: смену через ctx.save_payload тоже видно
        if defer(db, job, d.seconds, d.payload, now=now):
            phase = (job.payload or {}).get("phase")
            if job.payload.get("deferrals") == 1 or phase != before:  # заметка - на первую отсрочку и смену этапа
                _note(db, info.run_id, f"Ожидание источника — продолжение после {local_time(job.run_after)}"
                      + (f" (этап: {phase})" if phase else ""), now=now)
            db.commit()
            return
        db.rollback()
    _finish(info, conn, _Failure("source_error", "failed", "error",
                                 f"Задание откладывалось слишком много раз ({MAX_DEFERRALS}) — остановлено"))


def _paid_stop(row, f: _Failure) -> str:
    """Платный сбор после ошибки: остановлен до успешной ручной проверки."""
    row.facts = {**(row.facts or {}), "stopped": True, "stoppedCode": f.error_code}
    return f"{f.detail}. {PAID_STOP}" if f.detail else PAID_STOP


def _finish(info: JobInfo, conn, f: _Failure) -> None:
    """Ошибка задания сбора/проверки: повтор(если можно и попытки остались) или итог запуска и подключения - только
    пока задание у этого захвата; время - момент итога."""
    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        _failure_outcome(db, job, conn, f, info.now())
        db.commit()


def _failure_outcome(db: Session, job: models.Job, conn, f: _Failure, now: datetime, *,
                     retry_ok: bool = True) -> None:
    """Итог ошибки задания сбора/проверки в сессии db(без commit): повтор(retry_ok, повторяемая
    ошибка и попытки остались) или итог задания, запуска и подключения(пауза, остановка платного сбора, переход
    статуса)."""
    from .integrations import service
    from .integrations.base import local_time

    if retry_ok and f.retryable and retry(db, job, f.detail, now=now):
        _note(db, job.run_id, f"{f.detail} — повтор {job.attempts + 1} из {job.max_attempts} после "
                              f"{local_time(job.run_after)}", "warn", now=now)
        return
    if f.run_status == "rate_limited":
        complete(db, job, {"rateLimited": True})
    else:
        fail(db, job, f.detail)
    detail = f.detail
    if f.status is not None and conn is not None:
        row = service.row(db, conn.code, create=True)
        row.last_error, row.last_error_at = redact(f.detail), models.as_utc(now)
        if job.kind == "test":
            row.checked_at = models.as_utc(now)
        if f.run_status == "rate_limited":
            retry_s = pause_seconds(getattr(f.exc, "retry_after", None) or 3600, 3600)
            row.next_run_at = models.as_utc(now) + timedelta(seconds=retry_s)
            detail = f"Ограничение частоты: повтор после {local_time(row.next_run_at)}"
        elif f.status == "error":
            row.failures = (row.failures or 0) + 1
            pause = min(_interval_s(conn, row) * 2 ** min(row.failures, 5), 86400)
            row.next_run_at = models.as_utc(now) + timedelta(seconds=pause)
        if f.error_code == "bad_config":
            row.facts = {**(row.facts or {}), "stopped": True, "stoppedCode": "bad_config"}
        if conn.paid and f.status in ("error", "expired", "denied", "access_check"):
            detail = _paid_stop(row, f)
        service.transition(db, conn.code, f.status, detail, at=now, audit=f.status != "not_configured")
    if job.run_id is not None:
        service.finish_run(db, job.run_id, f.run_status, error_code=f.error_code, error=f.detail, now=now)


def _key_fps(conn, s) -> dict:
    return {name: fp for name in conn.required_env if (fp := s.key_fp(name))}


def _test_ok(info: JobInfo, conn, check) -> None:
    """Проверка прошла. Ручная проверка(администратор) снимает остановку сбора; плановая("Система") - нет:
    остановку после ошибки настроек или платного сбора снимают изменение настроек или проверка администратором
    (плановая проверка остановленного подключения и не ставится, это - на случай, если её поставили до остановки)."""
    from .integrations import service
    from .settings import get_settings

    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        now = info.now()
        complete(db, job, {"ok": True, "detail": redact(check.detail)})
        run = db.get(models.CollectionRun, info.run_id) if info.run_id is not None else None
        row = service.row(db, conn.code, create=True)
        keep_stop = bool((row.facts or {}).get("stopped")) and run is not None and run.requested_by == SYSTEM
        facts = {**(row.facts or {}), **dict(check.facts or {}), "keyFp": _key_fps(conn, get_settings())}
        row.checked_at = models.as_utc(now)
        if keep_stop:
            facts["stopped"], facts["stoppedCode"] = True, (row.facts or {}).get("stoppedCode")
            row.facts = facts
        else:
            facts.pop("stopped", None)
            facts.pop("stoppedCode", None)
            row.facts = facts
            row.failures = 0
            status = row.status if row.status in ("working", "partial") else "ready"
            service.transition(db, conn.code, status, redact(check.detail), at=now, audit=True)
        if info.run_id is not None:
            service.finish_run(db, info.run_id, "succeeded", now=now)
        db.commit()


def _summary(name: str, run: models.CollectionRun) -> str:
    what = (f"Сбор «{name}»: новых {run.items_new}, изменено {run.items_updated}, без изменений "
            f"{run.items_unchanged}")
    if run.items_failed:
        what += f", ошибок {run.items_failed}"
    return what + f" (запуск №{run.id})"


def _collect_ok(info: JobInfo, conn, ctx) -> None:
    """Сбор завершился без исключения: все записи с ошибкой - ошибка; есть ошибки записей/файлов или отметки
    "частично" - частично; иначе - успешно."""
    from .ads_service import _event
    from .integrations import service

    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        now = info.now()
        run = db.get(models.CollectionRun, info.run_id, populate_existing=True) if info.run_id else None
        seen = run.items_seen if run else ctx.counts["seen"]
        failed = run.items_failed if run else ctx.counts["failed"]
        media_failed = run.media_failed if run else ctx.counts["media_failed"]
        if seen > 0 and failed == seen:
            first = ctx.first_failure or "причина не указана"
            _failure_outcome(db, job, conn, _Failure("source_error", "failed", "error",
                                                     f"Все {seen} записей с ошибкой: {first}"), now)
            db.commit()
            return
        row = service.row(db, conn.code, create=True)
        partial = bool(failed or media_failed or ctx.partial_reasons)
        complete(db, job, {"partial": partial, **ctx.counts})
        facts = {**(row.facts or {}), "partial": partial}
        facts.pop("stopped", None)
        facts.pop("stoppedCode", None)
        row.facts = facts
        row.last_success_at, row.failures = models.as_utc(now), 0
        # postpone() только откладывает: пауза короче интервала не делает следующий запуск раньше расписания
        # пауза от подключения - в пределах base.MAX_PAUSE_S: NaN или бесконечность уронили бы запись итога(timedelta)
        row.next_run_at = models.as_utc(now) + timedelta(seconds=max(pause_seconds(ctx.postponed, 0.0),
                                                                     _interval_s(conn, row)))
        if partial:
            parts = list(dict.fromkeys(ctx.partial_reasons))
            if failed:
                parts.append(f"записей с ошибкой: {failed} из {seen}")
            if media_failed:
                parts.append(f"файлов не загружено: {media_failed}")
            detail = "; ".join(parts)
            detail = detail[:DETAIL_MAX - 1] + "…" if len(detail) > DETAIL_MAX else detail
            service.transition(db, conn.code, "partial", detail, at=now, audit=False)
        else:
            detail = None
            if run is not None:
                detail = (f"Последний сбор: новых {run.items_new}, изменено {run.items_updated}, без изменений "
                          f"{run.items_unchanged}")
            service.transition(db, conn.code, "working", detail, at=now, audit=False)
        if run is not None:
            service.finish_run(db, run.id, "partial" if partial else "succeeded", now=now)
            if partial or run.items_new + run.items_updated + run.items_failed > 0:
                _event(db, "data", SYSTEM, _summary(conn.name, run), at=now)
        db.commit()


def _collect(info: JobInfo, *, now: datetime, stop: threading.Event | None = None) -> None:
    _connector_job(info, now=now, stop=stop, test=False)


def _test(info: JobInfo, *, now: datetime, stop: threading.Event | None = None) -> None:
    _connector_job(info, now=now, stop=stop, test=True)


# ---------- анализ публикации ----------

def _analysis_failed(db: Session, job: models.Job, error: str, now: datetime) -> None:
    """Анализ публикации окончательно не выполнен(в сессии db, без commit): задание - ошибка с этапом сбоя, приём -
    "не выполнен", событие в журнале публикации."""
    from .ads_service import _event

    stage = (job.payload or {}).get("stage") or "media"
    fail(db, job, error)
    job.result = {"failedStage": stage}
    if not job.ad_id:  # без ad_id условие ad_id = NULL задело бы чужие записи приёма
        return
    I = models.IngestItem
    db.execute(update(I).where(I.ad_id == job.ad_id).values(analysis_status="failed", analysis_stage=stage,
                                                               analysis_error=error)
               .execution_options(synchronize_session=False))
    if db.get(models.Ad, job.ad_id) is not None:
        _event(db, "system", SYSTEM, f"Анализ публикации не выполнен: {error} — проверьте вручную", at=now,
               ad_id=job.ad_id)


def _analysis_requeued(db: Session, ad_id: str | None) -> None:
    """Анализ публикации снова в очереди(повтор, без commit): приём "выполняется" → "в очереди"."""
    if not ad_id:
        return
    I = models.IngestItem
    db.execute(update(I).where(I.ad_id == ad_id, I.analysis_status == "running").values(analysis_status="queued")
               .execution_options(synchronize_session=False))


def _analyze(info: JobInfo, *, now: datetime, stop: threading.Event | None = None) -> None:
    from . import analysis

    try:
        # force - "Проанализировать ещё раз"(POST /api/ads/{id}/reanalyze): Claude запрашивается снова; rulesOnly -
        # пересчёт правил после создания карточки ЖК по рекламе(app.discovery), без запроса к Claude
        p = info.payload or {}
        result = analysis.run_analysis(info.id, info.ad_id, now=now, force=bool(p.get("force")),
                                       rules_only=bool(p.get("rulesOnly")), reason=p.get("reason"))
    except Exception as e:
        log.exception("Анализ публикации %s (задание №%s) не выполнен", info.ad_id, info.id)
        # аналитик видит только причину, написанную для него(analysis.failure_text); текст прочих исключений(запрос SQL
        # с параметрами, пути к файлам) - только в журнале воркера
        error = analysis.failure_text(e) or (ANALYSIS_DB if isinstance(e, SQLAlchemyError) else ANALYSIS_FAILED)
        with SessionLocal() as db:
            job = fence(db, info.id, info.worker_id, info.attempts)
            if job is None:
                return
            now = info.now()
            if retry(db, job, error, now=now):
                _analysis_requeued(db, job.ad_id)
            else:
                _analysis_failed(db, job, error, now)
            db.commit()
        return
    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is not None:
            complete(db, job, result)
            db.commit()


# ---------- предупреждение жителям ----------

def _alert(info: JobInfo, *, now: datetime, stop: threading.Event | None = None) -> None:
    from . import alerts

    alerts.run_job(info, now=now, stop=stop)


HANDLERS: dict[str, Callable[..., None]] = {"collect": _collect, "test": _test, "analyze": _analyze, "alert": _alert}
