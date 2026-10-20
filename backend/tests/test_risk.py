"""Модель риска по ТЗ §5: контрольные кейсы A/B/C, границы зон, факторы R1-R6, наблюдения, оценка публикации, эталон.

Только чистые функции(app.risk / app.risk_model / app.golden) - без API и БД.
Синтетические входы - shared/testdata/risk_fixtures.json; ожидания ниже записаны явно, а не берутся из эталона.
"""
import copy
import itertools
import json
import math
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import golden, risk
from app.risk import hits, link_confirmed, quote, score_ad, score_complex, terms_rejected
from app.risk_model import ALMATY, FACTORS, MARKERS, MODEL, OBSERVATIONS, SHARED, T, fill, fmt_date, zone_for

AS_OF = "2026-10-15"
REGEN = "cd backend && .venv/bin/python -m app.golden"
SEED = json.loads((SHARED / "testdata" / "demo_seed.json").read_text(encoding="utf-8"))  # только для тестов
FX = json.loads((SHARED / "testdata" / "risk_fixtures.json").read_text(encoding="utf-8"))
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
    assert (T["readinessGap"], T["idleDays"], T["idlePhotosMin"], T["overdueDays"], T["idleShortDays"]) == (20, 90, 2, 180, 30)
    assert T["idleActivities"] == ["нет", "низкая"]
    # пороги ИИ-анализа - только для показа гипотез, в балл не входят
    assert (T["lowConfidence"], T["hypothesisMin"], T["hypothesesTop"]) == (0.5, 0.3, 3)
    assert 0 < T["hypothesisMin"] < T["lowConfidence"] < 1


# Порога "уверенность ≥ N% - факт" больше нет: привязка - факт только после подтверждения аналитиком(ТЗ §5, §6)
REMOVED_THRESHOLDS = ("linkConfirmed", "autoLinkConfidence")
_THRESHOLD_USE = re.compile(r"""(?:\bT|thresholds)(?:\.|\[\s*["'])(?:linkConfirmed|autoLinkConfidence)\b|autoLinkConfidence""")
APP = Path(risk.__file__).resolve().parent
FRONT = SHARED.parent / "frontend" / "src"  # в образе бэкенда фронтенда нет - тогда проверяется только бэкенд


def test_removed_link_thresholds_absent_from_model_and_engines():
    assert not set(REMOVED_THRESHOLDS) & set(T)
    for path in [APP / "risk.py", FRONT / "lib" / "risk.ts"]:
        if path.exists():
            assert not _THRESHOLD_USE.search(path.read_text(encoding="utf-8")), path
    for tpl in [*(t for f in MODEL["factors"] for t in f["reasons"].values()), *MODEL["ad"]["reasons"].values()]:
        assert "{threshold}%" not in tpl and "уверенность ниже" not in tpl, tpl


def test_removed_link_thresholds_unused_anywhere():
    """Ни бэкенд, ни фронтенд не читают удалённые пороги(иначе KeyError / undefined вместо правила привязки)."""
    files = sorted(APP.rglob("*.py")) + sorted(p for p in FRONT.rglob("*.ts*")  # тесты проверяют как раз отсутствие
                                               if p.suffix in (".ts", ".tsx") and ".test." not in p.name)
    used = [f"{p.parent.name}/{p.name}" for p in files if _THRESHOLD_USE.search(p.read_text(encoding="utf-8"))]
    assert not used, f"удалённые пороги привязки всё ещё используются: {used}"


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


# Все ключи пояснений, которые использует risk.py. Добавили ветку в движок - добавьте текст в модель и сюда.
REASONS = {
    "R1": {"built", "ok_guarantee", "ok_guarantee_no_mio", "ok_mio", "absent", "absent_unsourced", "mio_without_bank", "unknown"},
    "R2": {"built", "offer", "offer_unlinked", "offer_rejected", "none"},
    "R3": {"built", "ads", "ads_unlinked", "none"},
    "R4": {"verified", "pending", "none", "unchecked", "none_checked"},
    "R5": {"built", "mismatch", "match", "incomplete"},
    "R6": {"built", "overdue", "idle", "idle_unconfirmed", "no_photos", "active"},
}
AD_REASONS = {"R1_no_sale", "R1_no_complex", "R1_link_unconfirmed", "R2_offer", "R2_rejected", "R3_ads", "none"}
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
        # вариант - другая формулировка существующей причины, не новая причина
        for k, t in f.get("variants", {}).items():
            assert t.strip() and any(k.startswith(x + "_") for x in f["reasons"]) and k not in f["reasons"], (c, k)


def test_reasons_used_in_engine_exist():
    """Литеральные вызовы _factor(...) в risk.py ссылаются только на существующие тексты."""
    src = (Path(__file__).resolve().parents[1] / "app" / "risk.py").read_text(encoding="utf-8")
    used = re.findall(r'_factor\("(R\d)", "(?:hit|clear|unknown)", "(\w+)"', src)
    assert len(used) >= 20
    for code, reason in used:
        assert reason in FACTORS[code]["reasons"] or reason in MODEL["ad"]["reasons"], (code, reason)
    for a, b in re.findall(r'\("(\w+)", "(\w+)"\) if code == "R2"', src) + re.findall(r'else \("(\w+)", "(\w+)"\)', src):
        assert {a, b} <= set(FACTORS["R2"]["reasons"]) | set(FACTORS["R3"]["reasons"])
    # вето аналитика - только для R2(R3 не затрагивается)
    for reason in re.findall(r'_factor\(code, "(?:hit|clear|unknown)", "(\w+)"', src):
        assert reason in FACTORS["R2"]["reasons"], reason
        assert reason == "offer_rejected" or reason in FACTORS["R3"]["reasons"], reason
    assert 'code == "R2" and terms_rejected(a)' in src and "offer_rejected" not in FACTORS["R3"]["reasons"]


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
UNLINKED, ADS_UNLINKED, REJECTED = ("unknown", "offer_unlinked"), ("unknown", "ads_unlinked"), ("clear", "offer_rejected")
BUILT_ALL = {c: ("clear", "built") for c in ["R1", "R2", "R3", "R5", "R6"]}

