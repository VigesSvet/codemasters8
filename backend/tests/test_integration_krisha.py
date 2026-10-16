"""Сборщик Krisha.kz (integration-spec §12.6, §16.3 krisha, §16.5): без сети — httpx.MockTransport и синтетические
страницы tests/fixtures/krisha/ (написаны вручную, данные вымышленные)."""
import contextlib
import hashlib
import importlib.util
import json
import re
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, text

from app import models
from app.db import SessionLocal
from app.integrations import http, service
from conftest import png_bytes

FIX = Path(__file__).parent / "fixtures" / "krisha"
AUTHOR = "Омарова Ж."
SEARCH_URL = "https://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/"
ROBOTS_URL = "https://krisha.kz/robots.txt"
ROBOTS = "User-agent: *\nDisallow: /ajax/\nDisallow: /a/show-map/\nDisallow: /captcha\n"
IDS = ["900000001", "900000002", "900000003"]
CARD = re.compile(r"^https://krisha\.kz/a/show/(\d+)$")
PHOTO = re.compile(r"^https://(?:photos\.kcdn\.kz|krisha-photos\.kcdn\.online)/")
LIVE_CDN = "https://krisha-photos.kcdn.online/webp/90/"  # CDN фото живой выдачи (window.data.photoHost)
PREVIEW = LIVE_CDN + "900000001/1-280x175.webp"  # превью объявления 900000001 в выдаче (search.html)
HTML = {"content-type": "text/html; charset=utf-8"}
SECRETS = ("FAKE-APP-TOKEN", "FAKE-CONFIG-SECRET", "FAKE-CENTRIFUGE-TOKEN", "FAKE-SESSION-KEY", "Тестовый Пользователь",
           "fake-user@example.invalid", "+7 700 000 00 00", "+7 700 111 22 33", "203.0.113.7", "не сохранять")


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def card_html(eid: str = IDS[0]) -> str:
    return page("card.html").replace(IDS[0], eid)


def card_photo(name) -> str:
    """Фото карточки 900000001 на CDN (card.html: 1-, 2-, 3-750x470.webp)."""
    return LIVE_CDN + f"900000001/{name}-750x470.webp"


def photo(request: httpx.Request) -> httpx.Response:
    """Фото CDN: свой PNG для каждой ссылки (разный sha256)."""
    h = hashlib.sha256(str(request.url).encode()).digest()
    return httpx.Response(200, content=png_bytes((h[0], h[1], h[2])), headers={"content-type": "image/png"})


@pytest.fixture
def kr():
    from app.integrations import krisha

    return krisha


class Site:
    """Krisha.kz в тестах: robots.txt, выдача, карточки (200 — card.html с нужным ID, 468 — проверка доступа), фото."""

    def __init__(self, mock):
        self.mock = mock
        self.cards = "block"
        self.card_pages: dict[str, str] = {}

    def setup(self, *, search: str = "search.html", cards: str = "block", robots: str = ROBOTS) -> "Site":
        self.cards = cards
        self.mock.on("GET", ROBOTS_URL, content=robots.encode(), headers={"content-type": "text/plain"})
        self.mock.on("GET", SEARCH_URL, content=page(search).encode(), headers=HTML)
        self.mock.on("GET", PHOTO, handler=photo)
        self.mock.on("GET", CARD, handler=self.card)
        return self

    def search(self, html: str) -> None:
        self.mock.on("GET", SEARCH_URL, content=html.encode(), headers=HTML)

    def card(self, request: httpx.Request) -> httpx.Response:
        eid = CARD.match(str(request.url)).group(1)
        if self.cards == "block":
            return httpx.Response(468, content=page("safeline_468.html").encode(), headers=HTML)
        return httpx.Response(200, content=self.card_pages.get(eid, card_html(eid)).encode(), headers=HTML)

    def requests(self, pattern=None) -> list[str]:
        urls = [str(r.url) for r in self.mock.calls if r.url.host == "krisha.kz"]
        return [u for u in urls if pattern is None or re.search(pattern, u)]

    def photos(self) -> int:
        return sum(1 for r in self.mock.calls if r.url.host.endswith(("kcdn.kz", "kcdn.online")))

    def photo_urls(self) -> list[str]:
        return [str(r.url) for r in self.mock.calls if r.url.host.endswith(("kcdn.kz", "kcdn.online"))]


@pytest.fixture
def site(http_mock):
    return Site(http_mock)


def configure(**config) -> None:
    with SessionLocal() as db:
        row = service.row(db, "krisha", create=True)
        row.config = {"searchUrls": [SEARCH_URL], **config}
        db.commit()


def set_row(**values) -> None:
    with SessionLocal() as db:
        r = service.row(db, "krisha", create=True)
        for k, v in values.items():
            setattr(r, k, v)
        db.commit()


def run(run_worker, trigger: str = "manual", now=None) -> models.CollectionRun:
    with SessionLocal() as db:
        r, _ = service.request_run(db, "krisha", trigger=trigger, who=AUTHOR, now=now)
        db.commit()
        rid = r.id
    run_worker(now=now)
    with SessionLocal() as db:
        return db.get(models.CollectionRun, rid)


def row() -> models.Collector:
    with SessionLocal() as db:
        return service.row(db, "krisha")


def item(eid: str) -> models.IngestItem:
    with SessionLocal() as db:
        return db.scalar(select(models.IngestItem).where(models.IngestItem.platform == "krisha",
                                                         models.IngestItem.external_id == eid))


def versions(eid: str) -> list[models.IngestVersion]:
    with SessionLocal() as db:
        it = db.scalar(select(models.IngestItem).where(models.IngestItem.external_id == eid))
        return list(db.scalars(select(models.IngestVersion).where(models.IngestVersion.item_id == it.id)
                               .order_by(models.IngestVersion.version)))


def ad(eid: str) -> models.Ad:
    with SessionLocal() as db:
        return db.get(models.Ad, item(eid).ad_id)


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def assets(eid: str) -> dict[str, models.MediaAsset]:
    with SessionLocal() as db:
        return {a.source_key: a for a in db.scalars(select(models.MediaAsset)
                                                    .where(models.MediaAsset.item_id == item(eid).id))}


def run_notes(run_id: int) -> list[str]:
    with SessionLocal() as db:
        return [n["text"] for n in db.get(models.CollectionRun, run_id).notes]


def db_dump() -> str:
    """Все строки всех таблиц приложения одной строкой (поиск утечек)."""
    out = []
    with SessionLocal() as db:
        for table in models.Base.metadata.sorted_tables:
            for r in db.execute(select(table)):
                out.append(json.dumps([str(v) for v in r], ensure_ascii=False))
    return "\n".join(out)


# ---------- 1. window.data без JavaScript, очистка ----------

def test_window_data_parsed_without_js_and_sanitized(kr):
    html = page("search.html")
    raw = kr.extract_window_data(html)
    # «window.data == null» и «window.data.search.nbTotal = 999» — не присваивания: JS не выполнялся
    assert raw["search"]["nbTotal"] == "3" and raw["appToken"] == "FAKE-APP-TOKEN"
    clean = kr.sanitize_window_data(raw)
    assert set(clean) == {"search", "adverts"}  # photoHost, user, config, app*… — не сохраняются
    assert clean["search"] == {"nbTotal": 3, "ids": IDS, "page": 1}
    assert clean["adverts"] == [{"id": 900000003, "userType": "company", "isHot": False}]
    dumped = json.dumps(clean, ensure_ascii=False)
    assert not any(s in dumped for s in SECRETS)
    card = kr.sanitize_window_data(kr.extract_window_data(page("card.html")))
    assert set(card) == {"advert"} and "ownerPhone" not in card["advert"] and "sessionKey" not in card["advert"]
    assert kr.extract_window_data("<script>window.data = broken;</script>") is None
    assert kr.extract_window_data("<html>нет данных</html>") is None
    assert kr.sanitize_window_data(["не объект"]) == {}


