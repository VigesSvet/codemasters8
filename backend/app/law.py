"""Нормативная база модели риска - shared/law.json(общий с фронтендом, frontend/src/lib/law.ts).

Закон РК от 7 апреля 2016 года № 486-V "О долевом участии в жилищном строительстве", статьи 7, 8, 18(ориентиры ТЗ) и
23(реклама): выдержки(excerpts) - дословный текст из ИПС "Әділет" с адресом и редакцией источника, summary - пересказ
системы, не текст закона. refs: код фактора(R1-R3) или документа основания(guarantee, mio, bvu) → [{article, parts,
why}]; у факторов те же ссылки(без why) - поле law в shared/risk_model.json. Ссылки справочные: оценка риска - не
юридическое заключение, и "нет разрешения МИО" само по себе не значит "незаконно"(ст. 8 п. 6 - гарантия ЕО).
"""
import json
from datetime import date
from typing import Any

from .risk_model import FACTORS, SHARED

Obj = dict[str, Any]

LAW: Obj = json.loads((SHARED / "law.json").read_text(encoding="utf-8"))
ACT: Obj = LAW["act"]
ARTICLES: dict[str, Obj] = {a["num"]: a for a in LAW["articles"]}
REFS: dict[str, list[Obj]] = LAW["refs"]
NUANCE: str = LAW["nuance"]

_MONTHS_GEN = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
               "ноября", "декабря")


def act_date_text() -> str:
    """Дата принятия закона из law.json(act.date) по-русски, как в реквизитах: "7 апреля 2016 года"."""
    d = date.fromisoformat(ACT["date"])
    return f"{d.day} {_MONTHS_GEN[d.month - 1]} {d.year} года"


def refs(code: str) -> list[Obj]:
    """Ссылки на нормы по коду: у фактора - поле law модели риска, у документа основания - refs из law.json.
    Копии: правка результата вызывающим не меняет общую модель."""
    f = FACTORS.get(code)
    rs = (f.get("law") if f is not None else REFS.get(code)) or []
    return [{"article": r["article"], "parts": list(r["parts"])} for r in rs]


def ref_text(r: Obj) -> str:
    """{article: "7", parts: ["1", "2"]} → "ст. 7 п. 1, 2"; без пунктов - "ст. 23"."""
    parts = r.get("parts") or []
    return f"ст. {r['article']}" + (f" п. {', '.join(parts)}" if parts else "")


def basis_text(code: str) -> str:
    """"Основание: ст. 7 п. 1, 2; ст. 8 п. 6; ст. 18 п. 1 Закона РК "О долевом участии в жилищном строительстве"";
    пустая строка - норма к фактору не относится(R4-R6: обращения, готовность, простой)."""
    rs = refs(code)
    return f"Основание: {'; '.join(ref_text(r) for r in rs)} {ACT['short']}" if rs else ""


EXCERPT_MAX = 600  # символов выдержки в промпте; длиннее - обрезается по словам с "…"


def prompt_context() -> str:
    """Компактный правовой контекст для системного промпта анализа ИИ: по статье - заголовок, пересказ и дословные
    выдержки, отмеченные prompt в law.json(основные пункты), плюс правовой нюанс ТЗ."""
    lines = [f"{ACT['title']} от {act_date_text()} № {ACT['number']}. «Текст» — дословно из закона, «Кратко» — "
             "пересказ."]
    for a in LAW["articles"]:
        lines.append(f"Статья {a['num']}. {a['title']}. Кратко: {a['summary']}")
        for e in a["excerpts"]:
            if e.get("prompt"):
                t = " ".join(e["text"].split())
                t = t if len(t) <= EXCERPT_MAX else t[:EXCERPT_MAX - 1].rsplit(" ", 1)[0] + "…"
                lines.append(f"  Текст: {t}")
    lines.append(NUANCE)
    return "\n".join(lines)
