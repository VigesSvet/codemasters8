"""Модель риска по ТЗ §5: контрольные кейсы A/B/C, границы зон, факторы R1–R6, наблюдения, оценка публикации, эталон.

Только чистые функции (app.risk / app.risk_model / app.golden) — без API и БД.
Синтетические входы — shared/risk_fixtures.json; ожидания ниже записаны явно, а не берутся из эталона.
"""
import copy
import itertools
import json
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import golden, risk
from app.risk import hits, link_confirmed, quote, score_ad, score_complex
from app.risk_model import ALMATY, FACTORS, MARKERS, MODEL, OBSERVATIONS, SHARED, T, fill, fmt_date, zone_for

AS_OF = "2026-10-15"
REGEN = "cd backend && .venv/bin/python -m app.golden"
SEED = json.loads((SHARED / "seed_data.json").read_text(encoding="utf-8"))
FX = json.loads((SHARED / "risk_fixtures.json").read_text(encoding="utf-8"))
CX_FX = {f["name"]: f for f in FX["complex"]}
AD_FX = {f["name"]: f for f in FX["ad"]}
DEVS = {d["id"]: d for d in SEED["developers"]}
SEED_CX = {c["id"]: c for c in SEED["complexes"]}
CODES = ["R1", "R2", "R3", "R4", "R5", "R6"]


def run(name, as_of=None):
    f = CX_FX[name]
    return score_complex(f["complex"], f.get("developer"), f.get("ads", []), f.get("appeals", []), as_of or f.get("asOf", AS_OF))


def run_ad(name):
    f = AD_FX[name]
    return score_ad(f["ad"], f.get("complex"))


def run_seed(cid, as_of=AS_OF):
    c = SEED_CX[cid]
    return score_complex(c, DEVS.get(c["developerId"]), SEED["ads"], SEED["appeals"], as_of)


def fac(r, code):
    return next(f for f in r["factors"] if f["code"] == code)


def obs(r, code):
    return next(o for o in r["observations"] if o["code"] == code)


def hit_codes(r):
    return [f["code"] for f in r["factors"] if f["state"] == "hit"]


def unknown_codes(r):
    return [f["code"] for f in r["factors"] if f["state"] == "unknown"]


# ---------- модель: веса, пороги, тексты ----------

def test_weights_and_order():
    assert [f["code"] for f in MODEL["factors"]] == CODES
    assert {c: FACTORS[c]["weight"] for c in CODES} == {"R1": 35, "R2": 25, "R3": 10, "R4": 15, "R5": 10, "R6": 5}
    assert sum(f["weight"] for f in MODEL["factors"]) == 100 == MODEL["maxScore"]


def test_zone_thresholds():
    z = MODEL["zones"]
    assert (z["yellowFrom"], z["redFrom"], z["redRequires"]) == (25, 60, ["R1", "R2"])
    assert [i["zone"] for i in z["items"]] == ["green", "yellow", "red"]


def test_rule_thresholds():
    assert (T["linkConfirmed"], T["readinessGap"], T["idleDays"], T["idlePhotosMin"], T["overdueDays"], T["idleShortDays"]) == \
        (0.8, 20, 90, 2, 180, 30)
    assert T["idleActivities"] == ["нет", "низкая"]
    # автопривязка по названию — гипотеза: её уверенность ниже порога факта
    assert 0 < T["autoLinkConfidence"] < T["linkConfirmed"]


def test_control_cases_declared():
    cases = {c["id"]: c for c in MODEL["cases"]}
    assert {k: (c["complexId"], c["score"], c["zone"], c["hits"]) for k, c in cases.items()} == {
        "A": ("c14", 35, "yellow", ["R2", "R3"]),
        "B": ("c2", 70, "red", ["R1", "R2", "R3"]),
        "C": ("c10", 10, "green", ["R3"]),
    }
    assert cases["A"]["unknown"] == ["R1"]
    for c in cases.values():
        assert sum(FACTORS[h]["weight"] for h in c["hits"]) == c["score"]


# Все ключи пояснений, которые использует risk.py. Добавили ветку в движок — добавьте текст в модель и сюда.
REASONS = {
    "R1": {"built", "ok_guarantee", "ok_guarantee_no_mio", "ok_mio", "absent", "absent_unsourced", "mio_without_bank", "unknown"},
    "R2": {"built", "offer", "offer_unlinked", "none"},
    "R3": {"built", "ads", "ads_unlinked", "none"},
    "R4": {"verified", "pending", "none"},
    "R5": {"built", "mismatch", "match", "incomplete"},
    "R6": {"built", "overdue", "idle", "idle_unconfirmed", "no_photos", "active"},
}
AD_REASONS = {"R1_no_sale", "R1_no_complex", "R1_link_unconfirmed", "R2_offer", "R3_ads", "none"}
OBS_STATES = {"O1": {"ok", "flag", "unknown"}, "O2": {"ok", "flag", "unknown"}, "O3": {"ok", "flag"}}


def test_reason_texts_complete():
    assert {c: set(FACTORS[c]["reasons"]) for c in CODES} == REASONS
    assert set(MODEL["ad"]["reasons"]) == AD_REASONS
    assert {k: set(o["texts"]) for k, o in OBSERVATIONS.items()} == OBS_STATES
    assert set(MODEL["summaries"]) == {"red", "yellow_high", "yellow", "green", "needs_check"}
    for c in CODES:
        f = FACTORS[c]
        assert f["title"] and f["short"] and f["when"] and f["source"] and f["example"], c
        assert all(t.strip() for t in f["reasons"].values()), c


