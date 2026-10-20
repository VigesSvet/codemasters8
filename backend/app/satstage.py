""""Спутник против рекламы": стадия стройки на снимке Esri Wayback(гипотеза Claude по изображению) против того, что о
стадии заявляет реклама ЖК.

Командная строка - `python -m app.satstage [--only ID,…] [--limit N] [--max-usd X] [--force] [--dry-run]`: для каждого
ЖК с координатами берёт из индекса снимков(frontend/public/wayback-index.json, scripts/wayback-index.mjs) свежий снимок
точки и снимок, ближайший к началу стройки по документам(если он другой), скачивает по выпуску 3 × 3 тайла 17-го
масштаба вокруг точки, склеивает(Pillow), обрезает 512 × 512 с точкой в центре, отмечает точку кольцом и одним
запросом спрашивает модель ANTHROPIC_MODEL(app.integrations.analyzer_anthropic: тот же ключ, клиент, цены, fallbacks)
о стадии на каждом снимке: empty_lot | excavation | foundation | frame | roof_facade | completed | unclear, уверенность,
что видно(по-русски). Результат - shared/sat_stage.json(поставляется в репозитории, как индекс снимков): записи по
ключу "ЖК/выпуск/дата съёмки" с моделью, временем и стоимостью запроса. Те же снимки той же версией промпта повторно не
отправляются(--force - отправить). Без ANTHROPIC_API_KEY запросов нет. Предел - MAX_REQUESTS запросов и --max-usd за
запуск; расход пишется в лог и в runs файла.

GET /api/complexes/{id}/sat-stage читает файл и сравнивает снимок с рекламой ЖК(отнесение публикаций - как в
"Проверке по рекламе", screening.load_facts): расхождение - подсказка аналитику сверить готовность(R5) или основание
(R1, ст. 7), баллов не добавляет: R5 требует подтверждённых значений с источником и датой, снимок и ответ модели - нет.
"""
import argparse
import base64
import io
import json
import logging
import math
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, screening
from .db import get_db
from .integrations import analyzer_anthropic as claude
from .risk import hits, quote as marker_quote
from .risk_model import SHARED
from .settings import Settings, get_settings

log = logging.getLogger("saq.satstage")

ROOT = Path(__file__).resolve().parents[2]
STORE = SHARED / "sat_stage.json"  # в Docker - /app/shared(SAQ_SHARED_DIR)
INDEX = ROOT / "frontend" / "public" / "wayback-index.json"  # нужен только командной строке(в образе API его нет)
TILE_URL = ("https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/"
            "tile/{release}/{z}/{y}/{x}")
ZOOM = 17
TILE = 256
CROP = 512
RING = 56  # радиус кольца точки, пкс(≈ 65 м на 17-м масштабе в Усть-Каменогорске)
PROMPT_VERSION = "saq-sat-1"
MAX_REQUESTS = 25  # запросов за запуск
MAX_USD = 1.0  # расход за запуск по умолчанию
REASONING_MAX = 400
CUES_MAX = 5
CUE_LEN = 120
GRACE_DAYS = 90  # публикация не позже снимка + 90 дней - снимок ещё описывает площадку на дату публикации

STAGES = ("empty_lot", "excavation", "foundation", "frame", "roof_facade", "completed", "unclear")
STAGE_LABELS = {"empty_lot": "пустая площадка", "excavation": "котлован", "foundation": "фундамент, нулевой цикл",
                "frame": "каркас возводится", "roof_facade": "каркас возведён, кровля и фасад",
                "completed": "дом выглядит завершённым", "unclear": "по снимку не определить"}
LEVEL = {"empty_lot": 0, "excavation": 1, "foundation": 2, "frame": 3, "roof_facade": 4, "completed": 5}
CONFIDENCE = ("low", "medium", "high")
DISCLAIMER = ("Стадия на снимке — гипотеза ИИ по спутниковому изображению на дату съёмки, а не подтверждённая "
              "готовность и не вывод о нарушении. В балл не входит: R5 считается только по заявленной и подтверждённой "
              "готовности с источниками и датами (акт осмотра, фото-мониторинг).")

