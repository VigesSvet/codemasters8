"""TikTok через Apify (integration-spec §12.4, §16.3 test_integration_tiktok, §16.5 столбец K, план §8): проверка без
платного запуска, фазы сбора через воркер (запуск → опрос → Dataset постранично → стоимость), файлы только из хранилища
Apify с Bearer-токеном, бюджет и остановка платного сбора после ошибки, продолжение с сохранённой позиции и приёмка по
§14 плана. Без сети: API Apify — синтетическая заглушка поверх http_mock; токен — поддельный."""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from conftest import mp4_bytes, png_bytes

from app import models
from app.db import SessionLocal

TOKEN = "apify_api_FAKE0123456789abcdefghijkl"
API = "https://api.apify.com/v2/"
ACTOR = "clockworks~tiktok-scraper"
KV = "KvStore0001"
AUTHOR = "Омарова Ж."
TEXT = "Старт продаж ЖК Ульба Ривер, бронь"
PID1, PID2, PID3 = "7559230000000000001", "7559230000000000002", "7559230000000000003"
ME = {"data": {"id": "u1", "username": "saq-demo"}}
EVENTS = {"actor-start": {"eventTitle": "Старт", "eventDescription": "…", "eventPriceUsd": 0.005},
          "result": {"eventTitle": "Публикация", "eventDescription": "…", "eventPriceUsd": 0.004},
          "video-download": {"eventTitle": "Видео", "eventDescription": "…", "eventPriceUsd": 0.002}}
ACTOR_INFO = {"data": {"id": "GdWCkxBtKWOsKjdch", "username": "clockworks", "name": "tiktok-scraper",
                       "pricingInfos": [{"pricingModel": "PAY_PER_EVENT", "startedAt": "2025-01-01T00:00:00.000Z",
                                         "createdAt": "2024-12-01T00:00:00.000Z", "minimalMaxTotalChargeUsd": 0.5,
                                         "pricingPerEvent": {"actorChargeEvents": EVENTS}}]}}
PRICES = {"actor-start": 0.005, "result": 0.004, "video-download": 0.002}


def kv_url(key: str) -> str:
    return f"{API}key-value-stores/{KV}/records/{key}"


def item(pid: str, *, text: str = TEXT, name: str = "tumargroup", kv: bool = True, cover: bool = False,
         is_ad: bool = False, pinned: bool = False) -> dict:
    """Запись Dataset clockworks/tiktok-scraper (поля как в выгрузке владельца; значения синтетические)."""
    cdn = f"https://v16-webapp-prime.tiktok.com/video/{pid}?x-expires=1&signature=abc"
    vm = {"duration": 15, "originalDownloadAddr": cdn, "downloadAddr": kv_url(f"video-{name}-{pid}") if kv else cdn,
          "coverUrl": kv_url(f"cover-{pid}") if cover else "https://p16-sign-va.tiktokcdn.com/obj/c.jpeg?x-signature=s"}
    return {"id": pid, "text": text, "createTimeISO": "2026-10-09T11:10:23.000Z", "createTime": 1760008223,
            "webVideoUrl": f"https://www.tiktok.com/@{name}/video/{pid}",
            "authorMeta": {"name": name, "nickName": "Tumar Group", "verified": False}, "playCount": 1520,
            "diggCount": 12, "isAd": is_ad, "isSponsored": False, "isSlideshow": False, "isPinned": pinned,
            "videoMeta": vm, "mediaUrls": [vm["downloadAddr"]], "input": name}


class FakeApify:
    """Заглушка API Apify (через http_mock): Actor с ценами, запуск, опрос, прерывание, Dataset с пагинацией,
    записи хранилища. statuses — ответы опроса по порядку (последний повторяется)."""

    def __init__(self, mock, items=(), *, statuses=("RUNNING", "SUCCEEDED"), usage=0.01, events=None):
        self.mock = mock
        self.items = list(items)
        self.statuses = list(statuses)
        self.usage = usage
        self.events = dict(events or {})
        self.runs: list[str] = []
        self.bodies: list[dict] = []
        self.polls: dict[str, int] = {}
        self.aborted: list[str] = []
        self.kv: dict = {}
        mock.on("*", re.compile(r"^https://api\.apify\.com/v2/"), handler=self.handle)
        mock.on("GET", API + "users/me", json=ME)

    def run(self, rid: str, status: str, **kw) -> dict:
        return {"id": rid, "actId": "GdWCkxBtKWOsKjdch", "status": status, "defaultDatasetId": f"Ds{rid}",
                "defaultKeyValueStoreId": KV, "usageTotalUsd": self.usage, "chargedEventCounts": self.events, **kw}

    def handle(self, req: httpx.Request) -> httpx.Response:
        path, method = req.url.path, req.method
        if method == "GET" and path == f"/v2/actors/{ACTOR}":
            return httpx.Response(200, json=ACTOR_INFO)
        if method == "POST" and path == f"/v2/actors/{ACTOR}/runs":
            rid = f"Run{len(self.runs) + 1}"
            self.runs.append(rid)
            self.bodies.append(json.loads(req.content))
            return httpx.Response(201, json={"data": self.run(rid, "READY")})
        m = re.fullmatch(r"/v2/actor-runs/(\w+)", path)
        if m and method == "GET":
            rid = m.group(1)
            if "waitForFinish" in req.url.params:
                n = self.polls.get(rid, 0)
                self.polls[rid] = n + 1
                return httpx.Response(200, json={"data": self.run(rid, self.statuses[min(n, len(self.statuses) - 1)])})
            return httpx.Response(200, json={"data": self.run(rid, "SUCCEEDED")})
        m = re.fullmatch(r"/v2/actor-runs/(\w+)/abort", path)
        if m and method == "POST":
            self.aborted.append(m.group(1))
            return httpx.Response(200, json={"data": self.run(m.group(1), "ABORTING")})
        m = re.fullmatch(r"/v2/datasets/(\w+)/items", path)
        if m and method == "GET":
            off, lim = int(req.url.params.get("offset", 0)), int(req.url.params.get("limit", 1000))
            return httpx.Response(200, json=self.items[off:off + lim],
                                  headers={"X-Apify-Pagination-Total": str(len(self.items)),
                                           "X-Apify-Pagination-Offset": str(off)})
        m = re.fullmatch(r"/v2/key-value-stores/(\w+)/records/(.+)", path)
        if m and method == "GET":
            got = self.kv.get(m.group(2))
            if callable(got):
                return got(req)
            if got is None:
                return httpx.Response(404, json={"error": {"type": "record-not-found", "message": "Record not found"}})
            return httpx.Response(200, content=got)
        pytest.fail(f"нет заглушки Apify для {method} {req.url}")

    def calls(self, method: str, pattern: str) -> list:
        rx = re.compile(pattern)
        return [r for r in self.mock.calls if r.method == method and rx.search(str(r.url))]

    def posts(self) -> list:
        return self.calls("POST", r"/v2/actors/.+/runs")

    def kv_calls(self) -> list:
        return self.calls("GET", r"/v2/key-value-stores/")