def test_nb_total_with_digit_grouping(kr):
    """Живая выдача: nbTotal — строка с разделителем разрядов; общее число не теряется, мусор не принимается."""
    for value, expected in (("1 234", 1234), ("12\u00a0345", 12345), ("1\u202f000", 1000), (" 7 ", 7), (42, 42),
                            ("²", None), ("12a", None), ("", None), (True, None)):
        got = kr.sanitize_window_data({"search": {"nbTotal": value}})["search"].get("nbTotal")
        assert got == expected, (value, got)


def test_collect_stores_no_page_secrets(client, kr, site, run_worker, ocr_stub):
    site.setup(cards="ok")
    configure()
    r = run(run_worker)
    assert r.status == "succeeded" and r.items_new == 3
    dump = db_dump()
    assert not any(s in dump for s in SECRETS), [s for s in SECRETS if s in dump]
    assert "window.data" not in dump


# ---------- 2. разбор выдачи ----------

def test_parse_search_fields_hot_and_dedupe(kr):
    html = page("search.html")
    data, cards = kr.parse_search(html)
    assert [c["id"] for c in cards] == IDS
    first = cards[0]
    assert first == {"id": "900000001", "uuid": "00000000-aaaa-4bbb-8ccc-000000000001",
                     "title": "3-комнатная квартира · 86 м² · 5/9 этаж", "price": "31 500 000 ₸",
                     "address": "Усть-Каменогорск, Тестовая улица 1",
                     "preview": "Квартира в ЖК Ульба Ривер, черновая отделка, сдача в 2027 году",
                     "complex": "Ульба Ривер", "sellerType": "complex",
                     "photo": LIVE_CDN + "900000001/1-280x175.webp", "hot": True}
    assert [c["hot"] for c in cards] == [True, False, False]
    # тип продавца: класс user-<тип> подвала (1, 2), window.data.adverts (3)
    assert [c["sellerType"] for c in cards] == ["complex", "owner", "company"]
    assert [c["complex"] for c in cards] == ["Ульба Ривер", None, None]
    assert cards[1]["photo"] == "https://photos.kcdn.kz/test/900000002-0-280x175.jpg"  # ленивая загрузка (data-src)
    assert cards[2]["photo"] is None and cards[2]["price"] == "от 21 000 000 ₸"
    # блок «горячих» сверху повторяет карточку — она одна
    start = html.index('<div class="a-card a-storage-live ddl_product" data-id="900000002"')
    block = html[start:html.index('<div class="a-card a-storage-live ddl_product" data-id="900000003"')]
    _, again = kr.parse_search(html.replace('<section class="a-list">', '<section class="a-list">' + block))
    assert [c["id"] for c in again] == ["900000002", "900000001", "900000003"]
    assert data["search"]["ids"] == IDS


def test_parse_search_live_seller_and_complex_markup(kr):
    """Разметка живой выдачи (только имена классов): тип продавца — класс user-<тип> у подвала, ЖК — ссылка в блоке
    a-card__complex-info; подпись «Новостройка» — не название ЖК; без ссылки — текст блока без подписи и застройщика."""
    def one(footer: str) -> dict:
        html = ('<script>window.data = {"search": {"ids": [900000041]}};</script>'
                '<div class="a-card" data-id="900000041"><a class="a-card__title">Квартира</a>'
                f'<div class="a-card__price">1 000 000 〒</div>{footer}</div>')
        _, cards = kr.parse_search(html)
        return cards[0]

    c = one('<div class="a-card__footer user-complex"><div class="a-card__owner a-card__complex-info">'
            '<div class="a-card__complex-label">Новостройка</div>'
            '<a href="/complex/show/ust-kamenogorsk/x/">ЖК Ульба Ривер</a> от застройщика «Тест»</div></div>')
    assert (c["complex"], c["sellerType"]) == ("Ульба Ривер", "complex")
    assert kr._account(kr._summary(c)) == "krisha.kz / Ульба Ривер"
    c = one('<div class="a-card__footer user-complex"><div class="a-card__complex-info">'
            '<div class="a-card__complex-label">Новостройка</div> ЖК Тестовый Квартал'
            ' от застройщика «Тест»</div></div>')
    assert c["complex"] == "Тестовый Квартал"
    c = one('<div class="a-card__footer user-complex"><div class="a-card__complex-info">'
            '<div class="a-card__complex-label">Новостройка</div> от застройщика «Тест»</div></div>')
    assert c["complex"] is None and kr._account(kr._summary(c)) == "krisha.kz / застройщик/ЖК"
    for cls, label in (("user-owner", "хозяин"), ("user-company", "агентство"), ("user-specialist", "специалист")):
        c = one(f'<div class="a-card__footer {cls}"><div class="a-card__owner user-title-pro user-label-x">'
                '<span class="a-card__owner-label">Подпись</span></div></div>')
        assert kr._account(kr._summary(c)) == f"krisha.kz / {label}"
    # классы user-* у вложенных элементов (user-title-pro, user-label-owner) типом продавца не считаются
    c = one('<div class="a-card__footer"><div class="a-card__owner user-title-pro user-label-owner"></div></div>')
    assert c["sellerType"] is None and kr._account(kr._summary(c)) == "krisha.kz / продавец не указан"


def test_parse_card_whitelist_and_documents(kr):
    c = kr.parse_card(page("card.html"))
    assert (c["id"], c["price"], c["complex"], c["developer"]) == ("900000001", "31 500 000 ₸", "Ульба Ривер",
                                                                   "ТОО «Тестовый застройщик»")
    assert c["description"].startswith("Продаётся просторная квартира в ЖК Ульба Ривер.\nЧерновая отделка")
    assert c["documents"] == ["Разрешение на строительство № ТЕСТ-0001 (заявлено продавцом)",
                              "Договор долевого участия"]
    assert "Разрешение" not in c["description"] and "Просмотров" not in c["params"]
    assert (c["createdAt"], c["addedAt"], c["lat"], c["lon"]) == ("2026-09-01", "2026-10-10", 49.9, 82.6)
    assert c["photos"] == [LIVE_CDN + f"900000001/{n}-750x470.webp" for n in (1, 2, 3)]


# ---------- 3. проверка доступа ----------

def test_detect_block(kr):
    challenge = page("safeline_468.html")
    assert kr.detect_block(468, httpx.Headers(), "") == "HTTP 468, SafeLine"
    assert kr.detect_block(403, httpx.Headers(), challenge) == "HTTP 403, SafeLine"
    assert kr.detect_block(429, {}, "<p>Please solve the CAPTCHA</p>") == "HTTP 429, CAPTCHA"
    assert kr.detect_block(503, httpx.Headers([("set-cookie", "sl-session=abc; Path=/")]), "") == "HTTP 503, SafeLine"
    assert kr.detect_block(200, {}, challenge) == "HTTP 200, SafeLine"
    # обычные страницы: слово captcha в скрипте входа при наличии window.data — не проверка
    normal = page("search.html")
    assert kr.detect_block(200, httpx.Headers(), normal) is None
    assert kr.detect_block(200, {}, normal + "<script>loadRecaptcha()</script>") is None
    assert kr.detect_block(200, httpx.Headers([("set-cookie", "sl-session=x")]), normal) is None
    assert kr.detect_block(404, {}, challenge) is None
    # robots.txt со строкой «Disallow: /captcha» и cookie sl-session — не проверка доступа
    assert kr.detect_block(200, httpx.Headers([("set-cookie", "sl-session=x")]), ROBOTS) is None
    assert kr.detect_block(403, {}, "<html>Forbidden</html>") is None


