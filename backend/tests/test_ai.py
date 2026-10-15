"""ИИ-сценарий (ТЗ §6, T-20…T-25): нормализация и транслитерация, условия оплаты с цитатами и уверенностью,
сопоставление публикации с ЖК, проверка на новых текстах (shared/ai_cases.json), эндпоинты анализа, решений аналитика
и OCR."""
import io
import json
import shutil
import subprocess
import time

from pathlib import Path

import pytest

import app as app_pkg
from PIL import Image, ImageDraw, ImageFont

from app.ai import ENGINE, analyze, seed_catalog
from app.ai import ocr as ocr_engine
from app.ai.evaluate import load_cases, run
from app.ai.matching import W, account_keys, build_catalog
from app.ai.nlp import fold_homoglyphs, norm, phone_digits, phones, skeleton_text, tokens
from app.ai.terms import extract
from app.risk_model import MARKERS, SHARED, T
from app.schemas import AD_TEXT_MAX

AUTHOR = "Исаев Т."
SEED = json.loads((SHARED / "seed_data.json").read_text(encoding="utf-8"))


def _one(terms, kind):
    found = [t for t in terms if t["kind"] == kind]
    assert len(found) == 1, (kind, terms)
    return found[0]


def _top(text, ocr=None, account=None, url=None):
    hyps = analyze(seed_catalog(), text, ocr, account, url=url)["hypotheses"]
    return hyps[0] if hyps else None


def _features(h):
    return {r["feature"] for r in h["reasons"]}


# ---------- нормализация и транслитерация ----------

def test_normalization():
    assert norm("  ЖК «Ёлки»\u00a0—\u202fНовый  “дом” ") == "жк елки - новый дом"
    assert norm("Шығыс–Тауэр") == "шығыс-тауэр"
    # «ЖК», «ж.к.», «жилой комплекс» — не часть названия
    assert [t.norm for t in tokens("ЖК «Алтай Резиденс», ж.к. Nord Hills, жилой комплекс Green Line", drop_stop=True)] == [
        "алтай", "резиденс", "nord", "hills", "green", "line"]
    # позиции токенов — в исходной строке (для дословных цитат)
    text = "Бронь в ЖК «Алтай\u00a0Резиденс»"
    t = tokens(text, drop_stop=True)
    assert [x.norm for x in t] == ["бронь", "в", "алтай", "резиденс"]
    assert text[t[2].start:t[3].end] == "Алтай\u00a0Резиденс"


@pytest.mark.parametrize("a, b", [
    ("Altai Residence", "Алтай Резиденс"), ("Shygys Tower", "Шығыс Тауэр"), ("ШЫГЫС ТАУЭР", "Шығыс Тауэр"),
    ("Nord Hils", "Nord Hills"), ("Dostyk Plaza", "Достык Плаза"), ("Dostyq Plaza", "Достық Плаза"),
    ("Green Line", "Грин Лайн"), ("Bereke City", "Береке Сити"), ("Belukha Park", "Белуха Парк"), ("Нур Алем", "Нұр Әлем"),
    ("Ertis Park", "Ертіс Парк"), ("Алтай Резиденц", "Алтай Резиденс"), ("Jibek Joly", "Жибек Жолы"),
])
def test_translit_skeleton(a, b):
    assert skeleton_text(a) == skeleton_text(b)


def test_phones():
    found = phones("Звоните: +7 7232 00-00-02, 8 (7232) 00 00 04; 87232000006. Цена 7 200 000 000 ₸, взнос 1 500 000 ₸")
    assert [d for _, d, _, _ in found] == ["7232000002", "7232000004", "7232000006"]
    assert found[0][0] == "+7 7232 00-00-02"
    assert phone_digits("+7 (7232) 00-00-02") == "7232000002"
    assert account_keys("https://www.instagram.com/altai.build/") == {"altai.build"}
    assert account_keys("@Altai.Build") == {"altai.build"} and account_keys("https://instagram.com/p/xyz") == set()
    assert account_keys("t.me/nord_hills_uka") == {"nord_hills_uka"} and account_keys("https://ertis-invest.kz/flats") == {
        "ertis-invest.kz"}
    assert account_keys("krisha.kz / собственник") == {"krisha.kz / собственник"}  # продавец, а не вся площадка


# ---------- условия оплаты ----------

@pytest.mark.parametrize("text, kind, values", [
    ("Бронь 500 000 ₸", "booking", {"amount": 500000, "currency": "KZT"}),
    ("Бронь 500 тыс. тг", "booking", {"amount": 500000, "currency": "KZT"}),
    ("Депозит 5 млн тенге", "deposit", {"amount": 5000000, "currency": "KZT"}),
    ("Первый взнос 1,2 млн ₸", "down_payment", {"amount": 1200000}),
    ("Первоначальный взнос 1.5 млн", "down_payment", {"amount": 1500000, "currency": "KZT"}),
    ("Задаток $10 000", "deposit", {"amount": 10000, "currency": "USD"}),
    ("Задаток 3000 $", "deposit", {"amount": 3000, "currency": "USD"}),
    ("Предоплата 100 000 руб.", "deposit", {"amount": 100000, "currency": "RUB"}),
    ("Бронь 50к ₸", "booking", {"amount": 50000}),
    ("Квартиры от 290 000 ₸/м²", "price", {"amount": 290000, "unit": "m2"}),
    ("Цена 270 000 тг за квадрат", "price", {"amount": 270000, "unit": "m2"}),
    ("Цена 310.000 тг/м2", "price", {"amount": 310000, "unit": "m2"}),
    ("Взнос 20%", "down_payment", {"percent": 20}),
    ("30% первый взнос", "down_payment", {"percent": 30}),
    ("Рассрочка 0 % на 24 мес", "installment", {"percent": 0, "months": 24}),
    ("Рассрочка на 2 года", "installment", {"months": 24}),
    ("Рассрочка на полгода без процентов", "installment", {"months": 6, "percent": 0}),
    ("Рассрочка до 36 месяцев", "installment", {"months": 36}),
    ("Вход от 5 млн ₸", "investment", {"amount": 5000000}),
    ("Доходность до 30% к сдаче", "investment", {"percent": 30}),
    ("Бронь квартиры, 200 000 тг", "booking", {"amount": 200000}),  # сумма в соседнем отрезке той же фразы
    ("БРОНЬ50000₸", "booking", {"amount": 50000}),  # OCR без пробелов
    ("Ипотека не нужна — платите напрямую застройщику", "direct_payment", {}),
    ("Продажа по договору долевого участия", "ddu", {}),
    ("Переуступка по ДДУ", "ddu", {}),
    ("Бастапқы жарна 25%", "down_payment", {"percent": 25}),
    ("Бөліп төлеу 12 ай", "installment", {"months": 12}),
    ("Вход от $15к", "investment", {"amount": 15000, "currency": "USD"}),  # «к» и после валюты, а не $15
    ("Первый взнос 1 млн 200 тыс. тенге", "down_payment", {"amount": 1200000, "currency": "KZT"}),
    ("Первый взнос 1 млн 500 тыс тг", "down_payment", {"amount": 1500000}),
    ("Рассрочка на 1,5 года", "installment", {"months": 18}),
    ("Взнос 20–30%", "down_payment", {"percent": 20}),  # диапазон — нижняя граница («от 20%»)
])
def test_term_formats(text, kind, values):
    t = _one(extract(text), kind)
    assert {k: t.get(k) for k in values} == values
    assert t["quote"] in text and t["from"] == "text"
    assert t["money"] == (kind != "price") and t["label"]


