"""Кто продаёт, аномальная цена, связи застройщика: извлечение контактов(телефоны, WhatsApp, БИН,
@упоминания), группы продавцов, dHash картинок, разбор цены за м² и медиана, маршруты API на демо-наборе."""
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import media, models, prices, sellers
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


@pytest.mark.parametrize("raw,ok", [("+77 701 123 45 67", "7011234567"), ("+77 801 123 45 67", None),
                                    ("+7 916 123 45 67", None), ("", None)])
def test_norm_phone_edge_cases(raw, ok):
    assert sellers.norm_phone(raw) == ok


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


def test_mention_links_even_if_target_mentions_itself():
    # @two упоминает и сам себя, и его упоминает @one - связь @one ↔ @two есть
    groups = sellers.build([ad("a1", "@one", "Партнёр - @two"), ad("a2", "@two", "Пишите @two")])
    assert len(groups) == 1 and [ln["kind"] for ln in groups[0]["links"]] == ["mention"]
    alone = sellers.build([ad("a1", "@one", "Пишите @one")])  # только сам себя - связи нет
    assert not alone[0]["links"]


def test_account_without_date_does_not_break_first_seen():
    groups = sellers.build([ad("a1", "@one", "8 777 111 22 33", date=""), ad("a2", "@one", "", date="2026-09-01T10:00")])
    assert groups[0]["accounts"][0]["firstSeen"] == groups[0]["accounts"][0]["lastSeen"] == "2026-09-01"


def test_phone_counts_all_ads_beyond_evidence_cap():
    n = sellers.EVIDENCE_MAX + 5
    groups = sellers.build([ad(f"a{i:02}", f"@acc{i % 2}", "Тел. 8 777 111 22 33") for i in range(n)])
    p = groups[0]["phones"][0]
    assert p["ads"] == n and len(p["evidence"]) == sellers.EVIDENCE_MAX
    last = f"a{n - 1:02}"  # доказательства её не включают, а номер в подсказке есть
    assert sellers.ad_hint(groups, last)["phones"][0]["otherAds"] == n - 1


def _brute_clusters(hs: list[int], d: int) -> list[list[int]]:
    parent = list(range(len(hs)))

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for i in range(len(hs)):
        for j in range(i + 1, len(hs)):
            if (hs[i] ^ hs[j]).bit_count() <= d:
                parent[find(j)] = find(i)
    comps: dict[int, list[int]] = {}
    for i in range(len(hs)):
        comps.setdefault(find(i), []).append(i)
    return sorted(comps.values())


@pytest.mark.parametrize("seed", range(4))
def test_near_forest_matches_brute_force(seed):
    """Остов близких хэшей даёт те же кластеры, что перебор всех пар: цепочки пережатых копий, одинаковые хэши,
    далёкие хэши с общим куском; каждая пара остова - близкая, пар - на одну меньше хэшей в каждом кластере."""
    import random

    rnd = random.Random(seed)
    d = sellers.IMAGE_MAX_DISTANCE
    base = [rnd.getrandbits(64) for _ in range(40)]
    hs = base + [h ^ (1 << rnd.randrange(64)) ^ (1 << rnd.randrange(64)) for h in base[:20]] + [base[0]] * 3
    for start in base[:5]:  # цепочка: соседние близки, концы - нет
        h = start
        for _ in range(6):
            h ^= sum(1 << b for b in rnd.sample(range(64), 4))
            hs.append(h)
    hs += [(base[1] & 0x1FF) | (rnd.getrandbits(55) << 9) for _ in range(15)]  # общий кусок, остальное далеко
    rnd.shuffle(hs)
    edges = sellers._near_forest(hs, d)
    assert all(j < i and (hs[i] ^ hs[j]).bit_count() <= d for j, i in edges)
    parent = list(range(len(hs)))

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for j, i in edges:
        assert find(i) != find(j)  # пара остова соединяет разные кластеры
        parent[find(i)] = find(j)
    clusters = _brute_clusters(hs, d)
    assert sorted(sorted(i for i in range(len(hs)) if find(i) == r) for r in {find(i) for i in range(len(hs))}) == clusters
    assert len(edges) == len(hs) - len(clusters) and any(len(c) > 3 for c in clusters)


