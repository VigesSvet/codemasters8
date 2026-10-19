"""Сверка сведений аналитиком (app.verification): основание по документам, перечень ГАСК, готовность, обращения и
«обращений нет — сверено», снимки площадки. Проверяются проверка входа (по-русски), автор («Система» — 422), события
журнала, влияние на риск (assessed, R1, R4, R5, R6) и на «Проверку по рекламе» (permit: сверка аналитика важнее открытых
данных), пределы загрузки снимков; демо-набор без действий аналитика считается как прежде (эталон сида)."""
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

import demo_seed
from conftest import png_bytes

from app import demo, golden, media, models, screening
from app.db import SessionLocal
from app.risk_model import MODEL
from app.schemas import today_local

AUTHOR = "Омарова Ж."
ORDA = "r-orda"  # Homeportal: разрешение МИО № 1383/МҚЗТ-С-331 от 13.10.2025
ASYLYM = "r-asylym"  # Homeportal: гарантия ЕО; срока сдачи у карточки нет (R6 — только по снимкам)
AB = "r-ab-residence-2"  # Homeportal, раздел «Без разрешения на дольщиков»
TODAY = today_local()


def live(env) -> None:
    """Боевой режим: аналитики, открытый реестр и карточки ЖК из него, без демо-набора (demo_seed.live_start)."""
    env()
    demo_seed.live()


def cx(client, cid: str) -> dict:
    r = client.get(f"/api/complexes/{cid}")
    assert r.status_code == 200, r.text
    return r.json()


def risk(client, cid: str) -> dict:
    return client.get(f"/api/complexes/{cid}/risk").json()


def fac(r: dict, code: str) -> dict:
    return next(f for f in r["factors"] if f["code"] == code)


def obs(r: dict, code: str) -> dict:
    return next(o for o in r["observations"] if o["code"] == code)


def journal(cid: str) -> list:
    with SessionLocal() as db:
        E = models.AuditEvent
        return [(e.kind, e.who, e.what) for e in db.scalars(select(E).where(E.complex_id == cid).order_by(E.id))]


def basis(client, cid: str, **body):
    return client.post(f"/api/complexes/{cid}/basis", json={"author": AUTHOR, **body})


def errors(r) -> dict:
    """422: поле → текст ошибки."""
    assert r.status_code == 422, r.text
    return {".".join(str(x) for x in e["loc"][1:]): e["msg"] for e in r.json()["detail"]}


# ---------- основание ----------

