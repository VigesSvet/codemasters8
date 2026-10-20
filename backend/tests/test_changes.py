""""Удалено в источнике" и история изменений(app.changes): пословный diff версий, удалённые условия оплаты, статус в
источнике по окну сбора(classify), GET /api/ads/{id}/source-status и GET /api/changes на приёме Instagram."""
from datetime import datetime, timedelta, timezone

from app import changes, ingest, models
from app.db import SessionLocal
from app.integrations.base import Publication

T0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


# ---------- diff ----------

def test_diff_words_marks_removed_and_added():
    ops = changes.diff_words("Старт продаж! Бронь 200 000 ₸, рассрочка до сдачи", "Старт продаж! Рассрочка до сдачи 2027")
    assert "".join(t for op, t in ops if op != "del") == "Старт продаж! Рассрочка до сдачи 2027"
    assert "".join(t for op, t in ops if op != "add") == "Старт продаж! Бронь 200 000 ₸, рассрочка до сдачи"
    assert ("del", "Бронь 200 000 ₸, рассрочка") in ops and ("add", "Рассрочка") in ops and ops[-1] == ("add", " 2027")
    assert changes.diff_words("а  б", "а б") == [("eq", "а б")]  # только пробелы - без изменений
    assert changes.diff_words("", "новый текст") == [("add", "новый текст")]
    assert changes.diff_words("старый", "") == [("del", "старый")]


def test_diff_words_long_texts_fall_back(monkeypatch):
    monkeypatch.setattr(changes, "DIFF_CELLS", 4)
    ops = changes.diff_words("x a b c y", "x d e f y")
    assert ops == [("eq", "x "), ("del", "a b c"), ("add", "d e f"), ("eq", " y")]


def test_change_of_detects_terms_and_price():
    prev = {"version": 1, "text": "Квартиры в ЖК. Бронь 200 000 ₸ до конца месяца", "price": "30 млн ₸",
            "mediaKeys": ["a"]}
    cur = {"version": 2, "text": "Квартиры в ЖК.", "price": "32 млн ₸", "mediaKeys": ["a"], "collectedAt": "2026-10-02"}
    ch = changes.change_of(prev, cur)
    assert ch["kinds"] == ["price", "text_removed", "terms_removed"]
    assert ch["removed"] == ["Бронь 200 000 ₸ до конца месяца"] and ch["added"] == []
    assert "бронь" in [x.lower() for x in ch["termsRemoved"]] and "200 000 ₸" in ch["termsRemoved"]
    assert (ch["priceFrom"], ch["priceTo"], ch["fromVersion"], ch["toVersion"]) == ("30 млн ₸", "32 млн ₸", 1, 2)
    assert ch["hunks"] == [{"before": "Квартиры в ЖК.", "removed": "Бронь 200 000 ₸ до конца месяца", "added": "",
                            "after": ""}]
    assert changes.change_of(prev, {**prev, "version": 2})["kinds"] == []


def test_hunks_context():
    ops = changes.diff_words("Рассрочка от 30% до 10 месяцев на все квартиры", "Рассрочка от 50% до 6 месяцев на все квартиры")
    assert changes.hunks(ops, context=2) == [
        {"before": "Рассрочка от", "removed": "30%", "added": "50%", "after": "до"},
        {"before": "до", "removed": "10", "added": "6", "after": "месяцев на …"}]


# ---------- статус в источнике ----------

def it(i, run, pub_days, collector="instagram", account="@a"):
    return {"id": i, "collector": collector, "account": account, "lastRunId": run,
            "publishedAt": T0 + timedelta(days=pub_days), "collectedAt": T0 + timedelta(days=30)}


RUNS = [{"id": n, "status": "succeeded", "trigger": "schedule", "finishedAt": T0 + timedelta(days=n), "createdAt": None}
        for n in range(1, 6)] + [{"id": 6, "status": "succeeded", "trigger": "test", "finishedAt": None, "createdAt": None}]


