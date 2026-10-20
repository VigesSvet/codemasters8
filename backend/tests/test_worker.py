"""Задания, воркер, итоги запусков, планировщик и анализ публикаций(integration-spec §8, §9, §16.2 test_worker).

Подключения здесь - подменные(Fake…) в реестре под кодами настоящих: итог запуска определяет фундамент, а не модуль
подключения. Сеть не используется: файлы - из хранилища или через http_mock.
"""
import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from conftest import mp4_bytes, png_bytes

from app import analysis, db as app_db, ingest, jobs, media, models, video, worker
from app.ads_service import BOT
from app.db import SessionLocal
from app.integrations import registry, service
from app.integrations.base import (AccessBlocked, AuthExpired, BadConfig, CheckResult, Connector, Deferred, MediaIn,
                                   Publication, RateLimited, SourceError)

AUTHOR = "Омарова Ж."
APIFY = "fake-apify-token-0123456789abcd"
META = "fake-meta-token-0123456789abcdef"
SECRET_IN_ERROR = "t-secret-in-error-0123456789abcdef"


# ---------- подменные подключения ----------

class Fake(Connector):
    code = "krisha"
    kind = "collector"
    platform = "krisha"
    group = "collectors"
    name = "Krisha.kz"
    via = "тестовый сборщик"
    config_fields = ("searchUrls", "cardUrls")
    default_interval_min = 360
    interval_bounds = (120, 10080)

    def __init__(self, collect=None, check=None, **attrs):
        self._collect, self._check = collect, check
        for k, v in attrs.items():
            setattr(self, k, v)

    def check(self, ctx):
        return self._check(ctx) if self._check else CheckResult(True, "Подключено (тест)", {"account": "тест"})

    def collect(self, ctx):
        if self._collect:
            self._collect(ctx)


class PaidFake(Fake):
    code = "tiktok"
    platform = "tiktok"
    name = "TikTok"
    required_env = ("APIFY_TOKEN",)
    config_fields = ("profiles",)
    paid = True
    max_attempts = 3
    default_interval_min = 1440
    interval_bounds = (360, 10080)


class IgFake(Fake):
    code = "instagram"
    platform = "instagram"
    name = "Instagram"
    required_env = ("META_USER_ACCESS_TOKEN",)
    config_fields = ("accounts",)
    health_check_min = 1440
    default_interval_min = 60
    interval_bounds = (30, 1440)


@pytest.fixture
def use(monkeypatch):
    def put(fake: Connector) -> Connector:
        monkeypatch.setitem(registry.CONNECTORS, fake.code, fake)
        return fake
    return put


# ---------- помощники ----------

def pub(n, **kw) -> Publication:
    data = {"platform": "krisha", "external_id": str(n), "account": "krisha.kz / застройщик/ЖК",
            "url": f"https://krisha.kz/a/show/{n}", "text": f"Квартира №{n}, сдача в 2027 году",
            "collected_at": models.utcnow()}
    return Publication(**{**data, **kw})


def request(code: str = "krisha", trigger: str = "manual") -> int:
    with SessionLocal() as db:
        run, _ = service.request_run(db, code, trigger=trigger, who=AUTHOR)
        db.commit()
        return run.id


def get(model, key):
    with SessionLocal() as db:
        return db.get(model, key)


def row(code: str = "krisha") -> models.Collector:
    with SessionLocal() as db:
        return service.row(db, code)


def set_row(code: str = "krisha", **values) -> None:
    with SessionLocal() as db:
        r = service.row(db, code, create=True)
        for k, v in values.items():
            setattr(r, k, v)
        db.commit()


def events(*where) -> list[str]:
    E = models.AuditEvent
    with SessionLocal() as db:
        return list(db.scalars(select(E.what).where(*where).order_by(E.id)))


def source_events() -> list[str]:
    """События подключений и сборов в журнале(без исходного журнала демо-набора)."""
    E = models.AuditEvent
    return events(E.kind == "data", E.what.like("Источник «%") | E.what.like("Сбор «%"))


def skipped(out: dict) -> dict:
    return {x["code"]: x["reason"] for x in out["skipped"]}


def job_of(run_id: int) -> models.Job:
    with SessionLocal() as db:
        return db.scalar(select(models.Job).where(models.Job.run_id == run_id).order_by(models.Job.id.desc()))


def count(model, *where) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def take(p: Publication, collector: str = "intake", **kw) -> ingest.IngestResult:
    with SessionLocal() as db:
        res = ingest.ingest_publication(db, p, collector=collector, note="тест", **kw)
        db.commit()
        return res


def stored(color, key: str, **kw) -> MediaIn:
    return MediaIn(key=key, stored=media.store_bytes(png_bytes(color)), status="downloaded", **kw)


def sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


# ---------- очередь ----------

def test_enqueue_dedupe_and_partial_index(client):
    with SessionLocal() as db:
        a, created = jobs.enqueue(db, "collect", dedupe_key="collect:krisha", collector="krisha")
        b, again = jobs.enqueue(db, "collect", dedupe_key="collect:krisha", collector="krisha")
        assert created and not again and a.id == b.id
        db.commit()
    with SessionLocal() as db:  # второй активный с тем же ключом - запрещён индексом
        db.add(models.Job(kind="collect", status="queued", dedupe_key="collect:krisha", run_after=models.utcnow()))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.execute(update(models.Job).values(status="done"))
        db.add(models.Job(kind="collect", status="queued", dedupe_key="collect:krisha", run_after=models.utcnow()))
        db.commit()  # завершённый ключ не держит
    with SessionLocal() as db:
        j, _ = jobs.enqueue(db, "analyze", ad_id="x" * 32, dedupe_key="analyze:" + "x" * 32, content_version=1)
        same, created = jobs.enqueue(db, "analyze", ad_id="x" * 32, dedupe_key="analyze:" + "x" * 32,
                                     content_version=3)
        assert not created and same.id == j.id and same.content_version == 3
        db.commit()


def test_analyze_claim_releases_dedupe_and_one_per_ad(client):
    ad = "a" * 32
    with SessionLocal() as db:
        first, _ = jobs.enqueue(db, "analyze", ad_id=ad, dedupe_key=f"analyze:{ad}", content_version=1)
        db.commit()
    now = models.utcnow() + timedelta(seconds=1)
    with SessionLocal() as db:
        got = jobs.claim(db, "w1", now)
        assert got.id == first.id and got.dedupe_key is None and got.attempts == 1 and got.locked_by == "w1"
    with SessionLocal() as db:  # новая версия, пока идёт анализ, - новое задание
        second, created = jobs.enqueue(db, "analyze", ad_id=ad, dedupe_key=f"analyze:{ad}", content_version=2)
        other, _ = jobs.enqueue(db, "analyze", ad_id="b" * 32, dedupe_key="analyze:" + "b" * 32)
        db.commit()
    assert created and second.id != first.id
    with SessionLocal() as db:  # второй анализ той же публикации не захватывается, другой публикации - да
        assert jobs.claim(db, "w2", now).id == other.id
        assert jobs.claim(db, "w2", now) is None
        jobs.complete(db, db.get(models.Job, first.id))
        db.commit()
        assert jobs.claim(db, "w2", now).id == second.id