def test_basis_suggestion_and_one_click_confirmation(client, env):
    live(env)
    c = cx(client, ORDA)
    sg = c["basisSuggest"]["mio"]
    assert sg == {"ref": "№ 1383/МҚЗТ-С-331 от 13.10.2025", "source": "Homeportal", "sourceName": "Homeportal — карточки ЖК",
                  "url": "https://homeportal.kz/ru/projects/1329", "records": 1}
    assert set(c["basisSuggest"]) == {"mio"} and "Аналитиком не проверено" in c["basis"]["mioRef"]
    assert cx(client, "r-diamond")["basisSuggest"] is None  # запись Казреестра — без вида документа, подсказки нет
    view = client.get(f"/api/complexes/{ORDA}/basis").json()
    assert (view["checked"], view["total"]) == (0, 4)
    assert [d["suggestion"] is not None for d in view["documents"]] == [False, True, False]
    assert risk(client, ORDA)["assessed"] is False

    r = basis(client, ORDA, doc="mio", value="yes", ref=sg["ref"], source=sg["source"], sourceUrl=sg["url"])
    assert r.status_code == 200, r.text
    got = r.json()
    b = got["basis"]
    assert b["mio"] == "yes" and b["mioRef"] == sg["ref"]  # справка реестра заменена номером от аналитика
    assert b["sources"]["mio"] == {"ref": sg["ref"], "source": "Homeportal", "url": sg["url"],
                                   "checkedAt": TODAY.isoformat(), "checkedBy": AUTHOR}
    assert (b["source"], b["checkedAt"]) == ("Homeportal", TODAY.isoformat())  # общая сверка — последняя
    assert got["permit"] == {"status": "permitted", "source": "analyst", "url": sg["url"], "verified": True,
                             "registryKey": "orda", "registryStage": "under_construction",
                             "text": f"Основание сверено: разрешение МИО — есть (Homeportal, {TODAY:%d.%m.%Y})"}
    assert got["screening"]["reasons"][0]["text"] == got["permit"]["text"]
    assert journal(ORDA)[-1] == ("data", AUTHOR, f"Основание сверено: разрешение МИО — есть (№ 1383/МҚЗТ-С-331 от 13.10.2025), "
                                                 f"источник: Homeportal (https://homeportal.kz/ru/projects/1329), сверено "
                                                 f"{TODAY:%d.%m.%Y}")
    # риск карточки реестра оценивается: документ «есть» с источником и датой — проверенные сведения
    r = risk(client, ORDA)
    assert r["assessed"] is True and (fac(r, "R1")["state"], fac(r, "R1")["reason"]) == ("unknown", "mio_without_bank")
    item = next(i for i in fac(r, "R1")["items"] if i["ref"] == "mio")
    assert (item["source"], item["date"], item["url"], item["quote"]) == ("Homeportal", TODAY.isoformat(), sg["url"], sg["ref"])
    view = client.get(f"/api/complexes/{ORDA}/basis").json()
    mio = next(d for d in view["documents"] if d["doc"] == "mio")
    assert (mio["checked"], mio["checkedBy"], view["checked"]) == (True, AUTHOR, 1)
    # повтор той же сверки без комментария журнал не засоряет
    n = len(journal(ORDA))
    assert basis(client, ORDA, doc="mio", value="yes", ref=sg["ref"], source=sg["source"], sourceUrl=sg["url"]).status_code == 200
    assert len(journal(ORDA)) == n
    # основание называет источник своего документа, а не последнюю сверку другого документа
    r = basis(client, ORDA, doc="guarantee", value="no", source="ЕГКН", checkedAt="2026-10-18")
    assert r.json()["basis"]["source"] == "ЕГКН"
    assert r.json()["permit"]["text"] == f"Основание сверено: разрешение МИО — есть (Homeportal, {TODAY:%d.%m.%Y})"
    # повторный импорт реестра справку «Аналитиком не проверено» не возвращает
    with SessionLocal() as db:
        demo.import_registry(db)
        db.commit()
    assert cx(client, ORDA)["basis"]["mioRef"] == sg["ref"]


def test_basis_validation_and_author(client, env):
    live(env)
    assert errors(basis(client, ORDA, doc="mio", value="yes")) == {
        "source": "обязателен, если отмечено «есть» или «нет»: где это проверено"}
    e = errors(basis(client, ORDA, doc="permit", value="maybe", source="X"))
    assert set(e) == {"doc", "value"}
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    e = errors(basis(client, ORDA, doc="mio", value="no", source="X", checkedAt=tomorrow, sourceUrl="ftp://x"))
    assert e == {"checkedAt": "дата не может быть позже сегодняшней",
                 "sourceUrl": "нужна ссылка, начинающаяся с http:// или https://"}
    assert errors(basis(client, ORDA, doc="mio", value="no", source="X", checkedAt="19.10.2026")) == {
        "checkedAt": "дата в формате ГГГГ-ММ-ДД"}
    r = client.post(f"/api/complexes/{ORDA}/basis", json={"author": "система", "doc": "mio", "value": "unknown"})
    assert errors(r) == {"author": "Имя «Система» зарезервировано за автоматическими записями"}
    assert errors(client.post(f"/api/complexes/{ORDA}/basis", json={"doc": "mio", "value": "unknown"})) == {
        "author": "Field required"}
    assert errors(basis(client, ORDA, doc="mio", value="yes", source="X\x00")) == {"source": "недопустимый символ NUL (\\0)"}
    assert basis(client, "nope", doc="mio", value="unknown").status_code == 404
    # ключ объекта открытых данных — не ЖК: 404 с объяснением
    r = basis(client, "orda", doc="mio", value="unknown")
    assert r.status_code == 404 and r.json()["detail"] != "ЖК не найден"
    # пустая дата — сегодня; явная прошлая — как указана
    r = basis(client, ORDA, doc="guarantee", value="no", source="Ответ уполномоченного органа", checkedAt="",
              comment="Гарантии нет")
    assert r.json()["basis"]["sources"]["guarantee"]["checkedAt"] == TODAY.isoformat()
    assert r.json()["basis"]["sources"]["guarantee"]["comment"] == "Гарантии нет"
    assert journal(ORDA)[-1][2].endswith(". Комментарий: Гарантии нет")
    r = basis(client, ORDA, doc="guarantee", value="no", source="ЕГКН", checkedAt="2026-10-01")
    assert r.json()["basis"]["sources"]["guarantee"]["checkedAt"] == "2026-10-01"
    assert all(e[1] != "Система" for e in journal(ORDA) if e[0] == "data" and "сверен" in e[2])


