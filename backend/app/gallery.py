"""Фото объекта для списка "Новостройки" и карточки ЖК: откуда снимки дома и чья это обложка.

Источники - по порядку обложки(gallery[0]):
- operator - фото, которое оператор загрузил сам(POST /api/complexes/{id}/gallery): MediaAsset origin_ref "gallery",
  source_key "gallery:{ЖК}:{uuid}", meta {complexId, by, at, note?};
- site - датированные снимки площадки фото-мониторинга(complexes.photos, app.verification), новые первыми;
- homeportal - фото карточки ЖК на Homeportal(api.homeportal.kz, objects-detail / illegal-objects) - фото или
  визуализация застройщика, не снимок площадки: MediaAsset origin_ref "homeportal:{id объекта}", source_key
  "homeportal:{ЖК}:{id фото}". Загружает POST /api/complexes/{id}/gallery/fetch или python -m app.gallery fetch;
- ad - изображения публикаций, отнесённых к ЖК "Проверкой по рекламе"(подтверждённая привязка - фактом, остальные -
  гипотезой: hypothesis = true), - тоже может быть визуализация из рекламы.
Обложку можно закрепить(POST /api/complexes/{id}/cover: src одного из фото; пустой - снова автоматическая) - это поле
complexes.cover. Фото на риск и "Проверку по рекламе" не влияют: снимок площадки с датой и активностью - отдельный путь
(POST /api/complexes/{id}/photos). Каждое действие - событие журнала объекта вида "данные" от имени сотрудника.
"""
import argparse
import logging
import re
import threading
import uuid
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import media, models, schemas
from .db import get_db, get_for_update
from .verification import complex_or_404, complex_row, data_event, form_errors

log = logging.getLogger("saq.gallery")
router = APIRouter(tags=["gallery"])

Obj = dict[str, Any]
OPERATOR = "gallery"  # origin_ref и role фото оператора
HP_SITE = "homeportal.kz"  # карточки объектов(ссылки записей реестра)
HP_HOST = f"api.{HP_SITE}"  # API и файлы фото
HP_API = f"https://{HP_HOST}/api/v1"
HP_SOURCES = ("homeportal", "homeportal_section")
HP_MAX = 8  # фото одного объекта Homeportal
# записи реестра загружает открытый импорт: ссылок у ЖК может быть сколько угодно, а запросов к Homeportal - нет
HP_PAGES_MAX = 3  # карточек Homeportal одного ЖК(у настоящих - 1-3), первые по порядку записей
HP_FILES_MAX = 16  # загрузок фото за одну подгрузку ЖК
_PHOTO_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
_OBJECT_ID = re.compile(r"[0-9]{1,18}")  # id объекта в ссылке на карточку: только цифры ASCII("١٢" - тоже isdigit)
AD_MAX = 6  # фото из рекламы в галерее ЖК
AD_PER_POST = 2
PLATFORMS = {"instagram": "Instagram", "telegram": "Telegram", "tiktok": "TikTok", "krisha": "Krisha.kz",
             "olx": "OLX", "facebook": "Facebook", "vk": "VK", "youtube": "YouTube", "site": "Сайт", "bot": "Бот"}
KIND_ORDER = {"operator": 0, "site": 1, "homeportal": 2, "ad": 3}
NOT_IN_GALLERY = "Такого фото нет среди фото объекта — обновите страницу"
NO_HOMEPORTAL = "У объекта нет карточки Homeportal в открытом реестре — фото можно загрузить вручную"


def _key_cid(key: str) -> str | None:
    parts = (key or "").split(":")
    return parts[1] if len(parts) >= 3 else None


def _ads_by_complex(attributed: dict[str, list[tuple[str, str]]]) -> dict[str, list[tuple[str, bool]]]:
    """ЖК → [(публикация, привязка подтверждена)]: подтверждённые первыми."""
    out: dict[str, list[tuple[str, bool]]] = {}
    for cid, items in attributed.items():
        out[cid] = sorted(((aid, kind == "confirmed") for aid, kind in items), key=lambda x: not x[1])
    return out


