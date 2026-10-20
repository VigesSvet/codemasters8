"""Воркер сбора и анализа: python -m app.worker [--once] [--id ID] [--schema-wait S].

Один процесс, цикл по таблице jobs: отчёт о локальных сведениях подключений(например, есть ли сессия Telegram) →
такт лидера(планировщик сборов и плановых проверок, возврат зависших заданий, дозапрос Claude по старым публикациям раз
в ANTHROPIC_BACKFILL_INTERVAL_MIN, уборка раз в час) → захват задания →
выполнение → пауза SAQ_WORKER_POLL_S. Планирует только держатель аренды "scheduler"; выполнять задания может любой
воркер. Поток-пульс(раз в 30 с, своя сессия) продлевает пульс воркера, аренду планировщика и блокировку текущего задания
(не дольше SAQ_JOB_MAX_S - потом зависшее задание сможет забрать recover_stale).

Схему БД воркер не создаёт и не обновляет(это делает API при старте, init_db): он ждёт нужную версию схемы. Затем -
аренду worker:{id}: второй живой процесс с тем же ID(docker compose run, --scale) ждёт, пока первый остановится, и не
возвращает в очередь его выполняемые задания(--once при живом процессе с тем же ID пропускается); прошлый запуск,
который упал, аренду не держит. SIGTERM /
SIGINT - текущее задание завершается(подключения проверяют ctx.stopping(); пульс и блокировка задания продлеваются до
конца), затем выход с кодом 0. Сбой шага такта лидера не останавливает остальные шаги и выполнение заданий. Секреты в
журнал не пишутся: только коды подключений.
"""
import argparse
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import jobs, logs, media, models
from .db import SessionLocal, engine as default_engine
from .settings import get_settings

log = logging.getLogger("saq.worker")

HEARTBEAT_S = 30.0
LEASE_TTL_S = 90
WORKER_LEASE_TTL_S = 120
HOUSEKEEPING_S = 3600
PASS_S = 60.0  # проход воркера-демона не дольше: потом новый такт лидера(расписание, зависшие задания)
GALLERY_SYNC_S = 60.0  # фото объектов в такте лидера: новый ЖК не начинается позже, остальные - в следующих тактах
WAIT_LOG_S = 30.0  # ожидание схемы БД или аренды worker:{id} - строка в журнал не чаще
WORKER_WAIT_S = 5.0  # опрос аренды worker:{id}, пока её держит другой процесс
SCHEDULER = "scheduler"
GALLERY_FROM = "gallerySyncFrom"  # info аренды планировщика: id ЖК, после которого продолжить подгрузку фото
# этот процесс: отличает второй процесс с тем же SAQ_WORKER_ID от прошлого запуска(в контейнерах pid у них бывает один)
INSTANCE = uuid.uuid4().hex


# ---------- схема ----------

def schema_version(eng: Engine) -> str | None:
    """Версия схемы в meta(None - таблиц ещё нет или БД недоступна)."""
    try:
        with eng.connect() as conn:
            return conn.execute(text("SELECT value FROM meta WHERE key = 'schema_version'")).scalar()
    except SQLAlchemyError:
        return None


def wait_for_schema(eng: Engine = default_engine, *, poll: float = 2.0, max_wait: float | None = None,
                    stop: threading.Event | None = None) -> bool:
    """Ждать, пока API создаст или обновит схему до models.SCHEMA_VERSION. False - не дождались(max_wait, остановка)."""
    started = time.monotonic()
    logged = None
    while True:
        ver = schema_version(eng)
        if ver == models.SCHEMA_VERSION:
            return True
        if logged is None or time.monotonic() - logged >= WAIT_LOG_S:
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

