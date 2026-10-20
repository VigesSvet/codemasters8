"""Telegram-каналы: сбор публичных каналов через MTProto(Telethon) от выделенного аккаунта(integration-spec §12.2,
план §7.1).

Сессия Telegram - секрет: текст StringSession в TG_SESSION_PATH(файл 0600, каталог 0700, владелец - этот
пользователь); значение регистрируется для маскирования в логах. Один процесс с сессией за раз: воркер, вход и проверка
берут неблокирующую блокировку "<путь>.lock". API файла сессии не видит(у него нет тома с сессией) - о её наличии
сообщает воркер(report → collectors.state.report).

Сбор: первый запуск - последние TG_HISTORY_LIMIT сообщений канала, дальше - только новые после сохранённой позиции
(lastId, сохраняется после каждой принятой публикации - рестарт не теряет позицию и не создаёт дубликатов) плюс правки
среди последних TG_EDIT_WINDOW сообщений. Сообщения альбома объединяются в одну публикацию(внешний ID -
"{id канала}:{id первого сообщения}"); альбом на границе выборки дочитывается по соседним ID. Файлы скачиваются только
те, что ещё нужны(ключ файла включает ID файла Telegram - замена файла правкой загружается заново); не скачавшиеся
повторяются в следующих запусках(до трёх попыток). FloodWait - пауза источника(rate_limited) без повторов внутри
запуска; сбой серверов Telegram - повтор задания; закрытый или неизвестный канал - "частично" с причиной(ни одного
доступного - ошибка настроек). Исходный ответ Telegram сохраняется белым списком полей(объекты Telethon не JSON).
"""
import asyncio
import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from collections.abc import Callable, Iterator

from .. import logs, models
from ..settings import Settings
from .base import (AuthExpired, BadConfig, CheckResult, ConfigError, Connector, ConnectorError, MediaIn,
                   NotConfigured, Publication, RateLimited, SourceError)

if TYPE_CHECKING:
    from ..models import Collector
    from .context import RunContext

# Фабрика клиента Telethon(session, api_id, api_hash) → клиент; тесты подменяют её заглушкой
CLIENT_FACTORY: Callable[[str, int, str], Any] | None = None
LOGIN_HINT = ("Нет авторизованной сессии Telegram: выполните вход — python -m app.integrations.telegram_login "
              "--send-code, затем --code <код> (в Docker: docker compose run --rm worker python -m "
              "app.integrations.telegram_login …)")
SESSION_INVALID = "Сессия Telegram недействительна — повторите вход"
SESSION_BUSY = "Сессия Telegram занята другим процессом"
TIME_UP = "Время запуска истекло — продолжим в следующий раз"
PRIVATE_CHANNEL = "Закрытые каналы не поддерживаются — укажите публичный канал (t.me/имя)"
NEW_MAX = 500  # новых сообщений канала за один запуск; остальные - в следующий
ALBUM_SPAN = 9  # альбом - до 10 сообщений подряд: дочитываются ID first−9 … last+9
RETRY_MAX = 100  # публикаций канала, файлы которых ждут повторной загрузки
# ValueError Telethon при поиске по имени, означающие "имени нет"(users.py: _get_entity_from_string)
_NOT_FOUND = ("No user has ", "Cannot find any entity")
# ValueError Telethon после исчерпанных повторов(без raise_last_call_error) - временный сбой Telegram
_UNSUCCESSFUL = "Request was unsuccessful"
_CHANNEL =re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_LINK = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/", re.I)


class SessionBusy(Exception):
    """Файл сессии занят другим процессом(воркер, вход, проверка)."""