def collect(db: Session, complexes: Iterable[models.Complex],
            attributed: dict[str, list[tuple[str, str]]] | None = None) -> dict[str, list[Obj]]:
    """ЖК → фото объекта(GalleryPhotoOut), обложка первой: закреплённая, иначе по порядку источников."""
    cxs = list(complexes)
    ids = {c.id for c in cxs}
    A = models.MediaAsset
    own: dict[str, list[models.MediaAsset]] = {}
    q = select(A).where(A.status == "downloaded", A.ad_id.is_(None),
                        or_(A.source_key.like(f"{OPERATOR}:%"), A.source_key.like("homeportal:%")))
    for a in db.scalars(q.order_by(A.created_at.desc(), A.position)):
        cid = _key_cid(a.source_key)
        if cid in ids:
            own.setdefault(cid, []).append(a)
    by_cx = _ads_by_complex(attributed or {})
    ad_ids = {aid for cid in ids for aid, _ in by_cx.get(cid, [])}
    pics: dict[str, list[models.MediaAsset]] = {}
    ads: dict[str, models.Ad] = {}
    if ad_ids:
        for a in db.scalars(select(A).where(A.ad_id.in_(ad_ids), A.status == "downloaded", A.current.is_(True),
                                            A.kind == "image").order_by(A.position)):
            pics.setdefault(a.ad_id, []).append(a)
        ads = {x.id: x for x in db.scalars(select(models.Ad).where(models.Ad.id.in_(list(pics))))} if pics else {}
    out: dict[str, list[Obj]] = {}
    for c in cxs:
        items: list[Obj] = []
        for a in own.get(c.id, []):
            m = a.meta or {}
            if a.origin_ref == OPERATOR:
                items.append({"src": a.url, "kind": "operator", "source": "Фото оператора", "date": (m.get("at") or "")[:10] or None,
                              "mediaId": a.id, "by": m.get("by"), "note": m.get("note")})
            else:
                # страница карточки - только ссылка на сайт Homeportal: фото, загруженные до проверки ссылок
                # (_is_homeportal), могли сохранить любой адрес с "homeportal.kz"
                page = m.get("page")
                items.append({"src": a.url, "kind": "homeportal", "source": "Homeportal", "mediaId": a.id,
                              "url": page if isinstance(page, str) and _is_homeportal(page) else None,
                              "featured": bool(m.get("featured"))})
        for p in sorted(c.photos or [], key=lambda p: p.get("date") or "", reverse=True):
            if p.get("src"):
                items.append({"src": p["src"], "kind": "site", "source": p.get("source") or "Фото-мониторинг",
                              "date": p.get("date"), "by": p.get("by"), "note": p.get("note")})
        n = 0
        for aid, confirmed in by_cx.get(c.id, []):
            ad = ads.get(aid)
            if ad is None or n >= AD_MAX:
                continue
            for a in pics.get(aid, [])[:AD_PER_POST]:
                if n >= AD_MAX:
                    break
                who = ", ".join(x for x in (PLATFORMS.get(ad.platform, ad.platform or ""), ad.account) if x)
                items.append({"src": a.url, "kind": "ad", "source": who or "Реклама", "date": (ad.date or "")[:10] or None,
                              "adId": ad.id, "hypothesis": not confirmed, "mediaId": a.id})
                n += 1
        # homeportal: отмеченное источником главным - первым среди своих
        items.sort(key=lambda x: (KIND_ORDER[x["kind"]], not x.get("featured")))
        seen, uniq = set(), []
        for x in items:
            if x["src"] not in seen:
                seen.add(x["src"])
                uniq.append(x)
        pinned = (c.cover or "").strip()
        if pinned:
            hit = next((x for x in uniq if x["src"] == pinned), None)
            if hit is None:  # обложка демо-набора или прежняя ссылка - своим пунктом
                hit = {"src": pinned, "kind": "operator", "source": "Обложка карточки"}
            else:
                uniq.remove(hit)
            uniq.insert(0, {**hit, "cover": True})
        for x in uniq:
            x.pop("featured", None)
        out[c.id] = uniq
    return out


