"""Маршрут рассмотрения сигнала: этапы из журнала, переходы и запреты, кто согласует, сроки и просрочка."""
from datetime import datetime, timedelta, timezone, UTC
from types import SimpleNamespace

import pytest

from app import route

ANALYST, LEAD, GASK = "Нестерова А.", "Омарова Ж.", "Исаев Т."  # an1, an2(руководитель в пилоте), an3


def _signal(c, cid="c2"):
    r = c.post("/api/signals", json={"complexId": cid, "author": ANALYST})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _act(c, sid, action, author, comment=None):
    return c.post(f"/api/signals/{sid}/route", json={"action": action, "author": author, "comment": comment})


# ---------- сроки ----------

@pytest.mark.parametrize("start, days, due", [
    ("2026-10-19T10:00:00+05:00", 2, "2026-10-21"),  # пн → ср
    ("2026-10-19T10:00:00+05:00", 1, "2026-10-20"),
    ("2026-10-23T17:00:00+05:00", 1, "2026-10-26"),  # пт → пн
    ("2026-10-23T17:00:00+05:00", 2, "2026-10-27"),  # пт → вт
    ("2026-10-24T12:00:00+05:00", 1, "2026-10-26"),  # сб → пн
    ("2026-10-18T21:30:00+00:00", 1, "2026-10-20"),  # вс 21:30 UTC - уже пн 02:30 по Алматы
])
def test_due_at_counts_working_days_in_almaty(start, days, due):
    d = route.due_at(datetime.fromisoformat(start), days)
    local = d.astimezone(timezone(timedelta(hours=5)))
    assert local.date().isoformat() == due and (local.hour, local.minute, local.second) == (23, 59, 59)
    assert d.tzinfo is not None


def test_build_stages_and_overdue_with_frozen_time():
    created = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)  # пн 10:00 Алматы
    s = SimpleNamespace(id=7, code="S-0007", status="new", created_at=created, created_by=ANALYST, decided_at=None,
                        decided_by=None, decision_comment=None)
    ev = lambda at, what, who=ANALYST: {"at": at.isoformat(), "who": who, "what": what, "kind": "signal"}
    r = route.build(s, [], now=datetime(2026, 10, 21, 18, 0, tzinfo=UTC))  # ср 23:00 - срок разбора ещё не прошёл
    assert (r["stage"], r["slaDays"], r["overdue"], r["actions"]) == ("analysis", 2, False, ["submit"])
    assert r["dueAt"] == "2026-10-21T18:59:59+00:00"
    assert route.build(s, [], now=datetime(2026, 10, 21, 19, 0, tzinfo=UTC))["overdue"] is True

    submitted = datetime(2026, 10, 22, 4, 0, tzinfo=UTC)  # чт - разбор закрыт с просрочкой
    back = datetime(2026, 10, 23, 6, 0, tzinfo=UTC)
    events = [ev(created, "Сигнал создан: балл 70, зона красная"), ev(submitted, route.SUBMIT_WHAT),
              ev(back, route._what(route.BACK_WHAT, "Нет сверки МИО"), LEAD)]
    r = route.build(s, events, now=datetime(2026, 10, 26, 10, 0, tzinfo=UTC))
    st = r["stages"]
    assert [x["stage"] for x in st] == ["analysis", "approval", "analysis"]
    assert st[0]["overdue"] is True and st[0]["outcome"] == "submitted"
    assert st[1]["overdue"] is False and st[1]["outcome"] == "returned_to_analyst" and st[1]["outcomeComment"] == "Нет сверки МИО"
    assert st[1]["dueAt"] == "2026-10-23T18:59:59+00:00"  # 1 рабочий день от чт
    # возврат - разбор заново: срок от пт → вт, пн 15:00 ещё в срок
    assert st[2]["startedBy"] == LEAD and st[2]["comment"] == "Нет сверки МИО"
    assert (r["stage"], r["dueAt"], r["overdue"]) == ("analysis", "2026-10-27T18:59:59+00:00", False)

    s.status, s.decided_at, s.decided_by, s.decision_comment = "confirmed", back + timedelta(days=6), LEAD, "Ок"
    r = route.build(s, events, now=back + timedelta(days=30))
    assert (r["stage"], r["dueAt"], r["overdue"], r["actions"]) == ("confirmed", None, False, [])
    assert r["stages"][-2]["outcome"] == "confirmed" and r["stages"][-2]["overdue"] is True  # закрыт после срока


