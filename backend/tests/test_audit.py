"""Журнал (T-16…T-19): исходный журнал сида, смена статуса с автором, комментарии, журнал ЖК, запросы сведений,
общий журнал с фильтрами, демо-аналитики, JSON-ответ на необработанную ошибку."""
import logging

import pytest

AUTHOR = "Омарова Ж."
EVENT_KEYS = {"id", "at", "who", "what", "kind", "adId", "complexId", "signalId"}


def _log(c, path, **params):
    r = c.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _sorted(events):
    return events == sorted(events, key=lambda e: (e["at"], e["id"]))


def _status(c, aid, status, **body):
    return c.patch(f"/api/ads/{aid}/status", json={"status": status, "author": AUTHOR, **body})


# ---------- исходный журнал ----------

def test_seed_journal_of_ad(client):
    log = _log(client, "/api/ads/a6/audit")
    assert all(set(e) == EVENT_KEYS and e["at"].endswith("+00:00") for e in log) and _sorted(log)
    assert [(e["kind"], e["who"]) for e in log] == [("system", "Система"), ("link", "Система"),
                                                    ("status", "Нестерова А."), ("status", "Нестерова А.")]
    # время публикации в сиде — местное (UTC+5) без смещения; явные события — со смещением +05:00
    assert [e["at"] for e in log] == ["2026-10-11T11:02:00+00:00", "2026-10-11T11:02:00+00:00",
                                      "2026-10-12T04:30:00+00:00", "2026-10-12T06:10:00+00:00"]
    assert log[0]["what"] == "Публикация собрана автоматически (Instagram)"
    assert log[1]["what"] == "Привязка к ЖК «Достык Плаза» по совпадению: уверенность 95% — факт"
    assert log[2]["what"] == "Статус: «Новая» → «На проверке» · ответственный: Нестерова А."
    assert log[3]["what"].startswith("Статус: «На проверке» → «Нарушение подтверждено». Основание: ")
    assert {(e["adId"], e["complexId"], e["signalId"]) for e in log} == {("a6", "c11", None)}

    # источник публикации и порог привязки
    assert [e["what"] for e in _log(client, "/api/ads/a4/audit")][:2] == [
        "Публикация добавлена вручную",
        "Привязка к ЖК «Тихий берег» по совпадению: уверенность 71% — гипотеза, нужна проверка аналитиком"]
    assert _log(client, "/api/ads/a10/audit")[0]["what"] == "Демо-запись (2ГИС)"
    a12 = _log(client, "/api/ads/a12/audit")
    assert [(e["kind"], e["complexId"], e["what"]) for e in a12][-1] == ("link", None, "ЖК не определён — нужна ручная привязка")
    a2 = _log(client, "/api/ads/a2/audit")
    assert [(e["kind"], e["who"]) for e in a2][-2:] == [("status", "Исаев Т."), ("comment", "Исаев Т.")]


def test_seed_journal_of_complex(client):
    log = _log(client, "/api/complexes/c4/audit")
    assert _sorted(log) and all(e["complexId"] == "c4" and e["at"].endswith("+00:00") for e in log)
    data = [e for e in log if e["kind"] == "data"]
    assert all(e["who"] == "Система" and e["adId"] is None for e in data)
    texts = [e["what"] for e in data]
    # сведения с источником и временем фиксации (дата источника, 10:00 по UTC+5 = 05:00 UTC; снимки — 12:00)
    basis = next(e for e in data if e["what"].startswith("Основание сверено"))
    assert basis["what"] == ("Основание сверено (Картотека ГАСК ВКО (демо)): гарантия ЕО — нет, разрешение МИО — нет, "
                             "банк — нет сведений")
    assert basis["at"] == "2026-10-06T05:00:00+00:00"
    assert "Заявленная готовность 55% — TikTok @shygys_tower (демо)" in texts
    assert "Подтверждённая готовность 31% — Фото-мониторинг, облёт (демо)" in texts
    appeal = next(t for t in texts if t.startswith("Поступило коллективное обращение"))
    assert appeal.startswith("Поступило коллективное обращение (31 заявитель): Коллективное обращение дольщиков")
    assert appeal.endswith("— ДЭР ВКО, вх. № ЗТ-1184 (демо)") and "Обращение проверено: подтверждено" in texts
    shots = [e for e in data if e["what"].startswith("Снимок площадки")]
    assert len(shots) == 4 and all(e["at"].endswith("T07:00:00+00:00") for e in shots)
    assert shots[-1]["what"] == "Снимок площадки (дрон): активность — нет. Изменений нет 95 дней"
    # публикации по ЖК — тоже в журнале объекта
    assert {e["kind"] for e in log} == {"data", "system", "link", "status", "comment"}
    assert {e["adId"] for e in log if e["kind"] != "data"} == {"a2"}

    assert "Обращение отклонено после проверки" in [e["what"] for e in _log(client, "/api/complexes/c1/audit")]
    assert any(e["what"].startswith("Поступило коллективное обращение (17 заявителей)")
               for e in _log(client, "/api/complexes/c11/audit"))
    req = [e for e in _log(client, "/api/complexes/c6/audit") if e["kind"] == "request"]
    assert [(e["who"], e["at"]) for e in req] == [("Омарова Ж.", "2026-10-12T05:15:00+00:00")]
    # у c14 сведения не сверены (нет источника) — в журнале только его публикация
    assert {e["kind"] for e in _log(client, "/api/complexes/c14/audit")} == {"system", "link"}