# ---------- 4. проверка подключения ----------

def test_check_search_ok_card_blocked_is_ready(client, kr, site, run_worker):
    site.setup(cards="block")
    r = run(run_worker, trigger="test")
    assert r.status == "succeeded"
    c = row()
    assert c.status == "ready"
    assert c.status_detail == ("Выдача доступна; карточки объявлений закрыты проверкой доступа (HTTP 468, SafeLine) — "
                               "собираются данные из выдачи (объявлений в выдаче: 3, разобрано на странице: 3)")
    assert c.facts["searchTotal"] == 3
    assert (c.facts["search"], c.facts["card"], c.facts["searchCards"]) == ("ok", "access_check", 3)
    assert c.facts["cardUrl"] == kr.CONTROL_CARD and c.facts["playwright"] == "не выполнялась"
    assert site.requests() == [ROBOTS_URL, kr.DEFAULT_SEARCH, kr.CONTROL_CARD]


def test_check_card_ok_and_search_blocked(client, kr, site, run_worker):
    site.setup(cards="ok")
    configure(cardUrls=["https://krisha.kz/a/show/900000001"])
    assert run(run_worker, trigger="test").status == "succeeded"
    assert row().facts["card"] == "ok" and row().status_detail.startswith("Выдача и карточки объявлений доступны")
    site.mock.on("GET", SEARCH_URL, status=468, content=page("safeline_468.html").encode(), headers=HTML)
    r = run(run_worker, trigger="test")
    assert (r.status, r.error_code) == ("access_check", "access_check")
    c = row()
    assert c.status == "access_check" and "Krisha.kz отвечает проверкой доступа (HTTP 468, SafeLine)" in c.status_detail
    assert "обход проверок не выполняется" in c.status_detail


def test_check_smoke_ephemeral(client, kr, site):
    from app.integrations import smoke
    from app.integrations.context import RunContext

    site.setup(cards="block")
    ctx = RunContext.ephemeral("krisha")
    res = kr.CONNECTOR.check(ctx)
    assert res.ok and res.facts["card"] == "access_check" and "robots" in ctx.state
    out = smoke.check_one("krisha")
    assert out.startswith("krisha: готов к сбору — Выдача доступна; карточки объявлений закрыты проверкой доступа "
                          "(HTTP 468, SafeLine)")


def test_check_without_total_does_not_invent_it(client, kr, site):
    """Нет nbTotal (или он не разобран) — в итоге проверки не выдаётся число карточек страницы за общее число выдачи."""
    from app.integrations.context import RunContext

    site.setup(cards="block")
    site.search(page("search.html").replace('"nbTotal": "3", ', ""))
    res = kr.CONNECTOR.check(RunContext.ephemeral("krisha"))
    assert res.ok and res.facts["searchTotal"] is None and res.facts["searchCards"] == 3
    assert "(разобрано объявлений на странице: 3, всего в выдаче — не указано)" in res.detail
    assert "объявлений в выдаче:" not in res.detail


def test_check_without_window_data_is_error(client, kr, site, run_worker):
    site.setup()
    site.search("<html><body><div class='a-card' data-id='1'></div></body></html>")
    r = run(run_worker, trigger="test")
    assert r.status == "failed" and r.error == "Структура страницы Krisha изменилась — window.data не найден"
    assert row().status == "error"


# ---------- 5. сбор: выдача есть, карточки закрыты ----------

def test_collect_search_ok_cards_blocked_partial(client, kr, site, run_worker, ocr_stub, accept):
    site.setup(cards="block")
    configure()
    r = run(run_worker)
    assert (r.status, r.items_new, r.items_failed, r.media_saved) == ("partial", 3, 0, 2)
    c = row()
    assert c.status == "partial"
    assert c.status_detail == "Карточки закрыты проверкой доступа (HTTP 468, SafeLine) — сохранены данные из выдачи"
    assert len(site.requests(r"/a/show/")) == 1  # после первой проверки карточки больше не запрашиваются
    a = ad(IDS[0])
    assert (a.url, a.account, a.origin, a.source, a.platform) == (
        "https://krisha.kz/a/show/900000001", "krisha.kz / Ульба Ривер", "collected", "parser", "krisha")
    assert a.text == ("3-комнатная квартира · 86 м² · 5/9 этаж\nУсть-Каменогорск, Тестовая улица 1\n"
                      "Квартира в ЖК Ульба Ривер, черновая отделка, сдача в 2027 году")
    assert a.price == "31 500 000 ₸" and a.image and a.image.startswith("/api/media/")
    assert [ad(i).account for i in IDS[1:]] == ["krisha.kz / хозяин", "krisha.kz / агентство"]
    v = versions(IDS[0])[0]
    key = kr.photo_key(IDS[0], PREVIEW, cover=True)
    assert v.extra["source"] == "search" and v.raw["card"]["hot"] is True and v.media_keys == [key]
    cover = assets(IDS[0])[key]
    assert (cover.status, cover.role, cover.origin_url) == ("downloaded", "cover", PREVIEW)
    assert PREVIEW in site.photo_urls()
    # повтор: без дубликатов и без загрузок фото
    before, photos = accept.snapshot(), site.photos()
    r2 = run(run_worker)
    assert (r2.status, r2.items_new, r2.items_unchanged) == ("partial", 0, 3)
    assert accept.snapshot() == before and site.photos() == photos


# ---------- 6. карточки доступны: версии и документы ----------

def change_events() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(models.AuditEvent)
                         .where(models.AuditEvent.what.like("Публикация изменилась в источнике%")))


def test_cards_ok_versions_documents_and_no_flip_flop(client, kr, site, run_worker, ocr_stub):
    site.setup(cards="ok")
    configure()
    r = run(run_worker)
    # карточка получена в том же запуске: новая публикация сразу с данными карточки — одна версия, учтена один раз
    assert (r.status, r.items_seen, r.items_new, r.items_updated) == ("succeeded", 3, 3, 0)
    assert change_events() == 0
    vs = versions(IDS[0])
    assert [v.version for v in vs] == [1] and vs[0].extra["source"] == "card"
    a = ad(IDS[0])
    assert "Продаётся просторная квартира в ЖК Ульба Ривер." in a.text and "Разрешение" not in a.text
    assert vs[0].extra["documents"] == vs[0].raw["documents"] == [
        "Разрешение на строительство № ТЕСТ-0001 (заявлено продавцом)", "Договор долевого участия"]
    assert (vs[0].extra["createdAt"], vs[0].extra["addedAt"]) == ("2026-09-01", "2026-10-10")
    assert item(IDS[0]).published_at is None
    # превью выдачи (krisha:{id}:c:…) и фото карточки (krisha:{id}:p:…) в порядке галереи — разные файлы, хотя превью —
    # тот же снимок, что и фото 1: главное фото карточки загружено, превью не подменяет его
    cover, slides = kr.photo_key(IDS[0], PREVIEW, cover=True), [kr.photo_key(IDS[0], card_photo(n)) for n in (1, 2, 3)]
    assert vs[0].media_keys == [cover] + slides
    got = assets(IDS[0])
    assert (got[cover].role, got[cover].origin_url, got[cover].position) == ("cover", PREVIEW, 0)
    assert [(got[k].origin_url, got[k].role, got[k].position) for k in slides] == [
        (card_photo(n), "slide", n) for n in (1, 2, 3)]
    assert all(x.status == "downloaded" for x in got.values())
    assert card_photo(1) in site.photo_urls()
    assert a.image == f"/api/media/{got[slides[0]].id}"  # изображение публикации — фото карточки
    assert vs[0].extra["developer"] == "ТОО «Тестовый застройщик»" and vs[0].extra["price"] == "31 500 000 ₸"
    # второй сбор подряд (выдача и карточки — 200): всё без изменений, версий по-прежнему 1
    cards_before = len(site.requests(r"/a/show/"))
    r2 = run(run_worker)
    assert (r2.status, r2.items_seen, r2.items_new, r2.items_updated, r2.items_unchanged) == ("succeeded", 3, 0, 0, 3)
    assert [len(versions(i)) for i in IDS] == [1, 1, 1]
    assert len(site.requests(r"/a/show/")) == cards_before  # неизменённые карточки не перечитываются
    # цена изменилась в выдаче → карточка перечитана, ровно одна новая версия с описанием карточки
    site.search(page("search.html").replace("31&nbsp;500&nbsp;000", "30&nbsp;900&nbsp;000"))
    r3 = run(run_worker)
    assert (r3.items_seen, r3.items_new, r3.items_updated, r3.items_unchanged) == (3, 0, 1, 2)
    assert len(site.requests(r"/a/show/")) == cards_before + 1
    vs = versions(IDS[0])
    assert [v.version for v in vs] == [1, 2] and vs[1].price == "30 900 000 ₸"
    assert vs[1].text == vs[0].text and vs[1].media_keys == vs[0].media_keys
    assert ad(IDS[0]).price == "30 900 000 ₸" and "Продаётся просторная квартира" in ad(IDS[0]).text
    assert change_events() == 1


