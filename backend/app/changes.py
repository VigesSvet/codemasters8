""""Удалено в источнике" и история изменений собранных публикаций - по уже хранимым данным, без новых таблиц.

Удаление в источнике(source_status) - по отсутствию в сборах. Сборщики Instagram и TikTok берут у каждого аккаунта
окно последних публикаций(Instagram Graph - последние N медиа аккаунта), каждый запуск обновляет у увиденной публикации
last_run_id. Публикация считается удалённой(deleted), если:
- позже её аккаунт собирался успешно(у других его публикаций last_run_id новее) - аккаунт в сборе есть;
- она не старше самой старой публикации аккаунта, увиденной в последнем таком запуске, - то есть была бы в окне сбора;
- после неё прошло не меньше MIN_MISSED завершённых запусков этого подключения(один пропуск - ещё не удаление: сбой
  ответа; идущий запуск не считается - до публикации он может ещё дойти).
Иначе - present(видели в последнем сборе аккаунта) или unknown с причиной: вытеснена из окна новыми публикациями,
аккаунт позже не собирался, пропущена в одном запуске, площадка без окна по аккаунту. Krisha - всегда unknown: выдача -
первые страницы поиска(порядок меняется, объявление может уйти дальше), карточки закрыты проверкой доступа(HTTP 468),
поэтому ни отсутствие в выдаче, ни запрос карточки снятия не доказывают. Ручные и демо-публикации - unknown.
Копия сохранена: текст, файлы и версии остаются в БД; savedAt - когда получена последняя версия.

Изменения(recent_changes) - пары соседних версий ingest_versions: цена, удалённые и добавленные фрагменты текста
(пословный diff по LCS), удалённые условия оплаты(словарь markers.money модели риска и суммы), смена файлов.
"""
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .risk_model import MARKERS

Obj = dict[str, Any]
MIN_MISSED = 2  # запусков подключения без публикации, после которых отсутствие - удаление
COUNTED_RUNS = ("succeeded", "partial")  # завершённые сборы, в которых публикацию не нашли
WINDOWED = ("instagram", "tiktok")  # сборщики с окном последних публикаций по аккаунту
FRAGMENT_MAX = 160
FRAGMENTS = 5
DIFF_CELLS = 1_000_000  # предел таблицы LCS: длиннее - середина целиком "удалено → добавлено"
_TOKEN = re.compile(r"\S+|\s+")
_MONEY = re.compile(r"\d[\d\s  ]*(?:[.,]\d+)?\s*(?:₸|тг|тенге|млн|тыс|%|\$)", re.I)
TERMS = [*MARKERS["money"], "старт продаж", "скидк", "акци"]


# ---------- пословный diff ----------

def tokens(text: str | None) -> list[str]:
    """Слова и пробелы по порядку: склеенные обратно дают исходный текст."""
    return _TOKEN.findall(text or "")


def diff_words(a: str | None, b: str | None) -> list[tuple[str, str]]:
    """Пословный diff: [(op, текст)], op - eq | del | add; соседние куски одного вида склеены. Пробелы сравниваются
    как слова, но разница только в пробелах - eq. LCS по таблице; общие начало и конец отрезаются заранее."""
    ta, tb = tokens(a), tokens(b)
    key = lambda t: " " if t.isspace() else t
    ka, kb = [key(t) for t in ta], [key(t) for t in tb]
    i = 0
    while i < len(ka) and i < len(kb) and ka[i] == kb[i]:
        i += 1
    j = 0
    while j < len(ka) - i and j < len(kb) - i and ka[-1 - j] == kb[-1 - j]:
        j += 1
    ma, mb = ka[i:len(ka) - j], kb[i:len(kb) - j]
    ops: list[tuple[str, str]] = [("eq", t) for t in tb[:i]]
    if len(ma) * len(mb) > DIFF_CELLS:
        ops += [("del", t) for t in ta[i:len(ta) - j]] + [("add", t) for t in tb[i:len(tb) - j]]
    else:
        n, m = len(ma), len(mb)
        L = [[0] * (m + 1) for _ in range(n + 1)]
        for x in range(n - 1, -1, -1):
            row, nxt = L[x], L[x + 1]
            for y in range(m - 1, -1, -1):
                row[y] = nxt[y + 1] + 1 if ma[x] == mb[y] else max(nxt[y], row[y + 1])
        x = y = 0
        while x < n and y < m:
            if ma[x] == mb[y]:
                ops.append(("eq", tb[i + y]))
                x += 1
                y += 1
            elif L[x + 1][y] >= L[x][y + 1]:
                ops.append(("del", ta[i + x]))
                x += 1
            else:
                ops.append(("add", tb[i + y]))
                y += 1
        ops += [("del", t) for t in ta[i + x:len(ta) - j]] + [("add", t) for t in tb[i + y:len(tb) - j]]
    ops += [("eq", t) for t in tb[len(tb) - j:]]
    return _merge(ops)