def test_contacts_capped_per_kind_and_source():
    """Текст с сотнями номеров(список, а не реклама) даёт не больше CONTACTS_MAX телефонов из каждого источника - а не
    тысячи связей в группах продавцов; WhatsApp - тот же вид, что телефон."""
    many = " ".join(_phone(1, j) for j in range(300))
    wa = " ".join(f"wa.me/{_phone(2, j).replace(' ', '')[1:]}" for j in range(300))
    cs = sellers.contacts(f"{wa} {many} {' '.join('@acc' + str(j) for j in range(50))}", many)
    by = {}
    for c in cs:
        key = ("phone" if c.kind == "wa" else c.kind, c.source)
        by[key] = by.get(key, 0) + 1
    assert by == {("phone", "text"): sellers.CONTACTS_MAX, ("phone", "ocr"): sellers.CONTACTS_MAX,
                  ("mention", "text"): sellers.CONTACTS_MAX}
    assert {c.kind for c in cs if c.source == "text" and c.kind != "mention"} == {"wa"}  # первые - ссылки WhatsApp
    # номер внутри ссылки WhatsApp - не отдельный телефон, сколько бы ссылок ни было
    cs = sellers.contacts(" ".join(["wa.me/77777007733"] * 500) + " и 8 701 123 45 67", None)
    assert [(c.kind, c.value) for c in cs] == [("wa", "7777007733"), ("phone", "7011234567")]


def test_shared_picture_is_one_link_per_cluster():
    """Одна картинка у нескольких аккаунтов - одна связь со всеми аккаунтами(а не связь на каждую пару), доказательства -
    по одной паре картинок на пару аккаунтов: сначала одинаковые хэши, затем близкие."""
    h = 0x0123456789ABCDEF
    near, other = h ^ 0b111, h ^ ((1 << 64) - 1)
    ads = [ad(f"a{i}", f"@acc{i}") for i in range(4)] + [ad("a9", "@acc0")]
    hashes = {"a0": [(h, "s0", "m0")], "a1": [(h, "s1", "m1")], "a2": [(near, "s2", "m2")], "a3": [(other, "s3", "m3")],
              "a9": [(h, "s9", "m9")]}
    groups = sellers.build(ads, hashes)
    g = groups[0]
    assert [a["key"] for a in g["accounts"]] == ["instagram:@acc0", "instagram:@acc1", "instagram:@acc2"]
    (ln,) = g["links"]
    assert (ln["kind"], ln["value"], ln["accounts"]) == ("image", "s0", ["instagram:@acc0", "instagram:@acc1",
                                                                        "instagram:@acc2"])
    assert [(e["adId"], e["otherAdId"], e["mediaId"], e["otherMediaId"], e["distance"]) for e in ln["evidence"]] == [
        ("a0", "a1", "m0", "m1", 0), ("a0", "a2", "m0", "m2", 3), ("a1", "a2", "m1", "m2", 3)]
    assert len(groups) == 2 and not groups[1]["links"]  # другая картинка - отдельно
    assert sellers.stats(groups)["imageLinks"] == 1
    edges = [e for e in sellers.graph(g)["edges"] if e["kind"] == "image"]
    assert [(e["a"], e["b"]) for e in edges] == [("instagram:@acc0", "instagram:@acc1"),
                                                  ("instagram:@acc0", "instagram:@acc2")]


def test_picture_of_one_account_is_not_a_link():
    h = 0x0123456789ABCDEF
    groups = sellers.build([ad("a1", "@one"), ad("a2", "@one"), ad("a3", "@two")],
                           {"a1": [(h, "s1")], "a2": [(h ^ 1, "s2")], "a3": [(~h & ((1 << 64) - 1), "s3")]})
    assert len(groups) == 2 and not any(g["links"] for g in groups)


def _phone(i: int, j: int) -> str:
    d = f"7{(i * 10_000 + j) % 1_000_000_000:09d}"
    return f"+7 {d[:3]} {d[3:6]} {d[6:8]} {d[8:]}"