def test_claim_once_and_race(client):
    now = models.utcnow() + timedelta(seconds=1)
    with SessionLocal() as db:
        late, _ = jobs.enqueue(db, "collect", collector="krisha", run_after=now + timedelta(hours=1))
        low, _ = jobs.enqueue(db, "analyze", ad_id="c" * 32)
        high, _ = jobs.enqueue(db, "test", collector="krisha")
        db.commit()
    with SessionLocal() as db:
        assert [jobs.claim(db, "w1", now).id for _ in range(2)] == [high.id, low.id]  # приоритет, затем срок
        assert jobs.claim(db, "w1", now) is None  # отложенное - не раньше срока
    with SessionLocal() as db:
        db.execute(update(models.Job).where(models.Job.id == late.id).values(run_after=models.as_utc(now)))
        db.commit()
    a, b = SessionLocal(), SessionLocal()
    try:
        assert b.scalar(select(models.Job.id).where(models.Job.status == "queued")) == late.id  # оба видят задание
        assert jobs.claim(b, "w2", now).id == late.id
        assert jobs._try_claim(a, late.id, "w1", now) is False  # условный UPDATE проиграл
        a.rollback()
        assert jobs.claim(a, "w1", now) is None
    finally:
        a.close()
        b.close()
    assert get(models.Job, late.id).locked_by == "w2"


def test_retry_backoff_defer_and_recovery(client):
    now = models.utcnow()
    with SessionLocal() as db:
        job, _ = jobs.enqueue(db, "collect", collector="krisha", max_attempts=3)
        db.commit()
        for attempts, base_s in ((1, 60), (2, 120), (3, 240), (9, 3600)):
            for _ in range(5):
                job.attempts, job.max_attempts = attempts, 10
                assert jobs.retry(db, job, "сеть", now=now)
                pause = (models.as_utc(job.run_after) - now).total_seconds()
                assert 0.8 * base_s <= pause <= 1.2 * base_s, (attempts, pause)
        job.attempts, job.max_attempts = 3, 3
        assert jobs.retry(db, job, "сеть", now=now) is False  # попытки исчерпаны
        job.status, job.attempts, job.payload = "running", 2, {"phase": "poll"}
        assert jobs.defer(db, job, 15, {"apifyRunId": "r1"}, now=now)
        assert (job.status, job.attempts, job.payload) == ("queued", 1, {"phase": "poll", "apifyRunId": "r1",
                                                                          "deferrals": 1})
        assert abs((models.as_utc(job.run_after) - now).total_seconds() - 15) < 1
        job.payload = {"deferrals": jobs.MAX_DEFERRALS}
        assert jobs.defer(db, job, 15, None, now=now) is False
        db.commit()


def test_recover_stale_and_requeue_own(client):
    now = models.utcnow()
    old = now - timedelta(hours=2)
    with SessionLocal() as db:
        run = models.CollectionRun(collector="krisha", trigger="manual", status="running", created_at=old, notes=[])
        db.add(run)
        db.flush()
        stale = models.Job(kind="collect", status="running", collector="krisha", attempts=1, max_attempts=3,
                           locked_by="w9", locked_at=old, run_after=old)
        dead = models.Job(kind="collect", status="running", collector="krisha", run_id=run.id, attempts=3,
                          max_attempts=3, locked_by="w9", locked_at=old, run_after=old)
        fresh = models.Job(kind="collect", status="running", collector="krisha", attempts=1, max_attempts=3,
                           locked_by="w8", locked_at=now, run_after=old)
        own = models.Job(kind="test", status="running", collector="krisha", attempts=1, max_attempts=3,
                         locked_by="w8", locked_at=now, run_after=old)
        db.add_all([stale, dead, fresh, own])
        db.commit()
        assert jobs.recover_stale(db, now, 1800) == 2
        db.commit()
        assert (stale.status, stale.locked_by, dead.status) == ("queued", None, "failed")
        assert db.get(models.CollectionRun, run.id).status == "failed" and fresh.status == "running"
        assert jobs.requeue_own(db, "w8") == 2
        db.commit()
        assert (fresh.status, own.status) == ("queued", "queued")


def test_heartbeat_thread_refreshes_lease_and_lock(client, monkeypatch, use):
    monkeypatch.setattr(worker, "HEARTBEAT_S", 0.02)
    seen = {}

    def slow(ctx):
        t0 = time.monotonic()
        with SessionLocal() as db:
            start = models.as_utc(db.get(models.Job, ctx.job_id).locked_at)
        while time.monotonic() - t0 < 2:
            time.sleep(0.03)
            with SessionLocal() as db:
                locked = models.as_utc(db.get(models.Job, ctx.job_id).locked_at)
                info = service.worker_info(db)
            if locked > start and info["alive"]:
                seen.update(alive=info["alive"], id=info["id"], refreshed=True)
                return

    use(Fake(collect=slow))
    request()
    cur, stop = worker.Current(), threading.Event()
    t = worker.start_heartbeat("hb-1", cur, stop)
    try:
        assert worker.run_once(worker_id="hb-1", current=cur) >= 1
    finally:
        stop.set()
        t.join(2)
    assert seen == {"alive": True, "id": "hb-1", "refreshed": True}
    lease = get(models.WorkerLease, "worker:hb-1")
    assert lease.info["id"] == "hb-1" and lease.info["pid"] and lease.info["version"] == models.SCHEMA_VERSION


def test_heartbeat_stops_extending_after_job_max(client, env, caplog):
    env(SAQ_JOB_MAX_S="60")
    old = models.utcnow() - timedelta(hours=1)
    with SessionLocal() as db:
        job = models.Job(kind="collect", status="running", collector="krisha", attempts=1, locked_by="w5",
                         locked_at=old, run_after=old)
        db.add(job)
        db.commit()
    cur = worker.Current()
    cur.set(job.id)
    cur.started = time.monotonic() - 61
    worker.beat("w5", cur)
    worker.beat("w5", cur)
    assert models.as_utc(get(models.Job, job.id).locked_at) == old
    assert caplog.text.count(f"Задание №{job.id} выполняется дольше 60 с") == 1


# ---------- итоги запусков(§9.4) ----------

def test_collect_success_working_and_summary(client, run_worker, use):
    use(Fake(collect=lambda ctx: [ctx.ingest(pub(n)) for n in (1, 2)]))
    run_id = request()
    run_worker()
    run = get(models.CollectionRun, run_id)
    assert (run.status, run.items_seen, run.items_new, run.started_at is not None) == ("succeeded", 2, 2, True)
    r = row()
    assert (r.status, r.facts.get("partial"), r.failures, r.last_success_at is not None) == ("working", False, 0, True)
    assert r.next_run_at is not None
    assert source_events() == [f"Сбор «Krisha.kz»: новых 2, изменено 0, без изменений 0 (запуск №{run_id})"]
    assert get(models.Job, job_of(run_id).id).status == "done"
    # повтор без изменений - без записи в журнал
    second = request()
    run_worker()
    assert get(models.CollectionRun, second).items_unchanged == 2
    assert len(source_events()) == 1


def test_check_failed_is_error(client, run_worker, use):
    use(Fake(check=lambda ctx: CheckResult(False, "Выдача пустая — ответ сайта не распознан")))
    run_id = request(trigger="test")
    run_worker()
    run = get(models.CollectionRun, run_id)
    assert (run.status, run.error_code, run.error) == ("failed", "source_error",
                                                       "Выдача пустая — ответ сайта не распознан")
    r = row()
    assert (r.status, r.status_detail, r.checked_at is not None) == ("error", "Выдача пустая — ответ сайта не распознан",
                                                                     True)
    assert source_events() == ["Источник «Krisha.kz»: ошибка. Выдача пустая — ответ сайта не распознан"]