def test_basis_no_sets_r1_and_overrides_open_data(client, env):
    live(env)
    # «нет» только по МИО: документ Homeportal того же вида больше не основание; гарантия не сверена — основание не
    # установлено (у Park Line других записей нет), у Orda остаётся запись Казреестра
    r = basis(client, "r-park-line", doc="mio", value="no", source="Казреестр (Ashyq)", checkedAt="2026-10-18")
    p = r.json()["permit"]
    assert (p["status"], p["source"], p["verified"]) == ("unknown", None, False)
    assert p["text"] == ("Сверено аналитиком: разрешение МИО — нет (Казреестр (Ashyq), 18.10.2026); остальные документы не "
                         "сверены — основание не установлено")
    r = basis(client, ORDA, doc="mio", value="no", source="Казреестр (Ashyq)", checkedAt="2026-10-18")
    assert (r.json()["permit"]["status"], r.json()["permit"]["source"]) == ("permitted", "ashyq")
    r1 = fac(risk(client, ORDA), "R1")
    assert (r1["state"], r1["reason"]) == ("unknown", "unknown")
    # «нет» по гарантии ЕО и разрешению МИО — R1 и not_permitted (сверка аналитика важнее записей реестра)
    r = basis(client, ORDA, doc="guarantee", value="no", source="Homeportal", sourceUrl="https://homeportal.kz/x",
              checkedAt="2026-10-18")
    p = r.json()["permit"]
    assert (p["status"], p["source"], p["verified"], p["url"]) == ("not_permitted", "analyst", True, "https://homeportal.kz/x")
    assert p["text"] == ("Основание сверено: гарантии ЕО и разрешения МИО нет (Homeportal, 18.10.2026; Казреестр (Ashyq), "
                         "18.10.2026)")
    rk = risk(client, ORDA)
    assert rk["assessed"] is True and fac(rk, "R1")["state"] == "hit"
    assert fac(rk, "R1")["evidence"] == ("Проверкой установлено: гарантии ЕО нет (Homeportal, 18.10.2026); разрешения МИО "
                                         "нет (Казреестр (Ashyq), 18.10.2026)")
    # раздел «Без разрешения на дольщиков» уступает сверенному «есть»
    assert cx(client, AB)["permit"]["source"] == "homeportal_section"
    r = basis(client, AB, doc="guarantee", value="yes", ref="ДПГ-1", source="Homeportal")
    assert (r.json()["permit"]["status"], r.json()["permit"]["source"]) == ("permitted", "analyst")
    # «нет сведений» снимает сверку: документ снова без источника, риск снова не оценивается
    r = basis(client, AB, doc="guarantee", value="unknown")
    b = r.json()["basis"]
    assert b["guarantee"] == "unknown" and "guarantee" not in b["sources"]
    assert r.json()["permit"]["source"] == "homeportal_section" and risk(client, AB)["assessed"] is False
    assert journal(AB)[-1] == ("data", AUTHOR, "Сверка основания снята: гарантия ЕО (КЖК) — нет сведений")


# ---------- перечень ГАСК ----------