# ---------- общие шаги ----------

@pytest.fixture
def tk(client, env, admin_headers, http_mock, ocr_stub):
    """APIFY_TOKEN задан (поддельный); заголовки администратора."""
    env(APIFY_TOKEN=TOKEN)
    return admin_headers


@pytest.fixture
def page2(monkeypatch):
    from app.integrations import tiktok

    monkeypatch.setattr(tiktok, "PAGE_LIMIT", 2)


def configure(client, headers, profiles=("@tumargroup",), **opts) -> dict:
    r = client.patch("/api/sources/tiktok", headers=headers,
                     json={"author": AUTHOR, "config": {"profiles": list(profiles), **opts}})
    assert r.status_code == 200, r.text
    return r.json()


def start(client, headers, action: str = "collect") -> int:
    r = client.post(f"/api/sources/tiktok/{action}", headers=headers, json={"author": AUTHOR})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def drive(run_worker, *, steps: int = 6, step: float = 30.0, start_at=None):
    """Несколько проходов воркера со сдвигом времени (отсрочки Deferred и паузы повторов истекают)."""
    t = start_at or models.utcnow() + timedelta(seconds=1)
    for i in range(steps):
        run_worker(now=t + timedelta(seconds=step * i))
    return t + timedelta(seconds=step * steps)


def conn(client) -> dict:
    return client.get("/api/sources/connectors/tiktok").json()


def run_of(client, run_id: int) -> dict:
    return client.get(f"/api/collection-runs/{run_id}").json()


def ads() -> list:
    with SessionLocal() as db:
        A = models.Ad
        return list(db.scalars(select(A).where(A.platform == "tiktok", A.origin == "collected")))


def assets(**where) -> list:
    A = models.MediaAsset
    with SessionLocal() as db:
        return list(db.scalars(select(A).where(*[getattr(A, k) == v for k, v in where.items()]).order_by(A.source_key)))


def collect_job() -> models.Job:
    with SessionLocal() as db:
        return db.scalars(select(models.Job).where(models.Job.kind == "collect", models.Job.collector == "tiktok")
                          .order_by(models.Job.id.desc())).first()


def make_ready(client, headers, run_worker) -> None:
    start(client, headers, "test")
    drive(run_worker, steps=1)
    assert conn(client)["status"] == "ready", conn(client)


def full_run(client, headers, run_worker, fake, *, steps: int = 6) -> int:
    run_id = start(client, headers)
    drive(run_worker, steps=steps)
    return run_id


# ---------- 1. настройка и проверка ----------

def test_not_configured(client, env, admin_headers):
    from app.integrations import smoke

    c = conn(client)
    assert (c["status"], c["missing"], c["paid"]) == ("not_configured", ["APIFY_TOKEN"], True)
    assert c["statusLabel"] == "не настроен"
    r = client.post("/api/sources/tiktok/test", headers=admin_headers, json={"author": AUTHOR})
    assert r.status_code == 409 and "APIFY_TOKEN" in r.json()["detail"]
    assert smoke.check_one("tiktok") == "tiktok: не настроен — нет APIFY_TOKEN"  # без сети (conftest её запрещает)


def test_check_ok_without_paid_run(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock)
    make_ready(client, tk, run_worker)
    c = conn(client)
    assert c["facts"]["minCharge"] == 0.5 and c["facts"]["account"] == "saq-demo"
    assert c["facts"]["prices"] == PRICES and c["statusLabel"] == "готов к сбору"
    assert "Платный запуск при проверке не выполнялся" in c["statusDetail"]
    assert fake.posts() == []
    assert [r.url.path for r in http_mock.calls] == ["/v2/users/me", f"/v2/actors/{ACTOR}"]
    for r in http_mock.calls:
        assert r.headers["authorization"] == f"Bearer {TOKEN}" and TOKEN not in str(r.url)


def test_check_401_expired(tk, client, http_mock, run_worker):
    http_mock.on("GET", API + "users/me", status=401, json={"error": {"type": "token-not-valid", "message": "x"}})
    start(client, tk, "test")
    drive(run_worker, steps=1)
    c = conn(client)
    assert c["status"] == "expired" and "Токен Apify недействителен" in c["statusDetail"]


def test_check_actor_404_error(tk, client, http_mock, run_worker):
    http_mock.on("GET", API + "users/me", json=ME)
    http_mock.on("GET", API + f"actors/{ACTOR}", status=404, json={"error": {"type": "record-not-found"}})
    start(client, tk, "test")
    drive(run_worker, steps=1)
    c = conn(client)
    assert c["status"] == "error" and "не найден" in c["lastError"]


def test_cap_below_actor_minimum_no_post(tk, client, env, http_mock, run_worker):
    env(APIFY_MAX_TOTAL_CHARGE_USD="0.3")
    fake = FakeApify(http_mock, [item(PID1)])
    start(client, tk, "test")
    drive(run_worker, steps=1)
    c = conn(client)
    assert c["status"] == "error"
    assert "APIFY_MAX_TOTAL_CHARGE_USD (0.3) меньше минимума Actor (0.5)" in c["lastError"]
    configure(client, tk)
    run_id = full_run(client, tk, run_worker, fake, steps=2)
    assert run_of(client, run_id)["status"] == "failed" and fake.posts() == []
    assert conn(client)["stopped"] is True


def test_profiles_validation(tk, client):
    from app.integrations import tiktok

    assert tiktok.normalize_profile("@TumarGroup") == "tumargroup"
    assert tiktok.normalize_profile("https://www.tiktok.com/@ulba.river/video/123?lang=ru") == "ulba.river"
    for bad in ("https://vm.tiktok.com/ZMabc/", "@a", "@имя", "@x" * 20):
        with pytest.raises(ValueError):
            tiktok.normalize_profile(bad)
    c = configure(client, tk, ["@tumargroup", "https://www.tiktok.com/@tumargroup", "ulba.river"], resultsPerPage=3)
    assert c["config"]["profiles"] == ["tumargroup", "ulba.river"] and c["config"]["resultsPerPage"] == 3
    r = client.patch("/api/sources/tiktok", headers=tk,
                     json={"author": AUTHOR, "config": {"profiles": ["vm.tiktok.com/x"]}})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "config", "profiles"]