def test_reasons_used_in_engine_exist():
    """Литеральные вызовы _factor(...) в risk.py ссылаются только на существующие тексты."""
    src = (Path(__file__).resolve().parents[1] / "app" / "risk.py").read_text(encoding="utf-8")
    used = re.findall(r'_factor\("(R\d)", "(?:hit|clear|unknown)", "(\w+)"', src)
    assert len(used) >= 20
    for code, reason in used:
        assert reason in FACTORS[code]["reasons"] or reason in MODEL["ad"]["reasons"], (code, reason)
    for a, b in re.findall(r'\("(\w+)", "(\w+)"\) if code == "R2"', src) + re.findall(r'else \("(\w+)", "(\w+)"\)', src):
        assert {a, b} <= set(FACTORS["R2"]["reasons"]) | set(FACTORS["R3"]["reasons"])


def test_markers_dictionaries():
    assert {"бронь", "депозит", "первоначальный взнос", "рассрочк"} <= set(MARKERS["money"])
    assert {"старт продаж", "квартир"} <= set(MARKERS["sale"])
    assert not set(m.lower() for m in MARKERS["money"]) & set(m.lower() for m in MARKERS["sale"])


# ---------- зоны ----------

@pytest.mark.parametrize("score,hit,zone", [
    (0, [], "green"), (24, [], "green"), (24, ["R1", "R2"], "green"),
    (25, [], "yellow"), (25, ["R2"], "yellow"), (59, [], "yellow"), (59, ["R1", "R2"], "yellow"),
    (60, ["R1", "R2"], "red"), (60, ["R1"], "yellow"), (60, ["R2"], "yellow"), (60, [], "yellow"),
    (60, ["R1", "R3", "R4"], "yellow"), (60, ["R2", "R3", "R4", "R5"], "yellow"),
    (85, ["R1", "R2", "R3", "R4"], "red"), (100, CODES, "red"), (100, ["R2", "R3", "R4", "R5", "R6"], "yellow"), (100, [], "yellow"),
])
def test_zone_boundaries(score, hit, zone):
    assert zone_for(score, hit) == zone


@pytest.mark.parametrize("combo,score,zone", [
    (["R1", "R2"], 60, "red"),
    (["R1", "R3", "R4"], 60, "yellow"),
    (["R2", "R3", "R4", "R5"], 60, "yellow"),
    (CODES, 100, "red"),
    (["R2"], 25, "yellow"),
    (["R3", "R5"], 20, "green"),
    (["R1", "R2", "R3"], 70, "red"),
    (["R2", "R3"], 35, "yellow"),
])
def test_zone_of_factor_combinations(combo, score, zone):
    assert sum(FACTORS[c]["weight"] for c in combo) == score
    assert zone_for(score, combo) == zone


# ---------- синтетические случаи: явные ожидания ----------

DEFAULT = {"R1": ("clear", "ok_guarantee"), "R2": ("clear", "none"), "R3": ("clear", "none"),
           "R4": ("clear", "none"), "R5": ("clear", "match"), "R6": ("clear", "active")}
OFFER, ADS = ("hit", "offer"), ("hit", "ads")
ABSENT, VERIFIED, MISMATCH, IDLE = ("hit", "absent"), ("hit", "verified"), ("hit", "mismatch"), ("hit", "idle")
BUILT_ALL = {c: ("clear", "built") for c in ["R1", "R2", "R3", "R5", "R6"]}

