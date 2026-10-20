"""Сквозной сценарий README("Тестовый сценарий") через API на настоящем стартовом наборе shared/snapshot.

Путь аналитика по шагам README: обзор ЖК и риска -> карточка жёлтого ЖК и факторы -> публикация с предложением внести
деньги, её анализ и условия оплаты -> вето R2 аналитиком и обратно(балл 35 -> 10 -> 35) -> новый текст через
/api/analyze и ручное добавление публикации -> сигнал -> решение человеком -> PDF.

Без сети и без файлов набора(только строки БД): быстро и детерминированно. "Сегодня" для риска и "Проверки по
рекламе" - дата ответов офлайн-копии набора(manifest.json, api.asOf), иначе свежесть рекламы и сроки сдачи зависели бы
от дня прогона. Запуск отдельно: make e2e или pytest -m scenario.
"""
from datetime import date, datetime, time, UTC
from functools import partial

import pytest

from app import risk, screening, seed, snapshot

pytestmark = pytest.mark.scenario

MAN = snapshot.manifest(snapshot.DIR)
AS_OF = date.fromisoformat(MAN["api"]["asOf"]) if MAN else None
ROLE = "Аналитик · ДЭР ВКО"
CX = "r-ainalaiyn"  # жёлтый ЖК набора без открытого сигнала; R2 по нему даёт одна публикация
NEW_TEXT = ("Старт продаж в ЖК Сатти Сити! Бронь квартиры — 500 000 тг, первоначальный взнос от 30%, "
            "рассрочка на 2 года.")


