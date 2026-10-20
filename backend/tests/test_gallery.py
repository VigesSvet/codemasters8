"""Фото объекта(app.gallery): фото оператора, обложка карточки, подгрузка фото карточек Homeportal(сети нет -
http_mock), порядок источников и подписи, журнал, пределы; на риск фото не влияют."""
import re

from sqlalchemy import func, select

import demo_seed
from conftest import png_bytes

from app import gallery, models
from app.db import SessionLocal

AUTHOR = "Омарова Ж."
ORDA = "r-orda"  # Homeportal projects/1329
AB = "r-ab-residence-2"  # Homeportal Illegal-projects/1813
DIAMOND = "r-diamond"  # только Казреестр - карточки Homeportal нет


def live(env) -> None:
    env()
    demo_seed.live()


def cx(client, cid: str) -> dict:
    r = client.get(f"/api/complexes/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def journal(cid: str) -> list:
    with SessionLocal() as db:
        E = models.AuditEvent
        return [(e.who, e.what) for e in db.scalars(select(E).where(E.complex_id == cid).order_by(E.id))]


def upload(client, cid: str, data=None, **fields):
    form = {"author": AUTHOR, **fields}
    return client.post(f"/api/complexes/{cid}/gallery", data={k: v for k, v in form.items() if v is not None},
                       files={"file": ("dom.png", data if data is not None else png_bytes(), "image/png")})


def hp(http_mock, path: str, oid: str, n: int) -> None:
    """Карточка Homeportal с n фото(первое - главное у источника последним в списке)."""
    images = [{"id": 100 + i, "is_featured": int(i == n - 1),
               "image_link": f"https://api.homeportal.kz/storage/object-images/{oid}/img {i}.png"} for i in range(n)]
    http_mock.on("GET", f"https://api.homeportal.kz/api/v1/{path}", json={"success": True, "data": {"images": images}})
    http_mock.on("GET", re.compile(rf"/storage/object-images/{oid}/img%20\d\.png$"),
                 handler=lambda r: __import__("httpx").Response(200, content=png_bytes((int(str(r.url)[-5]) * 20, 9, 9))))


def test_operator_photo_cover_and_journal(client, env):
    live(env)
    before = client.get(f"/api/complexes/{DIAMOND}/risk").json()
    c = cx(client, DIAMOND)
    assert (c["gallery"], c["homeportal"], c["cover"]) == ([], False, "")
    r = upload(client, DIAMOND, png_bytes((131, 132, 133)), note="Вид с улицы")
    assert r.status_code == 201, r.text
    g = r.json()["gallery"]
    assert [(x["kind"], x["source"], x["by"], x["note"], x["cover"]) for x in g] == [
        ("operator", "Фото оператора", AUTHOR, "Вид с улицы", True)]
    assert r.json()["cover"] == g[0]["src"] == f"/api/media/{g[0]['mediaId']}"
    assert client.get(g[0]["src"]).content == png_bytes((131, 132, 133))
    assert journal(DIAMOND)[-1] == (AUTHOR, "Фото объекта загружено оператором и выбрано обложкой карточки. Вид с улицы")
    # второе фото обложку не меняет; закрепить второе, вернуть автоматическую
    second = upload(client, DIAMOND, png_bytes((134, 135, 136))).json()["gallery"]
    assert second[0]["mediaId"] == g[0]["mediaId"] and len(second) == 2
    other = second[1]
    r = client.post(f"/api/complexes/{DIAMOND}/cover", json={"author": AUTHOR, "src": other["src"]})
    assert r.status_code == 200 and r.json()["gallery"][0]["mediaId"] == other["mediaId"] and r.json()["gallery"][0]["cover"]
    assert journal(DIAMOND)[-1] == (AUTHOR, "Обложка карточки: Фото оператора")
    r = client.post(f"/api/complexes/{DIAMOND}/cover", json={"author": AUTHOR, "src": ""})
    assert r.json()["cover"] == "" and not any(x["cover"] for x in r.json()["gallery"])
    # новые фото оператора - первыми
    assert r.json()["gallery"][0]["mediaId"] == other["mediaId"]
    assert client.post(f"/api/complexes/{DIAMOND}/cover", json={"author": AUTHOR, "src": "/api/media/x"}).status_code == 422
    assert client.post(f"/api/complexes/{DIAMOND}/cover", json={"author": "Система", "src": ""}).status_code == 422
    # удалить: чужой ЖК - 404, своё - снимает и обложку
    client.post(f"/api/complexes/{DIAMOND}/cover", json={"author": AUTHOR, "src": other["src"]})
    assert client.request("DELETE", f"/api/complexes/{ORDA}/gallery/{other['mediaId']}", json={"author": AUTHOR}).status_code == 404
    r = client.request("DELETE", f"/api/complexes/{DIAMOND}/gallery/{other['mediaId']}", json={"author": AUTHOR})
    assert r.status_code == 200 and r.json()["cover"] == "" and len(r.json()["gallery"]) == 1
    assert journal(DIAMOND)[-1] == (AUTHOR, "Фото объекта от оператора удалено")
    # на риск фото объекта не влияют(снимок площадки - отдельный путь)
    assert client.get(f"/api/complexes/{DIAMOND}/risk").json() == before


def test_upload_validation(client, env):
    live(env)
    assert upload(client, DIAMOND, author="Система").status_code == 422
    assert upload(client, DIAMOND, b"%PDF-1.7 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").status_code == 415
    assert upload(client, "nope").status_code == 404
    assert cx(client, DIAMOND)["gallery"] == []


def test_homeportal_fetch(client, env, http_mock):
    live(env)
    assert cx(client, ORDA)["homeportal"] is True
    hp(http_mock, "objects-detail/1329", "1329", 3)
    r = client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR})
    assert r.status_code == 200, r.text
    g = r.json()["gallery"]
    assert [x["kind"] for x in g] == ["homeportal"] * 3
    assert g[0]["url"] == "https://homeportal.kz/ru/projects/1329" and g[0]["source"] == "Homeportal"
    assert client.get(g[0]["src"]).content == png_bytes((40, 9, 9))  # главное у источника фото - первым
    assert journal(ORDA)[-1][1].startswith("Фото объекта с Homeportal: добавлено 3")
    # повтор - без дублей и без новой записи журнала
    n = len(journal(ORDA))
    assert len(client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR}).json()["gallery"]) == 3
    assert len(journal(ORDA)) == n
    # фото оператора - впереди Homeportal
    up = upload(client, ORDA).json()["gallery"]
    assert [x["kind"] for x in up] == ["operator", "homeportal", "homeportal", "homeportal"]
    # раздел "Без разрешения на дольщиков" - illegal-objects; без карточки Homeportal - 422
    hp(http_mock, "illegal-objects/1813", "1813", 1)
    assert [x["kind"] for x in client.post(f"/api/complexes/{AB}/gallery/fetch", json={"author": AUTHOR}).json()["gallery"]][:1] == ["homeportal"]
    r = client.post(f"/api/complexes/{DIAMOND}/gallery/fetch", json={"author": AUTHOR})
    assert (r.status_code, r.json()["detail"]) == (422, gallery.NO_HOMEPORTAL)