def test_prices_and_cost():
    from app.integrations import tiktok

    tiered = {"pricingInfos": [
        {"pricingModel": "PAY_PER_EVENT", "startedAt": "2025-01-01T00:00:00Z", "minimalMaxTotalChargeUsd": 0.25,
         "pricingPerEvent": {"actorChargeEvents": {"result": {"eventTieredPricingUsd": {
             "FREE": {"tieredEventPriceUsd": 0.005}, "GOLD": {"tieredEventPriceUsd": 0.003}}}}}},
        {"pricingModel": "PAY_PER_EVENT", "startedAt": "2099-01-01T00:00:00Z", "minimalMaxTotalChargeUsd": 9,
         "pricingPerEvent": {"actorChargeEvents": {"result": {"eventPriceUsd": 1.0}}}}]}
    assert tiktok.prices(tiered) == {"result": 0.005}  # действующая запись, ступени — по наибольшей цене
    assert tiktok.min_charge(tiered) == 0.25
    assert tiktok.min_charge({}) == tiktok.ACTOR_MIN_CHARGE and tiktok.prices({}) == {}
    assert tiktok.estimate_usd(3, True, PRICES) == pytest.approx(0.005 + 3 * 0.004 + 3 * 0.002)
    assert tiktok.estimate_usd(3, False, {}) == pytest.approx(0.005 + 3 * 0.004)
    run = {"usageTotalUsd": 0.001, "chargedEventCounts": {"actor-start": 1, "result": 10, "unknown-event": 1}}
    assert tiktok.run_cost(run, PRICES, 3, True) == pytest.approx(0.005 + 10 * 0.004 + 0.005)
    assert tiktok.run_cost({"usageTotalUsd": 0.5}, PRICES, 3, True) == 0.5


# ---------- 2. полный запуск через воркер ----------

def test_full_run_through_worker(tk, client, http_mock, run_worker, page2, caplog):
    caplog.set_level(logging.DEBUG)
    redirect = "https://storage.example.net/apify/v2.mp4"
    fake = FakeApify(http_mock, [item(PID1, cover=True, is_ad=True), item(PID2, name="tumargroup"),
                                 {"error": "Profile not found", "input": "ulba.river"}],
                     usage=0.01, events={"actor-start": 1, "result": 3, "video-download": 2})
    v1, v2, cov = mp4_bytes(300), mp4_bytes(400), png_bytes((10, 20, 30))
    fake.kv[f"video-tumargroup-{PID1}"] = v1
    fake.kv[f"cover-{PID1}"] = cov
    fake.kv[f"video-tumargroup-{PID2}"] = lambda r: httpx.Response(302, headers={"Location": redirect})
    http_mock.on("GET", redirect, content=v2)
    configure(client, tk, ["@tumargroup", "https://www.tiktok.com/@ulba.river"], resultsPerPage=50)
    run_id = full_run(client, tk, run_worker, fake)

    (post,) = fake.posts()
    q = post.url.params
    assert (q["timeout"], float(q["maxTotalChargeUsd"]), q["maxItems"]) == ("300", 0.5, "10")
    body = fake.bodies[0]
    assert body["profiles"] == ["tumargroup", "ulba.river"] and body["resultsPerPage"] == 5
    assert body["shouldDownloadVideos"] is True and body["profileSorting"] == "latest"
    assert post.headers["authorization"] == f"Bearer {TOKEN}" and "token" not in str(post.url)
    pages = fake.calls("GET", r"/v2/datasets/")
    assert [(r.url.params["offset"], r.url.params["limit"], r.url.params["clean"]) for r in pages] == \
        [("0", "2", "true"), ("2", "2", "true")]
    kv = fake.kv_calls()
    assert len(kv) == 3 and all(r.headers.get("authorization") == f"Bearer {TOKEN}" for r in kv)
    (other,) = [r for r in http_mock.calls if r.url.host == "storage.example.net"]
    assert "authorization" not in other.headers  # перенаправление на другой хост — без токена

    run = run_of(client, run_id)
    assert run["status"] == "partial", run
    assert (run["counts"]["new"], run["counts"]["failed"], run["counts"]["mediaSaved"]) == (2, 1, 3)
    assert run["externalRunId"] == "Run1"
    expected = max(0.01, 0.005 + 3 * 0.004 + 2 * 0.002, 0.005 + 3 * 0.004 + 3 * 0.002)
    assert run["costUsd"] == pytest.approx(expected)
    assert any("Profile not found" in n["text"] for n in run["notes"])
    got = sorted(ads(), key=lambda a: a.url)
    assert [(a.account, a.url, a.source, a.status) for a in got] == [
        ("@tumargroup", f"https://www.tiktok.com/@tumargroup/video/{PID1}", "parser", "new"),
        ("@tumargroup", f"https://www.tiktok.com/@tumargroup/video/{PID2}", "parser", "new")]
    stored = assets(status="downloaded", parent_id=None)
    assert sorted((a.source_key, a.kind, a.role or "") for a in stored) == [
        (f"apify:{PID1}:cover", "image", "cover"), (f"apify:{PID1}:video", "video", ""),
        (f"apify:{PID2}:video", "video", "")]
    assert all(TOKEN not in (a.origin_url or "") and "?" not in (a.origin_url or "") for a in stored)
    with SessionLocal() as db:
        raws = [i.raw for i in db.scalars(select(models.IngestItem).where(models.IngestItem.platform == "tiktok"))]
    assert {r["isAd"] for r in raws} == {True, False}  # isAd=false не отфильтрован
    assert all("signature=" not in json.dumps(r) for r in raws)  # подписанные ссылки — без query
    c = conn(client)
    assert c["status"] == "partial" and "записей с ошибкой: 1 из 3" in c["statusDetail"]
    assert collect_job().status == "done"
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(run) and TOKEN not in json.dumps(c)
    assert TOKEN not in json.dumps(collect_job().payload)


# ---------- 3. файл из хранилища: 403 ----------

def test_kv_403_asset_failed_run_partial(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1)])
    fake.kv[f"video-tumargroup-{PID1}"] = lambda r: httpx.Response(403, json={"error": {"type": "no-permissions"}})
    configure(client, tk)
    run_id = full_run(client, tk, run_worker, fake)
    assert len(ads()) == 1
    (a,) = assets(source_key=f"apify:{PID1}:video")
    assert (a.status, a.error) == ("failed",
                                   "Apify отказал в доступе к файлу (403) — проверьте права токена на хранилище")
    assert run_of(client, run_id)["status"] == "partial" and conn(client)["status"] == "partial"


def test_no_kv_link_unavailable_not_downloaded(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1, kv=False)])
    configure(client, tk)
    full_run(client, tk, run_worker, fake)
    (a,) = assets(source_key=f"apify:{PID1}:video")
    assert (a.status, a.error, a.storage_key) == ("unavailable", "Apify не сохранил видео (нет ссылки на хранилище)",
                                                  None)
    assert fake.kv_calls() == [] and not any("tiktok.com" in (r.url.host or "") for r in http_mock.calls)