def _realistic(seed: int, n: int = 300) -> tuple[list, dict]:
    """Публикации, похожие на собранные: общие номера агентств, WhatsApp, БИН, упоминания, Krisha "специалист",
    одинаковые и пережатые картинки(близкий dHash)."""
    import random

    rnd = random.Random(seed)
    accounts = [(rnd.choice(["instagram", "tiktok", "telegram"]), f"@acc{i}") for i in range(160)]
    own = {acc: _phone(rnd.randrange(90), rnd.randrange(10_000)) for acc in accounts}  # свой номер аккаунта
    agencies = [_phone(95, j) for j in range(6)]  # номера агентств - у нескольких аккаунтов
    bins = ["201040022748", "040540018865", "970640000190"]
    renders = [rnd.getrandbits(64) for _ in range(12)]  # рендеры застройщиков - у агентов и застройщика
    ads, hashes = [], {}
    for i in range(n):
        acc = rnd.choice(accounts)
        platform, account = acc
        phone = own[acc]
        if rnd.random() < 0.25:
            platform, account = "krisha", rnd.choice(["krisha.kz / специалист", "krisha.kz / «Eco Polis»"])
        parts = [rnd.choice(["Звоните ", "Тел. "]) + phone] if rnd.random() < 0.6 else []
        if rnd.random() < 0.08:
            parts.append("Отдел продаж " + rnd.choice(agencies))
        if rnd.random() < 0.05:
            parts.append("wa.me/7" + phone.replace(" ", "")[2:])
        if rnd.random() < 0.04:
            parts.append("БИН " + rnd.choice(bins))
        if rnd.random() < 0.04:
            parts.append(f"Партнёр @acc{rnd.randrange(200)}")  # и аккаунты вне данных
        ocr = f"Речь в ролике: звоните {phone}" if rnd.random() < 0.1 else None
        aid = f"{i:04x}{rnd.getrandbits(32):08x}"
        ads.append(ad(aid, account, ". ".join(parts), ocr, platform=platform,
                      date=f"2026-{rnd.randint(1, 10):02d}-01T10:00"))
        if rnd.random() < 0.4:
            hs = []
            for _ in range(rnd.randint(1, 3)):
                h = rnd.choice(renders) if rnd.random() < 0.1 else rnd.getrandbits(64)
                for _ in range(rnd.choice([0, 0, 2, 4, 8])):  # пережатая копия - близкий хэш, 8 бит - уже другая
                    h ^= 1 << rnd.randrange(64)
                hs.append((h, f"{h:016x}"))
            hashes[aid] = hs
    return ads, hashes


def _reference_groups(ads: list, hashes: dict) -> list[list[str]]:
    """Группы по прежнему правилу перебором всех пар аккаунтов: общий телефон(с WhatsApp) или БИН, одно имя аккаунта на
    разных площадках, упоминание аккаунта из данных, пара картинок разных аккаунтов на расстоянии
    ≤ IMAGE_MAX_DISTANCE."""
    from itertools import combinations

    key = {a["id"]: sellers.account_key(a["platform"], a["account"], a["id"]) for a in ads}
    feats: dict[str, set] = {k: set() for k in key.values()}
    handle: dict[str, str] = {}
    for a in ads:
        k = key[a["id"]]
        feats[k] |= {("phone" if c.kind == "wa" else c.kind, c.value) for c in sellers.contacts(a["text"], a["ocr"])}
        if "#" not in k and sellers.handle_of(a["account"]):
            handle[k] = sellers.handle_of(a["account"])
    parent = {k: k for k in feats}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for x, y in combinations(feats, 2):
        if ({f for f in feats[x] if f[0] != "mention"} & feats[y] or (handle.get(x) and handle.get(x) == handle.get(y))
                or ("mention", handle.get(y)) in feats[x] or ("mention", handle.get(x)) in feats[y]):
            union(x, y)
    pics = [(h[0], key[aid]) for aid, hs in hashes.items() for h in hs]
    for (h1, k1), (h2, k2) in combinations(pics, 2):
        if k1 != k2 and (h1 ^ h2).bit_count() <= sellers.IMAGE_MAX_DISTANCE:
            union(k1, k2)
    comps: dict[str, list[str]] = {}
    for k in feats:
        comps.setdefault(find(k), []).append(k)
    return sorted(sorted(ks) for ks in comps.values())


