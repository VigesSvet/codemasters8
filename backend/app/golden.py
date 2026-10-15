"""Эталонные результаты модели риска: по ним проверяется, что Python и TypeScript считают одинаково.

    cd backend && .venv/bin/python -m app.golden     # перезаписать shared/risk_golden.json

Входы: shared/seed_data.json (весь сид) и shared/risk_fixtures.json (синтетические случаи):
    {"complex": [{"name", "asOf"?, "complex", "developer", "ads", "appeals"}],
     "ad":      [{"name", "ad", "complex"}]}
"""
import json
from typing import Any, Dict

from .risk import score_ad, score_complex
from .risk_model import SHARED, VERSION

AS_OF = "2026-10-15"
GOLDEN = SHARED / "risk_golden.json"


def build() -> Dict[str, Any]:
    seed = json.loads((SHARED / "seed_data.json").read_text(encoding="utf-8"))
    fx_path = SHARED / "risk_fixtures.json"
    fx = json.loads(fx_path.read_text(encoding="utf-8")) if fx_path.exists() else {"complex": [], "ad": []}
    devs = {d["id"]: d for d in seed["developers"]}
    cxs = {c["id"]: c for c in seed["complexes"]}
    return {
        "asOf": AS_OF,
        "modelVersion": VERSION,
        "seed": {
            "complexes": {c["id"]: score_complex(c, devs.get(c["developerId"]), seed["ads"], seed["appeals"], AS_OF)
                          for c in seed["complexes"]},
            "ads": {a["id"]: score_ad(a, cxs.get(a.get("complexId") or "")) for a in seed["ads"]},
        },
        "fixtures": {
            "complex": {f["name"]: score_complex(f["complex"], f.get("developer"), f.get("ads", []), f.get("appeals", []),
                                                 f.get("asOf", AS_OF)) for f in fx.get("complex", [])},
            "ad": {f["name"]: score_ad(f["ad"], f.get("complex")) for f in fx.get("ad", [])},
        },
    }


def dump(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    GOLDEN.write_text(dump(build()), encoding="utf-8")
    print(f"записано: {GOLDEN}")