def test_all_items_failed_is_error(client, run_worker, use, accept):
    use(Fake(collect=lambda ctx: [ctx.item_failed(f"запись {i}: ошибка разбора") for i in range(3)]))
    run_id = request()
    run_worker()
    run = get(models.CollectionRun, run_id)
    assert (run.status, run.items_seen, run.items_failed) == ("failed", 3, 3)
    assert row().status_detail == "Все 3 записей с ошибкой: запись 0: ошибка разбора"
    c = accept.assert_failure_visible("krisha", run_id)
    assert c["status"] == "error" and c["statusDetail"].startswith("Все 3 записей с ошибкой")


def test_partial_run(client, run_worker, use):
    def collect(ctx):
        ctx.ingest(pub(1))
        ctx.item_failed("запись 2: нет цены")
        ctx.mark_partial("Время запуска истекло — продолжим в следующий раз")

    use(Fake(collect=collect))
    run_id = request()
    run_worker()
    run = get(models.CollectionRun, run_id)
    assert run.status == "partial" and [n["level"] for n in run.notes] == ["warn", "warn"]
    r = row()
    assert r.status == "partial" and r.facts["partial"] is True
    assert "Время запуска истекло" in r.status_detail and "записей с ошибкой: 1 из 2" in r.status_detail
    assert source_events() == [f"Сбор «Krisha.kz»: новых 1, изменено 0, без изменений 0, ошибок 1 (запуск №{run_id})"]


def test_postpone_never_shortens_interval(client, run_worker, use):
    """postpone() только откладывает: пауза короче интервала не делает следующий запуск раньше расписания, длиннее -
    откладывает его."""
    def short(ctx):
        ctx.ingest(pub(1))
        ctx.postpone(600, "Пауза: лимит запросов почти исчерпан")

    use(Fake(collect=short))
    set_row(interval_min=720)
    for expected in (720 * 60, 48 * 3600):
        request()
        now = models.utcnow() + timedelta(seconds=1)
        run_worker(now=now)
        assert abs((models.as_utc(row().next_run_at) - now).total_seconds() - expected) < 5
        use(Fake(collect=lambda ctx: ctx.postpone(48 * 3600, "Пауза до послезавтра")))


def test_rate_limited(client, run_worker, use):
    def collect(ctx):
        raise RateLimited(120)

    use(Fake(collect=collect))
    run_id = request()
    now = models.utcnow()
    run_worker(now=now)
    run, r = get(models.CollectionRun, run_id), row()
    assert (run.status, run.error_code, job_of(run_id).status) == ("rate_limited", "rate_limited", "done")
    assert r.status == "rate_limited" and abs((models.as_utc(r.next_run_at) - now).total_seconds() - 120) < 2
    assert r.status_detail.startswith("Ограничение частоты: повтор после ")
    # пауза: ни расписание, ни "Обновить всё" не запускают сбор до next_run_at
    set_row(enabled=True)
    with SessionLocal() as db:
        assert worker.schedule_due(db, now + timedelta(seconds=60)) == []
        out = service.collect_all(db, AUTHOR, now + timedelta(seconds=60))
        db.rollback()
    assert out["queued"] == [] and re.fullmatch(r"пауза до \d\d:\d\d", skipped(out)["krisha"])
    with SessionLocal() as db:
        assert len(worker.schedule_due(db, now + timedelta(seconds=200))) == 1
        db.rollback()


def test_auth_expired_audit_once(client, run_worker, use):
    def check(ctx):
        raise AuthExpired("Токен недействителен — нужна повторная авторизация")

    use(Fake(check=check))
    for _ in range(2):
        run_id = request(trigger="test")
        run_worker()
    run = get(models.CollectionRun, run_id)
    assert (run.status, run.error_code, row().status) == ("failed", "auth_expired", "expired")
    assert source_events() == ["Источник «Krisha.kz»: истёк доступ. Токен недействителен — нужна повторная авторизация"]


def test_access_blocked_not_rescheduled(client, run_worker, use, accept):
    def collect(ctx):
        raise AccessBlocked("Krisha.kz отвечает проверкой доступа (HTTP 468) — автоматический сбор остановлен, нужен "
                            "ручной шаг; обход проверок не выполняется")

    use(Fake(collect=collect))
    set_row(enabled=True, status="ready")
    now = models.utcnow()
    run_worker(now=now)  # планировщик сам ставит сбор
    r = row()
    assert r.status == "access_check" and r.last_run_id
    assert get(models.CollectionRun, r.last_run_id).status == "access_check"
    accept.assert_failure_visible("krisha", r.last_run_id)
    with SessionLocal() as db:
        assert worker.schedule_due(db, now + timedelta(days=2)) == []
        assert skipped(service.collect_all(db, AUTHOR, now))["krisha"] == "нужно ручное действие"
        db.rollback()


def test_bad_config_stops_until_config_patch(client, run_worker, use, admin_headers):
    def collect(ctx):
        raise BadConfig("Добавьте URL поиска Krisha")

    use(Fake(collect=collect))
    run_id = request()
    run_worker()
    r = row()
    assert (r.status, r.facts["stopped"], r.facts["stoppedCode"]) == ("error", True, "bad_config")
    assert get(models.CollectionRun, run_id).error_code == "bad_config"
    with SessionLocal() as db:
        set_row(enabled=True)
        assert worker.schedule_due(db, models.utcnow() + timedelta(days=3)) == []
        db.rollback()
    resp = client.patch("/api/sources/krisha", headers=admin_headers,
                        json={"author": AUTHOR, "config": {"searchUrls": ["https://krisha.kz/prodazha/kvartiry/"]}})
    assert resp.status_code == 200, resp.text
    assert resp.json()["stopped"] is False and "stopped" not in row().facts


def test_retryable_then_error(client, run_worker, use):
    calls = []

    def collect(ctx):
        calls.append(1)
        raise SourceError("Сайт не ответил (HTTP 503)", retryable=True)

    use(Fake(collect=collect, max_attempts=2))
    run_id = request()
    now = models.utcnow()
    run_worker(now=now)
    run, job = get(models.CollectionRun, run_id), job_of(run_id)
    assert (run.status, job.status, job.attempts) == ("running", "queued", 1)
    assert re.search(r"повтор 2 из 2 после \d\d:\d\d", run.notes[-1]["text"])
    assert client.get(f"/api/collection-runs/{run_id}").json()["retryAt"]
    run_worker(now=now + timedelta(hours=2))
    run, r = get(models.CollectionRun, run_id), row()
    assert (len(calls), run.status, job_of(run_id).status, r.status, r.failures) == (2, "failed", "failed", "error", 1)
    assert models.as_utc(r.next_run_at) > now + timedelta(hours=2)  # пауза после ошибки