def _merge(ops: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Склеить соседние куски одного вида; пробел между двумя удалёнными(добавленными) словами - к ним же."""
    ops = list(ops)
    out: list[tuple[str, str]] = []
    for k, (kind, t) in enumerate(ops):
        op = kind
        if op == "eq" and t.isspace() and out and out[-1][0] != "eq" and k + 1 < len(ops) and ops[k + 1][0] == out[-1][0]:
            op = out[-1][0]
        if out and out[-1][0] == op:
            out[-1] = (op, out[-1][1] + t)
        else:
            out.append((op, t))
    return out


def _short(s: str, n: int = FRAGMENT_MAX) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def fragments(ops: Sequence[tuple[str, str]], op: str) -> list[str]:
    return [_short(t) for o, t in ops if o == op and t.strip()][:FRAGMENTS]


def hunks(ops: Sequence[tuple[str, str]], context: int = 4, limit: int = FRAGMENTS) -> list[Obj]:
    """Правки с контекстом: {before, removed, added, after} - до context слов совпадающего текста с каждой стороны."""
    out: list[Obj] = []
    k = 0
    while k < len(ops) and len(out) < limit:
        if ops[k][0] == "eq":
            k += 1
            continue
        h = {"before": "", "removed": "", "added": "", "after": ""}
        if k and ops[k - 1][0] == "eq":
            w = ops[k - 1][1].split()
            h["before"] = ("… " if len(w) > context else "") + " ".join(w[-context:])
        while k < len(ops) and ops[k][0] != "eq":
            h["removed" if ops[k][0] == "del" else "added"] += ops[k][1]
            k += 1
        if k < len(ops):
            w = ops[k][1].split()
            h["after"] = " ".join(w[:context]) + (" …" if len(w) > context else "")
        out.append({x: _short(v) for x, v in h.items()})
    return out


def terms_in(text: str) -> list[str]:
    """Условия оплаты и суммы во фрагменте: маркеры словаря и суммы("200 000 ₸", "30%")."""
    low = text.lower()
    found = [w for w in TERMS if w.lower() in low]
    found = [w for w in found if not any(o != w and w.lower() in o.lower() for o in found)]
    return found + [" ".join(m.group(0).split()) for m in _MONEY.finditer(text)]


def change_of(prev: Obj, cur: Obj) -> Obj:
    """Что изменилось между версиями: цена, удалённые и добавленные фрагменты, правки с контекстом(hunks), удалённые
    условия, файлы."""
    ops = diff_words(prev.get("text"), cur.get("text"))
    removed, added = fragments(ops, "del"), fragments(ops, "add")
    gone = []
    for t in removed:
        for x in terms_in(t):
            if x not in gone:
                gone.append(x)
    kinds = []
    if (prev.get("price") or None) != (cur.get("price") or None):
        kinds.append("price")
    if removed:
        kinds.append("text_removed")
    if added:
        kinds.append("text_added")
    if gone:
        kinds.append("terms_removed")
    if sorted(prev.get("mediaKeys") or []) != sorted(cur.get("mediaKeys") or []):
        kinds.append("media")
    return {"fromVersion": prev["version"], "toVersion": cur["version"], "at": cur.get("collectedAt"),
            "priceFrom": prev.get("price"), "priceTo": cur.get("price"), "removed": removed, "added": added,
            "termsRemoved": gone[:FRAGMENTS], "kinds": kinds, "hunks": hunks(ops)}


# ---------- удалено в источнике ----------

def _ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _iso(v: Any) -> str | None:
    d = _ts(v)
    return d.isoformat(timespec="seconds") if d else None


def classify(item: Obj, siblings: list[Obj], runs: list[Obj]) -> Obj:
    """Статус в источнике по данным сбора(без сети). item / siblings - {collector, account, lastRunId, publishedAt,
    collectedAt}; siblings - публикации того же подключения и аккаунта(вместе с item или без); runs - запуски этого
    подключения {id, status, trigger, finishedAt, createdAt}. Ответ - {status, how, checkedAt, lastSeenAt, missedRuns}."""
    seen = _iso(item.get("collectedAt"))
    base = {"lastSeenAt": seen, "missedRuns": 0}
    col = item.get("collector") or ""
    if col not in WINDOWED:
        how = ("Krisha: выдача — первые страницы поиска, карточки закрыты проверкой доступа — отсутствие в выдаче "
               "не доказывает снятия" if col == "krisha" else
               "Подключение не собирает окно публикаций по аккаунту — отсутствие в сборе не проверяется")
        return {"status": "unknown", "how": how, "checkedAt": None, **base}
    mine = item.get("lastRunId") or 0
    others = [s for s in siblings if s is not item and (s.get("lastRunId") or 0)]
    latest = max([s["lastRunId"] for s in others] + [mine])
    by_id = {r["id"]: r for r in runs}
    lr = by_id.get(latest) or {}
    checked = _iso(lr.get("finishedAt") or lr.get("createdAt")) or seen
    missed = sum(1 for r in runs if mine < r["id"] <= latest and r.get("trigger") != "test"
                 and r.get("status") in COUNTED_RUNS) if latest > mine else 0
    if missed == 0:  # после неё не было завершённого сбора аккаунта(или идёт сбор, который до неё ещё не дошёл)
        return {"status": "present", "how": "Найдена в последнем сборе аккаунта", "checkedAt": seen, **base}
    base["missedRuns"] = missed
    window = [s for s in others if s.get("lastRunId") == latest]
    oldest = min((d for d in (_ts(s.get("publishedAt")) for s in window) if d), default=None)
    pub = _ts(item.get("publishedAt"))
    if oldest is None or pub is None:
        return {"status": "unknown", "how": "Нет времени публикации — не сравнить с окном сбора", "checkedAt": checked,
                **base}
    if pub < oldest:
        return {"status": "unknown", "how": (f"Вытеснена из окна сбора: аккаунт опубликовал новее, в последнем сборе — "
                                             f"{len(window)} последних публикаций"), "checkedAt": checked, **base}
    if missed < MIN_MISSED:
        return {"status": "unknown", "how": (f"Не найдена в {missed} последнем сборе аккаунта, хотя попадает в окно — "
                                             "ждём подтверждения следующим сбором"), "checkedAt": checked, **base}
    return {"status": "deleted", "how": (f"Не найдена в {missed} последних сборах аккаунта, хотя новее самой старой "
                                         f"публикации окна ({len(window)} шт.) — вероятно, удалена или скрыта автором"),
            "checkedAt": checked, **base}


def _item_obj(i: models.IngestItem) -> Obj:
    return {"id": i.id, "adId": i.ad_id, "collector": i.collector, "account": i.account, "lastRunId": i.last_run_id,
            "publishedAt": i.published_at, "collectedAt": i.collected_at, "platform": i.platform, "url": i.url}


def _runs(db: Session, collectors: Iterable[str]) -> dict[str, list[Obj]]:
    R = models.CollectionRun
    out: dict[str, list[Obj]] = {}
    cols = list(set(collectors))
    if not cols:
        return out
    q = select(R.id, R.collector, R.status, R.trigger, R.finished_at, R.created_at).where(R.collector.in_(cols))
    for r in db.execute(q.order_by(R.id)):
        out.setdefault(r.collector, []).append({"id": r.id, "status": r.status, "trigger": r.trigger,
                                                "finishedAt": r.finished_at, "createdAt": r.created_at})
    return out


def _saved(db: Session, item_ids: list[int]) -> dict[int, Obj]:
    """Последняя версия публикации: когда получена копия и номер версии."""
    V = models.IngestVersion
    out: dict[int, Obj] = {}
    if not item_ids:
        return out
    q = select(V.item_id, V.version, V.collected_at).where(V.item_id.in_(item_ids)).order_by(V.item_id, V.version)
    for item_id, version, collected_at in db.execute(q):
        out[item_id] = {"savedAt": _iso(collected_at), "version": version}
    return out


def statuses(db: Session, items: list[models.IngestItem] | None = None) -> dict[str, Obj]:
    """Статус в источнике публикаций приёма: {ad_id: {...}}; items - по каким(иначе все)."""
    I = models.IngestItem
    rows = items if items is not None else list(db.scalars(select(I)))
    if not rows:
        return {}
    # публикации тех же(подключение, аккаунт): одним запросом на подключение, а не на каждый аккаунт
    accounts: dict[str, set[str]] = {}
    for r in rows:
        accounts.setdefault(r.collector, set()).add(r.account)
    groups: dict[tuple[str, str], list[Obj]] = {}
    by_id: dict[int, Obj] = {}
    for col, accs in accounts.items():
        for x in db.scalars(select(I).where(I.collector == col, I.account.in_(accs))):
            obj = by_id[x.id] = _item_obj(x)
            groups.setdefault((col, x.account), []).append(obj)
    runs = _runs(db, accounts)
    saved = _saved(db, [r.id for r in rows])
    out: dict[str, Obj] = {}
    for r in rows:
        me = by_id.get(r.id) or _item_obj(r)
        sib = groups.get((r.collector, r.account)) or [me]
        st = classify(me, sib, runs.get(r.collector, []))
        out[r.ad_id] = {**st, **saved.get(r.id, {"savedAt": _iso(r.collected_at), "version": r.version}),
                        "collector": r.collector}
    return out


def source_status(db: Session, ad_id: str) -> Obj:
    item = db.scalar(select(models.IngestItem).where(models.IngestItem.ad_id == ad_id))
    if item is None:
        return {"status": "unknown", "how": "Публикация внесена вручную или из демо-набора — источник не отслеживается",
                "checkedAt": None, "lastSeenAt": None, "savedAt": None, "missedRuns": 0, "collector": None}
    return statuses(db, [item])[ad_id]


# ---------- лента изменений ----------

def _version_obj(v: Any) -> Obj:
    """Версия(строка IngestVersion или её столбцы) → {version, text, price, mediaKeys, collectedAt}."""
    return {"version": v.version, "text": v.text, "price": v.price, "mediaKeys": list(v.media_keys or []),
            "collectedAt": _iso(v.collected_at)}


def recent_changes(db: Session, days: int = 30, limit: int = 100, now: datetime | None = None) -> Obj:
    """Изменения содержимого(пары соседних версий) за days дней и все удалённые в источнике публикации, новые первыми."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    I, V, A = models.IngestItem, models.IngestVersion, models.Ad
    items = {i.id: i for i in db.scalars(select(I))}
    multi = [i.id for i in items.values() if (i.version or 1) > 1]
    vers: dict[int, list[Obj]] = {}
    if multi:
        q = (select(V.item_id, V.version, V.text, V.price, V.media_keys, V.collected_at)
             .where(V.item_id.in_(multi)).order_by(V.item_id, V.version))
        for v in db.execute(q):
            vers.setdefault(v.item_id, []).append(_version_obj(v))

    pairs: list[tuple[models.IngestItem, Obj, Obj]] = []
    for iid, vs in vers.items():
        for prev, cur in pairwise(vs):
            at = _ts(cur.get("collectedAt"))
            if at is None or at >= since:
                pairs.append((items[iid], prev, cur))
    # новые первыми; пословный diff(LCS) - только пока не набралось limit изменений, а не для всех пар за days дней
    pairs.sort(key=lambda p: p[2].get("collectedAt") or "", reverse=True)
    changes: list[tuple[models.IngestItem, Obj]] = []
    for i, prev, cur in pairs:
        if len(changes) >= limit:
            break
        ch = change_of(prev, cur)
        if ch["kinds"]:
            changes.append((i, ch))
    st = statuses(db, list(items.values()))
    deleted = [(i, st[i.ad_id]) for i in items.values() if st.get(i.ad_id, {}).get("status") == "deleted"]
    deleted.sort(key=lambda d: d[1].get("lastSeenAt") or "", reverse=True)
    deleted = deleted[:limit]

    # ЖК и начало текста - только у публикаций, попавших в ответ
    shown = {i.ad_id for i, _ in changes + deleted}
    ads = {a.id: a for a in db.execute(select(A.id, A.complex_id, A.text).where(A.id.in_(shown)))} if shown else {}

    def head(i: models.IngestItem) -> Obj:
        a = ads.get(i.ad_id)
        return {"adId": i.ad_id, "platform": i.platform, "account": i.account, "url": i.url,
                "complexId": a.complex_id if a is not None else None,
                "text": _short((a.text if a is not None else "") or "", 200)}

    counts: dict[str, int] = {"present": 0, "deleted": 0, "unknown": 0}
    for s in st.values():
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {"generatedAt": now.isoformat(timespec="seconds"), "days": days,
            "changes": [{**head(i), **ch} for i, ch in changes],
            "deleted": [{**head(i), **s} for i, s in deleted], "sourceStatus": counts, "items": len(items),
            "limits": ("Удаление определяется по отсутствию в сборах Instagram и TikTok (окно последних публикаций "
                       "аккаунта); у Krisha, Telegram и ручных публикаций — не определяется. Изменения — по версиям, "
                       "которые сохранил приём: правка между двумя сборами без смены содержимого не видна.")}


router = APIRouter()


@router.get("/api/ads/{aid}/source-status")
def ad_source_status(aid: str, db: Session = Depends(get_db)):
    """Есть ли публикация в источнике: present | deleted | unknown, когда проверено, как, когда сохранена копия."""
    if db.get(models.Ad, aid) is None:
        raise HTTPException(404, "Публикация не найдена")
    return source_status(db, aid)


@router.get("/api/changes")
def changes(days: int = Query(30, ge=1, le=3650), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)):
    """Недавние изменения содержимого собранных публикаций(цена, условия, текст) и удалённые в источнике."""
    return recent_changes(db, days=days, limit=limit)