# ---------- 4. сбой опроса: второй платный запуск не начинается ----------

def test_poll_crash_no_second_post(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(500)

    def crash(req):
        raise RuntimeError("воркер упал во время опроса")
    http_mock.on("GET", re.compile(r"/v2/actor-runs/Run1\?waitForFinish"), handler=crash, times=1)
    configure(client, tk)
    run_id = start(client, tk)
    t = drive(run_worker, steps=2, step=30)
    job = collect_job()
    assert job.status == "queued" and job.attempts == 1 and job.payload["apifyRunId"] == "Run1"
    drive(run_worker, steps=4, step=100, start_at=t)
    assert len(fake.posts()) == 1 and len(ads()) == 1
    assert run_of(client, run_id)["status"] == "succeeded"


def test_poll_read_timeouts_keep_attempts(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(500)

    def timeout(req):
        raise httpx.ReadTimeout("read timeout", request=req)
    http_mock.on("GET", re.compile(r"/v2/actor-runs/Run1\?waitForFinish"), handler=timeout, times=2)
    configure(client, tk)
    run_id = start(client, tk)
    t = drive(run_worker, steps=3, step=30)
    job = collect_job()
    assert (job.status, job.attempts, job.payload["phase"]) == ("queued", 0, "poll")
    drive(run_worker, steps=3, step=30, start_at=t)
    job = collect_job()
    assert (job.status, job.attempts) == ("done", 1)
    assert len(fake.posts()) == 1 and run_of(client, run_id)["status"] == "succeeded"


# ---------- 5. сбой после первой страницы: продолжение со смещения ----------

def test_dataset_crash_resumes_at_saved_offset(tk, client, http_mock, run_worker, page2):
    fake = FakeApify(http_mock, [item(PID1), item(PID2), item(PID3)], statuses=("SUCCEEDED",))
    for pid, size in ((PID1, 501), (PID2, 502), (PID3, 503)):
        fake.kv[f"video-tumargroup-{pid}"] = mp4_bytes(size)

    def boom(req):
        raise httpx.ReadTimeout("read timeout", request=req)
    http_mock.on("GET", re.compile(r"/v2/datasets/DsRun1/items\?.*offset=2"), handler=boom, times=1)
    configure(client, tk)
    run_id = start(client, tk)
    t = drive(run_worker, steps=2, step=30)
    job = collect_job()
    assert job.status == "queued" and (job.payload["phase"], job.payload["offset"]) == ("dataset", 2)
    drive(run_worker, steps=4, step=100, start_at=t)
    offsets = [r.url.params["offset"] for r in fake.calls("GET", r"/v2/datasets/")]
    assert offsets == ["0", "2", "2"]  # первая страница повторно не запрашивается
    assert len(ads()) == 3 and len(fake.posts()) == 1
    run = run_of(client, run_id)
    assert run["status"] == "succeeded" and run["counts"]["new"] == 3


# ---------- 6. запуск Apify FAILED: остановка платного сбора ----------

def test_run_failed_stops_paid_collection(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1)], statuses=("FAILED",), usage=0.02)
    configure(client, tk)
    make_ready(client, tk, run_worker)
    r = client.patch("/api/sources/tiktok", headers=tk, json={"author": AUTHOR, "enabled": True})
    assert r.status_code == 200, r.text
    run_id = start(client, tk)
    drive(run_worker, steps=3)
    run = run_of(client, run_id)
    assert run["status"] == "failed" and run["error"] == "Запуск Apify завершился со статусом FAILED"
    assert run["costUsd"] == pytest.approx(0.02)
    c = conn(client)
    assert (c["status"], c["stopped"]) == ("error", True)
    assert "Платный сбор остановлен после ошибки" in c["statusDetail"]
    posts = len(fake.posts())
    drive(run_worker, steps=2, step=86400 * 3, start_at=models.utcnow() + timedelta(days=2))
    assert len(fake.posts()) == posts and conn(client)["lastRun"]["id"] == run_id  # расписание не запускает


# ---------- 7. дневной бюджет ----------

def _old_unknown_cost_run() -> None:
    with SessionLocal() as db:
        db.add(models.CollectionRun(collector="tiktok", trigger="schedule", status="failed", requested_by="Система",
                                    external_run_id="OldRun1", cost_usd=None, created_at=models.utcnow(), notes=[]))
        db.commit()


def test_daily_budget_unknown_cost_counts_at_cap(tk, client, env, http_mock, run_worker):
    env(APIFY_DAILY_BUDGET_USD="0.5")
    fake = FakeApify(http_mock, [item(PID1)])
    configure(client, tk)
    _old_unknown_cost_run()
    run_id = start(client, tk)
    drive(run_worker, steps=1)
    run = run_of(client, run_id)
    assert run["status"] == "rate_limited" and fake.posts() == []
    assert "Дневной лимит расходов Apify ($0.5) исчерпан" in run["error"]
    assert conn(client)["status"] == "rate_limited"


# ---------- 8. 402, 429, срок запуска ----------

def test_402_stops(tk, client, http_mock, run_worker):
    FakeApify(http_mock, [item(PID1)])
    http_mock.on("POST", API + f"actors/{ACTOR}/runs", status=402,
                 json={"error": {"type": "not-enough-usage-to-run-paid-actor", "message": "Not enough credit"}})
    configure(client, tk)
    run_id = start(client, tk)
    drive(run_worker, steps=2)
    run = run_of(client, run_id)
    assert run["status"] == "failed" and run["error"] == "Apify: нужна оплата/лимит аккаунта"
    assert conn(client)["stopped"] is True and collect_job().attempts == 1


def test_429_rate_limited_no_further_calls(tk, client, http_mock, run_worker):
    FakeApify(http_mock, [item(PID1)])
    http_mock.on("POST", API + f"actors/{ACTOR}/runs", status=429, headers={"Retry-After": "120"},
                 json={"error": {"type": "rate-limit-exceeded"}})
    configure(client, tk)
    run_id = start(client, tk)
    drive(run_worker, steps=3)
    assert run_of(client, run_id)["status"] == "rate_limited" and conn(client)["status"] == "rate_limited"
    assert [r.url.path for r in http_mock.calls] == [f"/v2/actors/{ACTOR}", f"/v2/actors/{ACTOR}/runs"]