def test_unexpected_exception_internal_error(client, run_worker, use, env, caplog):
    env(SAQ_ADMIN_TOKEN=SECRET_IN_ERROR)

    def collect(ctx):
        raise RuntimeError(f"сбой разбора, токен {SECRET_IN_ERROR}")

    use(Fake(collect=collect, max_attempts=1))
    run_id = request()
    with caplog.at_level(logging.ERROR):
        run_worker()
    run = get(models.CollectionRun, run_id)
    assert (run.status, run.error_code, run.error) == ("failed", "internal", jobs.INTERNAL)
    assert row().status == "error" and "Traceback" in caplog.text and "RuntimeError" in caplog.text
    assert SECRET_IN_ERROR not in caplog.text and SECRET_IN_ERROR not in (job_of(run_id).last_error or "")


def test_not_configured_run(client, run_worker, use, env):
    env(APIFY_TOKEN=APIFY)
    use(PaidFake())
    run_id = request("tiktok")
    env(APIFY_TOKEN=None)  # ключ убрали, пока задание ждало в очереди
    run_worker()
    run = get(models.CollectionRun, run_id)
    assert (run.status, run.error_code, run.error) == ("not_configured", "not_configured", "Нет на сервере: APIFY_TOKEN")
    assert client.get("/api/sources/connectors/tiktok").json()["status"] == "not_configured"
    env(APIFY_TOKEN=APIFY)  # ключ вернули - устаревшего "не настроен" нет
    c = client.get("/api/sources/connectors/tiktok").json()
    assert (c["status"], c["statusDetail"]) == ("unchecked", None)


def test_paid_stop_and_admin_test_clears(client, run_worker, use, env, admin_headers):
    env(APIFY_TOKEN=APIFY)
    posts = []

    def collect(ctx):
        posts.append(1)
        raise SourceError("Apify: нужна оплата/лимит аккаунта", retryable=False)

    use(PaidFake(collect=collect))
    set_row("tiktok", enabled=True, status="ready")
    now = models.utcnow()
    run_worker(now=now)
    r = row("tiktok")
    assert len(posts) == 1 and r.status == "error" and r.facts["stopped"] is True
    assert r.status_detail.endswith(jobs.PAID_STOP)
    with SessionLocal() as db:  # следующий такт - ни запуска, ни платного запроса
        assert worker.schedule_due(db, now + timedelta(days=5)) == []
        out = service.collect_all(db, AUTHOR, now)
        db.rollback()
    assert skipped(out)["tiktok"] == "остановлен после ошибки — запустите проверку"
    run_worker(now=now + timedelta(days=5))
    assert len(posts) == 1
    resp = client.post("/api/sources/tiktok/test", headers=admin_headers, json={"author": AUTHOR})
    assert resp.status_code == 202, resp.text
    run_worker()
    r = row("tiktok")
    assert r.status == "ready" and "stopped" not in r.facts and r.facts["keyFp"]["APIFY_TOKEN"]
    with SessionLocal() as db:
        assert len(worker.schedule_due(db, now + timedelta(days=5))) == 1
        db.rollback()


# ---------- планировщик(§9.5) ----------

@pytest.mark.parametrize("values", [
    {"enabled": False, "status": "ready"}, {"enabled": True, "status": "expired"},
    {"enabled": True, "status": "access_check"}, {"enabled": True, "status": "unchecked"},
    {"enabled": True, "status": "denied"}, {"enabled": True, "status": "ready", "facts": {"stopped": True}}])
def test_scheduler_skips(client, use, values):
    use(Fake())
    set_row(**values)
    with SessionLocal() as db:
        assert worker.schedule_due(db, models.utcnow()) == []


def test_scheduler_due_once(client, use):
    use(Fake())
    now = models.utcnow()
    set_row(enabled=True, status="working", next_run_at=now - timedelta(minutes=1))
    with SessionLocal() as db:
        created = worker.schedule_due(db, now)
        db.commit()
        assert len(created) == 1 and worker.schedule_due(db, now) == []
        run = db.get(models.CollectionRun, created[0])
        assert (run.trigger, run.requested_by) == ("schedule", "Система")
    set_row(next_run_at=now + timedelta(hours=1))
    with SessionLocal() as db:
        db.execute(update(models.Job).values(status="done"))
        db.commit()
        assert worker.schedule_due(db, now) == []  # срок ещё не наступил


def test_health_check_and_lease(client, use, env):
    env(META_USER_ACCESS_TOKEN=META)
    use(IgFake())
    now = models.utcnow()
    set_row("instagram", enabled=True, status="working", checked_at=now - timedelta(hours=25),
            next_run_at=now + timedelta(hours=1))
    with SessionLocal() as db:
        created = worker.schedule_due(db, now)
        db.commit()
        assert len(created) == 1 and db.get(models.CollectionRun, created[0]).trigger == "test"
        assert worker.schedule_due(db, now) == []
    set_row("instagram", checked_at=now - timedelta(hours=2))
    with SessionLocal() as db:
        db.execute(update(models.Job).values(status="done"))
        db.commit()
        assert worker.schedule_due(db, now) == []
        assert worker.acquire_lease(db, "scheduler", "w1", 60) is True
        assert worker.acquire_lease(db, "scheduler", "w2", 60) is False
        assert worker.acquire_lease(db, "scheduler", "w1", 60) is True  # продление своей
        assert worker.acquire_lease(db, "scheduler", "w2", 60, now=now + timedelta(minutes=2)) is True  # истекла
        assert db.get(models.WorkerLease, "scheduler").holder == "w2"


def test_spent_usd_timezone_and_unknown_cost(client, run_ctx, env):
    env(APIFY_TOKEN=APIFY)
    since = datetime(2026, 1, 10, 12, 0, tzinfo=timezone(timedelta(hours=5)))  # = 07:00 UTC
    with SessionLocal() as db:
        for created, cost, ext in ((datetime(2026, 1, 10, 7, 30), 0.25, "r1"), (datetime(2026, 1, 10, 8, 0), None, "r2"),
                                   (datetime(2026, 1, 10, 6, 59), 5.0, "r0"), (datetime(2026, 1, 10, 9, 0), None, None)):
            db.add(models.CollectionRun(collector="tiktok", trigger="schedule", status="succeeded", cost_usd=cost,
                                        external_run_id=ext, created_at=created.replace(tzinfo=timezone.utc), notes=[]))
        db.commit()
    ctx = run_ctx("tiktok")
    assert ctx.spent_usd(since) == pytest.approx(0.25)
    assert ctx.spent_usd(since, unknown_cost=0.5) == pytest.approx(0.75)


def test_wait_for_schema_and_once(client):
    with SessionLocal() as db:
        db.merge(models.Meta(key="schema_version", value="2026.10-sources-1"))
        db.commit()
    try:
        assert worker.wait_for_schema(app_db.engine, poll=0.02, max_wait=0.1) is False
        assert worker.main(["--once", "--id", "t-once"]) == 0
        assert get(models.WorkerLease, "worker:t-once") is None  # схема не та - работы не было
    finally:
        with SessionLocal() as db:
            db.merge(models.Meta(key="schema_version", value=models.SCHEMA_VERSION))
            db.commit()
    assert worker.wait_for_schema(app_db.engine, max_wait=0) is True
    assert worker.main(["--once", "--id", "t-once"]) == 0
    assert get(models.WorkerLease, "worker:t-once").holder == "t-once"
    lease = get(models.WorkerLease, "scheduler")  # аренда планировщика освобождена при выходе
    assert lease.holder == "t-once" and models.as_utc(lease.expires_at) <= models.utcnow()
    with SessionLocal() as db:
        assert worker.acquire_lease(db, "scheduler", "other", 60) is True