class _Unavailable(Exception):
    """Канал не читается(не найден, закрыт, не канал): запуск "частично" с причиной, сбор продолжается с другими
    каналами; не читается ни один - ошибка настроек."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


class _TimeUp(Exception):
    """Мягкий срок запуска истёк или воркер останавливается."""


# ---------- клиент и файл сессии ----------

def make_client(session: str, api_id: int, api_hash: str):
    """Клиент Telethon на StringSession: без приёма обновлений; FloodWait до 30 с Telethon выжидает сам, дольше -
    FloodWaitError(пауза источника). raise_last_call_error: исчерпав повторы, Telethon бросает последнюю ошибку
    Telegram(ServerError, TimedOutError…), а не безликий ValueError - сбой Telegram не похож на "канал не найден"."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    return TelegramClient(StringSession(session or None), api_id, api_hash, receive_updates=False,
                          flood_sleep_threshold=30, request_retries=3, timeout=20, raise_last_call_error=True)


def new_client(session: str, s: Settings):
    """Клиент через CLIENT_FACTORY(тесты) или make_client. Ключи - только из настроек сервера."""
    factory = CLIENT_FACTORY or make_client
    return factory(session, int(s.tg_api_id or 0), s.tg_api_hash.get())


@contextmanager
def session_lock(path: Path) -> Iterator[None]:
    """Неблокирующая монопольная блокировка "<path>.lock"(0600): один процесс с сессией за раз;
    занято - SessionBusy."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = Path(f"{path}.lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionBusy(SESSION_BUSY) from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _checked(path: Path) -> os.stat_result | None:
    """stat файла сессии или None(нет файла); чужой владелец или доступ группе/всем - NotConfigured."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    if st.st_mode & 0o077 or st.st_uid != os.geteuid():
        raise NotConfigured("Файл сессии Telegram доступен другим пользователям — исправьте права (chmod 600) и "
                            "владельца")
    return st


def read_private(path: Path) -> str | None:
    """Текст закрытого файла(сессия, ожидание кода) или None(файла нет или он пустой); права и владелец
    проверяются."""
    path = Path(path)
    st = _checked(path)
    if st is None or st.st_size == 0:
        return None
    data = path.read_bytes()
    if data.startswith(b"SQLite format 3"):
        raise NotConfigured("Файл сессии в старом формате — войдите заново")
    value = data.decode("utf-8", "replace").strip()
    return value or None


def read_session(path: Path) -> str | None:
    """Текст StringSession или None(файла нет или он пустой). Старый формат(SQLite), чужие права - NotConfigured.
    Значение регистрируется как секрет."""
    value = read_private(path)
    if value:
        logs.register_secret(value)
    return value