# ---------- маршруты ----------

def _row(db: Session, cid: str) -> Obj:
    return complex_row(db, complex_or_404(db, cid))


def _form_author(author: str | None, note: str | None) -> schemas.GalleryUploadIn:
    try:
        return schemas.GalleryUploadIn.model_validate({k: v for k, v in {"author": author, "note": note}.items() if v is not None})
    except ValidationError as e:
        raise form_errors(e) from None


@router.post("/api/complexes/{cid}/gallery", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             status_code=201,
             responses={413: {"description": "Файл больше 10 МБ"}, 415: {"description": "Не изображение"},
                        429: {"description": "Слишком много загрузок"}, 507: {"description": "Мало места"}})
def upload(cid: str, file: UploadFile = File(...), author: str | None = Form(None), note: str | None = Form(None),
           db: Session = Depends(get_db)):
    """Фото объекта от оператора(multipart: file, author, note?): те же пределы, что у скриншота(10 МБ, JPEG / PNG /
    WEBP / GIF по сигнатуре, часовой лимит ручных загрузок). Без закреплённой обложки новое фото становится обложкой."""
    body = _form_author(author, note)
    complex_or_404(db, cid)
    now = models.utcnow()
    st = media.accept_image_upload(db, file.file, now)
    meta = {"complexId": cid, "by": body.author, "at": models.iso(now)}
    if body.note:
        meta["note"] = body.note
    asset = models.MediaAsset(id=uuid.uuid4().hex, ad_id=None, item_id=None, kind="image", role=OPERATOR,
                              source_key=f"{OPERATOR}:{cid}:{uuid.uuid4().hex}", origin_ref=OPERATOR,
                              status="downloaded", attempts=1, mime=st.mime, size=st.size, sha256=st.sha256,
                              storage_key=st.storage_key, meta=meta, created_at=now, downloaded_at=now)
    db.add(asset)
    c = complex_or_404(db, cid, lock=True)
    what = "Фото объекта загружено оператором"
    if not (c.cover or "").strip():
        c.cover = asset.url
        what += " и выбрано обложкой карточки"
    data_event(db, body.author, f"{what}. {body.note}" if body.note else what, cid)
    db.commit()
    return _row(db, cid)


@router.post("/api/complexes/{cid}/cover", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             responses={422: {"description": NOT_IN_GALLERY}})
def set_cover(cid: str, body: schemas.CoverIn, db: Session = Depends(get_db)):
    """Закрепить обложку(src - одно из фото объекта) или вернуть автоматическую(src пустой)."""
    c = complex_or_404(db, cid, lock=True)
    src = (body.src or "").strip()
    if src:
        items = collect(db, [c], _attributed(db, c.id))[c.id]
        hit = next((x for x in items if x["src"] == src), None)
        if hit is None:
            raise HTTPException(422, NOT_IN_GALLERY)
        if (c.cover or "") == src:
            return _row(db, cid)
        c.cover = src
        what = f"Обложка карточки: {hit['source']}" + (" (привязка рекламы — гипотеза)" if hit.get("hypothesis") else "")
    else:
        if not (c.cover or "").strip():
            return _row(db, cid)
        c.cover = ""
        what = "Обложка карточки — снова автоматическая (первое фото по источникам)"
    data_event(db, body.author, what, cid)
    db.commit()
    return _row(db, cid)


@router.delete("/api/complexes/{cid}/gallery/{mid}", response_model=schemas.ComplexListOut,
               response_model_by_alias=True, responses={404: {"description": "Фото не найдено"}})
def remove(cid: str, mid: str, body: schemas.AuthorIn, db: Session = Depends(get_db)):
    """Убрать фото оператора(по ошибке не тот дом): запись файла удаляется, файл - уборкой хранилища."""
    a = db.get(models.MediaAsset, mid)
    if a is None or a.origin_ref != OPERATOR or _key_cid(a.source_key or "") != cid:
        raise HTTPException(404, "Фото оператора не найдено")
    c = complex_or_404(db, cid, lock=True)
    if (c.cover or "") == a.url:
        c.cover = ""
    db.delete(a)
    data_event(db, body.author, "Фото объекта от оператора удалено", cid)
    db.commit()
    return _row(db, cid)