def test_published_list(client, env):
    live(env)
    assert obs(risk(client, ORDA), "O1")["state"] == "unknown"
    r = client.post(f"/api/complexes/{ORDA}/published-list", json={"author": AUTHOR, "value": "no"})
    assert errors(r) == {"source": "обязателен, если отмечено «есть» или «нет»: где это проверено"}
    r = client.post(f"/api/complexes/{ORDA}/published-list",
                    json={"author": AUTHOR, "value": "no", "source": "Instagram ГАСК ВКО",
                          "sourceUrl": "https://www.instagram.com/p/x/", "checkedAt": "2026-10-17"})
    assert r.status_code == 200
    got = r.json()
    assert got["inPublishedList"] == "no"
    assert got["publishedListCheck"] == {"value": "no", "source": "Instagram ГАСК ВКО", "url": "https://www.instagram.com/p/x/",
                                         "checkedAt": "2026-10-17", "checkedBy": AUTHOR}
    assert obs(risk(client, ORDA), "O1")["state"] == "flag"
    assert risk(client, ORDA)["assessed"] is False  # наблюдение вне балла риск не открывает
    assert journal(ORDA)[-1] == ("data", AUTHOR, "Перечень ГАСК сверен: объект отсутствует в опубликованном перечне, "
                                                 "источник: Instagram ГАСК ВКО (https://www.instagram.com/p/x/), сверено 17.10.2026")
    view = client.get(f"/api/complexes/{ORDA}/basis").json()
    assert view["publishedList"]["checkedBy"] == AUTHOR and view["checked"] == 1
    r = client.post(f"/api/complexes/{ORDA}/published-list", json={"author": AUTHOR, "value": "unknown"})
    assert (r.json()["inPublishedList"], r.json()["publishedListCheck"]) == ("unknown", None)
    assert journal(ORDA)[-1][2] == "Сверка с перечнем ГАСК снята: нет сведений"


# ---------- готовность ----------

def test_readiness(client, env):
    live(env)
    url = f"/api/complexes/{ASYLYM}/readiness"
    e = errors(client.post(url, json={"author": AUTHOR, "kind": "confirmed", "value": 120, "source": "", "date": ""}))
    assert e == {"value": "Input should be less than or equal to 100", "source": "String should have at least 1 character",
                 "date": "укажите дату"}
    assert errors(client.post(url, json={"author": AUTHOR, "kind": "planned", "value": 5, "source": "x",
                                         "date": "2026-10-01"}))["kind"]
    r = client.post(url, json={"author": AUTHOR, "kind": "declared", "value": 55, "source": "Сайт застройщика",
                               "date": "2026-10-10"})
    assert r.status_code == 200
    d = r.json()["readiness"]["declared"]
    assert (d["value"], d["source"], d["date"], d["by"]) == (55, "Сайт застройщика", "2026-10-10", AUTHOR)
    assert risk(client, ASYLYM)["assessed"] is False  # заявленная готовность — слова застройщика, не проверка
    r = client.post(url, json={"author": AUTHOR, "kind": "confirmed", "value": 31.5, "source": "Выезд на площадку",
                               "date": "2026-10-12", "comment": "Акт осмотра № 7"})
    got = r.json()
    assert got["readiness"]["confirmed"]["value"] == 31.5 and got["progress"] == 32
    rk = risk(client, ASYLYM)
    r5 = fac(rk, "R5")
    assert rk["assessed"] is True and (r5["state"], r5["reason"]) == ("hit", "mismatch")
    assert r5["evidence"] == "Заявлено 55%, подтверждено 31.5%: разница 23.5 п.п. (порог 20 п.п.)"
    assert journal(ASYLYM)[-1] == ("data", AUTHOR, "Подтверждённая готовность 31.5% — Выезд на площадку, на 12.10.2026. "
                                                   "Комментарий: Акт осмотра № 7")


# ---------- обращения ----------

