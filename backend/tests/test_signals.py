"""Сигнальная карточка(модуль 05): создание со снимком, решение аналитика, журнал, реестр, PDF."""

AUTHOR = "Нестерова А."


def _create(c, cid, author=AUTHOR):
    return c.post("/api/signals", json={"complexId": cid, "author": author})


def _f(s, code):
    return next(f for f in s["factors"] if f["code"] == code)


def test_create_with_evidence_snapshot(client):
    r = _create(client, "c2")
    assert r.status_code == 201
    s = r.json()
    assert (s["id"], s["code"], s["status"], s["complexId"], s["complexName"]) == (1, "S-0001", "new", "c2", "Алтай Резиденс")
    assert (s["score"], s["zone"], s["needsCheck"], s["hits"]) == (70, "red", False, ["R1", "R2", "R3"])
    assert s["createdBy"] == AUTHOR and s["createdAt"].endswith("+00:00")
    assert s["decidedAt"] is None and s["decidedBy"] is None and s["decisionComment"] is None
    assert s["fileName"] == "signal-S-0001.pdf" and s["exportedAt"] is None
    assert s["summary"] and s["modelVersion"] and len(s["observations"]) == 3

    # факторы с доказательствами: цитата публикации с площадкой и ссылкой, документы-основания
    assert [f["code"] for f in s["factors"]] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    q = _f(s, "R2")["items"][0]
    assert q["kind"] == "ad" and q["ref"] == "a1" and "Бронь" in q["quote"] and q["url"] and q["source"] == "instagram"
    assert {i["kind"] for i in _f(s, "R1")["items"]} == {"document"}

    ev = s["evidence"]
    assert ev["complex"]["id"] == "c2" and ev["complex"]["readiness"]["declared"]["value"] == 20
    assert ev["developer"]["id"] == ev["complex"]["developerId"] and ev["developer"]["bin"]
    assert [a["id"] for a in ev["ads"]] == ["a1"]
    assert ev["ads"][0]["status"] == "new" and "бронь" in ev["ads"][0]["signals"]["funds"]
    assert {"platform", "account", "url", "date", "matchConfidence", "text", "price"} <= set(ev["ads"][0])
    assert [d["ref"] for d in ev["documents"]] == ["guarantee", "mio", "bvu"]
    assert all(d["source"] and d["date"] for d in ev["documents"])
    assert ev["photos"] == ev["complex"]["photos"]

    assert [(e["who"], e["what"], e["kind"]) for e in s["journal"]] == [(AUTHOR, "Сигнал создан: балл 70, зона красная", "signal")]
    assert (s["journal"][0]["signalId"], s["journal"][0]["complexId"], s["journal"][0]["adId"]) == (1, "c2", None)
    assert client.get("/api/signals/1/audit").json() == s["journal"]
    assert client.get("/api/signals/1").json() == s


def test_snapshot_filters_dismissed_ads_and_rejected_appeals(client):
    s = _create(client, "c4").json()
    assert [a["id"] for a in s["evidence"]["appeals"]] == ["ap1"] and _f(s, "R4")["state"] == "hit"

    s = _create(client, "c1").json()  # a3 отклонена, ap5 отклонено
    assert s["evidence"]["ads"] == [] and s["evidence"]["appeals"] == []

    s = _create(client, "c5").json()  # введённый объект: R1 не применяется, но документы в снимке есть
    assert _f(s, "R1")["reason"] == "built" and len(s["evidence"]["documents"]) == 3


def test_create_validation_and_conflict(client):
    assert _create(client, "nope").status_code == 404
    assert _create(client, "c2", author="   ").status_code == 422
    assert client.post("/api/signals", json={"author": AUTHOR}).status_code == 422

    first = _create(client, "c2").json()
    r = _create(client, "c2", author="Другой")
    assert r.status_code == 409
    assert r.json()["detail"]["signalId"] == first["id"] and "S-0001" in r.json()["detail"]["message"]
    assert len(client.get("/api/signals").json()) == 1


