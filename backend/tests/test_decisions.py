"""Решение аналитика по "Проверке по рекламе"(app.decisions, app.screening): "Объект сдан" и "Проверено - замечаний нет".
Проверяются маршруты(проверка входа по-русски, автор - "Система" 422, 404/409, журнал), замена статуса автоматической
оценки(check / suspect / ok → ok с вариантом и сведениями решения), автоматическое снятие новой рекламой(признак приёма
денег после решения; у "сдан" - только строящийся объект или новая очередь, обычная продажа - нет), отмена и замена
решения(стадия карточки возвращается), история, застройщик и /api/stats, риск "введён", стартовый набор(выгрузка и
загрузка с решениями, набор прежней версии без таблицы) и правила как чистые функции."""
import json
from datetime import date, timedelta
from functools import partial

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import demo_seed
from test_discovery import claude_ad, result

from app import models, screening, seed, snapshot
from app.db import SessionLocal, engine
from app.risk_model import BUILT
from app.schemas import today_local

AUTHOR = "Омарова Ж."
OTHER = "Нестерова А."
AB = "r-ab-residence-2"  # Homeportal, раздел "Без разрешения на дольщиков": без рекламы - "Требует проверки"
AB_DEV = "rd-191040019288"  # застройщик AB-Residence 2(единственный его ЖК)
ORDA = "r-orda"  # основание есть(Homeportal, разрешение МИО) - "В реестре - замечаний нет"
NOMAD = "a-nomad-t"  # ЖК по рекламе(создаётся в тесте)
TODAY = today_local()
DMY = f"{TODAY:%d.%m.%Y}"
COMMISSIONED = {"author": AUTHOR, "actNumber": "12", "actDate": "2026-09-01", "source": "ЕГКН",
                "sourceUrl": "https://egkn.kz/obj/1"}
NO_ISSUES = {"author": AUTHOR, "decision": "no_issues", "reason": "Сверено с ГАСК: продажи ведутся по разрешению МИО",
             "source": "Ответ акимата / ГАСК"}


# ---------- помощники ----------

def live(env) -> None:
    env()
    demo_seed.live()


