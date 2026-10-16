"""OpenAI — анализатор (Responses API): в этой версии не подключён (integration-spec D16). Подключение только показывает
статус: ключа нет — «не настроен»; ключ задан — «не используется» (анализ выполняют правила saq-ai-1 на сервере).
Ключ в браузер не передаётся и запросов к OpenAI нет."""
from datetime import datetime
from typing import Optional

from .base import Connector


class OpenAIAnalyzer(Connector):
    code = "openai"
    kind = "analyzer"
    platform = None
    group = "analyzer"
    name = "OpenAI — анализатор"
    via = "Responses API (в этой версии не подключён)"
    required_env = ("OPENAI_API_KEY",)
    capabilities = {"test": False, "collect": False, "toggle": False, "configure": False, "connect": False}
    secret_refs = ("env:OPENAI_API_KEY",)
    limitation = "Не используется: анализ выполняют правила saq-ai-1 на сервере."

    def computed_status(self, db, row, now: datetime) -> Optional[tuple[str, str]]:
        from ..settings import get_settings

        if get_settings().missing("OPENAI_API_KEY"):
            return "not_configured", "Нет на сервере: OPENAI_API_KEY"
        return "off", "Ключ задан, но анализ через OpenAI в этой версии не подключён — работают правила saq-ai-1"


CONNECTOR = OpenAIAnalyzer()
router = None