def test_appeals_r4_unknown_until_checked(client, env):
    live(env)
    r4 = fac(risk(client, ASYLYM), "R4")
    assert (r4["state"], r4["reason"]) == ("unknown", "unchecked")  # обращения не сверены — не «обращений нет»
    assert "не означает, что обращений нет" in r4["evidence"]
    url = f"/api/complexes/{ASYLYM}/appeals-check"
    assert errors(client.post(url, json={"author": AUTHOR, "source": " "})) == {
        "source": "String should have at least 1 character"}
    r = client.post(url, json={"author": AUTHOR, "source": "ДЭР ВКО, ответ на запрос", "checkedAt": "2026-10-16"})
    assert r.status_code == 200
    assert r.json()["appealsCheck"] == {"source": "ДЭР ВКО, ответ на запрос", "checkedAt": "2026-10-16", "checkedBy": AUTHOR}
    r4 = fac(risk(client, ASYLYM), "R4")
    assert (r4["state"], r4["evidence"]) == ("clear", "Коллективных обращений нет — сверено (ДЭР ВКО, ответ на запрос, 16.10.2026)")
    assert risk(client, ASYLYM)["assessed"] is False  # «обращений нет» само по себе риск не открывает
    assert journal(ASYLYM)[-1] == ("data", AUTHOR, "Обращений по объекту нет — сверено: источник: ДЭР ВКО, ответ на запрос, "
                                                   "сверено 16.10.2026")
    n = len(journal(ASYLYM))
    client.post(url, json={"author": AUTHOR, "source": "ДЭР ВКО, ответ на запрос", "checkedAt": "2026-10-16"})
    assert len(journal(ASYLYM)) == n


def test_appeals_add_verify_reject(client, env):
    live(env)
    url = f"/api/complexes/{ASYLYM}/appeals"
    e = errors(client.post(url, json={"author": AUTHOR, "date": "2026-10-01", "subject": "Тема", "applicants": 1,
                                      "collective": True, "source": "eOtinish"}))
    assert e == {"collective": "коллективное обращение — от двух заявителей"}
    e = errors(client.post(url, json={"author": "Система", "date": "2099-01-01", "subject": "", "applicants": 0,
                                      "source": "x"}))
    assert set(e) == {"author", "date", "subject", "applicants"}
    r = client.post(url, json={"author": AUTHOR, "date": "2026-10-05", "subject": "Взносы без ДДУ", "applicants": 12,
                               "collective": True, "source": "eOtinish № 77", "comment": "Со слов заявителей"})
    assert r.status_code == 201
    ap = r.json()
    assert (ap["status"], ap["origin"], ap["createdBy"], ap["comment"], ap["complexId"]) == (
        "pending", "analyst", AUTHOR, "Со слов заявителей", ASYLYM)
    assert journal(ASYLYM)[-1] == ("data", AUTHOR, "Внесено коллективное обращение (12 заявителей) от 05.10.2026: Взносы без "
                                                   "ДДУ — eOtinish № 77. Ожидает проверки. Комментарий: Со слов заявителей")
    assert client.get("/api/appeals", params={"complexId": ASYLYM}).json()[0]["id"] == ap["id"]
    r4 = fac(risk(client, ASYLYM), "R4")
    assert (r4["state"], r4["reason"]) == ("unknown", "pending") and risk(client, ASYLYM)["assessed"] is False
    # подтверждено — R4 и проверенные сведения
    v = client.post(f"/api/appeals/{ap['id']}/verify", json={"author": AUTHOR, "comment": "Сверено с ДЭР"})
    assert v.status_code == 200
    assert (v.json()["status"], v.json()["verifiedAt"], v.json()["decidedBy"], v.json()["decisionComment"]) == (
        "verified", TODAY.isoformat(), AUTHOR, "Сверено с ДЭР")
    rk = risk(client, ASYLYM)
    assert rk["assessed"] is True and fac(rk, "R4")["state"] == "hit" and rk["score"] == 15
    assert journal(ASYLYM)[-1] == ("data", AUTHOR, "Обращение проверено: подтверждено — Взносы без ДДУ. Комментарий: Сверено с ДЭР")
    n = len(journal(ASYLYM))
    assert client.post(f"/api/appeals/{ap['id']}/verify", json={"author": AUTHOR}).status_code == 200
    assert len(journal(ASYLYM)) == n  # повтор без комментария — без события
    # отклонено (решение изменено) — R4 больше не hit
    j = client.post(f"/api/appeals/{ap['id']}/reject", json={"author": "Нестерова А."})
    assert j.json()["status"] == "rejected"
    assert fac(risk(client, ASYLYM), "R4")["state"] == "unknown"  # обращения так и не сверены
    assert journal(ASYLYM)[-1] == ("data", "Нестерова А.", "Обращение отклонено после проверки (решение изменено) — Взносы без ДДУ")
    assert client.post("/api/appeals/nope/verify", json={"author": AUTHOR}).status_code == 404
    assert errors(client.post(f"/api/appeals/{ap['id']}/reject", json={"author": "Система"}))["author"]