def test_homeportal_unavailable(client, env, http_mock):
    live(env)
    http_mock.on("GET", "https://api.homeportal.kz/api/v1/objects-detail/1329", status=503)
    r = client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR})
    assert r.status_code == 502 and r.json()["detail"].startswith("Homeportal не отдал фото")
    assert cx(client, ORDA)["gallery"] == []


def test_site_photos_and_ads_in_gallery(client):
    """Демо-набор: снимки площадки - "Фото-мониторинг", новые первыми; изображения рекламы - с пометкой гипотезы."""
    rows = client.get("/api/complexes").json()
    for c in rows:
        kinds = [x["kind"] for x in c["gallery"]]
        assert kinds == sorted(kinds, key=gallery.KIND_ORDER.get) or c["gallery"][0].get("cover")
        site = [x["date"] for x in c["gallery"] if x["kind"] == "site"]
        assert site == sorted(site, reverse=True)
        for x in c["gallery"]:
            if x["kind"] == "ad":
                assert x["adId"] and isinstance(x["hypothesis"], bool)


def test_worker_fetches_missing_homeportal_photos(client, env, http_mock):
    """Такт лидера воркера раз в SAQ_GALLERY_SYNC_HOURS: фото Homeportal - только ЖК, у которых их ещё нет."""
    from app import worker

    live(env)
    env(SAQ_GALLERY_SYNC_HOURS="24")
    hp(http_mock, "objects-detail/1329", "1329", 2)
    with SessionLocal() as db:
        rep = dict(gallery.fetch_all(db, [ORDA], missing_only=True))
    assert rep["Orda"]["added"] == 2
    with SessionLocal() as db:  # уже есть - второй проход Homeportal не спрашивает
        n = len(http_mock.calls)
        assert gallery.fetch_all(db, [ORDA], missing_only=True) == []
        assert len(http_mock.calls) == n
    # такт воркера: остальные карточки Homeportal недоступны - сбой не роняет такт, Orda остаётся с фото
    http_mock.on("GET", re.compile(r"api\.homeportal\.kz/api/v1/(objects-detail|illegal-objects)/(?!1329)"), status=503)
    with SessionLocal() as db:
        worker.gallery_sync(db)
    assert [x["kind"] for x in cx(client, ORDA)["gallery"]] == ["homeportal", "homeportal"]