def test_build_accepts_naive_event_times():
    """Наивное время события(datetime из SQLite или ISO-строка без смещения) - UTC: раньше строка без смещения давала
    наивный datetime, и сравнение со сроком падало(TypeError)."""
    created = datetime(2026, 10, 19, 5, 0)  # наивное, как из SQLite
    s = SimpleNamespace(id=1, code="S-0001", status="new", created_at=created, created_by=ANALYST, decided_at=None,
                        decided_by=None, decision_comment=None)
    events = [{"at": "2026-10-19T06:00:00", "who": ANALYST, "what": route.SUBMIT_WHAT, "kind": "signal"}]
    r = route.build(s, events, now=datetime(2026, 10, 19, 7, 0, tzinfo=UTC))
    assert (r["stage"], r["since"], r["overdue"]) == ("approval", "2026-10-19T06:00:00+00:00", False)
    assert r["stages"][0]["startedAt"] == "2026-10-19T05:00:00+00:00"


# ---------- переходы через API ----------

def test_full_route_submit_return_submit_approve(client):
    sid = _signal(client)
    r = client.get(f"/api/signals/{sid}/route").json()
    assert (r["stage"], r["stageLabel"], r["actions"], r["sla"]) == ("analysis", "Разбор аналитиком", ["submit"],
                                                                     {"analysis": 2, "approval": 1})

    r = _act(client, sid, "submit", ANALYST, "Сверено с Homeportal")
    assert r.status_code == 200, r.text
    r = r.json()
    assert (r["stage"], r["stageLabel"], r["slaDays"], r["actions"]) == ("approval", "На согласовании у руководителя", 1,
                                                                         ["approve", "return"])

    r = _act(client, sid, "return", LEAD, "Нужна сверка разрешения МИО").json()
    assert r["stage"] == "analysis" and r["stages"][-1]["comment"] == "Нужна сверка разрешения МИО"
    assert client.get(f"/api/signals/{sid}").json()["status"] == "new"  # возврат аналитику - не "на доработку"

    assert _act(client, sid, "submit", ANALYST).status_code == 200
    r = _act(client, sid, "approve", LEAD, "Основание не подтверждено ни ЕО, ни МИО")
    assert r.status_code == 200, r.text
    r = r.json()
    assert (r["stage"], r["status"], r["actions"]) == ("confirmed", "confirmed", [])
    assert [x["stage"] for x in r["stages"]] == ["analysis", "approval", "analysis", "approval", "confirmed"]

    s = client.get(f"/api/signals/{sid}").json()  # согласование - та же логика, что POST /confirm
    assert (s["status"], s["decidedBy"], s["decisionComment"]) == ("confirmed", LEAD, "Основание не подтверждено ни ЕО, ни МИО")
    whats = [(e["who"], e["what"]) for e in s["journal"]]
    assert whats[1:] == [
        (ANALYST, f"{route.SUBMIT_WHAT}. Комментарий: Сверено с Homeportal"),
        (LEAD, f"{route.BACK_WHAT}. Комментарий: Нужна сверка разрешения МИО"),
        (ANALYST, route.SUBMIT_WHAT),
        (LEAD, f"{route.APPROVE_WHAT}. Основание не подтверждено ни ЕО, ни МИО"),
    ]
    assert all(e["kind"] == "signal" and e["signalId"] == sid and e["complexId"] == "c2" for e in s["journal"])