def acquire_lease(db: Session, name: str, holder: str, ttl_s: int = 60, now: datetime | None = None,
                  info: dict | None = None) -> bool:
    """Аренда name за holder: продлить свою или забрать истёкшую(условный UPDATE), нет строки - создать. Фиксирует
    транзакцию. True - аренда у holder."""
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
    """Остановка воркера: аренда планировщика освобождается сразу(другой воркер не ждёт её истечения), пульс
    worker:{id} отмечается остановленным(info.stoppedAt) - карточка "Подключения" сразу видит, что воркер не запущен,
    а не через WORKER_ALIVE_S; время последнего пульса сохраняется. Пульс, который последним записал другой процесс с
    тем же ID, остаётся: тот процесс жив."""
    L = models.WorkerLease
    now = models.utcnow()
    try:
        with SessionLocal() as db:
            db.execute(update(L).where(L.name == SCHEDULER, L.holder == worker_id).values(expires_at=now)
                       .execution_options(synchronize_session=False))
            own = db.get(L, f"worker:{worker_id}")
            if own is not None and (own.info or {}).get("instance") == INSTANCE:  # чужой пульс(тот же ID) не трогаем
                own.info = {**(own.info or {}), "stoppedAt": now.isoformat()}  # с долями секунды, как пульс
                own.expires_at = now
            db.commit()
    except Exception:
        log.warning("Аренда планировщика не освобождена", exc_info=True)


@dataclass
class Current:
    """Текущее задание воркера - для потока-пульса."""
    job_id: int | None = None
    started: float = 0.0
    warned: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, job_id: int | None) -> None:
        with self.lock:
            self.job_id, self.started = job_id, time.monotonic()

    def get(self) -> tuple[int | None, float]:
        with self.lock:
            return self.job_id, self.started


def _pulse_info(worker_id: str, job_id: int | None = None) -> dict:
    """info аренды worker:{id}: host, pid и instance - кто её держит(_held_elsewhere)."""
    return {"id": worker_id, "pid": os.getpid(), "host": socket.gethostname(), "instance": INSTANCE,
            "version": models.SCHEMA_VERSION, "jobId": job_id}


def beat(worker_id: str, current: Current | None = None) -> None:
    """Один пульс: worker:{id}, аренда планировщика(если у этого воркера), блокировка текущего задания."""
    now = models.utcnow()
    job_id, started = current.get() if current is not None else (None, 0.0)
    L, J = models.WorkerLease, models.Job
    with SessionLocal() as db:
        acquire_lease(db, f"worker:{worker_id}", worker_id, WORKER_LEASE_TTL_S, now,
                      info=_pulse_info(worker_id, job_id))
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
    """Поток-пульс(daemon): раз в HEARTBEAT_S с - beat(); ошибки БД - предупреждение, поток продолжает."""
    while not stop.is_set():
        try:
            beat(worker_id, job_ref)
        except Exception:
            log.warning("Пульс воркера %s не записан", worker_id, exc_info=True)
        stop.wait(HEARTBEAT_S)


def _pid_running(pid: int) -> bool:
    if os.name != "posix":  # os.kill(pid, 0) вне POSIX завершает процесс, а не проверяет его
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # процесс есть, но другого пользователя
        pass
    return True


def _held_elsewhere(lease: models.WorkerLease, now: datetime) -> bool:
    """Держит ли аренду worker:{id} другой живой процесс: она не истекла(пульс не старше WORKER_LEASE_TTL_S;
    release_lease при штатной остановке истекает её сразу), записана не этим процессом и не прошлым запуском на этом же
    хосте, который уже завершён - его pid наш(перезапуск контейнера: тот же pid) или такого процесса нет. Хост - имя
    хоста: у каждого контейнера Docker оно своё(id контейнера), если в compose не задано hostname."""
    if models.as_utc(lease.expires_at) <= now:
        return False
    info = lease.info or {}
    if info.get("instance") == INSTANCE:
        return False
    pid = info.get("pid")
    if info.get("host") == socket.gethostname() and type(pid) is int and pid > 0:
        return pid != os.getpid() and _pid_running(pid)
    return True


