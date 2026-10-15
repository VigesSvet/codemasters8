"""Описание модели риска — единый источник правды для бэкенда и фронтенда.

Сам файл модели лежит в shared/risk_model.json: коды, веса, условия, пороги, словари маркеров,
тексты пояснений и контрольные кейсы. Фронтенд импортирует тот же JSON (frontend/src/lib/risk.ts).
"""
import json
import os
import re
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable


def _shared_dir() -> Path:
    env = os.getenv("SAQ_SHARED_DIR")
    if env:
        return Path(env)
    for p in Path(__file__).resolve().parents:
        if (p / "shared" / "risk_model.json").is_file():
            return p / "shared"
    raise RuntimeError("Не найден каталог shared/ с risk_model.json — задайте SAQ_SHARED_DIR")


SHARED = _shared_dir()
MODEL: Dict[str, Any] = json.loads((SHARED / "risk_model.json").read_text(encoding="utf-8"))

VERSION: str = MODEL["version"]
T: Dict[str, Any] = MODEL["thresholds"]
MARKERS: Dict[str, list] = MODEL["markers"]
BUILT: str = MODEL["builtStage"]
# Дата расчёта по умолчанию и время в PDF — по Усть-Каменогорску (UTC+5), а не по поясу процесса
ALMATY = timezone(timedelta(hours=5), "UTC+5")
FACTORS: Dict[str, Dict[str, Any]] = {f["code"]: f for f in MODEL["factors"]}
OBSERVATIONS: Dict[str, Dict[str, Any]] = {o["code"]: o for o in MODEL["observations"]}

_PH = re.compile(r"\{(\w+)\}")


def fill(template: str, values: Dict[str, Any] | None = None) -> str:
    """Подставляет {ключ} из values; неизвестные ключи оставляет как есть (как и fill() во фронтенде)."""
    values = values or {}
    return _PH.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def fmt_date(iso: str | None) -> str:
    """'2026-10-09' или '2026-10-09T18:22' → '09.10.2026' без часовых поясов."""
    if not iso or len(iso) < 10:
        return "—"
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def zone_for(score: int, hits: Iterable[str]) -> str:
    z = MODEL["zones"]
    hit = set(hits)
    if score >= z["redFrom"] and all(code in hit for code in z["redRequires"]):
        return "red"
    return "yellow" if score >= z["yellowFrom"] else "green"