def cx(client, cid: str) -> dict:
    r = client.get(f"/api/complexes/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def commission(client, cid: str, **body):
    return client.post(f"/api/complexes/{cid}/commissioned", json={**COMMISSIONED, **body})


def no_issues(client, cid: str, **body):
    return client.post(f"/api/complexes/{cid}/screening-decision", json={**NO_ISSUES, **body})


def cancel(client, cid: str, path: str = "screening-decision", **body):
    return client.request("DELETE", f"/api/complexes/{cid}/{path}",
                          json={"author": AUTHOR, "reason": "Ошибочная отметка", **body})


def errors(r) -> dict:
    assert r.status_code == 422, r.text
    return {".".join(str(x) for x in e["loc"][1:]): e["msg"] for e in r.json()["detail"]}


def journal(cid: str) -> list:
    with SessionLocal() as db:
        E = models.AuditEvent
        return [(e.kind, e.who, e.what) for e in db.scalars(select(E).where(E.complex_id == cid).order_by(E.id))]


def rows(cid: str) -> list:
    with SessionLocal() as db:
        D = models.ScreeningDecision
        return [(d.kind, d.status, d.prev_stage) for d in db.scalars(select(D).where(D.complex_id == cid).order_by(D.id))]


def backdate(cid: str, days: int) -> None:
    """Решение записано days дней назад: реклама "после решения" и "до него" различается по дате публикации."""
    with SessionLocal() as db:
        D = models.ScreeningDecision
        row = db.scalar(select(D).where(D.complex_id == cid, D.status == "active"))
        row.created_at = models.utcnow() - timedelta(days=days)
        db.commit()


def advertised(cid: str = NOMAD, name: str = "Nomad Test") -> str:
    """ЖК, известный только по рекламе(как Nomad, Көктем, Riverside): основания нет, стадия неизвестна."""
    with SessionLocal() as db:
        if db.get(models.Developer, "ad-test") is None:
            db.add(models.Developer(id="ad-test", name="ТОО «Реклама»", bin="", director="", registered="", objects=0,
                                    unfinished=0, phones=[], origin="advertised"))
        db.add(models.Complex(id=cid, name=name, developer_id="ad-test", address="ул. Тестовая, 1", district="",
                              lat=None, lng=None, stage="", progress=0, floors=0, flats=0, start="", planned_end="",
                              basis={"guarantee": "unknown", "mio": "unknown", "bvu": "unknown"}, cover="",
                              origin="advertised"))
        db.commit()
    return cid


def rule_ad(client, cid: str, text: str, when: str) -> str:
    """Публикация аналитика с подтверждённой привязкой и датой when, без ответа Claude - признаки по словарю правил."""
    r = client.post("/api/ads", json={"platform": "instagram", "account": "@seller", "text": text, "date": when,
                                      "complexId": cid, "linkConfirmed": True, "author": AUTHOR})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def local_minutes(days: float = 0) -> str:
    return screening._local_minutes(models.utcnow() + timedelta(days=days))


# ---------- маршруты: проверка входа, автор, ошибки ----------

def test_routes_validation_author_and_errors(client, env):
    live(env)
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    e = errors(commission(client, AB, actDate=tomorrow, sourceUrl="ftp://x", actNumber="1" * 129))
    assert e == {"actDate": "дата не может быть позже сегодняшней",
                 "sourceUrl": "нужна ссылка, начинающаяся с http:// или https://",
                 "actNumber": "String should have at most 128 characters"}
    assert errors(commission(client, AB, actDate="")) == {"actDate": "укажите дату"}
    assert errors(commission(client, AB, actDate="01.09.2026")) == {"actDate": "дата в формате ГГГГ-ММ-ДД"}
    assert errors(commission(client, AB, actDate="1999-12-31")) == {"actDate": "дата раньше 2000 года — проверьте год"}
    r = client.post(f"/api/complexes/{AB}/commissioned", json={"author": AUTHOR})
    assert set(errors(r)) == {"actDate", "source"}
    assert errors(commission(client, AB, author="Система")) == {
        "author": "Имя «Система» зарезервировано за автоматическими записями"}
    assert errors(commission(client, AB, source="ЕГКН\x00")) == {"source": "недопустимый символ NUL (\\0)"}
    assert errors(no_issues(client, AB, reason="ок ок")) == {"reason": "опишите, что проверено, — не короче 10 символов"}
    assert set(errors(no_issues(client, AB, decision="commissioned"))) == {"decision"}
    assert set(errors(no_issues(client, AB, source=""))) == {"source"}
    assert errors(cancel(client, AB, reason="нет")) == {"reason": "укажите причину отмены — не короче 5 символов"}
    # ЖК нет - 404; ключ объекта открытых данных - 404 с объяснением
    assert commission(client, "nope").status_code == 404
    r = no_issues(client, "orda")
    assert r.status_code == 404 and r.json()["detail"] != "ЖК не найден"
    # отменять нечего
    r = cancel(client, AB)
    assert r.status_code == 404 and r.json()["detail"] == "Решения аналитика по этому ЖК нет — оценка и так автоматическая"
    assert client.get(f"/api/complexes/{AB}/screening-decisions").json() == []
    assert rows(AB) == [] and not [x for x in journal(AB) if x[1] != "Система"]  # ничего не записано
    # пустые необязательные поля - "не указано"
    r = commission(client, AB, actNumber=" ", sourceUrl="", comment="")
    assert r.status_code == 200, r.text
    d = r.json()["decision"]
    assert (d["actNumber"], d["sourceUrl"], d["comment"]) == (None, None, None)
    assert r.json()["screening"]["reasons"][0]["text"].startswith("Сдан: акт от 01.09.2026, источник ЕГКН;")


# ---------- "Объект сдан" ----------

def test_commissioned_overrides_check_and_sets_stage(client, env):
    live(env)
    before = cx(client, AB)
    assert before["screening"]["status"] == "check" and before["stage"] == "" and before["decision"] is None
    assert before["screening"]["variant"] is None
    r = commission(client, AB, comment="Акт приёмки в деле объекта")
    assert r.status_code == 200, r.text
    got = r.json()
    s = got["screening"]
    assert (s["status"], s["label"], s["variant"], s["basis"]) == ("ok", "Дом сдан — замечаний нет", "commissioned",
                                                                    "confirmed")
    assert s["reasons"][0] == {"text": f"Сдан: акт № 12 от 01.09.2026, источник ЕГКН; подтвердил: {AUTHOR}, {DMY}",
                               "adId": None, "quote": None, "signal": None, "kind": "decision"}
    assert s["reasons"][1]["text"] == got["permit"]["text"] and s["reasons"][1]["kind"] is None
    assert s["reasons"][2]["text"] == "Без решения аналитика оценка — «Требует проверки»"
    assert "новая реклама со стартом продаж или строящейся очередью" in s["reasons"][3]["text"]
    assert s["counts"] == before["screening"]["counts"] and s["disclaimer"] == screening.DISCLAIMER
    d = got["decision"]
    assert {k: d[k] for k in d if k not in ("id", "at")} == {
        "kind": "commissioned", "status": "active", "label": "Дом сдан — замечаний нет", "by": AUTHOR,
        "actNumber": "12", "actDate": "2026-09-01", "source": "ЕГКН", "sourceUrl": "https://egkn.kz/obj/1",
        "reason": None, "comment": "Акт приёмки в деле объекта", "reopenedBy": None}
    assert d["at"].endswith("+00:00")
    assert got["stage"] == BUILT == "введён"
    assert journal(AB)[-1] == ("data", AUTHOR, "Объект сдан — ввод в эксплуатацию подтверждён: акт № 12 от 01.09.2026, "
                                               "источник: ЕГКН (https://egkn.kz/obj/1); стадия карточки: не указана → "
                                               "«введён». Комментарий: Акт приёмки в деле объекта")
    assert rows(AB) == [("commissioned", "active", "")]
    # то же решение повторно без комментария - без новой записи; номер с "№" не удваивается
    n = len(journal(AB))
    assert commission(client, AB).status_code == 200 and len(journal(AB)) == n and len(rows(AB)) == 1
    r = commission(client, AB, actNumber="№ 12/2026")
    assert r.json()["screening"]["reasons"][0]["text"].startswith("Сдан: акт № 12/2026 от 01.09.2026")
    assert rows(AB) == [("commissioned", "superseded", ""), ("commissioned", "active", "")]  # прежняя стадия сохранена
    # оценка видна в списке ЖК и фильтре
    assert [c["id"] for c in client.get("/api/complexes", params={"screening": "check"}).json()] == []
    assert AB in [c["id"] for c in client.get("/api/complexes", params={"screening": "ok"}).json()]


def test_commissioned_risk_built_factors(client, env):
    """"Сдан" ставит стадию "введён": факторы R1, R2, R3, R5, R6 - "объект введён"(как у любого введённого ЖК, модель
    риска не меняется); отмена возвращает прежнюю стадию и прежний риск."""
    live(env)
    assert client.post(f"/api/complexes/{AB}/basis", json={"author": AUTHOR, "doc": "guarantee", "value": "no",
                                                           "source": "ЕГКН"}).status_code == 200
    risk0 = client.get(f"/api/complexes/{AB}/risk").json()
    assert risk0["assessed"] is True
    commission(client, AB)
    risk = client.get(f"/api/complexes/{AB}/risk").json()
    states = {f["code"]: (f["state"], f["reason"]) for f in risk["factors"]}
    assert all(states[k] == ("clear", "built") for k in ("R1", "R2", "R3", "R5", "R6"))
    assert cancel(client, AB, path="commissioned").status_code == 200
    assert client.get(f"/api/complexes/{AB}/risk").json() == risk0


# ---------- "Проверено - замечаний нет" ----------

def test_no_issues_overrides_suspect_and_ok(client, env):
    live(env)
    claude_ad("Старт продаж AB-Residence 2! Бронь 200 000 ₸", result(signals=[("booking", "Бронь 200 000 ₸")],
                                                                    candidates=[(AB, "high")]), days_ago=3)
    assert cx(client, AB)["screening"]["status"] == "suspect"
    r = no_issues(client, AB, sourceUrl="https://gask.kz/answer/7", comment="Вх. № 15-02/311")
    assert r.status_code == 200, r.text
    s, d = r.json()["screening"], r.json()["decision"]
    assert (s["status"], s["label"], s["variant"], s["basis"]) == ("ok", "Проверено — замечаний нет", "analyst", "confirmed")
    assert s["reasons"][0] == {"text": f"Проверено: Сверено с ГАСК: продажи ведутся по разрешению МИО — источник Ответ "
                                       f"акимата / ГАСК; проверил: {AUTHOR}, {DMY}",
                               "adId": None, "quote": None, "signal": None, "kind": "decision"}
    assert s["reasons"][2]["text"] == "Без решения аналитика оценка — «Подозрение: продажа без разрешения»"
    assert s["counts"]["concerning"] == 1  # счётчики рекламы - как у автоматической оценки
    assert (d["kind"], d["status"], d["reason"], d["actDate"]) == ("no_issues", "active", NO_ISSUES["reason"], None)
    assert cx(client, AB)["stage"] == ""  # стадию "проверено" не меняет
    assert journal(AB)[-1] == ("data", AUTHOR, "Проверка по рекламе: проверено — замечаний нет. Что проверено: Сверено с "
                                               "ГАСК: продажи ведутся по разрешению МИО; источник: Ответ акимата / ГАСК "
                                               "(https://gask.kz/answer/7). Комментарий: Вх. № 15-02/311")
    # у ЖК в реестре(ok) решение тоже можно записать - вариант "проверено"
    r = no_issues(client, ORDA)
    assert r.json()["screening"]["variant"] == "analyst" and r.json()["screening"]["inRegistry"] is True


# ---------- снятие решения новой рекламой ----------

def test_no_issues_reopened_by_new_concerning_ad(client, env):
    live(env)
    no_issues(client, AB)
    backdate(AB, 5)
    # реклама до решения - не снимает
    claude_ad("AB-Residence 2 — бронь", result(signals=[("booking", "бронь")], candidates=[(AB, "high")]), days_ago=7)
    assert cx(client, AB)["decision"]["status"] == "active"
    # после решения, но без признака приёма денег(продажа, цена) - не снимает
    claude_ad("AB-Residence 2 — продажа квартир от 250 000 ₸/м²",
              result(signals=[("sale", "продажа квартир"), ("price", "от 250 000 ₸/м²")], candidates=[(AB, "high")]),
              days_ago=2)
    got = cx(client, AB)
    assert got["decision"]["status"] == "active" and got["screening"]["variant"] == "analyst"
    # после решения с бронью - решение снято, оценка автоматическая(подозрение), первая причина - новая реклама
    aid = claude_ad("AB-Residence 2: бронь квартиры 100 000 ₸, сдача в 2028",
                    result(signals=[("booking", "бронь квартиры 100 000 ₸")], candidates=[(AB, "high")],
                           stage=("under_construction", "сдача в 2028")), days_ago=1)
    got = cx(client, AB)
    s, d = got["screening"], got["decision"]
    assert (s["status"], s["variant"], s["label"]) == ("suspect", None, "Подозрение: продажа без разрешения")
    with SessionLocal() as db:
        ad_date = db.get(models.Ad, aid).date
    since = (TODAY - timedelta(days=5)).strftime("%d.%m.%Y")
    assert s["reasons"][0] == {
        "text": f"Новая реклама после решения аналитика ({since}): {screening._fmt_day(ad_date)} (Instagram, @seller.uka) — "
                "бронь «бронь квартиры 100 000 ₸»; решение «Проверено — замечаний нет» снято автоматически, оценка — "
                "автоматическая",
        "adId": aid, "quote": "бронь квартиры 100 000 ₸", "signal": "booking", "kind": "decision"}
    assert s["reasons"][1]["text"] == got["permit"]["text"]
    assert d["status"] == "reopened" and d["reopenedBy"] == {"adId": aid, "date": ad_date, "signal": "booking",
                                                             "quote": "бронь квартиры 100 000 ₸"}
    assert rows(AB) == [("no_issues", "active", None)]  # в БД решение остаётся - это история
    st = client.get("/api/stats").json()["screening"]
    assert st["decisions"] == {"commissioned": 0, "analyst": 0, "reopened": 1} and st["complexes"]["suspect"] == 1
    # аналитик решил по публикации: "это не предложение внести деньги" - решение снова действует
    r = client.post(f"/api/ads/{aid}/terms-review", json={"decision": "rejected", "author": AUTHOR})
    assert r.status_code == 200, r.text
    assert cx(client, AB)["decision"]["status"] == "active"
    # отклонённая публикация не учитывается вовсе
    aid2 = claude_ad("AB-Residence 2 — рассрочка 0%", result(signals=[("installment", "рассрочка 0%")],
                                                            candidates=[(AB, "high")]), days_ago=0)
    assert cx(client, AB)["decision"]["reopenedBy"]["adId"] == aid2
    client.patch(f"/api/ads/{aid2}/status", json={"status": "dismissed", "author": AUTHOR})
    assert cx(client, AB)["decision"]["status"] == "active"
    # новое решение после рекламы снова действует: снятие считается от минуты нового решения
    aid3 = claude_ad("AB-Residence 2 — первоначальный взнос 10%", result(signals=[("down_payment", "взнос 10%")],
                                                                        candidates=[(AB, "high")]), days_ago=1)
    assert cx(client, AB)["decision"]["reopenedBy"]["adId"] == aid3
    r = no_issues(client, AB)  # то же решение, но прежнее снято рекламой - записывается заново
    got = r.json()
    assert got["decision"]["status"] == "active" and got["screening"]["variant"] == "analyst"
    assert rows(AB) == [("no_issues", "superseded", None), ("no_issues", "active", None)]


def test_commissioned_reopens_only_for_construction_or_new_queue(client, env):
    """Сданный дом: продажа квартир(в том числе с рассрочкой) решение не снимает; старт продаж, "строится" или новая
    очередь в новой рекламе с признаком приёма денег - снимает."""
    live(env)
    cid = advertised()
    claude_ad("Nomad Test — дом сдан, ключи сразу! Рассрочка 0%",
              result(signals=[("installment", "Рассрочка 0%")], candidates=[(cid, "high")],
                     stage=("completed", "дом сдан, ключи сразу")), days_ago=10)
    s = cx(client, cid)["screening"]
    assert s["status"] == "check" and any("сданном доме законна" in x["text"] for x in s["reasons"])
    r = commission(client, cid, actNumber="", source="2ГИС — жилой дом")
    assert r.json()["screening"]["label"] == "Дом сдан — замечаний нет"
    backdate(cid, 5)
    # после решения: рассрочка в сданном доме, обычная продажа - решение действует
    claude_ad("Nomad Test: квартиры в сданном доме, рассрочка 0% на 12 месяцев",
              result(signals=[("installment", "рассрочка 0% на 12 месяцев")], candidates=[(cid, "high")],
                     stage=("completed", "квартиры в сданном доме")), days_ago=2)
    claude_ad("Nomad Test — продажа квартир", result(signals=[("sale", "продажа квартир")], candidates=[(cid, "medium")]),
              days_ago=1)
    rule_ad(client, cid, "Продажа квартир в ЖК Nomad Test. Рассрочка без переплат", local_minutes(-1))
    got = cx(client, cid)
    assert got["decision"]["status"] == "active" and got["screening"]["variant"] == "commissioned"
    # "строится" по словам рекламы(без ответа Claude - признаки по словарю правил): бронь в строящейся очереди - снимает
    aid = rule_ad(client, cid, "Nomad Test: строится новая очередь! Бронь квартиры 50 000 ₸", local_minutes(-0.5))
    got = cx(client, cid)
    s, d = got["screening"], got["decision"]
    assert d["status"] == "reopened" and d["reopenedBy"]["adId"] == aid and d["reopenedBy"]["signal"] == "booking"
    # карточка "введён" - продажа без основания не подозрение, а "требует проверки"; причина - новая реклама
    assert (s["status"], s["variant"]) == ("check", None)
    assert s["reasons"][0]["kind"] == "decision" and "Новая реклама после решения аналитика" in s["reasons"][0]["text"]
    assert "строится — «Nomad Test: строится новая очередь!»" in s["reasons"][0]["text"]
    assert got["stage"] == BUILT  # стадия остаётся: акт ввода не отменён
    client.patch(f"/api/ads/{aid}/status", json={"status": "dismissed", "author": AUTHOR})
    assert cx(client, cid)["decision"]["status"] == "active"
    # старт продаж(Claude) - снимает, даже если стадия по рекламе не указана
    aid = claude_ad("Nomad Test — старт продаж! Первоначальный взнос 20%",
                    result(signals=[("presale", "старт продаж"), ("down_payment", "Первоначальный взнос 20%")],
                           candidates=[(cid, "high")]), days_ago=0)
    d = cx(client, cid)["decision"]
    assert (d["status"], d["reopenedBy"]["adId"], d["reopenedBy"]["signal"], d["reopenedBy"]["quote"]) == (
        "reopened", aid, "presale", "старт продаж")


# ---------- отмена и замена ----------

def test_cancel_supersede_history_and_conflicts(client, env):
    live(env)
    commission(client, AB)
    # отмена "проверено" через маршрут "сдан" невозможна - и наоборот любое решение отменяет общий маршрут
    r = no_issues(client, AB)
    assert r.status_code == 200 and r.json()["stage"] == ""  # "проверено" вместо "сдан" - стадия прежняя
    assert rows(AB) == [("commissioned", "superseded", ""), ("no_issues", "active", None)]
    assert "прежнее решение «Дом сдан — замечаний нет» от" in journal(AB)[-1][2]
    assert "стадия карточки: «введён» → не указана" in journal(AB)[-1][2]
    r = cancel(client, AB, path="commissioned")
    assert r.status_code == 409 and "Проверено — замечаний нет" in r.json()["detail"]
    r = cancel(client, AB, author=OTHER, reason="Сведения ГАСК устарели")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["decision"] is None and got["screening"]["status"] == "check" and got["screening"]["variant"] is None
    assert got["screening"]["reasons"][0]["text"] == got["permit"]["text"]
    assert journal(AB)[-1] == ("data", OTHER, f"Решение аналитика отменено: «Проверено — замечаний нет» от {DMY} "
                                              f"({AUTHOR}). Причина: Сведения ГАСК устарели. Оценка — автоматическая")
    assert cancel(client, AB).status_code == 404
    # "сдан" → отмена через свой маршрут: стадия возвращается
    commission(client, AB)
    r = cancel(client, AB, path="commissioned", reason="Акт относится к другой очереди")
    assert r.status_code == 200 and r.json()["stage"] == "" and r.json()["decision"] is None
    assert journal(AB)[-1][2].endswith("Оценка — автоматическая; стадия карточки: «введён» → не указана")
    hist = client.get(f"/api/complexes/{AB}/screening-decisions").json()
    assert [(h["kind"], h["status"]) for h in hist] == [("commissioned", "cancelled"), ("no_issues", "cancelled"),
                                                        ("commissioned", "superseded")]
    assert (hist[0]["closedBy"], hist[0]["closeReason"], hist[0]["createdBy"]) == (
        AUTHOR, "Акт относится к другой очереди", AUTHOR)
    assert hist[0]["closedAt"].endswith("+00:00") and hist[0]["actDate"] == "2026-09-01"
    # ЖК, который уже был "введён" до отметки: отмена оставляет "введён"
    with SessionLocal() as db:
        db.get(models.Complex, ORDA).stage = BUILT
        db.commit()
    commission(client, ORDA)
    assert rows(ORDA) == [("commissioned", "active", BUILT)]
    assert cancel(client, ORDA).json()["stage"] == BUILT
    # снятое рекламой решение тоже отменяется - в журнале это видно
    no_issues(client, AB)
    claude_ad("AB-Residence 2 — бронь", result(signals=[("booking", "бронь")], candidates=[(AB, "high")]), days_ago=0)
    assert cx(client, AB)["decision"]["status"] == "reopened"
    assert cancel(client, AB).status_code == 200
    assert ", уже снято новой рекламой)" in journal(AB)[-1][2]


def test_one_active_decision_per_complex(client):
    """Действующее решение - одно на ЖК: вторая строка active - ошибка БД(частичный уникальный индекс)."""
    with SessionLocal() as db:
        for _ in range(2):
            db.add(models.ScreeningDecision(complex_id="c2", kind="no_issues", status="active", source="X",
                                            created_by=AUTHOR, created_at=models.utcnow()))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add_all([models.ScreeningDecision(complex_id="c2", kind="no_issues", status=st, source="X", created_by=AUTHOR,
                                             created_at=models.utcnow()) for st in ("cancelled", "cancelled", "active")])
        db.commit()


# ---------- застройщик и сводка ----------

def test_developer_rollup_and_stats(client, env):
    live(env)
    st0 = client.get("/api/stats").json()["screening"]
    assert st0["complexes"] == {"ok": 14, "check": 1, "suspect": 0, "unknown": 0}
    assert st0["decisions"] == {"commissioned": 0, "analyst": 0, "reopened": 0}
    dev = lambda did: next(d for d in client.get("/api/developers").json() if d["id"] == did)["screening"]
    assert dev(AB_DEV)["status"] == "check"
    commission(client, AB)
    d = dev(AB_DEV)
    assert (d["status"], d["label"]) == ("ok", "Все ЖК без замечаний — в реестре, сданы или проверены аналитиком")
    assert d["reasons"] == [{"text": "Все ЖК застройщика (1) без замечаний: дом сдан — 1", "adId": None, "quote": None,
                             "signal": None, "kind": None}]
    assert d["basis"] == "confirmed" and d["counts"]["ok"] == 1
    st = client.get("/api/stats").json()["screening"]
    assert st["complexes"] == {"ok": 15, "check": 0, "suspect": 0, "unknown": 0}
    assert st["developers"]["ok"] == st0["developers"]["ok"] + 1 and st["developers"]["check"] == 0
    assert st["decisions"] == {"commissioned": 1, "analyst": 0, "reopened": 0}
    # застройщик без решений - прежняя подпись
    orda_dev = cx(client, ORDA)["developerId"]
    assert dev(orda_dev)["label"] == "Все ЖК в реестре — замечаний нет"
    no_issues(client, ORDA)
    d = dev(orda_dev)
    assert d["label"] == screening.DEV_OK_DECIDED and "проверено аналитиком — 1" in d["reasons"][0]["text"]
    assert client.get("/api/stats").json()["screening"]["decisions"] == {"commissioned": 1, "analyst": 1, "reopened": 0}


# ---------- стартовый набор ----------

def test_snapshot_export_import_keeps_decisions(client, monkeypatch, tmp_path):
    commission(client, "c2", comment="Акт в деле")
    no_issues(client, "c4")
    cancel(client, "c4")
    out = tmp_path / "snap"
    man = snapshot.export(engine, out, views=False)
    assert man["tables"]["screening_decisions"]["rows"] == 2
    data = json.loads((out / "screening_decisions.json").read_text(encoding="utf-8"))
    assert [(r["complex_id"], r["kind"], r["status"]) for r in data] == [("c2", "commissioned", "active"),
                                                                         ("c4", "no_issues", "cancelled")]
    assert next(c for c in json.loads((out / "complexes.json").read_text(encoding="utf-8")) if c["id"] == "c2")["stage"] == BUILT
    before = cx(client, "c2")
    monkeypatch.setattr(seed, "START", partial(seed.start, src=out, files=False))
    seed.reset()
    got = cx(client, "c2")
    assert got["decision"] == before["decision"] and got["screening"] == before["screening"]
    assert got["screening"]["variant"] == "commissioned" and got["stage"] == BUILT
    assert [h["status"] for h in client.get("/api/complexes/c4/screening-decisions").json()] == ["cancelled"]
    # новые решения после загрузки получают id за максимальным
    assert no_issues(client, "c6").status_code == 200
    assert client.get("/api/complexes/c6/screening-decisions").json()[0]["id"] == 3
    # набор прежней версии схемы(без таблицы решений) загружается: решений нет
    man = json.loads((out / snapshot.MANIFEST).read_text(encoding="utf-8"))
    man["schemaVersion"] = "2026.10-verification-1"
    del man["tables"]["screening_decisions"]
    (out / "screening_decisions.json").unlink()
    (out / snapshot.MANIFEST).write_text(snapshot.dump(man), encoding="utf-8")
    seed.reset()
    assert cx(client, "c2")["decision"] is None
    # а набор текущей версии без таблицы - неполный: отказ
    man["schemaVersion"] = models.SCHEMA_VERSION
    (out / snapshot.MANIFEST).write_text(snapshot.dump(man), encoding="utf-8")
    seed._drop_app_tables(engine)
    models.Base.metadata.create_all(engine)
    with SessionLocal() as db, pytest.raises(snapshot.SnapshotError, match="нет таблицы screening_decisions"):
        snapshot.load(db, out)
    monkeypatch.setattr(seed, "START", demo_seed.start)
    seed.reset()


# ---------- правила(чистые функции) ----------

def _facts(aid: str, day: str, *, concerning=(), stage=None, stage_quote="", building=()):
    return screening.AdFacts(id=aid, date=day, day=date.fromisoformat(day[:10]), platform="instagram", account="@x",
                             source="parser", status="new", complex_id=None, confirmed=False, rejected=frozenset(),
                             terms_rejected=False, concerning=list(concerning), stage=stage, stage_quote=stage_quote,
                             building=list(building))


def test_rules_pure_functions():
    assert screening.after("2026-10-19T10:05", "2026-10-19T10:05") is True  # та же минута - после
    assert screening.after("2026-10-19T10:04", "2026-10-19T10:05") is False
    assert screening.after("2026-10-19", "2026-10-19T10:05") is False  # дата без времени - со следующего дня
    assert screening.after("2026-10-20", "2026-10-19T10:05") is True
    assert screening.after("", "2026-10-19T10:05") is False
    assert screening.act_text("12", "2026-09-01") == "акт № 12 от 01.09.2026"
    assert screening.act_text("№12", "2026-09-01") == "акт №12 от 01.09.2026"
    assert screening.act_text(None, "2026-09-01") == "акт от 01.09.2026"
    since = "2026-10-10T12:00"
    booking = [("booking", "бронь")]
    old = (_facts("old", "2026-10-01T10:00", concerning=booking), "rules")
    sale = (_facts("sale", "2026-10-11T10:00"), "rules")
    inst = (_facts("inst", "2026-10-12T10:00", concerning=[("installment", "рассрочка")], stage="completed",
                    stage_quote="дом сдан"), "claude")
    build = (_facts("build", "2026-10-13T10:00", concerning=booking, building=[("строится", "строится 2 очередь")]),
             "rules")
    presale = (_facts("pre", "2026-10-14T10:00", concerning=[("booking", "бронь"), ("presale", "старт продаж")]), "claude")
    assert screening.reopen_trigger("no_issues", since, [old, sale]) is None
    assert screening.reopen_trigger("no_issues", since, [old, sale, inst])["fact"].id == "inst"
    assert screening.reopen_trigger("commissioned", since, [old, sale, inst]) is None
    t = screening.reopen_trigger("commissioned", since, [presale, inst, build])  # первая по дате
    assert (t["fact"].id, t["signal"], t["stage"]) == ("build", ("booking", "бронь"), "строится 2 очередь")
    t = screening.reopen_trigger("commissioned", since, [presale, inst])
    assert (t["fact"].id, t["signal"], t["stage"]) == ("pre", ("presale", "старт продаж"), None)
    uc = (_facts("uc", "2026-10-15T10:00", concerning=booking, stage="under_construction", stage_quote="сдача в 2028"),
          "claude")
    assert screening.reopen_trigger("commissioned", since, [uc])["stage"] == "сдача в 2028"
    # чистая функция with_decision: автоматическая оценка не меняется на месте
    auto = screening.screen({"status": "unknown", "text": "Основание: нет сведений"}, [inst], as_of=date(2026, 10, 19),
                            days=120, origin="advertised")
    d = {"id": 1, "kind": "commissioned", "status": "active", "label": screening.DECISION_LABELS["commissioned"],
         "by": AUTHOR, "at": "2026-10-10T07:00:00+00:00", "actNumber": None, "actDate": "2026-09-01", "source": "ЕГКН",
         "sourceUrl": None, "reason": None, "comment": None, "reopenedBy": None, "_since": since}
    out, view = screening.with_decision(auto, d, [inst])
    assert (out["status"], out["variant"], view["status"]) == ("ok", "commissioned", "active") and "_since" not in view
    assert auto["status"] == "check" and auto["variant"] is None
    out, view = screening.with_decision(auto, {**d, "kind": "no_issues", "reason": "Сверено с ГАСК."}, [inst])
    assert out["status"] == "check" and view["status"] == "reopened" and out["reasons"][1:] == auto["reasons"]
    assert view["reopenedBy"] == {"adId": "inst", "date": "2026-10-12T10:00", "signal": "installment", "quote": "рассрочка"}
