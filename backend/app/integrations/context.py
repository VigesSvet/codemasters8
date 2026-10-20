"""RunContext - всё, что подключение получает от воркера на время одного запуска(integration-spec §10.3).

Каждая запись(приём публикации, отметка "видели", ошибка записи, заметка, счётчики, состояние, фазы задания, внешний
запуск, квота) - в своей короткой сессии с commit: сеть, OCR и ffmpeg никогда не выполняются при открытой транзакции
записи. Счётчики запуска - атомарным UPDATE collection_runs SET x = x + :n(не чтение-изменение-запись).

Счётчики файлов(media_saved / media_failed) считает приём(ingest): файл учитывается один раз - когда публикация с ним
принята, а не когда его скачал download_media.

RunContext.ephemeral(code) - для проверки из командной строки(smoke): без БД, состояние - в памяти, приём публикаций
запрещён, квота не учитывается.
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional, Union

import httpx
from sqlalchemy import func, select, update

from .. import ingest, media, models
from ..ads_service import COLLECTOR
from ..db import SessionLocal
from ..logs import redact
from ..settings import Settings, get_settings
from . import http
from .base import Connector, MediaIn, MediaKind, Publication

if TYPE_CHECKING:
    from ..ingest import IngestResult, Known

log = logging.getLogger("saq.integrations")

NOTES_MAX = 100
COUNTERS = ("seen", "new", "updated", "unchanged", "failed", "media_saved", "media_failed")
_COLUMNS = {"seen": "items_seen", "new": "items_new", "updated": "items_updated", "unchanged": "items_unchanged",
            "failed": "items_failed", "media_saved": "media_saved", "media_failed": "media_failed"}
_UNAVAILABLE = ("http_403", "http_404", "http_410")
# после сигнала остановки воркера идущая загрузка файла получает столько секунд, потом прерывается(cancelled):
# так остановка укладывается в stop_grace_period контейнера и не превращается в SIGKILL посреди большого видео
DOWNLOAD_STOP_GRACE_S = 30.0


class RunContext:
    """Контекст запуска подключения. config, state, payload - копии; сохраняются только через save_state/save_payload."""

    def __init__(self, code: str, connector: Connector, *, settings: Optional[Settings] = None,
                 run_id: Optional[int] = None, job_id: Optional[int] = None, trigger: str = "manual",
                 config: Optional[dict] = None, state: Optional[dict] = None, payload: Optional[dict] = None,
                 now: Optional[datetime] = None, stop: Optional[threading.Event] = None,
                 external_run_id: Optional[str] = None, ephemeral: bool = False,
                 owner: Optional[tuple[str, int]] = None):
        """owner - захват задания(воркер, число попыток): save_payload пишет фазы, только пока задание у него
        (jobs.fence); None - без проверки(тесты, прямой вызов)."""
        from .. import jobs

        self.code = code
        self.connector = connector
        self.settings = settings or get_settings()
        self.run_id, self.job_id, self.trigger = run_id, job_id, trigger
        self.config = dict(config or {})
        self.state = dict(state or {})
        self.payload = dict(payload or {})
        self._base = models.as_utc(now)
        self._started = time.monotonic()
        self._stop = stop
        self._external_run_id = external_run_id
        self._ephemeral = ephemeral
        self._owner = owner
        self._soft_deadline = jobs.SOFT_DEADLINE_S
        self._foreign_noted: set[str] = set()
        self.counts = {k: 0 for k in COUNTERS}
        self.notes: list[dict] = []  # заметки этого запуска(в эфемерном режиме - только здесь)
        self.partial_reasons: list[str] = []
        self.postponed: Optional[float] = None
        self.first_failure: Optional[str] = None
        self._stop_seen: Optional[float] = None  # когда загрузка файла впервые увидела остановку воркера

    @classmethod
    def ephemeral(cls, code: str, *, config: Optional[dict] = None, state: Optional[dict] = None) -> "RunContext":
        """Проверка из командной строки: без БД и запуска; save_state/save_payload меняют только память."""
        from . import registry

        return cls(code, registry.get(code), config=config, state=state, trigger="test", ephemeral=True)

    # ---------- время и остановка ----------

    @property
    def external_run_id(self) -> Optional[str]:
        """Внешний запуск этого запуска(Apify): есть - второй платный запуск не начинать."""
        return self._external_run_id

    def now(self) -> datetime:
        """Текущее время запуска(UTC): от времени, переданного воркером(execute(now=…)), плюс прошедшее."""
        if self._base is None:
            return models.utcnow()
        return self._base + timedelta(seconds=time.monotonic() - self._started)

    def time_left(self) -> float:
        """Сколько секунд осталось до мягкого срока запуска(jobs.SOFT_DEADLINE_S)."""
        return self._soft_deadline - (time.monotonic() - self._started)

    def stopping(self) -> bool:
        """Воркер останавливается: подключение завершает текущий шаг и выходит(mark_partial)."""
        return self._stop is not None and self._stop.is_set()

    def _download_cancelled(self) -> bool:
        """Остановка воркера: загрузке файла даётся DOWNLOAD_STOP_GRACE_S с с момента, как её заметили, затем она
        прерывается."""
        if not self.stopping():
            return False
        if self._stop_seen is None:
            self._stop_seen = time.monotonic()
        return time.monotonic() - self._stop_seen > DOWNLOAD_STOP_GRACE_S

    def sleep(self, seconds: float) -> None:
        """Пауза между запросами(http.SLEEP - тесты подменяют); при остановке воркера - сразу возврат."""
        if seconds <= 0 or self.stopping():
            return
        if self._stop is not None and http.SLEEP is time.sleep:
            self._stop.wait(seconds)
        else:
            http.SLEEP(seconds)

    # ---------- сеть ----------

    def client(self, *, timeout: float = 20.0, headers: Optional[dict] = None, base_url: str = "",
               follow_redirects: bool = False) -> httpx.Client:
        return http.client(timeout=timeout, headers=headers, base_url=base_url, follow_redirects=follow_redirects)

    def download_media(self, url: str, *, key: str, kind: MediaKind = "image", role: Optional[str] = None,
                       allowed_hosts: Optional[Iterable[str]] = None, auth_headers: Optional[dict] = None,
                       auth_hosts: Iterable[str] = (), max_bytes: Optional[int] = None) -> MediaIn:
        """Файл по ссылке → хранилище. Никогда не бросает: ошибка - MediaIn "failed"(403/404/410 - "unavailable")
        с причиной без query и токенов. При остановке воркера загрузка прерывается через DOWNLOAD_STOP_GRACE_S с."""
        allowed = media.VIDEO_TYPES if kind == "video" else media.IMAGE_TYPES
        origin = http.redact_url(url)
        try:
            total = max(5.0, min(600.0, self.time_left()))
            f = http.fetch_to_temp(url, max_bytes=max_bytes or media.limit_for(kind), allowed_mime=allowed,
                                   allowed_hosts=allowed_hosts, auth_headers=auth_headers, auth_hosts=auth_hosts,
                                   total_timeout=total, cancel=self._download_cancelled)
            stored = media.store_file(f.path, allowed=allowed)
            return MediaIn(key=key, kind=kind, role=role, stored=stored, status="downloaded", origin_url=origin)
        except http.DownloadError as e:
            status = "unavailable" if e.code in _UNAVAILABLE else "failed"
            return MediaIn(key=key, kind=kind, role=role, status=status, error=e.detail, origin_url=origin)
        except media.MediaRejected as e:
            return MediaIn(key=key, kind=kind, role=role, status="failed", error=e.detail, origin_url=origin)
        except ValueError as e:  # учётные данные в обычных заголовках - ошибка подключения, файл не загружается
            return MediaIn(key=key, kind=kind, role=role, status="failed", error=redact(str(e)), origin_url=origin)
        except Exception as e:  # noqa: BLE001 - сбой одного файла не останавливает запуск
            log.warning("Файл %s (%s) не загружен: %s", key, origin, type(e).__name__)
            return MediaIn(key=key, kind=kind, role=role, status="failed",
                           error=f"Файл не загружен ({type(e).__name__})", origin_url=origin)

    def store_media(self, data: Union[bytes, Path], *, key: str, kind: MediaKind = "image",
                    role: Optional[str] = None, origin_ref: Optional[str] = None,
                    meta: Optional[dict] = None) -> MediaIn:
        """Байты или временный файл(забирается) → хранилище; вид файла - по сигнатуре. Никогда не бросает."""
        try:
            stored = media.store_file(Path(data)) if isinstance(data, (str, Path)) else media.store_bytes(bytes(data))
        except media.MediaRejected as e:
            return MediaIn(key=key, kind=kind, role=role, status="failed", error=e.detail, origin_ref=origin_ref,
                           meta=dict(meta or {}))
        except Exception as e:  # noqa: BLE001
            log.warning("Файл %s не сохранён: %s", key, type(e).__name__)
            return MediaIn(key=key, kind=kind, role=role, status="failed",
                           error=f"Файл не сохранён ({type(e).__name__})", origin_ref=origin_ref,
                           meta=dict(meta or {}))
        real = "video" if stored.mime in media.VIDEO_TYPES else "image"
        return MediaIn(key=key, kind=real, role=role, stored=stored, status="downloaded", origin_ref=origin_ref,
                       meta=dict(meta or {}))

    def temp_path(self, suffix: str = "") -> Path:
        """Уникальный путь во временном каталоге хранилища(файл не создаётся) - цель загрузки Telethon."""
        return media.tmp_dir() / f"ctx-{uuid.uuid4().hex}{suffix}"

    # ---------- приём ----------

    def _namespace(self, namespace: Optional[str]) -> str:
        return namespace or self.connector.platform or self.code

    def known(self, external_id: str, *, namespace: Optional[str] = None) -> Optional["Known"]:
        """Что уже принято по внешнему ID(без сети): какие файлы загружены, последняя версия содержимого."""
        if self._ephemeral:
            return None
        with SessionLocal() as db:
            return ingest.known(db, self._namespace(namespace), str(external_id))

    def ingest(self, pub: Publication, *, note: Optional[str] = None) -> "IngestResult":
        """Публикация → единый приём: файлы, которые ещё нужны, скачиваются(без открытой сессии), затем запись - в
        своей транзакции. Собранная другим источником - не перезаписывается(учитывается как "без изменений")."""
        if self._ephemeral:
            raise RuntimeError("Проверка из командной строки публикации не сохраняет")
        k = self.known(pub.external_id, namespace=pub.namespace or pub.platform)
        if k is not None and k.collector != self.code:
            res = ingest.IngestResult("foreign", k.ad_id, k.item_id, k.version, 0, 0, False, owner=k.collector)
        else:
            resolved = ingest.resolve_media(pub, k)
            note = note or f"сборщик «{self.connector.name}», запуск №{self.run_id}"
            with SessionLocal() as db:
                try:
                    res = ingest.ingest_publication(db, resolved, collector=self.code, run_id=self.run_id,
                                                    actor=COLLECTOR, note=note, external_run_id=self.external_run_id)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
        if res.status == "foreign":
            if res.owner not in self._foreign_noted:
                self._foreign_noted.add(res.owner or "")
                self.note(f"Публикация {pub.external_id} уже получена через «{self._owner_name(res.owner)}» — "
                          "не перезаписывается")
            self._bump(seen=1, unchanged=1)
        else:
            status = {"created": "new", "updated": "updated"}.get(res.status, "unchanged")
            self._bump(seen=1, **{status: 1}, media_saved=res.media_new, media_failed=res.media_failed)
        return res

    @staticmethod
    def _owner_name(owner: Optional[str]) -> str:
        from . import registry

        try:
            return registry.get(owner or "").name
        except KeyError:
            return owner or "другой источник"

    def touch(self, external_id: str, *, namespace: Optional[str] = None) -> bool:
        """Публикация есть в источнике и не менялась(без содержимого): "последний раз видели" и счётчики."""
        if self._ephemeral:
            return False
        I = models.IngestItem
        values = {"collected_at": models.as_utc(self.now())}
        if self.run_id is not None:
            values["last_run_id"] = self.run_id
        with SessionLocal() as db:
            res = db.execute(update(I).where(I.platform == self._namespace(namespace),
                                             I.external_id == str(external_id), I.collector == self.code)
                             .values(**values).execution_options(synchronize_session=False))
            db.commit()
        if res.rowcount != 1:
            return False
        self._bump(seen=1, unchanged=1)
        return True

    def item_failed(self, text: str) -> None:
        """Запись источника не принята(аккаунт не найден, ошибка разбора…): счётчики и заметка-предупреждение."""
        text = redact(text) or "запись не принята"
        if self.first_failure is None:
            self.first_failure = text
        self._bump(seen=1, failed=1)
        self.note(text, "warn")

    # ---------- запуск ----------

    def _bump(self, **counts: int) -> None:
        counts = {k: int(v) for k, v in counts.items() if v}
        for k, v in counts.items():
            self.counts[k] += v
        if not counts or self.run_id is None or self._ephemeral:
            return
        R = models.CollectionRun
        values = {_COLUMNS[k]: getattr(R, _COLUMNS[k]) + v for k, v in counts.items()}
        with SessionLocal() as db:
            db.execute(update(R).where(R.id == self.run_id).values(**values)
                       .execution_options(synchronize_session=False))
            db.commit()

    def note(self, text: str, level: str = "info") -> None:
        """Заметка в запуск(видна на странице "Подключения"): по-русски, маскируется, не больше 100 последних."""
        entry = {"at": models.iso(self.now()), "level": level if level in ("info", "warn", "error") else "info",
                 "text": redact(text) or ""}
        self.notes.append(entry)
        if self.run_id is None or self._ephemeral:
            return
        with SessionLocal() as db:
            run = db.get(models.CollectionRun, self.run_id)
            if run is not None:
                run.notes = (list(run.notes or []) + [entry])[-NOTES_MAX:]
                db.commit()

    def mark_partial(self, reason: str) -> None:
        """Запуск выполнен частично(лимит, время, закрытые карточки…): статус "работает частично" с причиной."""
        reason = redact(reason) or "часть данных не получена"
        self.partial_reasons.append(reason)
        self.note(reason, "warn")

    def postpone(self, seconds: float, reason: str) -> None:
        """Следующий запуск по расписанию - не раньше чем через seconds с(после успешного завершения)."""
        self.postponed = max(float(seconds), self.postponed or 0.0)
        self.note(reason)

    def save_state(self, **patch) -> None:
        """Дополнить состояние подключения(курсоры, кэш) - сразу в БД."""
        self.state = {**self.state, **patch}
        if self._ephemeral:
            return
        from . import service

        with SessionLocal() as db:
            row = service.row(db, self.code, create=True)
            row.state = {**(row.state or {}), **patch}
            db.commit()

    def save_payload(self, **patch) -> None:
        """Дополнить данные задания(фаза, смещение) - сразу в БД: после сбоя задание продолжит с этого места.
        Задание уже отдали другому воркеру(recover_stale / requeue_own) - его фазы не перезаписываются."""
        from .. import jobs

        self.payload = {**self.payload, **patch}
        if self._ephemeral or self.job_id is None:
            return
        worker_id, attempts = self._owner if self._owner is not None else (None, None)
        with SessionLocal() as db:
            job = jobs.fence(db, self.job_id, worker_id, attempts, what="данные задания не записаны")
            if job is not None:
                job.payload = {**(job.payload or {}), **patch}
                db.commit()

    def set_external_run(self, external_run_id: str, *, cost_usd: Optional[float] = None) -> None:
        """Внешний запуск(Apify Run ID) и его стоимость - сразу в БД(до следующего сетевого шага)."""
        self._external_run_id = str(external_run_id)[:64]
        if self._ephemeral or self.run_id is None:
            return
        R = models.CollectionRun
        values: dict = {"external_run_id": self._external_run_id}
        if cost_usd is not None:
            values["cost_usd"] = float(cost_usd)
        with SessionLocal() as db:
            db.execute(update(R).where(R.id == self.run_id).values(**values)
                       .execution_options(synchronize_session=False))
            db.commit()

    def spent_usd(self, since: datetime, *, unknown_cost: float = 0.0) -> float:
        """Расход этого подключения с момента since: сумма cost_usd запусков + unknown_cost за каждый запуск с внешним
        ID, стоимость которого ещё не известна."""
        if self._ephemeral:
            return 0.0
        R = models.CollectionRun
        since = models.as_utc(since)
        with SessionLocal() as db:
            known_cost = db.scalar(select(func.coalesce(func.sum(R.cost_usd), 0.0))
                                   .where(R.collector == self.code, R.created_at >= since)) or 0.0
            unknown = db.scalar(select(func.count(R.id)).where(R.collector == self.code, R.created_at >= since,
                                                              R.external_run_id.is_not(None),
                                                              R.cost_usd.is_(None))) or 0
        return float(known_cost) + unknown * float(unknown_cost)

    def reserve_quota(self, name: str, limit: int) -> bool:
        """Атомарно учесть один запрос квоты(до сетевого вызова); False - квота исчерпана. Проверка из командной
        строки квоту не учитывает."""
        if self._ephemeral:
            return True
        from . import service

        return service.reserve_quota(name, limit, self.now())

    def quota_used(self, name: str) -> int:
        if self._ephemeral:
            return 0
        from . import service

        with SessionLocal() as db:
            return service.quota_used(db, name)
