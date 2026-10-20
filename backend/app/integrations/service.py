"""Состояние подключений для API и воркера: строки collectors, фактический статус, настройки, запуски, переходы статуса
с записью в журнал, квоты, учёт отказов клиентов приёма.

Фактический статус - хранимый статус с наложениями(по порядку): нет ключей на сервере → "не настроен"; ключи
сменились после последней проверки → "не проверен"; идёт проверка → "проверяется"; сбор в очереди / идёт; живой статус
подключения(приём, анализатор, OAuth); иначе - хранимый. Значения секретов сюда не попадают: только имена переменных,
отпечатки и несекретные сведения(facts, очищенные ingest.scrub).

Функции работают в сессии вызывающего и не фиксируют транзакцию; исключения - reserve_quota, adjust_quota и
record_reject(своя короткая сессия: квота учитывается до сетевого вызова, отказ клиента пишется, даже если запрос
отклонён).
"""
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from .. import jobs, models, schemas
from ..ads_service import SYSTEM, _event
from ..db import SessionLocal
from ..ingest import scrub
from ..logs import redact
from ..settings import get_settings
from . import registry
from .base import RUN_STATUS_LABELS, Connector, local_time

log = logging.getLogger("saq.integrations")

KEYS_CHANGED = ("Ключи на сервере изменились после последней проверки — перезапустите воркер и нажмите «Проверить "
                "подключение»")
ACTIVE_RUN = ("queued", "running")
COLLECTABLE = jobs.SCHEDULABLE
REJECT_THROTTLE_S = 5.0
WORKER_ALIVE_S = 120
# Вид событий журнала о подключениях(статус с пояснением проверки, настройки, аккаунт): в них сведения, которые
# посетителю не показываются(PUBLIC_CONNECTOR_FIELDS), - журнал отдаёт их только администратору
AUDIT_KIND = "connector"
CONFIG_LABELS = {"accounts": "аккаунты", "channels": "каналы", "profiles": "профили", "searchUrls": "URL поиска",
                 "cardUrls": "карточки", "resultsPerPage": "результатов на профиль", "downloadVideos": "загрузка видео"}


