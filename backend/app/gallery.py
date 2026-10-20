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
import uuid
from typing import Any
from collections.abc import Iterable
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import media, models, schemas
from .db import get_db

log = logging.getLogger("saq.gallery")
router = APIRouter(tags=["gallery"])

Obj = dict[str, Any]
OPERATOR = "gallery"  # origin_ref и role фото оператора
HP_HOST = "api.homeportal.kz"
HP_API = f"https://{HP_HOST}/api/v1"
HP_SOURCES = ("homeportal", "homeportal_section")
HP_MAX = 8  # фото одного объекта Homeportal
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
                items.append({"src": a.url, "kind": "homeportal", "source": "Homeportal", "mediaId": a.id,
                              "url": m.get("page"), "featured": bool(m.get("featured"))})
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

def _complex(db: Session, cid: str, *, lock: bool = False) -> models.Complex:
    from .verification import _complex as get
    return get(db, cid, lock=lock)


def _event(db: Session, who: str, what: str, cid: str) -> None:
    db.add(models.AuditEvent(kind="data", who=who, what=what, at=models.utcnow(), complex_id=cid))


def _row(db: Session, cid: str) -> Obj:
    from .verification import _row as row
    return row(db, _complex(db, cid))


def _form_author(author: str | None, note: str | None) -> schemas.GalleryUploadIn:
    from .verification import _form_errors
    try:
        return schemas.GalleryUploadIn.model_validate({k: v for k, v in {"author": author, "note": note}.items() if v is not None})
    except ValidationError as e:
        raise _form_errors(e) from None


@router.post("/api/complexes/{cid}/gallery", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             status_code=201,
             responses={413: {"description": "Файл больше 10 МБ"}, 415: {"description": "Не изображение"},
                        429: {"description": "Слишком много загрузок"}, 507: {"description": "Мало места"}})
def upload(cid: str, file: UploadFile = File(...), author: str | None = Form(None), note: str | None = Form(None),
           db: Session = Depends(get_db)):
    """Фото объекта от оператора(multipart: file, author, note?): те же пределы, что у скриншота(10 МБ, JPEG / PNG /
    WEBP / GIF по сигнатуре, часовой лимит ручных загрузок). Без закреплённой обложки новое фото становится обложкой."""
    body = _form_author(author, note)
    _complex(db, cid)
    data = file.file.read(media.UPLOAD_MAX + 1)
    if len(data) > media.UPLOAD_MAX:
        raise HTTPException(413, f"Файл больше {media.UPLOAD_MAX // media.MB} МБ")
    now = models.utcnow()
    try:
        media.check_upload_budget(db, now, len(data))
        st = media.store_bytes(data, allowed=media.IMAGE_TYPES)
    except media.MediaRejected as e:
        raise HTTPException(e.status, e.detail) from None
    meta = {"complexId": cid, "by": body.author, "at": models.iso(now)}
    if body.note:
        meta["note"] = body.note
    asset = models.MediaAsset(id=uuid.uuid4().hex, ad_id=None, item_id=None, kind="image", role=OPERATOR,
                              source_key=f"{OPERATOR}:{cid}:{uuid.uuid4().hex}", origin_ref=OPERATOR,
                              status="downloaded", attempts=1, mime=st.mime, size=st.size, sha256=st.sha256,
                              storage_key=st.storage_key, meta=meta, created_at=now, downloaded_at=now)
    db.add(asset)
    c = _complex(db, cid, lock=True)
    what = "Фото объекта загружено оператором"
    if not (c.cover or "").strip():
        c.cover = asset.url
        what += " и выбрано обложкой карточки"
    _event(db, body.author, f"{what}. {body.note}" if body.note else what, cid)
    db.commit()
    return _row(db, cid)


@router.post("/api/complexes/{cid}/cover", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             responses={422: {"description": NOT_IN_GALLERY}})
def set_cover(cid: str, body: schemas.CoverIn, db: Session = Depends(get_db)):
    """Закрепить обложку(src - одно из фото объекта) или вернуть автоматическую(src пустой)."""
    c = _complex(db, cid, lock=True)
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
    _event(db, body.author, what, cid)
    db.commit()
    return _row(db, cid)


@router.delete("/api/complexes/{cid}/gallery/{mid}", response_model=schemas.ComplexListOut,
               response_model_by_alias=True, responses={404: {"description": "Фото не найдено"}})
def remove(cid: str, mid: str, body: schemas.AuthorIn, db: Session = Depends(get_db)):
    """Убрать фото оператора(по ошибке не тот дом): запись файла удаляется, файл - уборкой хранилища."""
    a = db.get(models.MediaAsset, mid)
    if a is None or a.origin_ref != OPERATOR or _key_cid(a.source_key or "") != cid:
        raise HTTPException(404, "Фото оператора не найдено")
    c = _complex(db, cid, lock=True)
    if (c.cover or "") == a.url:
        c.cover = ""
    db.delete(a)
    _event(db, body.author, "Фото объекта от оператора удалено", cid)
    db.commit()
    return _row(db, cid)


@router.post("/api/complexes/{cid}/gallery/fetch", response_model=schemas.ComplexListOut, response_model_by_alias=True,
             responses={422: {"description": NO_HOMEPORTAL}, 502: {"description": "Homeportal не ответил"}})