# имя → (балл, зона, «нужна проверка», отличия факторов от DEFAULT)
EXPECT = {
    "case_a": (35, "yellow", True, {"R1": ("unknown", "unknown"), "R2": OFFER, "R3": ADS}),
    "case_b": (70, "red", False, {"R1": ABSENT, "R2": OFFER, "R3": ADS}),
    "case_c": (10, "green", False, {"R1": ("clear", "ok_guarantee_no_mio"), "R3": ADS}),
    "zone_r1_r3_r4_60_yellow": (60, "yellow", False, {"R1": ABSENT, "R3": ADS, "R4": VERIFIED}),
    "zone_r2_r3_r4_r5_60_yellow": (60, "yellow", False, {"R2": OFFER, "R3": ADS, "R4": VERIFIED, "R5": MISMATCH}),
    "zone_all_six_100_red": (100, "red", False, {"R1": ABSENT, "R2": OFFER, "R3": ADS, "R4": VERIFIED, "R5": MISMATCH, "R6": IDLE}),
    "zone_r1_75_no_r2_yellow": (75, "yellow", False, {"R1": ABSENT, "R3": ADS, "R4": VERIFIED, "R5": MISMATCH, "R6": IDLE}),
    "zone_r1_unknown_65_yellow": (65, "yellow", True, {"R1": ("unknown", "unknown"), "R2": OFFER, "R3": ADS, "R4": VERIFIED,
                                                       "R5": MISMATCH, "R6": IDLE}),
    "zone_r1_only_35_yellow": (35, "yellow", False, {"R1": ABSENT}),
    "zone_r3_r4_25_yellow": (25, "yellow", False, {"R3": ADS, "R4": VERIFIED}),
    "zone_r3_r5_20_green": (20, "green", False, {"R3": ADS, "R5": MISMATCH}),
    "zone_clean_0_green": (0, "green", False, {}),
    "r1_guarantee_no_mio_clear": (0, "green", False, {"R1": ("clear", "ok_guarantee_no_mio")}),
    "r1_guarantee_mio_unknown_clear": (0, "green", False, {}),
    "r1_absent_sourced_hit": (35, "yellow", False, {"R1": ABSENT}),
    "r1_absent_unsourced_unknown": (0, "green", True, {"R1": ("unknown", "absent_unsourced")}),
    "r1_absent_no_date_unknown": (0, "green", True, {"R1": ("unknown", "absent_unsourced")}),
    "r1_mio_without_bank_unknown": (0, "green", True, {"R1": ("unknown", "mio_without_bank")}),
    "r1_mio_bank_clear": (0, "green", False, {"R1": ("clear", "ok_mio")}),
    "r1_all_unknown": (0, "green", True, {"R1": ("unknown", "unknown")}),
    "r1_built_clear": (0, "green", False, BUILT_ALL),
    "link_079_unknown": (0, "green", True, {"R2": ("unknown", "offer_unlinked"), "R3": ("unknown", "ads_unlinked")}),
    "link_080_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "link_050_confirmed_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "link_dismissed_ignored": (0, "green", False, {}),
    "link_other_complex_ignored": (0, "green", False, {}),
    "link_mixed_counts_confirmed_only": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "link_order_newest_first": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "r4_verified_collective_hit": (15, "green", False, {"R4": VERIFIED}),
    "r4_pending_unknown": (0, "green", True, {"R4": ("unknown", "pending")}),
    "r4_rejected_clear": (0, "green", False, {}),
    "r4_verified_not_collective_clear": (0, "green", False, {}),
    "r4_other_complex_ignored": (0, "green", False, {}),
    "r4_verified_and_pending_hit": (15, "green", False, {"R4": VERIFIED}),
    "r4_two_verified_newest_first": (15, "green", False, {"R4": VERIFIED}),
    "r5_diff_19_clear": (0, "green", False, {}),
    "r5_diff_20_hit": (10, "green", False, {"R5": MISMATCH}),
    "r5_confirmed_above_declared_hit": (10, "green", False, {"R5": MISMATCH}),
    "r5_float_diff_20_hit": (10, "green", False, {"R5": MISMATCH}),
    "r5_float_diff_20_2_hit": (10, "green", False, {"R5": MISMATCH}),
    "r5_float_diff_19_95_clear": (0, "green", False, {}),
    "r5_half_up_20_13_hit": (10, "green", False, {"R5": MISMATCH}),
    "r5_no_source_unknown": (0, "green", True, {"R5": ("unknown", "incomplete")}),
    "r5_no_date_unknown": (0, "green", True, {"R5": ("unknown", "incomplete")}),
    "r5_declared_null_unknown": (0, "green", True, {"R5": ("unknown", "incomplete")}),
    "r6_idle_89_clear": (0, "green", False, {}),
    "r6_idle_90_hit": (5, "green", False, {"R6": IDLE}),
    "r6_idle_120_one_photo_unknown": (0, "green", True, {"R6": ("unknown", "idle_unconfirmed")}),
    "r6_idle_120_active_photos_unknown": (0, "green", True, {"R6": ("unknown", "idle_unconfirmed")}),
    "r6_idle_100_mixed_activity_hit": (5, "green", False, {"R6": IDLE}),
    "r6_idle_120_undated_photos_unknown": (0, "green", True, {"R6": ("unknown", "idle_unconfirmed")}),
    "r6_no_photos_unknown": (0, "green", True, {"R6": ("unknown", "no_photos")}),
    "r6_overdue_179_clear": (0, "green", False, {}),
    "r6_overdue_180_hit": (5, "green", False, {"R6": ("hit", "overdue")}),
    "o2_young_developer_flag": (0, "green", False, {}),
    "o2_developer_unknown": (0, "green", False, {}),
}
OBS_DEFAULT = {"O1": "ok", "O2": "ok", "O3": "ok"}
OBS_EXPECT = {
    "r6_idle_89_clear": {"O3": "flag"},
    "o2_young_developer_flag": {"O1": "flag", "O2": "flag"},
    "o2_developer_unknown": {"O1": "unknown", "O2": "unknown"},
}


def test_every_fixture_has_expectation():
    assert set(EXPECT) == set(CX_FX)
    assert set(AD_EXPECT) == set(AD_FX)
    assert len(FX["complex"]) == len(CX_FX) and len(FX["ad"]) == len(AD_FX), "имена кейсов должны быть уникальны"


@pytest.mark.parametrize("name", list(EXPECT))
def test_complex_fixture(name):
    score, zone, needs, over = EXPECT[name]
    r = run(name)
    assert (r["score"], r["zone"], r["needsCheck"]) == (score, zone, needs)
    assert {f["code"]: (f["state"], f["reason"]) for f in r["factors"]} == {**DEFAULT, **over}
    assert {o["code"]: o["state"] for o in r["observations"]} == {**OBS_DEFAULT, **OBS_EXPECT.get(name, {})}
    assert r["modelVersion"] == MODEL["version"]
    assert ("Нужна проверка" in r["summary"]) == needs


def test_case_a_unknown_basis_is_not_r1():
    r = run("case_a")
    r1 = fac(r, "R1")
    assert r1["state"] == "unknown" and "не считается отсутствием основания" in r1["evidence"]
    assert {i["ref"]: i["value"] for i in r1["items"]} == {"guarantee": "unknown", "mio": "unknown", "bvu": "unknown"}
    assert hit_codes(r) == ["R2", "R3"] and unknown_codes(r) == ["R1"]
    assert r["summary"] == "Балл 35: углублённая ручная проверка документов и публикаций. Нужна проверка: R1."
    item = fac(r, "R2")["items"][0]
    assert (item["kind"], item["marker"], item["quote"]) == ("ad", "бронь", "Платная бронь квартиры от 500 000 ₸.")


