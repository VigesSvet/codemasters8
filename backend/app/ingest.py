"""Единый приём публикаций(план §5): Publication → IngestItem + Ad + IngestVersion + MediaAsset.

Им пользуются все сборщики(RunContext.ingest), приём через API(POST /api/intake/publications) и бот заявок.
Дедупликация - по(пространство площадки, внешний ID): сначала INSERT … ON CONFLICT DO NOTHING(гонка двух сборщиков
без SAVEPOINT), затем:
- новая - публикация(статус "new", без привязки к ЖК), версия 1, файлы, задание анализа - в одной транзакции;
- та же(хеш содержимого не изменился) - только время "последний раз видели" и дозагрузка файлов, которые раньше не
  скачались; анализ - только если появились новые файлы;
- изменилась - новая версия(история хранится), текст публикации обновляется условным UPDATE, решения аналитика не
  трогаются(в журнале - "Решения аналитика сохранены - проверьте их по новой версии"), анализ - заново;
- принадлежит другому источнику - ничего не пишется(status "foreign").

ingest_publication работает только с БД(без сети): загрузки файлов - заранее, в resolve_media. Фиксирует транзакцию
вызывающий. Снимок ответа источника(raw) очищается от секретов и подписанных ссылок(scrub).
"""
import dataclasses
import hashlib
import json
import logging
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import ads_service, media, models
from .ads_service import COLLECTOR, MEDIA_REF, Actor, local_minutes, safe_url
from .ai import ocr as ocr_engine
from .integrations.base import MediaIn, Publication
from .integrations.http import redact_url
from .logs import redact
from .pdf import PLATFORMS

log = logging.getLogger("saq.ingest")

RAW_MAX = 64 * 1024
# Ключи снимка источника, которые не сохраняются(сравнение - по ключу без регистра и не-букв:
# accessToken → accesstoken)
SCRUB_EXACT = {"auth", "authorization", "bearer", "jwt", "user", "ipinfo", "config", "phone", "email", "cookie",
               "cookies"}
SCRUB_PARTS = ("token", "secret", "passw", "session", "apikey", "appkey", "signature", "csrf", "credential",
               "privatekey")
ATTEMPTS_MAX = 3  # попыток загрузки файла сборщиком; дальше файл остаётся "не загружен"
KEY_MAX = 255
_DEPTH = 40
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_URL = re.compile(r"^https?://", re.I)
_ERRORS = {"failed": "Файл не загружен", "unavailable": "Источник не отдал файл", "skipped": "Файл пропущен"}


@dataclass
class Known:
    """Что уже принято по внешнему ID: владелец, хеш и версия, какие файлы загружены и сколько было попыток у остальных,
    последняя версия содержимого(текст, цена, extra, ключи файлов)."""
    item_id: int
    ad_id: str
    collector: str
    content_hash: str
    version: int
    downloaded_keys: set[str]
    failed_keys: dict[str, int]  # ключ → попыток(failed / pending / unavailable / skipped)
    text: str
    price: Optional[str]
    extra: dict
    media_keys: list[str]

    def needs(self, key: str) -> bool:
        """Файл ещё нужно загрузить: не загружен и попыток меньше ATTEMPTS_MAX."""
        return key not in self.downloaded_keys and self.failed_keys.get(key, 0) < ATTEMPTS_MAX


@dataclass
class IngestResult:
    status: Literal["created", "updated", "unchanged", "foreign"]
    ad_id: str
    item_id: int
    version: int
    media_new: int  # файлов сохранено сейчас
    media_failed: int  # не загружено(failed / unavailable)
    analysis_queued: bool
    owner: Optional[str] = None  # foreign: подключение-владелец
    media_pending: int = 0  # ждут загрузки заданием анализа(ссылки приёма через API)


# ---------- нормализация ----------

def normalize_text(s: Any) -> str:
    """Текст публикации: переводы строк \\n, без NUL и управляющих символов, без пробелов по краям, до предела
    публикации(schemas.AD_TEXT_MAX, по границе строки/слова)."""
    if s is None:
        return ""
    t = str(s).replace("\r\n", "\n").replace("\r", "\n")
    return ocr_engine.clip(_CTRL.sub("", t).strip())


