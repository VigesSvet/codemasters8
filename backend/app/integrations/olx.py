"""OLX.kz (Partner API · OAuth) — подключение собственного аккаунта (integration-spec §12.7, план §10).

Старт (администратор, POST /api/integrations/olx/start) создаёт одноразовый state: 256 бит случайности, в БД — только
SHA-256, срок 10 минут, привязка к отпечатку токена администратора (проверяется при возврате). Возврат
(GET /api/integrations/olx/callback — единственный путь, открытый через публичный адрес ngrok) проверяет state, срок и
ошибку провайдера, сервером обменивает код на токены, проверяет свой аккаунт (users/me, «Version: 2.0») и показывает
простую страницу без токенов и скриптов. Токены хранятся только в БД (без шифрования — см. документацию), браузеру не
отдаются; обновляются заранее (меньше часа до истечения) плановой проверкой раз в сутки, новый refresh token
сохраняется атомарно (UPDATE … WHERE rev = :rev).

Подключение (поколение) — момент выдачи разрешения obtained_at: меняется только при новом подключении (save_token), не
при обновлении токена. Сведения об аккаунте (подпись, facts.account) пишутся только условным UPDATE … WHERE obtained_at =
поколение того токена, которым проверяли: переподключение или отключение во время запроса к OLX не получает сведений о
прежнем аккаунте. Строка токена блокируется первой, строка подключения — после (один порядок везде).

Граница возможностей: Partner API даёт доступ только к ресурсам авторизованного аккаунта. Успешный OAuth — не сбор
чужих объявлений, поэтому статус «подключено · ограничено», а не «работает»; сбора публикаций у подключения нет.
Каждое значение кода, state и токенов регистрируется для маскирования (logs.register_secret) и в журналы не попадает.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..db import SessionLocal, get_db
from ..logs import redact, register_secret
from ..settings import Settings, get_settings
from . import http
from .base import (AccessDenied, AuthExpired, CheckResult, Connector, ConnectorError, NotConfigured, RateLimited,
                   SourceError, local_time)

if TYPE_CHECKING:
    from .context import RunContext

log = logging.getLogger("saq.integrations.olx")

PROVIDER = "olx"
ORIGIN = "https://www.olx.kz"
AUTHORIZE = "https://www.olx.kz/oauth/authorize/"
TOKEN = "https://www.olx.kz/api/open/oauth/token"
API = "https://www.olx.kz/api/partner/"
API_VERSION = "2.0"
STATE_TTL = timedelta(minutes=10)
REFRESH_TTL = timedelta(days=30)  # срок refresh token OLX
REFRESH_BEFORE = timedelta(hours=1)  # обновлять токен доступа, когда до истечения меньше часа
HEALTH_MIN_GAP = timedelta(minutes=15)  # проверка «токен скоро истечёт» — не чаще (неудачная не зацикливается)
STATE_MAX = 512
CODE_MAX = 2048
TEXT_MAX = 300
TIMEOUT = 20.0

NOT_CONFIGURED = "OLX не настроен: заявка отправлена, ключей OLX_CLIENT_ID/OLX_CLIENT_SECRET нет на сервере"
BAD_STATE = ("Ссылка авторизации OLX недействительна или устарела — начните подключение заново на странице "
             "«Источники»")
DENIED = "OLX отказал в доступе: {description}"
NO_CODE = "OLX не передал код авторизации — начните подключение заново на странице «Источники»"
BAD_CODE = "Код авторизации OLX недействителен или устарел — начните подключение заново на странице «Источники»"
EXPIRED = "Доступ OLX истёк — подключите аккаунт заново"
NOT_CONNECTED = "Аккаунт OLX не подключён — нажмите «Подключить аккаунт OLX» на странице «Источники»"
UNCHECKED = "Ключи OLX на сервере есть, аккаунт ещё не подключён через OAuth"
BAD_CLIENT = "OLX не принял ключи приложения (OLX_CLIENT_ID/OLX_CLIENT_SECRET)"
READY = "Аккаунт OLX: {label}. Доступны только ресурсы этого аккаунта — чужие объявления так не собираются"
CONNECTED_AUDIT = ("OLX: подключён аккаунт {label} (OAuth). Доступны только ресурсы этого аккаунта — чужие объявления "
                   "так не собираются")
DISCONNECTED_AUDIT = "OLX: аккаунт {label} отключён — токены удалены с сервера"
SUCCESS = "OLX подключён: {label}. Можно закрыть вкладку и вернуться на страницу «Источники»"
TOKEN_FAILED = "OLX не выдал токен: {detail}. Начните подключение заново на странице «Источники»"
ACCOUNT_FAILED = ("Токен OLX получен, но аккаунт не подтверждён: {detail}. На странице «Источники» нажмите «Проверить "
                  "подключение»")
SCOPE = ("У токена OLX недостаточно прав{detail} — проверьте OLX_SCOPES (нужны read и v2) и подключите аккаунт "
         "заново")
REPLACED_NOTE = "Аккаунт OLX переподключён или отключён во время проверки — проверяется текущее подключение"
REPLACED = "Аккаунт OLX переподключён или отключён во время проверки — повторите проверку"
REPLACED_PAGE = ("Подключение OLX заменено или отключено, пока проверялся аккаунт, — результат не сохранён. Проверьте "
                 "состояние на странице «Источники»")
RATE_PAUSE = "Ограничение частоты: повтор после {time}"  # тот же текст, что у jobs._finish
ACCOUNT_FACTS = ("account", "isBusiness")
PAGE_TITLE = "SAQ Qurylys — подключение OLX"
PAGE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'", "X-Content-Type-Options": "nosniff"}
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


# ---------- ошибки и разбор ответов ----------

class TokenRejected(AuthExpired):
    """Partner API ответил 401 на токен доступа: проверка обновляет токен один раз и повторяет запрос."""


def _clip(value: Any, limit: int = TEXT_MAX) -> str:
    """Текст от провайдера для сообщения: без управляющих символов, замаскирован, не длиннее limit."""
    text = _CTRL.sub(" ", str(value or "")).strip()
    return redact(text, limit=limit) or ""


def parse_json(resp: httpx.Response) -> dict:
    """Тело ответа OLX как JSON-объект. OLX отдаёт JSON с Content-Type text/html — заголовок не учитывается."""
    try:
        data = json.loads(resp.content.decode("utf-8-sig")) if resp.content else None
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        raise SourceError(f"OLX вернул ответ не в формате JSON (HTTP {resp.status_code})",
                          retryable=resp.status_code >= 500)
    return data


def _error(resp: httpx.Response) -> tuple[str, str]:
    """(код ошибки, описание) из ответа OLX: {"error": "...", "error_description": "..."} или
    {"error": {"title": "...", "detail": "..."}}; тело не JSON — пусто."""
    try:
        data = parse_json(resp)
    except SourceError:
        return "", ""
    err, desc = data.get("error"), data.get("error_description") or data.get("error_human_title") or ""
    if isinstance(err, dict):
        desc = desc or err.get("detail") or err.get("title") or ""
        err = err.get("code") or err.get("title") or ""
    return _clip(err, 64), _clip(desc)


def _transport_error(e: Exception, what: str) -> SourceError:
    if isinstance(e, httpx.TimeoutException):
        return SourceError(f"OLX не ответил вовремя ({what})", retryable=True)
    return SourceError(f"Сетевая ошибка при обращении к OLX ({what}: {type(e).__name__})", retryable=True)


def _status_error(resp: httpx.Response, what: str) -> ConnectorError:
    """Неуспешный ответ OLX → типизированная ошибка подключения (без тела запроса и токенов)."""
    code, desc = _error(resp)
    status = resp.status_code
    suffix = f": {desc or code}" if desc or code else ""
    if status == 429:
        return RateLimited(http.retry_after(resp.headers, 600.0),
                           f"OLX ограничил частоту запросов ({what}) — повтор позже")
    if status == 403:
        return AccessDenied(f"OLX не разрешает доступ ({what}, HTTP 403{suffix}) — проверьте права приложения "
                            f"(OLX_SCOPES)")
    if status >= 500:
        return SourceError(f"OLX временно недоступен ({what}, HTTP {status})", retryable=True)
    return SourceError(f"OLX отклонил запрос ({what}, HTTP {status}{suffix})", retryable=False)


# ---------- токены ----------

def _expires_in(value: Any) -> Optional[int]:
    try:
        secs = int(value)
    except (TypeError, ValueError):
        return None
    return min(max(secs, 60), 365 * 86400) if secs > 0 else None


def _token_request(client: httpx.Client, body: dict, *, what: str, invalid_grant: str) -> dict:
    """POST на адрес выдачи токенов OLX (JSON). invalid_grant → AuthExpired(invalid_grant), неверные ключи
    приложения → AccessDenied. Возвращает ответ с access_token; значения токенов регистрируются для маскирования."""
    try:
        resp = client.post(TOKEN, json=body, headers={"Accept": "application/json"})
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise _transport_error(e, what) from None
    if 200 <= resp.status_code < 300:
        data = parse_json(resp)
        for key in ("access_token", "refresh_token"):
            if isinstance(data.get(key), str):
                register_secret(data[key])
        if not isinstance(data.get("access_token"), str) or not data["access_token"].strip():
            raise SourceError(f"OLX не вернул токен доступа ({what})", retryable=False)
        return data
    code, desc = _error(resp)
    if code == "invalid_grant":
        raise AuthExpired(invalid_grant)
    if code in ("invalid_client", "unauthorized_client") or (resp.status_code == 401 and not code):
        raise AccessDenied(f"{BAD_CLIENT}{': ' + desc if desc else ''}")
    raise _status_error(resp, what)


def exchange_code(client: httpx.Client, code: str, s: Settings) -> dict:
    """Код авторизации → токены (обмен выполняет сервер, секрет приложения браузеру не передаётся)."""
    register_secret(code)
    body = {"grant_type": "authorization_code", "client_id": s.olx_client_id,
            "client_secret": s.olx_client_secret.get(), "code": code, "scope": s.olx_scopes,
            "redirect_uri": s.olx_redirect_uri}
    return _token_request(client, body, what="обмен кода на токен", invalid_grant=BAD_CODE)


def _token_values(data: dict, now: datetime, *, initial: bool) -> dict:
    """Поля integration_tokens из ответа OLX. initial — первичная выдача: refresh token заменяется всегда; при
    обновлении прежний остаётся, если OLX не прислал новый."""
    expires = _expires_in(data.get("expires_in"))
    values: dict = {"access_token": data["access_token"],
                    "token_type": (str(data.get("token_type") or "")[:16] or None),
                    "scope": (str(data.get("scope") or "")[:255] or None),
                    "expires_at": now + timedelta(seconds=expires) if expires else None,
                    "updated_at": now}
    refresh_token = data.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token.strip():
        values["refresh_token"] = refresh_token
        values["refresh_expires_at"] = now + REFRESH_TTL
    elif initial:
        values["refresh_token"] = None
        values["refresh_expires_at"] = None
    return values


def save_token(db: Session, data: dict, now: datetime) -> None:
    """Первичные токены после обмена кода: вставка или замена строки provider = 'olx' (rev + 1). Без commit."""
    T = models.IntegrationToken
    now = models.as_utc(now)
    values = _token_values(data, now, initial=True)
    if not models.insert_ignore(db, T, {"provider": PROVIDER, **values, "obtained_at": now, "rev": 0}, ("provider",)):
        db.execute(update(T).where(T.provider == PROVIDER)
                   .values(**values, obtained_at=now, account_label=None, rev=T.rev + 1)
                   .execution_options(synchronize_session=False))


def token_expired(tok: Optional[models.IntegrationToken], now: datetime) -> bool:
    """Доступ потерян без повторной авторизации: refresh token истёк, или истёк токен доступа, а refresh token нет."""
    if tok is None:
        return False
    now = models.as_utc(now)
    refresh_exp, exp = models.as_utc(tok.refresh_expires_at), models.as_utc(tok.expires_at)
    if tok.refresh_token and refresh_exp is not None and refresh_exp <= now:
        return True
    return not tok.refresh_token and exp is not None and exp <= now


def refresh(db: Session, client: httpx.Client, s: Settings, *,
            now: Optional[datetime] = None) -> models.IntegrationToken:
    """Обновить токен доступа по refresh token. Транзакция сессии завершается до сетевого запроса; запись — условным
    UPDATE … WHERE rev = :rev (одновременное обновление другим процессом не затирается). invalid_grant —
    AuthExpired, если токен за это время не обновил кто-то другой."""
    T = models.IntegrationToken
    now = models.as_utc(now) or models.utcnow()
    tok = db.get(T, PROVIDER, populate_existing=True)
    if tok is None:
        db.commit()
        raise NotConfigured(NOT_CONNECTED)
    refresh_token, rev = tok.refresh_token, tok.rev or 0
    expired = token_expired(tok, now) or not refresh_token
    db.commit()  # сеть — без открытой транзакции
    if expired:
        raise AuthExpired(EXPIRED)
    register_secret(refresh_token)
    body = {"grant_type": "refresh_token", "client_id": s.olx_client_id, "client_secret": s.olx_client_secret.get(),
            "refresh_token": refresh_token}
    try:
        data = _token_request(client, body, what="обновление токена", invalid_grant=EXPIRED)
    except AuthExpired:
        fresh = db.get(T, PROVIDER, populate_existing=True)
        db.commit()
        if fresh is not None and (fresh.rev or 0) != rev and not token_expired(fresh, now):
            return fresh  # токен уже обновил другой процесс — старый refresh token больше не действует
        raise
    res = db.execute(update(T).where(T.provider == PROVIDER, T.rev == rev)
                     .values(**_token_values(data, now, initial=False), rev=T.rev + 1)
                     .execution_options(synchronize_session=False))
    db.commit()
    if res.rowcount != 1:
        log.info("Токен OLX уже обновлён другим процессом — используется сохранённый")
    tok = db.get(T, PROVIDER, populate_existing=True)
    db.commit()
    if tok is None:
        raise NotConfigured(NOT_CONNECTED)
    return tok


def _ensure(db: Session, client: httpx.Client, s: Settings, *,
            now: Optional[datetime] = None) -> tuple[str, datetime]:
    """(действующий токен доступа, поколение подключения obtained_at): до истечения меньше часа — сначала обновить.
    Токен и поколение читаются из одной строки."""
    now = models.as_utc(now) or models.utcnow()
    tok = db.get(models.IntegrationToken, PROVIDER, populate_existing=True)
    if tok is None:
        db.commit()
        raise NotConfigured(NOT_CONNECTED)
    if token_expired(tok, now):
        db.commit()
        raise AuthExpired(EXPIRED)
    exp = models.as_utc(tok.expires_at)
    if exp is not None and exp - now < REFRESH_BEFORE:
        tok = refresh(db, client, s, now=now)
        return tok.access_token, tok.obtained_at
    token, grant = tok.access_token, tok.obtained_at
    db.commit()
    register_secret(token)
    return token, grant


def ensure_token(db: Session, client: httpx.Client, s: Settings, *, now: Optional[datetime] = None) -> str:
    """Действующий токен доступа: до истечения меньше часа — сначала обновить."""
    return _ensure(db, client, s, now=now)[0]


def _label_grant(db: Session, grant: Optional[datetime], label: Optional[str], now: datetime) -> bool:
    """Подпись аккаунта — в строку токена, только если подключение то же (obtained_at = grant: не переподключали и не
    отключали). UPDATE блокирует строку токена до конца транзакции — отключение и новое подключение ждут её. False —
    подключение заменено или удалено: сведения о прежнем аккаунте никуда не пишутся. Без commit."""
    if grant is None:
        return False
    T = models.IntegrationToken
    res = db.execute(update(T).where(T.provider == PROVIDER, T.obtained_at == models.as_utc(grant))
                     .values(account_label=label, updated_at=models.as_utc(now))
                     .execution_options(synchronize_session=False))
    return res.rowcount == 1


def _without_account(facts: Optional[dict]) -> dict:
    return {k: v for k, v in (facts or {}).items() if k not in ACCOUNT_FACTS}


def users_me(client: httpx.Client, token: str) -> dict:
    """Свой аккаунт: GET users/me (Authorization: Bearer, Version: 2.0, без Content-Type) → data. 401 — токен не
    принят (TokenRejected), кроме insufficient_scope: по документации OLX это тоже 401, но обновление токена и повторное
    подключение с теми же правами не помогут — «нет доступа» с подсказкой про OLX_SCOPES."""
    register_secret(token)
    try:
        resp = client.get(API + "users/me", headers={"Authorization": f"Bearer {token}", "Version": API_VERSION,
                                                     "Accept": "application/json"})
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise _transport_error(e, "проверка аккаунта") from None
    if resp.status_code == 401:
        code, desc = _error(resp)
        if code == "insufficient_scope":
            raise AccessDenied(SCOPE.format(detail=f": {desc}" if desc else ""))
        raise TokenRejected(EXPIRED)
    if not 200 <= resp.status_code < 300:
        raise _status_error(resp, "проверка аккаунта")
    data = parse_json(resp).get("data")
    if not isinstance(data, dict) or data.get("id") in (None, ""):
        raise SourceError("OLX вернул неожиданный ответ users/me — нет данных аккаунта", retryable=False)
    return data


def account_label(me: dict) -> str:
    """«{имя} (id {id})» — без e-mail и телефона."""
    name = _clip(me.get("name"), 120) or "без имени"
    return f"{name} (id {_clip(me.get('id'), 32)})"


def _key_fps(s: Settings) -> dict:
    """Отпечатки ключей, с которыми подключён аккаунт: их смена на сервере видна как «не проверен»."""
    return {name: s.key_fp(name) for name in CONNECTOR.required_env if s.key_fp(name)}


# ---------- state OAuth ----------

def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def start(db: Session, admin: auth.Admin, author: str) -> tuple[str, datetime]:
    """Начать подключение: одноразовый state (в БД — только SHA-256 и отпечаток токена администратора) и адрес
    авторизации OLX. Просроченные state удаляются. Без commit. Нет ключей — NotConfigured."""
    s = get_settings()
    missing = CONNECTOR.missing(s)
    if missing:
        if {"OLX_CLIENT_ID", "OLX_CLIENT_SECRET"} & set(missing):
            raise NotConfigured(NOT_CONFIGURED)
        raise NotConfigured(f"OLX не настроен: нет на сервере {', '.join(missing)}")
    S = models.OAuthState
    now = models.utcnow()
    db.execute(delete(S).where(S.expires_at < now).execution_options(synchronize_session=False))
    state = secrets.token_urlsafe(32)  # 256 бит
    register_secret(state)
    expires = now + STATE_TTL
    db.add(S(state_hash=_hash(state), provider=PROVIDER, created_by=author, admin_fp=admin.fp, created_at=now,
             expires_at=expires))
    db.flush()
    query = urlencode({"client_id": s.olx_client_id, "response_type": "code", "state": state,
                       "scope": s.olx_scopes, "redirect_uri": s.olx_redirect_uri})
    return f"{AUTHORIZE}?{query}", expires


def consume_state(db: Session, state: str, current_admin_fp: Optional[str], *,
                  now: Optional[datetime] = None) -> models.OAuthState:
    """Погасить state: условный UPDATE used_at (ровно одна строка: свой провайдер, не использован, не истёк), затем
    сверить отпечаток текущего токена администратора. Любая неудача — ValueError с одним текстом (state, погашенный
    до смены токена администратора, остаётся погашенным — вызывающий фиксирует транзакцию). Без commit."""
    if not state or len(state) > STATE_MAX:
        raise ValueError(BAD_STATE)
    S = models.OAuthState
    now = models.as_utc(now) or models.utcnow()
    h = _hash(state)
    res = db.execute(update(S).where(S.state_hash == h, S.provider == PROVIDER, S.used_at.is_(None),
                                     S.expires_at > now)
                     .values(used_at=now).execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise ValueError(BAD_STATE)
    # маскируется только настоящий state: произвольные значения с публичного адреса в список секретов не попадают
    register_secret(state)
    row = db.get(S, h, populate_existing=True)
    if row is None or not current_admin_fp or not hmac.compare_digest(row.admin_fp or "", current_admin_fp):
        raise ValueError(BAD_STATE)
    return row


# ---------- подключение ----------

class OlxConnector(Connector):
    code = "olx"
    kind = "oauth"
    platform = "olx"
    group = "limited"
    name = "OLX.kz"
    via = "Partner API · OAuth"
    required_env = ("OLX_CLIENT_ID", "OLX_CLIENT_SECRET", "OLX_REDIRECT_URI")
    capabilities = {"test": True, "collect": False, "toggle": False, "configure": False, "connect": True}
    health_check_min = 1440
    secret_refs = ("env:OLX_CLIENT_SECRET", "db:integration_tokens")
    labels = {"not_configured": "заявка отправлена", "unchecked": "не подключён", "ready": "подключено · ограничено"}
    limitation = ("Partner API даёт доступ только к ресурсам своего авторизованного аккаунта — чужие объявления так не "
                  "собрать.")

    def computed_status(self, db, row, now) -> Optional[tuple[str, str]]:
        """Ключи есть, токена нет — «не подключён»; токен не обновить — «истёк доступ»; иначе — хранимый статус."""
        if db is None:
            return None
        tok = db.get(models.IntegrationToken, PROVIDER)
        if tok is None:
            return "unchecked", UNCHECKED
        if token_expired(tok, now or models.utcnow()):
            return "expired", EXPIRED
        return None

    def health_due(self, db, row, now) -> bool:
        """Плановая проверка: аккаунт подключён и (последней проверке больше суток или токен истекает меньше чем через
        час — но не чаще раза в 15 минут). Лимит частоты OLX: до next_run_at не проверять, после — проверить снова (не
        чаще раза в 15 минут). Истёкший доступ и отказ не проверяются по расписанию."""
        if db is None or self.missing(get_settings(), row):
            return False
        now = models.as_utc(now) or models.utcnow()
        tok = db.get(models.IntegrationToken, PROVIDER)
        if tok is None or token_expired(tok, now):
            return False
        if row is not None and row.status in ("expired", "denied", "access_check"):
            return False
        checked = models.as_utc(row.checked_at) if row is not None else None
        if row is not None and row.status == "rate_limited":
            pause = models.as_utc(row.next_run_at)
            if pause is not None and pause > now:
                return False
            return checked is None or checked <= now - HEALTH_MIN_GAP
        if checked is None or checked <= now - timedelta(minutes=self.health_check_min):
            return True
        exp = models.as_utc(tok.expires_at)
        return exp is not None and exp - now < REFRESH_BEFORE and checked <= now - HEALTH_MIN_GAP

    def check(self, ctx: "RunContext") -> CheckResult:
        """Токен (обновить, если скоро истечёт) → users/me; 401 — обновить токен один раз и повторить; снова 401 —
        «Доступ OLX истёк»; 401 insufficient_scope — «нет доступа» без обновления. Подпись аккаунта пишется только в то
        подключение, чей токен проверялся: переподключили или отключили во время запроса — проверяется текущее
        подключение (один раз; отключено — «не подключён»), снова сменилось — ошибка без сведений об аккаунте.
        Сбора публикаций нет: Partner API не даёт чужих объявлений."""
        s = ctx.settings
        if self.missing(s):
            raise NotConfigured(self.missing_detail(s))
        with ctx.client(base_url=ORIGIN, timeout=TIMEOUT) as client:
            for attempt in range(2):
                me, grant = _verify(ctx, client, s)
                label = account_label(me)
                with SessionLocal() as db:
                    same = _label_grant(db, grant, label, ctx.now())
                    db.commit()
                if same:
                    return CheckResult(True, READY.format(label=label),
                                       {"account": label, "isBusiness": bool(me.get("is_business"))})
                if attempt == 0:
                    ctx.note(REPLACED_NOTE, "warn")
        raise SourceError(REPLACED, retryable=False)


def _verify(ctx: "RunContext", client: httpx.Client, s: Settings) -> tuple[dict, datetime]:
    """users/me действующим токеном; 401 — обновить токен один раз и повторить, снова 401 — AuthExpired.
    → (данные аккаунта, поколение подключения того токена, который OLX принял)."""
    with SessionLocal() as db:
        token, grant = _ensure(db, client, s, now=ctx.now())
    try:
        return users_me(client, token), grant
    except TokenRejected:
        ctx.note("OLX не принял токен доступа — токен обновляется", "warn")
    with SessionLocal() as db:
        tok = refresh(db, client, s, now=ctx.now())
    try:
        return users_me(client, tok.access_token), tok.obtained_at
    except TokenRejected:
        raise AuthExpired(EXPIRED) from None


CONNECTOR = OlxConnector()


# ---------- маршруты ----------

class OlxStartOut(schemas.Camel):
    authorize_url: str
    expires_at: datetime


router = APIRouter(tags=["integrations"])


def _page(text: str, status: int) -> HTMLResponse:
    """Страница возврата OAuth: только текст (экранирован), без скриптов и стилей, без токенов; не кэшируется."""
    body = ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{escape(PAGE_TITLE)}</title></head><body><h1>{escape(PAGE_TITLE)}</h1>"
            f"<p>{escape(text)}</p></body></html>")
    return HTMLResponse(body, status_code=status, headers=dict(PAGE_HEADERS))


def _fail_status(e: ConnectorError) -> str:
    if isinstance(e, AuthExpired):
        return "expired"
    if isinstance(e, AccessDenied):
        return "denied"
    if isinstance(e, RateLimited):
        return "rate_limited"
    return "error"


def _account_failed(e: ConnectorError, now: datetime) -> None:
    """Токен сохранён (поколение now), но users/me не прошёл: причина — в статус подключения (сбой не выглядит
    успехом), сведения о прежнем аккаунте убираются. Лимит частоты — пауза до Retry-After, как у jobs._finish.
    Подключение за это время заменено или отключено — его статус не трогается."""
    from . import service

    with SessionLocal() as db:
        if not _label_grant(db, now, None, now):
            db.rollback()
            return
        r = service.row(db, CONNECTOR.code, create=True)
        r.facts = _without_account(r.facts)
        r.last_error, r.last_error_at = redact(e.detail), now
        detail = e.detail
        if isinstance(e, RateLimited):
            r.checked_at = now
            r.next_run_at = models.utcnow() + timedelta(seconds=float(e.retry_after or 3600))
            detail = RATE_PAUSE.format(time=local_time(r.next_run_at))
        service.transition(db, CONNECTOR.code, _fail_status(e), detail, at=now)
        db.commit()


def _connected(label: str, me: dict, author: str, now: datetime, s: Settings) -> bool:
    """Аккаунт подтверждён: подпись в строке токена, статус «подключено · ограничено», событие журнала от автора.
    now — поколение, записанное save_token; подключение за это время заменено или отключено — False, ничего не
    пишется."""
    from ..ads_service import _event
    from . import service

    with SessionLocal() as db:
        if not _label_grant(db, now, label, now):
            db.rollback()
            return False
        r = service.row(db, CONNECTOR.code, create=True)
        facts = {k: v for k, v in (r.facts or {}).items() if k not in ("stopped", "stoppedCode")}
        r.facts = {**facts, "account": label, "isBusiness": bool(me.get("is_business")), "keyFp": _key_fps(s)}
        r.checked_at, r.failures = now, 0
        service.transition(db, CONNECTOR.code, "ready", READY.format(label=label), at=now, audit=False)
        _event(db, "data", author, CONNECTED_AUDIT.format(label=label), at=now)
        db.commit()
    return True


@router.post("/api/integrations/olx/start", response_model=OlxStartOut, response_model_by_alias=True,
             responses={409: {"description": "Ключей OLX нет на сервере"}})
def olx_start(body: schemas.AdminActionIn, admin: auth.Admin = Depends(auth.require_admin),
              db: Session = Depends(get_db)):
    """«Подключить аккаунт OLX» (администратор): адрес авторизации OLX с одноразовым state на 10 минут."""
    try:
        url, expires = start(db, admin, body.author)
    except NotConfigured as e:
        db.rollback()
        raise HTTPException(409, e.detail) from None
    db.commit()
    return {"authorize_url": url, "expires_at": expires}


@router.get("/api/integrations/olx/callback", response_class=HTMLResponse,
            responses={400: {"description": "state недействителен или OLX отказал"},
                       502: {"description": "OLX не выдал токен или не подтвердил аккаунт"}})
def olx_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None,
                 error_description: Optional[str] = None):
    """Возврат из OLX (публичный адрес): state → ошибка провайдера → обмен кода сервером → users/me → статус и журнал.
    Ответ — простая страница без токенов. Код и state регистрируются для маскирования только после проверки state
    (адрес публичный: чужие значения не должны расти списком секретов); в журнале запросов параметры code и state
    маскируются по имени (logs.redact)."""
    s = get_settings()
    now = models.utcnow()
    admin_secret = s.admin_token.get()
    current_fp = auth.token_fp(admin_secret) if admin_secret else None
    with SessionLocal() as db:
        try:
            st = consume_state(db, state or "", current_fp, now=now)
        except ValueError:
            db.commit()  # погашенный state (смена токена администратора) остаётся погашенным
            return _page(BAD_STATE, 400)
        author = st.created_by
        db.commit()
    if error:
        log.info("OLX: в доступе отказано (%s)", _clip(error, 64))
        return _page(DENIED.format(description=_clip(error_description) or _clip(error, 64)), 400)
    if not code or len(code) > CODE_MAX:
        return _page(NO_CODE, 400)
    if CONNECTOR.missing(s):
        return _page(NOT_CONFIGURED, 409)
    try:
        with http.client(base_url=ORIGIN, timeout=TIMEOUT) as client:
            data = exchange_code(client, code, s)
            with SessionLocal() as db:
                save_token(db, data, now)
                db.commit()
            try:
                me = users_me(client, data["access_token"])
            except ConnectorError as e:
                log.warning("OLX: токен получен, проверка аккаунта не прошла: %s", e.detail)
                _account_failed(e, now)
                return _page(ACCOUNT_FAILED.format(detail=e.detail), 502)
    except AuthExpired:
        return _page(BAD_CODE, 400)
    except AccessDenied as e:
        log.warning("OLX: обмен кода отклонён: %s", e.detail)
        return _page(e.detail, 400)
    except ConnectorError as e:
        log.warning("OLX: обмен кода не выполнен: %s", e.detail)
        return _page(TOKEN_FAILED.format(detail=e.detail), 502)
    except (http.DownloadError, httpx.HTTPError) as e:
        log.warning("OLX: обмен кода не выполнен (%s)", type(e).__name__)
        return _page(TOKEN_FAILED.format(detail=f"ошибка связи ({type(e).__name__})"), 502)
    label = account_label(me)
    if not _connected(label, me, author, now, s):
        log.warning("OLX: подключение заменено или отключено во время проверки аккаунта — результат не сохранён")
        return _page(REPLACED_PAGE, 409)
    log.info("OLX: подключён аккаунт %s", label)
    return _page(SUCCESS.format(label=label), 200)


@router.post("/api/integrations/olx/disconnect", response_model=schemas.ConnectorOut, response_model_by_alias=True)
def olx_disconnect(body: schemas.AdminActionIn, admin: auth.Admin = Depends(auth.require_admin),
                   db: Session = Depends(get_db)):
    """Отключить аккаунт OLX (администратор): токены удаляются из БД, статус «не подключён», запись в журнал. Сведения
    об аккаунте убираются всегда, даже если токена уже нет (повтор чинит остатки)."""
    from ..ads_service import _event
    from . import service

    now = models.utcnow()
    tok = db.get(models.IntegrationToken, PROVIDER)
    label = None
    if tok is not None:
        label = tok.account_label or "без подписи"
        db.delete(tok)
        db.flush()  # строка токена — первой: тот же порядок блокировок, что у возврата из OLX и проверки
    r = service.row(db, CONNECTOR.code, create=tok is not None)
    if r is not None:
        r.facts = _without_account(r.facts)
    if tok is not None:
        service.transition(db, CONNECTOR.code, "unchecked", None, at=now, audit=False)
        _event(db, "data", body.author, DISCONNECTED_AUDIT.format(label=label), at=now)
    db.commit()
    return service.view(db, [CONNECTOR.code])[0]