@pytest.mark.parametrize("marker", MARKERS["money"])
def test_every_money_marker_is_a_term(marker):
    """Каждый маркер R2 из словаря модели (markers.money) распознаётся как денежное условие."""
    terms = extract(f"Квартиры в новом доме: {marker} 20%, звоните")
    assert any(t["money"] for t in terms), marker


def test_term_quotes_are_verbatim_and_sourced():
    text = "Старт продаж! Бронь от 500\u00a0000 ₸, рассрочка 0% на 24 мес. Звоните!"
    ocr = "ПЕРВЫЙ ВЗНОС 20% · ДДУ"
    terms = extract(text, ocr)
    for t in terms:
        assert t["quote"] in (text if t["from"] == "text" else ocr), t
    assert _one(terms, "booking")["quote"] == "Бронь от 500\u00a0000 ₸"  # дословно, неразрывный пробел сохранён
    assert _one(terms, "installment")["quote"] == "рассрочка 0% на 24 мес."
    assert (_one(terms, "down_payment")["from"], _one(terms, "ddu")["from"]) == ("ocr", "ocr")


def test_term_confidence():
    with_number = _one(extract("Первый взнос 20%"), "down_payment")["confidence"]
    word_only = _one(extract("Первый взнос"), "down_payment")["confidence"]
    from_ocr = _one(extract("", "Первый взнос 20%"), "down_payment")["confidence"]
    assert with_number > word_only and with_number > from_ocr
    # то же условие и в тексте, и на скриншоте — одно условие из текста, уверенность выше
    alone = _one(extract("Рассрочка 24 мес"), "installment")["confidence"]
    both = extract("Рассрочка 24 мес", "РАССРОЧКА 24 МЕС")
    assert len(both) == 1 and both[0]["from"] == "text" and both[0]["confidence"] > alone
    # противоречие: два разных взноса — оба с пониженной уверенностью
    two = [t for t in extract("Первый взнос 20%. Акция: первоначальный взнос 30%") if t["kind"] == "down_payment"]
    assert sorted(t["percent"] for t in two) == [20, 30] and all(t["confidence"] < with_number for t in two)
    # низкая уверенность помечается
    weak = extract("Инвестиции в недвижимость")
    assert weak and all(t["low"] == (t["confidence"] < T["lowConfidence"]) for t in weak)


def test_amount_parts_are_one_amount():
    """«1 млн 200 тыс.» — одна сумма: остаток в тысячах не становится отдельной «ценой», которой в тексте нет."""
    for text in ("Первый взнос 1 млн 200 тыс. тенге", "первый взнос 1 млн 500 тыс тг", "Задаток 2 миллиона 300 тысяч ₸"):
        assert [t["kind"] for t in extract(text)] == [extract(text)[0]["kind"]] and extract(text)[0]["kind"] != "price", text
    assert _one(extract("Задаток 2 миллиона 300 тысяч ₸"), "deposit")["amount"] == 2300000


def test_free_booking_and_negation():
    t = _one(extract("Белуха Парк: бронь бесплатная на 7 дней"), "booking")
    assert t["money"] is False and t["low"] and t["label"] == "Бронь без оплаты"
    # отрицание после слова и «0 тг» — тоже не предложение внести деньги
    for text in ("Nord Hills: бронь не нужна, оплата по ДДУ", "Бронь квартиры не обязательна", "Бронь 0 тг.",
                 "Достык Плаза. Бронь 0 тг! Бронирование бесплатно."):
        t = _one(extract(text), "booking")  # «0 тг» и «бесплатно» — одно условие, а не два противоречащих
        assert (t["money"], t["label"], t["low"]) == (False, "Бронь без оплаты", True), text
    assert extract("Первоначальный взнос не нужен") == [] and extract("Предоплата не требуется") == []
    assert _one(extract("Ипотека не нужна — бронь от застройщика 50 000 тг"), "booking")["money"] is True
    terms = extract("Рассрочка без первоначального взноса! Бронь без предоплаты")
    assert {t["kind"] for t in terms} == {"installment", "booking"}  # «без взноса», «без предоплаты» — не условия
    assert _one(terms, "booking")["money"] is False
    # платная бронь — деньги
    assert _one(extract("Бронь 100 000 ₸"), "booking")["money"] is True


def test_ocr_lookalike_latin_letters():
    """Tesseract (rus+eng) читает кириллическое слово латиницей из похожих букв: «12 mec» — это 12 месяцев. Цитата — дословно
    из распознанного текста; настоящие латинские названия не трогаются."""
    inst = _one(extract("", "Рассрочка 12 mec · ДДУ"), "installment")
    assert (inst["months"], inst["quote"], inst["from"]) == (12, "Рассрочка 12 mec", "ocr")
    assert _one(extract("", "Бронь 100 000 Tr"), "booking")["amount"] == 100000
    for raw, folded in (("Рассрочка 12 mec", "Рассрочка 12 мес"), ("BCE КВАРТИРЫ", "ВСЕ КВАРТИРЫ"), ("36 Mec", "36 Мес"),
                        ("Бpонь", "Бронь"), ("Nord Hills 12 mec", "Nord Hills 12 мес"), ("Green Line · ДДУ", "Green Line · ДДУ"),
                        ("TOP\nквартиры", "TOP\nквартиры")):
        assert fold_homoglyphs(raw) == folded and len(fold_homoglyphs(raw)) == len(raw), raw


def test_long_texts_are_bounded():
    """Длинный текст: цитата — окно слов вокруг условия; тысячи совпадений не делают анализ квадратичным."""
    words = [f"слово{i}" for i in range(120)]
    text = " ".join(words[:60] + ["первый", "взнос", "20%"] + words[60:])  # один отрезок без разделителей
    t = _one(extract(text), "down_payment")
    assert t["quote"] in text and "первый взнос 20%" in t["quote"]
    assert len(t["quote"].split()) == T["quoteMaxWords"] and t["quote"].split()[T["quoteWordsBefore"]] == "первый"
    noisy = " ".join(f"Бронь {i} 000 ₸ · рассрочка {i % 90 + 1} мес" for i in range(1, 700))[:20000]
    started = time.perf_counter()
    an = analyze(seed_catalog(), noisy, noisy.upper(), "@x")
    assert time.perf_counter() - started < 5  # раньше — десятки секунд
    assert an["terms"] and all(x["quote"] in (noisy if x["from"] == "text" else noisy.upper()) for x in an["terms"])
    first = [x for x in an["terms"] if x["kind"] == "booking"][0]
    assert (first["amount"], first["quote"]) == (1000, "Бронь 1 000 ₸")


