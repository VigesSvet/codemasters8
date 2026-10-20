"""Вход в Telegram для сборщика каналов: python -m app.integrations.telegram_login(integration-spec §12.2,
план §7.1.2).

Режимы(один за запуск):
  без аргументов        - вход в одном процессе: код из Telegram через input(), пароль 2FA через getpass;
  --send-code [--force] - запросить код; ожидание кода - в "<TG_SESSION_PATH>.pending.json"(0600, 15 минут);
  --code КОД            - войти по коду("-" - прочитать код из stdin);
  --password            - пароль двухфакторной защиты(запрос через getpass; работает в docker compose run --rm);
  --password-stdin      - пароль из stdin(для автоматизации);
  --status              - есть ли сессия: "Сессии нет" / "Сессия есть: @имя" / "Сессия недействительна";
  --logout              - выйти из аккаунта и удалить файл сессии.

Читает TG_API_ID, TG_API_HASH, TG_PHONE, TG_SESSION_PATH из настроек сервера. Номер, phone_code_hash и сессия не
печатаются и регистрируются для маскирования в логах. Каждый запуск берёт блокировку сессии - воркер на время входа
нужно остановить(docker compose stop worker).

Коды выхода: 0 - готово; 1 - ошибка(нет кода, нет связи, нет сессии для --status); 2 - не заданы ключи;
3 - нужен пароль 2FA; 4 - неверный код или пароль; 5 - код устарел; 6 - Telegram просит подождать; 7 - сессия занята.
"""
import argparse
import asyncio
import getpass
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from .. import logs, models
from ..settings import Settings, get_settings
from . import telegram as tg
from .base import NotConfigured

PENDING_TTL_S = 15 * 60
OK, ERROR, NO_KEYS, NEED_PASSWORD, INVALID, EXPIRED, FLOOD, BUSY = 0, 1, 2, 3, 4, 5, 6, 7
CMD = "python -m app.integrations.telegram_login"
MSG_KEYS = "Задайте TG_API_ID, TG_API_HASH и TG_PHONE в .env на сервере"
MSG_BUSY = "Сессия используется воркером — остановите его (docker compose stop worker) и повторите"
MSG_NO_PENDING = "Сначала запросите код: --send-code"
MSG_EXPIRED = "Код устарел — запросите новый: --send-code"
MSG_NEED_PASSWORD = f"Нужен пароль двухфакторной защиты: {CMD} --password"
MSG_BAD_CODE = "Неверный код — повторите --code"
MSG_BAD_PASSWORD = "Неверный пароль — повторите --password"
CODE_TYPES = {"SentCodeTypeApp": "в приложение Telegram", "SentCodeTypeSms": "SMS", "SentCodeTypeCall": "звонком",
              "SentCodeTypeFlashCall": "звонком-сбросом", "SentCodeTypeMissedCall": "пропущенным звонком",
              "SentCodeTypeFragmentSms": "SMS через Fragment", "SentCodeTypeFirebaseSms": "SMS",
              "SentCodeTypeEmailCode": "на почту", "SentCodeTypeSmsWord": "SMS", "SentCodeTypeSmsPhrase": "SMS"}


class Exit(Exception):
    """Завершение с кодом и сообщением(stdout при code == 0, иначе stderr)."""

    def __init__(self, code: int, message: str = ""):
        super().__init__(message)
        self.code, self.message = code, message


def _say(text: str, *, err: bool = False) -> None:
    print(logs.redact(text, limit=None), file=sys.stderr if err else sys.stdout)


def _fp(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:12]


def pending_path(s: Settings) -> Path:
    return Path(f"{s.tg_session_path}.pending.json")


# ---------- файл ожидания кода ----------

def _pending_age(data: dict) -> float:
    """Сколько секунд назад запрошен код; нечитаемое время - "давно"(ожидание устарело)."""
    try:
        sent = models.as_utc(datetime.fromisoformat(str(data.get("sentAt"))))
    except (TypeError, ValueError):
        return float("inf")
    return (models.utcnow() - sent).total_seconds()