def test_homeportal_pages_only_homeportal_hosts():
    """Регрессия: ссылкой карточки считалась любая строка с "homeportal.kz" - чужой адрес попадал в ссылку фото."""
    recs = [{"source": "homeportal", "sourceUrl": u} for u in (
        "https://homeportal.kz/ru/projects/1329", "https://www.homeportal.kz/ru/projects/7",
        "https://homeportal.kz/ru/projects/1329",  # повтор
        "https://evil.example/homeportal.kz/projects/1", "javascript:alert(1)//homeportal.kz",
        "https://homeportal.kz.evil.example/projects/2", "https://[::1/homeportal.kz", None, 42)]
    recs.append({"source": "kazreestr", "sourceUrl": "https://homeportal.kz/ru/projects/9"})
    assert gallery.homeportal_page_urls(recs) == ["https://homeportal.kz/ru/projects/1329",
                                                  "https://www.homeportal.kz/ru/projects/7"]
    assert gallery.has_homeportal_pages(recs) and not gallery.has_homeportal_pages(recs[3:])
    assert not gallery.has_homeportal_pages([])


def test_fetch_stops_on_worker_stop(client, env, http_mock, monkeypatch):
    """Остановка воркера(SIGTERM) прерывает подгрузку фото Homeportal: следующий ЖК не начинается, начатый не
    записывается наполовину(missing_only подгрузит его целиком в следующий такт)."""
    import threading

    from app import worker
    from app.integrations import http

    live(env)
    hp(http_mock, "illegal-objects/1813", "1813", 2)
    hp(http_mock, "objects-detail/1329", "1329", 3)
    stop = threading.Event()
    real, fetched = http.fetch_to_temp, []

    def stopping(*a, **kw):  # SIGTERM приходит, пока качается первое фото
        fetched.append(kw.get("cancel"))
        stop.set()
        return real(*a, **kw)

    monkeypatch.setattr(http, "fetch_to_temp", stopping)
    with SessionLocal() as db:  # ЖК - по порядку id: AB, затем Orda
        rep = gallery.fetch_all(db, [ORDA, AB], stop=stop)
    assert [r for _, r in rep] == [{"added": 0, "known": 0, "errors": [], "stopped": True}]  # Orda не начат
    assert len(fetched) == 1 and fetched[0] is not None and fetched[0]()  # загрузка файла тоже прерываема
    assert [x["kind"] for x in cx(client, AB)["gallery"] if x["kind"] == "homeportal"] == []
    n = len(http_mock.calls)
    with SessionLocal() as db:  # уже остановлен - ни одного запроса
        worker.gallery_sync(db, stop)
    assert len(http_mock.calls) == n
    monkeypatch.setattr(http, "fetch_to_temp", real)
    http_mock.on("GET", re.compile(r"api\.homeportal\.kz/api/v1/(objects-detail|illegal-objects)/(?!1329|1813)"),
                 status=503)
    with SessionLocal() as db:  # следующий такт - ЖК целиком
        worker.gallery_sync(db, threading.Event())
    assert [x["kind"] for x in cx(client, AB)["gallery"] if x["kind"] == "homeportal"] == ["homeportal"] * 2
    assert [x["kind"] for x in cx(client, ORDA)["gallery"]] == ["homeportal"] * 3