def test_numbers_that_are_not_money():
    assert extract("Продаю гараж, 18 м², ворота новые, цена договорная") == []
    assert extract("Шығыс Тауэр — 18 этажей, сдача 2028 года, IV кв. 2028") == []
    assert extract("Ремонт квартир, гарантия 2 года, звоните +7 7232 00-00-99") == []
    # площадь рядом с ценой не становится суммой
    assert [t["amount"] for t in extract("2-комн., 64 м², 19 800 000 ₸")] == [19800000]


# ---------- сопоставление с ЖК ----------

def test_match_features():
    h = _top("Старт продаж ЖК «Алтай Резиденс»")
    assert (h["complexId"], h["confidence"], h["low"]) == ("c2", 0.8, False) and _features(h) == {"name"}
    assert h["quote"] == "Алтай Резиденс"
    h = _top("Altai Residence: старт продаж")
    assert h["complexId"] == "c2" and _features(h) == {"translit"} and h["quote"] == "Altai Residence"
    h = _top("Квартиры на Утепова 18")
    assert h["complexId"] == "c11" and _features(h) == {"address"} and not h["low"]
    # та же улица, другой дом — не признак; та же улица без дома — слабый
    assert _top("Квартиры, пр. Сатпаева 52")["complexId"] == "c8"
    assert _top("Квартиры на ул. Утепова, звоните")["low"]
    # официальный аккаунт застройщика с одним строящимся ЖК — гипотеза с низкой уверенностью
    h = _top("Осталось 12 квартир", account="@kvartal.vostok")
    assert (h["complexId"], h["low"]) == ("c9", True) and _features(h) == {"account"}
    assert _top("Осталось 12 квартир", url="https://www.instagram.com/kvartal.vostok/")["complexId"] == "c9"
    # название ЖК в имени аккаунта
    assert _top("Старт продаж", account="@belukha.park.sale")["complexId"] == "c10"
    # телефон застройщика с двумя строящимися ЖК — признак делится между ними
    hyps = analyze(seed_catalog(), "Звоните +7 7232 00-00-02", None, "@x")["hypotheses"]
    assert {h["complexId"] for h in hyps} == {"c2", "c8"} and all(h["low"] and _features(h) == {"phone"} for h in hyps)
    # история: у аккаунта есть подтверждённая аналитиком публикация по ЖК
    h = _top("Успей купить", account="@dostyk.plaza.uka")
    assert h["complexId"] == "c11" and "history" in _features(h)
    # один район — слабый признак: гипотезу не показываем
    assert _top("Квартира на Левобережье") is None


@pytest.mark.parametrize("text", [
    "Продам квартиру на Абая, 9 этаж, 72 м², хороший ремонт",  # 9 — этаж, а не дом 9 (Солнечный квартал, пр. Абая, 9)
    "Агентство на Утепова 18 лет на рынке!",  # стаж, а не дом 18 (Достык Плаза, ул. Утепова, 18)
    "Квартира на Сатпаева, 52 м², ремонт",  # площадь, а не дом 52 (Тихий берег, пр. Сатпаева, 52)
    "Новые квартиры в Казахстане: 66 м², рассрочка",  # страна и площадь, а не ул. Казахстан, 66 (Алтай Резиденс)
    "Квартиры на Абая 9-й этаж", "ул. Утепова 18лет",
])
def test_number_after_street_is_not_always_a_house(text):
    """Число после улицы с единицей («этаж», «м²», «лет») или через знак конца фразы — не номер дома: признака «улица
    и дом» нет, уверенной гипотезы нет (упоминание улицы — слабый признак)."""
    an = analyze(seed_catalog(), text, None, "@x")
    reasons = [r for h in an["hypotheses"] for r in h["reasons"]]
    assert all(h["low"] for h in an["hypotheses"]) and not [r for r in reasons if "улица и дом" in r["detail"]]
    assert not any(ch.isdigit() for a in an["addresses"] for ch in a), an["addresses"]


def test_address_with_and_without_prefix():
    for text in ("Квартиры на Утепова 18", "Квартиры на Утепова, д. 18", "ул.Утепова,18", "Адрес: Утепова 18, звоните"):
        h = _top(text)
        assert (h["complexId"], h["low"], h["reasons"][0]["weight"]) == ("c11", False, W["address"]), text
    # без «ул./пр./на» улица и дом — признак слабее: слово-улица может быть просто словом
    h = _top("Утепова 18 — старт продаж")
    assert (h["complexId"], h["low"], h["reasons"][0]["weight"]) == ("c11", True, W["address_bare"])


def test_name_declension_and_lost_kazakh_letters():
    for text in ("Остались квартиры в ЖК «Тихом береге», рассрочка на полгода.", "Продажа квартир Тихого берега",
                 "Скидки в Солнечном квартале"):
        h = _top(text)
        assert h["complexId"] in ("c8", "c14") and not h["low"] and _features(h) == {"name"}, text
        assert "в другом падеже" in h["reasons"][0]["detail"]
    assert _top("Квартира на тихом берегу Иртыша") is None  # не имя собственное — не название ЖК
    # «Нұр Әлем» без казахских букв (OCR, русское написание): ә → э
    h = _top("Квартиры в ЖК Нур Элем, рассрочка")
    assert (h["complexId"], _features(h)) == ("c13", {"translit"})
    # на скриншоте слово названия латинскими буквами-двойниками (реальный вывод Tesseract): «Hyp Әлем» — это «Нұр Әлем»;
    # цитата — дословно, настоящие латинские названия рядом с кириллицей не ломаются
    h = _top("Подробности на скриншоте", ocr="Hyp Әлем: 2-комн. : 28 000 000 П")
    assert (h["complexId"], h["quote"], h["low"]) == ("c13", "Hyp Әлем", False)
    assert _top("Подробности на скриншоте", ocr="ЖК Bereke City · бронь")["complexId"] == "c7"


def test_note_when_all_found_complexes_are_rejected():
    text = "Береке Сити: бронь 100 000 ₸, успейте!"
    assert _top(text)["complexId"] == "c7" and analyze(seed_catalog(), text)["note"] is None
    an = analyze(seed_catalog(), text, rejected=["c7"])
    assert an["hypotheses"] == [] and "Признаков конкретного ЖК не найдено" not in an["note"]
    assert an["note"] == ("Других гипотез нет: отклонённые аналитиком ЖК (1) не предлагаются — привязку делает аналитик "
                          "вручную")