# имя → (балл, зона, "нужна проверка", отличия факторов от DEFAULT)
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
    "r1_absent_per_doc_hit": (35, "yellow", False, {"R1": ABSENT}),
    "r1_absent_per_doc_no_general_hit": (35, "yellow", False, {"R1": ABSENT}),
    "r1_absent_mixed_fallback_hit": (35, "yellow", False, {"R1": ABSENT}),
    "r1_absent_general_only_hit": (35, "yellow", False, {"R1": ABSENT}),
    "r1_absent_same_source_per_doc_hit": (35, "yellow", False, {"R1": ABSENT}),
    "r1_absent_guarantee_only_sourced_unknown": (0, "green", True, {"R1": ("unknown", "absent_unsourced")}),
    "r1_absent_own_without_date_unknown": (0, "green", True, {"R1": ("unknown", "absent_unsourced")}),
    "r1_unknown_doc_without_source": (0, "green", True, {"R1": ("unknown", "unknown")}),
    "r1_unknown_doc_with_general_check": (0, "green", True, {"R1": ("unknown", "unknown")}),
    "link_hypothesis_079_unknown": (0, "green", True, {"R2": UNLINKED, "R3": ADS_UNLINKED}),
    "link_high_confidence_unconfirmed_unknown": (0, "green", True, {"R2": UNLINKED, "R3": ADS_UNLINKED}),
    "link_status_confirmed_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "link_analyst_confirmed_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "link_blank_confirmed_by_unknown": (0, "green", True, {"R2": UNLINKED, "R3": ADS_UNLINKED}),
    "link_dismissed_ignored": (0, "green", False, {}),
    "link_other_complex_ignored": (0, "green", False, {}),
    "link_mixed_counts_confirmed_only": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "link_order_newest_first": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "r2_terms_rejected_clear": (10, "green", False, {"R2": REJECTED, "R3": ADS}),
    "r2_terms_rejected_case_b_yellow": (45, "yellow", False, {"R1": ABSENT, "R2": REJECTED, "R3": ADS}),
    "r2_terms_rejected_other_offer_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "r2_terms_rejected_hypothesis_clear": (0, "green", True, {"R3": ADS_UNLINKED}),
    "r2_terms_rejected_hypothesis_not_counted": (10, "green", False, {"R2": REJECTED, "R3": ADS}),
    "r2_terms_accepted_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
    "r2_offer_from_ocr_hit": (35, "yellow", False, {"R2": OFFER, "R3": ADS}),
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
# Карточка ЖК из открытого реестра(origin "registry"): основание, готовность и снимки - "нет сведений"; обращения не
# сверены - пустой список не значит "обращений нет"(R4 unchecked), пока аналитик не отметит "Обращений нет - сверено"
REG_UNKNOWN = {"R1": ("unknown", "unknown"), "R4": ("unknown", "unchecked"), "R5": ("unknown", "incomplete"),
               "R6": ("unknown", "no_photos")}