def _frozen_now(at: datetime):
    """datetime для app.risk: now(tz) - заданный момент(дата "сегодня" для риска)."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)
    return Frozen


@pytest.fixture
def snap(client, monkeypatch):
    """Клиент API на БД, которую получает новое развёртывание: пустая БД -> стартовый набор(без файлов)."""
    if MAN is None:
        pytest.fail("нет shared/snapshot/manifest.json - сценарий идёт на стартовом наборе")
    monkeypatch.setattr(risk, "datetime", _frozen_now(datetime.combine(AS_OF, time(6, 0), tzinfo=UTC)))
    monkeypatch.setattr(screening, "today", lambda: AS_OF)
    monkeypatch.setattr(seed, "START", partial(seed.start, files=False))
    seed.reset()
    return client


def _f(r: dict, code: str) -> dict:
    return next(f for f in r["factors"] if f["code"] == code)


def _ok(r, status: int = 200) -> dict | list:
    assert r.status_code == status, (r.status_code, r.text[:500])
    return r.json()


def _risk(c, cid: str) -> dict:
    return _ok(c.get(f"/api/complexes/{cid}/risk"))


def _author(c) -> str:
    """Шаг 1. Вход: роль "Аналитик · ДЭР ВКО" - её имя становится автором действий в журнале."""
    return next(a["name"] for a in _ok(c.get("/api/analysts")) if a["role"] == ROLE)


def _overview(c) -> None:
    """Шаг 2. Обзор: 23 ЖК; жёлтая зона - 5 ЖК по 35 баллов, 16 - "риск не оценивается", красных нет."""
    cxs = _ok(c.get("/api/complexes"))
    assert len(cxs) == 23
    rows = _ok(c.get("/api/risk"))
    assessed = [r for r in rows if r["assessed"]]
    assert len(rows) - len(assessed) == 16
    assert sorted(r["score"] for r in assessed if r["zone"] == "yellow") == [35] * 5
    assert not [r for r in assessed if r["zone"] == "red"]
    yellow = _ok(c.get("/api/complexes", params={"zone": "yellow"}))
    assert len(yellow) == 5 and CX in {x["id"] for x in yellow}
    stats = _ok(c.get("/api/stats"))
    assert stats["zones"]["yellow"] == 5 and stats["zones"]["red"] == 0 and stats["notAssessed"] == 16
    statuses = [x["screening"]["status"] for x in cxs]
    assert (statuses.count("suspect"), statuses.count("check"), statuses.count("ok")) == (2, 8, 13)


def _card(c) -> str:
    """Шаги 3-4. Карточка ЖК и факторы: R2 +25 и R3 +10 с цитатой, R1 и R4-R6 - "Нужна проверка"; итог 35, жёлтая.
    Возвращает id публикации, на которую ведёт цитата R2."""
    cx = _ok(c.get(f"/api/complexes/{CX}"))
    assert cx["origin"] == "registry" and cx["developerId"]
    assert {cx["basis"][k] for k in ("guarantee", "mio", "bvu")} == {"unknown"}  # основание не проверялось
    r = _risk(c, CX)
    assert (r["score"], r["zone"], r["assessed"]) == (35, "yellow", True)
    assert [(f["code"], f["state"]) for f in r["factors"]] == [
        ("R1", "unknown"), ("R2", "hit"), ("R3", "hit"), ("R4", "unknown"), ("R5", "unknown"), ("R6", "unknown")]
    assert (_f(r, "R2")["points"], _f(r, "R3")["points"]) == (25, 10)
    assert "не считается отсутствием основания" in _f(r, "R1")["evidence"]  # неизвестное не становится R1
    [quote] = _f(r, "R2")["items"]
    assert quote["kind"] == "ad" and quote["marker"] and quote["quote"] and quote["date"] and quote["url"]
    return quote["ref"]


def _publication(c, aid: str) -> None:
    """Шаг 5. Разбор публикации: привязка - решение аналитика, условия оплаты - с дословной цитатой и уверенностью,
    анализ Claude - ответ из набора."""
    ad = _ok(c.get(f"/api/ads/{aid}"))
    assert ad["complexId"] == CX and ad["linkConfirmedBy"] and ad["termsReview"] is None
    assert ad["llm"]["status"] == "done"
    an = _ok(c.get(f"/api/ads/{aid}/analysis"))
    money = [t for t in an["terms"] if t["money"]]
    assert money and all(t["quote"] and 0 < t["confidence"] <= 1 for t in money)
    assert all(t["quote"] in (ad["text"] if t["from"] == "text" else ad["ocr"]) for t in money)  # цитата дословная
    assert an["hypotheses"][0]["complexId"] == CX
    s = _ok(c.get(f"/api/ads/{aid}/score"))
    assert _f(s, "R2")["state"] == "hit"


def _veto(c, aid: str, author: str) -> None:
    """Шаг 6. "Это не предложение денег" - балл ЖК 35 -> 10, зелёная, R3 остаётся; "Условия верны" - снова 35.
    Оба решения - в истории публикации с автором."""
    _ok(c.post(f"/api/ads/{aid}/terms-review", json={"author": author, "decision": "rejected"}))
    r = _risk(c, CX)
    assert (r["score"], r["zone"], _f(r, "R2")["state"], _f(r, "R3")["state"]) == (10, "green", "clear", "hit")
    ad = _ok(c.post(f"/api/ads/{aid}/terms-review", json={"author": author, "decision": "accepted"}))
    assert (ad["termsReview"], ad["termsReviewBy"]) == ("accepted", author)
    r = _risk(c, CX)
    assert (r["score"], r["zone"], _f(r, "R2")["state"]) == (35, "yellow", "hit")
    log = [(e["kind"], e["who"], e["what"]) for e in _ok(c.get(f"/api/ads/{aid}/audit"))]
    assert log[-2:] == [("ai", author, "Аналитик отклонил: это не предложение внести деньги"),
                        ("ai", author, "Условия оплаты (ИИ) подтверждены аналитиком")]


def _new_text(c, author: str) -> None:
    """Шаг 7. Текст, которого нет в данных: условия оплаты с цитатами и гипотеза "Satti City" по транслитерации.
    /api/analyze ничего не пишет; сохранённая публикация - гипотеза ИИ, а не факт: балла R2 ЖК она не даёт."""
    ads = _ok(c.get("/api/stats"))["ads"]
    an = _ok(c.post("/api/analyze", json={"text": NEW_TEXT}))
    terms = {t["kind"]: t for t in an["terms"]}
    assert terms["booking"]["amount"] == 500_000 and terms["booking"]["currency"] == "KZT"
    assert terms["down_payment"]["percent"] == 30 and terms["installment"]["months"] == 24
    assert all(t["money"] and t["quote"] in NEW_TEXT for t in terms.values())
    top = an["hypotheses"][0]
    assert (top["complexId"], top["complexName"]) == ("r-satti-city", "Satti City")
    assert top["confidence"] == pytest.approx(0.78) and top["reasons"][0]["feature"] == "translit"
    assert _ok(c.get("/api/stats"))["ads"] == ads

    before = _risk(c, "r-satti-city")
    ad = _ok(c.post("/api/ads", json={"platform": "instagram", "account": "@scenario", "text": NEW_TEXT,
                                      "author": author}), 201)
    assert (ad["complexId"], ad["linkConfirmedBy"]) == ("r-satti-city", None)
    after = _risk(c, "r-satti-city")
    assert _f(after, "R2")["state"] != "hit" and after["score"] == before["score"]


def _signal(c, author: str) -> dict:
    """Шаг 8. Сигнал по ЖК из шага 3 - снимок балла, факторов и доказательств; второй открытый сигнал - 409, сигнал по
    ЖК без оценки риска - 422."""
    s = _ok(c.post("/api/signals", json={"complexId": CX, "author": author}), 201)
    assert s["code"] == "S-0003" and s["status"] == "new"  # в наборе уже есть S-0001 и S-0002
    assert (s["score"], s["zone"], s["hits"]) == (35, "yellow", ["R2", "R3"])
    assert [a["id"] for a in s["evidence"]["ads"]] and s["fileName"] == "signal-S-0003.pdf"
    again = c.post("/api/signals", json={"complexId": CX, "author": author})
    assert again.status_code == 409 and again.json()["detail"]["signalId"] == s["id"]
    not_assessed = next(r["complexId"] for r in _ok(c.get("/api/risk")) if not r["assessed"])
    assert c.post("/api/signals", json={"complexId": not_assessed, "author": author}).status_code == 422
    return s


def _decision(c, sid: int, author: str) -> None:
    """Шаг 9. Решение - только с комментарием(пустой - 422), повторное - 409; автор и комментарий - в сигнале, его
    журнале и журнале карточки ЖК."""
    assert c.post(f"/api/signals/{sid}/return", json={"author": author, "comment": "  "}).status_code == 422
    comment = "Запросить у застройщика гарантию ЕО и разрешение МИО"
    s = _ok(c.post(f"/api/signals/{sid}/return", json={"author": author, "comment": comment}))
    assert (s["status"], s["decidedBy"], s["decisionComment"]) == ("returned", author, comment)
    assert c.post(f"/api/signals/{sid}/confirm", json={"author": author, "comment": "Сверено"}).status_code == 409
    what = f"Возвращён на доработку. {comment}"
    assert (s["journal"][-1]["who"], s["journal"][-1]["what"]) == (author, what)
    assert any(e["what"] == what and e["who"] == author for e in _ok(c.get(f"/api/complexes/{CX}/audit")))


def _pdf(c, s: dict, author: str) -> None:
    """Шаг 10. PDF сигнала: A4-файл signal-S-000N.pdf, событие "Выгружен PDF" в журнале сигнала."""
    r = c.get(f"/api/signals/{s['id']}/pdf", params={"author": author})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and len(r.content) > 1000
    assert f'filename="{s["fileName"]}"' in r.headers["content-disposition"]
    log = _ok(c.get(f"/api/signals/{s['id']}/audit"))
    assert (log[-1]["kind"], log[-1]["who"], log[-1]["what"]) == ("export", author, "Выгружен PDF")
    assert _ok(c.get(f"/api/signals/{s['id']}"))["exportedAt"]


def test_readme_scenario(snap):
    c = snap
    author = _author(c)
    _overview(c)
    aid = _card(c)
    _publication(c, aid)
    _veto(c, aid, author)
    _new_text(c, author)
    s = _signal(c, author)
    _decision(c, s["id"], author)
    _pdf(c, s, author)
