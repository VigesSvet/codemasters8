"""Риск-скоринг ЖК и публикаций. Логика совпадает с frontend/src/lib/risk.ts.

Правила (по ТЗ §5):
- отсутствие сведений → 0 баллов и отметка «Нужна проверка»;
- отсутствие отдельного разрешения МИО при действующей гарантии ЕО — не R1;
- красная зона только при score ≥ 60 и подтверждённых R1 + R2.
"""
from typing import Iterable, List, Optional

from .models import Ad, Complex

SALE = ["продаж", "продаём", "продаем", "продается", "продаётся", "квартир", "бронь", "цена", "₸"]
FUNDS = ["бронь", "бронирование", "первый взнос", "взнос", "рассрочка", "ДДУ", "долевого участия", "от фундамента",
         "цена котлована", "напрямую застройщику", "переуступка", "фиксируем цену"]
SAFE = ["гарантия ЕО", "гарантии ЕО", "номер гарантии", "ключи на руках", "сданном доме", "введён в эксплуатацию"]


def _hits(text: str, words: Iterable[str]) -> List[str]:
    t = text.lower()
    found = [w for w in words if w.lower() in t]
    return [w for w in found if not any(o != w and w.lower() in o.lower() for o in found)]


def ad_signals(ad: Ad) -> dict:
    t = f"{ad.text} {ad.ocr or ''}"
    return {"sale": _hits(t, SALE), "funds": _hits(t, FUNDS), "safe": _hits(t, SAFE)}


def score_complex(c: Complex, ads: List[Ad]) -> dict:
    linked = [a for a in ads if a.complex_id == c.id and a.status != "dismissed"]
    built = c.stage == "введён"
    b = c.basis or {}
    f = []

    basis_ok = b.get("guarantee") == "yes" or (b.get("mio") == "yes" and b.get("bvu") == "yes")
    basis_missing = b.get("guarantee") == "no" and (b.get("mio") == "no" or b.get("bvu") == "no")
    f.append(dict(code="R1", title="Нет основания для привлечения средств", points=35,
                  state="clear" if built or basis_ok else "hit" if basis_missing else "unknown",
                  evidence="Объект введён" if built else "Основание подтверждено" if basis_ok
                  else "Гарантия ЕО отсутствует, МИО/БВУ не подтверждено" if basis_missing else "Сведения не получены"))

    sale_ads = [a for a in linked if (s := ad_signals(a))["sale"] and not s["safe"]]
    f.append(dict(code="R2", title="Реклама продажи на стадии строительства", points=30,
                  state="hit" if sale_ads and not built else "clear",
                  evidence=f"{len(sale_ads)} публ." if sale_ads else "Нет публикаций с признаками продажи"))

    lst = c.in_published_list
    f.append(dict(code="R3", title="Отсутствует в опубликованном перечне", points=10,
                  state="clear" if built else {"no": "hit", "unknown": "unknown"}.get(lst, "clear"),
                  evidence={"yes": "Есть в перечне", "no": "Нет в перечне"}.get(lst, "Перечень не сверен")))

    photos = c.photos or []
    f.append(dict(code="R4", title="Простой строительства по снимкам", points=10 if c.idle_days >= 90 else 5,
                  state="clear" if built else "unknown" if not photos else "hit" if c.idle_days >= 30 else "clear",
                  evidence=f"Нет активности {c.idle_days} дн." if c.idle_days else "Активность есть"))

    funds = sorted({w for a in linked for w in ad_signals(a)["funds"]})
    f.append(dict(code="R5", title="Признаки сбора денег в тексте", points=10,
                  state="hit" if funds and not built else "clear", evidence=", ".join(funds) or "Не найдено"))

    d = c.developer
    dev_risk = bool(d and d.unfinished > 0 and d.objects <= 3)
    f.append(dict(code="R6", title="Профиль застройщика", points=10, state="hit" if dev_risk else "clear",
                  evidence=f"{d.objects} объектов, незавершённых {d.unfinished}" if d else "—"))

    score = min(100, sum(x["points"] for x in f if x["state"] == "hit"))
    r1, r2 = f[0]["state"] == "hit", f[1]["state"] == "hit"
    zone = "red" if score >= 60 and r1 and r2 else "yellow" if score >= 25 else "green"
    needs = any(x["state"] == "unknown" for x in f)
    summary = {
        "red": "Подтверждены R1 и R2: реклама продажи без основания. Требуется решение аналитика.",
        "yellow": "Есть отклонения — рекомендуется проверка.",
    }.get(zone, "Часть сведений не получена." if needs else "Основания подтверждены, замечаний нет.")
    return dict(score=score, zone=zone, needs_check=needs, factors=f, summary=summary)


def match_complex(text: str, complexes: List[Complex]) -> Optional[Complex]:
    t = text.lower()
    for c in complexes:
        if c.name.lower() in t:
            return c
    return None


def score_ad(ad: Ad, c: Optional[Complex], ads: List[Ad]) -> dict:
    s = ad_signals(ad)
    score = min(40, len(s["sale"]) * 10) + min(30, len(s["funds"]) * 10) - len(s["safe"]) * 25
    if c is not None:
        if score_complex(c, ads)["factors"][0]["state"] == "hit":
            score += 30
        if c.stage == "введён":
            score -= 30
    else:
        score += 10
    score = max(0, min(100, score))
    zone = "red" if score >= 60 else "yellow" if score >= 30 else "green"
    return dict(score=score, zone=zone, **s)