def test_search_change_one_version_per_run(client, kr, site, run_worker, ocr_stub):
    """Заголовок изменился в выдаче: карточка доступна — одна новая версия с новым заголовком; карточка закрыта —
    одна версия из выдачи, текст собран из нового заголовка и сохранённого описания (а не «старый текст + новый
    extra»)."""
    site.setup(cards="ok")
    configure()
    run(run_worker)
    old, new = "3-комнатная квартира · 86 м² · 5/9 этаж", "3-комнатная квартира · 87 м² · 5/9 этаж"
    site.search(page("search.html").replace(old, new))
    r = run(run_worker)
    assert (r.items_updated, r.items_unchanged) == (1, 2)
    vs = versions(IDS[0])
    assert [(v.version, v.extra["source"]) for v in vs] == [(1, "card"), (2, "card")]
    assert vs[1].text.splitlines()[0] == new and vs[1].extra["title"] == new
    assert "Продаётся просторная квартира" in vs[1].text
    # карточки закрыты: выдача снова изменилась → одна версия «выдача + сохранённая карточка»
    site.cards = "block"
    newer = "3-комнатная квартира · 88 м² · 5/9 этаж"
    site.search(page("search.html").replace(old, newer).replace("Тестовая улица 1", "Тестовая улица 1А"))
    r = run(run_worker)
    assert (r.status, r.items_updated, r.items_unchanged) == ("partial", 1, 2)
    vs = versions(IDS[0])
    assert len(vs) == 3 and vs[2].raw.get("merged") is True
    assert vs[2].text.splitlines()[:2] == [newer, "Усть-Каменогорск, Тестовая улица 1А"]
    assert vs[2].text.splitlines()[2:] == vs[1].text.splitlines()[2:]  # описание карточки сохранено
    assert (vs[2].extra["title"], vs[2].extra["source"], vs[2].media_keys) == (newer, "card", vs[1].media_keys)
    # карточки снова доступны, выдача та же: сводка совпадает — карточка не перечитывается, версий не добавляется
    site.cards = "ok"
    before = len(site.requests(r"/a/show/"))
    r = run(run_worker)
    assert (r.items_updated, r.items_unchanged) == (0, 3) and len(versions(IDS[0])) == 3
    assert len(site.requests(r"/a/show/")) == before


def test_card_urls_do_not_starve_search_only(client, kr, site, run_worker, ocr_stub, env):
    """Ссылки на объявления из настроек не занимают весь предел KRISHA_MAX_CARDS: после первой версии карточки они
    перечитываются после объявлений, у которых есть только данные выдачи."""
    env(KRISHA_MAX_CARDS="1")
    site.setup(cards="ok")
    configure(cardUrls=["https://krisha.kz/a/show/900000099"])
    for _ in range(5):
        run(run_worker)
    assert [u.rsplit("/", 1)[1] for u in site.requests(r"/a/show/")] == [
        IDS[0], "900000099", IDS[1], IDS[2], "900000099"]
    assert [versions(i)[-1].extra["source"] for i in IDS + ["900000099"]] == ["card"] * 4


# ---------- 6а. продавец заменил или удалил фото: ключ файла — само фото, а не его место ----------

def test_photo_key_is_photo_identity_not_position(kr):
    key = kr.photo_key
    # тот же снимок в другом размере, формате, на другом CDN или с query — тот же ключ
    same = [card_photo(1), card_photo(1).replace("-750x470", "-full"), PREVIEW,
            "https://photos.kcdn.kz/jpg/90/900000001/1-750x470.jpg?v=2"]
    assert len({key(IDS[0], u) for u in same}) == 1
    # другой снимок, другое объявление, превью выдачи — другие ключи
    assert len({key(IDS[0], card_photo(1)), key(IDS[0], card_photo(2)), key(IDS[0], card_photo("9-NEW")),
                key(IDS[1], card_photo(1)), key(IDS[0], PREVIEW, cover=True)}) == 5
    assert re.fullmatch(r"krisha:900000001:p:[0-9a-f]{16}", key(IDS[0], card_photo(1)))
    assert re.fullmatch(r"krisha:900000001:c:[0-9a-f]{16}", key(IDS[0], PREVIEW, cover=True))


def drop_card_photo(html: str, name: str) -> str:
    out = re.sub(r',\s*\{"src": "[^"]*/' + re.escape(name) + r'-750x470\.webp"[^}]*\}', "", html)
    assert out != html
    return out


def test_card_photo_replaced_or_deleted_is_new_version(client, kr, site, run_worker, ocr_stub):
    """Карточка из настроек (перечитывается каждый запуск): продавец заменил фото 1, потом удалил фото 2. Каждая правка —
    новая версия; новое фото загружено, заменённое и удалённое — не текущие; оставшиеся не перезагружаются и остаются
    текущими со своими байтами (а не байтами соседнего фото)."""
    eid = IDS[0]
    site.setup(cards="ok")
    configure(cardUrls=[f"https://krisha.kz/a/show/{eid}"])
    run(run_worker)
    cover = kr.photo_key(eid, PREVIEW, cover=True)
    p = {n: kr.photo_key(eid, card_photo(n)) for n in (1, 2, 3, 9)}
    first = assets(eid)
    assert versions(eid)[0].media_keys == [cover, p[1], p[2], p[3]]

    site.card_pages[eid] = card_html(eid).replace(card_photo(1), card_photo(9))
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.status, r.items_updated, r.items_unchanged) == ("succeeded", 1, 2)
    assert site.photo_urls() == [card_photo(9)]  # загружено только новое фото
    vs = versions(eid)
    assert [v.version for v in vs] == [1, 2] and vs[1].media_keys == [cover, p[9], p[2], p[3]]
    got = assets(eid)
    assert not got[p[1]].current and (got[p[9]].status, got[p[9]].current, got[p[9]].position) == ("downloaded", True, 1)
    assert got[p[9]].origin_url == card_photo(9) and got[p[9]].sha256 != first[p[1]].sha256
    assert all(got[k].current and got[k].sha256 == first[k].sha256 for k in (cover, p[2], p[3]))
    assert ad(eid).image == f"/api/media/{got[p[9]].id}"  # изображение публикации — новое главное фото
    assert change_events() == 1

    site.card_pages[eid] = drop_card_photo(card_html(eid).replace(card_photo(1), card_photo(9)), "2")
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.items_updated, r.items_unchanged) == (1, 2) and not site.photo_urls()
    vs = versions(eid)
    assert [v.version for v in vs] == [1, 2, 3] and vs[2].media_keys == [cover, p[9], p[3]]
    got = assets(eid)
    assert {k for k, a in got.items() if a.current} == {cover, p[9], p[3]}
    assert (got[p[3]].sha256, got[p[3]].position, got[p[3]].origin_url) == (first[p[3]].sha256, 2, card_photo(3))
    # история: прежние версии по-прежнему ссылаются на свои файлы
    assert vs[0].media_keys == [cover, p[1], p[2], p[3]] and got[p[1]].status == got[p[2]].status == "downloaded"
    # повтор без правок — без версий и загрузок
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.items_updated, r.items_unchanged) == (0, 3) and not site.photo_urls() and len(versions(eid)) == 3


