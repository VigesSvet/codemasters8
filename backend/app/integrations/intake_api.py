"""Приём публикаций через API (POST /api/intake/publications, integration-spec §7.4) — подключение только со статусом:
публикации присылает внешний сборщик по серверному токену INTEGRATION_INGEST_TOKEN, проверки и сбора у него нет.

Статус живой: токена нет — «не настроен»; последний отказ клиенту новее последней принятой публикации — 401/403 «нет
доступа» (клиент обращается с неверным токеном), 409/413/415/422 и сбой сервера (500) — «ошибка» (публикация
отклонена или не сохранена); публикация за последние 7 дней — «принимает публикации»; иначе — «ожидает публикаций»."""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from .. import models
from .base import Connector, when_text

RECENT = timedelta(days=7)


def intake_status(db, row, now: datetime, *, collector: str, last_at: Optional[datetime], denied: str,
                  rejected: str, working: str, never: str) -> tuple[str, str]:
    """Общее правило статуса приёмника (API и бот): отказ новее последнего успеха → denied / error; успех за 7 дней →
    working; иначе waiting. Тексты — с «{when}» и «{detail}»."""
    from . import service

    rej = service.last_reject(row)
    if rej is not None and (last_at is None or rej["at"] > last_at):
        when = when_text(rej["at"])
        if rej["code"] in (401, 403):
            return "denied", denied.format(when=when)
        return "error", rejected.format(detail=rej["detail"] or f"HTTP {rej['code']}", when=when)
    if last_at is not None and last_at >= models.as_utc(now) - RECENT:
        return "working", working.format(when=when_text(last_at))
    if last_at is not None:
        return "waiting", working.format(when=when_text(last_at))
    return "waiting", never


class IntakeApiConnector(Connector):
    code = "intake"
    kind = "intake"
    platform = None
    group = "intake"
    name = "Приём публикаций API"
    via = "POST /api/intake/publications"
    required_env = ("INTEGRATION_INGEST_TOKEN",)
    capabilities = {"test": False, "collect": False, "toggle": False, "configure": False, "connect": False}
    secret_refs = ("env:INTEGRATION_INGEST_TOKEN",)
    labels = {"working": "принимает публикации", "waiting": "ожидает публикаций"}
    limitation = ("Принимает нормализованные публикации от внешних сборщиков по серверному токену; решения аналитика "
                  "через приём не передаются.")

    def computed_status(self, db, row, now: datetime) -> Optional[tuple[str, str]]:
        I = models.IngestItem
        last = models.as_utc(db.scalar(select(func.max(I.collected_at)).where(I.collector == self.code)))
        return intake_status(db, row, now, collector=self.code, last_at=last,
                             denied="Клиент приёма обращается с неверным токеном (последняя попытка {when})",
                             rejected="Последняя публикация отклонена: {detail}",
                             working="Последняя публикация: {when}", never="Публикаций через API ещё не было")


CONNECTOR = IntakeApiConnector()
router = None
