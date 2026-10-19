"""Нормативная база (shared/law.json, app.law): структура, ссылки факторов модели риска на статьи, тексты оснований,
нормативная база в PDF сигнальной карточки и в GET /api/risk/model."""
import re
from datetime import datetime, timezone

from app import law, pdf
from app.risk_model import FACTORS, MODEL

DOCS = {d["key"] for d in MODEL["documents"]}


def test_act_and_articles():
    act = law.ACT
    assert act["number"] == "486-V" and act["date"] == "2016-04-07"
    assert act["url"] == "https://adilet.zan.kz/rus/docs/Z1600000486"
    assert act["redaction"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", act["fetchedAt"])
    assert act["short"].startswith("Закона РК «О долевом участии в жилищном строительстве»")
    assert list(law.ARTICLES) == ["7", "8", "18", "23"]  # ориентиры ТЗ (7, 8, 18) и реклама (23)
    for num, a in law.ARTICLES.items():
        assert a["url"] == f"{act['url']}#z{num}" and a["title"] and a["summary"]
        assert a["footnote"].startswith(f"Сноска. Статья {num} ")  # сноска об изменениях — как в источнике
        parts = [e["part"] for e in a["excerpts"]]
        assert parts and len(parts) == len(set(parts))
        for e in a["excerpts"]:
            assert e["text"] == e["text"].strip() and "  " not in e["text"]
            if re.fullmatch(r"\d+(-\d+)?", e["part"]):  # пронумерованный пункт начинается со своего номера
                assert e["text"].startswith(f"{e['part']}. "), (num, e["part"])
    # ключевые положения — дословно
    assert "одним из следующих способов:\n1) получение гарантии Единого оператора;" in law.ARTICLES["7"]["excerpts"][0]["text"]
    six = next(e["text"] for e in law.ARTICLES["8"]["excerpts"] if e["part"] == "6")
    assert "не требует получения застройщиком и уполномоченной компанией разрешения на привлечение денег дольщиков" in six
    assert "«нет разрешения = незаконно»" in law.NUANCE
    assert "не юридическое заключение" in law.LAW["note"]


def test_factor_refs_match_law_json():
    for code, f in FACTORS.items():
        assert "law" in f, code  # у каждого фактора поле есть, у R4–R6 — пустое
        assert f["law"] == [{"article": r["article"], "parts": r["parts"]} for r in law.REFS.get(code, [])], code
    assert {r["article"] for r in FACTORS["R1"]["law"]} == {"7", "8", "18"}
    assert {r["article"] for r in FACTORS["R2"]["law"]} == {"7", "8", "18"}
    assert FACTORS["R3"]["law"] == [{"article": "23", "parts": []}]
    assert [FACTORS[c]["law"] for c in ("R4", "R5", "R6")] == [[], [], []]
    assert set(law.REFS) <= set(FACTORS) | DOCS and DOCS <= set(law.REFS)
    for code, rs in law.REFS.items():
        for r in rs:
            a = law.ARTICLES[r["article"]]  # статья есть в базе
            assert set(r["parts"]) <= {e["part"] for e in a["excerpts"]}, (code, r)  # пункт есть в выдержках
            assert r["why"].strip(), code


def test_basis_text():
    assert law.basis_text("R1") == ("Основание: ст. 7 п. 1, 2; ст. 8 п. 6; ст. 18 п. 1 Закона РК «О долевом участии "
                                    "в жилищном строительстве»")
    assert law.basis_text("R3") == "Основание: ст. 23 Закона РК «О долевом участии в жилищном строительстве»"
    assert law.basis_text("R5") == "" and law.basis_text("нет такого") == ""
    assert law.ref_text({"article": "18", "parts": ["1", "8"]}) == "ст. 18 п. 1, 8"
    assert [law.ref_text(r) for r in law.refs("guarantee")] == ["ст. 8 п. 5, 6"]


def test_risk_model_api_has_law(client):
    m = client.get("/api/risk/model").json()
    assert {f["code"]: f["law"] for f in m["factors"]} == {c: f["law"] for c, f in FACTORS.items()}


def _text(x) -> str:
    from reportlab.platypus import KeepTogether, Paragraph, Table

    if isinstance(x, (list, tuple)):
        return "\n".join(_text(i) for i in x)
    if isinstance(x, Paragraph):
        return x.getPlainText()
    if isinstance(x, Table):
        return _text(x._cellvalues)
    if isinstance(x, KeepTogether):
        return _text(x._content)
    return x if isinstance(x, str) else ""


def test_pdf_has_basis_and_law_block(client):
    r = client.post("/api/signals", json={"complexId": "c2", "author": "Тест"})
    assert r.status_code == 201, r.text
    sig = client.get(f"/api/signals/{r.json()['id']}").json()
    text = _text(pdf.story(sig, datetime.now(timezone.utc)))
    assert law.basis_text("R1") in text and law.basis_text("R3") in text
    assert "Нормативная база" in text and "не юридическое заключение" in text and law.NUANCE in text
    for num in ("7", "8", "18", "23"):
        assert f"Ст. {num}. {law.ARTICLES[num]['title']}" in text and law.ARTICLES[num]["url"] in text
    assert pdf.render_signal(sig).startswith(b"%PDF")
