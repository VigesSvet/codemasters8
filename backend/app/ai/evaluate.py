"""Проверка ИИ на новых данных (T-25): тексты shared/ai_cases.json, которых нет в сиде, → ожидаемые условия оплаты и ЖК.

Метрики: termsFound / termsExpected — сколько ожидаемых условий найдено; termsPrecision — доля найденных условий, которые
ожидались; linkTop1 — доля кейсов с ожидаемым ЖК, где он первая гипотеза; falsePositives — кейсы, где движок уверенно
(не ниже lowConfidence) предложил бы не тот ЖК или ЖК там, где его быть не должно.
"""
import json
from typing import Any, Dict, List, Optional

from ..risk_model import SHARED
from . import ENGINE, analyze, seed_catalog
from .matching import Catalog
from .terms import LABELS

Obj = Dict[str, Any]

CASES = SHARED / "ai_cases.json"
TERM_KEYS = ("kind", "amount", "currency", "unit", "percent", "months", "money")


def load_cases() -> List[Obj]:
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def term_ok(expected: Obj, got: Obj) -> bool:
    """Найденное условие соответствует ожидаемому: тот же вид и все указанные в ожидании значения."""
    return all(got.get(k) == v for k, v in expected.items())


def _term_text(t: Obj) -> str:
    parts = [LABELS.get(t["kind"], t["kind"])]
    if t.get("amount") is not None:
        parts.append(f"{t['amount']:,}".replace(",", " ") + (" ₸" if t.get("currency", "KZT") == "KZT" else f" {t['currency']}")
                     + ("/м²" if t.get("unit") == "m2" else ""))
    if t.get("percent") is not None:
        parts.append(f"{t['percent']}%")
    if t.get("months") is not None:
        parts.append(f"{t['months']} мес.")
    if t.get("money") is False:
        parts.append("не предложение денег")
    return " ".join(parts)


def run_case(case: Obj, cat: Catalog) -> Obj:
    an = analyze(cat, case.get("text"), case.get("ocr"), case.get("account"), case.get("platform"), case.get("url"))
    exp = case["expected"]
    exp_terms: List[Obj] = exp.get("terms") or []
    got_terms = [{k: t[k] for k in (*TERM_KEYS, "from", "confidence", "low") if k in t} for t in an["terms"]]
    found = [e for e in exp_terms if any(term_ok(e, g) for g in got_terms)]
    correct = [g for g in got_terms if any(term_ok(e, g) for e in exp_terms)]
    hyps = an["hypotheses"]
    top: Optional[Obj] = hyps[0] if hyps else None
    want = exp.get("complexId")
    notes = [case["note"]] if case.get("note") else []
    if want:
        link_ok = bool(top) and top["complexId"] == want and ("low" not in exp or top["low"] == exp["low"])
    elif exp.get("noHypotheses"):
        link_ok = not hyps
    else:
        link_ok = all(h["low"] for h in hyps)
    false_positive = bool(top) and not top["low"] and top["complexId"] != want
    if not link_ok:
        got = f"{top['complexName']} ({round(top['confidence'] * 100)}%)" if top else "нет гипотез"
        notes.append(f"ЖК: ожидалось {want or 'без уверенной привязки'}, получено {got}")
    missing = [e for e in exp_terms if e not in found]
    extra = [g for g in got_terms if g not in correct]
    if missing:
        notes.append("не найдено: " + "; ".join(_term_text(e) for e in missing))
    if extra:
        notes.append("лишнее: " + "; ".join(_term_text(g) for g in extra))
    return {
        "id": case["id"], "title": case.get("title"), "text": case.get("text") or "", "ocr": case.get("ocr"),
        "account": case.get("account"), "expected": exp,
        "got": {"complexId": top["complexId"] if top else None, "complexName": top["complexName"] if top else None,
                "confidence": top["confidence"] if top else None, "low": top["low"] if top else None,
                "hypotheses": [{"complexId": h["complexId"], "confidence": h["confidence"], "low": h["low"]} for h in hyps],
                "terms": got_terms},
        "ok": link_ok and not missing and not extra,
        "notes": ". ".join(notes) or None,
        "_counts": {"expected": len(exp_terms), "found": len(found), "got": len(got_terms), "correct": len(correct),
                    "linkCase": bool(want), "top1": bool(want) and bool(top) and top["complexId"] == want,
                    "falsePositive": false_positive},
    }


def run(cat: Optional[Catalog] = None) -> Obj:
    cat = cat or seed_catalog()
    rows = [run_case(c, cat) for c in load_cases()]
    counts = [r.pop("_counts") for r in rows]
    s = lambda key: sum(int(c[key]) for c in counts)  # noqa: E731
    link_cases = s("linkCase")
    return {
        "engine": ENGINE,
        "cases": rows,
        "metrics": {
            "termsFound": s("found"), "termsExpected": s("expected"),
            "termsPrecision": round(s("correct") / s("got"), 2) if s("got") else 1.0,
            "linkTop1": round(s("top1") / link_cases, 2) if link_cases else 1.0,
            "linkCases": link_cases, "falsePositives": s("falsePositive"),
        },
    }
