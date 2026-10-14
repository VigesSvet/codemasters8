import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]

        # 14 ЖК в демо-наборе, светофор 4 / 3 / 7
        assert len(c.get("/api/complexes").json()) == 14
        assert c.get("/api/stats").json()["zones"] == {"red": 4, "yellow": 3, "green": 7}

        # Красная зона только при R1 + R2
        r = c.get("/api/complexes/c4/risk").json()
        assert r["zone"] == "red" and r["factors"][0]["state"] == "hit" and r["factors"][1]["state"] == "hit"

        # Гарантия ЕО есть, МИО нет → это не R1
        r = c.get("/api/complexes/c10/risk").json()
        assert r["factors"][0]["state"] == "clear"

        # Нет сведений → 0 баллов и «нужна проверка»
        r = c.get("/api/complexes/c14/risk").json()
        assert r["needsCheck"] and r["factors"][0]["state"] == "unknown"

        # Ручное добавление: ЖК определяется по названию в тексте
        ad = c.post("/api/ads", json={"platform": "instagram", "account": "@test", "text": "Старт продаж ЖК Алтай Резиденс, бронь"}).json()
        assert ad["complexId"] == "c2"
        assert c.get(f"/api/ads/{ad['id']}/score").json()["zone"] == "red"

        # Решение аналитика пишется в журнал
        assert c.patch(f"/api/ads/{ad['id']}/status", json={"status": "confirmed", "assignee": "Тест"}).json()["status"] == "confirmed"
        assert any("confirmed" in e["what"] for e in c.get(f"/api/ads/{ad['id']}/audit").json())