def take_worker_id(db: Session, worker_id: str, now: datetime | None = None) -> dict | None:
    """Старт воркера(демон, --once): аренда worker:{id} - этому процессу, если её не держит другой живой процесс с тем
    же ID(_held_elsewhere). acquire_lease тут не годится: holder у обоих процессов один. Условный UPDATE по прочитанной
    строке - из двух одновременно стартующих процессов аренду получает один. Фиксирует транзакцию. None - аренда у
    этого процесса; иначе - info держателя({} - её только что взял другой процесс)."""
    L = models.WorkerLease
    now = models.as_utc(now) or models.utcnow()
    name = f"worker:{worker_id}"
    lease = db.get(L, name)
    if lease is not None and _held_elsewhere(lease, now):
        info = dict(lease.info or {})
        db.rollback()
        return info
    values = {"holder": worker_id, "expires_at": now + timedelta(seconds=WORKER_LEASE_TTL_S), "heartbeat_at": now,
              "info": _pulse_info(worker_id)}
    if lease is None:
        ok = models.insert_ignore(db, L, {"name": name, **values}, ("name",))
    else:
        ok = db.execute(update(L).where(L.name == name, L.holder == lease.holder,
                                        L.expires_at == models.as_utc(lease.expires_at)).values(**values)
                        .execution_options(synchronize_session=False)).rowcount == 1
    db.commit()
    return None if ok else {}


def _log_busy(worker_id: str, holder: dict, then: str) -> None:
    log.warning("Воркер %s уже запущен другим процессом (хост %s, pid %s) — %s", worker_id, holder.get("host") or "?",
                holder.get("pid") or "?", then)


def wait_for_worker_id(worker_id: str, stop: threading.Event, *, poll: float = WORKER_WAIT_S) -> bool:
    """Ждать аренду worker:{id}(take_worker_id), пока её держит другой живой процесс с тем же ID: его выполняемые
    задания - не задания прошлого запуска, и requeue_own их не трогает(иначе платный сбор шёл бы дважды). False -
    остановлен сигналом, пока ждал."""
    logged = None
    while True:
        with SessionLocal() as db:
            holder = take_worker_id(db, worker_id)
        if holder is None:
            return True
        if logged is None or time.monotonic() - logged >= WAIT_LOG_S:
            _log_busy(worker_id, holder, "его задания не возвращаются в очередь; ожидание его остановки")
            logged = time.monotonic()
        if stop.wait(poll):
            return False


def start_heartbeat(worker_id: str, job_ref: Current, stop: threading.Event) -> threading.Thread:
    t = threading.Thread(target=heartbeat, args=(worker_id, job_ref, stop), name=f"heartbeat-{worker_id}",
                         daemon=True)
    t.start()
    return t


# ---------- планировщик ----------

def schedule_due(db: Session, now: datetime) -> list[int]:
    """Сборы по расписанию и плановые проверки, которым пора(без commit). Возвращает id созданных запусков. Сбой
    проверок одного подключения(не БД) или отказ постановки(service.Rejected) - запись в журнал, остальные
    планируются; ошибка БД и прочий сбой постановки - выше: запуск без задания не фиксируется."""
    from .integrations import registry, service

    s = get_settings()
    now = models.as_utc(now)
    created: list[int] = []
    for code, conn in registry.CONNECTORS.items():
        try:
            triggers = _due_triggers(db, s, code, conn, now)
        except SQLAlchemyError:
            raise
        except Exception:
            log.exception("Расписание подключения %s не обработано", code)
            continue
        for trigger in triggers:
            try:
                run, new = service.request_run(db, code, trigger=trigger, who=jobs.SYSTEM, now=now)
            except service.Rejected as e:  # отказ - до записи или после её отмены(гонка постановки)
                log.warning("Подключение %s: запуск по расписанию не поставлен — %s", code, e.detail)
                continue
            if not new:
                continue
            created.append(run.id)
            if trigger == "test":
                log.info("Плановая проверка «%s»: запуск №%s", conn.name, run.id)
            else:
                log.info("Сбор «%s» по расписанию: запуск №%s", conn.name, run.id)
    return created


