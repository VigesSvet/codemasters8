"""Анализ собранной публикации - задание analyze воркера(integration-spec §8; план: AnalysisJob).

Этапы: media(файлы по ссылкам приёма через API) → frames(кадры видео, ffmpeg) → speech(речь в роликах, app.speech -
faster-whisper, если установлен) → ocr(Tesseract по изображениям и кадрам) → rules(правила saq-ai-1: условия оплаты, гипотезы ЖК) → llm(Claude, app.llm - только если задан
ANTHROPIC_API_KEY) → events(журнал). Начало каждого этапа пишется в ingest_items.analysis_stage и jobs.payload.stage -
при сбое видно, на каком этапе он случился. Сбой этапа llm анализ не роняет: "анализ ИИ не выполнен" - в журнале,
повтор при сетевой ошибке, 429 и 5xx - новым заданием к сроку повтора(как повтор загрузки файла).

Расшифровка речи ролика хранится у видео(media_assets.meta.speech - без новой колонки) и попадает в ads.ocr абзацем
"Речь в ролике: …" после текста OCR: правила и Claude читают её вместе с OCR, а версия содержимого и снимок данных
(ads.ocr) - те же, что у OCR. Аналитик видит расшифровку отдельно(AdOut.media[].speech).

Транзакции короткие: загрузка, OCR, распознавание речи и ffmpeg выполняются между фиксациями, без открытой транзакции записи; состояние
каждого файла фиксируется сразу. Проблемы отдельных файлов(не скачался, нет Tesseract или ffmpeg) задание не роняют -
они видны у файла и в журнале. Версия содержимого фиксируется в начале(v): если во время анализа пришла новая версия,
этот анализ отметит только v, а новая версия получит своё задание.

Привязку к ЖК анализ собранной публикации не назначает(гипотеза ≠ факт, D5); SAQ_COLLECTED_AUTOLINK=1 включает правило
ручного ввода(лучшая гипотеза от порога - привязка-гипотеза, не подтверждённая). app.main не импортируется.

Повторный запуск по той же версии(повтор загрузки или запроса к Claude, дозапрос Claude, "Проанализировать ещё раз" -
force) журнал не дублирует: события OCR - если изменился текст OCR, файлов - по новым неудачам, гипотезы правил - если
назначена привязка, событие Claude - только по новому итогу этапа llm.

Пересчёт правил(rules_only, задание с payload rulesOnly - после создания карточки ЖК по рекламе, app.discovery): только
этап rules по уже проанализированной версии - без файлов, OCR и без запроса к Claude(справочник ЖК изменился, значит, и
ключ материала, - новый запрос был бы лишним расходом). Событие гипотезы - если лучшая гипотеза правил сменилась. Новая
версия, ещё не проанализированная, получает обычный анализ.
"""
import logging
import time
import uuid
from datetime import datetime, timedelta
from collections.abc import Callable

from sqlalchemy import func, or_, select, update

from . import ads_service, ai, llm, media, models, speech, video, world
from .ai import ocr as ocr_engine  # тесты подменяют analysis.ocr_engine.recognize
from .schemas import AD_TEXT_MAX
from .db import SessionLocal
from .integrations import http
from .logs import redact
from .risk_model import T
from .settings import get_settings

log = logging.getLogger("saq.analysis")

TERM_LABELS = {"booking": "бронь", "deposit": "депозит", "down_payment": "первоначальный взнос",
               "installment": "рассрочка", "ddu": "ДДУ", "direct_payment": "оплата напрямую застройщику",
               "investment": "инвестиции"}  # "price" - не условие оплаты
STAGES = ("media", "frames", "speech", "ocr", "rules", "llm", "events")
SPEECH_MARK = "Речь в ролике:"  # начало абзаца расшифровки в ads.ocr(фронтенд показывает его отдельным блоком)
_SPEECH_RETRY = ("unavailable", "failed", "skipped")  # "Проанализировать ещё раз" распознаёт такие ролики заново
FETCH_ATTEMPTS = 3
RETRY_BASE_S = 60
SYSTEM = ads_service.SYSTEM
BUDGET_SKIPPED = "Превышен объём файлов публикации (400 МБ)"
FFMPEG_MISSING = "ffmpeg не установлен — кадры видео не извлечены"
FRAMES_FAILED = "Кадры видео не извлечены (ffmpeg завершился с ошибкой)"
NO_SOURCE = "Ссылка на файл не сохранена"
_UNAVAILABLE = ("http_403", "http_404", "http_410")
_OCR_FAILED = ("Tesseract завершился", "не уложилось")
MONOTONIC: Callable[[], float] = time.monotonic  # время выполнения анализа(тесты подменяют)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return one
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return few
    return many