def _line(s: Any, limit: int) -> str:
    """Однострочное поле(аккаунт, цена): пробелы схлопнуты, без управляющих символов, не длиннее limit."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", _CTRL.sub("", str(s))).strip()[:limit]


def _walk(v: Any, depth: int, redacting: bool) -> Any:
    """Значение → JSON-совместимое(datetime → ISO, NaN → None, bytes → "<N байт>"); redacting - ещё и очистка: ключи
    секретов отбрасываются, строки маскируются, у ссылок убираются query и fragment."""
    if depth > _DEPTH:
        return "…"
    if isinstance(v, Mapping):
        return {str(k)[:128]: _walk(x, depth + 1, redacting) for k, x in v.items()
                if not (redacting and _secret_key(k))}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_walk(x, depth + 1, redacting) for x in v]
    if isinstance(v, str):
        if not redacting:
            return v
        # строка длиннее RAW_MAX всё равно приведёт к {"truncated": …} - маскируется только её начало, чтобы цена
        # маскирования не зависела от размера присланного тела(до 2 МБ)
        v = v[:RAW_MAX + 1]
        s = v.strip()
        return redact_url(s) if _URL.match(s) else redact(v, limit=None)
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} байт>"
    return redact(str(v)[:RAW_MAX + 1], limit=None) if redacting else str(v)


def _dict(v: Any, redacting: bool) -> dict:
    out = _walk(v, 0, redacting) if isinstance(v, Mapping) else {}
    return out if isinstance(out, dict) else {}


def _secret_key(k: Any) -> bool:
    nk = re.sub(r"[^a-z0-9]", "", str(k).lower())
    return nk in SCRUB_EXACT or any(p in nk for p in SCRUB_PARTS)


def scrub(raw: Any) -> dict:
    """Снимок ответа источника без секретов: ключи токенов, сессий, подписей, телефонов, e-mail, cookie, user/config
    (рекурсивно) отбрасываются, строки маскируются(logs.redact), ссылки - без query и fragment(подписанные ссылки
    CDN). Больше RAW_MAX(64 КБ JSON) - только {"truncated": true, "keys": [...]}."""
    if raw is None:
        return {}
    out = _walk(raw, 0, True)
    if not isinstance(out, dict):
        out = {"value": out}
    if len(json.dumps(out, ensure_ascii=False).encode()) > RAW_MAX:
        return {"truncated": True, "keys": sorted(out)[:200]}
    return out


def _unique_media(items: list[MediaIn]) -> list[MediaIn]:
    """Файлы публикации с ключом не длиннее KEY_MAX, без повторов ключа(первый - в силе)."""
    out, seen = [], set()
    for m in items or []:
        key = str(m.key or "").strip()[:KEY_MAX]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(m if key == m.key else dataclasses.replace(m, key=key))
    return out


def content_hash(pub: Publication) -> str:
    """SHA-256 содержимого: текст, аккаунт, ссылка, цена, ключи файлов(без порядка), extra. Время и raw не входят."""
    payload = {"text": normalize_text(pub.text), "account": _line(pub.account, 255), "url": safe_url(pub.url),
               "price": _line(pub.price, 64) or None, "media": sorted(m.key for m in _unique_media(pub.media)),
               "extra": _dict(pub.extra, False)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ---------- что уже принято ----------

def known(db: Session, namespace: str, external_id: str) -> Optional[Known]:
    I, A, V = models.IngestItem, models.MediaAsset, models.IngestVersion
    item = db.scalar(select(I).where(I.platform == namespace, I.external_id == external_id))
    if item is None:
        return None
    downloaded: set[str] = set()
    failed: dict[str, int] = {}
    rows = db.execute(select(A.source_key, A.status, A.attempts)
                      .where(A.item_id == item.id, A.parent_id.is_(None), A.source_key.is_not(None)))
    for key, status, attempts in rows:
        if status == "downloaded":
            downloaded.add(key)
        else:
            failed[key] = max(failed.get(key, 0), attempts or 0)
    ver = db.scalar(select(V).where(V.item_id == item.id, V.version == item.version))
    return Known(item.id, item.ad_id, item.collector, item.content_hash, item.version, downloaded, failed,
                 ver.text if ver else "", ver.price if ver else None, dict(ver.extra or {}) if ver else {},
                 list(ver.media_keys or []) if ver else [])


def resolve_media(pub: Publication, k: Optional[Known]) -> Publication:
    """Сеть, без БД: ленивые загрузки(MediaIn.fetch) вызываются только для файлов, которые ещё нужны(новая публикация
    или k.needs(key)); исключение загрузчика - файл "failed" с причиной, публикация принимается без него."""
    out = []
    for m in pub.media:
        if m.fetch is None or m.stored is not None or (k is not None and not k.needs(m.key)):
            out.append(m)
            continue
        try:
            got = m.fetch()
            if not isinstance(got, MediaIn):
                raise TypeError("загрузчик не вернул файл")
        except Exception as e:  # noqa: BLE001 - сбой одного файла не останавливает приём публикации
            log.warning("Файл %s публикации %s не загружен: %s", m.key, pub.external_id, type(e).__name__)
            got = MediaIn(key=m.key, kind=m.kind, role=m.role, status="failed",
                          error=redact(str(e) or type(e).__name__) or "Файл не загружен")
        out.append(dataclasses.replace(got, key=m.key, fetch=None, role=got.role or m.role,
                                       origin_url=got.origin_url or m.origin_url,
                                       origin_ref=got.origin_ref or m.origin_ref))
    return dataclasses.replace(pub, media=out)


# ---------- файлы ----------

def _new_asset(item: models.IngestItem, m: MediaIn, pos: int, now: datetime) -> models.MediaAsset:
    origin = m.origin_url or m.url
    a = models.MediaAsset(id=uuid.uuid4().hex, ad_id=item.ad_id, item_id=item.id, position=pos,
                          kind=m.kind if m.kind in ("image", "video") else "image",
                          role=(m.role or None) and m.role[:16],
                          source_key=m.key, current=True, origin_ref=(m.origin_ref or None) and m.origin_ref[:255],
                          origin_url=redact_url(origin) if origin else None, meta=_dict(m.meta, True),
                          attempts=0, status="pending", created_at=now)
    if _apply(a, m, now) == "none":  # ни байтов, ни ссылки, ни причины - загрузку пропустили(сеть не вызывалась)
        a.status, a.error = "failed", "Файл не загружен при сборе"
    return a


def _apply(a: models.MediaAsset, m: MediaIn, now: datetime) -> str:
    """Состояние файла по MediaIn: downloaded | failed | unavailable | skipped | pending(ссылка приёма) | none."""
    if m.stored is not None:
        s = m.stored
        a.status, a.error = "downloaded", None
        a.storage_key, a.sha256, a.size, a.mime = s.storage_key, s.sha256, s.size, s.mime
        a.kind = "video" if s.mime in media.VIDEO_TYPES else "image"
        a.downloaded_at = now
        a.attempts = (a.attempts or 0) + 1
        a.meta = {k: v for k, v in (a.meta or {}).items() if k not in ("src", "retryAt")}
        return "downloaded"
    if m.status in _ERRORS:
        a.status, a.error = m.status, redact(m.error or _ERRORS[m.status])
        if m.status != "skipped":
            a.attempts = (a.attempts or 0) + 1
        return m.status
    if m.url and a.status in ("failed", "unavailable") and (a.attempts or 0) < ATTEMPTS_MAX:
        # приём через API прислал ссылку снова(например, свежую подписанную вместо истёкшей): файл, который не
        # загрузился, загружается ещё раз - всего не больше ATTEMPTS_MAX попыток, как у сборщиков(§7.3 п. 3)
        a.status, a.error = "pending", None
        a.meta = {k: v for k, v in (a.meta or {}).items() if k != "retryAt"}
    if m.url and a.status == "pending":  # приём через API: файл загрузит анализ; meta.src не выводится и не логируется
        a.meta = {**(a.meta or {}), "src": m.url}
        a.origin_ref = a.origin_ref or "intake"
        return "pending"
    return "none"


def _cover(assets) -> Optional[str]:
    """Изображение публикации: первое загруженное текущее изображение не-обложка, затем обложка."""
    imgs = [a for a in sorted(assets, key=lambda a: a.position)
            if a.status == "downloaded" and a.kind == "image" and a.current]
    pick = next((a for a in imgs if a.role != "cover"), None) or (imgs[0] if imgs else None)
    return f"/api/media/{pick.id}" if pick else None


def _top_assets(db: Session, item_id: int) -> dict[str, models.MediaAsset]:
    A = models.MediaAsset
    rows = db.scalars(select(A).where(A.item_id == item_id, A.parent_id.is_(None)).order_by(A.position, A.created_at))
    return {a.source_key: a for a in rows if a.source_key}


def _count(outcomes: list[str]) -> tuple[int, int, int]:
    return (outcomes.count("downloaded"), outcomes.count("failed") + outcomes.count("unavailable"),
            outcomes.count("pending"))


# ---------- приём ----------

def has_decisions(ad: models.Ad) -> bool:
    """По публикации уже есть решения аналитика: привязка, условия оплаты, отклонённые гипотезы, ответственный,
    статус."""
    return bool(ad.link_confirmed_by or ad.terms_review or ad.rejected_links or ad.assignee
                or ad.status not in ("new", "needs_check"))


def enqueue_analysis(db: Session, ad_id: str, item: Optional[models.IngestItem] = None, *,
                     payload: Optional[dict] = None, priority: int = 0) -> bool:
    """Задание анализа публикации(одно активное на публикацию) и состояние "в очереди" у записи приёма. payload и
    priority - у нового задания(дозапрос Claude, повторный анализ, пересчёт правил); активное задание остаётся как
    есть, но ждущий пересчёт правил(rulesOnly) становится полным анализом, если ставится обычный."""
    from . import jobs  # jobs импортирует обработчики, которые сами импортируют приём, - только здесь

    if item is None:
        item = db.scalar(select(models.IngestItem).where(models.IngestItem.ad_id == ad_id))
    job, created = jobs.enqueue(db, "analyze", ad_id=ad_id, content_version=item.version if item is not None else None,
                                dedupe_key=f"analyze:{ad_id}", payload=payload, priority=priority)
    if not created and job.status == "queued" and (job.payload or {}).get("rulesOnly") \
            and not (payload or {}).get("rulesOnly"):
        job.payload = {**{k: v for k, v in (job.payload or {}).items() if k not in ("rulesOnly", "reason")},
                       **(payload or {})}
        job.priority = max(job.priority or 0, priority)
    now = models.utcnow()
    if not created and job.status == "queued" and models.as_utc(job.run_after) > now:
        job.run_after = now  # ждущий повтора загрузки анализ: новое содержимое или файлы - анализировать сейчас
    if item is not None:
        item.analysis_status, item.analysis_stage, item.analysis_error = "queued", None, None
    return True


def _ensure_ad(db: Session, item: models.IngestItem, fields: dict, actor: Actor, at: datetime,
               note: Optional[str]) -> models.Ad:
    ad = db.get(models.Ad, item.ad_id)
    if ad is None:  # публикацию удалили вне приёма - создаётся заново с тем же id
        ad = ads_service.create_ad(db, fields, actor=actor, at=at, note=note, ad_id=item.ad_id)
    return ad


def _update_ad(db: Session, ad: models.Ad, values: dict) -> None:
    """Содержимое публикации из новой версии - условным UPDATE по прочитанному номеру версии решений(решение аналитика,
    принятое по прежней версии, получит 409). Поля решений не пишутся. Гонка - одно перечитывание."""
    A = models.Ad
    rev = ad.decision_rev or 0
    for _ in range(2):
        res = db.execute(update(A).where(A.id == ad.id, A.decision_rev == rev)
                         .values(**values, decision_rev=A.decision_rev + 1)
                         .execution_options(synchronize_session=False))
        if res.rowcount == 1:
            db.expire(ad)
            return
        rev = db.scalar(select(A.decision_rev).where(A.id == ad.id)) or 0
    raise RuntimeError(f"Публикацию {ad.id} одновременно меняют — новая версия не записана, повторите приём")


def ingest_publication(db: Session, pub: Publication, *, collector: str, run_id: Optional[int] = None,
                       actor: Actor = COLLECTOR, note: Optional[str] = None, external_run_id: Optional[str] = None,
                       raw_extra: Optional[dict] = None) -> IngestResult:
    """Публикация → БД в сессии db(без сети, без commit). Ошибка - исключение: вызывающий откатывает транзакцию,
    и публикация, запись приёма, версия и файлы не остаются наполовину."""
    I = models.IngestItem
    eid = str(pub.external_id or "").strip()
    if not eid or len(eid) > 128:
        raise ValueError("Внешний ID публикации пустой или длиннее 128 символов")
    ns = str(pub.namespace or pub.platform or "other")[:16]
    platform = str(pub.platform or "other")[:16]
    text = normalize_text(pub.text)
    account = _line(pub.account, 255)
    url = safe_url(pub.url)
    price = _line(pub.price, 64) or None
    reach = pub.reach if isinstance(pub.reach, int) and not isinstance(pub.reach, bool) and pub.reach >= 0 else None
    extra = _dict(pub.extra, False)
    published_at = models.as_utc(pub.published_at)
    now = models.as_utc(pub.collected_at) or models.utcnow()
    items = _unique_media(pub.media)
    keys = [m.key for m in items]
    h = content_hash(dataclasses.replace(pub, media=items))
    raw_in = dict(pub.raw) if isinstance(pub.raw, Mapping) else ({"value": pub.raw} if pub.raw is not None else {})
    raw = scrub({**raw_in, **(raw_extra or {})})
    ext_run = str(external_run_id)[:64] if external_run_id else None
    ad_fields = {"platform": platform, "account": account, "url": url, "text": text,
                 "date": local_minutes(published_at or now), "price": price, "reach": reach, "image": None}

    inserted = models.insert_ignore(db, I, {
        "platform": ns, "external_id": eid, "collector": collector, "ad_id": uuid.uuid4().hex, "account": account,
        "url": url, "published_at": published_at, "first_collected_at": now, "collected_at": now, "content_hash": h,
        "version": 1, "raw": raw, "external_run_id": ext_run, "last_run_id": run_id}, ("platform", "external_id"))
    item = db.scalar(select(I).where(I.platform == ns, I.external_id == eid).with_for_update()
                     .execution_options(populate_existing=True))

    def version_row(n: int) -> models.IngestVersion:
        return models.IngestVersion(item_id=item.id, version=n, content_hash=h, text=text, account=account, url=url,
                                    price=price, extra=extra, media_keys=keys, raw=raw, collected_at=now, run_id=run_id)

    if inserted:  # новая публикация: всё - в этой транзакции
        ad = ads_service.create_ad(db, ad_fields, actor=actor, at=now, note=note, ad_id=item.ad_id)
        db.add(version_row(1))
        assets = [_new_asset(item, m, pos, now) for pos, m in enumerate(items)]
        db.add_all(assets)
        ad.image = _cover(assets)
        enqueue_analysis(db, ad.id, item)
        db.flush()
        new, failed, pending = _count([a.status for a in assets])
        return IngestResult("created", ad.id, item.id, 1, new, failed, True, media_pending=pending)

    if item.collector != collector:  # собрана другим источником: не перезаписывается
        return IngestResult("foreign", item.ad_id, item.id, item.version, 0, 0, False, owner=item.collector)

    item.collected_at = now
    if run_id is not None:
        item.last_run_id = run_id
    if ext_run:
        item.external_run_id = ext_run
    ad = _ensure_ad(db, item, ad_fields, actor, now, note)
    assets = _top_assets(db, item.id)
    outcomes: list[str] = []
    retried = 0  # не загрузившиеся файлы, снова ждущие загрузки(приём через API прислал ссылку)
    for pos, m in enumerate(items):
        a = assets.get(m.key)
        if a is None:
            a = assets[m.key] = _new_asset(item, m, pos, now)
            db.add(a)
            outcomes.append(a.status)
            continue
        a.position = pos
        if a.status != "downloaded":
            before = a.status
            outcomes.append(_apply(a, m, now))
            retried += before in ("failed", "unavailable") and a.status == "pending"

    if item.content_hash == h:  # без изменений: только "последний раз видели" и дозагрузка файлов
        new, failed, pending = _count(outcomes)
        if ad.image is None and new:
            ad.image = _cover(assets.values())
        queued = bool(new or retried) and enqueue_analysis(db, ad.id, item)
        db.flush()
        return IngestResult("unchanged", ad.id, item.id, item.version, new, failed, queued, media_pending=pending)

    # изменилась: новая версия, история сохраняется, решения аналитика - нет
    decisions = has_decisions(ad)
    complex_id = ad.complex_id
    item.version += 1
    n = item.version
    item.content_hash, item.account, item.url, item.raw = h, account, url, raw
    item.published_at = published_at or item.published_at
    db.add(version_row(n))
    current = set(keys)
    for key, a in assets.items():
        a.current = key in current
    db.flush()
    A = models.MediaAsset
    for flag in (True, False):  # кадры видео следуют за своим видео
        ids = [a.id for a in assets.values() if bool(a.current) == flag]
        if ids:
            db.execute(update(A).where(A.parent_id.in_(ids)).values(current=flag)
                       .execution_options(synchronize_session=False))
    _update_ad(db, ad, {"text": text, "account": account, "url": url, "price": price, "reach": reach,
                        "collected_at": now})
    by_id = {a.id: a for a in assets.values()}
    ref = MEDIA_REF.match(ad.image or "")
    if ad.image is None or (ref and ref.group(1) in by_id and not by_id[ref.group(1)].current):
        ad.image = _cover(assets.values())
    what = f"Публикация изменилась в источнике ({PLATFORMS.get(platform, platform)}): версия {n}"
    if decisions:
        what += ". Решения аналитика сохранены — проверьте их по новой версии"
    ads_service._event(db, "system", actor.who, what, at=now, ad_id=ad.id, complex_id=complex_id)
    enqueue_analysis(db, ad.id, item)
    db.flush()
    new, failed, pending = _count(outcomes)
    return IngestResult("updated", ad.id, item.id, n, new, failed, True, media_pending=pending)
