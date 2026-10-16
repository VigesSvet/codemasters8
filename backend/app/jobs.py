"""Задания воркера в таблице jobs (integration-spec §9.1, §9.3, §9.4): сбор (collect), проверка подключения (test),
анализ публикации (analyze). Не больше одного активного задания на dedupe_key (частичный уникальный индекс): гонка двух
постановок решается INSERT … ON CONFLICT DO NOTHING и повторным чтением, без SAVEPOINT.

Захват: на PostgreSQL — SELECT … FOR UPDATE SKIP LOCKED, на SQLite — условный UPDATE по прочитанному id (повтор до трёх
раз). Анализ публикации, по которой уже идёт анализ, не захватывается; у захваченного анализа dedupe_key обнуляется —
новая версия публикации ставит новое задание, пока идёт прежнее.

execute: сессия S1 (задание и запуск — «выполняется») фиксируется и закрывается ДО обработчика — сеть, OCR и ffmpeg идут
без открытой транзакции записи; итог применяется в новой сессии. Итог обработчика определяет статус запуска и подключения
(таблица §9.4): сбой никогда не выглядит как «нет новых», платный сбор после ошибки останавливается до ручной проверки.
Обработчики импортируют реестр, сервис, контекст и анализ лениво (порядок импорта §2.1).

Итог пишется, только если задание всё ещё у того же захвата (fence: выполняется, тот же воркер, то же число попыток):
задание, которое recover_stale или requeue_own отдали другому воркеру, прежний исполнитель уже не перезапишет. Время
итога (повтор, отсрочка, пауза частоты, следующий сбор) — момент итога, а не захвата: долгий запуск не делает паузу
прошедшей ещё до записи. Брошенное задание (попытки исчерпаны в recover_stale / requeue_own) получает тот же итог
ошибки, что и обычный сбой: подключение — «ошибка», платный сбор остановлен, анализ — событие «не выполнен».
"""
import hashlib
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy import and_, exists, not_, select, text, update
from sqlalchemy.orm import Session, aliased

from . import models
from .db import SessionLocal
from .logs import redact

log = logging.getLogger("saq.jobs")

PRIORITY = {"test": 10, "collect": 5, "analyze": 0}
SOFT_DEADLINE_S = 1200  # RunContext.time_left(): мягкий срок одного выполнения задания
ACTIVE = ("queued", "running")
MAX_DEFERRALS = 240  # отсрочек одного задания (Deferred), дальше — ошибка
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
CLAIM_TRIES = 3
SYSTEM = "Система"
INTERNAL = "Внутренняя ошибка сборщика — подробности в журнале воркера"
PAID_STOP = "Платный сбор остановлен после ошибки — запустите проверку вручную"
SCHEDULABLE = frozenset({"ready", "working", "partial", "error", "rate_limited"})
DETAIL_MAX = 500
MONOTONIC: Callable[[], float] = time.monotonic  # время выполнения задания (тесты подменяют)


# ---------- постановка ----------

def _active(db: Session, dedupe_key: str) -> Optional[models.Job]:
    J = models.Job
    return db.scalar(select(J).where(J.dedupe_key == dedupe_key, J.status.in_(ACTIVE)).order_by(J.id.desc()).limit(1))


def active(db: Session, dedupe_key: str) -> Optional[models.Job]:
    """Активное (в очереди или выполняется) задание с этим ключом."""
    return _active(db, dedupe_key)


def enqueue(db: Session, kind: str, *, dedupe_key: Optional[str] = None, collector: Optional[str] = None,
            run_id: Optional[int] = None, ad_id: Optional[str] = None, content_version: Optional[int] = None,
            payload: Optional[dict] = None, run_after: Optional[datetime] = None, priority: Optional[int] = None,
            max_attempts: int = 5) -> tuple[models.Job, bool]:
    """Задание в очередь (в сессии db, без commit). С dedupe_key: активное задание с тем же ключом уже есть —
    возвращается оно (created=False); для analyze его content_version поднимается до новой версии.
    Возвращает (задание, создано ли)."""
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
    for _ in range(3):  # активное задание могло завершиться между INSERT и SELECT — вставка пройдёт со второго раза
        created = models.insert_ignore(db, models.Job, values, ("dedupe_key",), where=text(models.ACTIVE_JOB))
        job = _active(db, dedupe_key)
        if job is None:
            continue
        newer = content_version is not None and (job.content_version or 0) < content_version
        if not created and kind == "analyze" and newer:
            job.content_version = content_version
        return job, created
    raise RuntimeError(f"Задание {dedupe_key} не поставлено в очередь: активное задание меняется слишком часто")