def test_stopped_sync_resumes_on_next_leader_tick(client, env, http_mock):
    """Подгрузка, прерванная остановкой воркера, не ждёт SAQ_GALLERY_SYNC_HOURS: отметка периода снимается, и первый
    же такт лидера(после перезапуска) проходит ЖК без фото; завершённая - снова раз в период."""
    import threading
    from datetime import timedelta

    from app import worker

    live(env)
    env(SAQ_GALLERY_SYNC_HOURS="24")
    hp(http_mock, "objects-detail/1329", "1329", 3)
    http_mock.on("GET", re.compile(r"api\.homeportal\.kz/api/v1/(objects-detail|illegal-objects)/(?!1329)"), status=503)

    def mark() -> tuple:
        with SessionLocal() as db:
            info = db.get(models.WorkerLease, worker.SCHEDULER).info or {}
        return info.get("gallerySyncAt"), info.get(worker.GALLERY_FROM)

    stop, now = threading.Event(), models.utcnow()
    stop.set()
    worker._tick("w-old", now, stop)
    started, rest = mark()
    assert started is not None and rest == "" and cx(client, ORDA)["gallery"] == []  # не начат ни один ЖК
    worker._tick("w-old", now + timedelta(minutes=1))  # тот же или новый лидер - сразу, а не через сутки
    assert [x["kind"] for x in cx(client, ORDA)["gallery"]] == ["homeportal"] * 3
    assert mark() == (started, None)  # проход завершён, период - от его начала
    n = len(http_mock.calls)
    worker._tick("w-old", now + timedelta(minutes=2))
    assert mark() == (started, None) and len(http_mock.calls) == n


def test_homeportal_unexpected_payload(client, env, http_mock):
    """Ответ API другой формы(images - не список, элементы - не объекты, чужие ссылки, длинный id) - не 500: годные
    фото загружаются, остальные пропускаются."""
    live(env)
    http_mock.on("GET", "https://api.homeportal.kz/api/v1/objects-detail/1329", json={"data": {"images": [
        "x", None, {"id": "a:b" * 50, "image_link": "https://api.homeportal.kz/storage/object-images/1329/img 0.png"},
        {"id": 7, "image_link": 12345}, {"id": 8, "image_link": "https://evil.example/x.png"},
        {"id": 9, "image_link": "https://[::1/x.png"}]}})
    http_mock.on("GET", re.compile(r"/storage/object-images/1329/img%200\.png$"), content=png_bytes((7, 7, 7)))
    r = client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR})
    assert r.status_code == 200, r.text
    assert [x["kind"] for x in r.json()["gallery"]] == ["homeportal"]
    with SessionLocal() as db:
        keys = list(db.scalars(select(models.MediaAsset.source_key).where(models.MediaAsset.source_key.like("homeportal:%"))))
    assert keys == [f"homeportal:{ORDA}:1329-0"]  # id с разделителями - объект и номер фото, а не id источника
    http_mock.on("GET", "https://api.homeportal.kz/api/v1/objects-detail/1329", json={"data": {"images": {"0": {}}}})
    assert client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR}).status_code == 200


def test_homeportal_concurrent_fetch_no_duplicates(client, env, http_mock, monkeypatch):
    """Регрессия: две подгрузки одного ЖК одновременно(повторное нажатие, такт воркера) добавляли каждое фото дважды
    - у фото Homeportal нет уникального ключа, а удалить их нельзя."""
    from app.integrations import http

    live(env)
    hp(http_mock, "objects-detail/1329", "1329", 2)
    real, raced = http.fetch_to_temp, []

    def racing(*a, **kw):
        if not raced:  # пока эта подгрузка качает первое фото, другая успевает записать всё
            raced.append(True)
            with SessionLocal() as other:
                assert dict(gallery.fetch_all(other, [ORDA]))["Orda"]["added"] == 2
        return real(*a, **kw)

    monkeypatch.setattr(http, "fetch_to_temp", racing)
    r = client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR})
    assert r.status_code == 200 and [x["kind"] for x in r.json()["gallery"]] == ["homeportal"] * 2
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(models.MediaAsset)
                      .where(models.MediaAsset.source_key.like(f"homeportal:{ORDA}:%")))
    assert n == 2


def test_homeportal_parallel_reads_before_commit_no_duplicates(client, env, http_mock):
    """Регрессия: обе подгрузки прочитали загруженные до записи любой из них - перед перечитыванием строка ЖК
    блокируется и на SQLite(get_for_update), вторая ждёт commit первой и видит её фото."""
    import threading

    live(env)
    hp(http_mock, "objects-detail/1329", "1329", 2)
    done, reps, errors = threading.Event(), [], []

    def other():
        try:
            with SessionLocal() as db:
                c = db.get(models.Complex, ORDA)
                reps.append(gallery.fetch_homeportal(db, c, gallery.homeportal_pages(db, c)))
                db.commit()
        except BaseException as e:  # noqa: BLE001 - ошибка потока - в основной поток теста
            errors.append(e)
        done.set()

    with SessionLocal() as db:
        c = db.get(models.Complex, ORDA)
        rep = gallery.fetch_homeportal(db, c, gallery.homeportal_pages(db, c))
        t = threading.Thread(target=other)
        t.start()
        assert not done.wait(0.5)  # вторая подгрузка ждёт блокировку первой
        db.commit()
    assert done.wait(10) and not errors, errors
    assert (rep["added"], reps[0]["added"], reps[0]["known"]) == (2, 0, 2)
    with SessionLocal() as db:
        n = db.scalar(select(func.count()).select_from(models.MediaAsset)
                      .where(models.MediaAsset.source_key.like(f"homeportal:{ORDA}:%")))
    assert n == 2