def test_seed_journal_is_deterministic(client):
    from app.seed import reset

    first = _log(client, "/api/audit", limit=500)
    reset()
    assert _log(client, "/api/audit", limit=500) == first
    assert _sorted(first) and len({e["id"] for e in first}) == len(first)


# ---------- смена статуса ----------

def test_status_change_journal(client):
    r = _status(client, "a5", "in_review", assignee=AUTHOR)
    assert r.status_code == 200 and (r.json()["status"], r.json()["assignee"]) == ("in_review", AUTHOR)
    e = _log(client, "/api/ads/a5/audit")[-1]
    assert (e["kind"], e["who"], e["adId"], e["complexId"]) == ("status", AUTHOR, "a5", "c6")
    assert e["what"] == f"Статус: «Новая» → «На проверке» · ответственный: {AUTHOR}" and e["at"].endswith("+00:00")
    n = len(_log(client, "/api/ads/a5/audit"))

    # повтор без изменений — 200, журнал не засоряется (и тот же ответственный — не изменение)
    assert _status(client, "a5", "in_review").status_code == 200
    assert _status(client, "a5", "in_review", assignee=f"  {AUTHOR} ").status_code == 200
    assert len(_log(client, "/api/ads/a5/audit")) == n
    # тот же статус с комментарием — событие пишется
    _status(client, "a5", "in_review", comment="  ждём ответ ГАСК  ")
    assert _log(client, "/api/ads/a5/audit")[-1]["what"] == "Статус: «На проверке» без изменений. Основание: ждём ответ ГАСК"
    # смена ответственного без смены статуса
    client.patch("/api/ads/a5/status", json={"status": "in_review", "author": "Исаев Т.", "assignee": "Исаев Т."})
    e = _log(client, "/api/ads/a5/audit")[-1]
    assert (e["who"], e["what"]) == ("Исаев Т.", "Статус: «На проверке» без изменений · ответственный: Исаев Т.")

    # подтверждение нарушения — только с основанием
    for bad in ({}, {"comment": ""}, {"comment": "   "}):
        r = _status(client, "a5", "confirmed", **bad)
        assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "comment", bad
    assert next(a for a in client.get("/api/ads").json() if a["id"] == "a5")["status"] == "in_review"
    r = _status(client, "a5", "confirmed", comment="Сверено с картотекой ГАСК: гарантии ЕО нет")
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["assignee"] == "Исаев Т."
    e = _log(client, "/api/ads/a5/audit")[-1]
    assert e["what"] == "Статус: «На проверке» → «Нарушение подтверждено». Основание: Сверено с картотекой ГАСК: гарантии ЕО нет"
    assert _sorted(_log(client, "/api/ads/a5/audit"))