# ---------- захват ----------

def _claimable(now: datetime):
    """Условие «можно захватить»: в очереди, срок наступил; анализ — только если по этой публикации анализ не идёт."""
    J, R = models.Job, aliased(models.Job)
    busy = exists().where(R.kind == "analyze", R.status == "running", R.ad_id == J.ad_id, R.id != J.id)
    return and_(J.status == "queued", J.run_after <= models.as_utc(now), not_(and_(J.kind == "analyze", busy)))


def _ad_lock_key(ad_id: str) -> int:
    """Ключ рекомендательной блокировки PostgreSQL «анализ публикации ad_id» (64 бита со знаком)."""
    return int.from_bytes(hashlib.sha256(f"analyze:{ad_id}".encode()).digest()[:8], "big", signed=True)


def _try_claim(db: Session, job_id: int, worker_id: str, now: datetime) -> bool:
    """Условный UPDATE одного задания (в сессии db, без commit): True — захвачено этим воркером.
    Анализ на PostgreSQL: сначала блокировка «анализ этой публикации» до конца транзакции — два воркера, захватывающие
    одновременно два задания одной публикации, идут по очереди, и условие «анализ не идёт» второго (новый снимок
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


def claim(db: Session, worker_id: str, now: datetime) -> Optional[models.Job]:
    """Следующее задание (приоритет, срок, id) → «выполняется» у этого воркера; фиксирует транзакцию. None — нечего
    делать. PostgreSQL: FOR UPDATE SKIP LOCKED; SQLite: условный UPDATE, при гонке — до трёх попыток."""
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
    return min(BACKOFF_BASE_S * 2 ** max(attempts - 1, 0), BACKOFF_MAX_S) * random.uniform(0.8, 1.2)


def complete(db: Session, job: models.Job, result: Optional[dict] = None) -> None:
    job.status, job.finished_at, job.locked_by = "done", models.utcnow(), None
    if result is not None:
        job.result = dict(result)


def fail(db: Session, job: models.Job, error: Optional[str]) -> None:
    job.status, job.finished_at, job.locked_by = "failed", models.utcnow(), None
    job.last_error = redact(error)


def cancel(db: Session, job: models.Job) -> None:
    job.status, job.finished_at, job.locked_by = "cancelled", models.utcnow(), None


def retry(db: Session, job: models.Job, error: Optional[str], *, now: datetime) -> bool:
    """Повтор с паузой (backoff): задание снова в очереди. False — попытки исчерпаны (вызывающий завершает ошибкой)."""
    job.last_error = redact(error)
    if (job.attempts or 0) >= (job.max_attempts or 1):
        return False
    job.status, job.locked_by = "queued", None
    job.run_after = models.as_utc(now) + timedelta(seconds=backoff(job.attempts or 1))
    return True


def defer(db: Session, job: models.Job, seconds: float, payload_update: Optional[dict], *, now: datetime) -> bool:
    """Отсрочка без расхода попытки (ожидание внешнего запуска): в очередь через seconds с, payload дополняется.
    False — отсрочек больше MAX_DEFERRALS: задание дальше не откладывается (вызывающий завершает ошибкой)."""
    payload = {**(job.payload or {}), **(payload_update or {})}
    payload["deferrals"] = int(payload.get("deferrals") or 0) + 1
    job.payload = payload
    if payload["deferrals"] > MAX_DEFERRALS:
        return False
    job.status, job.locked_by = "queued", None
    job.attempts = max((job.attempts or 0) - 1, 0)
    job.run_after = models.as_utc(now) + timedelta(seconds=max(float(seconds), 0.0))
    return True


def fence(db: Session, job_id: int, worker_id: Optional[str], attempts: Optional[int], *,
          what: str = "итог не записан") -> Optional[models.Job]:
    """Задание всё ещё у этого захвата (выполняется, тот же воркер, то же число попыток)? Условный UPDATE-«замок»
    (на PostgreSQL блокирует строку до конца транзакции, на SQLite берёт запись) и чтение свежей строки; None —
    задание уже вернули в очередь, завершили или отдали другому воркеру: итог этого исполнителя не записывается.
    worker_id None — без проверки (прямой вызов)."""
    J = models.Job
    if worker_id is None:
        return db.get(J, job_id)
    res = db.execute(update(J).where(J.id == job_id, J.status == "running", J.locked_by == worker_id,
                                     J.attempts == (attempts or 0)).values(locked_by=worker_id)
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:  # без отката: в той же транзакции могут быть чужие изменения (такт лидера)
        log.warning("Задание №%s уже не у воркера %s (возвращено в очередь или выполнено другим) — %s",
                    job_id, worker_id, what)
        return None
    return db.get(J, job_id, populate_existing=True)


def _requeue(db: Session, job: models.Job, now: datetime, reason: str) -> Optional[str]:
    """Задание, которое перестал выполнять воркер: снова в очередь (попытка уже израсходована) или итог ошибки, если
    попыток больше нет. Возвращает новый статус; None — задание тем временем завершилось или его уже забрали
    (условный UPDATE по прочитанному захвату не совпал)."""
    if fence(db, job.id, job.locked_by, job.attempts, what="повторно не возвращается") is None:
        return None
    if (job.attempts or 0) >= (job.max_attempts or 1):
        _abandoned(db, job, now, reason)
        return "failed"
    job.status, job.locked_by, job.last_error = "queued", None, reason
    job.run_after = models.as_utc(now)
    return "queued"


def _abandoned(db: Session, job: models.Job, now: datetime, reason: str) -> None:
    """Задание брошено окончательно (попытки исчерпаны, воркер упал или завис). Сбор и проверка — итог как у сбоя без
    повтора (§9.4): запуск «ошибка», подключение «ошибка» с паузой, платный сбор остановлен — иначе расписание сразу
    начало бы новый платный запуск. Анализ — «не выполнен» с событием в журнале публикации (§8 п. 11)."""
    if job.kind in ("collect", "test"):
        from .integrations import registry

        try:
            conn = registry.get(job.collector or "")
        except KeyError:
            conn = None
        _failure_outcome(db, job, conn, _Failure("internal", "failed", "error", reason), now, retry_ok=False)
        return
    fail(db, job, reason)
    if job.run_id is not None:
        from .integrations import service

        service.finish_run(db, job.run_id, "failed", error_code="internal", error=reason, now=now)
    if job.kind == "analyze" and job.ad_id:
        from .ads_service import _event

        I = models.IngestItem
        stage = (job.payload or {}).get("stage") or "media"
        job.result = {"failedStage": stage}
        db.execute(update(I).where(I.ad_id == job.ad_id).values(analysis_status="failed", analysis_stage=stage,
                                                                   analysis_error=reason)
                   .execution_options(synchronize_session=False))
        if db.get(models.Ad, job.ad_id) is not None:
            _event(db, "system", SYSTEM, f"Анализ публикации не выполнен: {reason} — проверьте вручную", at=now,
                   ad_id=job.ad_id)


def recover_stale(db: Session, now: datetime, lease_s: int) -> int:
    """Выполняемые задания без продления блокировки дольше lease_s (воркер упал или завис) → снова в очередь (или
    итог ошибки, если попытки исчерпаны). Без commit."""
    J = models.Job
    cutoff = models.as_utc(now) - timedelta(seconds=lease_s)
    rows = db.scalars(select(J).where(J.status == "running", J.locked_at < cutoff)).all()
    n = 0
    for job in rows:
        if _requeue(db, job, now, "Задание прервано: воркер перестал отвечать") is not None:
            log.warning("Задание №%s (%s) не продлевалось дольше %s с — возвращено в очередь или завершено ошибкой",
                        job.id, job.kind, lease_s)
            n += 1
    return n


def requeue_own(db: Session, worker_id: str) -> int:
    """Старт воркера: его же задания в статусе «выполняется» (прошлый процесс с этим SAQ_WORKER_ID упал) → в очередь
    (или итог ошибки, если попытки исчерпаны). Без commit."""
    J = models.Job
    now = models.utcnow()
    rows = db.scalars(select(J).where(J.status == "running", J.locked_by == worker_id)).all()
    return sum(_requeue(db, job, now, "Задание прервано перезапуском воркера") is not None for job in rows)


# ---------- выполнение ----------

@dataclass
class JobInfo:
    """Снимок задания на момент запуска: обработчик работает без открытой сессии. worker_id и attempts — захват,
    по которому итог проверяется (fence); base + прошедшее с started — время итога (now())."""
    id: int
    kind: str
    collector: Optional[str]
    run_id: Optional[int]
    ad_id: Optional[str]
    attempts: int
    max_attempts: int
    payload: dict = field(default_factory=dict)
    trigger: str = "manual"
    worker_id: Optional[str] = None
    base: Optional[datetime] = None
    started: float = field(default_factory=lambda: MONOTONIC())

    def now(self) -> datetime:
        """Текущее время задания (UTC): время, переданное воркером (захват), плюс прошедшее с начала выполнения."""
        base = self.base if self.base is not None else models.utcnow()
        return base + timedelta(seconds=MONOTONIC() - self.started)


def execute(job_id: int, *, worker_id: str, now: Optional[datetime] = None,
            stop: Optional[threading.Event] = None) -> None:
    """Выполнить захваченное задание. S1 (задание и запуск — «выполняется») фиксируется до обработчика; итог —
    в новой сессии и только пока задание у этого захвата. Исключения обработчика сюда не выходят (кроме ошибок БД
    при записи итога)."""
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
    handler(info, now=now, stop=stop)


# ---------- сбор и проверка подключения ----------

@dataclass
class _Failure:
    """Ошибка подключения для итога: код ошибки запуска, статус запуска и подключения, текст, повторять ли."""
    error_code: str
    run_status: str
    status: Optional[str]  # None — статус подключения не меняется (вычисляется)
    detail: str
    retryable: bool = False
    exc: Optional[BaseException] = None


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
    if isinstance(e, http.DownloadError):  # клиент подключения отклонил запрос (чужой хост) или сеть
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


def _connector_job(info: JobInfo, *, now: datetime, stop: Optional[threading.Event], test: bool) -> None:
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
    if missing:  # ключей нет на сервере: статус «не настроен» вычисляется, хранимый статус не меняется
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
    except Exception as e:  # noqa: BLE001 — любой сбой подключения — итог запуска, не падение воркера
        f = _failure(e)
        if f.error_code == "internal":
            log.exception("Подключение %s: необработанная ошибка в задании №%s", code, info.id)
        else:
            log.warning("Подключение %s, задание №%s: %s", code, info.id, f.detail)
        _finish(info, conn, f)


def _note(db: Session, run_id: Optional[int], text_: str, level: str = "info", *, now: datetime) -> None:
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
        before = (job.payload or {}).get("phase")
        if defer(db, job, d.seconds, d.payload, now=now):
            phase = (job.payload or {}).get("phase")
            if job.payload.get("deferrals") == 1 or phase != before:  # заметка — на первую отсрочку и смену этапа
                _note(db, info.run_id, f"Ожидание источника — продолжение после {local_time(job.run_after)}"
                      + (f" (этап: {phase})" if phase else ""), now=now)
            db.commit()
            return
        db.rollback()
    _finish(info, conn, _Failure("source_error", "failed", "error",
                                 f"Задание откладывалось слишком много раз ({MAX_DEFERRALS}) — остановлено"))


def _paid_stop(row, f: _Failure) -> str:
    """Платный сбор после ошибки: остановлен до успешной ручной проверки (D19)."""
    row.facts = {**(row.facts or {}), "stopped": True, "stoppedCode": f.error_code}
    return f"{f.detail}. {PAID_STOP}" if f.detail else PAID_STOP


def _finish(info: JobInfo, conn, f: _Failure) -> None:
    """Ошибка задания сбора/проверки: повтор (если можно и попытки остались) или итог запуска и подключения — только
    пока задание у этого захвата; время — момент итога."""
    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is None:
            return
        _failure_outcome(db, job, conn, f, info.now())
        db.commit()


def _failure_outcome(db: Session, job: models.Job, conn, f: _Failure, now: datetime, *,
                     retry_ok: bool = True) -> None:
    """Итог ошибки задания сбора/проверки в сессии db (без commit), таблица §9.4: повтор (retry_ok, повторяемая
    ошибка и попытки остались) или итог задания, запуска и подключения (пауза, остановка платного сбора, переход
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
            retry_s = float(getattr(f.exc, "retry_after", None) or 3600)
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
    return {name: s.key_fp(name) for name in conn.required_env if s.key_fp(name)}


def _test_ok(info: JobInfo, conn, check) -> None:
    """Проверка прошла. Ручная проверка (администратор) снимает остановку сбора; плановая («Система») — нет:
    остановку после ошибки настроек или платного сбора снимают изменение настроек или проверка администратором
    (плановая проверка остановленного подключения и не ставится, это — на случай, если её поставили до остановки)."""
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
    """Сбор завершился без исключения: все записи с ошибкой — ошибка; есть ошибки записей/файлов или отметки
    «частично» — частично; иначе — успешно."""
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
            db.rollback()
            first = ctx.first_failure or "причина не указана"
            _finish(info, conn, _Failure("source_error", "failed", "error", f"Все {seen} записей с ошибкой: {first}"))
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
        row.next_run_at = models.as_utc(now) + timedelta(seconds=max(ctx.postponed or 0.0, _interval_s(conn, row)))
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


def _collect(info: JobInfo, *, now: datetime, stop: Optional[threading.Event] = None) -> None:
    _connector_job(info, now=now, stop=stop, test=False)


def _test(info: JobInfo, *, now: datetime, stop: Optional[threading.Event] = None) -> None:
    _connector_job(info, now=now, stop=stop, test=True)


# ---------- анализ публикации ----------

def _analyze(info: JobInfo, *, now: datetime, stop: Optional[threading.Event] = None) -> None:
    from . import analysis
    from .ads_service import _event

    try:
        result = analysis.run_analysis(info.id, info.ad_id, now=now)
    except Exception as e:  # noqa: BLE001 — сбой анализа — повтор, затем «анализ не выполнен», не падение воркера
        log.exception("Анализ публикации %s (задание №%s) не выполнен", info.ad_id, info.id)
        error = redact(str(e) or type(e).__name__, limit=300)
        I = models.IngestItem
        with SessionLocal() as db:
            job = fence(db, info.id, info.worker_id, info.attempts)
            if job is None:
                return
            now = info.now()
            if retry(db, job, error, now=now):
                db.execute(update(I).where(I.ad_id == info.ad_id, I.analysis_status == "running")
                           .values(analysis_status="queued").execution_options(synchronize_session=False))
                db.commit()
                return
            stage = (job.payload or {}).get("stage") or "media"
            fail(db, job, error)
            job.result = {"failedStage": stage}
            db.execute(update(I).where(I.ad_id == info.ad_id).values(analysis_status="failed", analysis_stage=stage,
                                                                      analysis_error=error)
                       .execution_options(synchronize_session=False))
            if info.ad_id and db.get(models.Ad, info.ad_id) is not None:
                _event(db, "system", SYSTEM, f"Анализ публикации не выполнен: {error} — проверьте вручную", at=now,
                       ad_id=info.ad_id)
            db.commit()
        return
    with SessionLocal() as db:
        job = fence(db, info.id, info.worker_id, info.attempts)
        if job is not None:
            complete(db, job, result)
            db.commit()


HANDLERS: dict[str, Callable[..., None]] = {"collect": _collect, "test": _test, "analyze": _analyze}