def test_report_local_writes_on_change(client):
    with SessionLocal() as db:
        worker.report_local(db)
        rep = service.row(db, "telegram").state["report"]
    assert rep["sessionPresent"] is False and rep["checkedAt"]
    with SessionLocal() as db:
        worker.report_local(db)
        assert service.row(db, "telegram").state["report"]["checkedAt"] == rep["checkedAt"]  # без изменений - без записи


def test_no_nested_write_transactions(client, run_worker, use, env, http_mock, monkeypatch):
    """busy_timeout 1 с: сборщик "как Instagram"(загрузка файла, заметки, состояние) и "как TikTok"(фазы, внешний
    запуск, Deferred) проходят через воркер без "database is locked" - записи не вложены друг в друга."""
    monkeypatch.setattr(app_db, "BUSY_TIMEOUT_MS", 1000)
    app_db.engine.dispose()
    env(APIFY_TOKEN=APIFY)
    http_mock.on("GET", "https://cdn.example.com/a.png", content=png_bytes((40, 41, 42)),
                 headers={"Content-Type": "image/png"})

    def ig(ctx):
        ctx.note("Аккаунт @tumar.group.astana")
        p = pub(7, platform="krisha", media=[MediaIn(key="krisha:7:0", fetch=lambda: ctx.download_media(
            "https://cdn.example.com/a.png", key="krisha:7:0", allowed_hosts=("cdn.example.com",)))])
        assert ctx.ingest(p).status == "created"
        ctx.save_state(seeded=True)

    def tt(ctx):
        if ctx.payload.get("phase") != "dataset":
            ctx.set_external_run("apify-run-1")
            ctx.save_payload(phase="dataset", offset=0)
            raise Deferred(30)
        ctx.ingest(pub(8, platform="tiktok", url="https://www.tiktok.com/@x/video/8"))
        ctx.set_external_run("apify-run-1", cost_usd=0.012)

    use(Fake(collect=ig))
    use(PaidFake(collect=tt))
    ig_run, tt_run = request("krisha"), request("tiktok")
    now = models.utcnow()
    run_worker(now=now)
    assert job_of(tt_run).status == "queued" and job_of(tt_run).attempts == 0
    run_worker(now=now + timedelta(minutes=1))
    assert get(models.CollectionRun, ig_run).status == "succeeded" and row("krisha").state["seeded"] is True
    tt = get(models.CollectionRun, tt_run)
    assert (tt.status, tt.external_run_id, tt.cost_usd, tt.items_new) == ("succeeded", "apify-run-1", 0.012, 1)
    assert job_of(tt_run).attempts == 1  # отсрочка попытку не расходует
    assert count(models.MediaAsset, models.MediaAsset.status == "downloaded") == 1


def test_key_change_marks_unchecked(client, run_worker, use, env):
    env(META_USER_ACCESS_TOKEN=META)
    use(IgFake())
    request("instagram", trigger="test")
    run_worker()
    assert row("instagram").facts["keyFp"]["META_USER_ACCESS_TOKEN"]
    c = client.get("/api/sources/connectors/instagram").json()
    assert (c["status"], c["statusLabel"]) == ("ready", "готов к сбору")
    env(META_USER_ACCESS_TOKEN=META + "-new")
    c = client.get("/api/sources/connectors/instagram").json()
    assert (c["status"], c["statusDetail"]) == ("unchecked", service.KEYS_CHANGED)
    assert META not in client.get("/api/sources/connectors").text


# ---------- анализ публикаций(§8) ----------

TEXT = "Старт продаж в ЖК Ульба Ривер! Бронь квартиры за 100 000 ₸, рассрочка до сдачи дома"


def ig_pub(n=1, **kw) -> Publication:
    data = {"platform": "instagram", "external_id": f"1790000000000000{n}", "account": "@someone.kz",
            "url": f"https://www.instagram.com/p/X{n}/", "text": TEXT, "collected_at": models.utcnow()}
    return Publication(**{**data, **kw})


def ad_events(ad_id: str, kind=None) -> list[str]:
    E = models.AuditEvent
    where = [E.ad_id == ad_id] + ([E.kind == kind] if kind else [])
    return events(*where)


def test_analysis_ocr_order_events_no_link(client, run_worker, ocr_stub):
    a, b = png_bytes((10, 0, 0)), png_bytes((20, 0, 0))
    res = take(ig_pub(media=[MediaIn(key="ig:1", stored=media.store_bytes(a), status="downloaded"),
                             MediaIn(key="ig:2", stored=media.store_bytes(b), status="downloaded")]))
    run_worker()
    with SessionLocal() as db:
        ad = db.get(models.Ad, res.ad_id)
        item = db.get(models.IngestItem, res.item_id)
        assert ad.ocr == f"OCR {sha8(a)}\n\nOCR {sha8(b)}"
        assert (ad.complex_id, ad.link_confirmed_by, ad.status) == (None, None, "new")
        assert (item.analyzed_version, item.analysis_status, item.analysis_stage, item.analyzed_engine) == (
            1, "done", None, "saq-ai-1")
        job = db.scalars(select(models.Job).where(models.Job.ad_id == ad.id)).one()
        assert job.status == "done" and job.result["version"] == 1 and job.result["autolinked"] is False
    assert "OCR: распознан текст (2 изображения)" in ad_events(res.ad_id, "system")
    ai_events = ad_events(res.ad_id, "ai")
    assert ai_events[0].startswith("Гипотеза ИИ: ЖК «Ульба Ривер», уверенность ")
    assert ai_events[0].endswith(". Привязка не назначена автоматически: публикация собрана из внешнего источника")
    assert ai_events[1].startswith("ИИ: найдены условия оплаты — бронь") and "рассрочка" in ai_events[1]
    assert client.get(f"/api/ads/{res.ad_id}").json()["processing"]["status"] == "done"
    # повтор задания по той же версии - ничего нового
    with SessionLocal() as db:
        ingest.enqueue_analysis(db, res.ad_id)
        db.commit()
    n = len(ad_events(res.ad_id))
    run_worker()
    assert len(ad_events(res.ad_id)) == n and get(models.IngestItem, res.item_id).analysis_status == "done"


def test_analysis_autolink_opt_in(client, run_worker, ocr_stub, env):
    env(SAQ_COLLECTED_AUTOLINK="1")
    res = take(ig_pub())
    confirmed = take(ig_pub(2))
    rejected = take(ig_pub(3))
    with SessionLocal() as db:  # решения аналитика до анализа не трогаются
        db.execute(update(models.Ad).where(models.Ad.id == confirmed.ad_id)
                   .values(complex_id="c1", link_confirmed_by=AUTHOR, match_confidence=1.0))
        db.execute(update(models.Ad).where(models.Ad.id == rejected.ad_id).values(rejected_links=["c3"]))
        db.commit()
    run_worker()
    with SessionLocal() as db:
        ad = db.get(models.Ad, res.ad_id)
        assert (ad.complex_id, ad.link_confirmed_by) == ("c3", None) and ad.match_confidence >= 0.5
        assert ad.decision_rev == 1
        c = db.get(models.Ad, confirmed.ad_id)
        assert (c.complex_id, c.link_confirmed_by, c.match_confidence) == ("c1", AUTHOR, 1.0)
        assert db.get(models.Ad, rejected.ad_id).complex_id != "c3"
    first = ad_events(res.ad_id, "ai")[0]
    assert first.startswith("Гипотеза ИИ: ЖК «Ульба Ривер»") and "Привязка не назначена" not in first