def test_search_preview_replaced_cards_blocked_is_new_version(client, kr, site, run_worker, ocr_stub):
    """Карточки закрыты проверкой доступа (живая ситуация), продавец заменил главное фото — в выдаче новое превью: новая
    версия, новое превью загружено, прежнее не текущее, объявление первым в очереди карточек. Тот же снимок в другом
    размере — не правка."""
    eid = IDS[0]
    site.setup(cards="block")
    configure()
    run(run_worker)
    old, new_url = kr.photo_key(eid, PREVIEW, cover=True), LIVE_CDN + f"{eid}/7-NEWCOVER-280x175.webp"
    new = kr.photo_key(eid, new_url, cover=True)
    site.search(page("search.html").replace(PREVIEW, new_url))
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.status, r.items_updated, r.items_unchanged) == ("partial", 1, 2)
    assert site.photo_urls() == [new_url]
    assert site.requests(r"/a/show/") == [f"https://krisha.kz/a/show/{eid}"]  # карточка — на перечитывание
    vs = versions(eid)
    assert [(v.version, v.media_keys) for v in vs] == [(1, [old]), (2, [new])]
    got = assets(eid)
    assert not got[old].current and (got[new].status, got[new].current, got[new].role) == ("downloaded", True, "cover")
    assert ad(eid).image == f"/api/media/{got[new].id}"
    # то же фото в другом размере: без версии и без загрузки
    site.search(page("search.html").replace(PREVIEW, LIVE_CDN + f"{eid}/7-NEWCOVER-400x250.webp"))
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.items_updated, r.items_unchanged) == (0, 3) and not site.photo_urls() and len(versions(eid)) == 2


def test_search_preview_replaced_refetches_card(client, kr, site, run_worker, ocr_stub):
    """Объявление найдено поиском, версия карточки есть, сводка выдачи прежняя; продавец заменил главное фото — в
    выдаче новое превью: карточка перечитывается, одна новая версия с новым превью и новым фото карточки. Карточки
    закрыты — одна версия из выдачи: новое превью, фото и описание карточки прежние."""
    eid = IDS[0]
    site.setup(cards="ok")
    configure()
    run(run_worker)
    preview9 = LIVE_CDN + f"{eid}/9-280x175.webp"
    site.search(page("search.html").replace(PREVIEW, preview9))
    site.card_pages[eid] = card_html(eid).replace(card_photo(1), card_photo(9))
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.status, r.items_updated, r.items_unchanged) == ("succeeded", 1, 2)
    assert site.requests(r"/a/show/") == [f"https://krisha.kz/a/show/{eid}"]
    assert sorted(site.photo_urls()) == sorted([preview9, card_photo(9)])
    vs = versions(eid)
    assert [(v.version, v.extra["source"], v.raw["source"]) for v in vs] == [(1, "card", "card"), (2, "card", "card")]
    assert vs[1].media_keys == [kr.photo_key(eid, preview9, cover=True)] + [
        kr.photo_key(eid, card_photo(n)) for n in (9, 2, 3)]
    assert {k for k, a in assets(eid).items() if a.current} == set(vs[1].media_keys)

    site.cards = "block"
    preview5 = LIVE_CDN + f"{eid}/5-280x175.webp"
    site.search(page("search.html").replace(PREVIEW, preview5))
    site.mock.calls.clear()
    r = run(run_worker)
    assert (r.status, r.items_updated, r.items_unchanged) == ("partial", 1, 2)
    assert site.requests(r"/a/show/") == [f"https://krisha.kz/a/show/{eid}"] and site.photo_urls() == [preview5]
    vs = versions(eid)
    assert len(vs) == 3 and vs[2].raw.get("merged") is True and vs[2].extra["source"] == "card"
    assert vs[2].media_keys == [kr.photo_key(eid, preview5, cover=True)] + vs[1].media_keys[1:]
    assert vs[2].text == vs[1].text and "Продаётся просторная квартира" in vs[2].text
    assert {k for k, a in assets(eid).items() if a.current} == set(vs[2].media_keys)


# ---------- 7. изменилась разметка ----------

def test_changed_markup_fails_run(client, kr, site, run_worker, accept):
    site.setup(search="search_changed.html")
    configure()
    r = run(run_worker)
    assert r.status == "failed"
    assert r.error == "Структура выдачи Krisha изменилась: в window.data 2 объявлений, разобрано 0"
    c = accept.assert_failure_visible("krisha", r.id)
    assert c["status"] == "error" and count(models.IngestItem) == 0


def test_markup_change_detected_by_links_when_window_data_has_no_count(client, kr, site, run_worker):
    site.setup()
    site.search('<script>window.data = {"search": {"page": 1}};</script>'
                '<article><a href="/a/show/900000021">Квартира</a></article>'
                '<article><a href="https://krisha.kz/a/show/900000022">Квартира</a></article>')
    configure()
    r = run(run_worker)
    assert (r.status, r.error) == ("failed", "Структура выдачи Krisha изменилась: на странице 2 ссылок на "
                                             "объявления, разобрано 0")


def test_empty_search_is_succeeded_with_zero(client, kr, site, run_worker):
    site.setup()
    site.search('<script>window.data = {"search": {"nbTotal": "0", "ids": []}};</script>'
                '<aside><a href="/a/show/900000031">Похожее объявление</a></aside>')
    configure()
    r = run(run_worker)
    assert (r.status, r.items_seen) == ("succeeded", 0)  # «нет новых» — только когда выдача правда пуста


def test_pagination_keeps_query_and_stops(client, kr, site, run_worker, ocr_stub, env):
    env(KRISHA_MAX_PAGES="3")
    url = SEARCH_URL + "?das[live.rooms]=3"
    no_total = page("search.html").replace('"nbTotal": "3", ', "")

    def pages(request: httpx.Request) -> httpx.Response:
        n = request.url.params.get("page")
        if n is None:
            html = no_total
        elif n == "2":
            html = no_total.replace("9000000", "9100000")  # другие ID на второй странице
        else:
            html = no_total.replace("9000000", "9100000")  # третья: сайт отдаёт уже прочитанную страницу
        return httpx.Response(200, content=html.encode(), headers=HTML)

    site.setup(cards="block")
    site.mock.on("GET", re.compile(r"^https://krisha\.kz/prodazha/kvartiry/ust-kamenogorsk/\?"), handler=pages)
    configure(searchUrls=[url])
    r = run(run_worker)
    assert r.items_new == 6 and r.status == "partial"
    search_calls = [c for c in site.mock.calls if c.url.path == "/prodazha/kvartiry/ust-kamenogorsk/"]
    assert [c.url.params.get("page") for c in search_calls] == [None, "2", "3"]
    assert all(c.url.params.get("das[live.rooms]") == "3" for c in search_calls)