def test_demo_r4_unchanged(client):
    """Демо-ЖК: пустой список обращений — «обращений нет», как прежде; сверка обращений на них тоже работает."""
    r4 = fac(risk(client, "c1"), "R4")
    assert (r4["state"], r4["reason"]) == ("clear", "none")
    r = client.post("/api/complexes/c1/appeals-check", json={"author": AUTHOR, "source": "ДЭР ВКО"})
    assert r.status_code == 200 and fac(risk(client, "c1"), "R4")["reason"] == "none"


# ---------- снимки площадки ----------

def photo(client, cid: str, data: bytes = None, name: str = "site.png", mime: str = "image/png", **fields):
    form = {"author": AUTHOR, "date": "2026-10-01", "source": "Выезд на площадку", "activity": "active", **fields}
    return client.post(f"/api/complexes/{cid}/photos", data={k: v for k, v in form.items() if v is not None},
                       files={"file": (name, data if data is not None else png_bytes(), mime)})


def test_site_photo_upload(client, env):
    live(env)
    data = png_bytes((1, 2, 3))
    r = photo(client, ASYLYM, data, activity="idle", date="2026-06-01", source="дрон", note="Кран стоит")
    assert r.status_code == 201, r.text
    p = r.json()["photos"][0]
    assert (p["date"], p["source"], p["activity"], p["note"], p["by"]) == ("2026-06-01", "дрон", "нет", "Кран стоит", AUTHOR)
    assert p["src"] == f"/api/media/{p['mediaId']}"
    got = client.get(p["src"])
    assert got.status_code == 200 and got.content == data and got.headers["content-type"] == "image/png"
    assert journal(ASYLYM)[-1] == ("data", AUTHOR, "Снимок площадки от 01.06.2026 (дрон): активность — нет. Кран стоит")
    rk = risk(client, ASYLYM)
    assert rk["assessed"] is True  # датированный снимок с источником — проверенные сведения
    assert fac(rk, "R6")["reason"] == "active"
    # второй снимок без активности через 100 дней — простой подтверждён снимками (R6)
    r = photo(client, ASYLYM, png_bytes((4, 5, 6)), activity="idle", date="2026-09-09", source="дрон")
    assert r.json()["idleDays"] == 100 and [x["date"] for x in r.json()["photos"]] == ["2026-06-01", "2026-09-09"]
    assert journal(ASYLYM)[-1][2] == "Снимок площадки от 09.09.2026 (дрон): активность — нет; простой по снимкам — 100 дн."
    r6 = fac(risk(client, ASYLYM), "R6")
    assert (r6["state"], r6["evidence"]) == ("hit", "Нет активности 100 дн. по снимкам 01.06.2026 — 09.09.2026")
    # снимок «нет сведений» серию не прерывает, снимок с активностью — снимает простой
    assert photo(client, ASYLYM, png_bytes((7, 8, 9)), activity="unknown", date="2026-09-20").json()["idleDays"] == 100
    r = photo(client, ASYLYM, png_bytes((9, 9, 9)), activity="active", date="2026-10-02")
    assert r.json()["idleDays"] == 0 and fac(risk(client, ASYLYM), "R6")["reason"] == "active"
    # уборка непривязанных скриншотов снимки площадки не трогает
    with SessionLocal() as db:
        media.purge_unattached(db, models.utcnow() + timedelta(days=3))
        assert db.get(models.MediaAsset, p["mediaId"]) is not None
    assert client.get(p["src"]).status_code == 200