# Что реклама говорит о стадии(кроме objectStage Claude): маркеры с цитатой
ADVANCED_MARKERS = ["каркас готов", "каркас возвед", "возведён каркас", "возведен каркас", "возвели каркас",
                    "монолит заверш", "кровля готова", "монтаж кровли", "фасадные работы", "монтаж фасада",
                    "на финишной прямой", "готовится к сдаче", "сдача дома", "сдаём дом", "сдаем дом", "выдача ключей",
                    "получение ключей", "заселени"]  # описания отделки и фасада как свойства дома - не стадия
# "Каркас дома уже полностью готов", "каркас 9-этажного корпуса возведён" - между словами до 40 символов
FRAME_RE = re.compile(r"каркас\w*[^.!?\n]{0,40}?(?:готов|возвед|заверш)\w*", re.I)
EARLY_MARKERS = ["котлован", "нулевой цикл", "старт строительства", "начало строительства", "начали строительство"]
CLAIM_LABELS = {"completed": "дом сдан", "advanced": "каркас возведён, финиш или сдача", "building": "строится",
                "early": "котлован, начало строительства", "money": "приём денег"}

Obj = dict[str, Any]


# ---------- индекс снимков и тайлы ----------

def tile_of(lat: float, lng: float, z: int = ZOOM) -> tuple[float, float]:
    """Дробные координаты тайла Web Mercator(как tileOf в lib/wayback.ts, без округления)."""
    n = 2 ** z
    r = math.radians(lat)
    return (lng + 180) / 360 * n, (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n


def shot_key(lat: float, lng: float, z: int = ZOOM) -> str:
    fx, fy = tile_of(lat, lng, z)
    return f"{z}/{int(fy)}/{int(fx)}"


def load_index(path: Path = INDEX) -> dict[str, list[Obj]]:
    try:
        return json.loads(path.read_text("utf-8")).get("tiles") or {}
    except (OSError, ValueError):
        return {}


def shot_date(s: Obj) -> str:
    return str(s.get("acquired") or s.get("date") or "")


def pick_shots(shots: list[Obj], start: str | None) -> list[tuple[str, Obj]]:
    """Свежий снимок(role latest) и снимок, ближайший к началу стройки по документам(role start), если он другой."""
    shots = [s for s in shots if shot_date(s)]
    if not shots:
        return []
    shots = sorted(shots, key=shot_date, reverse=True)
    out = [("latest", shots[0])]
    d0 = _day(start)
    if d0 is not None:
        near = min(shots, key=lambda s: abs((_day(shot_date(s)) or d0) - d0))
        if near.get("id") != shots[0].get("id"):
            out.append(("start", near))
    return out


def fetch_tile(release: int, z: int, y: int, x: int) -> bytes | None:
    """Тайл выпуска Wayback(JPEG); нет тайла или ошибка - None. Тесты подменяют FETCH(сети в тестах нет)."""
    import httpx

    try:
        r = httpx.get(TILE_URL.format(release=release, z=z, y=y, x=x), timeout=20,
                      headers={"User-Agent": "saq-qurylys/satstage"})
    except httpx.HTTPError:
        return None
    return r.content if r.status_code == 200 and r.content else None


FETCH: Callable[[int, int, int, int], bytes | None] = fetch_tile


def compose(release: int, lat: float, lng: float, fetch: Callable | None = None) -> tuple[bytes, int]:
    """3 × 3 тайла вокруг точки → JPEG 512 × 512, точка в центре, кольцо вокруг неё. Возвращает(байты, тайлов получено)."""
    from PIL import Image, ImageDraw

    fetch = fetch or FETCH
    fx, fy = tile_of(lat, lng)
    cx, cy = int(fx), int(fy)
    canvas = Image.new("RGB", (TILE * 3, TILE * 3), (42, 42, 42))
    got = 0
    cells = [(dx, dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
    with ThreadPoolExecutor(max_workers=9) as pool:
        raws = list(pool.map(lambda c: fetch(release, ZOOM, cy + c[1], cx + c[0]), cells))
    for (dx, dy), raw in zip(cells, raws):
        if not raw:
            continue
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue
        canvas.paste(im.resize((TILE, TILE)), ((dx + 1) * TILE, (dy + 1) * TILE))
        got += 1
    px, py = (fx - cx + 1) * TILE, (fy - cy + 1) * TILE
    left, top = int(round(px - CROP / 2)), int(round(py - CROP / 2))
    img = canvas.crop((left, top, left + CROP, top + CROP))
    d = ImageDraw.Draw(img)
    c = CROP / 2
    d.ellipse((c - RING, c - RING, c + RING, c + RING), outline=(255, 40, 40), width=3)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue(), got


def metres_per_px(lat: float, z: int = ZOOM) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / 2 ** z


# ---------- запрос и ответ ----------

SYSTEM_PROMPT = """\
Ты помогаешь аналитику мониторинга долевого строительства (Усть-Каменогорск, Казахстан) читать спутниковые снимки.
По каждому снимку определи стадию строительства жилого дома в красном кольце (точка ЖК по карточке; точность — десятки
метров, дом может выходить за кольцо). Стадии: empty_lot — пустая или расчищенная площадка без выемки; excavation —
котлован, земляные работы; foundation — фундамент, плита, нулевой цикл, каркас не выше первого этажа; frame — каркас
возводится (видны этажи, краны, опалубка), кровли нет; roof_facade — каркас возведён, кровля или фасад; completed —
дом выглядит готовым: кровля, фасад, благоустроенный двор или парковка, нет стройплощадки; unclear — нельзя определить
(облака, тень, низкое разрешение, кольцо не на стройке, на месте старая застройка без признаков стройки — тогда укажи это).
Пиши только о том, что видно; не делай выводов о законности, застройщике и рекламе. reasoning — 1–3 предложения
по-русски: что именно видно (кран, котлован, тени этажей, кровля). confidence — high, если признаки однозначны;
medium — вероятно; low — догадка. Отвечай строго JSON по схеме, по одному элементу на каждый снимок (index с 1)."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "images": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"index": {"type": "integer"},
                           "stage": {"type": "string", "enum": list(STAGES)},
                           "confidence": {"type": "string", "enum": list(CONFIDENCE)},
                           "reasoning": {"type": "string"},
                           "cues": {"type": "array", "items": {"type": "string"}}},
            "required": ["index", "stage", "confidence", "reasoning", "cues"]}},
    },
    "required": ["images"],
}


def build_request(s: Settings, name: str, lat: float, images: list[tuple[Obj, bytes]]) -> dict:
    """Запрос Messages API: те же модель, предел токенов, effort и fallbacks, что у анализа публикаций; в сообщении -
    подписи снимков и изображения(base64 JPEG)."""
    req = claude.build_request(s, system=SYSTEM_PROMPT, user="", schema=SCHEMA)
    m = metres_per_px(lat)
    blocks: list[Obj] = [{"type": "text", "text": (
        f"ЖК «{name[:120]}». Снимков: {len(images)}. Масштаб ≈ {m:.2f} м/пкс, кадр {CROP} × {CROP} пкс "
        f"(≈ {m * CROP:.0f} м), север сверху, кольцо радиусом ≈ {m * RING:.0f} м.")}]
    for i, (shot, jpeg) in enumerate(images, 1):
        meta = ", ".join(x for x in (f"съёмка {shot.get('acquired')}" if shot.get("acquired") else
                                     f"выпуск {shot.get('date')}", shot.get("sensor"),
                                     f"{shot['res']} м/пкс" if shot.get("res") else None) if x)
        blocks.append({"type": "text", "text": f"Снимок {i}: {meta}"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(jpeg).decode()}})
    req["messages"] = [{"role": "user", "content": blocks}]
    return req


def _text(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def parse_reply(text: str, n: int) -> list[Obj]:
    """JSON модели → по элементу на каждый из n снимков(index 1…n). Стадия вне списка - unclear, уверенность вне
    списка - low, нет ответа по снимку - unclear с пояснением. Не JSON - ValueError."""
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("images"), list):
        raise ValueError("в ответе нет списка images")
    by: dict[int, Obj] = {}
    for x in data["images"]:
        if not isinstance(x, dict):
            continue
        i = x.get("index")
        if not isinstance(i, int) or isinstance(i, bool) or not 1 <= i <= n or i in by:
            continue
        stage = x.get("stage") if x.get("stage") in STAGES else "unclear"
        conf = x.get("confidence") if x.get("confidence") in CONFIDENCE else "low"
        cues = [_text(c, CUE_LEN) for c in (x.get("cues") or []) if isinstance(c, str) and c.strip()][:CUES_MAX]
        by[i] = {"stage": stage, "confidence": conf, "reasoning": _text(x.get("reasoning"), REASONING_MAX), "cues": cues}
    return [by.get(i) or {"stage": "unclear", "confidence": "low", "reasoning": "Модель не ответила по этому снимку.",
                          "cues": []} for i in range(1, n + 1)]


# ---------- хранилище ----------

def load_store(path: Path | None = None) -> Obj:
    path = path or STORE
    try:
        d = json.loads(path.read_text("utf-8"))
        if isinstance(d, dict) and isinstance(d.get("items"), dict):
            d.setdefault("runs", [])
            return d
    except (OSError, ValueError):
        pass
    return {"promptVersion": PROMPT_VERSION, "items": {}, "runs": []}


def save_store(d: Obj, path: Path | None = None) -> None:
    path = path or STORE
    d["about"] = ("Стадия стройки на снимках Esri Wayback по точкам ЖК — гипотезы ИИ (python -m app.satstage); "
                  "ключ — «ЖК/выпуск/дата съёмки»")
    d["updatedAt"] = _now().isoformat(timespec="seconds")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def item_key(cid: str, shot: Obj) -> str:
    return f"{cid}/{shot.get('id')}/{shot.get('acquired') or shot.get('date')}"


def items_for(store: Obj, cid: str) -> list[Obj]:
    """Ответы по снимкам ЖК, свежие снимки первыми."""
    xs = [dict(v) for k, v in (store.get("items") or {}).items() if k.split("/", 1)[0] == cid and isinstance(v, dict)]
    return sorted(xs, key=lambda v: (str(v.get("acquired") or v.get("releaseDate") or ""), v.get("role") == "latest"),
                  reverse=True)


# ---------- запуск ----------

@dataclass
class Plan:
    cid: str
    name: str
    lat: float
    lng: float
    shots: list[tuple[str, Obj]]


def _now() -> datetime:
    return datetime.now(UTC)


def _day(v: str | None) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def plans(db: Session, index: dict[str, list[Obj]], only: list[str] | None = None) -> tuple[list[Plan], list[str]]:
    out, skipped = [], []
    for c in db.scalars(select(models.Complex).order_by(models.Complex.id)):
        if only and c.id not in only:
            continue
        if c.lat is None or c.lng is None:
            skipped.append(f"{c.id}: нет координат")
            continue
        shots = pick_shots(index.get(shot_key(c.lat, c.lng)) or [], c.start or None)
        if not shots:
            skipped.append(f"{c.id}: точки нет в индексе снимков — node frontend/scripts/wayback-index.mjs")
            continue
        out.append(Plan(c.id, c.name, c.lat, c.lng, shots))
    return out, skipped


def run(db: Session, *, only: list[str] | None = None, limit: int = MAX_REQUESTS, max_usd: float = MAX_USD,
        force: bool = False, dry_run: bool = False, store_path: Path | None = None,
        index: dict[str, list[Obj]] | None = None, settings: Settings | None = None) -> Obj:
    """Один проход по ЖК: запросы только по снимкам, которых нет в файле(или все - force). Итог - счётчики и расход."""
    s = settings or get_settings()
    store = load_store(store_path)
    todo, skipped = plans(db, index if index is not None else load_index(), only)
    report: Obj = {"requests": 0, "costUsd": 0.0, "done": [], "cached": [], "failed": [], "skipped": skipped}
    if not s.anthropic_api_key:
        report["skipped"].append("нет ANTHROPIC_API_KEY — запросы к модели не отправляются")
        return report
    limit = max(0, min(int(limit), MAX_REQUESTS))
    for p in todo:
        keys = [item_key(p.cid, shot) for _, shot in p.shots]
        have = store["items"]
        if not force and all(k in have and have[k].get("promptVersion") == PROMPT_VERSION for k in keys):
            report["cached"].append(p.cid)
            continue
        if report["requests"] >= limit:
            report["skipped"].append(f"{p.cid}: предел {limit} запросов за запуск")
            continue
        if report["costUsd"] >= max_usd:
            report["skipped"].append(f"{p.cid}: предел расхода ${max_usd:.2f} за запуск")
            continue
        images = []
        for role, shot in p.shots:
            jpeg, got = compose(int(shot["id"]), p.lat, p.lng)
            if got:
                images.append((role, shot, jpeg))
        if not images:
            report["failed"].append(f"{p.cid}: тайлы снимка не получены")
            continue
        if dry_run:
            report["done"].append(f"{p.cid}: снимков {len(images)} (без запроса)")
            continue
        req = build_request(s, p.name, p.lat, [(shot, jpeg) for _, shot, jpeg in images])
        report["requests"] += 1
        try:
            reply = claude.send(s, req)
        except claude.CallError as e:
            report["failed"].append(f"{p.cid}: {e.detail}")
            log.warning("satstage %s: %s", p.cid, e.detail)
            if e.kind in ("auth", "denied"):
                break
            continue
        report["costUsd"] += reply.cost_usd
        log.info("satstage %s: %s, $%.4f, токены %s", p.cid, reply.served_model, reply.cost_usd, reply.tokens)
        if reply.stop_reason not in ("end_turn", "stop_sequence"):
            report["failed"].append(f"{p.cid}: ответ не завершён ({reply.stop_reason})")
            continue
        try:
            parsed = parse_reply(reply.text, len(images))
        except ValueError as e:
            report["failed"].append(f"{p.cid}: ответ не разобран ({e})")
            continue
        at = _now().isoformat(timespec="seconds")
        for (role, shot, _), res in zip(images, parsed):
            store["items"][item_key(p.cid, shot)] = {
                "complexId": p.cid, "role": role, "releaseId": shot.get("id"), "releaseDate": shot.get("date"),
                "acquired": shot.get("acquired"), "sensor": shot.get("sensor"), "res": shot.get("res"),
                "tile": shot_key(p.lat, p.lng), "point": {"lat": p.lat, "lng": p.lng}, **res,
                "model": s.anthropic_model, "servedModel": reply.served_model, "promptVersion": PROMPT_VERSION,
                "generatedAt": at, "costUsd": round(reply.cost_usd / len(images), 6), "requestImages": len(images)}
        report["done"].append(f"{p.cid}: " + "; ".join(f"{shot.get('acquired')} — {r['stage']} ({r['confidence']})"
                                                      for (_, shot, _), r in zip(images, parsed)))
        save_store(store, store_path)
    if report["requests"]:
        store["runs"] = (store.get("runs") or [])[-49:] + [{
            "at": _now().isoformat(timespec="seconds"), "model": s.anthropic_model, "requests": report["requests"],
            "costUsd": round(report["costUsd"], 4), "failed": len(report["failed"])}]
        save_store(store, store_path)
    report["costUsd"] = round(report["costUsd"], 4)
    return report


# ---------- сравнение с рекламой ----------

def _q(f: "screening.AdFacts", ad: models.Ad | None, markers: list[str]) -> list[tuple[str, str]]:
    if ad is None:
        return []
    out = []
    for m in hits(f"{ad.text or ''} {ad.ocr or ''}", markers):
        out.append((m, screening._short(marker_quote(ad.text, m) or marker_quote(ad.ocr, m) or m)))
    return out


def claims_of(f: "screening.AdFacts", ad: models.Ad | None, basis: str) -> list[Obj]:
    """Что публикация заявляет о стадии: сдан(objectStage completed), каркас возведён / на финишной прямой / сдача
    (маркеры), строится(objectStage under_construction или "строится"), котлован / начало строительства(маркеры),
    признак приёма денег(старт продаж, бронь, ДДУ, рассрочка, взнос - как в "Проверке по рекламе")."""
    base = {"adId": f.id, "date": f.date, "platform": f.platform, "account": f.account, "basis": basis}
    out: list[Obj] = []
    if f.stage == "completed" and f.stage_quote:
        out.append({**base, "kind": "completed", "quote": f.stage_quote, "via": "claude"})
    adv = _q(f, ad, ADVANCED_MARKERS)
    m = FRAME_RE.search(f"{ad.text or ''}\n{ad.ocr or ''}") if ad is not None and not adv else None
    if m:
        adv = [(m.group(0), screening._short(marker_quote(ad.text, m.group(0)) or marker_quote(ad.ocr, m.group(0))
                                             or m.group(0)))]
    for _, q in adv[:1]:
        out.append({**base, "kind": "advanced", "quote": q, "via": "rules"})
    if f.stage == "under_construction" and f.stage_quote:
        out.append({**base, "kind": "building", "quote": f.stage_quote, "via": "claude"})
    elif f.building:
        out.append({**base, "kind": "building", "quote": f.building[0][1], "via": "rules"})
    early = _q(f, ad, EARLY_MARKERS)
    if early:
        out.append({**base, "kind": "early", "quote": early[0][1], "via": "rules"})
    if f.concerning:
        out.append({**base, "kind": "money", "quote": f.concerning[0][1], "signal": f.concerning[0][0],
                    "via": "claude" if f.from_claude else "rules"})
    return out


def _months(a: date, b: date) -> int:
    return max(0, round((b - a).days / 30.4))


def _fmt(d: date | None) -> str:
    return d.strftime("%d.%m.%Y") if d else "дата не указана"


def compare(images: list[Obj], claims: list[Obj], start: str | None) -> list[Obj]:
    """Подсказки "снимок против рекламы"(без вердикта). kind: ahead - реклама заявляет каркас/сдачу, а на снимке
    пустая площадка или котлован; money_early - признак приёма денег при стадии до каркаса на снимке; behind - на снимке
    дом завершён(или каркас), а более поздняя реклама - "строится" / котлован / старт продаж; before_start - работы на
    снимке раньше начала стройки по документам; stale - заявления новее снимка больше чем на GRACE_DAYS - снимком не
    проверить; match - остальные заявления не противоречат снимку. factor - что сверить аналитику(подсказка, не балл)."""
    latest = next((x for x in images if x.get("role") == "latest"), images[0] if images else None)
    if latest is None:
        return []
    out: list[Obj] = []
    shot_day = _day(latest.get("acquired") or latest.get("releaseDate"))
    stage = latest.get("stage")
    lvl = LEVEL.get(stage or "")
    label = STAGE_LABELS.get(stage or "", stage or "")
    ref = {"releaseId": latest.get("releaseId"), "acquired": latest.get("acquired")}
    if lvl is None or shot_day is None:
        if claims:
            out.append({"kind": "unclear", "factor": None, **ref,
                        "text": "Стадию на снимке определить не удалось — сравнить с рекламой нельзя, нужен выезд или "
                                "фото-мониторинг"})
        return out
    stale: list[Obj] = []
    consistent = 0
    seen = set()

    def add(kind: str, c: Obj, factor: str | None, text: str) -> None:
        if (kind, c["adId"]) in seen:
            return
        seen.add((kind, c["adId"]))
        out.append({"kind": kind, "factor": factor, "adId": c["adId"], "quote": c["quote"], "adDate": c["date"],
                    **ref, "text": text})

    for c in claims:
        d = _day(c.get("date"))
        comparable = d is not None and d <= shot_day + timedelta(days=GRACE_DAYS)
        when = f"публикация от {_fmt(d)}"
        if c["kind"] in ("completed", "advanced") and lvl <= 1:
            if comparable:
                add("ahead", c, "R5", f"Реклама заявляет «{c['quote']}» ({when}), а на снимке от {_fmt(shot_day)} — "
                                      f"{label} (гипотеза ИИ). Сверьте заявленную и подтверждённую готовность (R5)")
            else:
                stale.append(c)
        elif c["kind"] == "money" and lvl <= 2:
            if comparable:
                add("money_early", c, "R1", f"Признак приёма денег «{c['quote']}» ({when}) при стадии на снимке от "
                                            f"{_fmt(shot_day)} — {label}: до возведения каркаса деньги дольщиков "
                                            "привлекаются только с гарантией Единого оператора или участием банка "
                                            "(ст. 7 п. 1) — сверьте основание (R1)")
            else:
                stale.append(c)
        elif c["kind"] in ("building", "early") and d is not None and d >= shot_day and (
                lvl == 5 or (c["kind"] == "early" and lvl >= 3)):
            add("behind", c, "R5", f"На снимке от {_fmt(shot_day)} — {label} (гипотеза ИИ), а реклама позже "
                                   f"({when}) — «{c['quote']}»: возможно, другая очередь или неточная точка ЖК, либо "
                                   "готовый дом продаётся как строящийся. Сверьте стадию и очередь (R5)")
        else:
            consistent += 1
    start_day = _day(start)
    for x in images:
        xd, xl = _day(x.get("acquired") or x.get("releaseDate")), LEVEL.get(x.get("stage") or "")
        if start_day and xd and xl and xd < start_day and x.get("confidence") != "low":
            out.append({"kind": "before_start", "factor": None, "releaseId": x.get("releaseId"),
                        "acquired": x.get("acquired"),
                        "text": f"На снимке от {_fmt(xd)} — {STAGE_LABELS[x['stage']]}, хотя начало стройки по "
                                f"документам — {_fmt(start_day)}: возможно, соседний дом или прежняя очередь в кольце, "
                                "либо работы до документа. Сверьте по снимку и документам"})
            break
    if stale:
        newest = max(stale, key=lambda c: c.get("date") or "")
        nd = _day(newest.get("date"))
        out.append({"kind": "stale", "factor": None, **ref, "adId": newest["adId"], "quote": newest["quote"],
                    "adDate": newest["date"],
                    "text": f"Снимок от {_fmt(shot_day)} старше рекламы на {_months(shot_day, nd) if nd else '?'} мес. "
                            f"({len(stale)} {_plural(len(stale), 'заявление', 'заявления', 'заявлений')} о стадии): "
                            "по снимку не проверить — нужен свежий снимок, выезд или фото-мониторинг"})
    if consistent and not any(h["kind"] in ("ahead", "money_early", "behind") for h in out):
        out.append({"kind": "match", "factor": None, **ref,
                    "text": f"Заявления рекламы о стадии ({consistent}) не противоречат снимку от {_fmt(shot_day)} — "
                            f"{label}"})
    return out


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


CLAIM_ORDER = {"completed": 0, "advanced": 1, "early": 2, "money": 3, "building": 4}


def claims_for(db: Session, cid: str, max_claims: int = 12) -> list[Obj]:
    """Заявления рекламы ЖК о стадии: публикации, отнесённые к ЖК так же, как в "Проверке по рекламе"; сначала сдача,
    каркас, котлован и приём денег, затем "строится", внутри вида - новые первыми; повторы цитаты - один раз."""
    facts = screening.load_facts(db)
    mine = [(f, f.targets()[cid]) for f in facts.values() if cid in f.targets()]
    ads = {a.id: a for a in db.scalars(select(models.Ad).where(models.Ad.id.in_([f.id for f, _ in mine])))} if mine else {}
    out: list[Obj] = []
    for f, basis in mine:
        out.extend(claims_of(f, ads.get(f.id), basis))
    # заявления о стадии важнее "строится", внутри вида - новые первыми; одна цитата в разных площадках - один раз
    out.sort(key=lambda c: c.get("date") or "", reverse=True)
    out.sort(key=lambda c: CLAIM_ORDER.get(c["kind"], 9))
    seen, uniq = set(), []
    for c in out:
        k = (c["kind"], " ".join(c["quote"].lower().split()))
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq[:max_claims]


def view(db: Session, c: models.Complex, store: Obj | None = None) -> Obj:
    store = store if store is not None else load_store()
    images = [{**x, "stageLabel": STAGE_LABELS.get(x.get("stage") or "", x.get("stage"))} for x in items_for(store, c.id)]
    claims = [{**x, "kindLabel": CLAIM_LABELS[x["kind"]]} for x in claims_for(db, c.id)]
    hints = compare(images, claims, c.start or None)
    return {"complexId": c.id, "images": images, "claims": claims, "hints": hints,
            "discrepancy": any(h["kind"] in ("ahead", "money_early", "behind") for h in hints),
            "promptVersion": store.get("promptVersion") or PROMPT_VERSION, "disclaimer": DISCLAIMER,
            "note": None if images else ("Снимок этого ЖК ещё не анализировался"
                                         if c.lat is not None else "У ЖК нет координат — снимка нет")}


router = APIRouter()


@router.get("/api/complexes/{cid}/sat-stage")
def sat_stage(cid: str, db: Session = Depends(get_db)):
    """Стадия на снимке(гипотеза ИИ из shared/sat_stage.json), заявления рекламы о стадии и подсказки-расхождения."""
    c = db.get(models.Complex, cid)
    if c is None:
        raise HTTPException(404, "ЖК не найден")
    return view(db, c)


# ---------- командная строка ----------

def main(argv: list[str] | None = None) -> int:
    from .db import SessionLocal

    ap = argparse.ArgumentParser(prog="python -m app.satstage", description="Стадия стройки на снимках Wayback (ИИ)")
    ap.add_argument("--only", default="", help="ID ЖК через запятую")
    ap.add_argument("--limit", type=int, default=MAX_REQUESTS, help=f"запросов за запуск (не больше {MAX_REQUESTS})")
    ap.add_argument("--max-usd", type=float, default=MAX_USD, help="предел расхода за запуск, $")
    ap.add_argument("--force", action="store_true", help="отправить и уже проанализированные снимки")
    ap.add_argument("--dry-run", action="store_true", help="скачать и склеить тайлы без запроса к модели")
    ap.add_argument("--save-images", default="", help="каталог для склеенных снимков (JPEG) — для проверки глазами")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # без строки на каждый тайл
    if a.save_images:
        out = Path(a.save_images)
        out.mkdir(parents=True, exist_ok=True)
        orig = compose

        def saving(release, lat, lng, fetch=None):
            jpeg, got = orig(release, lat, lng, fetch)
            (out / f"{release}-{lat:.5f}-{lng:.5f}.jpg").write_bytes(jpeg)
            return jpeg, got
        globals()["compose"] = saving
    with SessionLocal() as db:
        rep = run(db, only=[x for x in a.only.split(",") if x] or None, limit=a.limit, max_usd=a.max_usd,
                  force=a.force, dry_run=a.dry_run)
    for k in ("done", "cached", "failed", "skipped"):
        for x in rep[k]:
            print(f"[{k}] {x}")
    print(f"запросов: {rep['requests']}, расход: ${rep['costUsd']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