def test_robots_challenge_is_access_check(client, kr, site, run_worker):
    site.setup()
    site.mock.on("GET", ROBOTS_URL, status=468, content=page("safeline_468.html").encode(), headers=HTML)
    configure()
    r = run(run_worker)
    assert r.status == "access_check" and site.requests() == [ROBOTS_URL]


def test_search_url_404_is_failure_not_empty(client, kr, site, run_worker, accept):
    site.setup()
    site.mock.on("GET", SEARCH_URL, status=404, content=b"<html>not found</html>", headers=HTML)
    configure()
    r = run(run_worker)
    assert r.status == "failed" and r.error.startswith("Все 1 записей с ошибкой: Поиск Krisha")
    accept.assert_failure_visible("krisha", r.id)


# ---------- 8. ограничение частоты ----------

def test_rate_limited_429(client, kr, site, run_worker):
    site.setup()
    site.mock.on("GET", SEARCH_URL, status=429, headers={"retry-after": "120"})
    configure()
    now = models.utcnow()
    r = run(run_worker, now=now)
    assert (r.status, r.error_code) == ("rate_limited", "rate_limited")
    c = row()
    assert c.status == "rate_limited"
    assert abs((models.as_utc(c.next_run_at) - now).total_seconds() - 120) < 5
    assert site.requests(r"/a/show/") == []


# ---------- 9. настройки и robots.txt ----------