class Rejected(Exception):
    """Действие невозможно: status - HTTP-код(404, 409), detail - по-русски."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Invalid(Exception):
    """Неверное значение поля запроса(422): loc - путь поля, msg - по-русски."""

    def __init__(self, loc: tuple, msg: str, value: Any = None):
        super().__init__(msg)
        self.loc, self.msg, self.value = loc, msg, value


# ---------- строки подключений ----------

def row(db: Session, code: str, *, create: bool = False, lock: bool = False) -> models.Collector | None:
    """Строка подключения; create - создать, если нет(INSERT … ON CONFLICT DO NOTHING и перечитывание). lock -
    SELECT … FOR UPDATE со свежими значениями(на PostgreSQL; SQLite и так пишет по одному): чтение-изменение-запись
    JSON-полей(state, facts, config) не затирает параллельную запись другого запроса или воркера."""
    C = models.Collector
    r = db.get(C, code, with_for_update=lock or None, populate_existing=lock)
    if r is None and create:
        models.insert_ignore(db, C, {"code": code, "enabled": False, "config": {}, "status": "unchecked", "facts": {},
                                     "state": {}, "failures": 0}, ("code",))
        r = db.get(C, code, with_for_update=lock or None, populate_existing=True)
    return r


def keys_changed(conn: Connector, r: models.Collector | None) -> bool:
    """Отпечатки ключей, с которыми прошла последняя проверка, не совпадают с текущими на сервере."""
    saved = ((r.facts or {}).get("keyFp") if r is not None else None) or {}
    if not isinstance(saved, dict) or not saved:
        return False
    s = get_settings()
    return any(s.key_fp(name) != fp for name, fp in saved.items())


@dataclass
class _Jobs:
    test: models.Job | None = None
    collect: models.Job | None = None


def _active_jobs(db: Session, codes: list | None = None) -> dict[str, _Jobs]:
    J = models.Job
    q = select(J).where(J.status.in_(jobs.ACTIVE), J.kind.in_(("collect", "test")))
    if codes is not None:
        q = q.where(J.collector.in_(codes))
    out: dict[str, _Jobs] = {}
    for j in db.scalars(q.order_by(J.id)):
        slot = out.setdefault(j.collector or "", _Jobs())
        setattr(slot, j.kind, j)
    return out


def effective_status(db: Session, conn: Connector, r: models.Collector | None, now: datetime,
                     active: _Jobs | None = None) -> tuple[str, str | None]:
    """(статус, пояснение): хранимый статус с наложениями очереди, ключей и пульса воркера."""
    s = get_settings()
    if conn.missing(s, r):
        return "not_configured", conn.missing_detail(s, r) or None
    if keys_changed(conn, r):
        return "unchecked", KEYS_CHANGED
    if active is None:
        active = _active_jobs(db, [conn.code]).get(conn.code, _Jobs())
    if active.test is not None:
        return "checking", None
    if active.collect is not None:
        return ("running", None) if active.collect.status == "running" else ("queued", None)
    computed = conn.computed_status(db, r, now)
    if computed is not None:
        return computed
    if r is None:
        return "unchecked", None
    return r.status or "unchecked", r.status_detail


# ---------- запуски ----------

def run_out(db: Session, run: models.CollectionRun) -> dict:
    """RunOut: счётчики, заметки, когда повтор(если задание ждёт повтора или продолжения)."""
    J = models.Job
    retry_at = None
    if run.status in ACTIVE_RUN:
        job = db.scalar(select(J).where(J.run_id == run.id).order_by(J.id.desc()).limit(1))
        if job is not None and job.status == "queued" and (job.attempts or (job.payload or {}).get("deferrals")):
            retry_at = job.run_after
    return {"id": run.id, "source": run.collector, "trigger": run.trigger, "status": run.status,
            "status_label": RUN_STATUS_LABELS.get(run.status, run.status), "requested_by": run.requested_by,
            "created_at": run.created_at, "started_at": run.started_at, "finished_at": run.finished_at,
            "counts": {"seen": run.items_seen or 0, "new": run.items_new or 0, "updated": run.items_updated or 0,
                       "unchanged": run.items_unchanged or 0, "failed": run.items_failed or 0,
                       "media_saved": run.media_saved or 0, "media_failed": run.media_failed or 0},
            "external_run_id": run.external_run_id, "cost_usd": run.cost_usd, "error_code": run.error_code,
            "error": run.error, "notes": list(run.notes or [])[-100:], "retry_at": retry_at}


def start_run(db: Session, run_id: int, now: datetime) -> models.CollectionRun | None:
    """Запуск - "выполняется"(время начала - первое)."""
    run = db.get(models.CollectionRun, run_id)
    if run is None:
        return None
    if run.status in ACTIVE_RUN:
        run.status = "running"
        run.started_at = run.started_at or models.as_utc(now)
    return run


def finish_run(db: Session, run_id: int, status: str, *, error_code: str | None = None,
               error: str | None = None, now: datetime | None = None) -> None:
    run = db.get(models.CollectionRun, run_id)
    if run is None:
        return
    run.status, run.finished_at = status, models.as_utc(now) or models.utcnow()
    run.error_code = error_code
    run.error = redact(error) if error else None


def transition(db: Session, code: str, status: str, detail: str | None = None, *,
               at: datetime | None = None, audit: bool = True) -> None:
    """Хранимый статус подключения; при смене - событие журнала "Источник "…": {статус}. {пояснение}"(вид
    AUDIT_KIND: в пояснении - аккаунты, модель, лимиты, тексты ошибок)."""
    conn = registry.get(code)
    r = row(db, code, create=True)
    old = r.status
    r.status, r.status_detail = status, redact(detail) if detail else None
    if audit and old != status:
        what = f"Источник «{conn.name}»: {conn.status_label(status)}"
        if r.status_detail:
            what += f". {r.status_detail}"
        _event(db, AUDIT_KIND, SYSTEM, what, at=at)


def not_configured_detail(conn: Connector, s, r: models.Collector | None, missing: list[str]) -> str:
    """Текст 409 "не настроено": каких переменных окружения нет на сервере. Не хватает не переменной, а условия(нет
    сессии Telegram: TG_SESSION_PATH задан, нужен вход) - пояснение самого подключения(missing_detail), а не совет
    задать переменную, которая уже задана."""
    env = [n for n in missing if n in conn.required_env]
    if not env:
        detail = conn.missing_detail(s, r)
        if detail:
            return detail
        env = list(missing)
    return f"Сначала настройте подключение на сервере: {', '.join(env)}"


def request_run(db: Session, code: str, *, trigger: str, who: str,
                now: datetime | None = None) -> tuple[models.CollectionRun, bool]:
    """Запуск проверки(trigger test) или сбора(manual / schedule) в очередь. Уже есть активный - он и возвращается
    (created=False). Без commit."""
    conn = registry.get(code)
    now = models.as_utc(now) or models.utcnow()
    kind = "test" if trigger == "test" else "collect"
    caps = conn.capabilities or {}
    if kind == "collect" and (conn.kind != "collector" or not caps.get("collect")):
        raise Rejected(409, "У этого подключения нет сбора публикаций")
    if kind == "test" and not caps.get("test"):
        raise Rejected(409, "У этого подключения нет проверки")
    r = row(db, code, create=True)
    s = get_settings()
    missing = conn.missing(s, r)
    if missing:
        raise Rejected(409, not_configured_detail(conn, s, r, missing))
    key = f"{kind}:{code}"
    existing = jobs.active(db, key)
    if existing is not None and existing.run_id is not None:
        run = db.get(models.CollectionRun, existing.run_id)
        if run is not None:
            return run, False
    run = models.CollectionRun(collector=code, trigger=trigger, status="queued", requested_by=who, created_at=now,
                               notes=[])
    db.add(run)
    db.flush()
    job, created = jobs.enqueue(db, kind, dedupe_key=key, collector=code, run_id=run.id,
                                max_attempts=1 if kind == "test" else max(int(conn.max_attempts or 1), 1),
                                run_after=now)
    if not created:  # гонка: активное задание поставили одновременно - свой запуск не нужен
        db.delete(run)
        db.flush()
        other = db.get(models.CollectionRun, job.run_id) if job.run_id is not None else None
        if other is not None:
            return other, False
        raise Rejected(409, "Запуск уже идёт")
    r.last_run_id = run.id
    return run, True


def _stopped(r: models.Collector | None) -> bool:
    return bool(r is not None and (r.facts or {}).get("stopped"))


def collect_all(db: Session, who: str, now: datetime) -> dict:
    """"Обновить всё": сбор для каждого включённого, настроенного и проверенного сборщика; остальные - с причиной."""
    s = get_settings()
    now = models.as_utc(now)
    active = _active_jobs(db)
    queued, skipped = [], []
    for code, conn in registry.CONNECTORS.items():
        if conn.kind != "collector" or not (conn.capabilities or {}).get("collect"):
            continue
        r = row(db, code)
        reason = None
        if r is None or not r.enabled:
            reason = "выключен"
        elif conn.missing(s, r):
            reason = "не настроен"
        elif active.get(code, _Jobs()).collect is not None:
            reason = "уже идёт"
        elif _stopped(r):
            reason = "остановлен после ошибки — запустите проверку"
        elif r.status == "unchecked" or keys_changed(conn, r):
            reason = "не проверен — сначала «Проверить подключение»"
        elif r.status == "access_check":
            reason = "нужно ручное действие"
        elif r.status == "expired":
            reason = "истёк доступ"
        elif r.status == "denied":
            reason = "нет доступа"
        elif r.status == "rate_limited" and r.next_run_at is not None and models.as_utc(r.next_run_at) > now:
            reason = f"пауза до {local_time(r.next_run_at)}"
        elif r.status not in COLLECTABLE:
            reason = "не настроен" if r.status == "not_configured" else f"статус «{conn.status_label(r.status)}»"
        if reason is None:
            try:
                run, _ = request_run(db, code, trigger="manual", who=who, now=now)
            except Rejected as e:  # одно подключение не роняет "Обновить всё"(гонка с параллельной постановкой)
                reason = e.detail
            else:
                queued.append({"code": code, "run_id": run.id})
                continue
        skipped.append({"code": code, "reason": reason})
    return {"queued": queued, "skipped": skipped}


# ---------- настройки ----------

def _diff(label: str, old: Any, new: Any) -> str | None:
    if isinstance(new, list):
        before = list(old or [])
        added = [x for x in new if x not in before]
        removed = [x for x in before if x not in new]
        if not added and not removed:
            return None
        parts = [f"+{x}" for x in added] + [f"−{x}" for x in removed]
        return f"{label}: {', '.join(parts)}"
    if old == new:
        return None
    if isinstance(new, bool):
        return f"{label}: {'да' if new else 'нет'}"
    return f"{label}: {new}"


def patch(db: Session, code: str, body: schemas.ConnectorPatchIn, *, now: datetime | None = None) -> None:
    """Настройки подключения администратором: включение сбора, период, списки источников. Изменения - в журнал от имени
    автора(вид AUDIT_KIND: списки аккаунтов, каналов и адресов). Rejected(409) / Invalid(422) / base.ConfigError(422,
    loc config.поле). Без commit."""
    conn = registry.get(code)
    now = models.as_utc(now) or models.utcnow()
    s = get_settings()
    caps = conn.capabilities or {}
    r = row(db, code, create=True, lock=True)  # config и facts дополняются - не затереть запись воркера
    status_before, _ = effective_status(db, conn, r, now)
    changes: list[str] = []
    fields = body.model_fields_set
    if "config" in fields and body.config is not None:
        if not caps.get("configure") or not (conn.config_fields or conn.options):
            raise Rejected(409, "У этого подключения нет настроек сбора")
        cfg = body.config.model_dump(by_alias=True, exclude_none=True)
        valid = conn.validate_config(cfg)
        old = dict(r.config or {})
        for key, value in valid.items():
            d = _diff(CONFIG_LABELS.get(key, key), old.get(key), value)
            if d:
                changes.append(d)
        if valid and changes:
            r.config = {**old, **valid}
            facts = dict(r.facts or {})
            if facts.get("stoppedCode") == "bad_config" and not conn.paid:
                facts.pop("stopped", None)
                facts.pop("stoppedCode", None)
                r.facts = facts
    if "interval_min" in fields and body.interval_min is not None:
        if not caps.get("toggle"):
            raise Rejected(409, "У этого подключения нет сбора по расписанию")
        lo, hi = conn.interval_bounds
        if not lo <= body.interval_min <= hi:
            raise Invalid(("body", "intervalMin"), f"Период — от {lo} до {hi} минут", body.interval_min)
        if body.interval_min != (r.interval_min or conn.default_interval_min):
            changes.append(f"период: {body.interval_min} мин")
            _pull_next_run(r, body.interval_min, now)
        r.interval_min = body.interval_min
    if "enabled" in fields and body.enabled is not None and body.enabled != bool(r.enabled):
        if not caps.get("toggle"):
            raise Rejected(409, "У этого подключения нет сбора по расписанию")
        if body.enabled:
            missing = conn.missing(s, r)
            if missing:
                raise Rejected(409, not_configured_detail(conn, s, r, missing))
            why = conn.can_enable(r)
            if why:
                raise Rejected(409, why)
            r.enabled = True
            changes.append("сбор включён")
            if status_before == "unchecked" and caps.get("test"):
                request_run(db, code, trigger="test", who=body.author, now=now)
        else:
            r.enabled = False
            changes.append("сбор выключен")
            _cancel_queued_collect(db, code, now)
    if changes:
        r.updated_by, r.updated_at = body.author, now
        _event(db, AUDIT_KIND, body.author, f"Настройки источника «{conn.name}» изменены: {'; '.join(changes)}", at=now)


def _pull_next_run(r: models.Collector, interval_min: int, now: datetime) -> None:
    """Период изменён: следующий сбор по расписанию - не позже, чем через новый период(иначе сбор, назначенный по
    прежнему недельному периоду, ждал бы неделю и после перехода на два часа). Пауза после ошибки или лимита частоты
    не сокращается - её назначил итог запуска."""
    if r.status not in ("ready", "working", "partial") or r.next_run_at is None:
        return
    latest = now + timedelta(minutes=interval_min)
    if models.as_utc(r.next_run_at) > latest:
        r.next_run_at = latest


def _cancel_queued_collect(db: Session, code: str, now: datetime) -> None:
    """Сбор выключен: сбор, который ещё ждёт в очереди, отменяется(выполняющийся - доработает). Задание с уже
    начатым внешним запуском(Apify: ждёт между фазами) не отменяется - иначе оплаченный запуск не будет дочитан
    и его стоимость не запишется; оно доработает, новых по расписанию не будет. Задание, которое воркер успел
    захватить, не отменяется(jobs.cancel - только "ещё в очереди"), и его запуск тоже."""
    job = jobs.active(db, f"collect:{code}")
    if job is None or job.status != "queued":
        return
    run = db.get(models.CollectionRun, job.run_id) if job.run_id is not None else None
    if run is not None and run.external_run_id:
        return
    if jobs.cancel(db, job, now=now) and run is not None:
        finish_run(db, run.id, "cancelled", now=now)


# ---------- для страницы "Подключения" ----------

def _counts(db: Session, codes: list[str]) -> dict[str, dict]:
    I, A = models.IngestItem, models.MediaAsset
    out: dict[str, dict] = {}
    for code, items, ads in db.execute(select(I.collector, func.count(I.id), func.count(func.distinct(I.ad_id)))
                                       .where(I.collector.in_(codes)).group_by(I.collector)):
        out[code] = {"items": items, "ads": ads, "media": 0}
    for code, n in db.execute(select(I.collector, func.count(A.id)).join(I, A.item_id == I.id)
                              .where(A.status == "downloaded", I.collector.in_(codes)).group_by(I.collector)):
        out.setdefault(code, {"items": 0, "ads": 0, "media": 0})["media"] = n
    return out


def _last_runs(db: Session, codes: list[str]) -> dict[str, models.CollectionRun]:
    R = models.CollectionRun
    ids = db.scalars(select(func.max(R.id)).where(R.collector.in_(codes)).group_by(R.collector)).all()
    return {r.collector: r for r in db.scalars(select(R).where(R.id.in_(ids)))} if ids else {}


def connector_out(db: Session, conn: Connector, r: models.Collector | None, *, now: datetime,
                  active: _Jobs | None = None, counts: dict | None = None,
                  last: models.CollectionRun | None = None) -> dict:
    s = get_settings()
    if active is None:
        active = _active_jobs(db, [conn.code]).get(conn.code, _Jobs())
    status, detail = effective_status(db, conn, r, now, active)
    caps = {"test": False, "collect": False, "toggle": False, "configure": False, "connect": False,
            **(conn.capabilities or {})}
    collector = conn.kind == "collector"
    missing = conn.missing(s, r)
    run_job = active.collect or active.test
    facts = scrub(dict(r.facts or {})) if r is not None else {}
    return {
        "code": conn.code, "kind": conn.kind, "group": conn.group, "platform": conn.platform, "name": conn.name,
        "via": conn.via, "status": status, "status_label": conn.status_label(status), "status_detail": detail,
        "enabled": bool(r.enabled) if r is not None else False, "configured": not missing,
        "missing": missing, "secret_refs": list(conn.secret_refs),
        "config": dict(r.config or {}) if r is not None else {}, "config_fields": list(conn.config_fields),
        "options": list(conn.options),
        "interval_min": ((r.interval_min if r is not None else None) or conn.default_interval_min) if collector
        else None,
        "interval_bounds": list(conn.interval_bounds) if collector else None,
        "next_run_at": r.next_run_at if r is not None else None,
        "last_success_at": r.last_success_at if r is not None else None,
        "last_error": r.last_error if r is not None else None,
        "last_error_at": r.last_error_at if r is not None else None,
        "checked_at": r.checked_at if r is not None else None,
        "facts": facts, "counts": counts or {"items": 0, "ads": 0, "media": 0},
        "last_run": run_out(db, last) if last is not None else None,
        "active_run_id": run_job.run_id if run_job is not None else None,
        "capabilities": caps, "limitation": conn.limitation_text(s), "contract_verified": conn.contract_verified,
        "paid": bool(conn.paid), "stopped": _stopped(r),
    }


# Посетителю(без токена администратора) - только состояние: статусы, расписание, счётчики. Без способа
# подключения(адреса API, модели, сервисы), имён ключей, списков источников, пояснений и текстов ошибок, фактов
# проверки(аккаунты, отпечатки ключей), автора, стоимости и заметок запусков, ID воркера. Список разрешённых, а не
# скрытых полей: новое поле сведений посетителю по умолчанию не видно
PUBLIC_CONNECTOR_FIELDS = frozenset({
    "code", "kind", "group", "platform", "name", "status", "status_label", "enabled", "configured", "config_fields",
    "options", "interval_min", "interval_bounds", "next_run_at", "last_success_at", "checked_at", "counts",
    "last_run", "active_run_id", "capabilities", "limitation", "contract_verified", "paid", "stopped"})
PUBLIC_RUN_FIELDS = frozenset({"id", "source", "trigger", "status", "status_label", "created_at", "started_at",
                               "finished_at", "counts", "retry_at"})
PUBLIC_WORKER_FIELDS = frozenset({"alive", "last_seen_at"})


def _only(d: dict, shown: frozenset[str], blank: dict | None = None) -> dict:
    """Поля shown - как есть, остальные - пустые: список - [], словарь - {}, обязательное поле - его значение из
    blank, иначе None."""
    def empty(k: str, v: Any) -> Any:
        if blank and k in blank:
            return blank[k]
        return type(v)() if isinstance(v, (list, dict)) else None
    return {k: v if k in shown else empty(k, v) for k, v in d.items()}


def public_run(run: dict) -> dict:
    return _only(run, PUBLIC_RUN_FIELDS)


def public_connector(c: dict) -> dict:
    out = _only(c, PUBLIC_CONNECTOR_FIELDS, {"via": ""})
    if out.get("last_run"):
        out["last_run"] = public_run(out["last_run"])
    return out


def public_worker(w: dict) -> dict:
    return _only(w, PUBLIC_WORKER_FIELDS)


def view(db: Session, codes: list | None = None, *, now: datetime | None = None) -> list[dict]:
    """ConnectorOut по реестру(порядок страницы) или по выбранным кодам."""
    now = models.as_utc(now) or models.utcnow()
    codes = [c for c in registry.CONNECTORS if codes is None or c in codes]
    rows = {r.code: r for r in db.scalars(select(models.Collector).where(models.Collector.code.in_(codes)))}
    active = _active_jobs(db, codes)
    counts = _counts(db, codes)
    last = _last_runs(db, codes)
    return [connector_out(db, registry.CONNECTORS[c], rows.get(c), now=now, active=active.get(c, _Jobs()),
                          counts=counts.get(c), last=last.get(c)) for c in codes]


def _stopped_at(lease: models.WorkerLease) -> datetime | None:
    raw = (lease.info or {}).get("stoppedAt")
    try:
        return models.as_utc(datetime.fromisoformat(str(raw))) if raw else None
    except ValueError:
        return None


def worker_info(db: Session, now: datetime | None = None) -> dict:
    """Жив ли воркер: пульс любого worker:* не старше 120 с и воркер не отметил остановку(stoppedAt не раньше
    последнего пульса - release_lease при штатной остановке)."""
    now = models.as_utc(now) or models.utcnow()
    L = models.WorkerLease
    leases = list(db.scalars(select(L).where(L.name.like("worker:%")).order_by(L.heartbeat_at.desc()).limit(20)))
    if not leases:
        return {"alive": False, "last_seen_at": None, "id": None}

    def alive(lease) -> bool:
        seen, stopped = models.as_utc(lease.heartbeat_at), _stopped_at(lease)
        return seen is not None and seen >= now - timedelta(seconds=WORKER_ALIVE_S) and not (
            stopped is not None and stopped >= seen)

    lease = next((x for x in leases if alive(x)), leases[0])
    return {"alive": alive(lease), "last_seen_at": models.as_utc(lease.heartbeat_at),
            "id": (lease.info or {}).get("id") or lease.holder}


# ---------- квоты ----------

def reserve_quota(name: str, limit: int, now: datetime | None = None, *, amount: int = 1) -> bool:
    """Учесть amount единиц квоты(по умолчанию один запрос) атомарно, в своей сессии(commit - до сетевого вызова):
    UPDATE … count + amount WHERE count + amount <= limit; строки нет - создать и повторить. False - квота исчерпана."""
    Q = models.QuotaCounter
    now = models.as_utc(now) or models.utcnow()
    amount = max(int(amount), 0)
    with SessionLocal() as db:
        for _ in range(2):
            res = db.execute(update(Q).where(Q.name == name, Q.count + amount <= int(limit))
                             .values(count=Q.count + amount, limit_value=int(limit), updated_at=now)
                             .execution_options(synchronize_session=False))
            if res.rowcount == 1:
                db.commit()
                return True
            if not models.insert_ignore(db, Q, {"name": name, "count": 0, "limit_value": int(limit),
                                                "created_at": now, "updated_at": now}, ("name",)):
                break  # строка есть, а счётчик уже на пределе
        db.commit()
        return False


def adjust_quota(name: str, delta: int, now: datetime | None = None) -> None:
    """Поправка учтённого расхода(резерв → фактический): UPDATE … count + delta атомарно, не ниже нуля, в своей
    сессии. Строки нет - ничего не делает(резерва не было)."""
    Q = models.QuotaCounter
    now = models.as_utc(now) or models.utcnow()
    delta = int(delta)
    if not delta:
        return
    with SessionLocal() as db:
        db.execute(update(Q).where(Q.name == name)
                   .values(count=case((Q.count + delta < 0, 0), else_=Q.count + delta), updated_at=now)
                   .execution_options(synchronize_session=False))
        db.commit()


def quota_used(db: Session, name: str) -> int:
    return int(db.scalar(select(models.QuotaCounter.count).where(models.QuotaCounter.name == name)) or 0)


# ---------- отказы клиентов приёма ----------

_REJECT_AT: dict[tuple, float] = {}
_REJECT_LOCK = threading.Lock()


def record_reject(code: str, status: int, detail: str) -> None:
    """Отказ клиенту приёма публикаций или боту(401/403/409/413/415/422) или сбой сервера при приёме(500) →
    collectors[code].state.rejects = {count, lastAt, lastCode, lastDetail}. Пояснение маскируется и обрезается до 200
    символов, токен не пишется. Не чаще одной записи в 5 с на(подключение, код ответа) - count приблизительный;
    принятая публикация снимает это ограничение(reset_reject_throttle). Никогда не бросает."""
    try:
        key = (code, int(status))
        mono = time.monotonic()
        with _REJECT_LOCK:
            last = _REJECT_AT.get(key)
            if last is not None and mono - last < REJECT_THROTTLE_S:
                return
            _REJECT_AT[key] = mono
        with SessionLocal() as db:
            r = row(db, code, create=True, lock=True)  # state дополняется: не затереть параллельную запись
            state = dict(r.state or {})
            prev = dict(state.get("rejects") or {})
            state["rejects"] = {"count": int(prev.get("count") or 0) + 1, "lastAt": models.utcnow().isoformat(),
                                "lastCode": int(status), "lastDetail": (redact(str(detail or ""), limit=200) or "")}
            r.state = state
            db.commit()
    except Exception:
        log.warning("Отказ клиенту (%s, HTTP %s) не записан в состояние подключения", code, status, exc_info=True)


def reset_reject_throttle(code: str) -> None:
    """Принятая заявка/публикация: следующий отказ этому клиенту записывается сразу(иначе отказ в течение 5 с после
    успеха не попал бы в состояние, и статус показывал бы "принимает", хотя последняя попытка отклонена)."""
    with _REJECT_LOCK:
        for key in [k for k in _REJECT_AT if k[0] == code]:
            del _REJECT_AT[key]


def last_reject(r: models.Collector | None) -> dict | None:
    """Последний записанный отказ: {"at": datetime, "code": int, "detail": str} или None."""
    rej = ((r.state or {}).get("rejects") if r is not None else None) or {}
    at = rej.get("lastAt")
    if not at:
        return None
    try:
        when = models.as_utc(datetime.fromisoformat(str(at)))
    except ValueError:
        return None
    return {"at": when, "code": int(rej.get("lastCode") or 0), "detail": str(rej.get("lastDetail") or "")}