def test_case_b_red_waits_for_analyst():
    r = run("case_b")
    assert hit_codes(r) == ["R1", "R2", "R3"]
    assert fac(r, "R1")["evidence"] == \
        "Проверкой установлено: гарантии ЕО нет и разрешения МИО нет (Картотека ГАСК (демо), 09.10.2026)"
    assert fac(r, "R2")["items"][0]["marker"] == "депозит" and fac(r, "R2")["evidence"] == "Предложение внести деньги в публикациях: 1"
    assert r["summary"].startswith("Подтверждены R1 и R2, балл 70") and "решение аналитика" in r["summary"]


def test_case_c_guarantee_without_mio_is_not_r1():
    r1 = fac(run("case_c"), "R1")
    assert (r1["state"], r1["points"]) == ("clear", 35)
    assert "Отсутствие отдельного разрешения МИО не является R1" in r1["evidence"]
    assert {i["ref"]: i["value"] for i in r1["items"]} == {"guarantee": "yes", "mio": "no", "bvu": "no"}
    assert fac(run("case_c"), "R3")["items"][0]["quote"] == "Старт продаж!"


@pytest.mark.parametrize("src", ["fixture", "seed_c3"])
def test_guarantee_with_unknown_mio_claims_nothing_about_mio(src):
    """Гарантия есть, по МИО «нет данных» — не R1, но и не «отсутствие МИО»: его никто не устанавливал."""
    r1 = fac(run("r1_guarantee_mio_unknown_clear") if src == "fixture" else run_seed("c3"), "R1")
    assert (r1["state"], r1["reason"]) == ("clear", "ok_guarantee")
    assert r1["evidence"] == "Действующая гарантия ЕО подтверждена" and "МИО" not in r1["evidence"]
    assert {i["ref"]: i["value"] for i in r1["items"]}["mio"] == "unknown"


def test_yellow_high_summary_without_r1_r2_pair():
    for name in ["zone_r1_r3_r4_60_yellow", "zone_r2_r3_r4_r5_60_yellow", "zone_r1_75_no_r2_yellow"]:
        assert "R1 и R2 вместе не подтверждены" in run(name)["summary"], name
    assert "R1 и R2 вместе" not in run("zone_r3_r4_25_yellow")["summary"]


def test_r1_evidence_documents():
    r1 = fac(run("r1_absent_sourced_hit"), "R1")
    assert [i["ref"] for i in r1["items"]] == ["guarantee", "mio", "bvu"]
    assert all(i["kind"] == "document" and i["source"] == "Картотека ГАСК (демо)" and i["date"] == "2026-10-09" for i in r1["items"])
    ok = fac(run("zone_clean_0_green"), "R1")
    assert ok["items"][0]["quote"].startswith("Гарантия ЕО №")


def test_link_hypothesis_evidence():
    r = run("link_079_unknown")
    assert fac(r, "R2")["evidence"].endswith("(уверенность ниже 80%) — нужна проверка")
    assert fac(r, "R2")["items"][0]["ref"] and fac(r, "R3")["items"]
    assert r["summary"].endswith("Нужна проверка: R2, R3.")


def test_link_mixed_counts_only_confirmed():
    f = CX_FX["link_mixed_counts_confirmed_only"]
    confirmed = [a["id"] for a in f["ads"] if a["matchConfidence"] >= 0.8]
    r = run("link_mixed_counts_confirmed_only")
    for code in ["R2", "R3"]:
        assert [i["ref"] for i in fac(r, code)["items"]] == confirmed
    assert fac(r, "R2")["evidence"] == "Предложение внести деньги в публикациях: 1"
    assert fac(r, "R3")["evidence"] == "Реклама продажи до ввода объекта: публикаций 1"


def test_evidence_order_newest_first():
    """Доказательства: свежие первыми, при равной дате — по id как строке (так же сортирует фронтенд)."""
    r = run("link_order_newest_first")
    assert [i["ref"] for i in fac(r, "R2")["items"]] == ["fxt2", "fxt3"]
    assert [i["ref"] for i in fac(r, "R3")["items"]] == ["fxt10", "fxt2", "fxt3"]
    assert fac(r, "R3")["evidence"] == "Реклама продажи до ввода объекта: публикаций 3"
    r4 = fac(run("r4_two_verified_newest_first"), "R4")
    assert [i["date"] for i in r4["items"]] == ["2026-09-15", "2026-08-01"]
    assert r4["evidence"] == "Проверенное коллективное обращение: 2"


def test_r4_evidence():
    r4 = fac(run("r4_verified_and_pending_hit"), "R4")
    assert [i["value"] for i in r4["items"]] == ["verified"] and r4["evidence"] == "Проверенное коллективное обращение: 1"
    pending = fac(run("r4_pending_unknown"), "R4")
    assert [i["value"] for i in pending["items"]] == ["pending"]


def test_r5_evidence():
    assert fac(run("r5_diff_20_hit"), "R5")["evidence"] == "Заявлено 70%, подтверждено 50%: разница 20 п.п. (порог 20 п.п.)"
    assert fac(run("r5_confirmed_above_declared_hit"), "R5")["evidence"] == \
        "Заявлено 50%, подтверждено 70%: разница 20 п.п. (порог 20 п.п.)"
    assert "разница 19 п.п. — меньше порога 20 п.п." in fac(run("r5_diff_19_clear"), "R5")["evidence"]
    assert [i["ref"] for i in fac(run("r5_diff_20_hit"), "R5")["items"]] == ["declared", "confirmed"]
    assert [i["ref"] for i in fac(run("r5_declared_null_unknown"), "R5")["items"]] == ["confirmed"]


