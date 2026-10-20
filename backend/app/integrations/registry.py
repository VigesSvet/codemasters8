"""Реестр подключений(integration-spec §10.6): код → объект подключения, в порядке страницы "Подключения".

Каждый модуль подключения импортируется отдельно, в try/except: модуль, который не загрузился(ошибка в коде
подключения, нет зависимости), становится BrokenConnector со статусом "ошибка" и пояснением, остальные работают. Модули
подключений импортируют только base, http, media и помощники приёма; сервис(service) - лениво, внутри функций: реестр
загружается при импорте service, и ранний импорт service из модуля подключения был бы циклическим.
"""
import importlib
import logging
from typing import Literal
from collections.abc import Callable

from fastapi import APIRouter

from .base import Connector

log = logging.getLogger("saq.integrations")

# код → модуль; порядок - порядок на странице
MODULES = {"instagram": "instagram", "telegram": "telegram", "tiktok": "tiktok", "krisha": "krisha",
           "telegram_bot": "telegram_bot", "intake": "intake_api", "dgis": "dgis", "anthropic": "analyzer_anthropic",
           "olx": "olx"}
ConnectorCode = Literal["instagram", "telegram", "tiktok", "krisha", "telegram_bot", "intake", "dgis", "anthropic",
                        "olx"]
BROKEN = "Модуль подключения не загрузился — подробности в журнале API"
NOT_FOUND = "Подключение не найдено"

# Описание на случай, если модуль не загрузился: (вид, группа, название)
_FALLBACK = {"instagram": ("collector", "collectors", "Instagram"),
             "telegram": ("collector", "collectors", "Telegram-каналы"),
             "tiktok": ("collector", "collectors", "TikTok"), "krisha": ("collector", "collectors", "Krisha.kz"),
             "telegram_bot": ("intake", "intake", "Telegram-бот заявок"),
             "intake": ("intake", "intake", "Приём публикаций API"), "dgis": ("geo", "geo", "2ГИС — геокодер"),
             "anthropic": ("analyzer", "analyzer", "Анализатор ИИ (Anthropic)"),
             "olx": ("oauth", "limited", "OLX.kz")}


class BrokenConnector(Connector):
    """Подключение, модуль которого не загрузился: статус "ошибка", действия недоступны."""

    capabilities = {"test": False, "collect": False, "toggle": False, "configure": False, "connect": False}
    via = "модуль не загрузился"

    def __init__(self, code: str):
        kind, group, name = _FALLBACK.get(code, ("collector", "collectors", code))
        self.code, self.kind, self.group, self.name = code, kind, group, name

    def missing(self, s, row=None) -> list[str]:
        return []

    def computed_status(self, db, row, now):
        return "error", BROKEN


def load(importer: Callable[[str], object] = importlib.import_module) -> tuple[dict[str, Connector], list[APIRouter]]:
    """Импорт модулей подключений по одному: (подключения по коду, маршрутизаторы модулей)."""
    connectors: dict[str, Connector] = {}
    routers: list[APIRouter] = []
    for code, module in MODULES.items():
        try:
            mod = importer(f"{__package__}.{module}")
            conn = mod.CONNECTOR
            if not isinstance(conn, Connector) or conn.code != code:
                raise TypeError(f"CONNECTOR модуля {module} — не подключение «{code}»")
            router = getattr(mod, "router", None)
            if router is not None and not isinstance(router, APIRouter):
                raise TypeError(f"router модуля {module} — не APIRouter")
        except Exception:
            log.exception("Подключение %s: модуль %s не загрузился", code, module)
            connectors[code] = BrokenConnector(code)
            continue
        connectors[code] = conn
        if router is not None:
            routers.append(router)
    return connectors, routers


CONNECTORS, _ROUTERS = load()


def get(code: str) -> Connector:
    """Подключение по коду; нет такого - KeyError(маршруты отвечают 404 "Подключение не найдено")."""
    return CONNECTORS[code]


def find(code: str) -> Connector | None:
    return CONNECTORS.get(code)


def routers() -> list[APIRouter]:
    """Маршрутизаторы модулей подключений(бот заявок, 2ГИС, OLX)."""
    return list(_ROUTERS)