@pytest.mark.parametrize("seed", range(5))
def test_grouping_matches_pairwise_reference(seed):
    """Группировка за один проход(связи - по корню, картинки - кластерами) совпадает с прежним правилом попарного
    сравнения на данных, похожих на собранные."""
    ads, hashes = _realistic(seed)
    groups = sellers.build(ads, hashes)
    assert sorted(sorted(a["key"] for a in g["accounts"]) for g in groups) == _reference_groups(ads, hashes)
    assert sum(len(g["accounts"]) > 1 for g in groups) >= 5  # в наборе есть и связанные группы
    assert any(ln["kind"] == "image" for g in groups for ln in g["links"])
    for g in groups:  # связь - в группе своих аккаунтов
        keys = {a["key"] for a in g["accounts"]}
        assert all(set(ln["accounts"]) <= keys for ln in g["links"])
        assert all(set(p["accounts"]) <= keys for p in g["phones"])


def test_grouping_on_pathological_input_is_fast():
    """Регрессия: группы продавцов считались за квадрат - связи каждой группы искались перебором всех связей, картинка
    в тысяче публикаций давала связь на каждую пару аккаунтов(сотни тысяч), а текст с тысячей номеров - тысячу связей.
    Список продавцов считается в каждом запросе, и такие публикации останавливали API на минуты."""
    import random
    import time

    ads = [ad(f"p{i:04}", f"@p{i}", " ".join(_phone(i, j) for j in range(1200)),
              " ".join(_phone(i + 500, j) for j in range(1200))) for i in range(100)]
    ads += [ad(f"i{i:04}", f"@i{i}") for i in range(1500)]
    ads += [ad(f"c{i:04}", f"@c{i}", f"Тел. {_phone(900 + i, 1)}") for i in range(2000)]  # тысячи групп
    hashes = {f"i{i:04}": [(0x0123456789ABCDEF, f"s{i:04}")] for i in range(1500)}
    rnd = random.Random(3)  # и пережатые копии одного рендера: тысячи различных, но близких хэшей
    ads += [ad(f"n{i:04}", f"@n{i}") for i in range(6000)]
    hashes |= {f"n{i:04}": [(0x0FEDCBA987654321 ^ sum(1 << b for b in rnd.sample(range(64), 3)), f"n{i:04}")]
               for i in range(6000)}
    started = time.perf_counter()
    groups = sellers.build(ads, hashes)
    assert time.perf_counter() - started < 8  # прежний расчёт - больше минуты и гигабайты памяти
    for size in (1500, 6000):
        shared = next(g for g in groups if len(g["accounts"]) == size)
        (ln,) = shared["links"]
        assert ln["kind"] == "image" and len(ln["evidence"]) == sellers.EVIDENCE_MAX
    assert all(len(g["phones"]) <= 2 * sellers.CONTACTS_MAX for g in groups)  # текст и OCR
    assert len(groups) == 2 + 100 + 2000


def test_image_hash_cache_tolerates_bad_file(tmp_path):
    path = tmp_path / "image_hashes.json"
    for bad in ("{oops", "[1, 2]", '{"a": "x", "b": -1, "c": true}'):
        path.write_text(bad)
        assert sellers._load_cache(path) == {}
    sellers._save_cache(path, {"s1": 5, "s2": None})
    assert sellers._load_cache(path) == {"s1": 5, "s2": None}
    assert [p.name for p in tmp_path.iterdir()] == ["image_hashes.json"]  # временный файл не остался


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


def test_dhash_skips_huge_picture(tmp_path, monkeypatch):
    path = _picture(tmp_path / "big.jpg")
    with Image.open(path) as im:
        im.save(tmp_path / "big.png")  # PNG не уменьшается при декодировании(draft) - проверяется полный размер
    monkeypatch.setattr(sellers, "IMAGE_MAX_PIXELS", 320 * 240 - 1)
    assert sellers.dhash(tmp_path / "big.png") is None