def test_poll_deadline_aborts_run(tk, client, env, http_mock, run_worker):
    env(APIFY_RUN_TIMEOUT_S="60")  # срок опроса: 60 + 300 с
    fake = FakeApify(http_mock, [item(PID1)], statuses=("RUNNING",), usage=0.03)
    configure(client, tk)
    run_id = start(client, tk)
    drive(run_worker, steps=6, step=100)
    assert fake.aborted == ["Run1"] and len(fake.posts()) == 1
    run = run_of(client, run_id)
    assert run["status"] == "failed" and run["error"] == "Запуск Apify не завершился за 6 мин — прерван"
    assert run["costUsd"] == pytest.approx(0.03)
    assert conn(client)["stopped"] is True and ads() == []


# ---------- 9. Input без секретов ----------

def test_input_template_and_build_input(env):
    from app.integrations import tiktok

    raw = Path(tiktok.INPUT_FILE).read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["profiles"] == [] and data["shouldDownloadVideos"] is True
    assert not re.search(r"apify_api_|token|secret|password|Bearer", raw, re.I)
    token_like = re.compile(r"(?=[^\s]*\d)(?=[^\s]*[a-z])[A-Za-z0-9_-]{20,}")  # не константы вида NEVER_DOWNLOAD_…
    assert not token_like.search(raw)
    s = env(APIFY_MAX_ITEMS_PER_PROFILE="3")
    body = tiktok.build_input({"profiles": ["@A.b", "bad profile"], "resultsPerPage": 50, "downloadVideos": False}, s)
    assert (body["profiles"], body["resultsPerPage"], body["shouldDownloadVideos"]) == (["a.b"], 3, False)
    assert body["profileSorting"] == "latest" and json.loads(raw) == data  # шаблон не меняется


# ---------- приёмка по §14 плана (integration-spec §16.5, столбец K) ----------