def fetch_route(cid: str, body: schemas.AuthorIn, db: Session = Depends(get_db)):
    """Подгрузить фото карточки ЖК с Homeportal(по ссылкам записей открытого реестра)."""
    c = _complex(db, cid)
    pages = homeportal_pages(db, c)
    if not pages:
        raise HTTPException(422, NO_HOMEPORTAL)
    rep = fetch_homeportal(db, c, pages)
    if rep["errors"] and not rep["added"] and not rep["known"]:
        raise HTTPException(502, "Homeportal не отдал фото: " + "; ".join(rep["errors"][:2]))
    if rep["added"]:
        _event(db, body.author, f"Фото объекта с Homeportal: добавлено {rep['added']} ({', '.join(pages)}) — фото или "
                                "визуализация застройщика, не снимок площадки", cid)
    db.commit()
    return _row(db, cid)


def _attributed(db: Session, cid: str) -> dict[str, list[tuple[str, str]]]:
    from . import screening
    out: list[tuple[str, str]] = []
    for f in screening.load_facts(db).values():
        kind = f.targets().get(cid)
        if kind:
            out.append((f.id, kind))
    return {cid: out}


# ---------- Homeportal ----------

def homeportal_pages(db: Session, c: models.Complex) -> list[str]:
    """Ссылки на карточки Homeportal из записей открытого реестра этого ЖК(projects/{id}, Illegal-projects/{id})."""
    from . import screening
    _, rs = screening.registry_groups(db, [c])[c.id]
    out: list[str] = []
    for r in rs:
        url = r.get("sourceUrl") or ""
        if r.get("source") in HP_SOURCES and "homeportal.kz" in url and url not in out:
            out.append(url)
    return out


def _api_path(page: str) -> tuple[str, str] | None:
    """Карточка Homeportal → (путь API, id объекта)."""
    parts = [p for p in urlsplit(page).path.split("/") if p]
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    oid = parts[-1]
    if parts[-2].lower() == "projects":
        return f"objects-detail/{oid}", oid
    if parts[-2].lower() == "illegal-projects":
        return f"illegal-objects/{oid}", oid
    return None


def _quote_url(url: str) -> str:
    s = urlsplit(url)
    return urlunsplit((s.scheme, s.netloc, quote(s.path), s.query, ""))


def fetch_homeportal(db: Session, c: models.Complex, pages: list[str]) -> Obj:
    """Фото карточек Homeportal → хранилище и MediaAsset(без commit). Уже загруженные(тот же id фото) пропускаются."""
    from .integrations import http

    A = models.MediaAsset
    have = set(db.scalars(select(A.source_key).where(A.source_key.like(f"homeportal:{c.id}:%"))))
    rep: Obj = {"added": 0, "known": 0, "errors": []}
    with http.client(base_url=HP_API, timeout=20, headers={"Accept": "application/json"}) as cl:
        for page in pages:
            ap = _api_path(page)
            if ap is None:
                continue
            path, oid = ap
            try:
                r = cl.get(f"{HP_API}/{path}")
                r.raise_for_status()
                images = ((r.json() or {}).get("data") or {}).get("images") or []
            except Exception as e:
                rep["errors"].append(f"{page}: {type(e).__name__}")
                continue
            for n, im in enumerate(images[:HP_MAX]):
                link = im.get("image_link") or ""
                key = f"homeportal:{c.id}:{im.get('id') or f'{oid}-{n}'}"
                if key in have:
                    rep["known"] += 1
                    continue
                if urlsplit(link).hostname != HP_HOST:
                    continue
                try:
                    got = http.fetch_to_temp(_quote_url(link), max_bytes=media.UPLOAD_MAX, allowed_mime=media.IMAGE_TYPES,
                                             allowed_hosts=[HP_HOST], timeout=30)
                    st = media.store_file(got.path, allowed=media.IMAGE_TYPES)
                except (http.DownloadError, media.MediaRejected) as e:
                    rep["errors"].append(f"{link}: {getattr(e, 'detail', None) or e}")
                    continue
                now = models.utcnow()
                db.add(A(id=uuid.uuid4().hex, ad_id=None, item_id=None, position=n, kind="image", role=OPERATOR,
                         source_key=key, origin_ref=f"homeportal:{oid}", origin_url=link[:512], status="downloaded",
                         attempts=1, mime=st.mime, size=st.size, sha256=st.sha256, storage_key=st.storage_key,
                         meta={"complexId": c.id, "source": "Homeportal", "page": page, "featured": bool(im.get("is_featured"))},
                         created_at=now, downloaded_at=now))
                have.add(key)
                rep["added"] += 1
    return rep


def fetch_all(db: Session, only: list[str] | None = None, *, missing_only: bool = False) -> list[tuple[str, Obj]]:
    """Фото карточек Homeportal по всем ЖК с такими записями(only - только эти); missing_only - только ЖК, у которых
    фото Homeportal ещё нет(такт воркера: новые карточки реестра получают фото сами). Каждый ЖК - своей транзакцией."""
    A = models.MediaAsset
    have = {_key_cid(k) for k in db.scalars(select(A.source_key).where(A.source_key.like("homeportal:%")))} if missing_only else set()
    out = []
    for c in db.scalars(select(models.Complex).order_by(models.Complex.id)):
        if (only and c.id not in only) or c.id in have:
            continue
        pages = homeportal_pages(db, c)
        if not pages:
            continue
        rep = fetch_homeportal(db, c, pages)
        if rep["added"]:
            _event(db, "Система", f"Фото объекта с Homeportal: добавлено {rep['added']} ({', '.join(pages)}) — фото или "
                                  "визуализация застройщика, не снимок площадки", c.id)
        db.commit()
        out.append((c.name, rep))
    return out


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