def _retry_at(a: models.MediaAsset) -> datetime | None:
    raw = (a.meta or {}).get("retryAt")
    if not raw:
        return None
    try:
        return models.as_utc(datetime.fromisoformat(str(raw)))
    except ValueError:
        return None


def _due(a: models.MediaAsset, now: datetime) -> bool:
    """Ожидающий загрузки файл приёма, срок попытки которого наступил."""
    if a.status != "pending" or a.parent_id is not None:
        return False
    at = _retry_at(a)
    return at is None or at <= now


def _assets(db, item_id: int) -> list[models.MediaAsset]:
    A = models.MediaAsset
    return list(db.scalars(select(A).where(A.item_id == item_id)
                           .order_by(A.position, func.coalesce(A.frame_ms, -1), A.created_at, A.id)))


def _has_work(assets: list[models.MediaAsset], now: datetime) -> bool:
    for a in assets:
        if _due(a, now):
            return True
        if a.current and a.status == "downloaded" and a.ocr_status is None and a.kind in ("image", "frame", "video"):
            return True
        if _speech_due(a, False):  # ролик, проанализированный до распознавания речи(T-60)
            return True
    return False


def _speech_due(a: models.MediaAsset, force: bool) -> bool:
    """Загруженное видео текущей версии без расшифровки речи; force - и с неудачной(движок, тайм-аут, длина)."""
    if a.kind != "video" or a.parent_id is not None or not a.current or a.status != "downloaded":
        return False
    sp = (a.meta or {}).get("speech")
    if not isinstance(sp, dict):
        return True
    if sp.get("detail") == speech.DISABLED:  # этап был выключен, теперь включён
        return get_settings().asr_model.lower() != "off"
    return force and sp.get("status") in _SPEECH_RETRY


def _stage(job_id: int | None, item_id: int, stage: str) -> None:
    """Начало этапа: ingest_items.analysis_stage и jobs.payload.stage(своя короткая сессия)."""
    with SessionLocal() as db:
        db.execute(update(models.IngestItem).where(models.IngestItem.id == item_id).values(analysis_stage=stage)
                   .execution_options(synchronize_session=False))
        job = db.get(models.Job, job_id) if job_id is not None else None
        if job is not None:
            job.payload = {**(job.payload or {}), "stage": stage}
        db.commit()


def _cover(assets) -> str | None:
    imgs = [a for a in assets if a.status == "downloaded" and a.kind == "image" and a.current and a.parent_id is None]
    imgs.sort(key=lambda a: a.position)
    pick = next((a for a in imgs if a.role != "cover"), None) or (imgs[0] if imgs else None)
    return f"/api/media/{pick.id}" if pick else None


# ---------- этапы ----------

def _download(asset_id: str, kind: str, src: str | None, attempts: int, remaining: int,
              clock: Callable[[], datetime]) -> tuple[dict, int]:
    """Сеть, без сессии: файл по ссылке приёма → хранилище. (новые значения полей файла, сохранено байт). Срок
    повтора - от момента неудачной загрузки(clock()), а не от захвата задания."""
    allowed = media.VIDEO_TYPES if kind == "video" else media.IMAGE_TYPES
    limit = media.limit_for(kind)
    drop = {"meta_drop": True}
    if not src:
        return {"status": "failed", "error": NO_SOURCE, **drop}, 0
    if remaining <= 0:
        return {"status": "skipped", "error": BUDGET_SKIPPED, **drop}, 0
    try:
        f = http.fetch_to_temp(src, max_bytes=min(limit, remaining), allowed_mime=allowed)
        st = media.store_file(f.path, allowed=allowed)
    except http.DownloadError as e:
        if e.code == "too_large" and remaining < limit:
            return {"status": "skipped", "error": BUDGET_SKIPPED, **drop}, 0
        if e.retryable:
            n = attempts + 1
            if n < FETCH_ATTEMPTS:
                retry = models.as_utc(clock()) + timedelta(seconds=RETRY_BASE_S * 2 ** n)
                return {"status": "pending", "error": e.detail, "attempts": n, "retryAt": models.iso(retry)}, 0
            return {"status": "failed", "error": e.detail, "attempts": n, **drop}, 0
        status = "unavailable" if e.code in _UNAVAILABLE else "failed"
        return {"status": status, "error": e.detail, "attempts": attempts + 1, **drop}, 0
    except media.MediaRejected as e:
        return {"status": "failed", "error": e.detail, "attempts": attempts + 1, **drop}, 0
    except Exception as e:
        log.warning("Файл %s не загружен: %s", asset_id, type(e).__name__)
        return {"status": "failed", "error": f"Файл не загружен ({type(e).__name__})", "attempts": attempts + 1,
                **drop}, 0
    return {"status": "downloaded", "stored": st, "attempts": attempts + 1, **drop}, st.size