def test_analysis_ocr_unavailable_and_empty(client, run_worker, monkeypatch):
    monkeypatch.setattr(video, "ffmpeg_available", lambda: False)
    monkeypatch.setattr(analysis.ocr_engine, "recognize", lambda raw, ct=None: {
        "text": "", "engine": "none", "detail": "Tesseract не установлен на сервере"})
    res = take(ig_pub(media=[stored((1, 2, 3), "ig:1")]))
    run_worker()
    assert "OCR не выполнен: Tesseract не установлен на сервере" in ad_events(res.ad_id, "system")
    monkeypatch.setattr(analysis.ocr_engine, "recognize", lambda raw, ct=None: {
        "text": "", "engine": "tesseract", "detail": "Текст на изображении не найден"})
    other = take(ig_pub(2, media=[stored((3, 2, 1), "ig:2")]))
    run_worker()
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == other.ad_id)).one()
        assert (a.ocr_status, a.ocr_detail) == ("empty", "Текст на изображении не найден")
    assert not [e for e in ad_events(other.ad_id) if e.startswith("OCR")]
    monkeypatch.setattr(analysis.ocr_engine, "recognize", lambda raw, ct=None: {
        "text": "", "engine": "tesseract", "detail": "Распознавание не уложилось в 60 с — уменьшите скриншот"})
    third = take(ig_pub(3, media=[stored((9, 9, 1), "ig:3")]))
    run_worker()
    with SessionLocal() as db:
        assert db.scalars(select(models.MediaAsset.ocr_status)
                          .where(models.MediaAsset.ad_id == third.ad_id)).one() == "failed"


def test_analysis_failures_not_silent(client, run_worker, monkeypatch):
    """Регрессия: сбой OCR на всех изображениях и не загрузившееся изображение выглядели в журнале публикации как
    "текста нет" / "картинок нет"."""
    monkeypatch.setattr(video, "ffmpeg_available", lambda: False)
    good = png_bytes((7, 7, 70))

    def recognize(raw, ct=None):
        if raw == good:
            return {"text": "Бронь 100 000 ₸", "engine": "tesseract"}
        return {"text": "", "engine": "tesseract", "detail": "Tesseract завершился с ошибкой (код 1)"}

    monkeypatch.setattr(analysis.ocr_engine, "recognize", recognize)
    res = take(ig_pub(media=[stored((5, 6, 7), "ig:1")]))
    run_worker()
    assert "OCR не выполнен: Tesseract завершился с ошибкой (код 1)" in ad_events(res.ad_id, "system")
    part = take(ig_pub(2, media=[MediaIn(key="ig:2a", stored=media.store_bytes(good), status="downloaded"),
                                 stored((5, 6, 8), "ig:2b")]))
    run_worker()
    assert ("OCR: распознан текст (1 изображение); не распознано файлов: 1 (Tesseract завершился с ошибкой (код 1))"
            in ad_events(part.ad_id, "system"))
    gone = "Источник ответил HTTP 404 (https://cdn.example.com/x.jpg)"
    bad = take(ig_pub(3, media=[MediaIn(key="ig:3", status="unavailable", error=gone)]))
    mixed = take(ig_pub(4, media=[stored((5, 6, 9), "ig:4a"), MediaIn(key="ig:4b", status="failed", error="сеть"),
                                  MediaIn(key="ig:4c", status="failed", error="сеть")]))
    run_worker()
    assert f"Изображение не загружено ({gone}) — проанализирован только текст" in ad_events(bad.ad_id, "system")
    assert ("Изображения не загружены: 2 (сеть) — проанализированы остальные файлы и текст"
            in ad_events(mixed.ad_id, "system"))


def test_analysis_video_events(client, run_worker, ocr_stub, http_mock, monkeypatch):
    http_mock.on("GET", "https://cdn.example.com/v.mp4", status=404)
    gone = take(ig_pub(media=[MediaIn(key="v", kind="video", url="https://cdn.example.com/v.mp4?sig=SECRETSIG")]))
    no_ffmpeg = take(ig_pub(2, media=[MediaIn(key="v2", stored=media.store_bytes(mp4_bytes()), status="downloaded"),
                                      stored((5, 5, 5), "cover", role="cover")]))
    run_worker()
    assert ("Видео не загружено (Источник ответил HTTP 404 (https://cdn.example.com/v.mp4)) — проанализирована только "
            "обложка/текст") in ad_events(gone.ad_id, "system")
    with SessionLocal() as db:
        v = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == gone.ad_id)).one()
        assert (v.status, v.meta.get("src")) == ("unavailable", None)
        v2 = db.scalars(select(models.MediaAsset).where(models.MediaAsset.source_key == "v2")).one()
        assert (v2.ocr_status, v2.ocr_detail) == ("unavailable", analysis.FFMPEG_MISSING)
    assert ("Кадры видео не распознаны (ffmpeg не установлен — кадры видео не извлечены) — проанализирована только "
            "обложка/текст") in ad_events(no_ffmpeg.ad_id, "system")
    assert "SECRETSIG" not in " ".join(ad_events(gone.ad_id))
    # с ffmpeg: кадры сохраняются и распознаются
    f1, f2 = png_bytes((60, 0, 0)), png_bytes((70, 0, 0))
    monkeypatch.setattr(video, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(video, "extract_frames", lambda path, mime, **kw: [(0, f1), (2000, f2)])
    framed = take(ig_pub(3, media=[MediaIn(key="v3", stored=media.store_bytes(mp4_bytes(300)),
                                           status="downloaded")]))
    run_worker()
    with SessionLocal() as db:
        frames = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == framed.ad_id,
                                                            models.MediaAsset.kind == "frame")
                            .order_by(models.MediaAsset.frame_ms)).all()
        assert [(f.frame_ms, f.ocr_status) for f in frames] == [(0, "done"), (2000, "done")]
        assert db.get(models.Ad, framed.ad_id).ocr == f"OCR {sha8(f1)}\n\nOCR {sha8(f2)}"
    assert "OCR: распознан текст (2 кадра видео)" in ad_events(framed.ad_id, "system")
    assert [m["kind"] for m in client.get(f"/api/ads/{framed.ad_id}").json()["media"]] == ["video"]


def test_analysis_bot_claims_ignored(client, run_worker, ocr_stub):
    res = take(ig_pub(account="@ulba.dev", url="https://www.instagram.com/ulba.dev/", text="Продаю квартиру, звоните",
                      namespace="tgbot", external_id="555:1"), collector="telegram_bot", actor=BOT)
    run_worker()
    first = ad_events(res.ad_id, "ai")[0]
    assert first.startswith("ИИ: ЖК не определён — нужна ручная привязка")
    assert first.endswith(". Аккаунт и ссылка указаны жителем — в гипотезе не учитывались, сверьте вручную")
    # тот же аккаунт от сборщика - признак(для сравнения)
    same = take(ig_pub(9, account="@ulba.dev", url="https://www.instagram.com/ulba.dev/",
                       text="Продаю квартиру, звоните"))
    run_worker()
    assert "Ульба Ривер" in ad_events(same.ad_id, "ai")[0]


