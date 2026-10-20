"""Серверная проверка доступа к подключениям: администратор, клиент приёма публикаций, бот заявок.

Три отдельных секрета(SAQ_ADMIN_TOKEN, INTEGRATION_INGEST_TOKEN, SERVICE_API_TOKEN), без них - отказ(fail-closed):
секрет не задан или слабый(settings: короче 24 символов, меньше 12 разных; токен администратора не совпадает с
другими) - 503, заголовка нет - 401(WWW-Authenticate: Bearer), токен неверный - 403. Сравнение - hmac.compare_digest.
Отказы приёма публикаций и бота(401/403) записываются(service.record_reject, без токена) и видны в статусе
подключения: клиент с неверным токеном выглядит как "нет доступа", а не как "ожидает заявок".
Чтение состояния подключений токена не требует и секретов не содержит; полные сведения(пояснения, ошибки, настройки,
факты проверки) - только с токеном администратора(is_admin), посетителю - статусы и счётчики.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request

from .settings import get_settings

log = logging.getLogger("saq.auth")

ADMIN_UNSET = "Администрирование подключений не настроено: задайте SAQ_ADMIN_TOKEN на сервере"
ADMIN_MISSING = "Нужен токен администратора"
ADMIN_WRONG = "Неверный токен администратора"
INGEST_UNSET = "Приём публикаций не настроен: задайте INTEGRATION_INGEST_TOKEN на сервере"
INGEST_MISSING = "Нужен токен приёма публикаций"
INGEST_WRONG = "Неверный токен приёма публикаций"
SERVICE_UNSET = "Приём заявок бота не настроен: задайте SERVICE_API_TOKEN на сервере"
SERVICE_MISSING = "Нужен сервисный токен бота"
SERVICE_WRONG = "Неверный сервисный токен"


@dataclass(frozen=True)
class Admin:
    """Проверенный администратор. fp - отпечаток его токена(sha256[:12]): привязка OAuth state, не секрет."""
    fp: str


def token_fp(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def _bearer(request: Request) -> Optional[str]:
    """Значение "Authorization: Bearer <токен>"(схема без учёта регистра) или None."""
    scheme, _, value = (request.headers.get("authorization") or "").strip().partition(" ")
    value = value.strip()
    return value if scheme.lower() == "bearer" and value else None


def _same(got: str, secret: str) -> bool:
    return hmac.compare_digest(got.encode(), secret.encode())


def _record(code: str, status: int, detail: str) -> None:
    """Отказ клиента приёма/бота - в состояние подключения(service.record_reject сам не падает и ограничивает частоту
    записи). Учёт отказов не должен менять ответ: любая ошибка здесь - только предупреждение в журнал API."""
    try:
        from .integrations import service

        service.record_reject(code, status, detail)
    except Exception:  # noqa: BLE001
        log.warning("Отказ доступа (%s, HTTP %s) не записан в состояние подключения", code, status, exc_info=True)


def _check(got: Optional[str], secret: str, unset: str, missing: str, wrong: str, record: Optional[str]) -> None:
    if not secret:
        raise HTTPException(503, unset)
    got = (got or "").strip()
    if not got:
        if record:
            _record(record, 401, missing)
        raise HTTPException(401, missing, headers={"WWW-Authenticate": "Bearer"})
    if not _same(got, secret):
        if record:
            _record(record, 403, wrong)
        raise HTTPException(403, wrong)


def require_admin(request: Request) -> Admin:
    """Администратор подключений: X-Admin-Token или Authorization: Bearer."""
    secret = get_settings().admin_token.get()
    _check(request.headers.get("x-admin-token") or _bearer(request), secret, ADMIN_UNSET, ADMIN_MISSING, ADMIN_WRONG,
           None)
    return Admin(token_fp(secret))


def is_admin(request: Request) -> bool:
    """Чтение состояния подключений: верный токен администратора - полные сведения; без токена, с неверным или при
    ненастроенном администрировании - сведения для посетителя(чтение открыто, отказа нет)."""
    secret = get_settings().admin_token.get()
    got = (request.headers.get("x-admin-token") or _bearer(request) or "").strip()
    return bool(secret and got and _same(got, secret))


def require_ingest(request: Request) -> None:
    """Клиент приёма публикаций(POST /api/intake/publications): только Authorization: Bearer."""
    _check(_bearer(request), get_settings().ingest_token.get(), INGEST_UNSET, INGEST_MISSING, INGEST_WRONG, "intake")


def require_service(request: Request) -> None:
    """Бот заявок(saq-telegram-bot): Authorization: Bearer или X-API-Key."""
    _check(_bearer(request) or request.headers.get("x-api-key"), get_settings().service_token.get(), SERVICE_UNSET,
           SERVICE_MISSING, SERVICE_WRONG, "telegram_bot")