def _media_stage(item_id: int, now: datetime, changed: set, clock: Callable[[], datetime]) -> None:
    A = models.MediaAsset
    with SessionLocal() as db:
        assets = _assets(db, item_id)
        todo = [(a.id, a.kind, (a.meta or {}).get("src"), a.attempts or 0) for a in assets if _due(a, now)]
        used = sum(a.size or 0 for a in assets if a.parent_id is None and a.status == "downloaded")
    for aid, kind, src, attempts in todo:
        values, size = _download(aid, kind, src, attempts, media.PUBLICATION_BYTES_MAX - used, clock)
        used += size
        with SessionLocal() as db:
            a = db.get(A, aid)
            if a is None or a.status != "pending":
                continue
            meta = dict(a.meta or {})
            if values.pop("meta_drop", False):
                meta.pop("src", None)
                meta.pop("retryAt", None)
            retry = values.pop("retryAt", None)
            if retry:
                meta["retryAt"] = retry
            st = values.pop("stored", None)
            a.status = values["status"]
            a.attempts = values.get("attempts", a.attempts)
            a.error = redact(values.get("error")) if values.get("error") else None
            if st is not None:
                a.storage_key, a.sha256, a.size, a.mime = st.storage_key, st.sha256, st.size, st.mime
                a.kind = "video" if st.mime in media.VIDEO_TYPES else "image"
                a.downloaded_at = models.as_utc(clock())
            a.meta = meta
            if a.status in ("failed", "unavailable", "skipped"):
                changed.add(a.id)  # событие "не загружено" - и для новой неудачи после первого анализа
            db.commit()
    with SessionLocal() as db:
        item = db.get(models.IngestItem, item_id)
        ad = db.get(models.Ad, item.ad_id) if item is not None else None
        if ad is not None and ad.image is None:
            cover = _cover(_assets(db, item_id))
            if cover:
                db.execute(update(models.Ad).where(models.Ad.id == ad.id, models.Ad.image.is_(None))
                           .values(image=cover).execution_options(synchronize_session=False))
                db.commit()


def _frames_stage(item_id: int, now: datetime, changed: set) -> None:
    A = models.MediaAsset
    with SessionLocal() as db:
        videos = [(a.id, a.storage_key, a.mime) for a in _assets(db, item_id)
                  if a.kind == "video" and a.parent_id is None and a.current and a.status == "downloaded"
                  and a.ocr_status is None]
    for vid, key, mime in videos:
        frames: list[tuple[int, bytes]] = []
        status, detail = "failed", FRAMES_FAILED
        if not video.ffmpeg_available():
            status, detail = "unavailable", FFMPEG_MISSING
        else:
            try:
                frames = video.extract_frames(media.path_of(key), mime or "video/mp4")
            except (OSError, ValueError) as e:
                log.warning("Кадры видео %s не извлечены: %s", vid, type(e).__name__)
                frames = []
        stored = []
        for ms, png in frames:
            try:
                stored.append((ms, media.store_bytes(png, allowed=media.IMAGE_TYPES)))
            except media.MediaRejected as e:
                status, detail = "failed", f"Кадры видео не сохранены: {e.detail}"
                stored = []
                break
        if stored:
            status, detail = "done", f"Кадров: {len(stored)}"
        with SessionLocal() as db:
            v = db.get(A, vid)
            if v is None:
                continue
            for ms, st in stored:
                db.add(A(id=uuid.uuid4().hex, ad_id=v.ad_id,
                         item_id=v.item_id, parent_id=v.id, position=v.position, kind="frame", role=None,
                         source_key=None, current=v.current, origin_ref="frame", status="downloaded",
                         attempts=1, mime=st.mime, size=st.size, sha256=st.sha256, storage_key=st.storage_key,
                         frame_ms=int(ms), meta={}, created_at=models.as_utc(now),
                         downloaded_at=models.as_utc(now)))
            v.ocr_status, v.ocr_detail = status, detail
            if status != "done":
                changed.add(vid)
            db.commit()


