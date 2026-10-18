"""Воркер сбора и анализа: python -m app.worker [--once] [--id ID] [--schema-wait S] (integration-spec §9.2, §9.5).

Один процесс, цикл по таблице jobs: отчёт о локальных сведениях подключений (например, есть ли сессия Telegram) →
такт лидера (планировщик сборов и плановых проверок, возврат зависших заданий, дозапрос Claude по старым публикациям раз
в ANTHROPIC_BACKFILL_INTERVAL_MIN, уборка раз в час) → захват задания →
выполнение → пауза SAQ_WORKER_POLL_S. Планирует только держатель аренды «scheduler»; выполнять задания может любой
воркер. Поток-пульс (раз в 30 с, своя сессия) продлевает пульс воркера, аренду планировщика и блокировку текущего задания
(не дольше SAQ_JOB_MAX_S — потом зависшее задание сможет забрать recover_stale).

Схему БД воркер не создаёт и не обновляет (это делает API при старте, init_db): он ждёт нужную версию схемы. SIGTERM /
SIGINT — текущее задание завершается (подключения проверяют ctx.stopping()), затем выход с кодом 0. Секреты в журнал не
пишутся: только коды подключений.
"""
import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import jobs, logs, media, models
from .db import SessionLocal, engine as default_engine
from .settings import get_settings

log = logging.getLogger("saq.worker")

HEARTBEAT_S = 30.0
LEASE_TTL_S = 90
WORKER_LEASE_TTL_S = 120
HOUSEKEEPING_S = 3600
SCHEMA_LOG_S = 30.0
SCHEDULER = "scheduler"


# ---------- схема ----------

def schema_version(eng: Engine) -> Optional[str]:
    """Версия схемы в meta (None — таблиц ещё нет или БД недоступна)."""
    try:
        with eng.connect() as conn:
            return conn.execute(text("SELECT value FROM meta WHERE key = 'schema_version'")).scalar()
    except Exception:  # noqa: BLE001 — нет таблицы meta или БД ещё не поднялась
        return None


def wait_for_schema(eng: Engine = default_engine, *, poll: float = 2.0, max_wait: Optional[float] = None,
                    stop: Optional[threading.Event] = None) -> bool:
    """Ждать, пока API создаст или обновит схему до models.SCHEMA_VERSION. False — не дождались (max_wait, остановка)."""
    started = time.monotonic()
    logged = None
    while True:
        ver = schema_version(eng)
        if ver == models.SCHEMA_VERSION:
            return True
        if logged is None or time.monotonic() - logged >= SCHEMA_LOG_S:
            log.info("Воркер ждёт схему БД %s (сейчас %s) — её создаёт API при старте", models.SCHEMA_VERSION,
                     ver or "нет")
            logged = time.monotonic()
        if max_wait is not None and time.monotonic() - started >= max_wait:
            return False
        wait = poll if max_wait is None else max(min(poll, max_wait - (time.monotonic() - started)), 0.0)
        if stop is not None:
            if stop.wait(wait):
                return False
        else:
            time.sleep(wait)


# ---------- аренды ----------

def acquire_lease(db: Session, name: str, holder: str, ttl_s: int = 60, now: Optional[datetime] = None,
                  info: Optional[dict] = None) -> bool:
    """Аренда name за holder: продлить свою или забрать истёкшую (условный UPDATE), нет строки — создать. Фиксирует
    транзакцию. True — аренда у holder."""
    L = models.WorkerLease
    now = models.as_utc(now) or models.utcnow()
    values: dict = {"holder": holder, "expires_at": now + timedelta(seconds=ttl_s), "heartbeat_at": now}
    if info is not None:
        values["info"] = dict(info)
    res = db.execute(update(L).where(L.name == name, (L.holder == holder) | (L.expires_at < now)).values(**values)
                     .execution_options(synchronize_session=False))
    ok = res.rowcount == 1
    if not ok:
        ok = models.insert_ignore(db, L, {"name": name, **values, "info": dict(info or {})}, ("name",))
    db.commit()
    return ok


