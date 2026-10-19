"""«Спутник против рекламы» (app.satstage): выбор снимков, склейка тайлов, запрос с изображениями к поддельному
Anthropic API, разбор JSON модели, кэш по «ЖК/выпуск/дата съёмки», без ключа — без запросов, сравнение с рекламой и
GET /api/complexes/{id}/sat-stage. Сети нет: тайлы — FETCH, клиент SDK — httpx2.MockTransport (как test_llm)."""
import base64
import io
import json

import pytest
from PIL import Image

from app import satstage
from app.db import SessionLocal
from app.integrations import analyzer_anthropic as claude
from test_llm import KEY, FakeApi, message

SHOTS = [{"id": 26334, "date": "2026-08-05", "acquired": "2024-09-26", "res": 0.31, "sensor": "WV03"},
         {"id": 49849, "date": "2024-11-18", "acquired": "2023-06-03", "res": 0.5, "sensor": "WV02"},
         {"id": 9181, "date": "2020-09-02", "acquired": "2019-10-13", "res": 0.5, "sensor": "WV02"}]


def tile_jpeg(color=(120, 110, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def api(monkeypatch):
    fake = FakeApi()
    monkeypatch.setattr(claude, "CLIENT_FACTORY", fake.factory)
    return fake


@pytest.fixture
def tiles(monkeypatch):
    calls = []

    def fetch(release, z, y, x):
        calls.append((release, z, y, x))
        return tile_jpeg()
    monkeypatch.setattr(satstage, "FETCH", fetch)
    return calls


# ---------- снимки и тайлы ----------

def test_pick_shots_latest_and_nearest_to_start():
    assert [(r, s["id"]) for r, s in satstage.pick_shots(SHOTS, "2023-04-01")] == [("latest", 26334), ("start", 49849)]
    # ближайший к началу — сам свежий снимок: второго нет; начала нет — только свежий
    assert [r for r, _ in satstage.pick_shots(SHOTS, "2025-06-01")] == ["latest"]
    assert [r for r, _ in satstage.pick_shots(SHOTS, None)] == ["latest"]
    assert satstage.pick_shots([], "2024-01-01") == []


def test_compose_stitches_3x3_and_centres_point(tiles):
    jpeg, got = satstage.compose(26334, 49.893336, 82.618958)
    assert got == 9 and len(tiles) == 9
    fx, fy = satstage.tile_of(49.893336, 82.618958)
    assert {(y, x) for _, _, y, x in tiles} == {(int(fy) + dy, int(fx) + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
    im = Image.open(io.BytesIO(jpeg))
    assert im.size == (satstage.CROP, satstage.CROP)
    r, g, b = im.getpixel((satstage.CROP // 2 + satstage.RING, satstage.CROP // 2))[:3]
    assert r > 200 and g < 120  # кольцо точки — красное
    assert satstage.shot_key(49.893336, 82.618958) == f"17/{int(fy)}/{int(fx)}"


def test_compose_missing_tiles_are_grey(monkeypatch):
    monkeypatch.setattr(satstage, "FETCH", lambda *a: None)
    jpeg, got = satstage.compose(1, 49.9, 82.6)
    assert got == 0 and jpeg[:2] == b"\xff\xd8"


# ---------- ответ модели ----------

def test_parse_reply_validates_fields():
    text = json.dumps({"images": [
        {"index": 2, "stage": "frame", "confidence": "high", "reasoning": "  Видны  этажи и кран ", "cues": ["кран", ""]},
        {"index": 1, "stage": "skyscraper", "confidence": "sure", "reasoning": "x" * 900, "cues": []},
        {"index": 2, "stage": "completed", "confidence": "high", "reasoning": "повтор", "cues": []},
        {"index": 7, "stage": "frame", "confidence": "high", "reasoning": "лишний", "cues": []}]})
    one, two, three = satstage.parse_reply(text, 3)
    assert one["stage"] == "unclear" and one["confidence"] == "low" and len(one["reasoning"]) == satstage.REASONING_MAX
    assert two == {"stage": "frame", "confidence": "high", "reasoning": "Видны этажи и кран", "cues": ["кран"]}
    assert three["stage"] == "unclear" and "не ответила" in three["reasoning"]
    with pytest.raises(ValueError):
        satstage.parse_reply("не json", 1)
    with pytest.raises(ValueError):
        satstage.parse_reply(json.dumps({"stage": "frame"}), 1)


def demo_cx(cid="c2"):
    with SessionLocal() as db:
        c = db.get(satstage.models.Complex, cid)
        return c.id, c.lat, c.lng, c.start


def test_run_sends_images_saves_store_and_caches(client, env, api, tiles, tmp_path):
    env(ANTHROPIC_API_KEY=KEY)
    cid, lat, lng, start = demo_cx("c5")  # начало 2023-04-01 — ближайший к нему снимок 2023-06-03
    index = {satstage.shot_key(lat, lng): SHOTS}
    reply = {"images": [{"index": 1, "stage": "roof_facade", "confidence": "medium", "reasoning": "Кровля, фасад",
                         "cues": ["кровля"]},
                        {"index": 2, "stage": "excavation", "confidence": "high", "reasoning": "Котлован", "cues": []}]}
    api.add(message(reply))
    store = tmp_path / "sat.json"
    with SessionLocal() as db:
        rep = satstage.run(db, only=[cid], index=index, store_path=store)
    assert rep["requests"] == 1 and rep["costUsd"] > 0 and not rep["failed"]
    body = api.messages[0]["body"]
    assert body["model"] == "claude-sonnet-5-5" and body["output_config"]["format"]["type"] == "json_schema"
    blocks = body["messages"][0]["content"]
    imgs = [b for b in blocks if b["type"] == "image"]
    assert len(imgs) == 2 and imgs[0]["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(imgs[0]["source"]["data"])[:2] == b"\xff\xd8"
    assert "съёмка 2024-09-26" in json.dumps(blocks, ensure_ascii=False)
    d = json.loads(store.read_text("utf-8"))
    a, b = d["items"][f"{cid}/26334/2024-09-26"], d["items"][f"{cid}/49849/2023-06-03"]
    assert (a["role"], a["stage"], b["role"], b["stage"]) == ("latest", "roof_facade", "start", "excavation")
    assert a["model"] == "claude-sonnet-5-5" and a["generatedAt"] and a["promptVersion"] == satstage.PROMPT_VERSION
    assert d["runs"][-1]["requests"] == 1 and d["runs"][-1]["costUsd"] > 0
    # те же снимки повторно не отправляются
    with SessionLocal() as db:
        rep = satstage.run(db, only=[cid], index=index, store_path=store)
    assert rep["requests"] == 0 and rep["cached"] == [cid] and len(api.messages) == 1


def test_run_without_key_sends_nothing(client, api, tiles, tmp_path):
    cid, lat, lng, _ = demo_cx()
    with SessionLocal() as db:
        rep = satstage.run(db, only=[cid], index={satstage.shot_key(lat, lng): SHOTS}, store_path=tmp_path / "s.json")
    assert rep["requests"] == 0 and any("ANTHROPIC_API_KEY" in x for x in rep["skipped"]) and not api.calls
    assert not tiles and not (tmp_path / "s.json").exists()


def test_run_limit_and_bad_reply(client, env, api, tiles, tmp_path):
    env(ANTHROPIC_API_KEY=KEY)
    cxs = [demo_cx(c) for c in ("c2", "c4")]
    index = {satstage.shot_key(lat, lng): SHOTS[:1] for _, lat, lng, _ in cxs}
    api.add(message("это не JSON"))
    with SessionLocal() as db:
        rep = satstage.run(db, only=[c[0] for c in cxs], index=index, store_path=tmp_path / "s.json", limit=1)
    assert rep["requests"] == 1 and "не разобран" in rep["failed"][0]
    assert any("предел 1 запросов" in x for x in rep["skipped"])


# ---------- сравнение с рекламой ----------

def img(stage, acquired="2024-09-26", role="latest", conf="medium"):
    return {"role": role, "stage": stage, "acquired": acquired, "releaseId": 26334, "confidence": conf}


def claim(kind, date, quote="цитата", ad="a1"):
    return {"kind": kind, "date": date, "quote": quote, "adId": ad}


def kinds(hints):
    return [h["kind"] for h in hints]


def test_compare_ahead_and_stale():
    # реклама до снимка заявляет каркас, на снимке котлован — расхождение R5
    h = satstage.compare([img("excavation")], [claim("advanced", "2024-08-01 10:00", "каркас готов")], None)
    assert kinds(h) == ["ahead"] and h[0]["factor"] == "R5" and "каркас готов" in h[0]["text"]
    assert "гипотеза ИИ" in h[0]["text"] and h[0]["releaseId"] == 26334
    # реклама на 2 года новее снимка — снимком не проверить, расхождения нет
    h = satstage.compare([img("empty_lot")], [claim("completed", "2026-09-01", "дом сдан")], None)
    assert kinds(h) == ["stale"] and "старше рекламы на 23 мес." in h[0]["text"]
    # сданный дом на снимке и позднее «дом сдан» — не противоречит при любой давности
    assert kinds(satstage.compare([img("completed")], [claim("completed", "2026-09-01")], None)) == ["match"]


def test_compare_money_early_behind_before_start():
    h = satstage.compare([img("foundation")], [claim("money", "2024-10-10", "бронь 200 000 ₸")], None)
    assert kinds(h) == ["money_early"] and h[0]["factor"] == "R1" and "ст. 7" in h[0]["text"]
    # на снимке дом готов, позже реклама — «строится»: возможно, другая очередь
    h = satstage.compare([img("completed")], [claim("building", "2025-03-01", "строится")], None)
    assert kinds(h) == ["behind"] and "другая очередь" in h[0]["text"]
    # реклама «строится» до снимка с готовым домом — не противоречит
    assert kinds(satstage.compare([img("completed")], [claim("building", "2024-01-01")], None)) == ["match"]
    # работы на снимке раньше начала стройки по документам
    h = satstage.compare([img("frame"), img("foundation", "2023-06-03", "start")], [], "2025-06-01")
    assert kinds(h) == ["before_start"] and "01.06.2025" in h[0]["text"]
    # уверенность low и пустая площадка — не наблюдение
    assert satstage.compare([img("frame", conf="low")], [], "2025-06-01") == []


def test_compare_unclear_and_empty():
    assert kinds(satstage.compare([img("unclear")], [claim("money", "2024-09-01")], None)) == ["unclear"]
    assert satstage.compare([img("unclear")], [], None) == []
    assert satstage.compare([], [claim("money", "2024-09-01")], None) == []


def test_claims_of_ad_markers():
    from app.screening import AdFacts

    class A:
        text = "Каркас возведён! Котлован второй очереди готов, бронь 100 000 ₸"
        ocr = ""
    f = AdFacts(id="x", date="2026-01-01", day=None, platform="instagram", account="@a", source="collector",
                status="new", complex_id=None, confirmed=False, rejected=frozenset(), terms_rejected=False,
                concerning=[("booking", "бронь 100 000 ₸")], stage="under_construction", stage_quote="строится дом")
    got = {c["kind"]: c["quote"] for c in satstage.claims_of(f, A(), "claude")}
    assert set(got) == {"advanced", "building", "early", "money"}
    assert "Каркас возведён" in got["advanced"] and got["money"] == "бронь 100 000 ₸"
    A.text = "🏗 Каркас дома уже полностью готов. Квартиры с отделкой и вентилируемым фасадом"
    got = {c["kind"]: c["quote"] for c in satstage.claims_of(f, A(), "claude")}
    assert got["advanced"] == "🏗 Каркас дома уже полностью готов."
    A.text = "Чистовая отделка, вентилируемый фасад, плоская кровля"  # свойства дома — не стадия
    assert "advanced" not in {c["kind"] for c in satstage.claims_of(f, A(), "claude")}


def test_sat_stage_endpoint(client, monkeypatch, tmp_path):
    store = tmp_path / "sat.json"
    store.write_text(json.dumps({"items": {"c2/26334/2024-09-26": {**img("excavation"), "complexId": "c2",
                                                                    "reasoning": "Котлован", "model": "m"}}}), "utf-8")
    monkeypatch.setattr(satstage, "STORE", store)
    r = client.get("/api/complexes/c2/sat-stage")
    assert r.status_code == 200
    d = r.json()
    assert d["complexId"] == "c2" and d["images"][0]["stageLabel"] == "котлован" and d["note"] is None
    assert isinstance(d["claims"], list) and isinstance(d["hints"], list) and "не вывод о нарушении" in d["disclaimer"]
    assert all(c["kindLabel"] for c in d["claims"])
    r = client.get("/api/complexes/c3/sat-stage")
    assert r.json()["images"] == [] and "не анализировался" in r.json()["note"]
    assert client.get("/api/complexes/nope/sat-stage").status_code == 404
