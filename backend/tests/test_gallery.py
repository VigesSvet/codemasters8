"""Фото объекта(app.gallery): фото оператора, обложка карточки, подгрузка фото карточек Homeportal(сети нет -
http_mock), порядок источников и подписи, журнал, пределы; на риск фото не влияют."""
import re

from sqlalchemy import select

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