def release_lease(worker_id: str) -> None:
    """Остановка воркера: аренда планировщика освобождается сразу (другой воркер не ждёт её истечения), пульс
    worker:{id} отмечается остановленным (info.stoppedAt) — карточка «Подключения» сразу видит, что воркер не запущен,
    а не через WORKER_ALIVE_S; время последнего пульса сохраняется."""
    L = models.WorkerLease
    now = models.utcnow()
    try:
        with SessionLocal() as db:
            db.execute(update(L).where(L.name == SCHEDULER, L.holder == worker_id).values(expires_at=now)
                       .execution_options(synchronize_session=False))
            own = db.get(L, f"worker:{worker_id}")
            if own is not None:
                own.info = {**(own.info or {}), "stoppedAt": now.isoformat()}  # с долями секунды, как пульс
                own.expires_at = now
            db.commit()
    except Exception:  # noqa: BLE001 — при остановке это не ошибка воркера
        log.warning("Аренда планировщика не освобождена", exc_info=True)


@dataclass
class Current:
    """Текущее задание воркера — для потока-пульса."""
    job_id: Optional[int] = None
    started: float = 0.0
    warned: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, job_id: Optional[int]) -> None:
        with self.lock:
            self.job_id, self.started = job_id, time.monotonic()

    def get(self) -> tuple[Optional[int], float]:
        with self.lock:
            return self.job_id, self.started


def beat(worker_id: str, current: Optional[Current] = None) -> None:
    """Один пульс: worker:{id}, аренда планировщика (если у этого воркера), блокировка текущего задания."""
    now = models.utcnow()
    job_id, started = current.get() if current is not None else (None, 0.0)
    info = {"id": worker_id, "pid": os.getpid(), "version": models.SCHEMA_VERSION, "jobId": job_id}
    L, J = models.WorkerLease, models.Job
    with SessionLocal() as db:
        acquire_lease(db, f"worker:{worker_id}", worker_id, WORKER_LEASE_TTL_S, now, info=info)
        db.execute(update(L).where(L.name == SCHEDULER, L.holder == worker_id)
                   .values(expires_at=now + timedelta(seconds=LEASE_TTL_S)).execution_options(synchronize_session=False))
        if job_id is not None:
            limit = get_settings().job_max_s
            if time.monotonic() - started < limit:
                db.execute(update(J).where(J.id == job_id, J.status == "running", J.locked_by == worker_id)
                           .values(locked_at=now).execution_options(synchronize_session=False))
            elif current is not None and job_id not in current.warned:
                current.warned.add(job_id)
                log.warning("Задание №%s выполняется дольше %s с — продление блокировки прекращено", job_id, limit)
        db.commit()


def heartbeat(worker_id: str, job_ref: Current, stop: threading.Event) -> None:
    """Поток-пульс (daemon): раз в HEARTBEAT_S с — beat(); ошибки БД — предупреждение, поток продолжает."""
    while not stop.is_set():
        try:
            beat(worker_id, job_ref)
        except Exception:  # noqa: BLE001
            log.warning("Пульс воркера %s не записан", worker_id, exc_info=True)
        stop.wait(HEARTBEAT_S)


def start_heartbeat(worker_id: str, job_ref: Current, stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=heartbeat, args=(worker_id, job_ref, stop), name=f"heartbeat-{worker_id}",
                         daemon=True)
    t.start()
    return t


# ---------- планировщик (§9.5) ----------

def schedule_due(db: Session, now: datetime) -> list[int]:
    """Сборы по расписанию и плановые проверки, которым пора (без commit). Возвращает id созданных запусков."""
    from .integrations import registry, service

    s = get_settings()
    now = models.as_utc(now)
    created: list[int] = []
    for code, conn in registry.CONNECTORS.items():
        r = service.row(db, code)
        caps = conn.capabilities or {}
        if conn.kind == "collector" and caps.get("collect") and r is not None and r.enabled \
                and not conn.missing(s, r) and r.status in jobs.SCHEDULABLE and not (r.facts or {}).get("stopped") \
                and not service.keys_changed(conn, r) \
                and (r.next_run_at is None or models.as_utc(r.next_run_at) <= now) \
                and jobs.active(db, f"collect:{code}") is None:
            run, new = service.request_run(db, code, trigger="schedule", who=jobs.SYSTEM, now=now)
            if new:
                created.append(run.id)
                log.info("Сбор «%s» по расписанию: запуск №%s", conn.name, run.id)
        # остановленное подключение (ошибка настроек, платный сбор) плановой проверкой не «оживляется» (§9.5 п. 3):
        # остановку снимают изменение настроек или проверка администратором
        if conn.health_check_min and caps.get("test") and not conn.missing(s, r) \
                and not (r is not None and (r.facts or {}).get("stopped")) \
                and jobs.active(db, f"test:{code}") is None and conn.health_due(db, r, now):
            run, new = service.request_run(db, code, trigger="test", who=jobs.SYSTEM, now=now)
            if new:
                created.append(run.id)
                log.info("Плановая проверка «%s»: запуск №%s", conn.name, run.id)
    return created