def _load_pending(s: Settings) -> dict | None:
    """Ожидание кода или None(нет файла). Нечитаемый файл удаляется."""
    path = pending_path(s)
    try:
        text = tg.read_private(path)
    except NotConfigured as e:
        raise Exit(ERROR, f"{e.detail} ({path.name})") from None
    if not text:
        return None
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("v") != 1:
            raise ValueError("формат")
    except ValueError:
        path.unlink(missing_ok=True)
        return None
    for key in ("session", "phoneCodeHash"):
        logs.register_secret(data.get(key))
    return data


def _save_pending(s: Settings, data: dict) -> None:
    tg.write_private(pending_path(s), json.dumps(data, ensure_ascii=False))


def _drop_pending(s: Settings) -> None:
    pending_path(s).unlink(missing_ok=True)


def _drop_stale(s: Settings) -> bool:
    """Удалить ожидание кода старше 15 минут. True - такое было(код устарел)."""
    path = pending_path(s)
    if not path.exists():
        return False
    try:
        data = _load_pending(s)
    except Exit:
        return False
    if data is not None and _pending_age(data) <= PENDING_TTL_S:
        return False
    path.unlink(missing_ok=True)
    return data is not None


# ---------- шаги входа ----------

def _phone(s: Settings) -> str:
    phone = s.tg_phone.get()
    logs.register_secret(phone)
    return phone


def _keys(s: Settings, *, phone: bool = True) -> None:
    if s.tg_api_id is None or not s.tg_api_hash or (phone and not s.tg_phone):
        raise Exit(NO_KEYS, MSG_KEYS)
    logs.register_secret(s.tg_api_hash.get())


async def _connect(session: str, s: Settings):
    client = tg.new_client(session, s)
    try:
        await client.connect()
    except BaseException:
        await _close(client)
        raise
    return client


async def _close(client) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass


def _save_session(client) -> str:
    value = client.session.save()
    logs.register_secret(value)
    return value


async def _who(client) -> str | None:
    """"@username" авторизованной сессии или None."""
    if not await client.is_user_authorized():
        return None
    return tg._me_label(await client.get_me())


async def _existing(s: Settings) -> str | None:
    """Авторизованная сессия в файле: "@username" или None(нет файла или сессия недействительна)."""
    session = tg.read_session(s.tg_session_path)
    if not session:
        return None
    client = await _connect(session, s)
    try:
        return await _who(client)
    finally:
        await _close(client)


async def _send_code(s: Settings, force: bool) -> tuple:
    """Запросить код. Возвращает(клиент, отправленный код) - клиент остаётся подключённым."""
    if not force:
        who = await _existing(s)
        if who:
            raise Exit(ERROR, f"Сессия уже есть: {who}. Чтобы войти заново — --force")
    phone = _phone(s)
    client = await _connect("", s)
    try:
        sent = await client.send_code_request(phone)
    except BaseException:
        await _close(client)
        raise
    logs.register_secret(getattr(sent, "phone_code_hash", None))
    return client, sent


def _code_type(sent) -> str:
    name = type(getattr(sent, "type", None)).__name__
    return CODE_TYPES.get(name, name)


async def _finish(client, s: Settings, user=None) -> str:
    """Вход выполнен: сессия - в файл(0600), ожидание кода удаляется."""
    me = user if user is not None and getattr(user, "id", None) is not None else await client.get_me()
    tg.write_session(s.tg_session_path, _save_session(client))
    _drop_pending(s)
    return f"Вход выполнен: {tg._me_label(me)} (id {getattr(me, 'id', '?')}). Сессия сохранена в {s.tg_session_path}"


def _pending_or_exit(s: Settings, stale: bool) -> dict:
    data = _load_pending(s)
    if data is None:
        raise Exit(EXPIRED if stale else ERROR, MSG_EXPIRED if stale else MSG_NO_PENDING)
    if data.get("phoneFp") != _fp(_phone(s)):  # номер в настройках сменился - код не к нему
        _drop_pending(s)
        raise Exit(EXPIRED, MSG_EXPIRED)
    return data