def test_homeportal_page_link_only_to_homeportal(client, env, http_mock):
    """Ссылка "страница источника" - только на сайт Homeportal: адрес, сохранённый до проверки ссылок
    (javascript:…//homeportal.kz/projects/1329), в карточку фото не попадает."""
    live(env)
    hp(http_mock, "objects-detail/1329", "1329", 2)
    assert client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR}).status_code == 200
    with SessionLocal() as db:
        a = db.scalars(select(models.MediaAsset).where(models.MediaAsset.source_key.like(f"homeportal:{ORDA}:%"))
                       .order_by(models.MediaAsset.position)).first()
        a.meta = {**a.meta, "page": "javascript:alert(1)//homeportal.kz/projects/1329"}
        db.commit()
        bad = a.id
    urls = {x["mediaId"]: x.get("url") for x in cx(client, ORDA)["gallery"]}
    assert urls.pop(bad) is None
    assert list(urls.values()) == ["https://homeportal.kz/ru/projects/1329"]


def test_homeportal_network_outside_transaction(client, env, http_mock, monkeypatch):
    """Загрузка фото идёт без открытой транзакции: соединение БД не занято на время сетевых запросов."""
    from app.integrations import http

    live(env)
    hp(http_mock, "objects-detail/1329", "1329", 1)
    real, seen = http.fetch_to_temp, []
    with SessionLocal() as db:
        def spy(*a, **kw):
            seen.append(db.in_transaction())
            return real(*a, **kw)

        monkeypatch.setattr(http, "fetch_to_temp", spy)
        c = db.get(models.Complex, ORDA)
        rep = gallery.fetch_homeportal(db, c, gallery.homeportal_pages(db, c))
        db.commit()
    assert seen == [False] and rep["added"] == 1


def test_homeportal_pages_capped_and_deduped_by_object():
    """Записи реестра загружает открытый импорт: ссылок у ЖК сколько угодно, а карточек Homeportal - не больше
    HP_PAGES_MAX, первые по порядку записей; тот же объект под другой ссылкой и ссылка без id объекта не считаются."""
    urls = ["https://homeportal.kz/ru/", "https://homeportal.kz/ru/projects/1329",
            "https://homeportal.kz/kz/projects/1329/", "https://www.homeportal.kz/ru/projects/1329", "https://homeportal.kz/ru/Illegal-projects/1329"]
    urls += [f"https://homeportal.kz/ru/projects/{i}" for i in range(1, 500)]
    recs = [{"source": "homeportal", "sourceUrl": u} for u in urls]
    assert gallery.homeportal_page_urls(recs) == ["https://homeportal.kz/ru/projects/1329",
                                                  "https://homeportal.kz/ru/Illegal-projects/1329",
                                                  "https://homeportal.kz/ru/projects/1"]
    assert not gallery.has_homeportal_pages(recs[:1])  # фото не загрузить - кнопки нет
    # id объекта - цифры ASCII без ведущих нулей: другая запись того же числа - тот же объект
    odd = ["https://homeportal.kz/ru/projects/٤٢", "https://homeportal.kz/ru/projects/" + "9" * 19,
           "https://homeportal.kz/ru/projects/0042", "https://homeportal.kz/ru/projects/42"]
    assert gallery.homeportal_page_urls({"source": "homeportal", "sourceUrl": u} for u in odd) == [odd[2]]
    assert gallery._api_path(odd[2]) == ("objects-detail/42", "42")