def _due_triggers(db: Session, s, code: str, conn, now: datetime) -> list[str]:
    """Что пора поставить одному подключению(только чтение): "schedule" - сбор по расписанию, "test" - плановая
    проверка."""
    from .integrations import service

    due: list[str] = []
    r = service.row(db, code)
    caps = conn.capabilities or {}
    if conn.kind == "collector" and caps.get("collect") and r is not None and r.enabled \
            and not conn.missing(s, r) and r.status in jobs.SCHEDULABLE and not (r.facts or {}).get("stopped") \
            and not service.keys_changed(conn, r) \
            and (r.next_run_at is None or models.as_utc(r.next_run_at) <= now) \
            and jobs.active(db, f"collect:{code}") is None:
        due.append("schedule")
    # остановленное подключение(ошибка настроек, платный сбор) плановой проверкой не "оживляется":
    # остановку снимают изменение настроек или проверка администратором
    if conn.health_check_min and caps.get("test") and not conn.missing(s, r) \
            and not (r is not None and (r.facts or {}).get("stopped")) \
            and jobs.active(db, f"test:{code}") is None and conn.health_due(db, r, now):
        due.append("test")
    return due


# ---------- уборка ----------

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
    """Периодическая работа лидера(отметка mark в info аренды планировщика): прошло every_s с - отметка обновляется
    (commit), True."""
    lease = db.get(models.WorkerLease, SCHEDULER)
    if lease is None or lease.holder != worker_id:
        return False
    last = (lease.info or {}).get(mark)
    try:
        due = last is None or models.as_utc(datetime.fromisoformat(last)) <= now - timedelta(seconds=every_s)
    except (TypeError, ValueError):
        due = True
    if due:
        lease.info = {**(lease.info or {}), mark: models.iso(now)}
        db.commit()
    return due


def _lease_mark(db: Session, worker_id: str, mark: str, value: str | None) -> None:
    """Отметка mark в info аренды планировщика: value или снять(None); commit."""
    lease = db.get(models.WorkerLease, SCHEDULER)
    if lease is None or lease.holder != worker_id or (lease.info or {}).get(mark) == value:
        return
    info = {k: v for k, v in (lease.info or {}).items() if k != mark}
    lease.info = info if value is None else {**info, mark: value}
    db.commit()


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
    """Задания анализа для старых публикаций без ответа Claude(app.llm.backfill: новые первыми, партия, бюджет).
    Фиксирует транзакцию; сбой - предупреждение в журнал, такт лидера продолжается."""
    from . import llm

    try:
        ids = llm.backfill(db, now)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("Дозапрос анализа ИИ не выполнен", exc_info=True)
        return []
    if ids:
        log.info("Дозапрос анализа ИИ: поставлено заданий анализа %s", len(ids))
    return ids


# ---------- фото объектов: карточки Homeportal для ЖК реестра без фото ----------

def _gallery_due(db: Session, worker_id: str, now: datetime) -> bool:
    hours = int(get_settings().gallery_sync_hours)
    return hours > 0 and _periodic_due(db, worker_id, now, "gallerySyncAt", hours * 3600.0)


def gallery_sync(db: Session, stop: threading.Event | None = None, *, after: str | None = None,
                 budget_s: float | None = None) -> str | None:
    """Фото карточек Homeportal для ЖК открытого реестра, у которых их ещё нет(app.gallery.fetch_each, missing_only),
    по порядку id после after. Через budget_s(по умолчанию GALLERY_SYNC_S) новый ЖК не начинается: такт лидера не
    задерживает задания(доставку предупреждений, сборы) на весь реестр. stop - сигнал остановки воркера: подгрузка
    прерывается, и SIGTERM укладывается в stop_grace_period. Возвращает, откуда продолжить: id последнего пройденного
    ЖК("" - с начала), None - проход завершён. Сбой - предупреждение в журнал, проход считается завершённым(повтор -
    через период), такт лидера продолжается."""
    from . import gallery

    budget = GALLERY_SYNC_S if budget_s is None else budget_s
    started, last, added, n = time.monotonic(), after or "", 0, 0
    cut = False
    try:
        for c, rep in gallery.fetch_each(db, missing_only=True, stop=stop, after=after):
            if rep.get("stopped"):  # прерванный ЖК не записан - с него и продолжить
                break
            last, added, n = c.id, added + rep["added"], n + 1
            if time.monotonic() - started >= budget:
                cut = True
                break
    except Exception:
        db.rollback()
        log.warning("Фото объектов с Homeportal не подгружены", exc_info=True)
        return None
    if added:
        log.info("Фото объектов с Homeportal: добавлено %s (ЖК: %s)", added, n)
    return last if cut or (stop is not None and stop.is_set()) else None