def test_forbidden_transitions_409(client):
    sid = _signal(client)
    assert _act(client, sid, "approve", LEAD, "рано").status_code == 409  # ещё не передан
    assert _act(client, sid, "return", LEAD, "рано").status_code == 409
    assert _act(client, sid, "submit", ANALYST).status_code == 200
    r = _act(client, sid, "submit", ANALYST)
    assert r.status_code == 409 and "На согласовании у руководителя" in r.json()["detail"]
    assert _act(client, sid, "approve", LEAD).status_code == 200  # без комментария - "Согласовано без замечаний"
    assert client.get(f"/api/signals/{sid}").json()["decisionComment"] == route.APPROVE_DEFAULT
    for action, who in (("submit", ANALYST), ("approve", LEAD), ("return", LEAD)):
        r = _act(client, sid, action, who, "ещё раз")
        assert r.status_code == 409 and "уже рассмотрен" in r.json()["detail"], action
    # прежние решения по-прежнему работают и закрывают маршрут
    sid2 = _signal(client, "c4")
    assert client.post(f"/api/signals/{sid2}/return", json={"author": ANALYST, "comment": "Нет данных"}).status_code == 200
    assert client.get(f"/api/signals/{sid2}/route").json()["stage"] == "returned"
    assert _act(client, sid2, "submit", ANALYST).status_code == 409


def test_only_lead_approves_403(client):
    sid = _signal(client)
    _act(client, sid, "submit", ANALYST)
    for who in (ANALYST, GASK, "Посторонний"):
        assert _act(client, sid, "approve", who, "согласую").status_code == 403, who
        assert _act(client, sid, "return", who, "верну").status_code == 403, who
    assert client.get(f"/api/signals/{sid}/route").json()["stage"] == "approval"
    assert client.get(f"/api/signals/{sid}").json()["status"] == "new"


def test_validation_and_404(client):
    sid = _signal(client)
    _act(client, sid, "submit", ANALYST)
    assert _act(client, sid, "return", LEAD).status_code == 422  # возврат - только с комментарием
    assert _act(client, sid, "return", LEAD, "   ").status_code == 422
    assert _act(client, sid, "archive", LEAD).status_code == 422
    assert _act(client, sid, "submit", "Система").status_code == 422
    assert _act(client, 999, "submit", ANALYST).status_code == 404
    assert client.get("/api/signals/999/route").status_code == 404


def test_routes_list_overdue_with_frozen_clock(client, monkeypatch):
    s1, s2 = _signal(client), _signal(client, "c4")
    _act(client, s2, "submit", ANALYST)
    rows = {r["signalId"]: r for r in client.get("/api/signals/routes").json()}
    assert set(rows) == {s1, s2}
    assert (rows[s1]["stage"], rows[s1]["slaDays"], rows[s1]["overdue"]) == ("analysis", 2, False)
    assert (rows[s2]["stage"], rows[s2]["slaDays"], rows[s2]["overdue"]) == ("approval", 1, False)
    assert set(rows[s1]) == {"signalId", "code", "status", "stage", "stageLabel", "since", "dueAt", "slaDays", "overdue"}

    later = datetime.now(UTC) + timedelta(days=10)  # через 10 календарных дней оба срока прошли
    monkeypatch.setattr(route, "clock", lambda: later)
    rows = {r["signalId"]: r for r in client.get("/api/signals/routes").json()}
    assert rows[s1]["overdue"] and rows[s2]["overdue"]
    full = client.get(f"/api/signals/{s2}/route").json()
    assert full["overdue"] and full["now"] == later.isoformat(timespec="seconds")
    # решённый сигнал без срока и без просрочки
    client.post(f"/api/signals/{s1}/confirm", json={"author": LEAD, "comment": "Ок"})
    rows = {r["signalId"]: r for r in client.get("/api/signals/routes").json()}
    assert (rows[s1]["stage"], rows[s1]["dueAt"], rows[s1]["overdue"]) == ("confirmed", None, False)