def test_imported_records_do_not_multiply_requests(client, env, http_mock):
    """Регрессия: сотни записей Homeportal одного ЖК(открытый импорт) - сотни запросов к Homeportal за одну подгрузку
    (POST .../gallery/fetch держал поток API, такт воркера - все задания)."""
    live(env)
    with SessionLocal() as db:
        R = models.SourceRecord
        key = db.scalars(select(R.key).where(R.source == "homeportal", R.source_url.like("%/projects/1329"))).first()
        db.add_all([R(key=key, source="homeportal", source_name="Homeportal — карточки ЖК", name="Orda",
                      source_record_id=f"x{i}", source_url=f"https://homeportal.kz/projects/{i}", row=10_000 + i)
                    for i in range(1, 200)])
        db.commit()
    hp(http_mock, "objects-detail/1329", "1329", 1)
    http_mock.on("GET", re.compile(r"api\.homeportal\.kz/api/v1/objects-detail/(?!1329)"), status=404)
    r = client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR})
    assert r.status_code == 200, r.text
    api = [str(c.url) for c in http_mock.calls if "/api/v1/" in str(c.url)]
    assert api == ["https://api.homeportal.kz/api/v1/objects-detail/1329",
                   "https://api.homeportal.kz/api/v1/objects-detail/1",
                   "https://api.homeportal.kz/api/v1/objects-detail/2"]
    assert [x["kind"] for x in r.json()["gallery"]] == ["homeportal"]


def test_homeportal_files_capped_per_fetch(client, env, http_mock, monkeypatch):
    """Новых файлов за одну подгрузку ЖК - не больше HP_FILES_MAX; остальные фото - следующей подгрузкой."""
    live(env)
    monkeypatch.setattr(gallery, "HP_FILES_MAX", 3)
    hp(http_mock, "objects-detail/1329", "1329", 5)
    files = re.compile(r"/storage/object-images/")
    r = client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR})
    assert r.status_code == 200 and len(r.json()["gallery"]) == 3
    assert sum(bool(files.search(str(c.url))) for c in http_mock.calls) == 3
    assert journal(ORDA)[-1][1].startswith("Фото объекта с Homeportal: добавлено 3")
    assert len(client.post(f"/api/complexes/{ORDA}/gallery/fetch", json={"author": AUTHOR}).json()["gallery"]) == 5


def test_sync_budget_continues_on_next_tick(client, env, http_mock, monkeypatch):
    """Такт лидера подгружает фото не дольше GALLERY_SYNC_S(новый ЖК после него не начинается): следующий такт
    продолжает со следующего ЖК, а не с начала - ЖК, чьи карточки не отдают фото, не занимают каждый такт."""
    from datetime import timedelta

    from app import worker

    live(env)
    env(SAQ_GALLERY_SYNC_HOURS="24")
    monkeypatch.setattr(worker, "GALLERY_SYNC_S", 0.0)  # бюджет исчерпан после первого же ЖК
    hp(http_mock, "objects-detail/1329", "1329", 1)
    hp(http_mock, "illegal-objects/1813", "1813", 1)
    http_mock.on("GET", re.compile(r"api\.homeportal\.kz/api/v1/(objects-detail|illegal-objects)/(?!1329|1813)"),
                 status=503)
    with SessionLocal() as db:
        ids = [c.id for c in db.scalars(select(models.Complex).order_by(models.Complex.id))
               if gallery.homeportal_pages(db, c)]
    assert len(ids) > 2 and AB in ids and ORDA in ids

    def rest() -> str | None:
        with SessionLocal() as db:
            return (db.get(models.WorkerLease, worker.SCHEDULER).info or {}).get(worker.GALLERY_FROM)

    now, seen = models.utcnow(), []
    for i, cid in enumerate(ids):
        n = len(http_mock.calls)
        worker._tick("w-g", now + timedelta(minutes=i))
        assert rest() == cid
        seen.append({str(c.url) for c in http_mock.calls[n:] if "/api/v1/" in str(c.url)})
    with SessionLocal() as db:  # каждый такт - карточки ровно одного ЖК, по порядку id
        pages = [gallery.homeportal_pages(db, db.get(models.Complex, cid)) for cid in ids]
    assert seen == [{f"{gallery.HP_API}/{gallery._api_path(u)[0]}" for u in p} for p in pages]
    worker._tick("w-g", now + timedelta(hours=1))  # ЖК после последнего нет - проход завершён
    assert rest() is None
    n = len(http_mock.calls)
    worker._tick("w-g", now + timedelta(hours=2))  # следующий проход - через период
    assert len(http_mock.calls) == n
    assert [x["kind"] for x in cx(client, AB)["gallery"] if x["kind"] == "homeportal"] == ["homeportal"]
    assert [x["kind"] for x in cx(client, ORDA)["gallery"]] == ["homeportal"]