def write_private(path: Path, value: str) -> None:
    """Атомарная запись закрытого файла: каталог 0700, временный файл 0600(O_EXCL) → fsync → os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def write_session(path: Path, value: str) -> None:
    """Сохранить StringSession(0600, каталог 0700, атомарно)."""
    logs.register_secret(value)
    write_private(path, value)


def session_problem(path: Path) -> str | None:
    """Почему файл сессии не годится(права, владелец, старый формат) или None - файла нет или он в порядке.
    Без сети; читается только начало файла."""
    path = Path(path)
    try:
        st = _checked(path)
        if st is None or st.st_size == 0:
            return None
        with open(path, "rb") as f:
            head = f.read(16)
    except NotConfigured as e:
        return e.detail
    except OSError as e:
        return f"Файл сессии Telegram не читается ({type(e).__name__})"
    if head.startswith(b"SQLite format 3"):
        return "Файл сессии в старом формате — войдите заново"
    return None


def session_present(path: Path) -> bool:
    """Файл сессии есть, не пустой, права и владелец в порядке(без сети и без чтения содержимого)."""
    try:
        st = _checked(Path(path))
    except NotConfigured:
        return False
    except OSError:
        return False
    return st is not None and st.st_size > 0 and session_problem(path) is None


# ---------- каналы и сообщения ----------

def normalize_channel(s: str) -> str:
    """"x", "@x", "t.me/x", "https://t.me/x", "https://t.me/s/x", "t.me/x/123" → "x"(без регистра). Ссылки-приглашения
    ("t.me/+…", "joinchat", "t.me/c/…") - ValueError: закрытые каналы не читаются."""
    raw = (s or "").strip()
    v = _LINK.sub("", raw)
    linked = v != raw
    if linked and v.lower().startswith("s/"):
        v = v[2:]
    v = v.split("?", 1)[0].split("#", 1)[0].strip("/")
    low = v.lower()
    if v.startswith("+") or low.startswith("joinchat") or (linked and low.startswith("c/")):
        raise ValueError(PRIVATE_CHANNEL)
    v = v.lstrip("@")
    if linked and "/" in v:  # ссылка на сообщение: t.me/name/123
        v = v.split("/", 1)[0]
    if not _CHANNEL.match(v):
        raise ValueError(f"Неверное имя канала «{raw[:60]}»: нужно имя публичного канала — латиница, цифры и «_», "
                         "от 4 до 32 символов, например t.me/nord_hills")
    return v.lower()


def external_id(channel_id: int, first_msg_id: int) -> str:
    """Внешний ID публикации: "{id канала}:{id первого сообщения}"(положительный ID канала Telethon)."""
    return f"{int(channel_id)}:{int(first_msg_id)}"


def media_key(channel_id: int, m) -> str:
    """Ключ файла сообщения: "tg:{id канала}:{id сообщения}:{id файла}". ID файла(photo.id / document.id) меняется,
    когда правка заменяет фото или видео(editMessageMedia): новый файл загружается, прежний перестаёт быть текущим.
    Без ID файла - "tg:{канал}:{сообщение}"."""
    f = getattr(m, "photo", None)
    if f is None:
        f = getattr(m, "document", None)
    fid = getattr(f, "id", None)
    base = f"tg:{int(channel_id)}:{int(m.id)}"
    return f"{base}:{int(fid)}" if isinstance(fid, int) else base


def group_messages(msgs) -> list[list[Any]]:
    """Сообщения → публикации: по возрастанию ID, сообщения одного альбома(grouped_id) - вместе, повторы ID убраны."""
    by_id = {m.id: m for m in msgs if m is not None}
    out: list[list[Any]] = []
    albums: dict[int, list[Any]] = {}
    for mid in sorted(by_id):
        m = by_id[mid]
        gid = getattr(m, "grouped_id", None)
        if gid:
            g = albums.get(gid)
            if g is None:
                g = albums[gid] = []
                out.append(g)
            g.append(m)
        else:
            out.append([m])
    return out


def _media_kind(m) -> str | None:
    """"image" / "video" для фото и документов-изображений/видео; предпросмотр ссылки и прочие файлы - None."""
    if getattr(m, "web_preview", None) is not None:
        return None
    if getattr(m, "photo", None) is not None:
        return "image"
    if getattr(m, "document", None) is None:
        return None
    mime = (getattr(getattr(m, "file", None), "mime_type", None) or "").lower()
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("image/"):
        return "image"
    return None


def _edited(group: list) -> bool:
    return any(getattr(m, "edit_date", None) for m in group)


def _iso(dt) -> str | None:
    return models.iso(models.as_utc(dt)) if dt is not None else None


def _me_label(me) -> str:
    """"@username" или "id …" - без номера телефона."""
    username = getattr(me, "username", None)
    return f"@{username}" if username else f"id {getattr(me, 'id', '?')}"


# ---------- ошибки Telethon → ошибки подключения ----------

def _auth_errors() -> tuple:
    from telethon import errors

    return (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.SessionExpiredError,
            errors.UserDeactivatedError, errors.UserDeactivatedBanError, errors.AuthKeyDuplicatedError,
            errors.UnauthorizedError)


def _fatal_errors() -> tuple:
    """Ошибки, которые останавливают запуск целиком(не ошибка одного файла или канала)."""
    from telethon import errors

    return (errors.FloodError, ConnectionError) + _auth_errors()


def map_error(e: BaseException) -> BaseException:
    """Исключение Telethon/сети → типизированная ошибка подключения(integration-spec §10.1); прочее - как есть."""
    from telethon import errors

    if isinstance(e, ConnectorError):
        return e
    if isinstance(e, errors.FloodError):
        secs = int(getattr(e, "seconds", 0) or 0) or 3600
        return RateLimited(secs, f"Telegram просит подождать {secs} с (FloodWait) — сбор продолжится после паузы")
    if isinstance(e, (errors.UserDeactivatedError, errors.UserDeactivatedBanError)):
        return AuthExpired("Аккаунт Telegram сборщика удалён или заблокирован — нужен вход другим аккаунтом")
    if isinstance(e, errors.AuthKeyDuplicatedError):
        return AuthExpired("Сессия Telegram использована одновременно из двух мест и отозвана — повторите вход")
    if isinstance(e, _auth_errors()):
        return AuthExpired(SESSION_INVALID)
    if isinstance(e, (errors.ServerError, errors.TimedOutError)):
        return SourceError(f"Telegram временно не отвечает ({type(e).__name__})", retryable=True)
    if isinstance(e, errors.RPCError):
        return SourceError(f"Telegram отклонил запрос ({type(e).__name__})", retryable=False)
    if isinstance(e, ValueError) and str(e).startswith(_UNSUCCESSFUL):
        return SourceError("Telegram временно не отвечает (повторы запроса исчерпаны)", retryable=True)
    if isinstance(e, PermissionError):  # каталог сессии или блокировка - не сеть
        return NotConfigured("Нет доступа к файлу или каталогу сессии Telegram — проверьте права и владельца")
    if isinstance(e, (ConnectionError, OSError)):
        return SourceError(f"Нет связи с Telegram ({type(e).__name__})", retryable=True)
    return e


def _channel_problem(e: BaseException, name: str, *, resolving: bool = True) -> str | None:
    """Ошибка уровня канала → текст причины; None - ошибка не про канал(сеть, сбой Telegram - повтор задания).
    ValueError - "не найден" только при поиске канала по имени и только с текстом Telethon "имени нет"; прочие
    ValueError(например, повторы запроса исчерпаны) - не про канал."""
    from telethon import errors

    if isinstance(e, (errors.UsernameNotOccupiedError, errors.UsernameInvalidError)) \
            or (resolving and isinstance(e, ValueError) and str(e).startswith(_NOT_FOUND)):
        return f"канал @{name} не найден"
    if isinstance(e, (errors.ChannelPrivateError, errors.ChannelPublicGroupNaError)):
        return f"канал @{name} закрыт"
    if isinstance(e, errors.ChannelInvalidError):
        return f"канал @{name} недоступен"
    return None


# ---------- подключение ----------

class TelegramConnector(Connector):
    code = "telegram"
    kind = "collector"
    platform = "telegram"
    group = "collectors"
    name = "Telegram-каналы"
    via = "MTProto (Telethon) · отдельный аккаунт"
    required_env = ("TG_API_ID", "TG_API_HASH")
    config_fields = ("channels",)
    default_interval_min = 30
    interval_bounds = (15, 1440)
    secret_refs = ("env:TG_API_HASH", "file:TG_SESSION_PATH")
    limitation = ("Только публичные каналы; закрытые каналы не читаются. Правки старше последних {TG_EDIT_WINDOW} "
                  "сообщений канала не отслеживаются.")

    # --- настройка и состояние ---

    @staticmethod
    def _report(row: Optional["Collector"]) -> dict:
        return ((row.state or {}).get("report") if row is not None else None) or {}

    def _has_session(self, s: Settings, row: Optional["Collector"]) -> bool:
        if session_present(s.tg_session_path):
            return True
        return bool(self._report(row).get("sessionPresent"))

    def missing(self, s: Settings, row: Optional["Collector"] = None) -> list[str]:
        names = s.missing(*self.required_env)
        if not self._has_session(s, row):
            names.append("TG_SESSION_PATH")
        return names

    def missing_detail(self, s: Settings, row: Optional["Collector"] = None) -> str:
        names = self.missing(s, row)
        env = [n for n in names if n != "TG_SESSION_PATH"]
        if env:
            return f"Нет на сервере: {', '.join(env)}"
        if not names:
            return ""
        return session_problem(s.tg_session_path) or self._report(row).get("problem") or LOGIN_HINT

    def report(self, s: Settings) -> dict | None:
        """Воркер сообщает API, есть ли сессия(у API нет тома с сессией); без сети."""
        out = {"sessionPresent": session_present(s.tg_session_path), "checkedAt": models.iso(models.utcnow())}
        problem = session_problem(s.tg_session_path)
        if problem:
            out["problem"] = problem
        return out

    def normalize_item(self, field_name: str, value: str) -> str:
        try:
            return normalize_channel(value)
        except ValueError as e:
            raise ConfigError(field_name, str(e), value) from None

    # --- работа(воркер) ---

    def check(self, ctx: "RunContext") -> CheckResult:
        return self._run(ctx, self._check)

    def collect(self, ctx: "RunContext") -> None:
        self._run(ctx, self._collect)

    def _run(self, ctx: "RunContext", body: Callable):
        """Сессия под блокировкой → asyncio.run(подключение → body → отключение); ошибки Telethon - типизированные."""
        s = ctx.settings
        path = Path(s.tg_session_path)
        try:
            with session_lock(path):
                session = read_session(path)
                if not session:
                    raise NotConfigured(LOGIN_HINT)
                return asyncio.run(self._session(ctx, session, body))
        except SessionBusy:
            raise SourceError(SESSION_BUSY, retryable=True) from None
        except Exception as e:
            mapped = map_error(e)
            if mapped is e:
                raise
            raise mapped from e

    async def _session(self, ctx: "RunContext", session: str, body: Callable):
        logs.register_secret(ctx.settings.tg_api_hash.get())
        client = new_client(session, ctx.settings)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise AuthExpired(SESSION_INVALID)
            return await body(ctx, client)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    # --- проверка ---

    async def _check(self, ctx: "RunContext", client) -> CheckResult:
        me = await client.get_me()
        account = _me_label(me)
        names = list(ctx.config.get("channels") or [])
        chans = {k: dict(v) for k, v in (ctx.state.get("channels") or {}).items()}
        ok, bad = 0, []
        for name in names:
            try:
                await self._peer(ctx, client, name, chans, verify=True)
                ok += 1
            except _Unavailable as e:
                bad.append(e.text)
        if names and not ok:
            raise BadConfig(f"Ни один канал не доступен: {'; '.join(bad)}")
        if names:
            detail = f"Подключено: {account}. Каналов доступно: {ok} из {len(names)}"
            if bad:
                detail += f" ({'; '.join(bad)})"
        else:
            detail = f"Подключено: {account}. Каналы ещё не заданы — добавьте их в настройках"
        return CheckResult(True, detail, {"account": account, "channels": len(names), "channelsOk": ok})

    async def _peer(self, ctx: "RunContext", client, name: str, chans: dict, *, verify: bool = False):
        """Адрес канала: сохранённые id/accessHash(без поиска по имени) или get_entity(имя) один раз - сохраняется."""
        from telethon.tl.types import InputPeerChannel

        st = chans.get(name) or {}
        try:
            if st.get("id") and st.get("accessHash") is not None:
                peer = InputPeerChannel(int(st["id"]), int(st["accessHash"]))
                if verify:
                    await client.get_entity(peer)
                return peer
            ent = await client.get_entity(name)
        except Exception as e:
            text = _channel_problem(e, name)
            if text is None:
                raise
            if st.get("accessHash") is not None:  # сохранённый адрес не годится - в следующий раз искать по имени
                chans[name] = {k: v for k, v in st.items() if k != "accessHash"}
                ctx.save_state(channels=chans)
            raise _Unavailable(text) from None
        if getattr(ent, "access_hash", None) is None or not getattr(ent, "broadcast", False):
            raise _Unavailable(f"@{name} — не публичный канал (группа или пользователь)")
        if st.get("id") and int(st["id"]) != int(ent.id):  # имя перешло к другому каналу - позиция не переносится
            st = {}
        chans[name] = {**st, "id": int(ent.id), "accessHash": int(ent.access_hash),
                       "title": str(getattr(ent, "title", "") or "")[:255]}
        ctx.save_state(channels=chans)
        return InputPeerChannel(int(ent.id), int(ent.access_hash))

    # --- сбор ---

    async def _collect(self, ctx: "RunContext", client) -> None:
        """Каналы по очереди. Недоступный канал - причина "частично"(ctx.mark_partial), а не запись с ошибкой: сбор
        инкрементальный, и при тихом здоровом канале "запись с ошибкой" оказалась бы единственной - запуск выглядел бы
        ошибкой целиком(и откладывал бы расписание здорового канала)."""
        names = list(ctx.config.get("channels") or [])
        if not names:
            raise BadConfig("Список каналов пуст — добавьте каналы в настройках")
        chans = {k: dict(v) for k, v in (ctx.state.get("channels") or {}).items()}
        bad: list[str] = []
        done = 0
        try:
            for name in names:
                self._deadline(ctx)
                done += 1
                try:
                    peer = await self._peer(ctx, client, name, chans)
                except _Unavailable as e:
                    ctx.mark_partial(e.text)
                    bad.append(e.text)
                    continue
                try:
                    await self._channel(ctx, client, name, peer, chans)
                except Exception as e:
                    text = _channel_problem(e, name, resolving=False)
                    if text is None:
                        raise
                    st = chans.get(name) or {}
                    chans[name] = {k: v for k, v in st.items() if k != "accessHash"}
                    ctx.save_state(channels=chans)  # в следующий раз канал ищется по имени; позиция - если тот же id
                    ctx.mark_partial(text)
                    bad.append(text)
        except _TimeUp:
            ctx.mark_partial(TIME_UP)
            return
        if done == len(names) and len(bad) == len(names):
            raise BadConfig(f"Ни один канал не доступен: {'; '.join(bad)}")

    @staticmethod
    def _deadline(ctx: "RunContext") -> None:
        if ctx.time_left() < 60 or ctx.stopping():
            raise _TimeUp()

    async def _channel(self, ctx: "RunContext", client, name: str, peer, chans: dict) -> None:
        s = ctx.settings
        st = chans[name]
        ch_id = int(st["id"])
        last = st.get("lastId")
        old: list[list[Any]] = []  # правки и повторные загрузки(ID не больше lastId)
        if last is None:
            batch = [m async for m in client.iter_messages(peer, limit=s.tg_history_limit)]
            batch.reverse()
        else:
            last = int(last)
            batch = [m async for m in client.iter_messages(peer, min_id=last, reverse=True, limit=NEW_MAX)]
            if len(batch) >= NEW_MAX:
                ctx.note(f"@{name}: новых сообщений больше {NEW_MAX} — остальные будут собраны в следующий раз")
            if s.tg_edit_window > 0:
                window = [m async for m in client.iter_messages(peer, limit=s.tg_edit_window)]
                window = [m for m in window if m is not None and m.id <= last]
                for g in await self._complete(client, peer, window, need=_edited):
                    if max(m.id for m in g) <= last and _edited(g):
                        old.append(g)
        new = await self._complete(client, peer, batch)
        if last is not None:  # альбом, начатый до lastId и дополненный после, - уже в new
            new_first = {g[0].id for g in new}
            old = [g for g in old if g[0].id not in new_first]
        retry = dict(st.get("retry") or {})
        if retry:
            seen = {str(g[0].id) for g in new + old}
            want = {k: v for k, v in retry.items() if k not in seen}
            if want:
                ids = sorted({int(i) for v in want.values() for i in v})
                got = await client.get_messages(peer, ids=ids)
                by_id = {m.id: m for m in (got or []) if m is not None}
                for key, v in sorted(want.items(), key=lambda kv: int(kv[0])):
                    if int(key) not in by_id:  # первое сообщение публикации удалено в канале - повторять нечего:
                        retry.pop(key, None)   # остаток альбома под ID другого сообщения стал бы дубликатом
                        continue
                    old.append([by_id[int(i)] for i in v if int(i) in by_id])
        for g in sorted(old, key=lambda g: g[0].id):
            self._deadline(ctx)
            await self._publish(ctx, client, name, ch_id, peer, g, retry)
            st["retry"] = self._trim(retry)
            chans[name] = st
            ctx.save_state(channels=chans)
        for g in new:
            self._deadline(ctx)
            await self._publish(ctx, client, name, ch_id, peer, g, retry)
            st["lastId"] = max(int(st.get("lastId") or 0), max(m.id for m in g))
            st["retry"] = self._trim(retry)
            chans[name] = st
            ctx.save_state(channels=chans)
        st["retry"] = self._trim(retry)  # и снятые повторы, когда публиковать было нечего
        if not st.get("retry"):
            st.pop("retry", None)
        chans[name] = st
        ctx.save_state(channels=chans)

    @staticmethod
    def _trim(retry: dict) -> dict:
        keys = sorted(retry, key=int)[-RETRY_MAX:]
        return {k: retry[k] for k in keys}

    async def _complete(self, client, peer, batch: list, *, need: Callable | None = None) -> list[list[Any]]:
        """Группы выборки; альбомы на границе выборки дочитываются по соседним ID(get_messages). need - какие
        группы на границе стоит дочитывать(окно правок - только изменённые)."""
        batch = [m for m in batch if m is not None]
        groups = group_messages(batch)
        if not groups:
            return groups
        lo, hi = min(m.id for m in batch), max(m.id for m in batch)
        edge = [g for g in groups if getattr(g[0], "grouped_id", None)
                and (g[0].id == lo or g[-1].id == hi) and (need is None or need(g))]
        if not edge:
            return groups
        ids: set[int] = set()
        for g in edge:
            ids.update(i for i in range(g[0].id - ALBUM_SPAN, g[-1].id + ALBUM_SPAN + 1) if i > 0)
        ids -= {m.id for m in batch}
        if not ids:
            return groups
        got = await client.get_messages(peer, ids=sorted(ids))
        gids = {g[0].grouped_id for g in edge}
        extra = [m for m in (got or []) if m is not None and getattr(m, "grouped_id", None) in gids]
        return group_messages(batch + extra) if extra else groups

    async def _publish(self, ctx: "RunContext", client, name: str, ch_id: int, peer, group: list, retry: dict) -> None:
        """Группа сообщений → публикация → единый приём; файлы - только те, что ещё нужны(ключ файла включает ID файла
        Telegram - замена фото или видео правкой сообщения загружается заново)."""
        from ..ingest import ATTEMPTS_MAX

        msgs = [m for m in group if getattr(m, "action", None) is None]
        if not msgs:
            return
        first = msgs[0]
        eid = external_id(ch_id, first.id)
        text = next((m.message for m in msgs if (getattr(m, "message", None) or "").strip()), "")
        with_media = [(m, k) for m in msgs if (k := _media_kind(m))]
        if not text.strip() and not with_media:  # опрос, голосовое, файл без подписи - не публикация
            retry.pop(str(first.id), None)
            return
        k = ctx.known(eid)
        foreign = k is not None and k.collector != ctx.code  # принята другим источником - не перезаписывается
        role = "slide" if len(with_media) > 1 else None
        items: list[MediaIn] = []
        for m, kind in with_media:
            key = media_key(ch_id, m)
            if foreign or (k is not None and not k.needs(key)):
                items.append(MediaIn(key=key, kind=kind, role=role, origin_ref=f"telegram:{ch_id}/{m.id}"))
                continue
            items.append(await self._download(ctx, client, peer, ch_id, m, key, kind, role))
        views = [int(v) for m in msgs if isinstance((v := getattr(m, "views", None)), int)]
        edits = [m.edit_date for m in msgs if getattr(m, "edit_date", None)]
        edited = _iso(max(edits)) if edits else None
        gid = getattr(first, "grouped_id", None)
        pub = Publication(
            platform="telegram", external_id=eid, account=f"t.me/{name}", url=f"https://t.me/{name}/{first.id}",
            text=text, collected_at=ctx.now(), published_at=getattr(first, "date", None), media=items,
            reach=max(views) if views else None, extra={"edited": edited},
            raw={"channelId": ch_id, "messageIds": [m.id for m in msgs], "groupedId": str(gid) if gid else None,
                 "views": max(views) if views else None, "editDate": edited,
                 "fwdFrom": any(getattr(m, "fwd_from", None) is not None for m in msgs)})
        try:
            res = ctx.ingest(pub)
        except ValueError as e:
            ctx.item_failed(f"@{name}/{first.id}: публикация не принята ({e})")
            retry.pop(str(first.id), None)
            return
        failed = [m.key for m in items if m.status == "failed"]
        again = res.status != "foreign" and any(
            (k.failed_keys.get(key, 0) if k is not None else 0) + 1 < ATTEMPTS_MAX for key in failed)
        if again:
            retry[str(first.id)] = [m.id for m in msgs]
        else:
            retry.pop(str(first.id), None)

    async def _download(self, ctx: "RunContext", client, peer, ch_id: int, m, key: str, kind: str,
                        role: str | None, *, again: bool = False) -> MediaIn:
        """Файл сообщения → хранилище. Фото - в память; видео и документы - во временный файл по явному пути(не
        каталог); больше TG_MAX_VIDEO_MB - пропуск; срок TG_DOWNLOAD_TIMEOUT_S. Ошибка файла - MediaIn "failed"."""
        from telethon import errors

        s = ctx.settings
        ref = f"telegram:{ch_id}/{m.id}"
        timeout = float(s.tg_download_timeout_s)
        photo = getattr(m, "photo", None) is not None
        if not photo:
            size = int(getattr(getattr(m, "file", None), "size", None) or 0)
            if size > s.tg_max_video_mb * 1024 * 1024:
                what = "Видео больше" if kind == "video" else "Файл больше"
                return MediaIn(key=key, kind=kind, role=role, status="skipped", origin_ref=ref,
                               error=f"{what} {s.tg_max_video_mb} МБ — не загружено")
        path: Path | None = None
        try:
            if photo:
                data = await asyncio.wait_for(client.download_media(m, file=bytes), timeout)
                if not data:
                    return MediaIn(key=key, kind=kind, role=role, status="unavailable", origin_ref=ref,
                                   error="Telegram не отдал файл")
                return ctx.store_media(bytes(data), key=key, kind=kind, role=role, origin_ref=ref)
            path = ctx.temp_path(".bin")
            got = await asyncio.wait_for(client.download_media(m, file=str(path)), timeout)
            target = Path(got) if got else path
            if target.parent.resolve() != path.parent.resolve():  # не во временном каталоге хранилища - не берём
                Path(target).unlink(missing_ok=True)
                return MediaIn(key=key, kind=kind, role=role, status="failed", origin_ref=ref,
                               error="Файл сохранён не во временный каталог — отклонён")
            if not target.is_file():
                return MediaIn(key=key, kind=kind, role=role, status="unavailable", origin_ref=ref,
                               error="Telegram не отдал файл")
            if target != path:
                path = target
            return ctx.store_media(target, key=key, kind=kind, role=role, origin_ref=ref)
        except TimeoutError:
            return MediaIn(key=key, kind=kind, role=role, status="failed", origin_ref=ref,
                           error=f"Файл не загружен из Telegram за {timeout:g} с")
        except errors.FileReferenceExpiredError:
            if again:
                return MediaIn(key=key, kind=kind, role=role, status="failed", origin_ref=ref,
                               error="Ссылка на файл в Telegram устарела")
            fresh = await client.get_messages(peer, ids=m.id)
            if fresh is None:
                return MediaIn(key=key, kind=kind, role=role, status="unavailable", origin_ref=ref,
                               error="Сообщение удалено в Telegram")
            return await self._download(ctx, client, peer, ch_id, fresh, key, kind, role, again=True)
        except _fatal_errors():
            raise
        except Exception as e:
            return MediaIn(key=key, kind=kind, role=role, status="failed", origin_ref=ref,
                           error=f"Файл не загружен из Telegram ({type(e).__name__})")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)  # частичная загрузка; сохранённый файл уже перенесён в хранилище


CONNECTOR = TelegramConnector()
router = None