def _gallery_step(db: Session, worker_id: str, now: datetime, stop: threading.Event | None) -> None:
    """Фото объектов в такте лидера: проход раз в SAQ_GALLERY_SYNC_HOURS; прерванный(бюджет такта, остановка воркера)
    продолжается со следующего ЖК(GALLERY_FROM) в первый же такт лидера, а не через период и не с начала - иначе ЖК,
    чьи карточки не отдают фото, занимали бы каждый такт."""
    lease = db.get(models.WorkerLease, SCHEDULER)
    after = (lease.info or {}).get(GALLERY_FROM) if lease is not None and lease.holder == worker_id else None
    if not isinstance(after, str):
        if not _gallery_due(db, worker_id, now):
            return
        after = None
    elif int(get_settings().gallery_sync_hours) <= 0:  # подгрузку выключили посреди прохода
        _lease_mark(db, worker_id, GALLERY_FROM, None)
        return
    _lease_mark(db, worker_id, GALLERY_FROM, gallery_sync(db, stop, after=after or None))


# ---------- сведения, видимые только воркеру ----------

def report_local(db: Session) -> None:
    """Connector.report() → collectors.state.report(только при изменении; время проверки не считается изменением)."""
    from .integrations import registry, service

    s = get_settings()
    changed = False
    for code, conn in registry.CONNECTORS.items():
        try:
            rep = conn.report(s)
        except Exception:
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

def _leader_step(db: Session, failed: str, fn: Callable[..., object], *args) -> None:
    """Шаг такта лидера с фиксацией: сбой - откат и предупреждение failed в журнал, остальные шаги такта идут."""
    try:
        fn(db, *args)
        db.commit()
    except Exception:
        db.rollback()
        log.warning(failed, exc_info=True)


def _tick(worker_id: str, now: datetime, stop: threading.Event | None = None) -> None:
    """Такт лидера: аренда планировщика, сборы и проверки по расписанию, зависшие задания, дозапрос Claude, фото
    объектов(прерываются по stop), уборка."""
    s = get_settings()
    with SessionLocal() as db:
        if not acquire_lease(db, SCHEDULER, worker_id, LEASE_TTL_S, now=models.utcnow()):
            return
        # сначала зависшие задания(транзакция на задание): брошенный сбор получает итог "ошибка" до решений
        # расписания, и блокировки строк подключений не пересекаются с итогом, который пишет другой воркер
        _leader_step(db, "Возврат зависших заданий не выполнен", jobs.recover_stale, now, s.job_lease_s)
        _leader_step(db, "Сборы и проверки по расписанию не поставлены", schedule_due, now)
        if _backfill_due(db, worker_id, now):
            backfill(db, now)
        _gallery_step(db, worker_id, now, stop)
        if _housekeeping_due(db, worker_id, now):
            _leader_step(db, "Уборка не выполнена", housekeeping, now)