# ---------- уборка (§6.4) ----------

def housekeeping(db: Session, now: datetime) -> None:
    """Раз в час у лидера: непривязанные ручные загрузки старше суток, кандидаты 2ГИС старше срока кэша, истёкшие
    state OAuth, брошенные временные файлы, файлы хранилища без записей старше суток. Фиксирует транзакцию."""
    now = models.as_utc(now)
    n = media.purge_unattached(db, now)
    G = models.GeoCheck
    ttl = timedelta(days=get_settings().dgis_cache_ttl_days)
    res = db.execute(update(G).where(G.fetched_at < now - ttl, G.purged_at.is_(None)).values(candidates=[],
                                                                                            purged_at=now)
                     .execution_options(synchronize_session=False))
    states = db.execute(delete(models.OAuthState).where(models.OAuthState.expires_at < now)
                        .execution_options(synchronize_session=False))
    db.commit()
    tmp = media.purge_temp()
    orphans = media.purge_orphans(db)
    if n or res.rowcount or states.rowcount or tmp or orphans:
        log.info("Уборка: загрузок удалено %s, сверок 2ГИС очищено %s, state OAuth удалено %s, временных файлов %s, "
                 "файлов без записей %s", n, res.rowcount, states.rowcount, tmp, orphans)


def _periodic_due(db: Session, worker_id: str, now: datetime, mark: str, every_s: float) -> bool:
    """Периодическая работа лидера (отметка mark в info аренды планировщика): прошло every_s с — отметка обновляется
    (commit), True."""
    lease = db.get(models.WorkerLease, SCHEDULER)
    if lease is None or lease.holder != worker_id:
        return False
    last = (lease.info or {}).get(mark)
    try:
        due = last is None or models.as_utc(datetime.fromisoformat(last)) <= now - timedelta(seconds=every_s)
    except ValueError:
        due = True
    if due:
        lease.info = {**(lease.info or {}), mark: models.iso(now)}
        db.commit()
    return due


def _housekeeping_due(db: Session, worker_id: str, now: datetime) -> bool:
    return _periodic_due(db, worker_id, now, "housekeepingAt", HOUSEKEEPING_S)


# ---------- дозапрос Claude по старым публикациям ----------

def _backfill_due(db: Session, worker_id: str, now: datetime) -> bool:
    """Пора дозапросить Claude: ключ задан, партия больше нуля и прошло ANTHROPIC_BACKFILL_INTERVAL_MIN минут."""
    s = get_settings()
    if s.missing("ANTHROPIC_API_KEY") or int(s.anthropic_backfill_batch) <= 0:
        return False
    return _periodic_due(db, worker_id, now, "llmBackfillAt", float(s.anthropic_backfill_interval_min) * 60)


def backfill(db: Session, now: datetime) -> list[str]:
    """Задания анализа для старых публикаций без ответа Claude (app.llm.backfill: новые первыми, партия, бюджет).
    Фиксирует транзакцию; сбой — предупреждение в журнал, такт лидера продолжается."""
    from . import llm

    try:
        ids = llm.backfill(db, now)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("Дозапрос анализа ИИ (Claude) не выполнен", exc_info=True)
        return []
    if ids:
        log.info("Дозапрос анализа ИИ (Claude): поставлено заданий анализа %s", len(ids))
    return ids


# ---------- сведения, видимые только воркеру ----------

def report_local(db: Session) -> None:
    """Connector.report() → collectors.state.report (только при изменении; время проверки не считается изменением)."""
    from .integrations import registry, service

    s = get_settings()
    changed = False
    for code, conn in registry.CONNECTORS.items():
        try:
            rep = conn.report(s)
        except Exception:  # noqa: BLE001 — отчёт одного подключения не мешает остальным
            log.warning("Отчёт подключения %s не получен", code, exc_info=True)
            continue
        if rep is None:
            continue
        r = service.row(db, code, create=True)
        old = dict((r.state or {}).get("report") or {})
        if {k: v for k, v in old.items() if k != "checkedAt"} != {k: v for k, v in rep.items() if k != "checkedAt"}:
            r.state = {**(r.state or {}), "report": dict(rep)}
            changed = True
    if changed:
        db.commit()
    else:
        db.rollback()


# ---------- цикл ----------

