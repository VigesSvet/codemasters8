"""ИИ-модуль SAQ Qurylys(ТЗ §6): детерминированный офлайн-анализ публикаций на сервере, без внешних API и ключей.

- ocr.py - распознавание скриншота(Tesseract LSTM, rus+kaz+eng) с предобработкой;
- terms.py - условия оплаты(бронь, депозит, взнос, рассрочка, ДДУ, цена…) с дословной цитатой и уверенностью;
- matching.py - гипотезы "публикация ↔ ЖК" по названию(в т. ч. транслитерация KZ/RU/Latin), адресу, аккаунту,
  телефону, истории аккаунта и району;
- evaluate.py - прогон на новых текстах shared/ai_cases.json(T-25).

Всё, что выдаёт модуль, - гипотезы: привязку к ЖК фактом делает аналитик(linkConfirmedBy), решение по условиям оплаты -
тоже аналитик(termsReview). Юридического вердикта модуль не выносит. Одинаковый вход → одинаковый результат.
"""
import json
from functools import lru_cache
from typing import Any, Dict, Optional
from collections.abc import Iterable

from ..risk import ad_signals
from ..risk_model import SHARED, T
from .matching import Catalog, build_catalog, hypotheses
from .terms import extract

Obj = dict[str, Any]

ENGINE = "saq-ai-1"


def analyze(cat: Catalog, text: str | None, ocr: str | None = None, account: str | None = None,
            platform: str | None = None, url: str | None = None, rejected: Iterable[str] = (),
            limit: int | None = None) -> Obj:
    """Analysis: условия оплаты, гипотезы ЖК(до hypothesesTop, без отклонённых аналитиком), сигналы словарей модели,
    найденные телефоны и адреса. platform пока не влияет на результат(зарезервировано)."""
    skip = set(rejected or ())
    found, info = hypotheses(cat, text, ocr, account, url)
    hidden = [h for h in found if h["complexId"] in skip]
    hyps = [h for h in found if h["complexId"] not in skip][:limit or T["hypothesesTop"]]
    notes = []
    if len(info["named"]) >= 2:
        notes.append(f"В тексте названы несколько ЖК ({', '.join(info['named'])}) — похоже на подборку: "
                     "уверенность каждой гипотезы снижена")
    if not hyps and hidden:  # признаки были, но все найденные ЖК аналитик уже отклонил
        notes.append(f"Других гипотез нет: отклонённые аналитиком ЖК ({len(hidden)}) не предлагаются — привязку делает "
                     "аналитик вручную")
    elif not hyps:
        notes.append("Признаков конкретного ЖК не найдено — привязку делает аналитик вручную")
    elif hidden:
        notes.append(f"Отклонённые аналитиком ЖК не предлагаются: {len(hidden)}")
    return {"engine": ENGINE, "terms": extract(text, ocr), "hypotheses": hyps,
            "signals": ad_signals({"text": text or "", "ocr": ocr or ""}), "phones": info["phones"],
            "addresses": info["addresses"], "note": ". ".join(notes) or None}


def all_hypotheses(cat: Catalog, ad: Obj) -> list:
    """Все гипотезы по сохранённой публикации(без ограничения top и без учёта отклонённых) - для решения аналитика."""
    return hypotheses(cat, ad.get("text"), ad.get("ocr"), ad.get("account"), ad.get("url"))[0]


@lru_cache(maxsize=1)
def case_catalog() -> Catalog:
    """Справочник кейсов проверки ИИ - catalog в shared/ai_cases.json(вымышленные ЖК, только для этой проверки):
    воспроизводимо и не зависит от данных и решений в БД."""
    cat = json.loads((SHARED / "ai_cases.json").read_text(encoding="utf-8"))["catalog"]
    return build_catalog(cat["complexes"], cat["developers"], cat.get("history", []))