APPEALS_CHECKED = ("clear", "none_checked")
EXPECT |= {
    # без проверенных сведений риск не оценивается: балл 0, факторы - справочно(просрочка из реестра - hit без балла)
    "registry_not_assessed": (0, "green", True, REG_UNKNOWN),
    "registry_hypothesis_not_assessed": (0, "green", True, {**REG_UNKNOWN, "R2": UNLINKED, "R3": ADS_UNLINKED,
                                                            "R4": ("unknown", "pending")}),
    "registry_overdue_not_assessed": (0, "green", True, {**REG_UNKNOWN, "R6": ("hit", "overdue")}),
    "registry_dismissed_link_not_assessed": (0, "green", True, REG_UNKNOWN),
    "registry_unsourced_basis_not_assessed": (0, "green", True, {**REG_UNKNOWN, "R1": ("unknown", "absent_unsourced")}),
    # проверенные сведения есть - обычный расчёт только по подтверждённым фактам
    "registry_confirmed_link_assessed": (35, "yellow", True, {**REG_UNKNOWN, "R2": OFFER, "R3": ADS}),
    "registry_sourced_basis_assessed": (35, "yellow", True, {**REG_UNKNOWN, "R1": ABSENT}),
    "registry_verified_appeal_assessed": (15, "green", True, {**REG_UNKNOWN, "R4": VERIFIED}),
    "registry_photo_assessed": (0, "green", True, {"R1": ("unknown", "unknown"), "R4": ("unknown", "unchecked"),
                                                   "R5": ("unknown", "incomplete")}),
    # "Обращений нет - сверено"(источник и дата): R4 снят; без даты - не сверено; сама сверка риск не открывает
    "registry_appeals_checked_assessed": (35, "yellow", True, {**REG_UNKNOWN, "R1": ABSENT, "R4": APPEALS_CHECKED}),
    "registry_appeals_check_undated_assessed": (35, "yellow", True, {**REG_UNKNOWN, "R1": ABSENT}),
    "registry_appeals_check_only_not_assessed": (0, "green", True, {**REG_UNKNOWN, "R4": APPEALS_CHECKED}),
}
NOT_ASSESSED = {n for n in EXPECT if n.endswith("_not_assessed")}
OBS_DEFAULT = {"O1": "ok", "O2": "ok", "O3": "ok"}
OBS_EXPECT = {
    "r6_idle_89_clear": {"O3": "flag"},
    "o2_young_developer_flag": {"O1": "flag", "O2": "flag"},
    "o2_developer_unknown": {"O1": "unknown", "O2": "unknown"},
    # застройщик из реестра: портфель не получен - профиль неизвестен
    **{n: {"O1": "unknown", "O2": "unknown"} for n in EXPECT if n.startswith("registry_")},
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
    assert r["assessed"] is (name not in NOT_ASSESSED)
    if r["assessed"]:
        assert ("Нужна проверка" in r["summary"]) == needs
    else:  # вместо итога - "риск не оценивается"
        assert r["summary"] == MODEL["notAssessed"]["summary"]


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
    """Гарантия есть, по МИО "нет данных" - не R1, но и не "отсутствие МИО": его никто не устанавливал."""
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
    assert not any("url" in i for i in r1["items"]), "у общей сверки ссылки нет"
    ok = fac(run("zone_clean_0_green"), "R1")
    assert ok["items"][0]["quote"].startswith("Гарантия ЕО №")


# ---------- R1: провенанс по документам(basis.sources) ----------

KZHK = ("Реестр гарантий КЖК, homeportal.kz (демо)", "2026-10-09", "https://homeportal.kz")
MIO = ("Реестр разрешений МИО, elicense.kz (демо)", "2026-10-08", "https://elicense.kz")


def _docs(r):
    """ref документа → (источник, дата, ссылка) из доказательств R1; нет поля - None."""
    return {i["ref"]: (i.get("source"), i.get("date"), i.get("url")) for i in fac(r, "R1")["items"]}


def test_r1_absent_per_document_text_and_items():
    """Свои записи документов важнее общей сверки: текст и доказательства - по документу, со ссылкой."""
    r1 = fac(run("r1_absent_per_doc_hit"), "R1")
    assert r1["evidence"] == ("Проверкой установлено: гарантии ЕО нет (Реестр гарантий КЖК, homeportal.kz (демо), 09.10.2026); "
                              "разрешения МИО нет (Реестр разрешений МИО, elicense.kz (демо), 08.10.2026)")
    assert "Картотека ГАСК" not in r1["evidence"]
    assert _docs(run("r1_absent_per_doc_hit")) == {"guarantee": KZHK, "mio": MIO,
                                                   "bvu": ("Ответ БВУ на запрос ДЭР (демо)", "2026-10-07", None)}


def test_r1_absent_per_document_without_general_check():
    r = run("r1_absent_per_doc_no_general_hit")
    assert fac(r, "R1")["evidence"].startswith("Проверкой установлено: гарантии ЕО нет (Реестр гарантий КЖК")
    # пустая ссылка не выводится; документ без записи и без общей сверки - без источника и даты
    assert _docs(r) == {"guarantee": KZHK, "mio": MIO[:2] + (None,), "bvu": (None, None, None)}


def test_r1_absent_mixed_fallback():
    """Своя запись только у гарантии - МИО(и банк) берут общую сверку; у каждого документа свой источник в тексте."""
    r = run("r1_absent_mixed_fallback_hit")
    assert fac(r, "R1")["evidence"] == ("Проверкой установлено: гарантии ЕО нет (Реестр гарантий КЖК, homeportal.kz (демо), "
                                        "09.10.2026); разрешения МИО нет (Картотека ГАСК (демо), 05.10.2026)")
    general = ("Картотека ГАСК (демо)", "2026-10-05", None)
    assert _docs(r) == {"guarantee": KZHK, "mio": general, "bvu": general}


@pytest.mark.parametrize("name", ["case_b", "r1_absent_sourced_hit", "r1_absent_general_only_hit", "r1_absent_same_source_per_doc_hit"])
def test_r1_absent_same_source_short_text(name):
    """Один источник и одна дата(по выводу) у обоих документов - короткий текст, как у общей сверки."""
    r1 = fac(run(name), "R1")
    assert (r1["state"], r1["reason"]) == ("hit", "absent")
    assert r1["evidence"] == "Проверкой установлено: гарантии ЕО нет и разрешения МИО нет (Картотека ГАСК (демо), 09.10.2026)"


@pytest.mark.parametrize("name,missing", [
    ("r1_absent_unsourced_unknown", "Гарантия ЕО (КЖК), Разрешение МИО"),
    ("r1_absent_no_date_unknown", "Гарантия ЕО (КЖК), Разрешение МИО"),
    ("r1_absent_guarantee_only_sourced_unknown", "Разрешение МИО"),
    # своя запись МИО без даты общую дату не занимает: дата чужой сверки не подтверждает этот документ
    ("r1_absent_own_without_date_unknown", "Разрешение МИО"),
])
def test_r1_absent_needs_both_documents_sourced(name, missing):
    r = run(name)
    r1 = fac(r, "R1")
    assert (r1["state"], r1["reason"], r["score"]) == ("unknown", "absent_unsourced", 0)
    assert r1["evidence"] == \
        f"Отсутствие основания указано, но нет источника или даты сверки по документам: {missing} — нужна проверка"


def test_r1_guarantee_only_sourced_items():
    assert _docs(run("r1_absent_guarantee_only_sourced_unknown")) == {"guarantee": KZHK, "mio": (None, None, None),
                                                                      "bvu": (None, None, None)}
    assert _docs(run("r1_absent_own_without_date_unknown"))["mio"] == (MIO[0], None, MIO[2])


@pytest.mark.parametrize("name", ["r1_unknown_doc_without_source", "r1_unknown_doc_with_general_check"])
def test_r1_unknown_document_without_source(name):
    """Документ "нет данных" не сверялся: в доказательствах ни источника, ни даты, ни ссылки - даже при общей сверке
    и даже при своей записи(у банка во втором случае). Сверенный документ(гарантия) - со своим провенансом."""
    r1 = fac(run(name), "R1")
    assert (r1["state"], r1["reason"]) == ("unknown", "unknown")
    unknown = [i for i in r1["items"] if i["value"] == "unknown"]
    assert [i["ref"] for i in unknown] == ["mio", "bvu"]
    assert all(set(i) == {"kind", "ref", "label", "value"} for i in unknown), unknown
    assert _docs(run(name))["guarantee"] == KZHK


def test_r1_general_check_only_for_checked_documents():
    """Общая сверка подписывает только документы "да/нет" без своей записи: у c6 гарантия "нет данных" не получает
    источник "Реестр разрешений МИО", у c12 гарантия и МИО - ответ банка."""
    assert _docs(run("r1_guarantee_mio_unknown_clear"))["mio"] == (None, None, None)
    assert _docs(run("r1_guarantee_no_mio_clear"))["mio"] == ("Реестр гарантий ЕО (демо)", "2026-10-05", None)
    assert _docs(run_seed("c6"))["guarantee"] == (None, None, None)
    assert _docs(run_seed("c12"))["guarantee"] == _docs(run_seed("c12"))["mio"] == (None, None, None)


@pytest.mark.parametrize("cid", ["c2", "c4", "c11"])
def test_seed_r1_by_document(cid):
    """Сид: у каждого документа в состоянии "да/нет" своя запись - R1 и ссылки в доказательствах по документу."""
    b = SEED_CX[cid]["basis"]
    r1 = fac(run_seed(cid), "R1")
    g, m = b["sources"]["guarantee"], b["sources"]["mio"]
    assert r1["evidence"] == (f"Проверкой установлено: гарантии ЕО нет ({g['source']}, {fmt_date(g['checkedAt'])}); "
                              f"разрешения МИО нет ({m['source']}, {fmt_date(m['checkedAt'])})")
    docs = _docs(run_seed(cid))
    for key, own in b["sources"].items():
        assert docs[key] == (own["source"], own["checkedAt"], own["url"] or None), (cid, key)


def test_seed_document_provenance():
    """Сид: документ "да/нет" - своя запись, без неё общая сверка; документ "нет данных" - без источника и даты."""
    for cid, c in SEED_CX.items():
        b = c["basis"]
        for key, prov in _docs(run_seed(cid)).items():  # у введённых R1 без доказательств
            own = (b.get("sources") or {}).get(key)
            if b.get(key) not in ("yes", "no"):
                assert prov == (None, None, None), (cid, key)
            elif own:
                assert prov == (own["source"], own["checkedAt"], own["url"] or None), (cid, key)
            else:
                assert prov == (b.get("source"), b.get("checkedAt"), None), (cid, key)
    assert set(_docs(run_seed("c14")).values()) == {(None, None, None)}


@pytest.mark.parametrize("name", ["link_hypothesis_079_unknown", "link_high_confidence_unconfirmed_unknown",
                                  "link_blank_confirmed_by_unknown"])
def test_link_hypothesis_evidence(name):
    """Гипотеза ИИ - "нужна проверка" без порога-факта в тексте: уверенность, даже 95-97%, фактом не делает."""
    r = run(name)
    assert fac(r, "R2")["evidence"] == \
        "Предложение внести деньги найдено в публикациях: 1, но привязка к ЖК — гипотеза ИИ, аналитик её не подтвердил — нужна проверка"
    assert fac(r, "R3")["evidence"] == \
        "Реклама продажи найдена в публикациях: 1, но привязка к ЖК — гипотеза ИИ, аналитик её не подтвердил — нужна проверка"
    assert fac(r, "R2")["items"][0]["ref"] and fac(r, "R3")["items"]
    assert "%" not in fac(r, "R2")["evidence"] + fac(r, "R3")["evidence"]
    assert r["summary"].endswith("Нужна проверка: R2, R3.")
    assert not any(link_confirmed(a) for a in CX_FX[name]["ads"])


def test_link_confirmed_by_analyst_regardless_of_confidence():
    for name, conf in [("link_analyst_confirmed_hit", 0.55), ("link_status_confirmed_hit", 0.5)]:
        a = CX_FX[name]["ads"][0]
        assert a["matchConfidence"] == conf and link_confirmed(a), name
    assert CX_FX["link_analyst_confirmed_hit"]["ads"][0]["linkConfirmedBy"]
    assert CX_FX["link_status_confirmed_hit"]["ads"][0]["linkConfirmedBy"] is None
    assert CX_FX["link_high_confidence_unconfirmed_unknown"]["ads"][0]["matchConfidence"] == 0.97


def test_link_mixed_counts_only_confirmed():
    """Подтверждённая аналитиком привязка с уверенностью 0.6 - факт; гипотеза ИИ с уверенностью 0.9 - нет."""
    f = CX_FX["link_mixed_counts_confirmed_only"]
    confirmed = [a["id"] for a in f["ads"] if a.get("linkConfirmedBy")]
    assert len(confirmed) == 1 and [a["matchConfidence"] for a in f["ads"]] == [0.9, 0.6]
    r = run("link_mixed_counts_confirmed_only")
    for code in ["R2", "R3"]:
        assert [i["ref"] for i in fac(r, code)["items"]] == confirmed
    assert fac(r, "R2")["evidence"] == "Предложение внести деньги в публикациях: 1"
    assert fac(r, "R3")["evidence"] == "Реклама продажи до ввода объекта: публикаций 1"


# ---------- решение аналитика по условиям оплаты(вето R2) ----------

def test_r2_terms_rejected_veto():
    r = run("r2_terms_rejected_clear")
    r2, r3 = fac(r, "R2"), fac(r, "R3")
    assert (r2["state"], r2["reason"], r2["points"]) == ("clear", "offer_rejected", 25)
    assert r2["evidence"] == "Аналитик отклонил условия оплаты в публикациях: 1 — это не предложение внести деньги, в балл не входит"
    # отклонённая публикация остаётся в доказательствах - видно, что и почему не засчитано
    assert [(i["ref"], i["marker"], i["quote"]) for i in r2["items"]] == [("fxa33", "бронь", "Платная бронь квартиры от 500 000 ₸.")]
    assert (r3["state"], [i["ref"] for i in r3["items"]]) == ("hit", ["fxa33"]), "R3 решение по условиям оплаты не затрагивает"


def test_r2_terms_rejected_breaks_red_zone():
    """Кейс B с отклонённым аналитиком депозитом: R1 + R3 = 45 - без R2 красной зоны нет."""
    base, vetoed = run("case_b"), run("r2_terms_rejected_case_b_yellow")
    assert (base["score"], base["zone"]) == (70, "red")
    assert (vetoed["score"], vetoed["zone"], hit_codes(vetoed)) == (45, "yellow", ["R1", "R3"])
    assert vetoed["summary"] == "Балл 45: углублённая ручная проверка документов и публикаций."


def test_r2_terms_rejected_only_that_ad():
    r = run("r2_terms_rejected_other_offer_hit")
    assert [i["ref"] for i in fac(r, "R2")["items"]] == ["fxa36"]
    assert fac(r, "R2")["evidence"] == "Предложение внести деньги в публикациях: 1"
    assert [i["ref"] for i in fac(r, "R3")["items"]] == ["fxa35", "fxa36"]


def test_r2_terms_rejected_on_hypothesis_needs_no_r2_check():
    r = run("r2_terms_rejected_hypothesis_clear")
    assert (fac(r, "R2")["state"], fac(r, "R3")["state"]) == ("clear", "unknown")
    assert r["summary"].endswith("Нужна проверка: R3.")
    # привязка-гипотеза - не факт: отклонённая публикация к этому ЖК не относится и в его R2 не показывается
    assert (fac(r, "R2")["reason"], fac(r, "R2")["items"]) == ("none", [])
    assert [i["ref"] for i in fac(r, "R3")["items"]] == ["fxa37"]


def test_r2_terms_rejected_counts_only_confirmed_links():
    """Отклонены условия у публикации с привязкой-гипотезой(свежее) и у привязанной фактом: "отклонено аналитиком"
    считает и показывает только вторую - первая к ЖК не привязана, в доказательства объекта(карточка, сигнал, PDF) не идёт."""
    r = run("r2_terms_rejected_hypothesis_not_counted")
    r2 = fac(r, "R2")
    assert (r2["state"], r2["reason"], [i["ref"] for i in r2["items"]]) == ("clear", "offer_rejected", ["fxa45"])
    assert r2["evidence"] == "Аналитик отклонил условия оплаты в публикациях: 1 — это не предложение внести деньги, в балл не входит"
    assert (fac(r, "R3")["state"], [i["ref"] for i in fac(r, "R3")["items"]]) == ("hit", ["fxa45"])
    # та же публикация-гипотеза без решения аналитика давала бы "нужна проверка" - решение её снимает, но фактом не делает
    f = CX_FX["r2_terms_rejected_hypothesis_not_counted"]
    plain = score_complex(f["complex"], f["developer"], [_without_review(f["ads"][0]), f["ads"][1]], [], AS_OF)
    assert (fac(plain, "R2")["state"], [i["ref"] for i in fac(plain, "R2")["items"]]) == ("unknown", ["fxa44"])


def test_r2_terms_rejected_seed_hypothesis_not_in_complex_evidence():
    """Сид: a4 привязана к c8 гипотезой(71%), a8 - фактом. Отклонены условия у обеих → в R2 ЖК только a8."""
    ads = [{**a, "termsReview": "rejected"} if a["id"] in ("a4", "a8") else a for a in SEED["ads"]]
    c = SEED_CX["c8"]
    r2 = fac(score_complex(c, DEVS.get(c["developerId"]), ads, SEED["appeals"], AS_OF), "R2")
    assert (r2["state"], r2["reason"], [i["ref"] for i in r2["items"]]) == ("clear", "offer_rejected", ["a8"])
    assert r2["evidence"].startswith("Аналитик отклонил условия оплаты в публикациях: 1 ")


def _without_review(a):
    return {k: v for k, v in a.items() if not k.startswith("termsReview")}


def test_terms_accepted_does_not_change_risk():
    f = copy.deepcopy(CX_FX["r2_terms_accepted_hit"])
    assert f["ads"][0]["termsReview"] == "accepted"
    plain = score_complex(f["complex"], f["developer"], [_without_review(a) for a in f["ads"]], [], AS_OF)
    assert run("r2_terms_accepted_hit") == plain
    a = AD_FX["ad_terms_accepted_red"]
    assert run_ad("ad_terms_accepted_red") == score_ad(_without_review(a["ad"]), a["complex"])


def test_ad_terms_rejected():
    r = run_ad("ad_terms_rejected")
    r2 = fac(r, "R2")
    assert (r2["state"], r2["reason"]) == ("clear", "R2_rejected")
    assert r2["evidence"] == "Найден маркер «бронь», но аналитик отклонил: это не предложение внести деньги — в балл не входит"
    assert [i["quote"] for i in r2["items"]] == ["Платная бронь квартиры от 500 000 ₸."]
    assert hit_codes(r) == ["R1", "R3"] and (r["score"], r["zone"]) == (45, "yellow")
    assert r["signals"]["funds"] == ["бронь"], "сигналы словаря остаются как есть — решение аналитика их не стирает"


def test_terms_rejected():
    assert terms_rejected({"termsReview": "rejected"})
    assert not any(terms_rejected(a) for a in [{"termsReview": "accepted"}, {"termsReview": None}, {}])


def _all_ads():
    """Все публикации сида и фикстур со своим ЖК(для свойств модели)."""
    yield from ((a, SEED_CX.get(a.get("complexId") or "")) for a in SEED["ads"])
    yield from ((f["ad"], f["complex"]) for f in FX["ad"])
    yield from ((a, f["complex"] if a.get("complexId") == f["complex"]["id"] else None) for f in FX["complex"] for a in f["ads"])


@pytest.mark.parametrize("review", ["rejected", "accepted", None])
def test_terms_review_touches_only_r2(review):
    """Отклонение условий оплаты снимает только R2(по ЖК и по публикации); подтверждение и "нет решения" ничего не меняют."""
    for cid, c in SEED_CX.items():
        ads = [{**_without_review(a), "termsReview": review} for a in SEED["ads"]]
        base = run_seed(cid)
        r = score_complex(c, DEVS.get(c["developerId"]), ads, SEED["appeals"], AS_OF)
        if review != "rejected":
            assert r == base, cid
            continue
        assert fac(r, "R2")["state"] != "hit" and fac(r, "R3") == fac(base, "R3"), cid
        assert [f for f in r["factors"] if f["code"] != "R2"] == [f for f in base["factors"] if f["code"] != "R2"], cid
        assert r["score"] == base["score"] - (25 if fac(base, "R2")["state"] == "hit" else 0), cid
        assert r["zone"] != "red", f"{cid}: без R2 красной зоны нет"
    for a, c in _all_ads():
        base, r = score_ad(_without_review(a), c), score_ad({**_without_review(a), "termsReview": review}, c)
        if review != "rejected":
            assert r == base, a["id"]
            continue
        assert fac(r, "R2")["state"] != "hit" and fac(r, "R3") == fac(base, "R3"), a["id"]
        if fac(base, "R2")["state"] == "hit":
            assert (fac(r, "R2")["reason"], fac(r, "R2")["items"]) == ("R2_rejected", fac(base, "R2")["items"]), a["id"]


def test_evidence_order_newest_first():
    """Доказательства: свежие первыми, при равной дате - по id как строке(так же сортирует фронтенд)."""
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
    # 32.3 − 12.3 = 19.999999999999996 во float: без округления это было бы "меньше порога"
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
    """Простой подтверждают только датированные снимки "нет"/"низкая"; период - по ним, доказательства - вся серия."""
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
    "ad_high_confidence_unconfirmed": (35, "yellow", True, {"R1": ("unknown", "R1_link_unconfirmed"), "R2": ("hit", "R2_offer"),
                                                            "R3": ("hit", "R3_ads")}),
    "ad_terms_rejected": (45, "yellow", False, {"R1": ABSENT, "R2": ("clear", "R2_rejected"), "R3": ("hit", "R3_ads")}),
    "ad_terms_accepted_red": (70, "red", False, {"R1": ABSENT, "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
    "ad_quote_from_ocr": (35, "yellow", True, {"R1": ("unknown", "unknown"), "R2": ("hit", "R2_offer"), "R3": ("hit", "R3_ads")}),
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
        "Привязка к ЖК — гипотеза ИИ (уверенность 60%), аналитик её не подтвердил — основание объекта не учитывается"
    # уверенность ИИ - информативно: и 97% без подтверждения аналитиком остаются гипотезой
    assert fac(run_ad("ad_high_confidence_unconfirmed"), "R1")["evidence"] == \
        "Привязка к ЖК — гипотеза ИИ (уверенность 97%), аналитик её не подтвердил — основание объекта не учитывается"
    assert fac(run_ad("ad_money_basis_absent_red"), "R2")["evidence"] == "В публикации есть предложение внести деньги"
    assert fac(run_ad("ad_no_complex"), "R1")["evidence"] == "ЖК не определён — основание проверить нельзя"
    r = run_ad("ad_no_markers")
    assert r["signals"] == {"sale": [], "funds": [], "safe": []}
    assert fac(r, "R1")["evidence"] == "Публикация не продвигает продажу — основание объекта не учитывается"
    assert fac(r, "R2")["evidence"] == "Не найдено"


def test_ad_ocr_quote():
    r = run_ad("ad_ocr_only_money")
    item = fac(r, "R2")["items"][0]
    assert (item["marker"], item["quote"], item["quoteFrom"]) == ("предоплат", "ПРЕДОПЛАТА 10% · ФИКСИРУЕМ ЦЕНУ", "ocr")
    assert r["signals"]["funds"] == ["предоплат", "фиксируем цену"]


@pytest.mark.parametrize("name", ["r2_offer_from_ocr_hit", "ad_quote_from_ocr"])
def test_quote_from_marks_screenshot(name):
    """Цитата со скриншота помечена quoteFrom = 'ocr', из текста публикации - 'text' (T-24)."""
    r = run(name) if name in CX_FX else run_ad(name)
    r2, r3 = fac(r, "R2")["items"][0], fac(r, "R3")["items"][0]
    assert (r2["marker"], r2["quote"], r2["quoteFrom"]) == ("первый взнос", "ПЕРВЫЙ ВЗНОС 20% · ДДУ · ФИКСИРУЕМ ЦЕНУ", "ocr")
    assert (r3["marker"], r3["quote"], r3["quoteFrom"]) == ("старт продаж", "Старт продаж!", "text")


def test_quote_from_matches_source_for_every_ad():
    """У каждого доказательства-публикации: цитата есть ⇔ есть quoteFrom, и цитата взята дословно из указанного источника."""
    seen = Counter()
    for a, c in _all_ads():
        for f in score_ad(a, c)["factors"]:
            for i in f["items"]:
                if i["kind"] != "ad":
                    continue
                assert ("quote" in i) == ("quoteFrom" in i), (a["id"], f["code"])
                if "quote" not in i:
                    continue
                src = " ".join((a.get(i["quoteFrom"]) or "").split())
                assert i["quote"].removeprefix("… ").removesuffix(" …") in src, (a["id"], f["code"], i["quoteFrom"])
                if i["quoteFrom"] == "ocr":
                    assert quote(a.get("text"), i["marker"]) is None, "сначала цитата ищется в тексте публикации"
                seen[i["quoteFrom"]] += 1
    assert seen["text"] and seen["ocr"]


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
    """Факт - только после решения аналитика(linkConfirmedBy или подтверждённая публикация); уверенность ИИ не в счёт."""
    for conf in [0.79, 0.8, 0.97, 1]:
        assert not link_confirmed({"matchConfidence": conf, "status": "new"}), conf
        assert not link_confirmed({"matchConfidence": conf, "status": "in_review", "linkConfirmedBy": None}), conf
    assert link_confirmed({"matchConfidence": 0.1, "status": "new", "linkConfirmedBy": "Нестерова А."})
    assert link_confirmed({"matchConfidence": 0.1, "status": "confirmed"})
    assert link_confirmed({"status": "confirmed", "linkConfirmedBy": None})
    assert not link_confirmed({"status": "in_review"})
    assert not link_confirmed({})
    # пустое имя - не подтверждение(пробелы как в Python str.strip - их же отрезает фронтенд); одна дата - тоже
    for blank in ["", "  ", "\u00a0\u3000\t\n"]:
        assert not link_confirmed({"status": "new", "linkConfirmedBy": blank}), repr(blank)
    assert not link_confirmed({"status": "new", "linkConfirmedAt": "2026-10-11T10:00:00+05:00"})


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
    if g == "yes":  # "отсутствие МИО не является R1" - только при установленном отсутствии МИО
        assert fac(r, "R1")["reason"] == ("ok_guarantee_no_mio" if m == "no" else "ok_guarantee")
    if "unknown" in (g, m) and g != "yes" and not (m == "yes" and k == "yes"):
        assert state == "unknown"
    assert r["score"] == (35 if state == "hit" else 0)


OWN = {"full": {"source": "Реестр (демо)", "url": "https://example.kz", "checkedAt": "2026-10-08"},
       "no_date": {"source": "Реестр (демо)", "url": None, "checkedAt": None},
       "no_source": {"source": "", "url": None, "checkedAt": "2026-10-08"},
       "empty": {}, "null": None, "none": "—"}


@pytest.mark.parametrize("own_g,own_m,general", list(itertools.product(OWN, OWN, [True, False])))
def test_r1_absent_requires_provenance_of_both_documents(own_g, own_m, general):
    """Гарантии и МИО нет: R1 ⇔ у каждого из двух документов есть источник и дата - своя запись целиком, а без неё общая сверка."""
    f = _clean()
    basis = {"guarantee": "no", "mio": "no", "bvu": "no", "sources": {}}
    for key, own in (("guarantee", own_g), ("mio", own_m)):
        if own != "none":
            basis["sources"][key] = copy.deepcopy(OWN[own])
    if general:
        basis |= {"source": "Картотека ГАСК (демо)", "checkedAt": "2026-10-09"}
    f["complex"]["basis"] = basis
    r1 = fac(score_complex(f["complex"], f["developer"], [], [], AS_OF), "R1")
    ok = lambda own: own == "full" or (own in ("empty", "null", "none") and general)
    assert (r1["state"], r1["reason"]) == (("hit", "absent") if ok(own_g) and ok(own_m) else ("unknown", "absent_unsourced"))
    if r1["state"] == "hit":
        short = (own_g == "full") == (own_m == "full")  # оба своих одинаковых или оба по общей сверке
        assert ("гарантии ЕО нет и разрешения МИО нет" in r1["evidence"]) == short, r1["evidence"]
    urls = {i["ref"]: i.get("url") for i in r1["items"]}
    assert urls == {"guarantee": OWN["full"]["url"] if own_g == "full" else None,
                    "mio": OWN["full"]["url"] if own_m == "full" else None, "bvu": None}


@pytest.mark.parametrize("conf", [0, 0.31, 0.5, 0.79, 0.8, 0.95, 1])
@pytest.mark.parametrize("status", ["new", "needs_check", "in_review"])
def test_link_hypothesis_never_becomes_r2_r3(conf, status):
    """Без подтверждения аналитиком привязка - гипотеза при любой уверенности ИИ."""
    f = _clean()
    ad = copy.deepcopy(CX_FX["case_b"]["ads"][0]) | {"matchConfidence": conf, "status": status, "complexId": f["complex"]["id"],
                                                     "linkConfirmedBy": None, "linkConfirmedAt": None}
    r = score_complex(f["complex"], f["developer"], [ad], [], AS_OF)
    assert (fac(r, "R2")["state"], fac(r, "R3")["state"]) == ("unknown", "unknown")
    assert (r["score"], r["zone"], r["needsCheck"]) == (0, "green", True)
    absent = copy.deepcopy(CX_FX["case_b"]["complex"])
    r1 = fac(score_ad(ad, absent), "R1")
    assert (r1["state"], r1["reason"]) == ("unknown", "R1_link_unconfirmed")
    assert f"(уверенность {math.floor(conf * 100 + 0.5)}%)" in r1["evidence"]
    # то же с подтверждением аналитика - факт при любой уверенности
    confirmed = ad | {"linkConfirmedBy": "Тестова А. (демо)", "linkConfirmedAt": "2026-10-11T10:00:00+05:00"}
    r = score_complex(f["complex"], f["developer"], [confirmed], [], AS_OF)
    assert hit_codes(r) == ["R2", "R3"] and (r["score"], r["needsCheck"]) == (35, False)
    assert fac(score_ad(confirmed, absent), "R1")["state"] == "hit"


def _all_results():
    g = golden.build()
    yield from (("seed:" + k, r, False) for k, r in g["seed"]["complexes"].items())
    yield from (("fx:" + k, r, False) for k, r in g["fixtures"]["complex"].items())
    yield from (("seed-ad:" + k, r, True) for k, r in g["seed"]["ads"].items())
    yield from (("fx-ad:" + k, r, True) for k, r in g["fixtures"]["ad"].items())


def _templates(code, reason, is_ad):
    """Шаблоны текста причины: основной и её варианты(ключ "<причина>_<вариант>" в factors[].variants)."""
    if is_ad and reason in MODEL["ad"]["reasons"]:
        return [MODEL["ad"]["reasons"][reason]]
    variants = FACTORS[code].get("variants", {})
    return [FACTORS[code]["reasons"][reason], *(t for k, t in variants.items() if k.startswith(reason + "_"))]


def _matches(tpl, text):
    return re.fullmatch("".join(".+?" if p.startswith("{") else re.escape(p) for p in re.split(r"(\{\w+\})", tpl)), text, re.S)


def test_formula_and_texts_hold_for_every_result():
    """Для всего сида и всех синтетических случаев: формула §5, зона, "нужна проверка", тексты из модели."""
    for name, r, is_ad in _all_results():
        hit = hit_codes(r)
        if r.get("assessed", True):
            assert r["score"] == min(100, sum(FACTORS[c]["weight"] for c in hit)), name
            assert r["zone"] == zone_for(r["score"], hit), name
        else:  # карточка из реестра без проверенных сведений: балла и зоны нет(0 / green - только для формы ответа)
            assert (r["score"], r["zone"], r["summary"]) == (0, "green", MODEL["notAssessed"]["summary"]), name
        assert r["needsCheck"] == bool(unknown_codes(r)), name
        assert ("R2" not in hit) or ("R3" in hit), f"{name}: предложение денег — это и реклама продажи (кейсы A/B)"
        for f in r["factors"]:
            assert (f["title"], f["points"]) == (FACTORS[f["code"]]["title"], FACTORS[f["code"]]["weight"]), name
            assert any(_matches(t, f["evidence"]) for t in _templates(f["code"], f["reason"], is_ad)), \
                (name, f["code"], f["evidence"])
            assert "{" not in f["evidence"], (name, f["code"])
            if f["state"] == "hit":
                assert f["items"], f"{name}: у подтверждённого {f['code']} должно быть доказательство (ТЗ §7)"
        for o in r.get("observations", []):
            assert "{" not in o["text"] and o["state"] in OBS_STATES[o["code"]], name
        assert "{" not in r.get("summary", ""), name


def test_fixtures_cover_every_branch():
    covered, ad_covered, obs_covered, summaries, variants = set(), set(), set(), set(), set()
    for name, r, is_ad in _all_results():
        for f in r["factors"]:
            (ad_covered if is_ad and f["reason"] in AD_REASONS else covered).add((f["code"], f["reason"]))
            variants |= {(f["code"], k) for k, t in FACTORS[f["code"]].get("variants", {}).items()
                         if k.startswith(f["reason"] + "_") and _matches(t, f["evidence"])}
        obs_covered |= {(o["code"], o["state"]) for o in r.get("observations", [])}
        if not is_ad:
            summaries.add(r["zone"] if r["zone"] != "yellow" else ("yellow_high" if r["score"] >= 60 else "yellow"))
            if r["needsCheck"]:
                summaries.add("needs_check")
    assert covered == {(c, x) for c, xs in REASONS.items() for x in xs}
    assert {x for _, x in ad_covered} == AD_REASONS
    assert variants == {(c, k) for c in CODES for k in FACTORS[c].get("variants", {})} == {("R1", "absent_same_source")}
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
    for name in ["link_mixed_counts_confirmed_only", "link_order_newest_first", "r2_terms_rejected_other_offer_hit",
                 "r4_two_verified_newest_first"]:
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


def test_seed_link_confirmations():
    """В сиде привязки подтверждены аналитиками явно; a4, a9, a12 - гипотезы ИИ(баллы сида от этого не меняются)."""
    confirmed = {a["id"] for a in SEED["ads"] if link_confirmed(a)}
    assert confirmed == {"a1", "a2", "a3", "a5", "a6", "a7", "a8", "a10", "a11"}
    for a in SEED["ads"]:
        assert "linkConfirmedBy" in a and "linkConfirmedAt" in a, a["id"]
        assert bool(a["linkConfirmedBy"]) == bool(a["linkConfirmedAt"]), a["id"]
        if a["linkConfirmedBy"]:
            assert a["linkConfirmedBy"] in {x["name"] for x in SEED["analysts"]}, a["id"]
    res = {cid: run_seed(cid) for cid in SEED_CX}
    assert {cid: res[cid]["score"] for cid in ["c2", "c4", "c8", "c10", "c14"]} == {"c2": 70, "c4": 100, "c8": 60, "c10": 10, "c14": 35}


def test_seed_without_analyst_confirmations():
    """Сотрите подтверждения аналитиков - и высокая уверенность ИИ(0.83-0.99) не даст ни R2, ни R3:
    фактом остаётся только привязка публикации, по которой аналитик подтвердил нарушение(a6 → c11)."""
    ads = [{**a, "linkConfirmedBy": None, "linkConfirmedAt": None} for a in SEED["ads"]]
    by_status = {a["complexId"] for a in ads if a.get("status") == "confirmed"}
    assert by_status == {"c11"}
    for cid, c in SEED_CX.items():
        r = score_complex(c, DEVS.get(c["developerId"]), ads, SEED["appeals"], AS_OF)
        for code in ["R2", "R3"]:
            assert (fac(r, code)["state"] == "hit") == (cid in by_status and fac(run_seed(cid), code)["state"] == "hit"), (cid, code)
    assert run_seed("c2")["zone"] == "red"
    assert score_complex(SEED_CX["c2"], DEVS["d2"], ads, SEED["appeals"], AS_OF)["zone"] != "red"


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
        assert {"id", "text", "matchConfidence", "status", "linkConfirmedBy"} <= set(f["ad"]), f["name"]
    for f in FX["complex"]:  # подтверждение привязки задано явно(строка или null) у каждой публикации
        assert all("linkConfirmedBy" in a and "linkConfirmedAt" in a for a in f["ads"]), f["name"]


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
                    f"shared/testdata/risk_golden.json устарел ({part}.{kind}.{k}) — перегенерируйте: {REGEN}"
    assert actual == stored, f"shared/testdata/risk_golden.json устарел — перегенерируйте: {REGEN}"


def test_as_of_accepts_datetime_and_string():
    c, dev = SEED_CX["c4"], DEVS["d4"]
    base = score_complex(c, dev, SEED["ads"], SEED["appeals"], AS_OF)
    assert score_complex(c, dev, SEED["ads"], SEED["appeals"], datetime(2026, 10, 15, 23, 59)) == base
    assert score_complex(c, dev, SEED["ads"], SEED["appeals"], date(2026, 10, 15)) == base


def _frozen_now(utc):
    """Подмена datetime в app.risk: now(tz) отдаёт заданный момент UTC; без tz - "наивное" UTC(будто процесс в UTC)."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc.astimezone(tz) if tz else utc.replace(tzinfo=None)
    return Frozen


@pytest.mark.parametrize("utc,day", [
    (datetime(2026, 10, 15, 18, 59, tzinfo=timezone.utc), "2026-10-15"),  # 23:59 в Усть-Каменогорске
    (datetime(2026, 10, 15, 19, 0, tzinfo=timezone.utc), "2026-10-16"),   # 00:00 - по UTC ещё 15-е
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