def test_compilation_and_counterexamples():
    an = analyze(seed_catalog(), "Подборка: Береке Сити, Самал Сити и Ертіс Парк — рассрочка, бронь", None, "@x")
    assert len(an["hypotheses"]) == T["hypothesesTop"] and all(h["low"] for h in an["hypotheses"])
    assert "подборку" in an["note"]
    for text in ("Продаю гараж в кооперативе", "Ремонт квартир под ключ, +7 7232 00-00-99", ""):
        an = analyze(seed_catalog(), text, None, "@x")
        assert an["hypotheses"] == [] and "не найдено" in an["note"]


def test_analysis_is_deterministic():
    case = ("Altai Residence — бронь 200 000 тг, взнос 30%, рассрочка 18 мес. +7 7232 00-00-02", "ДДУ · ПЕРВЫЙ ВЗНОС 30%",
            "@altai.kvartiry", None, "https://instagram.com/altai.kvartiry/")
    first = json.dumps(analyze(seed_catalog(), *case), ensure_ascii=False, sort_keys=True)
    for _ in range(3):
        assert json.dumps(analyze(seed_catalog(), *case), ensure_ascii=False, sort_keys=True) == first
    # порядок ЖК в справочнике не влияет на результат
    shuffled = build_catalog(list(reversed(SEED["complexes"])), list(reversed(SEED["developers"])), SEED["ads"])
    assert json.dumps(analyze(shuffled, *case), ensure_ascii=False, sort_keys=True) == first


def test_seed_ads_match_their_confirmed_complex():
    """На демо-публикациях с подтверждённой привязкой первая гипотеза — тот же ЖК (история самой публикации не учитывается)."""
    for ad in SEED["ads"]:
        if not ad.get("linkConfirmedBy"):
            continue
        cat = build_catalog(SEED["complexes"], SEED["developers"], SEED["ads"], exclude_ad=ad["id"])
        hyps = analyze(cat, ad["text"], ad.get("ocr"), ad["account"], url=ad.get("url"))["hypotheses"]
        assert hyps and hyps[0]["complexId"] == ad["complexId"], ad["id"]


# ---------- проверка на новых данных (T-25) ----------

CASES = load_cases()