@router.post("/api/complexes/{cid}/gallery/fetch", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             responses={422: {"description": NO_HOMEPORTAL}, 502: {"description": "Homeportal не ответил"}})
def fetch_route(cid: str, body: schemas.AuthorIn, db: Session = Depends(get_db)):
    """Подгрузить фото карточки ЖК с Homeportal(по ссылкам записей открытого реестра)."""
    c = complex_or_404(db, cid)
    pages = homeportal_pages(db, c)
    if not pages:
        raise HTTPException(422, NO_HOMEPORTAL)
    rep = fetch_homeportal(db, c, pages)
    if rep["errors"] and not rep["added"] and not rep["known"]:
        raise HTTPException(502, "Homeportal не отдал фото: " + "; ".join(rep["errors"][:2]))
    if rep["added"]:
        data_event(db, body.author, _hp_event(rep["added"], pages), cid)
    db.commit()
    return _row(db, cid)


def _attributed(db: Session, cid: str) -> dict[str, list[tuple[str, str]]]:
    from . import screening

    return {cid: [(f.id, kind) for _, f, kind in screening.attributed_to(db, cid)]}


# ---------- Homeportal ----------

def _host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:  # "https://[::1" и подобное
        return None


def _is_homeportal(url: str) -> bool:
    """Ссылка на сайт Homeportal(https://homeportal.kz/... или поддомен): не подстрока "homeportal.kz" где угодно -
    ссылка уходит в карточку фото(url) и выводится ссылкой на странице."""
    host = _host(url) or ""
    return url.lower().startswith(("https://", "http://")) and (host == HP_SITE or host.endswith("." + HP_SITE))


def homeportal_page_urls(records: Iterable[Obj]) -> list[str]:
    """Ссылки на карточки Homeportal из записей открытого реестра ЖК(registry_groups): источник homeportal или
    homeportal_section, ссылка на сайт Homeportal(_is_homeportal) с id объекта(_api_path - без него фото не загрузить),
    без повторов объекта(та же карточка под другой ссылкой), по порядку записей, не больше HP_PAGES_MAX."""
    out: list[str] = []
    seen: set[str] = set()
    for r in records:
        url = r.get("sourceUrl")
        if r.get("source") not in HP_SOURCES or not isinstance(url, str) or not _is_homeportal(url):
            continue
        ap = _api_path(url)
        if ap is None or ap[0] in seen:
            continue
        seen.add(ap[0])
        out.append(url)
        if len(out) == HP_PAGES_MAX:
            break
    return out


def homeportal_pages(db: Session, c: models.Complex) -> list[str]:
    """Ссылки на карточки Homeportal из записей открытого реестра этого ЖК(projects/{id}, Illegal-projects/{id})."""
    from . import screening
    return homeportal_page_urls(screening.registry_groups(db, [c])[c.id][1])


def has_homeportal_pages(records: Iterable[Obj]) -> bool:
    """Есть ли у ЖК карточка Homeportal, откуда подгружаются фото(иначе POST /api/complexes/{id}/gallery/fetch - 422
    NO_HOMEPORTAL): по записям открытого реестра этого ЖК, то же правило, что у homeportal_page_urls."""
    return bool(homeportal_page_urls(records))


def _api_path(page: str) -> tuple[str, str] | None:
    """Карточка Homeportal → (путь API, id объекта). id - без ведущих нулей: projects/0042 и projects/42 - один объект
    (повтор в homeportal_page_urls)."""
    parts = [p for p in urlsplit(page).path.split("/") if p]
    if len(parts) < 2 or not _OBJECT_ID.fullmatch(parts[-1]):
        return None
    oid = str(int(parts[-1]))
    if parts[-2].lower() == "projects":
        return f"objects-detail/{oid}", oid
    if parts[-2].lower() == "illegal-projects":
        return f"illegal-objects/{oid}", oid
    return None