async def _with_code(s: Settings, code: str, stale: bool) -> str:
    from telethon import errors

    data = _pending_or_exit(s, stale)
    if data.get("needsPassword"):
        raise Exit(NEED_PASSWORD, MSG_NEED_PASSWORD)
    client = await _connect(data["session"], s)
    try:
        try:
            user = await client.sign_in(phone=_phone(s), code=code, phone_code_hash=data["phoneCodeHash"])
        except errors.SessionPasswordNeededError:
            _save_pending(s, {**data, "session": _save_session(client), "needsPassword": True})
            raise Exit(NEED_PASSWORD, MSG_NEED_PASSWORD) from None
        except errors.PhoneCodeInvalidError:
            raise Exit(INVALID, MSG_BAD_CODE) from None
        except errors.PhoneCodeExpiredError:
            _drop_pending(s)
            raise Exit(EXPIRED, MSG_EXPIRED) from None
        return await _finish(client, s, user)
    finally:
        await _close(client)


async def _with_password(s: Settings, password: str, stale: bool) -> str:
    from telethon import errors

    data = _pending_or_exit(s, stale)
    if not data.get("needsPassword"):
        raise Exit(ERROR, f"Пароль сейчас не нужен — сначала войдите по коду: {CMD} --code <код>")
    client = await _connect(data["session"], s)
    try:
        try:
            user = await client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            raise Exit(INVALID, MSG_BAD_PASSWORD) from None
        return await _finish(client, s, user)
    finally:
        await _close(client)


async def _interactive(s: Settings, force: bool) -> str:
    """Вход в одном процессе: код через input(), пароль 2FA через getpass(до трёх попыток каждого)."""
    from telethon import errors

    client, sent = await _send_code(s, force)
    try:
        _say(f"Код отправлен ({_code_type(sent)}).")
        user = None
        for attempt in range(3):
            code = input("Код из Telegram: ").strip()
            try:
                user = await client.sign_in(phone=_phone(s), code=code, phone_code_hash=sent.phone_code_hash)
                break
            except errors.PhoneCodeInvalidError:
                if attempt == 2:
                    raise Exit(INVALID, "Неверный код — запустите вход заново") from None
                _say("Неверный код — попробуйте ещё раз", err=True)
            except errors.PhoneCodeExpiredError:
                raise Exit(EXPIRED, "Код устарел — запустите вход заново") from None
            except errors.SessionPasswordNeededError:
                for p_attempt in range(3):
                    password = getpass.getpass("Пароль двухфакторной защиты Telegram: ")
                    try:
                        user = await client.sign_in(password=password)
                        break
                    except errors.PasswordHashInvalidError:
                        if p_attempt == 2:
                            raise Exit(INVALID, "Неверный пароль — запустите вход заново") from None
                        _say("Неверный пароль — попробуйте ещё раз", err=True)
                break
        return await _finish(client, s, user)
    finally:
        await _close(client)


async def _status(s: Settings) -> tuple[int, str]:
    if not tg.read_session(s.tg_session_path):
        return ERROR, "Сессии нет"
    _keys(s, phone=False)
    who = await _existing(s)
    return (OK, f"Сессия есть: {who}") if who else (ERROR, "Сессия недействительна")


async def _logout(s: Settings) -> str:
    session = tg.read_session(s.tg_session_path)
    if not session:
        return "Сессии нет"
    client = await _connect(session, s)
    try:
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        await _close(client)
    Path(s.tg_session_path).unlink(missing_ok=True)
    _drop_pending(s)
    return "Выход выполнен, файл сессии удалён"


# ---------- командная строка ----------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=CMD,
                                description="Вход в Telegram для сборщика каналов (сессия — в TG_SESSION_PATH)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--send-code", action="store_true", help="запросить код входа")
    mode.add_argument("--code", metavar="КОД", help="войти по коду («-» — прочитать из stdin)")
    mode.add_argument("--password", action="store_true", help="ввести пароль двухфакторной защиты")
    mode.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--status", action="store_true", help="проверить сессию")
    mode.add_argument("--logout", action="store_true", help="выйти и удалить файл сессии")
    p.add_argument("--force", action="store_true", help="войти заново, даже если сессия уже есть")
    return p