def _speech_stage(item_id: int, force: bool, heard: set) -> None:
    """Речь в роликах → meta.speech видео. Тот же файл(sha256), уже расшифрованный у другой публикации, не
    распознаётся заново. heard - видео, расшифровка которых получена в этом анализе(событие журнала)."""
    A = models.MediaAsset
    with SessionLocal() as db:
        videos = [(a.id, a.storage_key, a.mime, a.sha256) for a in _assets(db, item_id) if _speech_due(a, force)]
    for vid, key, mime, sha in videos:
        r = None
        if sha:
            with SessionLocal() as db:
                for other in db.scalars(select(A).where(A.sha256 == sha, A.id != vid, A.kind == "video")):
                    sp = (other.meta or {}).get("speech")
                    if isinstance(sp, dict) and sp.get("status") in ("done", "empty"):
                        r = dict(sp)
                        break
        if r is None:
            try:
                r = speech.transcribe(media.path_of(key), mime or "video/mp4")
            except (OSError, ValueError) as e:  # нет файла в хранилище, неверный ключ
                log.warning("Речь в ролике %s не распознана: %s", vid, type(e).__name__)
                r = {"status": "failed", "engine": speech.ENGINE,
                     "detail": f"Файл ролика не прочитан ({type(e).__name__})"}
        if r.get("detail"):
            r["detail"] = redact(r["detail"])
        with SessionLocal() as db:
            v = db.get(A, vid)
            if v is None:
                continue
            v.meta = {**(v.meta or {}), "speech": r}
            db.commit()
        heard.add(vid)


def _ocr_result(r: dict) -> tuple[str, str, str | None, str | None]:
    """Ответ ocr.recognize → (ocr_status, текст, движок, пояснение)."""
    engine = r.get("engine") or "none"
    text = (r.get("text") or "").strip()
    detail = r.get("detail")
    if engine == "none":
        return "unavailable", "", engine, detail or ocr_engine.NOT_INSTALLED
    if not text:
        failed = bool(detail) and any(m in detail for m in _OCR_FAILED)
        return ("failed" if failed else "empty"), "", engine, detail
    return "done", text, engine, detail


def _ocr_stage(item_id: int) -> None:
    A = models.MediaAsset
    with SessionLocal() as db:
        todo = [(a.id, a.sha256) for a in _assets(db, item_id)
                if a.kind in ("image", "frame") and a.current and a.status == "downloaded" and a.ocr_status is None]
    for aid, sha in todo:
        with SessionLocal() as db:
            same = None
            if sha:
                same = db.scalar(select(A).where(A.sha256 == sha, A.id != aid, A.ocr_status.in_(("done", "empty")))
                                 .limit(1))
            if same is not None:
                a = db.get(A, aid)
                a.ocr_status, a.ocr_text, a.ocr_engine, a.ocr_detail = (same.ocr_status, same.ocr_text,
                                                                        same.ocr_engine, same.ocr_detail)
                db.commit()
                continue
            a = db.get(A, aid)
            if a is None or not a.storage_key:
                continue
            key, mime = a.storage_key, a.mime
        try:
            data = media.path_of(key).read_bytes()
            status, text, engine, detail = _ocr_result(ocr_engine.recognize(data, mime))
        except ocr_engine.OcrError as e:
            status, text, engine, detail = "failed", "", None, e.detail
        except Exception as e:
            log.warning("OCR файла %s не выполнен: %s", aid, type(e).__name__)
            status, text, engine, detail = "failed", "", None, f"Ошибка распознавания ({type(e).__name__})"
        with SessionLocal() as db:
            a = db.get(A, aid)
            if a is not None:
                a.ocr_status, a.ocr_text, a.ocr_engine = status, text or None, engine
                a.ocr_detail = redact(detail) if detail else None
                db.commit()