def test_cases_file():
    assert len(CASES) >= 10 and len({c["id"] for c in CASES}) == len(CASES)
    seed_texts = {a["text"] for a in SEED["ads"]} | {a.get("ocr") for a in SEED["ads"]}
    assert not any(c.get("text") in seed_texts or (c.get("ocr") and c["ocr"] in seed_texts) for c in CASES)
    texts = " ".join(f"{c.get('text', '')} {c.get('ocr') or ''}" for c in CASES)
    # три кейса из плана
    assert "Altai Residence" in texts and "ШЫГЫС ТАУЭР · ДДУ · ПЕРВЫЙ ВЗНОС 20 %" in texts and "Утепова 18" in texts
    assert {c["expected"]["complexId"] for c in CASES if c["id"] in ("k1", "k2", "k3")} == {"c2", "c4", "c11"}
    # контрпримеры и подборка
    assert any(c["expected"].get("noHypotheses") for c in CASES)
    assert any(c["expected"]["complexId"] is None and not c["expected"].get("noHypotheses") for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    row = next(r for r in run()["cases"] if r["id"] == case["id"])
    assert row["ok"], row["notes"]


def test_eval_metrics():
    m = run()["metrics"]
    assert m["termsExpected"] >= 20 and m["termsFound"] == m["termsExpected"]
    assert m["termsPrecision"] >= 0.9 and m["linkTop1"] >= 0.9 and m["linkCases"] >= 10
    assert m["falsePositives"] == 0


# ---------- эндпоинты ----------

def _log(c, aid):
    return [(e["kind"], e["who"], e["what"]) for e in c.get(f"/api/ads/{aid}/audit").json()]


def _risk(c, cid):
    return c.get(f"/api/complexes/{cid}/risk", params={"asOf": "2026-10-15"}).json()


def _f(r, code):
    return next(f for f in r["factors"] if f["code"] == code)


def test_analyze_endpoint_writes_nothing(client):
    before = (client.get("/api/ads").json(), client.get("/api/audit", params={"limit": 500}).json())
    r = client.post("/api/analyze", json={"text": "Altai Residence — бронь 200 000 тг, взнос 30%. +7 7232 00-00-02",
                                          "ocr": "РАССРОЧКА 18 МЕС", "account": "@altai.kvartiry", "platform": "instagram"})
    assert r.status_code == 200
    an = r.json()
    assert an["engine"] == ENGINE and an["hypotheses"][0]["complexId"] == "c2"
    assert {"engine", "terms", "hypotheses", "signals", "phones", "addresses", "note"} <= set(an)
    assert an["phones"] == ["+7 7232 00-00-02"] and "бронь" in an["signals"]["funds"]
    assert {t["kind"]: t["from"] for t in an["terms"]} == {"booking": "text", "down_payment": "text", "installment": "ocr"}
    t = an["terms"][0]
    assert set(t) == {"kind", "label", "amount", "currency", "quote", "from", "confidence", "low", "money"}
    h = an["hypotheses"][0]
    assert {"complexId", "complexName", "confidence", "low", "reasons", "quote"} <= set(h)
    assert all({"feature", "detail", "weight"} == set(x) for x in h["reasons"])
    assert (client.get("/api/ads").json(), client.get("/api/audit", params={"limit": 500}).json()) == before
    # пустой текст — пустой анализ, а не ошибка; слишком длинный — 422
    empty = client.post("/api/analyze", json={"text": ""}).json()
    assert empty["terms"] == [] and empty["hypotheses"] == []
    assert client.post("/api/analyze", json={"text": "x" * 20001}).status_code == 422
    assert client.post("/api/analyze", json={"text": "a\u0000b"}).status_code == 422


def test_ad_analysis(client):
    an = client.get("/api/ads/a1/analysis").json()
    assert an["hypotheses"][0]["complexId"] == "c2" and an["engine"] == ENGINE
    assert _one(an["terms"], "booking")["amount"] == 500000
    inst = _one(an["terms"], "installment")
    assert (inst["percent"], inst["months"], inst["from"]) == (0, 24, "text")
    assert (an["linkConfirmedBy"], an["linkConfirmedAt"]) == ("Нестерова А.", "2026-10-13T14:02:00+00:00")
    assert (an["termsReview"], an["rejectedLinks"]) == (None, [])
    assert an["note"] is None
    assert client.get("/api/ads/nope/analysis").status_code == 404
    # a4: привязка к «Тихий берег» из сида — гипотеза 71%, а в тексте только район: анализ её не поддерживает, и это видно
    an = client.get("/api/ads/a4/analysis").json()
    assert an["hypotheses"] == [] and an["linkConfirmedBy"] is None
    assert "Текущая привязка к ЖК «Тихий берег» (гипотеза, 71%) анализом не подтверждается" in an["note"]
    # a9 (подборка): текущая привязка среди гипотез, но сохранена с другой уверенностью — у одной привязки одна
    # уверенность, расхождение объяснено прямо
    an = client.get("/api/ads/a9/analysis").json()
    green = next(h for h in an["hypotheses"] if h["complexId"] == "c12")
    assert (green["confidence"], green["low"]) == (0.34, True)
    assert ("Текущая привязка к ЖК «Green Line» — гипотеза с уверенностью 54% при сборе; по текущему анализу — 34%, "
            "низкая уверенность") in an["note"]
    # совпадающая уверенность — без пояснения
    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@x", "text": "ЖК Green Line — старт продаж"}).json()
    assert ad["matchConfidence"] == 0.8 and client.get(f"/api/ads/{ad['id']}/analysis").json()["note"] is None
    # предел длины текста — как у /api/analyze: анализ длинного текста ограничен по времени
    body = {"platform": "instagram", "account": "@x", "text": "x" * 20001}
    r = client.post("/api/ads", json=body)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "text"
    assert client.post("/api/ads", json={**body, "text": "т", "ocr": "x" * 20001}).status_code == 422


def test_accept_hypothesis(client):
    # a9 — подборка: привязка к Green Line — гипотеза (54% из сида); R3 у c12 — «нужна проверка»
    assert _f(_risk(client, "c12"), "R3")["state"] == "unknown"
    hyps = client.get("/api/ads/a9/analysis").json()["hypotheses"]
    green = next(h for h in hyps if h["complexId"] == "c12")
    r = client.post("/api/ads/a9/hypotheses/accept", json={"author": AUTHOR, "complexId": "c12"})
    assert r.status_code == 200
    ad = r.json()
    assert (ad["complexId"], ad["linkConfirmedBy"], ad["matchConfidence"]) == ("c12", AUTHOR, green["confidence"])
    assert ad["linkConfirmedAt"].endswith("+00:00")
    # уверенность — по текущему анализу (34%, низкая), а не сохранённая при сборе (54%): расхождение — в журнале
    assert _log(client, "a9")[-1] == (
        "ai", AUTHOR, "Гипотеза ИИ принята: ЖК «Green Line» (уверенность 34% — низкая; при сборе — 54%)")
    assert _f(_risk(client, "c12"), "R3")["state"] == "hit"  # привязка — факт: реклама засчитана
    # повтор — без нового события
    n = len(_log(client, "a9"))
    client.post("/api/ads/a9/hypotheses/accept", json={"author": AUTHOR, "complexId": "c12"})
    assert len(_log(client, "a9")) == n
    # другой ЖК: прежний теряет публикацию — событие и в его журнале
    client.post("/api/ads/a9/hypotheses/accept", json={"author": "Омарова Ж.", "complexId": "c10"})
    gone = [e for e in client.get("/api/complexes/c12/audit").json() if e["adId"] == "a9"][-1]
    assert (gone["kind"], gone["who"], gone["what"]) == (
        "link", "Омарова Ж.", "Публикация отвязана от ЖК «Green Line»: принята гипотеза ИИ — ЖК «Белуха Парк»")
    assert _log(client, "a9")[-1][2] == "Гипотеза ИИ принята: ЖК «Белуха Парк» (уверенность 34% — низкая)"
    # ошибки
    assert client.post("/api/ads/a9/hypotheses/accept", json={"author": AUTHOR, "complexId": "nope"}).status_code == 404
    assert client.post("/api/ads/nope/hypotheses/accept", json={"author": AUTHOR, "complexId": "c1"}).status_code == 404
    assert client.post("/api/ads/a9/hypotheses/accept", json={"author": " ", "complexId": "c1"}).status_code == 422


def test_accept_only_what_ai_offered(client):
    """Принять можно гипотезу ИИ или текущую привязку-гипотезу; ЖК, которого ИИ не предлагал, — это ручная привязка."""
    n = len(_log(client, "a4"))
    r = client.post("/api/ads/a4/hypotheses/accept", json={"author": AUTHOR, "complexId": "c1"})
    assert r.status_code == 422 and "ИИ для этой публикации не предлагал" in r.json()["detail"]
    a4 = next(a for a in client.get("/api/ads").json() if a["id"] == "a4")
    assert (a4["complexId"], a4["matchConfidence"], a4["linkConfirmedBy"]) == ("c8", 0.71, None) and len(_log(client, "a4")) == n
    # текущая привязка-гипотеза, которую анализ не находит, подтверждается с уверенностью при сборе — и это видно
    r = client.post("/api/ads/a4/hypotheses/accept", json={"author": AUTHOR, "complexId": "c8"}).json()
    assert (r["complexId"], r["matchConfidence"], r["linkConfirmedBy"]) == ("c8", 0.71, AUTHOR)
    assert _log(client, "a4")[-1] == ("ai", AUTHOR, "Гипотеза ИИ принята: ЖК «Тихий берег» (уверенность при сборе 71%; "
                                                    "текущий анализ эту привязку не поддерживает)")
    # отклонённую и снятую привязку без признаков в тексте «принять» нельзя: «уверенность 0%» в журнал не попадает
    client.patch("/api/ads/a4/complex", json={"complexId": None, "author": AUTHOR})
    r = client.post("/api/ads/a4/hypotheses/accept", json={"author": AUTHOR, "complexId": "c8"})
    assert r.status_code == 422
    assert not [w for k, _, w in _log(client, "a4") if "уверенность 0%" in w]


def _stale(aid):
    """Сессия, в которой публикация уже прочитана (как у второго аналитика, открывшего её раньше чужого решения), и сама
    публикация: ссылка на неё держит объект в сессии (карта объектов сессии хранит их по слабым ссылкам)."""
    from app import models
    from app.db import SessionLocal

    db = SessionLocal()
    return db, db.get(models.Ad, aid)


@pytest.mark.parametrize("first, second", [
    (("reject", {"complexId": "c12"}), ("accept", {"complexId": "c12"})),
    (("accept", {"complexId": "c12"}), ("reject", {"complexId": "c12"})),
    (("accept", {"complexId": "c12"}), ("accept", {"complexId": "c10"})),
    (("reject", {"complexId": "c3"}), ("reject", {"complexId": "c10"})),
    (("complex", {"complexId": None}), ("accept", {"complexId": "c12"})),
    (("terms", {"decision": "rejected"}), ("terms", {"decision": "accepted"})),
])
def test_decision_on_stale_data_is_409(client, first, second):
    """Два аналитика решают по одной публикации почти одновременно: второе решение (по устаревшим данным) — 409, а не
    смесь двух решений («факт привязки» без ЖК) и журнал, противоречащий состоянию."""
    from fastapi import HTTPException

    from app import main, schemas

    calls = {
        "accept": lambda db, who, b: main.accept_hypothesis("a9", schemas.HypothesisIn(author=who, **b), db),
        "reject": lambda db, who, b: main.reject_hypothesis("a9", schemas.HypothesisRejectIn(author=who, **b), db),
        "complex": lambda db, who, b: main.set_complex("a9", schemas.AdComplexIn(author=who, **b), db),
        "terms": lambda db, who, b: main.terms_review("a9", schemas.TermsReviewIn(author=who, **b), db),
    }
    late, seen = _stale("a9")  # второй аналитик открыл публикацию до решения первого
    try:
        body = {"author": "Аналитик А", **first[1]}
        r = (client.patch("/api/ads/a9/complex", json=body) if first[0] == "complex" else
             client.post({"accept": "/api/ads/a9/hypotheses/accept", "reject": "/api/ads/a9/hypotheses/reject",
                          "terms": "/api/ads/a9/terms-review"}[first[0]], json=body))
        assert r.status_code == 200 and seen.decision_rev == 0, r.json()
        state, log = client.get("/api/ads").json(), _log(client, "a9")
        with pytest.raises(HTTPException) as e:
            calls[second[0]](late, "Аналитик Б", second[1])
        assert e.value.status_code == 409 and "Публикацию уже изменили: сейчас" in e.value.detail
    finally:
        late.close()
    assert (client.get("/api/ads").json(), _log(client, "a9")) == (state, log)  # решение второго не записано
    a9 = next(a for a in state if a["id"] == "a9")
    assert a9["complexId"] is not None or a9["linkConfirmedBy"] is None


def test_parallel_decisions_stay_consistent(client, monkeypatch):
    """Одновременные accept и reject одной гипотезы на живом API: accept прочитал публикацию и считает гипотезы, тем
    временем reject снял привязку — accept получает 409; состояние и журнал согласованы (нет «факта привязки» без ЖК)."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app import main

    ad = client.post("/api/ads", json={"platform": "instagram", "account": "@x",
                                       "text": "ЖК Green Line, ул. Кирова 59 — старт продаж, бронь 100 000 тг"}).json()
    assert (ad["complexId"], ad["linkConfirmedBy"]) == ("c12", None)
    started, proceed = threading.Event(), threading.Event()
    slow = main.ai.all_hypotheses

    def held(*args):
        started.set()
        proceed.wait(5)
        return slow(*args)

    monkeypatch.setattr(main.ai, "all_hypotheses", held)
    url = f"/api/ads/{ad['id']}/hypotheses/"
    with ThreadPoolExecutor(1) as pool:
        accept = pool.submit(client.post, url + "accept", json={"author": "Аналитик А", "complexId": "c12"})
        assert started.wait(5)
        reject = client.post(url + "reject", json={"author": "Аналитик Б", "complexId": "c12"})
        proceed.set()
        assert (reject.status_code, accept.result().status_code) == (200, 409)
    assert "Публикацию уже изменили: сейчас ЖК не привязан" in accept.result().json()["detail"]
    now = next(a for a in client.get("/api/ads").json() if a["id"] == ad["id"])
    assert (now["complexId"], now["linkConfirmedBy"], now["rejectedLinks"]) == (None, None, ["c12"])
    assert [w for k, _, w in _log(client, ad["id"]) if k == "ai"][-1] == "Гипотеза ИИ отклонена: ЖК «Green Line», привязка снята"


def test_reject_hypothesis(client):
    # не текущая привязка: ЖК больше не предлагается
    r = client.post("/api/ads/a9/hypotheses/reject", json={"author": AUTHOR, "complexId": "c3", "comment": "  это подборка  "})
    assert r.status_code == 200 and r.json()["rejectedLinks"] == ["c3"] and r.json()["complexId"] == "c12"
    assert _log(client, "a9")[-1] == ("ai", AUTHOR, "Гипотеза ИИ отклонена: ЖК «Ульба Ривер». Комментарий: это подборка")
    an = client.get("/api/ads/a9/analysis").json()
    assert "c3" not in [h["complexId"] for h in an["hypotheses"]] and an["rejectedLinks"] == ["c3"]
    assert "Отклонённые аналитиком ЖК не предлагаются: 1" in an["note"]
    assert "Признаков конкретного ЖК не найдено" not in an["note"]
    # повтор без комментария — без события
    n = len(_log(client, "a9"))
    client.post("/api/ads/a9/hypotheses/reject", json={"author": AUTHOR, "complexId": "c3"})
    assert len(_log(client, "a9")) == n
    # текущая привязка-гипотеза снимается
    r = client.post("/api/ads/a9/hypotheses/reject", json={"author": AUTHOR, "complexId": "c12"}).json()
    assert (r["complexId"], r["matchConfidence"], r["rejectedLinks"]) == (None, 0, ["c3", "c12"])
    assert _log(client, "a9")[-1] == ("ai", AUTHOR, "Гипотеза ИИ отклонена: ЖК «Green Line», привязка снята")
    assert _f(_risk(client, "c12"), "R3")["state"] == "clear"
    # принятие после отклонения возвращает ЖК в выдачу
    r = client.post("/api/ads/a9/hypotheses/accept", json={"author": AUTHOR, "complexId": "c12"}).json()
    assert (r["complexId"], r["rejectedLinks"]) == ("c12", ["c3"])
    # подтверждённую привязку отклонением гипотезы не отменить
    r = client.post("/api/ads/a1/hypotheses/reject", json={"author": AUTHOR, "complexId": "c2"})
    assert r.status_code == 409 and "подтверждена аналитиком (Нестерова А.)" in r.json()["detail"]
    assert client.post("/api/ads/a1/hypotheses/reject", json={"author": AUTHOR, "complexId": "nope"}).status_code == 404
    assert client.post("/api/ads/a1/hypotheses/reject", json={"author": AUTHOR, "complexId": "c3",
                                                               "comment": "x" * 2001}).status_code == 422
    # ручная привязка к отклонённому ЖК снимает отклонение
    client.patch("/api/ads/a9/complex", json={"complexId": "c3", "author": AUTHOR})
    assert next(a for a in client.get("/api/ads").json() if a["id"] == "a9")["rejectedLinks"] == []


def test_terms_review_vetoes_r2(client):
    # c2: R2 даёт единственная публикация a1 (бронь) — кейс B, 70, красная
    r = _risk(client, "c2")
    assert (r["score"], r["zone"], _f(r, "R2")["state"]) == (70, "red", "hit")
    body = {"author": AUTHOR, "decision": "rejected", "comment": "это цена, не взнос"}
    res = client.post("/api/ads/a1/terms-review", json=body)
    assert res.status_code == 200
    ad = res.json()
    assert (ad["termsReview"], ad["termsReviewBy"]) == ("rejected", AUTHOR) and ad["termsReviewAt"].endswith("+00:00")
    assert _log(client, "a1")[-1] == (
        "ai", AUTHOR, "Аналитик отклонил: это не предложение внести деньги. Комментарий: это цена, не взнос")
    r = _risk(client, "c2")
    assert _f(r, "R2")["state"] == "clear" and _f(r, "R3")["state"] == "hit" and (r["score"], r["zone"]) == (45, "yellow")
    s = client.get("/api/ads/a1/score").json()
    assert _f(s, "R2")["state"] == "clear"
    assert client.get("/api/ads/a1/analysis").json()["termsReview"] == "rejected"
    # повтор того же решения без комментария — без события; другое решение — меняет его
    n = len(_log(client, "a1"))
    client.post("/api/ads/a1/terms-review", json={"author": AUTHOR, "decision": "rejected"})
    assert len(_log(client, "a1")) == n
    ad = client.post("/api/ads/a1/terms-review", json={"author": "Омарова Ж.", "decision": "accepted"}).json()
    assert (ad["termsReview"], ad["termsReviewBy"]) == ("accepted", "Омарова Ж.")
    assert _log(client, "a1")[-1] == ("ai", "Омарова Ж.", "Условия оплаты (ИИ) подтверждены аналитиком")
    assert (_risk(client, "c2")["score"], _f(_risk(client, "c2"), "R2")["state"]) == (70, "hit")
    # журнал ЖК видит решение; фильтр общего журнала по kind=ai
    assert any(e["kind"] == "ai" and e["adId"] == "a1" for e in client.get("/api/complexes/c2/audit").json())
    assert {e["kind"] for e in client.get("/api/audit", params={"kind": "ai", "limit": 500}).json()} == {"ai"}
    # ошибки
    for bad in ({"author": AUTHOR, "decision": "maybe"}, {"author": "", "decision": "accepted"}, {"decision": "accepted"}):
        assert client.post("/api/ads/a1/terms-review", json=bad).status_code == 422, bad
    assert client.post("/api/ads/nope/terms-review", json={"author": AUTHOR, "decision": "accepted"}).status_code == 404


def test_signal_snapshot_carries_link_decision(client):
    s = client.post("/api/signals", json={"complexId": "c2", "author": AUTHOR}).json()
    a1 = s["evidence"]["ads"][0]
    assert (a1["linkConfirmedBy"], a1["linkConfirmedAt"], a1["termsReview"]) == (
        "Нестерова А.", "2026-10-13T14:02:00+00:00", None)


def test_eval_endpoint(client):
    r = client.get("/api/ai/eval")
    assert r.status_code == 200
    ev = r.json()
    assert ev["engine"] == ENGINE and len(ev["cases"]) == len(CASES) and all(c["ok"] for c in ev["cases"])
    assert ev["metrics"]["falsePositives"] == 0 and ev["metrics"]["linkTop1"] >= 0.9
    case = ev["cases"][0]
    assert {"id", "text", "expected", "got", "ok", "notes"} <= set(case)


# ---------- OCR ----------

def _png(size=(320, 80), text="Бронь 500 000 ₸", mode="RGB") -> bytes:
    im = Image.new(mode, size, "white")
    font = ImageFont.truetype(str(Path(app_pkg.__file__).parent / "fonts" / "NotoSans-Regular.ttf"), 28)
    ImageDraw.Draw(im).text((10, 20), text, fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _upload(c, data: bytes, name="shot.png", ctype="image/png"):
    return c.post("/api/ocr", files={"file": (name, data, ctype)})


TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
       "1\t1\t0\t0\t0\t0\t0\t0\t960\t240\t-1\t\n"
       "5\t1\t1\t1\t1\t1\t10\t10\t90\t30\t96\tБронь\n"
       "5\t1\t1\t1\t1\t2\t110\t10\t60\t30\t90\t500\n"
       "5\t1\t1\t1\t1\t3\t180\t10\t60\t30\t84\t000\n"
       "5\t1\t1\t1\t2\t1\t10\t50\t60\t30\t-1\t \n"
       "5\t1\t2\t1\t1\t1\t10\t90\t120\t30\t70\tДДУ\n")


@pytest.fixture
def fake_tesseract(monkeypatch):
    """Подмена вызова tesseract: версия, языки (без kaz) и TSV; аргументы распознавания запоминаются."""
    calls = []

    def run(args, data=None, timeout=10):
        calls.append(args)
        if args[1:] == ["--version"]:
            out = b"tesseract 5.3.0\n leptonica-1.82.0\n"
        elif args[1:] == ["--list-langs"]:
            out = b'List of available languages in "/usr/share/tesseract-ocr/5/tessdata/" (3):\neng\nosd\nrus\n'
        else:
            with Image.open(args[1]) as im:
                calls.append(("image", im.mode, im.size))
            out = TSV.encode()
        return subprocess.CompletedProcess(args, 0, out, b"")

    monkeypatch.setattr(ocr_engine, "_which", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr_engine, "_run", run)
    return calls


def test_ocr_recognizes_with_preprocessing(client, fake_tesseract):
    st = client.get("/api/ocr/status").json()
    assert st == {"available": True, "engine": "tesseract", "version": "5.3.0", "languages": ["rus", "eng"],
                  "detail": "Нет языковых пакетов: kaz — распознаём на rus+eng"}
    r = _upload(client, _png(mode="RGBA"))
    assert r.status_code == 200
    res = r.json()
    assert res["text"] == "Бронь 500 000\n\nДДУ" and res["engine"] == "tesseract" and res["languages"] == ["rus", "eng"]
    assert res["confidence"] == 0.85 and isinstance(res["durationMs"], int) and res["version"] == "5.3.0"
    assert res["preprocess"][:2] == ["прозрачность на белом фоне", "оттенки серого"] and res["preprocess"][-1] == "автоконтраст"
    assert any(s.startswith("увеличение ×") for s in res["preprocess"])
    args = next(a for a in fake_tesseract if isinstance(a, list) and "tsv" in a)
    assert args[args.index("-l") + 1] == "rus+eng"
    image = next(c for c in fake_tesseract if c[0] == "image")
    assert image[1] == "L" and image[2][0] >= 960  # серое и увеличенное (мелкий скриншот 320 px)
    # распознанный текст — сразу в анализ: условие из OCR помечено источником
    an = client.post("/api/analyze", json={"text": "", "ocr": res["text"]}).json()
    assert {t["kind"]: t["from"] for t in an["terms"]} == {"booking": "ocr", "ddu": "ocr"}


def test_ocr_rejects_bad_files(client, fake_tesseract):
    r = _upload(client, b"%PDF-1.7 not an image", "doc.pdf", "application/pdf")
    assert r.status_code == 415 and "Это не изображение" in r.json()["detail"]
    r = _upload(client, b"just text", "note.txt", "text/plain")
    assert r.status_code == 415
    r = _upload(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert r.status_code == 422 and "повреждён" in r.json()["detail"]
    r = _upload(client, b"garbage bytes", "shot.png", "image/png")
    assert r.status_code == 422 and "повреждён" in r.json()["detail"]
    assert _upload(client, b"").status_code == 422
    r = _upload(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * (ocr_engine.MAX_BYTES + 1))
    assert r.status_code == 413 and "10 МБ" in r.json()["detail"]
    assert client.post("/api/ocr").status_code == 422  # без файла
    # распознавание не запускалось ни разу
    assert not [a for a in fake_tesseract if isinstance(a, list) and "tsv" in a]


def test_ocr_errors_are_plain_russian(client, fake_tesseract, caplog):
    """Аналитику — понятный русский текст без внутренностей Pillow («cannot identify image file <_io.BytesIO …>»,
    «image file is truncated»); техническая причина — в журнал API. HEIC с iPhone — изображение, но не тот формат."""
    png = _png()
    jpeg = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(jpeg, "JPEG")
    for data, ctype in ((b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png"), (png[:len(png) // 2], "image/png"),
                        (jpeg.getvalue()[:len(jpeg.getvalue()) // 2], "image/jpeg")):
        with caplog.at_level("WARNING", logger="saq.ocr"):
            r = _upload(client, data, "shot", ctype)
        detail = r.json()["detail"]
        assert r.status_code == 422 and "повреждён и не читается — сохраните скриншот заново" in detail, detail
        assert not any(w in detail for w in ("BytesIO", "cannot", "truncated", "Truncated", "<"))
    assert "не декодируется" in caplog.text  # причина — в журнале
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
    for data, name, ctype, fmt in ((heic, "IMG_0001.HEIC", "image/heic", "HEIC"),
                                   (heic, "x.bin", "application/octet-stream", "HEIC"), (svg, "a.svg", "image/svg+xml", "SVG"),
                                   (b"\x00\x00\x01\x00junk", "favicon.ico", "image/x-icon", "image/x-icon")):
        r = _upload(client, data, name, ctype)
        assert r.status_code == 415 and r.json()["detail"] == (
            f"Формат {fmt} не поддерживается — сохраните скриншот как PNG или JPEG"), (name, r.json())


def test_ocr_text_is_clipped_to_ad_limit(client, fake_tesseract, monkeypatch):
    """Распознанный текст длиннее предела публикации обрезается по строке — иначе после OCR ломались бы и анализ, и
    сохранение (422 на поле ocr); аналитику — пояснение."""
    rows = [f"5\t1\t1\t1\t{n // 8 + 1}\t{n % 8 + 1}\t0\t0\t10\t10\t90\tслово{n:05d}" for n in range(4000)]
    long_tsv = TSV.splitlines()[0] + "\n" + "\n".join(rows) + "\n"
    plain_run = ocr_engine._run
    monkeypatch.setattr(ocr_engine, "_run", lambda args, data=None, timeout=10: (
        subprocess.CompletedProcess(args, 0, long_tsv.encode(), b"") if "tsv" in args else plain_run(args)))
    res = _upload(client, _png()).json()
    last = res["text"].split()[-1]
    assert len(res["text"]) <= AD_TEXT_MAX and last.startswith("слово") and len(last) == len("слово00000")  # без обрубка
    assert "Распознанный текст длиннее 20 000 символов — обрезан, проверьте его" in res["detail"]
    assert client.post("/api/analyze", json={"text": "", "ocr": res["text"]}).status_code == 200


def test_ocr_upload_limit_before_reading_body(client, monkeypatch):
    """Больше 10 МБ — 413 сразу по Content-Length (тело не читается и не ложится во временный файл), а без Content-Length
    (chunked) — как только прочитано больше предела."""
    called = []
    monkeypatch.setattr(ocr_engine, "recognize", lambda *a: called.append(a))
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (ocr_engine.MAX_BYTES + 200_000)
    r = _upload(client, big)
    assert (r.status_code, r.json()["detail"]) == (413, ocr_engine.TOO_BIG) and not called
    head = (b"--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n"
            b"Content-Type: image/png\r\n\r\n")

    def chunks():
        yield head
        for _ in range(12):
            yield b"\x00" * (1024 * 1024)
        yield b"\r\n--b--\r\n"

    r = client.post("/api/ocr", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert (r.status_code, r.json()["detail"]) == (413, ocr_engine.TOO_BIG) and not called
    # по Content-Length ответ уходит, не прочитав из тела ни байта и не вызывая приложение
    import asyncio

    from app.main import UploadLimit

    sent, reads = [], []

    async def app_(scope, receive, send):
        raise AssertionError("обработчик не должен вызываться")

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/ocr",
             "headers": [(b"content-length", str(UploadLimit.LIMIT + 1).encode())]}
    asyncio.run(UploadLimit(app_)(scope, receive, send))
    assert sent[0]["status"] == 413 and json.loads(sent[1]["body"])["detail"] == ocr_engine.TOO_BIG and not reads


def test_ocr_too_many_pixels(client, fake_tesseract, monkeypatch):
    monkeypatch.setattr(ocr_engine, "MAX_PIXELS", 10_000)
    r = _upload(client, _png())
    assert r.status_code == 413 and "слишком большое" in r.json()["detail"]


def test_ocr_without_tesseract(client, monkeypatch):
    monkeypatch.setattr(ocr_engine, "_which", lambda: None)
    st = client.get("/api/ocr/status").json()
    assert st["available"] is False and st["engine"] == "none" and "не установлен" in st["detail"]
    r = _upload(client, _png())
    assert r.status_code == 200 and r.json()["engine"] == "none" and r.json()["text"] == ""
    assert "не установлен" in r.json()["detail"]
    # проверки файла работают и без движка
    assert _upload(client, b"text", "a.txt", "text/plain").status_code == 415


def test_ocr_engine_failures(client, monkeypatch, fake_tesseract):
    def failing(args, data=None, timeout=10):
        if "tsv" in args:
            raise subprocess.TimeoutExpired(args, timeout)
        return fake_tesseract_run(args)

    fake_tesseract_run = ocr_engine._run
    monkeypatch.setattr(ocr_engine, "_run", failing)
    r = _upload(client, _png())
    assert r.status_code == 200 and r.json()["text"] == "" and "не уложилось" in r.json()["detail"]

    def crashing(args, data=None, timeout=10):
        if "tsv" in args:
            return subprocess.CompletedProcess(args, 1, b"", b"Error: unknown language\n")
        return fake_tesseract_run(args)

    monkeypatch.setattr(ocr_engine, "_run", crashing)
    r = _upload(client, _png())
    assert r.status_code == 200 and "ошибкой: Error: unknown language" in r.json()["detail"]


@pytest.mark.skipif(shutil.which(ocr_engine.TESSERACT) is None, reason="Tesseract не установлен (проверяется в Docker-образе)")
def test_ocr_real_tesseract():
    res = ocr_engine.recognize(_png((900, 120), "Бронь 500 000 тг, первый взнос 30%"), "image/png")
    assert res["engine"] == "tesseract" and "взнос" in res["text"].lower()


def test_phone_regex_linear_on_long_separators():
    # раньше «[\\s\\-()]*» давал квадратичный перебор: 20 КБ пробелов — около 5 с
    import time
    for text in ("x" + " " * 20000 + "1", "-" * 50000, "(" * 30000 + "7"):
        t = time.perf_counter()
        phones(text)
        assert time.perf_counter() - t < 0.5
    assert [d for _, d, _, _ in phones("Звоните +7 (7232) 00-00-02 или 8 777 123 45 67")] == ["7232000002", "7771234567"]