async def _dispatch(args, s: Settings, stale: bool) -> tuple[int, str]:
    if args.status:
        return await _status(s)
    if args.logout:
        _keys(s, phone=False)
        return OK, await _logout(s)
    _keys(s)
    if args.send_code:
        client, sent = await _send_code(s, args.force)
        try:
            _save_pending(s, {"v": 1, "session": _save_session(client), "phoneCodeHash": sent.phone_code_hash,
                              "phoneFp": _fp(_phone(s)), "sentAt": models.iso(models.utcnow()),
                              "codeType": type(getattr(sent, "type", None)).__name__, "needsPassword": False})
        finally:
            await _close(client)
        return OK, f"Код отправлен ({_code_type(sent)}). Введите: {CMD} --code <код>"
    if args.code is not None:
        code = sys.stdin.readline().strip() if args.code == "-" else args.code.strip()
        if not code:
            raise Exit(ERROR, "Код пустой — повторите --code")
        return OK, await _with_code(s, code, stale)
    if args.password or args.password_stdin:
        if args.password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass.getpass("Пароль двухфакторной защиты Telegram: ")
        if not password:
            raise Exit(ERROR, "Пароль пустой — повторите --password")
        return OK, await _with_password(s, password, stale)
    return OK, await _interactive(s, args.force)


def _telegram_error(e: BaseException) -> Exit | None:
    from telethon import errors

    if isinstance(e, errors.FloodError):
        secs = int(getattr(e, "seconds", 0) or 0)
        return Exit(FLOOD, f"Telegram просит подождать {secs} с")
    if isinstance(e, errors.PhoneNumberInvalidError):
        return Exit(ERROR, "Telegram не принял номер TG_PHONE — проверьте его в .env на сервере")
    if isinstance(e, (errors.PhoneNumberUnoccupiedError, errors.PhoneNumberBannedError)):
        return Exit(ERROR, "Номер TG_PHONE не зарегистрирован в Telegram или заблокирован")
    if isinstance(e, (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError)):
        return Exit(ERROR, "Telegram не принял TG_API_ID / TG_API_HASH — проверьте их на my.telegram.org")
    if isinstance(e, (errors.ServerError, errors.TimedOutError)) \
            or (isinstance(e, ValueError) and str(e).startswith(tg._UNSUCCESSFUL)):
        return Exit(ERROR, f"Telegram временно не отвечает ({type(e).__name__}) — повторите позже")
    if isinstance(e, errors.RPCError):
        return Exit(ERROR, f"Telegram отклонил запрос ({type(e).__name__})")
    if isinstance(e, (ConnectionError, OSError)):
        return Exit(ERROR, f"Нет связи с Telegram ({type(e).__name__})")
    return None


def main(argv: list | None = None) -> int:
    args = _parser().parse_args(argv)
    logs.install()
    s = get_settings()
    path = Path(s.tg_session_path)
    if (args.status or args.logout) and not path.parent.exists():
        _say("Сессии нет")  # каталога сессии нет - нет ни сессии, ни ожидания кода; каталог не создаётся
        return ERROR if args.status else OK
    try:
        with tg.session_lock(path):
            stale = _drop_stale(s)
            code, message = asyncio.run(_dispatch(args, s, stale))
    except tg.SessionBusy:
        code, message = BUSY, MSG_BUSY
    except Exit as e:
        code, message = e.code, e.message
    except NotConfigured as e:
        code, message = ERROR, e.detail
    except PermissionError as e:
        code, message = ERROR, f"Нет доступа к каталогу сессии Telegram ({type(e).__name__})"
    except Exception as e:
        mapped = _telegram_error(e)
        if mapped is None:
            raise
        code, message = mapped.code, mapped.message
    if message:
        _say(message, err=code not in (OK,) and message not in ("Сессии нет", "Сессия недействительна"))
    return code


if __name__ == "__main__":
    sys.exit(main())