def test_concurrent_create_hits_unique_index(client, monkeypatch):
    from app import signals

    first = _create(client, "c11").json()
    real, calls = signals._open_signal, []

    def racing(db, cid):  # первая проверка "не видит" открытый сигнал - как при двух одновременных запросах
        calls.append(cid)
        return None if len(calls) == 1 else real(db, cid)

    monkeypatch.setattr(signals, "_open_signal", racing)
    r = _create(client, "c11")
    assert r.status_code == 409 and r.json()["detail"]["signalId"] == first["id"]


def test_decisions(client):
    sid = _create(client, "c2").json()["id"]
    assert client.post(f"/api/signals/{sid}/confirm", json={"author": "Тест"}).status_code == 422
    assert client.post(f"/api/signals/{sid}/confirm", json={"author": "Тест", "comment": "   "}).status_code == 422
    assert client.post(f"/api/signals/{sid}/return", json={"author": " ", "comment": "ok"}).status_code == 422

    r = client.post(f"/api/signals/{sid}/return", json={"author": "Тест", "comment": "  Нет скриншота публикации  "})
    assert r.status_code == 200
    s = r.json()
    assert s["status"] == "returned" and s["decidedBy"] == "Тест" and s["decidedAt"].endswith("+00:00")
    assert s["decisionComment"] == "Нет скриншота публикации"
    assert [e["what"] for e in s["journal"]] == ["Сигнал создан: балл 70, зона красная",
                                                 "Возвращён на доработку. Нет скриншота публикации"]
    assert (s["journal"][1]["who"], s["journal"][1]["kind"]) == ("Тест", "signal")

    # повторное решение - только из "new"
    r = client.post(f"/api/signals/{sid}/confirm", json={"author": "Тест", "comment": "передумал"})
    assert r.status_code == 409 and r.json()["detail"]["signalId"] == sid
    assert client.get(f"/api/signals/{sid}").json()["status"] == "returned"

    # после возврата по тому же ЖК можно создать новый сигнал
    s2 = _create(client, "c2").json()
    assert s2["code"] == "S-0002"
    s2 = client.post(f"/api/signals/{s2['id']}/confirm", json={"author": "Тест", "comment": "Факты подтверждены"}).json()
    assert s2["status"] == "confirmed" and s2["journal"][-1]["what"] == "Сигнал подтверждён. Факты подтверждены"