def compose_ocr(texts: list[str], said: list[str]) -> str | None:
    """ads.ocr: тексты OCR, затем абзацы "Речь в ролике: …". Предел AD_TEXT_MAX: обрезается OCR, а не расшифровка
    (её у ролика не больше speech.TEXT_MAX)."""
    tail = "\n\n".join(f"{SPEECH_MARK} {t}" for t in said)
    room = max(AD_TEXT_MAX - len(tail) - 2, AD_TEXT_MAX // 2) if tail else AD_TEXT_MAX
    head = ocr_engine.clip("\n\n".join(texts), room) if texts else ""
    out = "\n\n".join(p for p in (head, tail) if p)
    return ocr_engine.clip(out) if out else None


def _ocr_text(item_id: int, ad_id: str) -> bool:
    """Текст OCR публикации - распознанное по изображениям и кадрам в порядке публикации, без повторов, и расшифровки
    речи роликов. True - ad.ocr изменился(UPDATE без decision_rev: это не решение аналитика)."""
    with SessionLocal() as db:
        texts: list[str] = []
        said: list[str] = []
        for a in _assets(db, item_id):
            if not a.current or a.status != "downloaded":
                continue
            if a.kind in ("image", "frame") and a.ocr_status == "done":
                t = (a.ocr_text or "").strip()
                if t and t not in texts:
                    texts.append(t)
            elif a.kind == "video" and a.parent_id is None:
                sp = (a.meta or {}).get("speech")
                t = (sp.get("text") or "").strip() if isinstance(sp, dict) and sp.get("status") == "done" else ""
                if t and t not in said:
                    said.append(t)
        ocr = compose_ocr(texts, said)
        current = db.scalar(select(models.Ad.ocr).where(models.Ad.id == ad_id))
        if (current or None) == ocr:
            return False
        db.execute(update(models.Ad).where(models.Ad.id == ad_id).values(ocr=ocr)
                   .execution_options(synchronize_session=False))
        db.commit()
        return True


def _rules(ad_id: str) -> tuple[dict, bool, str | None, str]:
    """Правила saq-ai-1 по тексту и OCR; при SAQ_COLLECTED_AUTOLINK - привязка-гипотеза условным UPDATE.
    (результат анализа, привязка назначена, почему не назначена, источник публикации)."""
    s = get_settings()
    A = models.Ad
    with SessionLocal() as db:
        ad = db.get(A, ad_id)
        cat = world.catalog(db, exclude_ad=ad.id)
        m = ads_service.match_obj(ad)
        res = ai.analyze(cat, ad.text, ad.ocr, m["account"], ad.platform, m["url"], rejected=ad.rejected_links or [])
        best = res["hypotheses"][0] if res["hypotheses"] else None
        linked, why_not = False, None
        if s.collected_autolink and best is not None and best["confidence"] >= T["lowConfidence"]:
            if ad.link_confirmed_by or ad.status == "confirmed" or ad.complex_id is not None:
                why_not = "у публикации уже есть привязка"
            else:
                r = db.execute(update(A).where(A.id == ad.id, A.decision_rev == (ad.decision_rev or 0),
                                               A.complex_id.is_(None), A.link_confirmed_by.is_(None))
                               .values(complex_id=best["complexId"], match_confidence=best["confidence"],
                                       decision_rev=A.decision_rev + 1)
                               .execution_options(synchronize_session=False))
                linked = r.rowcount == 1
                why_not = None if linked else "решение по публикации изменилось во время анализа"
                db.commit()
        return res, linked, why_not, ad.source


def _ocr_event(cur: list[models.MediaAsset]) -> str | None:
    """Событие OCR по изображениям и кадрам. Сбой распознавания(Tesseract упал, не уложился, файл не разобран) не
    выглядит как "текста нет": ничего не распознано - "OCR не выполнен: …", часть - счёт нераспознанных."""
    imgs = [a for a in cur if a.kind in ("image", "frame") and a.status == "downloaded"]
    if not imgs:
        return None
    off = next((a.ocr_detail for a in imgs if a.ocr_status == "unavailable"), None)
    if any(a.ocr_status == "unavailable" for a in imgs):
        return f"OCR не выполнен: {off or ocr_engine.NOT_INSTALLED}"
    failed = [a for a in imgs if a.ocr_status == "failed"]
    n_img = sum(a.ocr_status == "done" and a.kind == "image" for a in imgs)
    n_fr = sum(a.ocr_status == "done" and a.kind == "frame" for a in imgs)
    if not n_img and not n_fr:
        if failed:
            return f"OCR не выполнен: {failed[0].ocr_detail or 'ошибка распознавания'}"
        return None
    parts = []
    if n_img:
        parts.append(f"{n_img} {_plural(n_img, 'изображение', 'изображения', 'изображений')}")
    if n_fr:
        parts.append(f"{n_fr} {_plural(n_fr, 'кадр', 'кадра', 'кадров')} видео")
    what = f"OCR: распознан текст ({', '.join(parts)})"
    if failed:
        what += f"; не распознано файлов: {len(failed)} ({failed[0].ocr_detail or 'ошибка распознавания'})"
    return what


def _image_event(cur: list[models.MediaAsset], only: set | None) -> str | None:
    """Изображения публикации, которые не загрузились(ошибка, недоступно, превышен объём): сбой не выглядит как
    публикация без картинок. only - только эти файлы(новая неудача после первого анализа)."""
    bad = [a for a in cur if a.kind == "image" and a.parent_id is None
           and a.status in ("failed", "unavailable", "skipped") and (only is None or a.id in only)]
    if not bad:
        return None
    error = bad[0].error or "причина не указана"
    rest = any(a.parent_id is None and a.status == "downloaded" and a.kind in ("image", "video") for a in cur)
    tail = "проанализированы остальные файлы и текст" if rest else "проанализирован только текст"
    if len(bad) == 1:
        return f"Изображение не загружено ({error}) — {tail}"
    return f"Изображения не загружены: {len(bad)} ({error}) — {tail}"


def _video_event(cur: list[models.MediaAsset], only: set | None) -> str | None:
    for a in cur:
        if a.kind != "video" or a.parent_id is not None or (only is not None and a.id not in only):
            continue
        if a.status in ("failed", "unavailable", "skipped"):
            return f"Видео не загружено ({a.error or 'причина не указана'}) — проанализирована только обложка/текст"
        if a.ocr_status in ("unavailable", "failed"):
            return (f"Кадры видео не распознаны ({a.ocr_detail or 'причина не указана'}) — проанализирована только "
                    "обложка/текст")
    return None


def _speech_event(cur: list[models.MediaAsset], heard: set) -> str | None:
    """Событие распознавания речи по роликам этого анализа. Нет звука или речи и выключенный этап - без события; сбой
    движка, тайм-аут и слишком длинный ролик не выглядят как "в ролике молчат"."""
    sps = [(a.meta or {}).get("speech") or {} for a in cur if a.id in heard and a.kind == "video"]
    done = [s for s in sps if s.get("status") == "done"]
    bad = [s for s in sps if s.get("status") in _SPEECH_RETRY and s.get("detail") != speech.DISABLED]
    if not done:
        return f"Речь в ролике не распознана: {bad[0].get('detail') or 'причина не указана'}" if bad else None
    langs = ", ".join(dict.fromkeys(speech.LANG_LABELS.get(s.get("language"), s.get("language") or "?") for s in done))
    n = len(done)
    what = (f"Речь в ролике распознана ({langs}) — текст добавлен к OCR" if n == 1 else
            f"Речь распознана в {n} {_plural(n, 'ролике', 'роликах', 'роликах')} ({langs}) — текст добавлен к OCR")
    if bad:
        what += f"; не распознано роликов: {len(bad)} ({bad[0].get('detail') or 'причина не указана'})"
    return what


def _hypothesis_event(res: dict, linked: bool, source: str) -> tuple[str, str | None]:
    low = T["lowConfidence"]
    hyps = res.get("hypotheses") or []
    best = hyps[0] if hyps else None
    if best is not None and best["confidence"] >= low:
        what = (f"Гипотеза ИИ: ЖК «{best['complexName']}», уверенность {ads_service._pct(best['confidence'])}% "
                f"({ads_service._why(best)}) — нужна проверка аналитиком")
        if not linked:
            what += ". Привязка не назначена автоматически: публикация собрана из внешнего источника"
        cid = best["complexId"]
    else:
        what = "ИИ: ЖК не определён — нужна ручная привязка"
        if best is not None:
            what += (f" (лучшая гипотеза — «{best['complexName']}», {ads_service._pct(best['confidence'])}%, ниже "
                     f"порога {ads_service._pct(low)}%)")
        cid = None
    if source == "bot":
        what += ". Аккаунт и ссылка указаны жителем — в гипотезе не учитывались, сверьте вручную"
    return what, cid


def _terms_event(res: dict) -> str | None:
    kinds = list(dict.fromkeys(t["kind"] for t in res.get("terms") or [] if t.get("kind") != "price"))
    labels = [TERM_LABELS.get(k, k) for k in kinds]
    return f"ИИ: найдены условия оплаты — {', '.join(labels)} — нужна проверка аналитиком" if labels else None


# ---------- задание ----------

def last_rules_hypothesis(db, ad_id: str) -> tuple[bool, str | None]:
    """(было ли событие гипотезы правил, её ЖК) - последнее "Гипотеза ИИ: ЖК …" / "ИИ: ЖК не определён" от "Системы"."""
    from .screening import rules_hypotheses

    got = rules_hypotheses(db, [ad_id])
    return ad_id in got, got.get(ad_id)


def _rules_only(job_id: int | None, ad_id: str, item_id: int, v: int, reason: str,
                clock: Callable[[], datetime]) -> dict:
    """Пересчёт правил сопоставления по проанализированной версии(без файлов, OCR и Claude)."""
    I = models.IngestItem
    _stage(job_id, item_id, "rules")
    res, linked, why_not, source = _rules(ad_id)
    _stage(job_id, item_id, "events")
    best = (res.get("hypotheses") or [None])[0]
    cid = best["complexId"] if best is not None and best["confidence"] >= T["lowConfidence"] else None
    with SessionLocal() as db:
        seen, last = last_rules_hypothesis(db, ad_id)
        if linked or not seen or last != cid:
            what, ev_cid = _hypothesis_event(res, linked, source)
            ads_service._event(db, "ai", SYSTEM, f"{what} (пересчёт правил: {reason})", at=clock(), ad_id=ad_id,
                               complex_id=ev_cid)
        db.execute(update(I).where(I.id == item_id, I.analysis_status.in_(("queued", "running")))
                   .values(analysis_status="done", analysis_stage=None, analysis_error=None)
                   .execution_options(synchronize_session=False))
        db.commit()
    out = {"engine": ai.ENGINE, "version": v, "rulesOnly": True,
           "hypotheses": [{"complexId": h["complexId"], "confidence": h["confidence"]} for h in res["hypotheses"]],
           "autolinked": linked}
    if why_not:
        out["autolinkSkipped"] = why_not
    return out


def run_analysis(job_id: int | None, ad_id: str, *, now: datetime, force: bool = False,
                 rules_only: bool = False, reason: str | None = None) -> dict:
    """Анализ публикации ad_id(задание job_id): этапы media → frames → speech → ocr → rules → llm → events. Итог - jobs.result.
    now - время захвата задания; сроки повтора загрузок и время событий - от текущего момента(clock). force -
    "Проанализировать ещё раз": анализ идёт и по уже проанализированной версии, Claude запрашивается снова. rules_only -
    только пересчёт правил по проанализированной версии(без Claude); force его отменяет."""
    now = models.as_utc(now) or models.utcnow()
    t0 = MONOTONIC()

    def clock() -> datetime:
        return now + timedelta(seconds=MONOTONIC() - t0)

    I = models.IngestItem
    with SessionLocal() as db:
        ad = db.get(models.Ad, ad_id) if ad_id else None
        if ad is None:
            return {"skipped": "ad gone"}
        item = db.scalar(select(I).where(I.ad_id == ad_id))
        if item is None:
            return {"skipped": "no ingest item"}
        v, item_id, prev = item.version, item.id, item.analyzed_version
        rules_only = rules_only and not force and prev == v  # новая версия - обычный анализ
        if rules_only:
            item.analysis_status, item.analysis_error = "running", None
            db.commit()
            return _rules_only(job_id, ad_id, item_id, v, reason or "справочник ЖК изменился", clock)
        if not force and prev == v and not _has_work(_assets(db, item_id), now) and not llm.due(db, ad_id, now):
            db.execute(update(I).where(I.id == item_id, I.analysis_status.in_(("queued", "running")))
                       .values(analysis_status="done", analysis_stage=None, analysis_error=None)
                       .execution_options(synchronize_session=False))
            db.commit()
            return {"skipped": "up to date", "version": v}
        item.analysis_status, item.analysis_error = "running", None
        db.commit()
    first = prev != v
    changed: set = set()  # файлы, которые в этом анализе не загрузились или не дали кадров

    _stage(job_id, item_id, "media")
    _media_stage(item_id, now, changed, clock)
    _stage(job_id, item_id, "frames")
    _frames_stage(item_id, now, changed)
    heard: set = set()  # видео, речь которых распознана(или не распознана) в этом анализе
    _stage(job_id, item_id, "speech")
    _speech_stage(item_id, force, heard)
    _stage(job_id, item_id, "ocr")
    _ocr_stage(item_id)
    ocr_changed = _ocr_text(item_id, ad_id)
    _stage(job_id, item_id, "rules")
    res, linked, why_not, source = _rules(ad_id)
    claude = None
    if not get_settings().missing("ANTHROPIC_API_KEY"):
        _stage(job_id, item_id, "llm")
        claude = llm.run_stage(ad_id, item_id, v, res.get("hypotheses") or [], now=clock(), force=force)
    _stage(job_id, item_id, "events")

    suffix = f" (версия {v})" if v > 1 else ""
    with SessionLocal() as db:
        cur = [a for a in _assets(db, item_id) if a.current]
        events: list[tuple[str, str, str | None]] = []
        if ocr_changed or first:
            what = _ocr_event(cur)
            if what:
                events.append(("system", what, None))
        if heard:
            what = _speech_event(cur, heard)
            if what:
                events.append(("system", what, None))
        if first or changed:
            for what in (_image_event(cur, None if first else changed), _video_event(cur, None if first else changed)):
                if what:
                    events.append(("system", what, None))
        if first or linked:  # привязка-гипотеза, назначенная при повторном запуске, тоже видна в журнале
            what, cid = _hypothesis_event(res, linked, source)
            events.append(("ai", what + suffix, cid))
        if first:
            terms = _terms_event(res)
            if terms:
                events.append(("ai", terms + suffix, None))
        if claude is not None and claude.event is not None and (claude.fresh or first):
            events.append((claude.event[0], claude.event[1] + suffix, None))  # гипотезы Claude - без привязки к ЖК
        at = clock()
        for kind, what, cid in events:
            ads_service._event(db, kind, SYSTEM, what, at=at, ad_id=ad_id, complex_id=cid)
        counts = {k: sum(a.ocr_status == k for a in cur if a.kind in ("image", "frame"))
                  for k in ("done", "empty", "unavailable", "failed")}
        heard_st = [((a.meta or {}).get("speech") or {}).get("status") for a in cur if a.id in heard]
        speech_counts = {k: heard_st.count(k) for k in dict.fromkeys(heard_st) if k}
        retry_times = [t for t in (_retry_at(a) for a in cur if a.status == "pending" and a.parent_id is None) if t]
        llm_retry = claude.retry_at if claude is not None else None
        db.execute(update(I).where(I.id == item_id, or_(I.analyzed_version.is_(None), I.analyzed_version <= v))
                   .values(analyzed_version=v, analyzed_engine=ai.ENGINE, analyzed_at=at)
                   .execution_options(synchronize_session=False))
        db.execute(update(I).where(I.id == item_id, I.version == v, I.analysis_status == "running")
                   .values(analysis_status="done", analysis_stage=None, analysis_error=None)
                   .execution_options(synchronize_session=False))
        if retry_times or llm_retry:  # файлы или запрос к Claude ждут повтора - новое задание к сроку первого повтора
            from . import jobs

            jobs.enqueue(db, "analyze", ad_id=ad_id, content_version=v, dedupe_key=f"analyze:{ad_id}",
                         run_after=min(retry_times + ([llm_retry] if llm_retry else [])), priority=0)
            if retry_times:  # повтор только запроса к Claude - правила и OCR готовы, анализ не "в очереди"
                db.execute(update(I).where(I.id == item_id).values(analysis_status="queued")
                           .execution_options(synchronize_session=False))
        db.commit()
    out = {"engine": ai.ENGINE, "version": v, "ocr": counts,
           "terms": list(dict.fromkeys(t["kind"] for t in res.get("terms") or [])),
           "hypotheses": [{"complexId": h["complexId"], "confidence": h["confidence"]} for h in res["hypotheses"]],
           "autolinked": linked}
    if why_not:
        out["autolinkSkipped"] = why_not
    if speech_counts:
        out["speech"] = speech_counts
    if retry_times:
        out["mediaRetryAt"] = models.iso(min(retry_times))
    if claude is not None:
        out["llm"] = claude.summary()
    if force:
        out["force"] = True
    return out