def test_classify_present_deleted_window_and_single_miss():
    window = [it(i, 5, 10 + i) for i in range(1, 5)]  # в последнем запуске - публикации дней 11…14
    gone = it(9, 2, 12)  # дня 12 - в окне, не видели в запусках 3, 4, 5
    s = changes.classify(gone, window + [gone], RUNS)
    assert s["status"] == "deleted" and s["missedRuns"] == 3 and "3 последних сборах" in s["how"]
    assert s["checkedAt"] == (T0 + timedelta(days=5)).isoformat(timespec="seconds")
    old = it(10, 2, 3)  # старше окна - вытеснена новыми публикациями
    assert changes.classify(old, window + [old], RUNS)["status"] == "unknown"
    assert "Вытеснена" in changes.classify(old, window, RUNS)["how"]
    once = it(11, 4, 12)  # пропущена одним запуском - ещё не удаление
    s = changes.classify(once, window + [once], RUNS)
    assert s["status"] == "unknown" and s["missedRuns"] == 1 and "ждём подтверждения" in s["how"]
    assert changes.classify(window[0], window, RUNS)["status"] == "present"
    alone = it(12, 2, 12, account="@b")  # аккаунт позже не собирался - нет данных против
    assert changes.classify(alone, [alone], RUNS)["status"] == "present"
    nopub = {**gone, "publishedAt": None}
    assert changes.classify(nopub, window + [nopub], RUNS)["status"] == "unknown"


def test_classify_krisha_and_other_unknown():
    k = it(1, 1, 1, collector="krisha")
    s = changes.classify(k, [k, it(2, 5, 2, collector="krisha")], RUNS)
    assert s["status"] == "unknown" and "Krisha" in s["how"]
    assert changes.classify(it(1, 1, 1, collector="telegram"), [], RUNS)["status"] == "unknown"


# ---------- API на приёме ----------

def pub(n, text, when, price=None, account="@zhk.test"):
    return Publication(platform="instagram", external_id=f"chg-{n}", account=account,
                       url=f"https://www.instagram.com/p/CHG{n}/", text=text, collected_at=when,
                       published_at=T0 + timedelta(days=n), price=price)


def run_row(db, n):
    r = models.CollectionRun(collector="instagram", trigger="schedule", status="succeeded",
                             created_at=T0 + timedelta(days=20 + n), finished_at=T0 + timedelta(days=20 + n))
    db.add(r)
    db.flush()
    return r.id


def test_source_status_and_changes_api(client):
    with SessionLocal() as db:
        r1 = run_row(db, 1)
        res = {n: ingest.ingest_publication(db, pub(n, f"ЖК Тест, пост {n}. Бронь 200 000 ₸", T0), collector="instagram",
                                            run_id=r1) for n in range(1, 5)}
        # запуски 2 и 3: публикация 2 пропала(в окне), у публикации 3 убрали бронь - новая версия
        for k in (2, 3):
            rk = run_row(db, k)
            for n in (1, 3, 4):
                text = "ЖК Тест, пост 3. Рассрочка" if n == 3 else f"ЖК Тест, пост {n}. Бронь 200 000 ₸"
                ingest.ingest_publication(db, pub(n, text, T0 + timedelta(days=20 + k)), collector="instagram", run_id=rk)
        db.commit()
    gone, edited = res[2].ad_id, res[3].ad_id
    s = client.get(f"/api/ads/{gone}/source-status").json()
    assert s["status"] == "deleted" and s["missedRuns"] == 2 and s["savedAt"] and s["lastSeenAt"]
    assert client.get(f"/api/ads/{edited}/source-status").json()["status"] == "present"
    manual = client.get("/api/ads").json()[0]["id"]
    m = client.get(f"/api/ads/{manual}/source-status").json()
    assert m["status"] == "unknown" and m["collector"] is None
    assert client.get("/api/ads/nope/source-status").status_code == 404

    d = client.get("/api/changes", params={"days": 3650}).json()
    ch = [c for c in d["changes"] if c["adId"] == edited]
    assert len(ch) == 1 and ch[0]["fromVersion"] == 1 and ch[0]["toVersion"] == 2
    assert "terms_removed" in ch[0]["kinds"] and "200 000 ₸" in ch[0]["termsRemoved"] and ch[0]["added"] == ["Рассрочка"]
    assert [x["adId"] for x in d["deleted"]] == [gone] and d["sourceStatus"]["deleted"] == 1
    assert d["limits"] and d["items"] == 4
    assert client.get("/api/changes", params={"days": 0}).status_code == 422


def test_changes_empty(client):
    d = client.get("/api/changes").json()
    assert d["changes"] == [] and d["deleted"] == [] and d["items"] == 0