def test_analysis_new_version_during_running(client, run_worker, ocr_stub, monkeypatch):
    res = take(ig_pub())
    real = analysis._media_stage
    calls = []

    def media_stage(item_id, *args, **kwargs):
        if not calls:  # пока идёт анализ версии 1, пришла версия 2
            take(ig_pub(text=TEXT + ". Цены снижены"))
        calls.append(item_id)
        return real(item_id, *args, **kwargs)

    monkeypatch.setattr(analysis, "_media_stage", media_stage)
    run_worker()
    with SessionLocal() as db:
        item = db.get(models.IngestItem, res.item_id)
        assert (item.version, item.analyzed_version, item.analysis_status) == (2, 2, "done")
        js = db.scalars(select(models.Job).where(models.Job.ad_id == res.ad_id).order_by(models.Job.id)).all()
        assert [(j.status, j.result["version"]) for j in js] == [("done", 1), ("done", 2)]
    hyps = [e for e in ad_events(res.ad_id, "ai") if e.startswith("Гипотеза ИИ")]
    assert len(hyps) == 2 and not hyps[0].endswith("(версия 2)") and hyps[1].endswith(" (версия 2)")


def test_analysis_failure_exhausted(client, run_worker, ocr_stub, monkeypatch):
    res = take(ig_pub())

    def boom(*a, **kw):
        raise RuntimeError("каталог недоступен")

    monkeypatch.setattr(analysis.world, "catalog", boom)
    now = models.utcnow()
    for i in range(5):
        assert run_worker(now=now + timedelta(hours=2 * i)) == 1
        status = get(models.IngestItem, res.item_id).analysis_status
        assert status == ("queued" if i < 4 else "failed")
    p = client.get(f"/api/ads/{res.ad_id}").json()["processing"]
    assert (p["status"], p["stage"], p["error"]) == ("failed", "rules", "каталог недоступен")
    with SessionLocal() as db:
        job = db.scalars(select(models.Job).where(models.Job.ad_id == res.ad_id)).one()
        assert (job.status, job.attempts, job.result) == ("failed", 5, {"failedStage": "rules"})
    assert "Анализ публикации не выполнен: каталог недоступен — проверьте вручную" in ad_events(res.ad_id, "system")


# ---------- регрессии ревью: брошенные задания, время итога, ограждение, остановка, проверки ----------

def _claim_and_crash(times: int, worker_id: str = "w-crash") -> None:
    """Воркер захватывает задание и падает(SIGKILL): при перезапуске requeue_own возвращает его в очередь."""
    for _ in range(times):
        with SessionLocal() as db:
            assert jobs.claim(db, worker_id, models.utcnow() + timedelta(seconds=1)) is not None
        with SessionLocal() as db:
            jobs.requeue_own(db, worker_id)
            db.commit()


def test_abandoned_paid_collect_stops_paid_schedule(client, use, env, accept):
    """Регрессия: задание, брошенное после исчерпания попыток(падения воркера), оставляло подключение "работает", а
    платный сбор - не остановленным: расписание сразу начинало новый платный запуск."""
    env(APIFY_TOKEN=APIFY)
    use(PaidFake())
    now = models.utcnow()
    set_row("tiktok", enabled=True, status="working", next_run_at=now - timedelta(minutes=1))
    with SessionLocal() as db:
        [run_id] = worker.schedule_due(db, now)
        db.commit()
    _claim_and_crash(3)  # max_attempts = 3: третье падение исчерпывает попытки
    run, r, job = get(models.CollectionRun, run_id), row("tiktok"), job_of(run_id)
    assert (job.status, run.status, run.error_code) == ("failed", "failed", "internal")
    assert (r.status, r.failures, r.facts.get("stopped"), r.facts.get("stoppedCode")) == ("error", 1, True, "internal")
    assert r.status_detail == f"Задание прервано перезапуском воркера. {jobs.PAID_STOP}"
    assert models.as_utc(r.next_run_at) > now
    accept.assert_failure_visible("tiktok", run_id)
    with SessionLocal() as db:
        assert worker.schedule_due(db, now + timedelta(days=3)) == []  # нового платного запуска нет
        db.rollback()
    assert "Источник «TikTok»: ошибка. Задание прервано перезапуском воркера" in source_events()[-1]


def test_abandoned_stale_collect_and_test_are_errors(client, use):
    """recover_stale на последней попытке: сбор и проверка - итог "ошибка" у подключения(не "работает")."""
    use(Fake(max_attempts=1))
    set_row(status="working")
    run_id = request()
    with SessionLocal() as db:
        job = jobs.claim(db, "w-hung", models.utcnow() + timedelta(seconds=1))
        job.locked_at = models.utcnow() - timedelta(hours=3)  # воркер завис: блокировка не продлевалась
        db.commit()
        assert jobs.recover_stale(db, models.utcnow(), 1800) == 1
        db.commit()
    r = row()
    assert (job_of(run_id).status, get(models.CollectionRun, run_id).status) == ("failed", "failed")
    assert (r.status, r.failures, r.last_error) == ("error", 1, "Задание прервано: воркер перестал отвечать")
    assert "stopped" not in (r.facts or {})  # не платный - не остановлен, только пауза
    test_run = request(trigger="test")
    _claim_and_crash(1)  # проверка: одна попытка
    assert get(models.CollectionRun, test_run).status == "failed" and row().status == "error"


def test_abandoned_analysis_writes_event(client):
    res = take(ig_pub())
    with SessionLocal() as db:
        db.execute(update(models.Job).where(models.Job.ad_id == res.ad_id).values(max_attempts=1))
        db.commit()
    _claim_and_crash(1)
    item = get(models.IngestItem, res.item_id)
    assert (item.analysis_status, item.analysis_error) == ("failed", "Задание прервано перезапуском воркера")
    assert ("Анализ публикации не выполнен: Задание прервано перезапуском воркера — проверьте вручную"
            in ad_events(res.ad_id, "system"))


@pytest.mark.parametrize("outcome", ["retry", "defer", "rate"])
def test_outcome_time_counts_from_outcome_not_claim(client, run_worker, use, monkeypatch, outcome):
    """Регрессия: повтор, отсрочка и пауза частоты считались от времени захвата - после долгого запуска срок оказывался
    уже прошедшим, повтор шёл сразу, Retry-After источника не соблюдался."""
    clock = [1000.0]
    monkeypatch.setattr(jobs, "MONOTONIC", lambda: clock[0])

    def collect(ctx):
        clock[0] += 1800  # запуск шёл полчаса
        if outcome == "retry":
            raise SourceError("Сайт не ответил (HTTP 503)", retryable=True)
        if outcome == "defer":
            raise Deferred(60, {"phase": "poll"})
        raise RateLimited(120)

    use(Fake(collect=collect, max_attempts=3))
    run_id = request()
    now = models.utcnow()
    assert run_worker(now=now) == 1  # повтор не захвачен сразу же в том же проходе
    end = now + timedelta(seconds=1800)
    if outcome == "rate":
        assert abs((models.as_utc(row().next_run_at) - end).total_seconds() - 120) < 1
        with SessionLocal() as db:  # до конца паузы - ни сбора по расписанию
            set_row(enabled=True)
            assert worker.schedule_due(db, end + timedelta(seconds=60)) == []
            db.rollback()
        return
    job = job_of(run_id)
    pause = (models.as_utc(job.run_after) - end).total_seconds()
    assert job.status == "queued" and (48 <= pause <= 72 if outcome == "retry" else abs(pause - 60) < 1), pause