def _quote_url(url: str) -> str:
    s = urlsplit(url)
    return urlunsplit((s.scheme, s.netloc, quote(s.path), s.query, ""))


def _images(payload: Any) -> list[Obj]:
    """Фото из ответа API Homeportal(data.images); ответ другой формы - пусто, а не сбой всей подгрузки."""
    data = payload.get("data") if isinstance(payload, dict) else None
    images = data.get("images") if isinstance(data, dict) else None
    return [im for im in images if isinstance(im, dict)][:HP_MAX] if isinstance(images, list) else []


def _photo_key(cid: str, im: Obj, oid: str, n: int) -> str:
    """source_key фото Homeportal: id фото источника, если он короткий и без разделителей, иначе - объект и номер."""
    pid = im.get("id")
    if not pid or isinstance(pid, bool) or not isinstance(pid, (int, str)) or not _PHOTO_ID.fullmatch(str(pid)):
        pid = f"{oid}-{n}"
    return f"homeportal:{cid}:{pid}"


def _hp_keys(db: Session, cid: str) -> set[str]:
    A = models.MediaAsset
    return set(db.scalars(select(A.source_key).where(A.source_key.startswith(f"homeportal:{cid}:", autoescape=True))))


def _hp_event(added: int, pages: list[str]) -> str:
    return (f"Фото объекта с Homeportal: добавлено {added} ({', '.join(pages)}) — фото или визуализация застройщика, "
            "не снимок площадки")


def fetch_homeportal(db: Session, c: models.Complex, pages: list[str], *,
                     stop: threading.Event | None = None) -> Obj:
    """Фото карточек Homeportal → хранилище и MediaAsset(записи - без commit). Уже загруженные(тот же id фото)
    пропускаются; загрузок файлов - не больше HP_FILES_MAX(остальные фото - следующей подгрузкой). Несохранённых
    изменений у вызывающего быть не должно: читающая транзакция фиксируется до сетевых запросов(соединение не занято
    минутами загрузки, на PostgreSQL - без "idle in transaction"). Загрузка идёт без блокировки; перед записью строка
    ЖК блокируется и загруженные перечитываются - параллельная подгрузка того же ЖК(повторное нажатие, такт воркера)
    не добавляет дублей. stop(остановка воркера) - загрузка прерывается перед
    запросом карточки или на следующем блоке файла(cancel), уже загруженное не записывается(stopped = True): ЖК
    остаётся без фото Homeportal, и следующая подгрузка(missing_only; воркер после остановки начинает её в первый же
    такт лидера) возьмёт его целиком, а не наполовину."""
    from .integrations import http

    halted = stop.is_set if stop is not None else None
    have = _hp_keys(db, c.id)
    db.commit()
    rep: Obj = {"added": 0, "known": 0, "errors": []}
    got: list[models.MediaAsset] = []  # ещё не в сессии: добавляются под блокировкой ЖК
    files = 0  # загрузки файлов, и неудачные: предел - на время подгрузки, а не только на число фото
    with http.client(base_url=HP_API, timeout=20, headers={"Accept": "application/json"}) as cl:
        for page in pages:
            ap = _api_path(page)
            if ap is None:
                continue
            if files >= HP_FILES_MAX:
                break
            if halted is not None and halted():
                return {**rep, "stopped": True}
            path, oid = ap
            try:
                r = cl.get(f"{HP_API}/{path}")
                r.raise_for_status()
                images = _images(r.json())
            except Exception as e:  # сеть, статус, не JSON - причина в отчёт, остальные карточки грузятся
                rep["errors"].append(f"{page}: {type(e).__name__}")
                continue
            for n, im in enumerate(images):
                key = _photo_key(c.id, im, oid, n)
                if key in have:
                    rep["known"] += 1
                    continue
                link = im.get("image_link")
                if not isinstance(link, str) or _host(link) != HP_HOST:
                    continue
                if files >= HP_FILES_MAX:
                    break
                files += 1
                try:
                    f = http.fetch_to_temp(_quote_url(link), max_bytes=media.UPLOAD_MAX, allowed_mime=media.IMAGE_TYPES,
                                           allowed_hosts=[HP_HOST], timeout=30, cancel=halted)
                    st = media.store_file(f.path, allowed=media.IMAGE_TYPES)
                except (http.DownloadError, media.MediaRejected) as e:
                    if halted is not None and halted():  # загрузка прервана остановкой - это не ошибка Homeportal
                        return {**rep, "stopped": True}
                    rep["errors"].append(f"{link}: {getattr(e, 'detail', None) or e}")
                    continue
                now = models.utcnow()
                got.append(models.MediaAsset(
                    id=uuid.uuid4().hex, ad_id=None, item_id=None, position=n, kind="image", role=OPERATOR,
                    source_key=key, origin_ref=f"homeportal:{oid}", origin_url=link[:512], status="downloaded",
                    attempts=1, mime=st.mime, size=st.size, sha256=st.sha256, storage_key=st.storage_key,
                    meta={"complexId": c.id, "source": "Homeportal", "page": page,
                          "featured": bool(im.get("is_featured"))},
                    created_at=now, downloaded_at=now))
                have.add(key)
    if not got:
        return rep
    get_for_update(db, models.Complex, c.id)  # и на SQLite: голый FOR UPDATE там не блокирует
    have = _hp_keys(db, c.id)
    for a in got:
        if a.source_key in have:  # успела записать параллельная подгрузка
            rep["known"] += 1
            continue
        db.add(a)
        rep["added"] += 1
    return rep