def test_config_validation(client, kr, admin_headers):
    def patch(cfg):
        return client.patch("/api/sources/krisha", headers=admin_headers, json={"author": AUTHOR, "config": cfg})

    ok = patch({"searchUrls": ["https://www.krisha.kz/prodazha/kvartiry/ust-kamenogorsk/?das[live.rooms]=3#x",
                               "https://krisha.kz/arenda/kvartiry/ust-kamenogorsk/"],
                "cardUrls": ["https://krisha.kz/a/show/1009924986?srchid=1", "https://krisha.kz/a/show/1009924986"]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["config"] == {
        "searchUrls": ["https://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/?das[live.rooms]=3",
                       "https://krisha.kz/arenda/kvartiry/ust-kamenogorsk/"],
        "cardUrls": ["https://krisha.kz/a/show/1009924986"]}
    bad = {
        "searchUrls": ["https://evil.example/prodazha/kvartiry/", "http://krisha.kz/prodazha/kvartiry/",
                       "https://krisha.kz/prodazha/kvartiry/?till=1", "https://krisha.kz/prodazha/kvartiry/?gclid=x",
                       "https://krisha.kz/ajax/prodazha/", "https://krisha.kz/a/show/1009924986",
                       "https://krisha.kz:8443/prodazha/kvartiry/", "https://user@krisha.kz/prodazha/kvartiry/",
                       "https://krisha.kz/prodazha/kvartiry/?utm_source=x",
                       # сегменты «.»/«..»: httpx схлопывает их при запросе — раздел и robots.txt проверялись бы не там
                       "https://krisha.kz/prodazha/../my/", "https://krisha.kz/prodazha/%2e%2e/my/",
                       "https://krisha.kz/arenda/%2E%2E%2Fmy/", "https://krisha.kz/prodazha/./kvartiry/",
                       "https://krisha.kz/prodazha/..\\my/"],
        "cardUrls": ["https://krisha.kz/a/show-map/1009924986", "https://krisha.kz/a/show/abc",
                     "https://evil.example/a/show/1"]}
    for field, values in bad.items():
        for v in values:
            r = patch({field: [v]})
            assert r.status_code == 422, (v, r.text)
            assert r.json()["detail"][0]["loc"] == ["body", "config", field]
    r = patch({"searchUrls": ["https://krisha.kz/prodazha/../my/"]})
    assert r.json()["detail"][0]["msg"] == ("Ссылка Krisha с «.» или «..» в пути не принимается: "
                                            "https://krisha.kz/prodazha/../my/")
    # точки внутри имени сегмента — обычная ссылка
    assert patch({"searchUrls": ["https://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/..x/"]}).status_code == 200
    six = [f"https://krisha.kz/prodazha/kvartiry/?page={n}" for n in range(6)]
    r = patch({"searchUrls": six})
    assert r.status_code == 422 and r.json()["detail"][0]["msg"] == "Не больше 5 ссылок поиска Krisha"


def test_robots_disallow_item_failed_and_all_disallowed_error(client, kr, site, run_worker, ocr_stub, monkeypatch):
    from urllib.robotparser import RobotFileParser

    monkeypatch.setattr(RobotFileParser, "read", lambda self: pytest.fail("RobotFileParser.read() не используется"))
    other = "https://krisha.kz/arenda/kvartiry/ust-kamenogorsk/"
    site.setup(cards="block", robots="User-agent: *\nDisallow: /arenda/\n")
    configure(searchUrls=[SEARCH_URL, other])
    r = run(run_worker)
    assert (r.status, r.items_new, r.items_failed) == ("partial", 3, 1)
    with SessionLocal() as db:
        notes = [n["text"] for n in db.get(models.CollectionRun, r.id).notes]
    assert f"robots.txt Krisha запрещает {other}" in notes
    assert other not in site.requests() and site.requests(r"robots\.txt$") == [ROBOTS_URL]
    # правила кэшированы на сутки: второй запуск robots.txt не запрашивает
    run(run_worker)
    assert site.requests(r"robots\.txt$") == [ROBOTS_URL]
    # все ссылки запрещены → ошибка настроек, сбор остановлен
    set_row(state={})
    site.setup(cards="block", robots="User-agent: *\nDisallow: /\n")
    r = run(run_worker)
    assert (r.status, r.error_code, r.error) == ("failed", "bad_config", f"robots.txt Krisha запрещает {SEARCH_URL}")
    assert row().status == "error" and row().facts["stopped"] is True


def test_robots_unavailable_retries_and_404_allows(client, kr, site, run_worker, ocr_stub):
    site.setup(cards="block")
    site.mock.on("GET", ROBOTS_URL, status=503)
    configure()
    r = run(run_worker)
    assert r.status == "running"  # 5xx robots.txt — повтор задания, выдача не запрашивалась
    assert site.requests() == [ROBOTS_URL]
    site.mock.on("GET", ROBOTS_URL, status=404)
    r = run(run_worker, now=models.utcnow() + timedelta(minutes=10))
    assert r.status == "partial" and r.items_new == 3


def test_no_search_urls_is_bad_config(client, kr, run_worker):
    set_row(config={})
    r = run(run_worker)
    assert (r.status, r.error_code, r.error) == ("failed", "bad_config", "Добавьте URL поиска Krisha")


# ---------- 10. вежливость: паузы, перенаправления, cookie ----------

def test_delays_redirects_cookies_and_user_agent(client, kr, site, run_worker, ocr_stub, monkeypatch, env):
    from app.settings import get_settings

    sleeps: list[float] = []
    monkeypatch.setattr(http, "SLEEP", sleeps.append)
    env(KRISHA_MIN_DELAY_S="7")
    site.setup(cards="block")
    site.mock.on("GET", ROBOTS_URL, content=ROBOTS.encode(),
                 headers={"content-type": "text/plain", "set-cookie": "sl-session=FAKE-SL-COOKIE; Path=/"})
    moved = SEARCH_URL + "?das[live.rooms]=3"
    site.mock.on("GET", moved, status=301, headers={"location": "/prodazha/kvartiry/ust-kamenogorsk/"})
    configure(searchUrls=[moved])
    r = run(run_worker)
    assert r.status == "partial" and r.items_new == 3
    krisha_calls = [c for c in site.mock.calls if c.url.host == "krisha.kz"]
    assert [str(c.url) for c in krisha_calls] == [ROBOTS_URL, moved, SEARCH_URL, "https://krisha.kz/a/show/900000001"]
    assert len(sleeps) == len(krisha_calls) - 1 and all(7 <= s <= 9 for s in sleeps)
    for c in krisha_calls:
        assert "cookie" not in c.headers
        assert c.headers["user-agent"] == get_settings().user_agent and c.headers["accept-language"] == "ru"
    # перенаправление на чужой хост и с https на http — не выполняется
    for target in ("https://evil.example/prodazha/", "http://krisha.kz/prodazha/kvartiry/ust-kamenogorsk/"):
        site.mock.on("GET", moved, status=302, headers={"location": target})
        r = run(run_worker)
        assert r.status == "failed" and "перенаправляет на другой адрес" in r.error
        assert not any(c.url.host == "evil.example" or c.url.scheme == "http" for c in site.mock.calls)


def test_request_budget_per_run(client, kr, site, run_worker, ocr_stub, env):
    env(KRISHA_MAX_PAGES="50")

    def pages(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params.get("page", "1"))
        eid = str(910000000 + n)
        html = (f'<script>window.data = {{"search": {{"nbTotal": 5000, "ids": [{eid}]}}}};</script>'
                f'<div class="a-card" data-id="{eid}"><a class="a-card__title">Квартира {n}</a>'
                f'<div class="a-card__price">{n} 000 000 〒</div></div>')
        return httpx.Response(200, content=html.encode(), headers=HTML)

    site.setup(cards="block")
    site.mock.on("GET", re.compile(r"^https://krisha\.kz/prodazha/kvartiry/ust-kamenogorsk/"), handler=pages)
    configure()
    r = run(run_worker)
    assert r.status == "partial" and len(site.requests()) == kr.MAX_REQUESTS
    assert row().status_detail == f"Достигнут предел запросов к Krisha за запуск ({kr.MAX_REQUESTS}) — продолжим в " \
                                  "следующий раз"
    assert r.items_new == kr.MAX_REQUESTS - 1  # robots.txt + 29 страниц выдачи
    # в синтетической выдаче нет подвала с типом продавца: предупреждение о разметке — одно на запуск
    assert run_notes(r.id).count(kr.NO_SELLER_MARKUP) == 1
    assert ad("910000001").account == "krisha.kz / продавец не указан"


def test_time_up_marks_partial(client, kr, site, run_worker, monkeypatch):
    from app.integrations.context import RunContext

    monkeypatch.setattr(RunContext, "time_left", lambda self: 30.0)
    site.setup()
    configure()
    r = run(run_worker)
    assert r.status == "partial" and row().status_detail == "Время запуска истекло — продолжим в следующий раз"
    assert site.requests() == [ROBOTS_URL]


# ---------- 11. Playwright (необязательный) ----------

class FakeRoute:
    def __init__(self, url: str):
        self.request = SimpleNamespace(url=url)
        self.result = None

    def continue_(self):
        self.result = "continue"

    def abort(self, *_a):
        self.result = "abort"


class FakeResponse:
    def __init__(self, status: int, body: str, url: str):
        self.status, self._body, self.url = status, body, url
        self.headers = {"content-type": "text/html"}

    def text(self) -> str:
        return self._body


class FakeBrowser:
    """Браузер-заглушка: ответ навигации задаёт тест; страница «после проверки» (content) — уже обычная карточка."""

    def __init__(self, status: int = 468, body: str = "", final_url=None):
        self.status, self.body, self.final_url = status, body or page("safeline_468.html"), final_url
        self.contexts: list[dict] = []
        self.routes: list = []
        self.gotos: list = []
        self.content_read = False
        self.closed: list[str] = []

    def factory(self):
        return contextlib.nullcontext(self)

    def new_context(self, **kw):
        self.contexts.append(kw)
        browser = self

        class Ctx:
            def route(self, pattern, handler):
                browser.routes.append((pattern, handler))

            def new_page(self):
                class Page:
                    def goto(self, url, wait_until=None, timeout=None):
                        browser.gotos.append((url, wait_until, timeout))
                        return FakeResponse(browser.status, browser.body, browser.final_url or url)

                    def content(self):
                        browser.content_read = True
                        return card_html()

                    def close(self):
                        browser.closed.append("page")
                return Page()

            def close(self):
                browser.closed.append("context")
        return Ctx()


class _ImportTrap:
    """Ловушка импорта: любая попытка импортировать playwright записывается (и не удаётся, как без пакета)."""

    def __init__(self):
        self.attempts: list[str] = []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] == "playwright":
            self.attempts.append(name)
            raise ImportError("playwright: импорт запрещён тестом")
        return None


def test_playwright_not_imported_when_off(client, kr, site, run_worker, ocr_stub, monkeypatch, env):
    env(KRISHA_PLAYWRIGHT="0")
    trap = _ImportTrap()
    monkeypatch.setattr(sys, "meta_path", [trap, *sys.meta_path])
    monkeypatch.delitem(sys.modules, "playwright", raising=False)
    monkeypatch.setattr(kr, "BROWSER_FACTORY", None)  # иначе выключенная настройка не дошла бы до импорта
    site.setup(cards="block")
    configure()
    r = run(run_worker)
    assert r.status == "partial" and trap.attempts == [] and "playwright" not in sys.modules
    notes = run_notes(r.id)
    assert not any("Playwright" in n or "браузер" in n for n in notes), notes
    # проверка: тот же путь карточки — факт «не выполнялась», без импорта
    run(run_worker, trigger="test")
    assert trap.attempts == [] and row().facts["playwright"] == "не выполнялась"
    # ловушка работает: при KRISHA_PLAYWRIGHT=1 попытка импорта видна и даёт заметку «не установлен»
    env(KRISHA_PLAYWRIGHT="1")
    r = run(run_worker)
    assert trap.attempts == ["playwright"]
    assert "KRISHA_PLAYWRIGHT=1, но Playwright не установлен — проверка карточек браузером не выполнялась" in \
        run_notes(r.id)


def test_playwright_on_without_package_notes(client, kr, site, run_worker, ocr_stub, monkeypatch, env):
    if importlib.util.find_spec("playwright") is not None:
        pytest.skip("Playwright установлен — проверяется заглушкой ниже")
    env(KRISHA_PLAYWRIGHT="1")
    monkeypatch.setattr(kr, "BROWSER_FACTORY", None)
    site.setup(cards="block")
    configure()
    r = run(run_worker)
    assert r.status == "partial" and "playwright" not in sys.modules
    with SessionLocal() as db:
        notes = [n["text"] for n in db.get(models.CollectionRun, r.id).notes]
    assert "KRISHA_PLAYWRIGHT=1, но Playwright не установлен — проверка карточек браузером не выполнялась" in notes


def test_playwright_blocked_navigation_is_not_used(client, kr, site, run_worker, ocr_stub, monkeypatch, env):
    from app.settings import get_settings

    env(KRISHA_PLAYWRIGHT="1")
    browser = FakeBrowser(status=468)
    monkeypatch.setattr(kr, "BROWSER_FACTORY", browser.factory)
    site.setup(cards="block")
    configure()
    r = run(run_worker)
    assert r.status == "partial"
    assert row().status_detail == ("Карточки закрыты проверкой доступа (HTTP 468, SafeLine, браузер) — сохранены "
                                   "данные из выдачи")
    assert browser.content_read is False and "Продаётся просторная" not in ad(IDS[0]).text
    assert len(browser.gotos) == 1 and browser.gotos[0] == ("https://krisha.kz/a/show/900000001", "commit", 30000)
    kw = browser.contexts[0]
    assert (kw["java_script_enabled"], kw["service_workers"], kw["user_agent"]) == (
        False, "block", get_settings().user_agent)
    assert "storage_state" not in kw and browser.closed == ["page", "context"]
    pattern, handler = browser.routes[0]
    assert pattern == "**/*"
    results = {}
    for url in ("https://evil.example/x.js", "https://krisha.kz/a/show/1", "https://photos.kcdn.kz/a.jpg",
                "https://krisha.kz.evil.example/", LIVE_CDN + "1/1-750x470.webp", "https://kcdn.online.evil.example/"):
        route = FakeRoute(url)
        handler(route)
        results[url] = route.result
    assert results == {"https://evil.example/x.js": "abort", "https://krisha.kz/a/show/1": "continue",
                       "https://photos.kcdn.kz/a.jpg": "continue", "https://krisha.kz.evil.example/": "abort",
                       LIVE_CDN + "1/1-750x470.webp": "continue", "https://kcdn.online.evil.example/": "abort"}


def test_playwright_ok_card_and_redirect_refused(client, kr, site, run_worker, ocr_stub, monkeypatch, env):
    env(KRISHA_PLAYWRIGHT="1", KRISHA_MAX_CARDS="2")
    browser = FakeBrowser(status=200, body=card_html())
    monkeypatch.setattr(kr, "BROWSER_FACTORY", browser.factory)
    site.setup(cards="block")
    configure()
    r = run(run_worker)
    # первая карточка: HTTP 468 → браузер 200 — карточка принята; вторая — сразу браузером, но ответ не её ID
    assert "Продаётся просторная квартира" in ad(IDS[0]).text
    assert len(site.requests(r"/a/show/")) == 1 and len(browser.gotos) == 2
    assert r.items_failed == 1 and r.status == "partial"
    browser2 = FakeBrowser(status=200, body=card_html(IDS[1]), final_url="https://evil.example/a/show/900000002")
    monkeypatch.setattr(kr, "BROWSER_FACTORY", browser2.factory)
    with SessionLocal() as db:
        it = db.scalar(select(models.IngestItem).where(models.IngestItem.external_id == IDS[1]))
        assert it.version == 1
    r2 = run(run_worker)
    assert r2.status == "partial" and "перенаправление на https://evil.example/a/show/900000002" in row().status_detail


# ---------- приёмка по §14 плана (integration-spec §16.5) ----------

def test_acceptance_repeat_no_duplicate(client, kr, site, run_worker, ocr_stub, accept):
    site.setup(cards="ok")
    configure()
    run(run_worker)
    before, photos = accept.snapshot(), site.photos()
    assert before == {"ads": count(models.Ad), "ingest_items": 3, "ingest_versions": 3,
                      "media_assets": count(models.MediaAsset)}
    r = run(run_worker)
    assert r.items_new == 0 and accept.snapshot() == before and site.photos() == photos


def test_acceptance_failure_not_empty(client, kr, site, run_worker, accept):
    site.setup()
    site.mock.on("GET", SEARCH_URL, status=468, content=page("safeline_468.html").encode(), headers=HTML)
    configure()
    r = run(run_worker)
    assert (r.status, r.items_seen) == ("access_check", 0)
    c = accept.assert_failure_visible("krisha", r.id)
    assert c["status"] == "access_check" and "проверкой доступа" in c["statusDetail"]
    assert count(models.IngestItem) == 0


def test_acceptance_restart_keeps_position(client, kr, site, run_worker, ocr_stub, accept, monkeypatch, env):
    from app.integrations.context import RunContext

    env(KRISHA_MAX_CARDS="0")
    original = RunContext.ingest
    calls = {"n": 0}

    def flaky(self, pub, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("сбой воркера после первой публикации")
        return original(self, pub, **kw)

    monkeypatch.setattr(RunContext, "ingest", flaky)
    site.setup()
    configure()
    r = run(run_worker)
    assert r.status == "running" and count(models.IngestItem) == 1  # задание ждёт повтора
    r = run(run_worker, now=models.utcnow() + timedelta(minutes=10))
    # счётчики запуска общие для попыток: 1 до сбоя + 2 после; первая при повторе — без изменений
    assert (r.status, r.items_new, r.items_unchanged) == ("succeeded", 3, 1)
    assert accept.snapshot()["ingest_items"] == 3 and accept.snapshot()["ingest_versions"] == 3
    assert [item(i).version for i in IDS] == [1, 1, 1]
    assert count(models.Ad) == accept.snapshot()["ads"]


def test_acceptance_media_survives_source(client, kr, site, run_worker, ocr_stub, accept):
    site.setup(cards="block")
    configure()
    run(run_worker)
    ad_id = item(IDS[0]).ad_id
    site.mock.clear()  # исходной страницы и CDN больше нет
    media = client.get(f"/api/ads/{ad_id}").json()["media"]
    assert media and media[0]["url"] == f"/api/media/{media[0]['id']}"
    assert accept.media_ok(media[0]["id"]).startswith(b"\x89PNG")


def test_acceptance_not_confirmed_automatically(client, kr, site, run_worker, ocr_stub, accept):
    site.setup(cards="block")
    configure()
    run(run_worker)
    ad_id = item(IDS[0]).ad_id  # текст называет демо-ЖК «Ульба Ривер»
    accept.assert_not_confirmed(ad_id)
    r = client.post(f"/api/ads/{ad_id}/hypotheses/accept", json={"author": AUTHOR, "complexId": "c3"})
    assert r.status_code == 200, r.text
    assert (r.json()["complexId"], r.json()["linkConfirmedBy"]) == ("c3", AUTHOR)


def test_acceptance_history_kept(client, kr, site, run_worker, ocr_stub):
    site.setup(cards="block")
    configure()
    run(run_worker)
    ad_id = item(IDS[1]).ad_id
    site.search(page("search.html").replace("Продаю сам, без посредников.", "Продаю сам, торг уместен."))
    r = run(run_worker)
    assert (r.items_updated, r.items_unchanged) == (1, 2)
    v = client.get(f"/api/ads/{ad_id}/versions").json()
    assert [x["version"] for x in v] == [2, 1]
    assert "торг уместен" in v[0]["text"] and "без посредников" in v[1]["text"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(models.IngestVersion)
                         .where(models.IngestVersion.item_id == item(IDS[1]).id)) == 2


def test_acceptance_limit_no_endless_loop(client, kr, site, run_worker, ocr_stub):
    site.setup()
    site.mock.on("GET", SEARCH_URL, status=429, headers={"retry-after": "600"})
    configure()
    set_row(enabled=True)
    now = models.utcnow()
    r = run(run_worker, now=now)
    assert r.status == "rate_limited" and site.requests() == [ROBOTS_URL, SEARCH_URL]
    calls = len(site.mock.calls)
    for minutes in (1, 5, 9):  # такты воркера до next_run_at — без обращений к сайту
        run_worker(now=now + timedelta(minutes=minutes))
        assert len(site.mock.calls) == calls
    site.setup(cards="block")
    run_worker(now=now + timedelta(minutes=11))  # пауза прошла — плановый сбор
    assert len(site.mock.calls) > calls and count(models.IngestItem) == 3
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM collection_runs WHERE collector = 'krisha'")).scalar() == 2