def test_analysis_media_retry_time_from_failure(client, run_worker, ocr_stub, http_mock, monkeypatch):
    """Срок повтора загрузки файла приёма - от момента неудачи, а не от захвата задания."""
    clock = [5000.0]
    monkeypatch.setattr(analysis, "MONOTONIC", lambda: clock[0])

    def slow_503(request):
        clock[0] += 3600
        return httpx.Response(503)

    import httpx

    http_mock.on("GET", "https://cdn.example.com/slow.png", handler=slow_503)
    res = take(ig_pub(media=[MediaIn(key="ig:1", url="https://cdn.example.com/slow.png", origin_ref="intake")]))
    now = models.utcnow()
    run_worker(now=now)
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == res.ad_id)).one()
        retry_at = models.as_utc(datetime.fromisoformat(a.meta["retryAt"]))
    assert a.status == "pending" and retry_at >= now + timedelta(seconds=3600 + 119)  # retryAt - с точностью до секунды


@pytest.mark.parametrize("same_id", [False, True])
def test_late_outcome_of_reclaimed_job_not_written(client, run_worker, use, caplog, same_id):
    """Регрессия: задание, которое recover_stale(или requeue_own при том же SAQ_WORKER_ID) отдали другому захвату,
    прежний исполнитель перезаписывал: "выполнено", запуск завершён дважды, сводка дважды, фазы - поверх новых."""
    taker = "test" if same_id else "w2"

    def collect(ctx):
        ctx.ingest(pub(1))
        later = models.utcnow() + timedelta(hours=3)
        with SessionLocal() as db:  # пока этот исполнитель работает, задание забрали
            if same_id:
                assert jobs.requeue_own(db, "test") == 1
            else:
                assert jobs.recover_stale(db, later, 1800) == 1
            db.commit()
            assert jobs.claim(db, taker, later).id == ctx.job_id
        ctx.save_payload(phase="late")

    use(Fake(collect=collect))
    run_id = request()
    with caplog.at_level(logging.WARNING, logger="saq.jobs"):
        run_worker()
    job, run = job_of(run_id), get(models.CollectionRun, run_id)
    assert (job.status, job.locked_by, job.attempts) == ("running", taker, 2)
    assert "phase" not in (job.payload or {}) and run.status == "running" and run.finished_at is None
    assert not [e for e in source_events() if e.startswith("Сбор «")]
    assert "уже не у воркера test" in caplog.text


def test_stopped_connector_not_health_checked(client, run_worker, use, env, admin_headers):
    """Регрессия: плановая проверка шла и у остановленного подключения и снимала остановку bad_config без изменения
    настроек - каждый день сбор с теми же настройками, ошибка, остановка, записи в журнале."""
    env(META_USER_ACCESS_TOKEN=META)
    collects = []

    def collect(ctx):
        collects.append(1)
        raise BadConfig("Ни один аккаунт из списка не доступен")

    use(IgFake(collect=collect))
    now = models.utcnow()
    set_row("instagram", enabled=True, status="ready", checked_at=now - timedelta(hours=1), next_run_at=now)
    run_worker(now=now)
    r = row("instagram")
    assert (r.status, r.facts.get("stopped"), r.facts.get("stoppedCode"), len(collects)) == ("error", True,
                                                                                               "bad_config", 1)
    with SessionLocal() as db:
        assert worker.schedule_due(db, now + timedelta(days=2)) == []
        db.rollback()
    for day in (1, 2, 3):
        run_worker(now=now + timedelta(days=day))
    assert len(collects) == 1 and row("instagram").facts.get("stopped") is True
    # плановая проверка, поставленная до остановки, остановку тоже не снимает
    with SessionLocal() as db:
        run, _ = service.request_run(db, "instagram", trigger="test", who=jobs.SYSTEM)
        db.commit()
    run_worker()
    r = row("instagram")
    assert get(models.CollectionRun, run.id).status == "succeeded"
    assert (r.status, r.facts.get("stopped"), r.facts.get("stoppedCode")) == ("error", True, "bad_config")
    # проверка администратором - снимает
    assert client.post("/api/sources/instagram/test", headers=admin_headers, json={"author": AUTHOR}).status_code == 202
    run_worker()
    r = row("instagram")
    assert r.status == "ready" and "stopped" not in r.facts


def test_clean_stop_marks_worker_not_alive(client):
    """Регрессия: после штатной остановки воркер ещё до 2 минут показывался живым."""
    worker.beat("w-stop")
    with SessionLocal() as db:
        assert service.worker_info(db)["alive"] is True
    worker.release_lease("w-stop")
    with SessionLocal() as db:
        info = service.worker_info(db)
        assert (info["alive"], info["id"]) == (False, "w-stop") and info["last_seen_at"] is not None
    worker.beat("w-other")  # другой воркер жив - он и показывается
    with SessionLocal() as db:
        assert (service.worker_info(db)["alive"], service.worker_info(db)["id"]) == (True, "w-other")
    worker.beat("w-stop")  # перезапуск: пульс снова "жив"
    with SessionLocal() as db:
        assert "stoppedAt" not in get(models.WorkerLease, "worker:w-stop").info


@pytest.mark.skipif(not app_db.DATABASE_URL.startswith("postgresql"), reason="гонка захвата — только PostgreSQL")
def test_pg_analyze_claim_one_per_ad_under_race(client):
    """Регрессия(PostgreSQL, READ COMMITTED): два воркера одновременно захватывали два задания анализа одной
    публикации - условие "анализ не идёт" не видело незафиксированный захват другого."""
    ad = "d" * 32
    with SessionLocal() as db:
        first, _ = jobs.enqueue(db, "analyze", ad_id=ad, dedupe_key=f"analyze:{ad}", content_version=1)
        db.commit()
    now = models.utcnow() + timedelta(seconds=1)
    with SessionLocal() as db:
        assert jobs.claim(db, "w0", now).id == first.id  # ключ освобождён захватом
        second, created = jobs.enqueue(db, "analyze", ad_id=ad, dedupe_key=f"analyze:{ad}", content_version=2)
        db.commit()
        assert created
        job = db.get(models.Job, first.id)
        assert jobs.retry(db, job, "сеть", now=now - timedelta(hours=1))  # первое - снова в очереди рядом со вторым
        job.run_after = models.as_utc(now - timedelta(minutes=1))
        db.commit()
    a = SessionLocal()
    got: dict = {}
    try:
        J = models.Job
        picked = a.scalar(select(J.id).where(jobs._claimable(now)).order_by(J.priority.desc(), J.run_after, J.id)
                          .limit(1).with_for_update(skip_locked=True, of=J))
        assert jobs._try_claim(a, picked, "wA", now)  # захват A ещё не зафиксирован

        def other():
            with SessionLocal() as b:
                j = jobs.claim(b, "wB", now)
                got["id"] = j.id if j is not None else None

        t = threading.Thread(target=other)
        t.start()
        t.join(1.0)
        assert t.is_alive()  # B ждёт блокировку "анализ этой публикации"
        a.commit()
        t.join(10)
    finally:
        a.close()
    assert got == {"id": None}
    assert count(models.Job, models.Job.ad_id == ad, models.Job.status == "running") == 1