def test_site_photo_validation_and_limits(client, env):
    live(env)
    e = errors(photo(client, ASYLYM, activity="busy", date="2099-01-01", author="Система", source=""))
    assert set(e) == {"author", "date", "source", "activity"}
    assert errors(photo(client, ASYLYM, author=None)) == {"author": "Field required"}
    r = photo(client, ASYLYM, b"%PDF-1.7 " + b"x" * 100, name="doc.pdf", mime="application/pdf")
    assert r.status_code == 415
    big = png_bytes()[:16] + b"\x00" * (media.UPLOAD_MAX + 10)
    r = photo(client, ASYLYM, big)
    assert (r.status_code, r.json()["detail"]) == (413, "Файл больше 10 МБ")
    assert photo(client, "nope").status_code == 404
    assert cx(client, ASYLYM)["photos"] == []  # ни один отказ снимок не записал
    # часовой лимит ручных загрузок — общий со скриншотами формы (POST /api/media)
    for i in range(media.UPLOAD_HOURLY_FILES - 1):
        assert client.post("/api/media", files={"file": ("s.png", png_bytes((i, 1, 1)), "image/png")}).status_code == 201
    assert photo(client, ASYLYM, png_bytes((250, 1, 1))).status_code == 201
    r = photo(client, ASYLYM, png_bytes((251, 1, 1)))
    assert (r.status_code, r.json()["detail"]) == (429, "Слишком много загрузок — попробуйте позже")


def test_photo_upload_limit_by_content_length(client):
    """Тело больше предела по Content-Length — 413 до разбора формы (как у POST /api/media)."""
    r = client.post("/api/complexes/c1/photos", content=b"x", headers={
        "content-type": "multipart/form-data; boundary=x", "content-length": str(media.UPLOAD_MAX + 70 * 1024)})
    assert (r.status_code, r.json()["detail"]) == (413, "Файл больше 10 МБ")


# ---------- демо-набор без действий аналитика ----------

def test_demo_risk_and_screening_unchanged(client):
    """Риск демо-ЖК по API совпадает с эталоном сида (shared/testdata/risk_golden.json); «Проверка по рекламе» демо-ЖК — прежняя
    (c4: «нет» по гарантии ЕО и МИО с источником теперь not_permitted — как и R1, статус оценки тот же)."""
    stored = json.loads(golden.GOLDEN.read_text(encoding="utf-8"))
    api = {r["complexId"]: {k: v for k, v in r.items() if k != "complexId"}
           for r in client.get("/api/risk", params={"asOf": stored["asOf"]}).json()}
    assert api == stored["seed"]["complexes"]
    cxs = {c["id"]: c for c in client.get("/api/complexes").json()}
    assert {cid: c["screening"]["status"] for cid, c in cxs.items()} == {
        "c1": "ok", "c2": "suspect", "c3": "ok", "c4": "suspect", "c5": "ok", "c6": "suspect", "c7": "ok", "c8": "ok",
        "c9": "ok", "c10": "ok", "c11": "suspect", "c12": "ok", "c13": "ok", "c14": "suspect"}
    assert all(c["basisSuggest"] is None and c["appealsCheck"] is None and c["publishedListCheck"] is None
               for c in cxs.values())
    c4 = cxs["c4"]["permit"]
    assert (c4["status"], c4["source"]) == ("not_permitted", "analyst")
    # при прежнем «нет сведений» оценка c4 была бы той же
    with SessionLocal() as db:
        r = screening.compute(db)
        facts = screening.load_facts(db)
        ads = [(f, k) for f in facts.values() for cid, k in f.targets().items() if cid == "c4"]
        old = screening.screen({**r.permits["c4"], "status": "unknown"}, ads, as_of=r.as_of, days=r.days, origin="demo")
        assert old["status"] == r.complexes["c4"]["status"]