def run_once(*, worker_id: str, now: datetime | None = None, max_jobs: int = 100,
             current: Current | None = None, stop: threading.Event | None = None,
             max_s: float | None = None) -> int:
    """Один проход: пульс, отчёт, такт лидера, затем задания, пока они есть(не больше max_jobs и, если задано, новых
    заданий не позже max_s с от начала прохода - очередь долгих анализов не откладывает расписание). Возвращает число
    выполненных заданий. now - время для расписания и захвата(тесты); по умолчанию - текущее."""
    started = time.monotonic()
    current = current or Current()
    beat(worker_id, current)
    # сбой отчёта или такта лидера не останавливает выполнение заданий
    try:
        with SessionLocal() as db:
            report_local(db)
    except Exception:
        log.warning("Отчёт подключений не записан", exc_info=True)
    try:
        _tick(worker_id, models.as_utc(now) or models.utcnow(), stop)
    except Exception:
        log.warning("Такт лидера не выполнен", exc_info=True)
    done = 0
    while done < max_jobs and not (stop is not None and stop.is_set()):
        if max_s is not None and done and time.monotonic() - started >= max_s:
            break
        t = models.as_utc(now) or models.utcnow()
        with SessionLocal() as db:
            job = jobs.claim(db, worker_id, t)
            job_id = job.id if job is not None else None
        if job_id is None:
            break
        current.set(job_id)
        try:
            jobs.execute(job_id, worker_id=worker_id, now=t, stop=stop)
        except Exception:
            log.exception("Задание №%s: итог не записан", job_id)
        finally:
            current.set(None)
        done += 1
    return done


def _configured(s) -> list[str]:
    from .integrations import registry

    return [code for code, conn in registry.CONNECTORS.items() if not conn.missing(s)]


@contextmanager
def _pulse(worker_id: str, current: Current) -> Iterator[None]:
    """Поток-пульс на время работы. Свой сигнал остановки, а не stop воркера: после SIGTERM пульс и блокировка
    текущего задания продлеваются, пока оно завершается. На выходе - аренды освобождаются(release_lease)."""
    done = threading.Event()
    t = start_heartbeat(worker_id, current, done)
    try:
        yield
    finally:
        done.set()
        t.join(5)  # последний пульс не перезапишет отметку остановки
        release_lease(worker_id)


def main(argv: list | None = None) -> int:
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
        with SessionLocal() as db:
            holder = take_worker_id(db, worker_id)
        if holder is not None:  # пульс и отметка остановки --once легли бы поверх пульса живого процесса с тем же ID
            _log_busy(worker_id, holder, "запуск пропущен")
            return 0
        current = Current()
        with _pulse(worker_id, current):  # долгое задание и в --once не отдаётся recover_stale другого воркера
            n = run_once(worker_id=worker_id, current=current, stop=stop)
        log.info("Воркер %s: выполнено заданий: %s", worker_id, n)
        return 0
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        if not wait_for_schema(default_engine, max_wait=args.schema_wait, stop=stop):
            if not stop.is_set():  # остановлен сигналом, пока ждал схему, - это не ошибка схемы
                log.warning("Схема БД не готова (%s) — воркер остановлен", schema_version(default_engine) or "нет")
            return 0
        if not wait_for_worker_id(worker_id, stop):  # остановлен сигналом, пока ждал аренду
            return 0
        with SessionLocal() as db:  # аренда у этого процесса: "running" с этим ID - задания упавшего прошлого запуска
            n = jobs.requeue_own(db, worker_id)
            db.commit()
        if n:
            log.warning("Воркер %s: возвращено в очередь заданий прошлого запуска: %s", worker_id, n)
        configured = _configured(s)
        from .integrations import registry

        log.info("Воркер %s запущен: %s подключений, настроено: %s", worker_id, len(registry.CONNECTORS),
                 ", ".join(configured) or "нет")
        current = Current()
        with _pulse(worker_id, current):
            while not stop.is_set():
                try:
                    n = run_once(worker_id=worker_id, current=current, stop=stop, max_s=PASS_S)
                except Exception:
                    log.exception("Проход воркера не выполнен")
                    n = 0
                if not n:
                    stop.wait(s.worker_poll_s)
        log.info("Воркер %s остановлен", worker_id)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