def _tick(worker_id: str, now: datetime) -> None:
    """Такт лидера: аренда планировщика, сборы и проверки по расписанию, зависшие задания, дозапрос Claude, уборка."""
    s = get_settings()
    with SessionLocal() as db:
        if not acquire_lease(db, SCHEDULER, worker_id, LEASE_TTL_S, now=models.utcnow()):
            return
        # сначала зависшие задания (своя транзакция): брошенный сбор получает итог «ошибка» до решений расписания,
        # и блокировки строк подключений не пересекаются с итогом, который пишет другой воркер
        jobs.recover_stale(db, now, s.job_lease_s)
        db.commit()
        schedule_due(db, now)
        db.commit()
        if _backfill_due(db, worker_id, now):
            backfill(db, now)
        if _housekeeping_due(db, worker_id, now):
            try:
                housekeeping(db, now)
            except Exception:  # noqa: BLE001
                db.rollback()
                log.warning("Уборка не выполнена", exc_info=True)


def run_once(*, worker_id: str, now: Optional[datetime] = None, max_jobs: int = 100,
             current: Optional[Current] = None, stop: Optional[threading.Event] = None) -> int:
    """Один проход: пульс, отчёт, такт лидера, затем задания, пока они есть (не больше max_jobs). Возвращает число
    выполненных заданий. now — время для расписания и захвата (тесты); по умолчанию — текущее."""
    current = current or Current()
    beat(worker_id, current)
    with SessionLocal() as db:
        report_local(db)
    _tick(worker_id, models.as_utc(now) or models.utcnow())
    done = 0
    while done < max_jobs and not (stop is not None and stop.is_set()):
        t = models.as_utc(now) or models.utcnow()
        with SessionLocal() as db:
            job = jobs.claim(db, worker_id, t)
            job_id = job.id if job is not None else None
        if job_id is None:
            break
        current.set(job_id)
        try:
            jobs.execute(job_id, worker_id=worker_id, now=t, stop=stop)
        except Exception:  # noqa: BLE001 — ошибка записи итога: задание останется «выполняется» до recover_stale
            log.exception("Задание №%s: итог не записан", job_id)
        finally:
            current.set(None)
        done += 1
    return done


def _configured(s) -> list[str]:
    from .integrations import registry

    return [code for code, conn in registry.CONNECTORS.items() if not conn.missing(s)]


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.worker", description="Воркер сбора публикаций и анализа")
    p.add_argument("--once", action="store_true", help="один проход и выход")
    p.add_argument("--id", help="ID воркера (по умолчанию SAQ_WORKER_ID или имя хоста)")
    p.add_argument("--schema-wait", type=float, default=None,
                   help="сколько секунд ждать схему БД (с --once по умолчанию 0)")
    args = p.parse_args(argv)
    logs.install()
    s = get_settings()
    worker_id = (args.id or s.worker_id or socket.gethostname())[:48]
    stop = threading.Event()
    if args.once:
        if not wait_for_schema(default_engine, poll=0.5, max_wait=args.schema_wait or 0.0):
            log.warning("Схема БД не готова (%s) — запуск пропущен", schema_version(default_engine) or "нет")
            return 0
        try:
            n = run_once(worker_id=worker_id, stop=stop)
        finally:
            release_lease(worker_id)
        log.info("Воркер %s: выполнено заданий: %s", worker_id, n)
        return 0
    previous = {}
    hb: Optional[threading.Thread] = None
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        if not wait_for_schema(default_engine, max_wait=args.schema_wait, stop=stop):
            log.warning("Схема БД не готова (%s) — воркер остановлен", schema_version(default_engine) or "нет")
            return 0
        with SessionLocal() as db:
            n = jobs.requeue_own(db, worker_id)
            db.commit()
        if n:
            log.warning("Воркер %s: возвращено в очередь заданий прошлого запуска: %s", worker_id, n)
        configured = _configured(s)
        from .integrations import registry

        log.info("Воркер %s запущен: %s подключений, настроено: %s", worker_id, len(registry.CONNECTORS),
                 ", ".join(configured) or "нет")
        current = Current()
        hb = start_heartbeat(worker_id, current, stop)
        while not stop.is_set():
            try:
                n = run_once(worker_id=worker_id, current=current, stop=stop)
            except Exception:  # noqa: BLE001 — сбой БД и т. п.: пауза и следующий проход
                log.exception("Проход воркера не выполнен")
                n = 0
            if not n:
                stop.wait(s.worker_poll_s)
        log.info("Воркер %s остановлен", worker_id)
        return 0
    finally:
        stop.set()
        if hb is not None:
            hb.join(5)  # последний пульс не перезапишет отметку остановки
        release_lease(worker_id)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