def test_status_line_has_no_double_dot(client):
    """Инициалы ответственного кончаются точкой («Исаев Т.»): перед основанием — без «..»; тот же текст, что statusLine в демо."""
    r = client.patch("/api/ads/a1/status",
                     json={"status": "confirmed", "author": "Исаев Т.", "assignee": "Исаев Т.", "comment": "сверено"})
    assert r.status_code == 200 and (r.json()["status"], r.json()["assignee"]) == ("confirmed", "Исаев Т.")
    e = _log(client, "/api/ads/a1/audit")[-1]
    assert (e["kind"], e["who"]) == ("status", "Исаев Т.")
    assert e["what"] == "Статус: «Новая» → «Нарушение подтверждено» · ответственный: Исаев Т. Основание: сверено"
    # смена ответственного на «Омарова Ж.» без смены статуса — тоже без «..»
    _status(client, "a1", "confirmed", assignee=AUTHOR, comment="передано")
    assert _log(client, "/api/ads/a1/audit")[-1]["what"] == (
        "Статус: «Нарушение подтверждено» без изменений · ответственный: Омарова Ж. Основание: передано")
    # ответственный без точки на конце и без смены ответственного — обычная точка перед основанием
    _status(client, "a5", "in_review", assignee="Тест", comment="ок")
    assert _log(client, "/api/ads/a5/audit")[-1]["what"] == "Статус: «Новая» → «На проверке» · ответственный: Тест. Основание: ок"
    assert not [e["what"] for e in _log(client, "/api/audit", limit=500) if ".." in e["what"]]