@pytest.mark.parametrize("name,text", [
    # 32.3 − 12.3 = 19.999999999999996 во float: без округления это было бы «меньше порога»
    ("r5_float_diff_20_hit", "Заявлено 32.3%, подтверждено 12.3%: разница 20 п.п. (порог 20 п.п.)"),
    ("r5_float_diff_20_2_hit", "Заявлено 62.5%, подтверждено 42.3%: разница 20.2 п.п. (порог 20 п.п.)"),
    ("r5_float_diff_19_95_clear", "Заявлено 32.35%, подтверждено 12.4%: разница 19.95 п.п. — меньше порога 20 п.п."),
    # половина округляется вверх, как Math.floor(x + 0.5) во фронтенде; round() дал бы 20.12
    ("r5_half_up_20_13_hit", "Заявлено 30.125%, подтверждено 10%: разница 20.13 п.п. (порог 20 п.п.)"),
])
def test_r5_diff_rounded_before_compare(name, text):
    assert fac(run(name), "R5")["evidence"] == text


@pytest.mark.parametrize("d,k,diff", [(32.3, 12.3, 20), (12.3, 32.3, 20), (0.3, 0.1, 0.2), (19.994, 0, 19.99),
                                      (20.125, 0, 20.13), (50, 30, 20), (70.5, 50.5, 20)])
def test_r5_rounding_property(d, k, diff):
    f = _clean()
    f["complex"]["readiness"]["declared"]["value"], f["complex"]["readiness"]["confirmed"]["value"] = d, k
    r5 = fac(score_complex(f["complex"], f["developer"], [], [], AS_OF), "R5")
    assert f"разница {diff} п.п." in r5["evidence"]
    assert r5["state"] == ("hit" if diff >= T["readinessGap"] else "clear")


def test_r6_evidence():
    assert fac(run("r6_overdue_180_hit"), "R6")["evidence"] == "Срок сдачи 18.04.2026 прошёл 180 дн. назад, объект не введён"
    assert fac(run("r6_idle_90_hit"), "R6")["evidence"] == "Нет активности 90 дн. по снимкам 01.07.2026 — 09.09.2026"
    assert fac(run("r6_idle_120_one_photo_unknown"), "R6")["evidence"] == \
        "Простой 120 дн. не подтверждён: нужно не менее 2 датированных снимков с активностью «нет» или «низкая»"
    assert obs(run("r6_idle_89_clear"), "O3")["text"] == "Нет активности 89 дн. — ниже порога R6 (90 дн.)"
    # граница просрочки сдвигается вместе с датой расчёта
    assert fac(run("r6_overdue_179_clear", as_of="2026-10-16"), "R6")["state"] == "hit"
    assert fac(run("r6_overdue_180_hit", as_of="2026-10-14"), "R6")["state"] == "clear"


def test_r6_idle_needs_inactive_photos():
    """Простой подтверждают только датированные снимки «нет»/«низкая»; период — по ним, доказательства — вся серия."""
    active = fac(run("r6_idle_120_active_photos_unknown"), "R6")
    assert (active["state"], active["reason"]) == ("unknown", "idle_unconfirmed")
    assert [i["value"] for i in active["items"]] == ["есть", "есть"]
    mixed = fac(run("r6_idle_100_mixed_activity_hit"), "R6")
    assert mixed["evidence"] == "Нет активности 100 дн. по снимкам 06.06.2026 — 09.09.2026"
    assert [(i["date"], i["value"]) for i in mixed["items"]] == \
        [("2026-05-04", "есть"), ("2026-06-06", "низкая"), ("2026-09-09", "нет")]


def test_r6_seed_c4_idle_by_inactive_photos():
    r = run_seed("c4")
    r6 = fac(r, "R6")
    assert (r6["state"], r6["evidence"]) == ("hit", "Нет активности 97 дн. по снимкам 06.06.2026 — 09.09.2026")
    assert [i["value"] for i in r6["items"]] == ["есть", "низкая", "нет", "нет"]
    assert (r["score"], r["zone"]) == (100, "red")


@pytest.mark.parametrize("acts", list(itertools.product(["есть", "низкая", "нет", None], repeat=2)))
@pytest.mark.parametrize("dated", [True, False])
def test_r6_idle_property(acts, dated):
    f = _clean()
    f["complex"]["idleDays"] = 120
    f["complex"]["photos"] = [{"date": f"2026-0{7 + i}-01" if dated else "", "src": f"/img/p{i}.jpg", "source": "дрон",
                               "activity": a} for i, a in enumerate(acts)]
    r6 = fac(score_complex(f["complex"], f["developer"], [], [], AS_OF), "R6")
    idle = dated and all(a in ("нет", "низкая") for a in acts)
    assert (r6["state"], r6["reason"]) == (("hit", "idle") if idle else ("unknown", "idle_unconfirmed"))
    assert len(r6["items"]) == 2


def test_observations_do_not_change_score():
    base = run("zone_clean_0_green")
    for name in ["o2_young_developer_flag", "o2_developer_unknown", "r6_idle_89_clear"]:
        r = run(name)
        assert (r["score"], r["zone"], r["needsCheck"]) == (base["score"], base["zone"], base["needsCheck"]), name
    assert obs(run("o2_young_developer_flag"), "O2")["text"] == "У застройщика незавершённых объектов: 1 из 1"
    assert obs(run("o2_developer_unknown"), "O2")["text"] == "Сведения о застройщике не получены"