def test_list_filters_and_stats(client):
    ids = [_create(client, cid).json()["id"] for cid in ("c2", "c4", "c11")]
    client.post(f"/api/signals/{ids[1]}/confirm", json={"author": "Тест", "comment": "ok"})

    rows = client.get("/api/signals").json()
    assert [r["id"] for r in rows] == ids[::-1]
    assert {"id", "code", "complexId", "complexName", "status", "score", "zone", "needsCheck", "hits", "createdAt",
            "createdBy", "decidedAt", "decidedBy"} == set(rows[0])
    assert [r["complexId"] for r in client.get("/api/signals", params={"status": "new"}).json()] == ["c11", "c2"]
    assert [r["complexId"] for r in client.get("/api/signals", params={"status": "confirmed"}).json()] == ["c4"]
    assert [r["id"] for r in client.get("/api/signals", params={"complexId": "c11"}).json()] == [ids[2]]
    assert client.get("/api/signals", params={"status": "returned", "complexId": "c2"}).json() == []
    assert client.get("/api/signals", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/stats").json()["signals"] == {"new": 2, "confirmed": 1, "returned": 0}


def test_snapshot_is_immutable(client):
    before = _create(client, "c2").json()
    assert client.patch("/api/ads/a1/status", json={"status": "dismissed", "author": "Тест"}).status_code == 200

    live = client.get("/api/complexes/c2/risk").json()
    assert _f(live, "R2")["state"] != "hit" and live["score"] < 70  # живой риск изменился

    after = client.get(f"/api/signals/{before['id']}").json()
    assert {k: v for k, v in after.items() if k != "journal"} == {k: v for k, v in before.items() if k != "journal"}
    assert after["evidence"]["ads"][0]["status"] == "new" and _f(after, "R2")["state"] == "hit"


def test_pdf_export(client):
    sid = _create(client, "c4").json()["id"]
    r = client.get(f"/api/signals/{sid}/pdf", params={"author": "Тест"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="signal-S-0001.pdf"'
    assert r.content.startswith(b"%PDF") and r.content.rstrip().endswith(b"%%EOF") and len(r.content) > 5000

    s = client.get(f"/api/signals/{sid}").json()
    assert s["exportedAt"] and s["exportedAt"].endswith("+00:00")
    assert (s["journal"][-1]["who"], s["journal"][-1]["what"], s["journal"][-1]["kind"]) == ("Тест", "Выгружен PDF", "export")
    assert s["status"] == "new"  # выгрузка не решение

    client.post(f"/api/signals/{sid}/confirm", json={"author": "Тест", "comment": "ok"})
    assert client.get(f"/api/signals/{sid}/pdf").status_code == 200  # и после решения

    # автор выгрузки - как в формах: NUL(PostgreSQL его не хранит), "Система" и длинное имя - 422, пустое - "Аналитик"
    for bad in ("Те\x00ст", "Система", " система ", "x" * 129):
        assert client.get(f"/api/signals/{sid}/pdf", params={"author": bad}).status_code == 422, bad
    assert client.get(f"/api/signals/{sid}/pdf", params={"author": "  Иванов   И. "}).status_code == 200
    for empty in ("", "  "):  # фронтенд без входа шлёт ?author=
        assert client.get(f"/api/signals/{sid}/pdf", params={"author": empty}).status_code == 200
    r = client.get(f"/api/signals/{sid}/pdf", params={"author": "Система"})
    assert r.json()["detail"][0]["loc"] == ["query", "author"]
    who = [e["who"] for e in client.get(f"/api/signals/{sid}/audit").json() if e["kind"] == "export"]
    assert who == ["Тест", "Аналитик", "Иванов И.", "Аналитик", "Аналитик"]


def test_pdf_survives_unprintable_text(client):
    client.post("/api/ads", json={"platform": "telegram", "account": "@x <b>&", "complexId": "c14", "matchConfidence": 0.9,
                                  "text": "🔥🔥 Солнечный квартал ✅ бронь от 1 000 000 ₸ → звоните <script> & «Қазақ» 🏙️"})
    sid = _create(client, "c14").json()["id"]
    r = client.get(f"/api/signals/{sid}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_not_found(client):
    body = {"author": "Тест", "comment": "ok"}
    assert client.get("/api/signals/999").status_code == 404
    assert client.get("/api/signals/999/audit").status_code == 404
    assert client.get("/api/signals/999/pdf").status_code == 404
    assert client.post("/api/signals/999/confirm", json=body).status_code == 404
    assert client.post("/api/signals/999/return", json=body).status_code == 404
    assert client.get("/api/signals/abc").status_code == 422
    # id за пределами INTEGER - 422, а не 500 из драйвера БД; граница диапазона - обычный 404
    for bad in ("0", "-1", "2147483648", "99999999999999999999999"):
        assert client.get(f"/api/signals/{bad}").status_code == 422, bad
        assert client.get(f"/api/signals/{bad}/audit").status_code == 422, bad
        assert client.get(f"/api/signals/{bad}/pdf").status_code == 422, bad
        assert client.post(f"/api/signals/{bad}/confirm", json=body).status_code == 422, bad
        assert client.post(f"/api/signals/{bad}/return", json=body).status_code == 422, bad
    assert client.get("/api/signals/2147483647").status_code == 404