def fetch_each(db: Session, only: list[str] | None = None, *, missing_only: bool = False,
               stop: threading.Event | None = None, after: str | None = None) -> Iterator[tuple[models.Complex, Obj]]:
    """Фото карточек Homeportal по ЖК с такими записями, по порядку id(only - только эти, after - только после этого
    id: продолжение прерванного прохода): (ЖК, отчёт fetch_homeportal) после каждого ЖК. missing_only - только ЖК, у
    которых фото Homeportal ещё нет(такт воркера: новые карточки реестра получают фото сами). Каждый ЖК - своей
    транзакцией; записи реестра читаются один раз на весь проход. stop(остановка воркера) проверяется перед каждым ЖК
    и внутри подгрузки(fetch_homeportal): SIGTERM не ждёт прохода по всему реестру."""
    from . import screening

    A = models.MediaAsset
    have = {_key_cid(k) for k in db.scalars(select(A.source_key).where(A.source_key.like("homeportal:%")))} if missing_only else set()
    q = select(models.Complex).order_by(models.Complex.id)
    if only:
        q = q.where(models.Complex.id.in_(only))
    if after is not None:
        q = q.where(models.Complex.id > after)
    cxs = [c for c in db.scalars(q) if c.id not in have]
    groups = screening.registry_groups(db, cxs)
    for c in cxs:
        if stop is not None and stop.is_set():
            return
        pages = homeportal_page_urls(groups[c.id][1])
        if not pages:
            continue
        rep = fetch_homeportal(db, c, pages, stop=stop)
        if rep["added"]:
            data_event(db, "Система", _hp_event(rep["added"], pages), c.id)
        db.commit()
        yield c, rep


def fetch_all(db: Session, only: list[str] | None = None, *, missing_only: bool = False,
              stop: threading.Event | None = None) -> list[tuple[str, Obj]]:
    """Весь проход fetch_each: [(название ЖК, отчёт)]."""
    return [(c.name, rep) for c, rep in fetch_each(db, only, missing_only=missing_only, stop=stop)]


def main(argv: list[str] | None = None) -> int:
    from .db import SessionLocal

    ap = argparse.ArgumentParser(prog="python -m app.gallery", description="Фото объектов для карточек ЖК")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="подгрузить фото карточек Homeportal для ЖК из открытого реестра")
    f.add_argument("--only", default="", help="ID ЖК через запятую")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with SessionLocal() as db:
        for name, rep in fetch_all(db, [x for x in a.only.split(",") if x] or None):
            print(f"{name}: добавлено {rep['added']}, уже было {rep['known']}" + (f", ошибки: {rep['errors'][:2]}" if rep["errors"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