# ---------- оценка публикации ----------

AD_EXPECT = {
    "ad_no_complex": (35, "yellow", True, {"R1": ("unknown", "R1_no_complex"), "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
    "ad_link_060": (35, "yellow", True, {"R1": ("unknown", "R1_link_unconfirmed"), "R2": ("hit", "R2_offer"),
                                         "R3": ("hit", "R3_ads")}),
    "ad_no_markers": (0, "green", False, {"R1": ("clear", "R1_no_sale"), "R2": ("clear", "none"), "R3": ("clear", "none")}),
    "ad_built_complex": (0, "green", False, {"R1": ("clear", "built"), "R2": ("clear", "built"), "R3": ("clear", "built")}),
    "ad_built_complex_link_hypothesis": (35, "yellow", True, {"R1": ("unknown", "R1_link_unconfirmed"),
                                                              "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
    "ad_money_basis_absent_red": (70, "red", False, {"R1": ABSENT, "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
    "ad_confirmed_low_conf_red": (70, "red", False, {"R1": ABSENT, "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
    "ad_sale_only_guarantee": (10, "green", False, {"R1": ("clear", "ok_guarantee_no_mio"), "R2": ("clear", "none"),
                                                     "R3": ("hit", "R3_ads")}),
    "ad_ocr_only_money": (35, "yellow", True, {"R1": ("unknown", "unknown"), "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
    "ad_long_sentence_quote": (35, "yellow", True, {"R1": ("unknown", "unknown"), "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
}


@pytest.mark.parametrize("name", list(AD_EXPECT))
def test_ad_fixture(name):
    score, zone, needs, factors = AD_EXPECT[name]
    r = run_ad(name)
    assert (r["score"], r["zone"], r["needsCheck"]) == (score, zone, needs)
    assert {f["code"]: (f["state"], f["reason"]) for f in r["factors"]} == factors
    assert [f["code"] for f in r["factors"]] == MODEL["ad"]["factors"]


def test_ad_texts():
    assert fac(run_ad("ad_link_060"), "R1")["evidence"] == \
        "Привязка к ЖК не подтверждена (уверенность 60%) — основание объекта не учитывается"
    assert fac(run_ad("ad_no_complex"), "R1")["evidence"] == "ЖК не определён — основание проверить нельзя"
    r = run_ad("ad_no_markers")
    assert r["signals"] == {"sale": [], "funds": [], "safe": []}
    assert fac(r, "R1")["evidence"] == "Публикация не продвигает продажу — основание объекта не учитывается"
    assert fac(r, "R2")["evidence"] == "Не найдено"


def test_ad_ocr_quote():
    r = run_ad("ad_ocr_only_money")
    item = fac(r, "R2")["items"][0]
    assert (item["marker"], item["quote"]) == ("предоплат", "ПРЕДОПЛАТА 10% · ФИКСИРУЕМ ЦЕНУ")
    assert r["signals"]["funds"] == ["предоплат", "фиксируем цену"]


def test_ad_long_sentence_quote_trimmed():
    text = AD_FX["ad_long_sentence_quote"]["ad"]["text"]
    assert len(text.split()) > T["quoteMaxWords"]
    q = fac(run_ad("ad_long_sentence_quote"), "R2")["items"][0]["quote"]
    assert q == ("… нашего проекта что в новом квартале уже открыта бронь на просторные светлые квартиры "
                 "с видом на реку и большой зелёный …")
    assert len(q.strip("… ").split()) == T["quoteWordsBefore"] + T["quoteWordsAfter"]
    q3 = fac(run_ad("ad_long_sentence_quote"), "R3")["items"][0]["quote"]
    assert q3.startswith("… ") and q3.endswith("до конца месяца") and "квартиры" in q3


# ---------- текст публикации ----------

def test_hits_drops_nested_markers():
    assert hits("Первый взнос 10%, остальное — взнос частями", MARKERS["money"]) == ["первый взнос"]
    assert hits("Цена 300 000 ₸/м²", MARKERS["sale"]) == ["₸/м²", "цена"]
    assert hits("ДДУ сегодня", MARKERS["money"]) == ["ДДУ"]
    assert hits("Во дворе посадили деревья", MARKERS["money"] + MARKERS["sale"]) == []


def test_quote():
    assert quote(None, "бронь") is None and quote("", "бронь") is None
    assert quote("Без нужного слова.", "бронь") is None
    assert quote("Первое. Вторая БРОНЬ тут! Третье.", "бронь") == "Вторая БРОНЬ тут!"
    assert quote("Строка один\nбронь  на   строке два", "бронь") == "бронь на строке два"


def test_link_confirmed():
    assert not link_confirmed({"matchConfidence": 0.79, "status": "new"})
    assert link_confirmed({"matchConfidence": 0.8, "status": "new"})
    assert link_confirmed({"matchConfidence": 0.1, "status": "confirmed"})
    assert not link_confirmed({"status": "in_review"})


def test_fill_and_fmt_date():
    assert fill("{a} и {b}", {"a": 1}) == "1 и {b}"
    assert fmt_date("2026-10-09T18:22") == "09.10.2026" and fmt_date(None) == "—" and fmt_date("2026") == "—"


# ---------- свойства модели ----------

def _clean():
    return copy.deepcopy(CX_FX["zone_clean_0_green"])


@pytest.mark.parametrize("g,m,k,src,chk", list(itertools.product(["yes", "no", "unknown"], ["yes", "no", "unknown"],
                                                                 ["yes", "no", "unknown"], [True, False], [True, False])))
def test_unknown_never_becomes_r1(g, m, k, src, chk):
    f = _clean()
    basis = {"guarantee": g, "mio": m, "bvu": k}
    if src:
        basis["source"] = "Картотека ГАСК (демо)"
    if chk:
        basis["checkedAt"] = "2026-10-09"
    f["complex"]["basis"] = basis
    r = score_complex(f["complex"], f["developer"], [], [], AS_OF)
    state = fac(r, "R1")["state"]
    assert (state == "hit") == (g == "no" and m == "no" and src and chk)
    if g == "yes" or (m == "yes" and k == "yes"):
        assert state == "clear"
    if g == "yes":  # «отсутствие МИО не является R1» — только при установленном отсутствии МИО
        assert fac(r, "R1")["reason"] == ("ok_guarantee_no_mio" if m == "no" else "ok_guarantee")
    if "unknown" in (g, m) and g != "yes" and not (m == "yes" and k == "yes"):
        assert state == "unknown"
    assert r["score"] == (35 if state == "hit" else 0)


@pytest.mark.parametrize("conf", [0, 0.31, 0.5, 0.79, 0.7999])
@pytest.mark.parametrize("status", ["new", "needs_check", "in_review"])
def test_link_hypothesis_never_becomes_r2_r3(conf, status):
    f = _clean()
    ad = copy.deepcopy(CX_FX["case_b"]["ads"][0]) | {"matchConfidence": conf, "status": status, "complexId": f["complex"]["id"]}
    r = score_complex(f["complex"], f["developer"], [ad], [], AS_OF)
    assert (fac(r, "R2")["state"], fac(r, "R3")["state"]) == ("unknown", "unknown")
    assert (r["score"], r["zone"], r["needsCheck"]) == (0, "green", True)
    absent = copy.deepcopy(CX_FX["case_b"]["complex"])
    assert fac(score_ad(ad, absent), "R1")["state"] == "unknown"


def _all_results():
    g = golden.build()
    yield from (("seed:" + k, r, False) for k, r in g["seed"]["complexes"].items())
    yield from (("fx:" + k, r, False) for k, r in g["fixtures"]["complex"].items())
    yield from (("seed-ad:" + k, r, True) for k, r in g["seed"]["ads"].items())
    yield from (("fx-ad:" + k, r, True) for k, r in g["fixtures"]["ad"].items())


def _template(code, reason, is_ad):
    if is_ad and reason in MODEL["ad"]["reasons"]:
        return MODEL["ad"]["reasons"][reason]
    return FACTORS[code]["reasons"][reason]


def test_formula_and_texts_hold_for_every_result():
    """Для всего сида и всех синтетических случаев: формула §5, зона, «нужна проверка», тексты из модели."""
    for name, r, is_ad in _all_results():
        hit = hit_codes(r)
        assert r["score"] == min(100, sum(FACTORS[c]["weight"] for c in hit)), name
        assert r["zone"] == zone_for(r["score"], hit), name
        assert r["needsCheck"] == bool(unknown_codes(r)), name
        assert ("R2" not in hit) or ("R3" in hit), f"{name}: предложение денег — это и реклама продажи (кейсы A/B)"
        for f in r["factors"]:
            assert (f["title"], f["points"]) == (FACTORS[f["code"]]["title"], FACTORS[f["code"]]["weight"]), name
            tpl = _template(f["code"], f["reason"], is_ad)
            pattern = "".join(".+?" if p.startswith("{") else re.escape(p) for p in re.split(r"(\{\w+\})", tpl))
            assert re.fullmatch(pattern, f["evidence"], re.S), (name, f["code"], f["evidence"])
            assert "{" not in f["evidence"], (name, f["code"])
            if f["state"] == "hit":
                assert f["items"], f"{name}: у подтверждённого {f['code']} должно быть доказательство (ТЗ §7)"
        for o in r.get("observations", []):
            assert "{" not in o["text"] and o["state"] in OBS_STATES[o["code"]], name
        assert "{" not in r.get("summary", ""), name


def test_fixtures_cover_every_branch():
    covered, ad_covered, obs_covered, summaries = set(), set(), set(), set()
    for name, r, is_ad in _all_results():
        for f in r["factors"]:
            (ad_covered if is_ad and f["reason"] in AD_REASONS else covered).add((f["code"], f["reason"]))
        obs_covered |= {(o["code"], o["state"]) for o in r.get("observations", [])}
        if not is_ad:
            summaries.add(r["zone"] if r["zone"] != "yellow" else ("yellow_high" if r["score"] >= 60 else "yellow"))
            if r["needsCheck"]:
                summaries.add("needs_check")
    assert covered == {(c, x) for c, xs in REASONS.items() for x in xs}
    assert {x for _, x in ad_covered} == AD_REASONS
    assert obs_covered == {(c, s) for c, ss in OBS_STATES.items() for s in ss}
    assert summaries == set(MODEL["summaries"])


def test_deterministic_and_order_independent():
    rnd = random.Random(5)
    for cid, c in SEED_CX.items():
        first = run_seed(cid)
        ads, appeals = copy.deepcopy(SEED["ads"]), copy.deepcopy(SEED["appeals"])
        for _ in range(3):
            rnd.shuffle(ads)
            rnd.shuffle(appeals)
            assert score_complex(c, DEVS.get(c["developerId"]), ads, appeals, AS_OF) == first, cid
        assert score_complex(c, DEVS.get(c["developerId"]), SEED["ads"], SEED["appeals"], date(2026, 10, 15)) == first, cid
    for name in ["link_mixed_counts_confirmed_only", "link_order_newest_first", "r4_two_verified_newest_first"]:
        f = CX_FX[name]
        assert score_complex(f["complex"], f["developer"], f["ads"][::-1], f["appeals"][::-1], AS_OF) == run(name), name


def test_inputs_not_mutated():
    before = copy.deepcopy(SEED)
    for cid in SEED_CX:
        run_seed(cid)
    for a in SEED["ads"]:
        score_ad(a, SEED_CX.get(a.get("complexId") or ""))
    assert SEED == before


# ---------- сид ----------

@pytest.mark.parametrize("case", MODEL["cases"], ids=lambda c: c["id"])
def test_seed_control_cases(case):
    r = run_seed(case["complexId"])
    assert (r["score"], r["zone"]) == (case["score"], case["zone"])
    assert hit_codes(r) == case["hits"]
    assert set(case["unknown"]) <= set(unknown_codes(r))


def test_seed_traffic_light():
    res = {cid: run_seed(cid) for cid in SEED_CX}
    assert Counter(r["zone"] for r in res.values()) == {"red": 3, "yellow": 3, "green": 8}
    assert {cid: r["score"] for cid, r in res.items() if r["zone"] == "red"} == {"c2": 70, "c4": 100, "c11": 85}
    assert {cid: r["score"] for cid, r in res.items() if r["zone"] == "yellow"} == {"c6": 35, "c8": 60, "c14": 35}
    c8 = res["c8"]
    assert "R1" not in hit_codes(c8) and "R2" in hit_codes(c8), "c8: 60 баллов без R1 — жёлтая"
    assert all(r["score"] < 25 for r in res.values() if r["zone"] == "green")


def test_seed_readiness_matches_progress():
    for c in SEED["complexes"]:
        k = (c.get("readiness") or {}).get("confirmed")
        if k:
            assert k["value"] == c["progress"], c["id"]


def test_seed_is_demo():
    assert "ДЕМО" in SEED["_note"].upper()
    assert len(SEED["complexes"]) >= 8 and len(SEED["ads"]) >= 12


# ---------- эталон ----------

def test_fixtures_shape():
    required = {"id", "name", "developerId", "address", "stage", "progress", "plannedEnd", "basis", "readiness",
                "inPublishedList", "photos", "idleDays"}
    for f in FX["complex"]:
        assert required <= set(f["complex"]), f["name"]
        assert f["asOf"] == AS_OF and isinstance(f["ads"], list) and isinstance(f["appeals"], list), f["name"]
    for f in FX["ad"]:
        assert {"id", "text", "matchConfidence", "status"} <= set(f["ad"]), f["name"]


def test_golden_up_to_date():
    stored = json.loads(golden.GOLDEN.read_text(encoding="utf-8"))
    actual = json.loads(golden.dump(golden.build()))
    assert stored["modelVersion"] == MODEL["version"] and stored["asOf"] == AS_OF
    for part in ["seed", "fixtures"]:
        for kind in actual[part]:
            assert set(actual[part][kind]) == set(stored[part][kind]), \
                f"{part}.{kind}: набор кейсов изменился — перегенерируйте: {REGEN}"
            for k in actual[part][kind]:
                assert actual[part][kind][k] == stored[part][kind][k], \
                    f"shared/risk_golden.json устарел ({part}.{kind}.{k}) — перегенерируйте: {REGEN}"
    assert actual == stored, f"shared/risk_golden.json устарел — перегенерируйте: {REGEN}"


def test_as_of_accepts_datetime_and_string():
    c, dev = SEED_CX["c4"], DEVS["d4"]
    base = score_complex(c, dev, SEED["ads"], SEED["appeals"], AS_OF)
    assert score_complex(c, dev, SEED["ads"], SEED["appeals"], datetime(2026, 10, 15, 23, 59)) == base
    assert score_complex(c, dev, SEED["ads"], SEED["appeals"], date(2026, 10, 15)) == base


def _frozen_now(utc):
    """Подмена datetime в app.risk: now(tz) отдаёт заданный момент UTC; без tz — «наивное» UTC (будто процесс в UTC)."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)
    return Frozen


@pytest.mark.parametrize("utc,day", [
    (datetime(2026, 10, 15, 18, 59, tzinfo=timezone.utc), "2026-10-15"),  # 23:59 в Усть-Каменогорске
    (datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc), "2026-10-16"),   # 00:00 — по UTC ещё 15-е
    (datetime(2026, 10, 15, 23, 30, tzinfo=timezone.utc), "2026-10-16"),
])
def test_default_as_of_is_today_utc5(monkeypatch, utc, day):
    """Без asOf расчёт идёт на сегодняшнюю дату по UTC+5, а не по поясу процесса."""
    assert ALMATY.utcoffset(None) == timedelta(hours=5)
    monkeypatch.setattr(risk, "datetime", _frozen_now(utc))
    for name in ["r6_overdue_179_clear", "r6_overdue_180_hit"]:
        f = CX_FX[name]
        assert score_complex(f["complex"], f.get("developer"), f["ads"], f["appeals"]) == run(name, as_of=day), name
    # граница R6 видна напрямую: 179 дн. на 15.10 становятся 180 на 16.10
    f = CX_FX["r6_overdue_179_clear"]
    expected = "hit" if day == "2026-10-16" else "clear"
    assert fac(score_complex(f["complex"], f.get("developer"), [], []), "R6")["state"] == expected