def test_acceptance_repeat_no_duplicate(tk, client, http_mock, run_worker, accept):
    fake = FakeApify(http_mock, [item(PID1, cover=True)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(600)
    fake.kv[f"cover-{PID1}"] = png_bytes((1, 2, 3))
    configure(client, tk)
    full_run(client, tk, run_worker, fake)
    before, kv_before = accept.snapshot(), len(fake.kv_calls())
    run_id = full_run(client, tk, run_worker, fake)
    assert accept.snapshot() == before and len(fake.kv_calls()) == kv_before == 2
    run = run_of(client, run_id)
    assert run["status"] == "succeeded" and run["counts"]["unchanged"] == 1 and len(fake.posts()) == 2


def test_acceptance_failure_not_empty(tk, client, http_mock, run_worker, accept):
    FakeApify(http_mock, [item(PID1)], statuses=("FAILED",))
    configure(client, tk)
    run_id = full_run(client, tk, run_worker, None, steps=3)
    c = accept.assert_failure_visible("tiktok", run_id)
    assert c["status"] == "error" and "FAILED" in c["lastError"]


def test_acceptance_restart_keeps_position(tk, client, http_mock, run_worker, monkeypatch):
    from app.integrations import tiktok

    monkeypatch.setattr(tiktok, "PAGE_LIMIT", 1)
    fake = FakeApify(http_mock, [item(PID1), item(PID2)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(701)
    fake.kv[f"video-tumargroup-{PID2}"] = mp4_bytes(702)

    def boom(req):
        raise httpx.RemoteProtocolError("соединение оборвалось", request=req)
    http_mock.on("GET", re.compile(r"/v2/datasets/DsRun1/items\?.*offset=1"), handler=boom, times=1)
    configure(client, tk)
    start(client, tk)
    t = drive(run_worker, steps=2)
    assert collect_job().payload["offset"] == 1 and len(ads()) == 1  # позиция сохранена после первой записи
    drive(run_worker, steps=4, step=100, start_at=t)
    assert sorted(a.url.rsplit("/", 1)[1] for a in ads()) == [PID1, PID2]  # без пропуска и без дубликата
    assert [r.url.params["offset"] for r in fake.calls("GET", r"/v2/datasets/")] == ["0", "1", "1"]


def test_acceptance_media_survives_source(tk, client, http_mock, run_worker, accept):
    data = mp4_bytes(800)
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = data
    configure(client, tk)
    full_run(client, tk, run_worker, fake)
    http_mock.clear()  # исходной ссылки (хранилища Apify) больше нет
    (a,) = assets(source_key=f"apify:{PID1}:video")
    assert accept.media_ok(a.id) == data


def test_acceptance_not_confirmed_automatically(tk, client, http_mock, run_worker, accept):
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))  # текст называет демо-ЖК «Ульба Ривер»
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(900)
    configure(client, tk)
    full_run(client, tk, run_worker, fake)
    (ad,) = ads()
    accept.assert_not_confirmed(ad.id)
    r = client.post(f"/api/ads/{ad.id}/hypotheses/accept", json={"author": AUTHOR, "complexId": "c3"})
    assert r.status_code == 200, r.text
    assert (r.json()["complexId"], r.json()["linkConfirmedBy"]) == ("c3", AUTHOR)


def test_acceptance_history_kept(tk, client, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(950)
    configure(client, tk)
    full_run(client, tk, run_worker, fake)
    fake.items = [item(PID1, text="Изменённый текст: скидка 10 %")]
    run_id = full_run(client, tk, run_worker, fake)
    assert run_of(client, run_id)["counts"]["updated"] == 1
    (ad,) = ads()
    v = client.get(f"/api/ads/{ad.id}/versions").json()
    assert [x["text"] for x in v] == ["Изменённый текст: скидка 10 %", TEXT]
    with SessionLocal() as db:
        assert len(list(db.scalars(select(models.IngestVersion)))) == 2


def test_acceptance_limit_no_paid_loop(tk, client, env, http_mock, run_worker):
    fake = FakeApify(http_mock, [item(PID1)])
    configure(client, tk)
    make_ready(client, tk, run_worker)
    assert client.patch("/api/sources/tiktok", headers=tk, json={"author": AUTHOR, "enabled": True}).status_code == 200
    env(APIFY_DAILY_BUDGET_USD="0.5")
    _old_unknown_cost_run()
    run_id = start(client, tk)
    t0 = models.utcnow() + timedelta(seconds=1)
    drive(run_worker, steps=1, start_at=t0)
    assert run_of(client, run_id)["status"] == "rate_limited" and fake.posts() == []
    nxt = datetime.fromisoformat(conn(client)["nextRunAt"].replace("Z", "+00:00"))
    assert nxt > t0
    calls = len(http_mock.calls)
    gap = (nxt - t0).total_seconds()
    drive(run_worker, steps=3, step=gap / 4, start_at=t0 + timedelta(seconds=gap / 4))  # проходы до next_run_at
    assert len(http_mock.calls) == calls and fake.posts() == []
    assert conn(client)["lastRun"]["id"] == run_id


# ---------- 10. временные сбои после платного запуска, неподтверждённый запуск, лимит без округления ----------

def enable(client, headers, run_worker) -> None:
    """Подключение проверено и включено: работает расписание воркера (оно не должно начать второй платный запуск)."""
    configure(client, headers)
    make_ready(client, headers, run_worker)
    r = client.patch("/api/sources/tiktok", headers=headers, json={"author": AUTHOR, "enabled": True})
    assert r.status_code == 200, r.text


def ds_429(req):
    return httpx.Response(429, headers={"Retry-After": "60"}, json={"error": {"type": "rate-limit-exceeded"}})


def ds_503(req):
    return httpx.Response(503, json={"error": {"type": "service-unavailable", "message": "Service Unavailable"}})


def ds_timeout(req):
    raise httpx.ReadTimeout("read timeout", request=req)


def three_items(http_mock) -> "FakeApify":
    fake = FakeApify(http_mock, [item(PID1), item(PID2), item(PID3)], statuses=("SUCCEEDED",))
    for pid, size in ((PID1, 511), (PID2, 512), (PID3, 513)):
        fake.kv[f"video-tumargroup-{pid}"] = mp4_bytes(size)
    return fake


@pytest.mark.parametrize("failure", [ds_429, ds_503, ds_timeout], ids=["429", "503", "timeout"])
def test_dataset_transient_error_waits_no_second_paid_run(tk, client, http_mock, run_worker, page2, failure):
    """429 / 5xx / сеть при чтении Dataset уже оплаченного запуска: задание ждёт (Deferred, попытка не расходуется) и
    продолжает с сохранённого смещения; запуск сбора не завершается «паузой», расписание не начинает второй запуск."""
    fake = three_items(http_mock)
    enable(client, tk, run_worker)
    http_mock.on("GET", re.compile(r"/v2/datasets/DsRun1/items\?.*offset=2"), handler=failure, times=1)
    run_id = start(client, tk)
    t = drive(run_worker, steps=2, step=30)
    job = collect_job()
    assert (job.status, job.attempts, job.payload["phase"], job.payload["offset"]) == ("queued", 0, "dataset", 2)
    assert run_of(client, run_id)["status"] == "running"
    drive(run_worker, steps=4, step=100, start_at=t)
    assert fake.runs == ["Run1"] and len(fake.posts()) == 1
    assert [r.url.params["offset"] for r in fake.calls("GET", r"/v2/datasets/")] == ["0", "2", "2"]
    run = run_of(client, run_id)
    assert run["status"] == "succeeded" and (run["counts"]["new"], run["counts"]["seen"]) == (3, 3)
    assert len(ads()) == 3 and conn(client)["status"] == "working" and collect_job().status == "done"


def test_dataset_unavailable_too_long_fails_and_stops(tk, client, http_mock, run_worker, page2, monkeypatch):
    """Apify не отдаёт Dataset дольше DATASET_WAIT_S подряд: сбор — ошибка, платный сбор остановлен; второй платный
    запуск не начинается, принятые записи остаются."""
    from app.integrations import tiktok

    monkeypatch.setattr(tiktok, "DATASET_WAIT_S", 150)
    fake = three_items(http_mock)
    enable(client, tk, run_worker)
    http_mock.on("GET", re.compile(r"/v2/datasets/DsRun1/items\?.*offset=2"), handler=ds_503)
    run_id = start(client, tk)
    drive(run_worker, steps=8, step=100)
    run = run_of(client, run_id)
    assert run["status"] == "failed", run
    assert run["error"].startswith("Набор данных запуска Apify Run1 не дочитан")
    assert "прочитано записей: 2" in run["error"]
    c = conn(client)
    assert (c["status"], c["stopped"]) == ("error", True)
    assert fake.runs == ["Run1"] and len(ads()) == 2 and collect_job().attempts == 1
    offsets = [r.url.params["offset"] for r in fake.calls("GET", r"/v2/datasets/")]
    assert offsets[0] == "0" and set(offsets[1:]) == {"2"}  # первая страница повторно не запрашивается


def test_dataset_stop_checked_per_item(tk, client, http_mock, run_worker, monkeypatch):
    """Остановка воркера (и мягкий срок) проверяются перед каждой записью: позиция — на записи, задание откладывается
    без расхода попытки; продолжение не принимает записи повторно (счётчики не удваиваются)."""
    from app.integrations.context import RunContext

    fake = three_items(http_mock)
    configure(client, tk)
    flag = {"stop": False}
    orig = RunContext.ingest

    def ingest(self, pub, **kw):
        res = orig(self, pub, **kw)
        flag["stop"] = True  # после первой принятой записи воркер получил сигнал остановки
        return res
    monkeypatch.setattr(RunContext, "ingest", ingest)
    monkeypatch.setattr(RunContext, "stopping", lambda self: flag["stop"])
    run_id = start(client, tk)
    t = drive(run_worker, steps=2, step=30)
    job = collect_job()
    assert (job.status, job.attempts, job.payload["offset"], job.payload["items"]) == ("queued", 0, 1, 1)
    assert len(ads()) == 1
    monkeypatch.setattr(RunContext, "ingest", orig)
    flag["stop"] = False
    drive(run_worker, steps=4, step=100, start_at=t)
    assert [r.url.params["offset"] for r in fake.calls("GET", r"/v2/datasets/")] == ["0", "1"]
    run = run_of(client, run_id)
    assert run["status"] == "succeeded" and (run["counts"]["new"], run["counts"]["seen"]) == (3, 3)
    assert len(ads()) == 3 and len(fake.posts()) == 1


def _post_lost(fake, mode):
    """POST запуска дошёл до Apify (запуск создан и оплачивается), но подтверждения нет."""
    def handler(req):
        fake.runs.append(f"Run{len(fake.runs) + 1}")
        if mode == "timeout":
            raise httpx.ReadTimeout("read timeout", request=req)
        if mode == "garbled":
            return httpx.Response(201, content=b"<html>upstream reset</html>")
        return httpx.Response(int(mode), text="Bad Gateway")
    return handler


@pytest.mark.parametrize("mode", ["timeout", "500", "502", "504", "garbled"])
def test_unconfirmed_start_no_retry_counts_in_budget(tk, client, env, http_mock, run_worker, mode):
    """Ответ на POST запуска потерян (тайм-аут чтения, 5xx шлюза, 2xx без ID): запуск мог начаться — повтора нет,
    платный сбор остановлен, а в дневном лимите он учтён по лимиту на запуск: после ручной проверки в тот же день
    второй платный запуск не начинается, если лимит исчерпан."""
    env(APIFY_DAILY_BUDGET_USD="0.5")  # = лимит на запуск: один возможный запуск исчерпывает день
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    http_mock.on("POST", API + f"actors/{ACTOR}/runs", handler=_post_lost(fake, mode), times=1)
    configure(client, tk)
    run_id = start(client, tk)
    drive(run_worker, steps=4, step=600)  # пауза повтора (backoff) истекла бы — повтора нет
    run = run_of(client, run_id)
    assert run["status"] == "failed" and "запуск мог начаться и оплачиваться" in run["error"], run
    assert fake.runs == ["Run1"] and len(fake.posts()) == 1 and collect_job().attempts == 1
    assert any("в дневном лимите учтён как $0.5" in n["text"] for n in run["notes"])
    assert conn(client)["stopped"] is True
    make_ready(client, tk, run_worker)  # администратор проверил подключение — остановка снята
    run2 = start(client, tk)
    drive(run_worker, steps=2)
    r2 = run_of(client, run2)
    assert r2["status"] == "rate_limited" and "Дневной лимит расходов Apify ($0.5) исчерпан" in r2["error"]
    assert fake.runs == ["Run1"] and len(fake.posts()) == 1


def test_post_connect_error_retried_without_budget_mark(tk, client, http_mock, run_worker):
    """Соединение не установлено — запрос точно не ушёл: повтор допустим, в лимите ничего не учитывается."""
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(990)

    def refused(req):
        raise httpx.ConnectError("connection refused", request=req)
    http_mock.on("POST", API + f"actors/{ACTOR}/runs", handler=refused, times=1)
    configure(client, tk)
    run_id = start(client, tk)
    drive(run_worker, steps=6, step=100)
    assert fake.runs == ["Run1"] and len(fake.posts()) == 2
    assert run_of(client, run_id)["status"] == "succeeded" and len(ads()) == 1
    with SessionLocal() as db:
        state = db.scalar(select(models.Collector).where(models.Collector.code == "tiktok")).state
    assert not state.get("unconfirmedStarts")


def test_cap_sent_exactly_never_rounded(tk, client, env, http_mock, run_worker):
    from app.integrations import tiktok

    for cap in (0.5, 0.5555555555, 1.2345678, 1234567.0, 0.1 + 0.2, 1e-05, 2e16):
        q = tiktok.usd_param(cap)
        assert float(q) == cap and not re.search(r"[eE]|inf|nan", q), (cap, q)
    env(APIFY_MAX_TOTAL_CHARGE_USD="0.5555555555", APIFY_DAILY_BUDGET_USD="2")
    fake = FakeApify(http_mock, [item(PID1)], statuses=("SUCCEEDED",))
    fake.kv[f"video-tumargroup-{PID1}"] = mp4_bytes(991)
    configure(client, tk)
    full_run(client, tk, run_worker, fake)
    (post,) = fake.posts()
    assert post.url.params["maxTotalChargeUsd"] == "0.5555555555"
    env(APIFY_MAX_TOTAL_CHARGE_USD="inf", APIFY_DAILY_BUDGET_USD="inf")
    start(client, tk, "test")
    drive(run_worker, steps=1)
    assert "APIFY_MAX_TOTAL_CHARGE_USD" in conn(client)["lastError"] and len(fake.posts()) == 1


# ---------- 11. окно выдачи: пропуск публикаций не выглядит полным сбором (план §14) ----------

P = [f"75592300000001{i:05d}" for i in range(14)]  # P[1]…P[13]: чем больше номер, тем новее публикация
U = [f"75592300000002{i:05d}" for i in range(8)]  # публикации второго профиля (ID TikTok общие для всех)


def feed(fake, pids, *, name: str = "tumargroup", pinned=()) -> list:
    """Выдача профиля (новые сверху) с видео в хранилище Apify."""
    out = []
    for pid in pids:
        fake.kv[f"video-{name}-{pid}"] = mp4_bytes(1000 + int(pid[-5:]))
        out.append(item(pid, name=name, pinned=pid in pinned))
    return out


def warns(run: dict) -> list:
    return [n["text"] for n in run["notes"] if n["level"] == "warn"]


def seen_state() -> dict:
    with SessionLocal() as db:
        row = db.scalar(select(models.Collector).where(models.Collector.code == "tiktok"))
        return dict((row.state or {}).get("profiles") or {})


GAP = ("@tumargroup: все последние публикации в выдаче ({k}) новые — более ранние могли быть пропущены; увеличьте "
       "«Публикаций за запуск с каждого профиля» (сейчас 3, предел APIFY_MAX_ITEMS_PER_PROFILE = 5) или сократите "
       "интервал сбора")


def test_window_all_new_marks_partial(tk, client, http_mock, run_worker):
    """Профиль собран прежде, а вся выдача (resultsPerPage последних) — новые публикации: более ранние новые могли
    выпасть из окна — сбор «частично» с причиной, а не «работает». Первый сбор профиля — только заметка; выдача,
    пересекающаяся с уже собранным, — обычный успешный сбор."""
    fake = FakeApify(http_mock, statuses=("SUCCEEDED",))
    configure(client, tk, resultsPerPage=3)
    fake.items = feed(fake, [P[3], P[2], P[1]])
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "succeeded" and run["counts"]["new"] == 3, run
    assert "@tumargroup: первый сбор профиля — получены последние публикации (3), более ранние не собирались" in \
        [n["text"] for n in run["notes"]]
    c = conn(client)
    assert warns(run) == [] and c["status"] == "working"
    assert "За запуск — не больше 5 последних публикаций с профиля" in c["limitation"]  # окно видно на странице
    assert seen_state()["tumargroup"]["items"] == 3

    fake.items = feed(fake, [P[12], P[11], P[10]])  # P[4]…P[9] вышли между запусками и в окно не попали
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "partial" and run["counts"]["new"] == 3 and run["counts"]["failed"] == 0, run
    assert warns(run) == [GAP.format(k=3)]
    c = conn(client)
    assert c["status"] == "partial" and c["statusDetail"] == GAP.format(k=3)
    assert len(fake.posts()) == 2  # предупреждение не начинает платных запусков

    fake.items = feed(fake, [P[13], P[12], P[11]])  # пересечение с собранным: сбор непрерывен
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "succeeded" and (run["counts"]["new"], run["counts"]["unchanged"]) == (1, 2), run
    assert warns(run) == [] and conn(client)["status"] == "working"


def test_window_pinned_posts_do_not_prove_continuity(tk, client, http_mock, run_worker):
    """Закреплённая публикация стоит в выдаче вне хронологии: уже собранная закреплённая не доказывает, что окно
    дошло до прошлого сбора; выдача из одних закреплённых — тоже возможный пропуск."""
    fake = FakeApify(http_mock, statuses=("SUCCEEDED",))
    configure(client, tk, resultsPerPage=3)
    fake.items = feed(fake, [P[3], P[2], P[1]])
    assert run_of(client, full_run(client, tk, run_worker, fake))["status"] == "succeeded"

    fake.items = feed(fake, [P[1], P[9], P[8]], pinned={P[1]})  # P[1] закреплена и уже собрана; P[4]…P[7] пропущены
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "partial" and warns(run) == [GAP.format(k=2)], run

    fake.items = feed(fake, [P[1], P[10], P[9]], pinned={P[1]})  # P[9] уже собрана — сбор непрерывен
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "succeeded" and warns(run) == [], run

    configure(client, tk, resultsPerPage=2)
    fake.items = feed(fake, [P[1], P[2]], pinned={P[1], P[2]})  # окно занято закреплёнными: новых не видно
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "partial", run
    assert warns(run) == ["@tumargroup: в выдаче только закреплённые публикации (2) — новые могли быть пропущены; "
                          "увеличьте «Публикаций за запуск с каждого профиля» (сейчас 2, предел "
                          "APIFY_MAX_ITEMS_PER_PROFILE = 5) или сократите интервал сбора"]


def test_window_per_profile_survives_dataset_deferral(tk, client, env, http_mock, run_worker, page2):
    """Счётчики по профилям переживают отсрочку чтения Dataset (итог считает фаза cost в другом проходе воркера);
    пропуск отмечается только у профиля без пересечения; выдача на пределе APIFY_MAX_ITEMS_PER_PROFILE — подсказка
    про .env."""
    env(APIFY_MAX_ITEMS_PER_PROFILE="2")
    fake = FakeApify(http_mock, statuses=("SUCCEEDED",))
    configure(client, tk, ["@tumargroup", "@ulba.river"])  # resultsPerPage не задан: 5 из шаблона → предел 2
    fake.items = feed(fake, [P[2], P[1]]) + feed(fake, [U[2], U[1]], name="ulba.river")
    run = run_of(client, full_run(client, tk, run_worker, fake))
    assert run["status"] == "succeeded" and run["counts"]["new"] == 4, run
    assert sorted(n["text"].split(":")[0] for n in run["notes"] if "первый сбор профиля" in n["text"]) == \
        ["@tumargroup", "@ulba.river"]
    body, q = fake.bodies[0], fake.posts()[0].url.params
    assert (body["resultsPerPage"], q["maxItems"]) == (2, "4")

    # страница 1: ulba.river U[4], tumargroup P[3] (новые); сбой; страница 2: ulba.river U[3] (новая), tumargroup P[2]
    fake.items = [*feed(fake, [U[4]], name="ulba.river"), *feed(fake, [P[3]]), *feed(fake, [U[3]], name="ulba.river"),
                  *feed(fake, [P[2]])]
    http_mock.on("GET", re.compile(r"/v2/datasets/DsRun2/items\?.*offset=2"), handler=ds_503, times=1)
    run_id = start(client, tk)
    t = drive(run_worker, steps=2, step=30)
    job = collect_job()
    assert (job.status, job.payload["phase"], job.payload["offset"]) == ("queued", "dataset", 2)
    assert job.payload["profileStats"] == {"ulba.river": [1, 1, 1], "tumargroup": [1, 1, 1]}
    assert job.payload["seeded"] == ["tumargroup", "ulba.river"]
    drive(run_worker, steps=4, step=100, start_at=t)
    run = run_of(client, run_id)
    assert run["status"] == "partial" and run["counts"]["new"] == 3, run
    assert warns(run) == ["@ulba.river: все последние публикации в выдаче (2) новые — более ранние могли быть "
                          "пропущены; увеличьте APIFY_MAX_ITEMS_PER_PROFILE в .env (сейчас 2) и «Публикаций за запуск "
                          "с каждого профиля» или сократите интервал сбора"]
    assert fake.runs == ["Run1", "Run2"] and collect_job().status == "done"


def test_window_rules(env):
    """Правила окна выдачи без воркера: счётчики, неполная выдача, обрезка maxItems, запуск, начатый до проверки
    (без снимка seeded), предел памяти профилей."""
    from app.integrations import tiktok
    from app.integrations.context import RunContext

    s = env()
    stats: dict = {}
    tiktok.tally(stats, item(P[1]), "created")
    tiktok.tally(stats, item(P[2], pinned=True), "created")
    tiktok.tally(stats, item(P[3], name="UlbaRiver"), "unchanged")
    tiktok.tally(stats, {"id": P[4], "authorMeta": {}, "input": "https://www.tiktok.com/@ulbariver"}, "created")
    tiktok.tally(stats, {"id": P[5], "authorMeta": {"name": "имя"}}, "created")  # профиль не определён — не считается
    assert stats == {"tumargroup": [2, 1, 1], "ulbariver": [2, 2, 1]}
    assert tiktok._stats({"a": [1, 1, 1], "b": [1, -1, 0], "c": "x", "d": [True, 1, 1], 5: [1, 1, 1]}) == \
        {"a": [1, 1, 1]}

    def window(st: dict, *, per_page: int = 3, max_items=None, seeded=("a", "b"), state=None, run_id: int = 7):
        ctx = RunContext.ephemeral("tiktok", state=state)
        ctx.run_id = run_id
        ctx.payload = {"profileStats": st, "perPage": per_page, "maxItems": max_items}
        if seeded is not None:
            ctx.payload["seeded"] = list(seeded)
        tiktok.CONNECTOR._window(ctx, s)
        return ctx

    def flagged(ctx) -> list:
        return sorted(r.split(":")[0] for r in ctx.partial_reasons)

    assert flagged(window({"a": [2, 2, 2], "b": [3, 3, 2]})) == []  # публикаций меньше окна; пересечение
    assert flagged(window({"a": [3, 3, 3], "b": [2, 2, 2]})) == ["@a"]
    assert flagged(window({"a": [4, 4, 4], "b": [2, 2, 2]}, max_items=6)) == ["@a", "@b"]  # maxItems мог обрезать b
    at = "2026-10-01T00:00:00Z"
    legacy = {"profiles": {"a": {"run": 7, "at": at}, "b": {"run": 6, "at": at}}}
    ctx = window({"a": [3, 3, 3], "b": [3, 3, 3]}, seeded=None, state=legacy)
    assert flagged(ctx) == ["@b"]  # a отмечен этим же запуском (повтор фазы cost) — первый сбор, не пропуск
    assert ctx.state["profiles"]["a"]["run"] == 7 and ctx.state["profiles"]["b"]["items"] == 3
    many = {"profiles": {f"p{i:03d}": {"run": 1, "at": f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z"}
                         for i in range(tiktok.SEEN_MAX + 20)}}
    kept = window({"a": [1, 1, 1]}, state=many).state["profiles"]
    assert len(kept) == tiktok.SEEN_MAX and "a" in kept and "p000" not in kept and f"p{tiktok.SEEN_MAX + 19}" in kept
