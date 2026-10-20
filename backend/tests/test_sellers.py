"""Кто продаёт(T-73), аномальная цена(T-74), связи застройщика(T-75): извлечение контактов(телефоны, WhatsApp, БИН,
@упоминания), группы продавцов, dHash картинок, разбор цены за м² и медиана, маршруты API на демо-наборе."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import models, prices, sellers
from app.db import SessionLocal


def ad(id, account, text="", ocr=None, platform="instagram", date="2026-10-01T10:00", **kw):
    return {"id": id, "platform": platform, "account": account, "date": date, "text": text, "ocr": ocr, **kw}


# ---------- контакты ----------

@pytest.mark.parametrize("raw", ["+7 777 700 77 33", "8 777 700 77 33", "8 (777) 700-77-33", "87777007733", "+77777007733",
                                 "+7-777-700-77-33", "7 777 700 7733"])
def test_phone_normalization(raw):
    found = [c for c in sellers.contacts(f"Звоните: {raw}!", None) if c.kind == "phone"]
    assert [c.value for c in found] == ["7777007733"]
    assert found[0].shown == "+7 777 700 77 33"
    assert sellers.norm_phone(raw) == "7777007733"


def test_price_is_not_a_phone_and_city_number_is():
    assert not sellers.contacts("Квартиры от 7 200 000 000 ₸ и 17 427 600 ₸", None)
    city = sellers.contacts("Офис: +7 7232 70-00-02", None)
    assert [c.value for c in city] == ["7232700002"]


def test_whatsapp_link_is_the_same_number():
    cs = sellers.contacts("Пишите https://wa.me/77777007733?text=Здравствуйте или api.whatsapp.com/send?phone=87011234567", None)
    assert {(c.kind, c.value) for c in cs} == {("wa", "7777007733"), ("wa", "7011234567")}
    g = sellers.build([ad("a1", "@one", "wa.me/77777007733"), ad("a2", "@two", "Тел. 8 777 700 77 33")])
    assert g[0]["linked"] and g[0]["phones"][0]["whatsapp"] is True and len(g[0]["phones"]) == 1


@pytest.mark.parametrize("b,ok", [("201040022748", True), ("040540018865", True), ("970640000190", True),
                                  ("201040022749", False), ("12345", False), ("180840012345", False)])
def test_bin_checksum(b, ok):
    assert sellers.bin_valid(b) is ok


def test_bin_needs_label_and_checksum():
    cs = sellers.contacts("ТОО «EKM COMPANY», БИН 201040022748. Номер 201040022749 без подписи; БСН: 201040022749", None)
    assert [(c.kind, c.value) for c in cs] == [("bin", "201040022748")]
    assert "БИН 201040022748" in cs[0].quote


def test_sources_and_quotes():
    ocr = "ЦЕНЫ СНИЖЕНЫ\n\nРечь в ролике: звоните по номеру восемь 8 701 555 44 33 прямо сейчас"
    cs = sellers.contacts("Подробнее в директ, пишите @an.groups", ocr)
    assert {(c.kind, c.source) for c in cs} == {("mention", "text"), ("phone", "speech")}
    phone = next(c for c in cs if c.kind == "phone")
    assert "8 701 555 44 33" in phone.quote and phone.quote.startswith("Речь в ролике")


def test_email_is_not_a_mention():
    assert not [c for c in sellers.contacts("Почта info@khc.kz и zhbk@mail.ru", None) if c.kind == "mention"]


# ---------- группы ----------

def test_groups_by_phone_bin_handle_and_mention():
    ads = [
        ad("a1", "@alpha", "Звоните 8 777 111 22 33", complexId="c1", complexName="Альфа", linkConfirmed=True),
        ad("a2", "@agency.uka", "Квартиры в ЖК Альфа: +7 (777) 111-22-33", date="2026-09-01T09:00"),
        ad("a3", "@alpha", "Наш TikTok", platform="tiktok", date="2026-10-05T12:00"),
        ad("a4", "@beta", "Официальный партнёр — @agency.uka"),
        ad("a5", "@gamma", "ТОО «Гамма», БИН 201040022748"),
        ad("a6", "@delta", "БИН: 201040022748, отдел продаж"),
        ad("a7", "@lonely", "Без контактов"),
    ]
    groups = sellers.build(ads, developers=[{"id": "d9", "name": "ТОО «Гамма»", "bin": "201040022748"}])
    big = groups[0]
    # по одной публикации у каждого - порядок по ключу; ключ группы - первый аккаунт
    assert [a["key"] for a in big["accounts"]] == ["instagram:@agency.uka", "instagram:@alpha", "instagram:@beta", "tiktok:@alpha"]
    assert big["key"] == "instagram-agency.uka" and big["linked"] and big["hypothesis"]
    assert {ln["kind"] for ln in big["links"]} == {"phone", "handle", "mention"}
    assert big["phones"][0]["phone"] == "+7 777 111 22 33" and big["phones"][0]["ads"] == 2
    ev = big["phones"][0]["evidence"]
    assert {e["adId"] for e in ev} == {"a1", "a2"} and all("111" in e["quote"] for e in ev)
    assert big["complexes"] == [{"id": "c1", "name": "Альфа", "ads": 1, "confirmed": 1, "accounts": ["instagram:@alpha"]}]
    assert (big["firstSeen"], big["lastSeen"]) == ("2026-09-01", "2026-10-05")
    gamma = sellers.find(groups, "instagram:@delta")
    assert gamma and gamma["bins"][0]["developerId"] == "d9" and len(gamma["accounts"]) == 2
    assert groups[-1]["accounts"][0]["account"] == "@lonely" and not groups[-1]["linked"]
    gr = sellers.graph(big)
    kinds = {n["kind"] for n in gr["nodes"]}
    assert kinds == {"account", "phone", "complex"}
    assert {"a": "instagram:@agency.uka", "b": "phone:7771112233", "kind": "phone"} in gr["edges"]
    st = sellers.stats(groups)
    assert st["linked"] == 2 and st["sharedPhones"] == 1


def test_mention_of_unknown_account_does_not_link():
    groups = sellers.build([ad("a1", "@one", "Ипотека с @halykbank"), ad("a2", "@two", "Ипотека с @halykbank")])
    assert len(groups) == 2 and not any(g["links"] for g in groups)


def test_krisha_seller_type_is_not_an_account():
    ads = [ad("ab12cd34ef", "krisha.kz / специалист", "2-комнатная", platform="krisha"),
           ad("ff12cd34ef", "krisha.kz / специалист", "1-комнатная", platform="krisha"),
           ad("k3", "krisha.kz / «Eco Polis»", "", platform="krisha"), ad("k4", "krisha.kz / «Eco Polis»", "", platform="krisha")]
    groups = sellers.build(ads)
    assert len(groups) == 3
    assert {g["title"] for g in groups} == {"krisha.kz / «Eco Polis»", "krisha.kz / специалист · AD-AB12CD34",
                                            "krisha.kz / специалист · AD-FF12CD34"}


def _picture(path: Path, shift: int = 0, plain=False):
    size = (320, 240)
    im = Image.new("RGB", size, "white")
    if not plain:
        d = ImageDraw.Draw(im)
        for i in range(0, size[0], 40):
            d.rectangle([i, 0, i + 20, size[1]], fill=(i % 255, 80, 200 - i % 150))
        d.ellipse([60 + shift, 40, 220 + shift, 200], fill="black")
    im.save(path, "JPEG", quality=90)
    return path


def test_dhash_same_picture_links_accounts(tmp_path):
    a = sellers.dhash(_picture(tmp_path / "a.jpg"))
    with Image.open(tmp_path / "a.jpg") as im:  # та же картинка крупнее и пережата
        im.resize((640, 480)).save(tmp_path / "b.jpg", "JPEG", quality=60)
    b = sellers.dhash(tmp_path / "b.jpg")
    c = sellers.dhash(_picture(tmp_path / "c.jpg", shift=90))
    assert sellers.dhash(_picture(tmp_path / "w.jpg", plain=True)) is None  # однотонная - не сравнивается
    assert (a ^ b).bit_count() <= sellers.IMAGE_MAX_DISTANCE < (a ^ c).bit_count()
    groups = sellers.build([ad("a1", "@one"), ad("a2", "@two"), ad("a3", "@three")],
                           {"a1": [(a, "s1")], "a2": [(b, "s2")], "a3": [(c, "s3")]})
    assert len(groups[0]["accounts"]) == 2 and groups[0]["links"][0]["kind"] == "image"
    assert groups[0]["links"][0]["evidence"][0]["otherAdId"] == "a2"


def test_ad_hint_counts_other_ads():
    groups = sellers.build([ad("a1", "@one", "8 777 111 22 33"), ad("a2", "@one", "8 777 111 22 33"),
                            ad("a3", "@two", "+7 777 111 22 33")])
    h = sellers.ad_hint(groups, "a1")
    assert h["phones"] == [{"phone": "+7 777 111 22 33", "digits": "7771112233", "whatsapp": False, "otherAds": 2, "accounts": 2}]
    assert h["accounts"] == 2 and h["linked"]


# ---------- цена ----------

@pytest.mark.parametrize("text,per,method", [
    ("Квартиры от 350 000 ₸/м²", 350_000, "explicit"),
    ("Мы зафиксировали цену — 470 000 тенге за квадратный метр.", 470_000, "explicit"),
    ("Цена за 1 м² — от 450 000 тенге.", 450_000, "explicit"),
    ("Бағасы: 400 000 теңге/шаршы метр", 400_000, "explicit"),
    ("Цена 25 млн, 60 м²", 416_667, "total_area"),
    ("✨ 50,12 м² — пространство.\n💰 Стоимость — от 22 554 000 ₸.", 450_000, "total_area"),
    ("💰 Цена от 450 000 тенге", 450_000, "implied"),
])
def test_price_parse(text, per, method):
    p = prices.parse(ad("a", "@x", text))
    assert p["perM2"] == per and p["method"] == method and p["quote"]


@pytest.mark.parametrize("text", [
    "Квартиры площадью от 39 до 111 м², цена 30 млн",  # диапазон площади
    "Сумма займа — до 30 млн тенге, квартира 60 м²",  # займ, а не цена
    "Кладовые помещения от 6,8 м². Стоимость — 130 000 ₸/м².",  # не квартира
    "Цена 25 млн, 60 м², а 2-комнатная 70 м² — 31 млн",  # несколько цен и площадей
    "Ипотека 7%: первоначальный взнос от 5 000 000 ₸, 45 м²",
])
def test_price_not_parsed(text):
    p = prices.parse(ad("a", "@x", text))
    assert p is None or p["perM2"] is None


def test_krisha_price_and_rooms():
    p = prices.parse(ad("k", "krisha.kz / «ASYLTAS»", "2-комнатная квартира · 71.84 м²\nУвалиева 8/3", platform="krisha",
                        price="от 33 046 400 ₸"))
    assert (p["perM2"], p["rooms"], p["area"], p["from"], p["method"]) == (460_000, 2, 71.84, True, "total_area")


def test_speech_numbers_are_ignored_but_full_payment_is_marked():
    p = prices.parse(ad("a", "@x", "Скидка до 20% при 100% оплате!", "Речь в ролике: от 16 млн 60 м²"))
    assert p["perM2"] is None and p["fullPayment"]["quote"].startswith("Скидка до 20% при 100% оплате")


@pytest.mark.parametrize("text,marked", [
    ("Цена 39 999 999₸ (наличный расчет), 60 м²", True),
    ("Дом сдаётся в ноябре 2026, только за наличный расчёт", True),
    ("100% төлем жасағанда 20%-ға дейін жеңілдік", True),
    ("Квартиру можно приобрести за наличный расчет и в ипотеку", False),
])
def test_full_payment_marker(text, marked):
    p = prices.parse(ad("a", "@x", text))
    assert bool(p and p["fullPayment"]) is marked


def test_anomaly_below_median():
    ads = [ad(f"k{i}", "@market", f"Цена 30 млн, 60 м²") for i in range(6)]  # 500 000 ₸/м²
    ads += [ad("cheap", "@cheap", "Только сейчас! 2-комнатная, 60 м², цена 18 млн. Скидка за 100% оплату"),
            ad("near", "@near", "Цена 27 млн, 60 м²")]
    a = prices.analyze(ads)
    assert a["median"] == 500_000
    rows = {r["adId"]: r for r in a["rows"]}
    cheap = rows["cheap"]
    assert cheap["anomaly"] and cheap["belowPct"] == 40 and cheap["medianBasis"] == "city" and cheap["fullPayment"]
    assert not rows["near"]["anomaly"] and rows["near"]["belowPct"] == 10
    rep = prices.report(a)
    assert [r["adId"] for r in rep["items"]] == ["cheap"] and rep["anomalies"] == 1
    assert prices.for_ad(a, "cheap")["cityMedian"] == 500_000


def test_room_median_used_when_enough():
    ads = [ad(f"o{i}", "@m", f"1-комнатная, 40 м², цена 24 млн") for i in range(5)]  # 600 000
    ads += [ad(f"t{i}", "@m", f"3-комнатная, 100 м², цена 40 млн") for i in range(5)]  # 400 000
    ads.append(ad("x", "@x", "1-комнатная, 40 м², цена 16,4 млн"))  # 410 000 - дёшево для однокомнатных
    rows = {r["adId"]: r for r in prices.analyze(ads)["rows"]}
    assert rows["x"]["medianBasis"] == "rooms" and rows["x"]["median"] == 600_000 and rows["x"]["anomaly"]


# ---------- API ----------

def _add_ads(*rows):
    with SessionLocal() as db:
        for r in rows:
            db.add(models.Ad(id=r["id"], platform=r.get("platform", "instagram"), account=r["account"], url="",
                             date=r.get("date", "2026-10-01T10:00"), text=r["text"], ocr=None,
                             complex_id=r.get("complexId"), origin="collected", source="parser", status="new"))
        db.commit()


def test_api_sellers_and_by_ad(client):
    _add_ads({"id": "s1", "account": "@seller.one", "text": "Квартиры в ЖК, звоните 8 705 999 00 11"},
             {"id": "s2", "account": "@seller.two", "text": "WhatsApp: wa.me/77059990011, цена 18 млн, 60 м²"})
    r = client.get("/api/sellers", params={"linked": "true"})
    assert r.status_code == 200
    body = r.json()
    g = next(g for g in body["groups"] if "s1" in g["adIds"])
    assert {a["account"] for a in g["accounts"]} == {"@seller.one", "@seller.two"} and "гипотеза" in body["note"]
    one = client.get(f"/api/sellers/{g['key']}").json()
    assert one["graph"]["nodes"] and {a["id"] for a in one["ads"]} == {"s1", "s2"}
    assert client.get("/api/sellers/instagram:@seller.two").json()["key"] == g["key"]  # по ключу аккаунта
    assert client.get("/api/sellers/nobody").status_code == 404
    hint = client.get("/api/sellers/by-ad/s1").json()
    assert hint["seller"]["phones"][0]["otherAds"] == 1 and hint["seller"]["accounts"] == 2
    assert client.get("/api/sellers/by-ad/s2").json()["price"]["perM2"] == 300_000
    assert client.get("/api/sellers/by-ad/none").status_code == 404
    pa = client.get("/api/price-anomalies", params={"all": "true"}).json()
    assert pa["n"] >= 1 and any(r["adId"] == "s2" for r in pa["items"]) and pa["threshold"] == 0.7


def test_api_affiliations(client):
    with SessionLocal() as db:
        d1, d2 = db.get(models.Developer, "d1"), db.get(models.Developer, "d2")
        d2.director = d1.director.upper()  # тот же руководитель в другом написании
        cx1 = next(c for c in db.query(models.Complex).filter_by(developer_id="d1"))
        cx2 = next(c for c in db.query(models.Complex).filter_by(developer_id="d2"))
        ids = (cx1.id, cx2.id)
        db.commit()
    _add_ads({"id": "f1", "account": "@agent.uka", "text": "Продажа: 8 702 123 45 67", "complexId": ids[0]},
             {"id": "f2", "account": "@agent2.uka", "text": "Тел. +7 702 123 45 67", "complexId": ids[1]})
    r = client.get("/api/developers/d1/affiliations")
    assert r.status_code == 200
    x = r.json()
    assert x["developer"]["bin"] and x["developer"]["director"]
    rel = next(o for o in x["related"] if o["id"] == "d2")
    assert {z["kind"] for z in rel["reasons"]} >= {"director", "phone", "seller"}
    phone = next(z for z in rel["reasons"] if z["kind"] == "phone")
    assert phone["value"] == "+7 702 123 45 67" and set(phone["adIds"]) == {"f1", "f2"}
    assert any(p["digits"] == "7021234567" for p in x["phones"]) and x["adsCount"] >= 1
    assert all(link["url"].startswith("https://") for link in x["lookups"]) and "гипотеза" in x["note"]
    assert client.get("/api/developers/nope/affiliations").status_code == 404