def test_concurrent_status_decisions(client, monkeypatch):
    """Два аналитика решают по одной публикации одновременно (оба прочитали «Новая»): первое решение записывается,
    второе — 409, а не молчаливая перезапись с событием от неверного прежнего статуса."""
    import threading

    from fastapi import HTTPException

    from app import main, schemas
    from app.db import SessionLocal

    before = _log(client, "/api/ads/a1/audit")
    barrier = threading.Barrier(2, timeout=10)
    read = main._ad

    def read_then_wait(db, aid):
        ad = read(db, aid)
        barrier.wait()  # оба прочитали статус и ответственного до записи
        return ad

    bodies = [schemas.StatusIn(status="in_review", author=AUTHOR, assignee=AUTHOR),
              schemas.StatusIn(status="dismissed", author="Исаев Т.")]
    results: list = [None, None]

    def decide(i):
        db = SessionLocal()
        try:
            results[i] = main.set_status("a1", bodies[i], db).status
        except Exception as e:  # noqa: BLE001 — 409 ожидаем, любое другое исключение провалит проверку ниже
            results[i] = e
        finally:
            db.close()

    with monkeypatch.context() as m:
        m.setattr(main, "_ad", read_then_wait)
        threads = [threading.Thread(target=decide, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    assert not any(t.is_alive() for t in threads)

    won = [i for i, r in enumerate(results) if isinstance(r, str)]
    lost = [r for r in results if isinstance(r, HTTPException)]
    assert len(won) == 1 and len(lost) == 1 and lost[0].status_code == 409, results
    winner = bodies[won[0]]
    ad = next(a for a in client.get("/api/ads").json() if a["id"] == "a1")
    assert (ad["status"], ad["assignee"]) == (winner.status, winner.assignee)
    assert lost[0].detail.startswith(f"Публикацию уже изменили: сейчас «{main.STATUS_NAMES[winner.status]}»")
    assert "Обновите страницу" in lost[0].detail and ".." not in lost[0].detail

    log = _log(client, "/api/ads/a1/audit")
    assert log[:len(before)] == before and len(log) == len(before) + 1  # одно решение — одно событие
    assert (log[-1]["who"], log[-1]["what"].split(" · ")[0]) == (
        winner.author, f"Статус: «Новая» → «{main.STATUS_NAMES[winner.status]}»")

    # следующее решение — уже от фактического статуса
    assert _status(client, "a1", "needs_check").status_code == 200
    assert _log(client, "/api/ads/a1/audit")[-1]["what"].startswith(f"Статус: «{main.STATUS_NAMES[winner.status]}» →")


def test_status_validation_and_current_complex(client):
    # автор обязателен; пустая строка или пробелы — 422; длиннее 128 — 422; неизвестный статус — 422
    assert client.patch("/api/ads/a5/status", json={"status": "in_review"}).status_code == 422
    for bad in ("", "   ", "x" * 129):
        assert _status(client, "a5", "in_review", author=bad).status_code == 422
    assert _status(client, "a5", "bogus").status_code == 422
    assert _status(client, "a5", "in_review", assignee="x" * 129).status_code == 422
    assert _status(client, "a5", "in_review", comment="x" * 4001).status_code == 422
    assert next(a for a in client.get("/api/ads").json() if a["id"] == "a5")["status"] == "new"

    # complexId события — текущий ЖК публикации (после перепривязки), без привязки — null
    client.patch("/api/ads/a5/complex", json={"complexId": "c1", "author": AUTHOR})
    assert _log(client, "/api/ads/a5/audit")[-1]["kind"] == "link"
    _status(client, "a5", "needs_check")
    e = _log(client, "/api/ads/a5/audit")[-1]
    assert (e["complexId"], e["what"]) == ("c1", "Статус: «Новая» → «Нужна проверка»")
    _status(client, "a12", "dismissed", assignee="  ")  # пустой ответственный — как не указан
    e = _log(client, "/api/ads/a12/audit")[-1]
    assert (e["complexId"], e["what"]) == (None, "Статус: «Новая» → «Отклонено»")


def test_manual_ad_events(client):
    body = {"platform": "instagram", "account": "@x", "text": "Старт продаж ЖК Ульба Ривер, бронь"}
    ad = client.post("/api/ads", json={**body, "id": "m1", "author": f" {AUTHOR} "}).json()
    log = _log(client, f"/api/ads/{ad['id']}/audit")
    assert [(e["kind"], e["who"], e["complexId"]) for e in log] == [("system", AUTHOR, "c3"), ("system", "Система", "c3")]
    assert "author" not in ad
    # без автора — «Система»; повтор id — 409, а не ошибка БД; длина полей — как у столбцов БД
    assert _log(client, f"/api/ads/{client.post('/api/ads', json=body).json()['id']}/audit")[0]["who"] == "Система"
    assert client.post("/api/ads", json={**body, "id": "m1"}).status_code == 409
    assert client.post("/api/ads", json={**body, "id": "a1"}).status_code == 409
    for bad in ({"platform": "x" * 17}, {"account": "x" * 256}, {"id": "x" * 33}, {"author": "x" * 129}):
        assert client.post("/api/ads", json={**body, **bad}).status_code == 422, bad


def test_manual_ad_cannot_carry_decision(client):
    """Решение по публикации — только через PATCH /status (автор, основание, событие kind='status'). При создании статус
    решения — 422: иначе «Нарушение подтверждено» сделало бы гипотезу-привязку фактом и дало R2/R3 без следа решения."""
    risk = client.get("/api/complexes/c12/risk").json()
    body = {"id": "zz1", "platform": "instagram", "account": "@z", "text": "ЖК Green Line: бронь, первый взнос 10%"}
    for status in ("confirmed", "in_review", "dismissed"):
        for extra in ({}, {"author": AUTHOR}):
            r = client.post("/api/ads", json={**body, "status": status, **extra})
            assert r.status_code == 422 and r.json()["detail"][0]["loc"][-1] == "status", (status, extra)
    assert client.get("/api/ads/zz1/audit").status_code == 404
    assert client.get("/api/complexes/c12/risk").json() == risk
    # статусы до решения — можно; привязка по названию остаётся гипотезой
    r = client.post("/api/ads", json={**body, "status": "needs_check"})
    assert r.status_code == 201 and (r.json()["status"], r.json()["complexId"], r.json()["matchConfidence"]) == (
        "needs_check", "c12", 0.6)
    assert {e["kind"] for e in _log(client, "/api/ads/zz1/audit")} == {"system"}


# ---------- комментарии ----------

def test_comments(client):
    r = client.post("/api/ads/a2/comments", json={"author": AUTHOR, "text": "  Позвонить в отдел продаж  "})
    assert r.status_code == 201
    e = r.json()
    assert set(e) == EVENT_KEYS and isinstance(e["id"], int) and e["at"].endswith("+00:00")
    assert (e["kind"], e["who"], e["what"], e["adId"], e["complexId"], e["signalId"]) == (
        "comment", AUTHOR, "Позвонить в отдел продаж", "a2", "c4", None)
    assert _log(client, "/api/ads/a2/audit")[-1] == e
    assert _log(client, "/api/complexes/c4/audit")[-1] == e

    assert client.post("/api/ads/a2/comments", json={"author": AUTHOR, "text": "x" * 2000}).status_code == 201
    for bad in ({"author": AUTHOR, "text": ""}, {"author": AUTHOR, "text": "   "}, {"author": AUTHOR, "text": "x" * 2001},
                {"author": AUTHOR}, {"text": "ok"}, {"author": " ", "text": "ok"}):
        assert client.post("/api/ads/a2/comments", json=bad).status_code == 422, bad
    assert client.post("/api/ads/nope/comments", json={"author": AUTHOR, "text": "ok"}).status_code == 404
    # без привязки к ЖК комментарий всё равно пишется
    assert client.post("/api/ads/a12/comments", json={"author": AUTHOR, "text": "ok"}).json()["complexId"] is None


# ---------- журнал ЖК и запросы сведений ----------

def test_complex_journal_collects_everything(client):
    sid = client.post("/api/signals", json={"complexId": "c4", "author": AUTHOR}).json()["id"]
    assert client.get(f"/api/signals/{sid}/pdf", params={"author": AUTHOR}).status_code == 200
    client.post("/api/complexes/c4/requests", json={"author": AUTHOR, "topics": ["readiness"]})
    client.post("/api/ads/a2/comments", json={"author": AUTHOR, "text": "ok"})
    _status(client, "a2", "in_review")

    log = _log(client, "/api/complexes/c4/audit")
    assert _sorted(log) and all(e["complexId"] == "c4" for e in log)
    assert {e["kind"] for e in log} == {"data", "system", "link", "status", "comment", "signal", "export", "request"}
    sig = [e for e in log if e["signalId"] == sid]
    assert [(e["kind"], e["what"]) for e in sig] == [("signal", "Сигнал создан: балл 100, зона красная"), ("export", "Выгружен PDF")]
    assert [e["kind"] for e in log[-5:]] == ["signal", "export", "request", "comment", "status"]
    # журнал сигнала — та же форма
    assert _log(client, f"/api/signals/{sid}/audit") == sig
    assert client.get(f"/api/signals/{sid}").json()["journal"] == sig

    assert client.get("/api/complexes/nope/audit").status_code == 404
    assert all(e["complexId"] == "c3" for e in _log(client, "/api/complexes/c3/audit"))


def test_relink_is_in_both_complex_journals(client):
    """Перепривязка A → B: прежний ЖК теряет доказательство (R2/R3) — событие с автором и временем есть и в его журнале."""
    assert not [e for e in _log(client, "/api/complexes/c3/audit") if e["adId"] == "a3"]
    r = client.patch("/api/ads/a3/complex", json={"complexId": "c3", "author": "Исаев Т."})
    assert r.status_code == 200 and (r.json()["complexId"], r.json()["matchConfidence"]) == ("c3", 1)

    gone = [e for e in _log(client, "/api/complexes/c1/audit") if e["adId"] == "a3"][-1]
    assert (gone["kind"], gone["who"], gone["complexId"]) == ("link", "Исаев Т.", "c1")
    assert gone["what"] == "Публикация отвязана от ЖК «Ертіс Парк»: перепривязана к ЖК «Ульба Ривер»"
    came = [e for e in _log(client, "/api/complexes/c3/audit") if e["adId"] == "a3"]
    assert [(e["kind"], e["who"], e["what"]) for e in came] == [
        ("link", "Исаев Т.", "Привязка изменена: «Ертіс Парк» → «Ульба Ривер»")]
    # в журнале публикации — обе записи, по порядку
    assert _log(client, "/api/ads/a3/audit")[-2:] == [gone, came[0]]

    # подтверждение той же привязки и снятие — по-прежнему одно событие, в текущем ЖК
    n = len(_log(client, "/api/ads/a3/audit"))
    client.patch("/api/ads/a3/complex", json={"complexId": "c3", "author": AUTHOR})
    client.patch("/api/ads/a3/complex", json={"complexId": None, "author": AUTHOR})
    assert [(e["complexId"], e["who"], e["what"]) for e in _log(client, "/api/ads/a3/audit")[n:]] == [
        ("c3", AUTHOR, "Привязка к ЖК «Ульба Ривер» подтверждена аналитиком"), ("c3", AUTHOR, "Привязка к ЖК снята")]
    # привязка публикации без ЖК — одно событие
    client.patch("/api/ads/a3/complex", json={"complexId": "c6", "author": AUTHOR})
    assert _log(client, "/api/ads/a3/audit")[-1]["what"] == "Привязка к ЖК «Nord Hills» подтверждена аналитиком"
    assert len(_log(client, "/api/ads/a3/audit")) == n + 3


def test_info_requests(client):
    r = client.post("/api/complexes/c2/requests",
                    json={"author": f"  {AUTHOR}  ", "topics": ["other", "basis", "basis"], "comment": "  уточнить банк  "})
    assert r.status_code == 201
    e = r.json()
    assert (e["kind"], e["who"], e["adId"], e["complexId"], e["signalId"]) == ("request", AUTHOR, None, "c2", None)
    assert e["what"] == ("Запрос сведений: основание привлечения средств (гарантия ЕО / разрешение МИО / банк), другое. "
                         "Комментарий: уточнить банк")
    assert _log(client, "/api/complexes/c2/audit")[-1] == e

    e = client.post("/api/complexes/c2/requests",
                    json={"author": AUTHOR, "topics": ["list", "appeals", "readiness"], "comment": "  "}).json()
    assert e["what"] == "Запрос сведений: подтверждённая готовность, обращения граждан, перечень ГАСК"

    for bad in ({"author": AUTHOR, "topics": []}, {"author": AUTHOR, "topics": ["bogus"]}, {"author": AUTHOR},
                {"author": AUTHOR, "topics": ["basis", "nope"]}, {"author": "", "topics": ["basis"]},
                {"topics": ["basis"]}, {"author": AUTHOR, "topics": ["basis"], "comment": "x" * 2001},
                {"author": AUTHOR, "topics": "basis"}):
        assert client.post("/api/complexes/c2/requests", json=bad).status_code == 422, bad
    assert client.post("/api/complexes/nope/requests", json={"author": AUTHOR, "topics": ["basis"]}).status_code == 404


# ---------- общий журнал ----------

def test_audit_filters_and_limit(client):
    sid = client.post("/api/signals", json={"complexId": "c2", "author": AUTHOR}).json()["id"]
    client.post("/api/ads/a1/comments", json={"author": AUTHOR, "text": "ok"})

    everything = _log(client, "/api/audit", limit=500)
    assert _sorted(everything) and all(set(e) == EVENT_KEYS for e in everything)
    assert _log(client, "/api/audit") == everything[-200:]  # по умолчанию — последние 200
    # limit — последние N, но по возрастанию времени
    last = _log(client, "/api/audit", limit=5)
    assert last == everything[-5:] and last[-1]["kind"] == "comment" and last[-2]["kind"] == "signal"

    assert _log(client, "/api/audit", adId="a2") == _log(client, "/api/ads/a2/audit")
    assert _log(client, "/api/audit", complexId="c4", limit=500) == _log(client, "/api/complexes/c4/audit")
    assert [e["what"] for e in _log(client, "/api/audit", signalId=sid)] == ["Сигнал создан: балл 70, зона красная"]
    assert _log(client, "/api/audit", adId="nope") == []

    status = _log(client, "/api/audit", kind="status", limit=500)
    assert status and {e["kind"] for e in status} == {"status"}
    both = _log(client, "/api/audit", kind="status,comment", limit=500)
    assert {e["kind"] for e in both} == {"status", "comment"} and _sorted(both)
    r = client.get("/api/audit", params=[("kind", "status"), ("kind", "comment"), ("limit", "500")])
    assert r.json() == both
    assert _log(client, "/api/audit", kind="comment", adId="a1")[-1]["who"] == AUTHOR

    for bad in ({"kind": "bogus"}, {"kind": "status,bogus"}, {"limit": 0}, {"limit": 501}, {"limit": "x"},
                {"signalId": 0}, {"signalId": "x"}, {"signalId": 2_147_483_648}):
        assert client.get("/api/audit", params=bad).status_code == 422, bad


def test_analysts(client):
    rows = _log(client, "/api/analysts")
    assert [a["id"] for a in rows] == ["an1", "an2", "an3"]
    assert set(rows[0]) == {"id", "name", "fullName", "short", "role"}
    assert (rows[0]["name"], rows[0]["short"]) == ("Нестерова А.", "АН") and "(демо)" in rows[0]["fullName"]


# ---------- ошибки ----------

def test_unhandled_error_is_json_500(client, monkeypatch, caplog):
    from app import main

    def boom(*_a, **_kw):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(main.world, "ad_score", boom)
    with caplog.at_level(logging.ERROR, logger="saq"):
        r = client.get("/api/ads/a1/score", headers={"Origin": "http://localhost:5180"})
    assert r.status_code == 500 and r.headers["content-type"] == "application/json"
    assert r.json() == {"detail": "Внутренняя ошибка сервера. Подробности — в журнале API."}
    assert r.headers.get("access-control-allow-origin") == "*"  # фронт с другого адреса тоже увидит detail
    assert any("Необработанная ошибка" in rec.getMessage() and rec.exc_info for rec in caplog.records)
    # остальные ответы не затронуты
    assert client.get("/api/ads/nope/score").status_code == 404
    assert client.get("/api/health").json()["ok"]


def test_nul_bytes_are_422(client):
    """PostgreSQL не хранит NUL (\\x00) в тексте: в теле, пути и параметрах запроса — 422 «недопустимый символ», а не 500
    ошибки БД. На SQLite проверяется сам контракт (SQLite NUL принимает); прогон на PostgreSQL — SAQ_TEST_DATABASE_URL."""
    from app.schemas import NUL_MSG

    def rejected(r, what):
        assert r.status_code == 422, (what, r.status_code, r.text)
        assert NUL_MSG in [d["msg"] for d in r.json()["detail"]], what

    before = _log(client, "/api/audit", limit=500)
    ads = client.get("/api/ads").json()
    nul = "a\u0000b"
    bodies = [
        ("POST", "/api/ads/a2/comments", {"author": AUTHOR, "text": nul}),
        ("POST", "/api/ads/a2/comments", {"author": nul, "text": "ok"}),
        ("POST", "/api/complexes/c2/requests", {"author": "Омарова\u0000Ж.", "topics": ["basis"]}),
        ("POST", "/api/complexes/c2/requests", {"author": AUTHOR, "topics": ["basis"], "comment": nul}),
        ("PATCH", "/api/ads/a2/status", {"status": "confirmed", "author": AUTHOR, "comment": "x\u0000"}),
        ("PATCH", "/api/ads/a2/status", {"status": "in_review", "author": AUTHOR, "assignee": nul}),
        ("PATCH", "/api/ads/a2/complex", {"complexId": "c\u0000", "author": AUTHOR}),
        ("PATCH", "/api/ads/a2/complex", {"complexId": "c1", "author": nul}),
        ("POST", "/api/signals", {"complexId": "c2", "author": nul}),
        ("POST", "/api/signals", {"complexId": "c\u00002", "author": AUTHOR}),
        ("POST", "/api/ads", {"platform": "instagram", "account": "@x", "text": nul}),
        ("POST", "/api/ads", {"platform": "instagram", "account": "@x\u0000", "text": "ok"}),
        ("POST", "/api/ads", {"platform": "instagram", "account": "@x", "text": "ok", "author": nul}),
    ]
    for method, path, body in bodies:
        rejected(client.request(method, path, json=body), (path, body))
    for path in ("/api/complexes/c%00/audit", "/api/ads/a%00/audit", "/api/complexes/c%00", "/api/ads/a%00b/score",
                 "/api/audit?adId=a%00b", "/api/audit?complexId=%00", "/api/signals?complexId=c%002", "/api/ads?status=%00",
                 "/api/signals/1/pdf?author=%00", "/api/complexes/c2/risk?x=1&asOf=%00"):
        rejected(client.get(path), path)
    # ничего не записано
    assert _log(client, "/api/audit", limit=500) == before and client.get("/api/ads").json() == ads
    assert client.get("/api/signals").json() == []

    # решение по сигналу с NUL в основании — 422, сигнал остаётся нерассмотренным
    sid = client.post("/api/signals", json={"complexId": "c2", "author": AUTHOR}).json()["id"]
    for action in ("confirm", "return"):
        rejected(client.post(f"/api/signals/{sid}/{action}", json={"author": AUTHOR, "comment": nul}), action)
    assert client.get(f"/api/signals/{sid}").json()["status"] == "new"
    # обычный текст по-прежнему принимается
    assert client.post("/api/ads/a2/comments", json={"author": AUTHOR, "text": "a\\0b"}).status_code == 201


@pytest.mark.parametrize("path", ["/api/ads/a1/audit", "/api/complexes/c2/audit", "/api/audit"])
def test_audit_times_are_utc(client, path):
    assert all(e["at"].endswith("+00:00") for e in _log(client, path))