def test_dhash_decodes_only_storage_formats(tmp_path):
    """dHash декодирует только форматы хранилища(media.open_image): чужой формат - ошибка Pillow, список продавцов
    кэширует None, а не перебирает все декодеры Pillow."""
    from PIL import UnidentifiedImageError

    path = tmp_path / "x.bmp"
    with Image.open(_picture(tmp_path / "x.jpg")) as im:
        im.save(path, "BMP")
    with pytest.raises(UnidentifiedImageError):
        sellers.dhash(path)


def test_dhash_pixel_limit_is_low_for_full_decode(tmp_path):
    """PNG, WEBP и GIF декодируются целиком: больше IMAGE_MAX_PIXELS - не декодируется(None); JPEG уменьшается при
    декодировании(draft) - такой же по размеру хэшируется."""
    w, h = 4100, sellers.IMAGE_MAX_PIXELS // 4100 + 1
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).rectangle([0, 0, w // 2, h], fill=255)
    im.save(tmp_path / "huge.png")
    im.save(tmp_path / "huge.jpg", quality=50)
    assert sellers.dhash(tmp_path / "huge.png") is None
    assert sellers.dhash(tmp_path / "huge.jpg") is not None


def _stored(tmp_path, shift: int) -> media.Stored:
    return media.store_bytes(_picture(tmp_path / f"p{shift}.jpg", shift=shift).read_bytes())


def _attach(st: media.Stored, ad_id: str | None) -> None:
    with SessionLocal() as db:
        db.add(models.MediaAsset(id=os.urandom(16).hex(), ad_id=ad_id, kind="image", status="downloaded",
                                 origin_ref="intake" if ad_id else "gallery", storage_key=st.storage_key,
                                 sha256=st.sha256, size=st.size, mime=st.mime))
        db.commit()


@pytest.fixture
def hashing(client, monkeypatch):
    """Пустой кэш хэшей(файл общий для тестов) и счётчик вызовов dHash: sha256 файлов, которые декодировались."""
    sellers._cache_path().unlink(missing_ok=True)
    calls: list[str] = []
    real = sellers.dhash

    def counted(path):
        calls.append(Path(path).stem)
        return real(path)

    monkeypatch.setattr(sellers, "dhash", counted)
    return calls


def test_image_hashes_skip_files_without_ad(tmp_path, hashing):
    """Фото объекта и непривязанные загрузки в группы продавцов не входят - и не декодируются."""
    linked, loose = _stored(tmp_path, 1), _stored(tmp_path, 2)
    _attach(linked, "ad1")
    _attach(loose, None)
    with SessionLocal() as db:
        out = sellers.image_hashes(db)
    assert list(out) == ["ad1"] and out["ad1"][0][1] == linked.sha256
    assert hashing == [linked.sha256]
    assert set(sellers._load_cache(sellers._cache_path())) == {linked.sha256}


def test_image_hashes_parallel_requests_decode_once(tmp_path, hashing, monkeypatch):
    """Регрессия: каждый запрос списка продавцов декодировал все новые картинки сам, без очереди - десятки
    параллельных запросов держали в памяти десятки декодированных картинок. Теперь - по одной на процесс, а
    дождавшийся очереди берёт посчитанное из кэша."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    stored = [_stored(tmp_path, k) for k in (10, 20, 30)]
    for i, st in enumerate(stored):
        _attach(st, f"ad{i}")
    inside, peak, lock = [0], [0], threading.Lock()
    real = sellers.dhash

    def slow(path):
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.05)
        try:
            return real(path)
        finally:
            with lock:
                inside[0] -= 1

    monkeypatch.setattr(sellers, "dhash", slow)

    def one(_):
        with SessionLocal() as db:
            return sellers.image_hashes(db)

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(one, range(6)))
    assert peak[0] == 1 and len(hashing) == 3
    assert all(r == results[0] and len(r) == 3 for r in results)


def test_image_hashes_busy_request_uses_cache(tmp_path, hashing, monkeypatch):
    """Расчёт хэшей занят дольше HASH_WAIT_S - запрос не ждёт, группы - по уже посчитанным картинкам."""
    done, new = _stored(tmp_path, 40), _stored(tmp_path, 50)
    _attach(done, "ad1")
    with SessionLocal() as db:
        sellers.image_hashes(db)
    _attach(new, "ad2")
    monkeypatch.setattr(sellers, "HASH_WAIT_S", 0.01)
    with sellers._HASH_LOCK, SessionLocal() as db:
        out = sellers.image_hashes(db)
    assert list(out) == ["ad1"] and hashing == [done.sha256]
    with SessionLocal() as db:
        assert set(sellers.image_hashes(db)) == {"ad1", "ad2"}


def test_image_hashes_saved_as_computed(tmp_path, hashing, monkeypatch):
    """Каждый посчитанный хэш попадает в кэш сразу(HASH_SAVE_S) и при сбое посреди расчёта - не теряется."""
    stored = [_stored(tmp_path, k) for k in (60, 70, 80)]
    for i, st in enumerate(stored):
        _attach(st, f"ad{i}")
    monkeypatch.setattr(sellers, "HASH_SAVE_S", 0)
    real, seen = sellers.dhash, []

    def check(path):
        seen.append(len(sellers._load_cache(sellers._cache_path())))
        if len(seen) == 3:
            raise KeyboardInterrupt  # остановка процесса посреди расчёта
        return real(path)

    monkeypatch.setattr(sellers, "dhash", check)
    with pytest.raises(KeyboardInterrupt), SessionLocal() as db:
        sellers.image_hashes(db)
    assert seen == [0, 1, 2] and len(sellers._load_cache(sellers._cache_path())) == 2
    assert not sellers._HASH_LOCK.locked()


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


def test_rooms_and_price_from():
    assert prices.rooms_of("10 комнат на этаже") is None and prices.rooms_of("3-х комнатная") == 3
    assert prices.rooms_of("студия 25 м²") == 0 and prices.rooms_of("двухкомнатная") == 2
    p = prices.parse(ad("a", "@x", "Без доплат за работ 450 000 ₸/м²"))
    assert p["perM2"] == 450_000 and p["from"] is False
    assert prices.parse(ad("a", "@x", "Квартиры от 350 000 ₸/м²"))["from"] is True
    assert prices.parse(ad("a", "@x", "Цена от450 000 ₸/м²"))["from"] is True  # "от" вплотную к числу
    # окно перед ценой начинается с хвоста "работ" - граница слова по всему тексту, а не по срезу
    assert prices.parse(ad("a", "@x", "Без работ -- всего 450 000 ₸/м²"))["from"] is False


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
    ads = [ad(f"k{i}", "@market", "Цена 30 млн, 60 м²") for i in range(6)]  # 500 000 ₸/м²
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


def test_report_orders_zero_below_before_above_market():
    rows = [{"adId": "up", "anomaly": False, "belowPct": -20, "perM2": 1, "fullPayment": None},
            {"adId": "zero", "anomaly": False, "belowPct": 0, "perM2": 1, "fullPayment": None},
            {"adId": "none", "anomaly": False, "belowPct": None, "perM2": None, "fullPayment": None}]
    rep = prices.report({"rows": rows, "median": 1, "n": 2, "byRooms": {}}, include_all=True)
    assert [r["adId"] for r in rep["items"]] == ["zero", "up", "none"]


def test_room_median_used_when_enough():
    ads = [ad(f"o{i}", "@m", "1-комнатная, 40 м², цена 24 млн") for i in range(5)]  # 600 000
    ads += [ad(f"t{i}", "@m", "3-комнатная, 100 м², цена 40 млн") for i in range(5)]  # 400 000
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


def test_affiliations_ignore_empty_card_phones(client):
    # пустой телефон и "нет" в карточках двух застройщиков - не совпадение по телефону
    with SessionLocal() as db:
        for d in db.query(models.Developer):
            d.phones = ["", "нет"]
        db.commit()
    x = client.get("/api/developers/d1/affiliations").json()
    assert all(z["value"] for o in x["related"] for z in o["reasons"] if z["kind"] == "phone")
    assert all(p["digits"] for p in x["phones"])
