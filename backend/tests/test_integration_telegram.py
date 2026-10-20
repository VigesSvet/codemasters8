"""Telegram-каналы: поддельный клиент Telethon через telegram.CLIENT_FACTORY, настоящие классы telethon.errors; сети
нет. Модули подключения импортируются внутри тестов."""
import asyncio
import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, UTC
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from conftest import mp4_bytes, png_bytes

from app import models
from app.db import SessionLocal

AUTHOR = "Омарова Ж."
API_ID = 12345
API_HASH = "FAKETGHASH-0123456789abcdefgh"
PHONE = "+77010000000"
SESSION = "FAKE-STRING-SESSION-0123456789abcdefghijklmnopqrstuvwxyz"
T0 = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
LOGIN = "Нет авторизованной сессии Telegram: выполните вход — python -m app.integrations.telegram_login --send-code"


# ---------- поддельный Telethon ----------

class Msg:
    """Сообщение канала с полями, которые читает сборщик(как у telethon Message)."""

    def __init__(self, id, message="", *, grouped_id=None, photo=None, video=None, mime=None, size=None, views=None,
                 edit_date=None, fwd=False, action=None, web=False):
        self.id = id
        self.message = message
        self.grouped_id = grouped_id
        self.views = views
        self.edit_date = edit_date
        self.fwd_from = SimpleNamespace(from_id=1) if fwd else None
        self.action = action
        self.date = T0 + timedelta(minutes=id)
        self.web_preview = SimpleNamespace(url="https://example.com") if web else None
        self.data = photo if photo is not None else video
        # ID файла Telegram: тот же файл - тот же ID, другой файл(замена правкой) - другой
        fid = int(hashlib.sha256(self.data).hexdigest()[:12], 16) if self.data is not None else None
        self.photo = SimpleNamespace(id=fid) if photo is not None else None
        self.document = SimpleNamespace(id=fid) if video is not None else None
        if video is not None:
            self.file = SimpleNamespace(size=len(video) if size is None else size, mime_type=mime or "video/mp4")
        elif photo is not None:
            self.file = SimpleNamespace(size=len(photo), mime_type="image/jpeg")
        else:
            self.file = None


class FakeTelegram:
    """Клиент Telethon: каналы и сообщения в памяти, журнал вызовов, подменяемые ошибки."""

    def __init__(self):
        self.authorized = True
        self.me = SimpleNamespace(id=777, username="saq_reader", phone=PHONE)
        self.channels: dict = {}
        self.private: set = set()
        self.history: dict = {}
        self.calls: list = []
        self.fail: dict = {}  # метод → исключение или функция(*аргументы) → исключение | None
        self.download_fail: dict = {}  # id сообщения → исключение(один раз)
        self.download_hang: set = set()
        self.factory_args: list = []
        self.saved = "FAKE-NEW-SESSION-abcdefghijklmnopqrstuvwxyz0123"
        self.session = SimpleNamespace(save=lambda: self.saved)
        self.code_hash = "FAKEHASH-0123456789abcdef"
        self.sign_in_errors: list = []
        self.path: Path = Path()

    def factory(self, session, api_id, api_hash):
        self.factory_args.append((session, api_id, api_hash))
        return self

    def channel(self, name, cid, *msgs, broadcast=True):
        self.channels[name] = SimpleNamespace(id=cid, access_hash=cid * 10 + 7, title=f"Канал {name}", username=name,
                                              broadcast=broadcast)
        self.history[cid] = {m.id: m for m in msgs}
        return self.channels[name]

    def add(self, cid, *msgs):
        for m in msgs:
            self.history[cid][m.id] = m

    def names(self) -> list:
        return [c[0] for c in self.calls]

    def key(self, cid, mid) -> str:
        """Ключ файла сообщения, как его строит сборщик: tg:{канал}:{сообщение}:{ID файла}."""
        m = self.history[cid][mid]
        return f"tg:{cid}:{mid}:{(m.photo or m.document).id}"

    def _hit(self, name, *args):
        self.calls.append((name, args))
        f = self.fail.get(name)
        if f is None:
            return
        exc = f if isinstance(f, BaseException) else f(*args)
        if exc is not None:
            raise exc

    async def connect(self):
        self._hit("connect")

    async def disconnect(self):
        self.calls.append(("disconnect", ()))

    async def is_user_authorized(self):
        self._hit("is_user_authorized")
        return self.authorized

    async def get_me(self):
        self._hit("get_me")
        return self.me

    async def get_entity(self, x):
        from telethon import errors

        self._hit("get_entity", x)
        if isinstance(x, str):
            if x in self.private:
                raise errors.ChannelPrivateError(request=None)
            ch = self.channels.get(x)
            if ch is None:  # так Telethon сообщает о свободном имени(UsernameNotOccupiedError внутри)
                raise ValueError(f'No user has "{x}" as username') from errors.UsernameNotOccupiedError(request=None)
            return ch
        ch = next((c for c in self.channels.values() if c.id == x.channel_id), None)
        if ch is None:
            raise errors.ChannelInvalidError(request=None)
        return ch

    def iter_messages(self, peer, limit=None, min_id=0, reverse=False):
        self._hit("iter_messages", peer.channel_id, limit, min_id, reverse)
        msgs = sorted((m for m in self.history[peer.channel_id].values() if m.id > (min_id or 0)),
                      key=lambda m: m.id, reverse=not reverse)
        if limit is not None:
            msgs = msgs[:limit]

        async def gen():
            for m in msgs:
                yield m
        return gen()

    async def get_messages(self, peer, ids=None):
        self._hit("get_messages", peer.channel_id, ids)
        h = self.history[peer.channel_id]
        if isinstance(ids, int):
            return h.get(ids)
        return [h.get(i) for i in ids]

    async def download_media(self, msg, file=None):
        self._hit("download_media", msg.id, file)
        exc = self.download_fail.pop(msg.id, None)
        if exc is not None:
            raise exc
        if msg.id in self.download_hang:
            await asyncio.sleep(30)
        if file is bytes:
            return msg.data
        Path(file).write_bytes(msg.data)
        return file

    async def send_code_request(self, phone):
        from telethon.tl.types.auth import SentCodeTypeApp

        self._hit("send_code_request", phone)
        return SimpleNamespace(phone_code_hash=self.code_hash, type=SentCodeTypeApp(length=5))

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        self._hit("sign_in", phone, code, password, phone_code_hash)
        if self.sign_in_errors:
            raise self.sign_in_errors.pop(0)
        self.authorized = True
        return self.me

    async def log_out(self):
        self._hit("log_out")
        self.authorized = False
        return True


@pytest.fixture
def tg(client, env, monkeypatch, tmp_path):
    """Ключи Telegram(поддельные), свой файл сессии в tmp_path и поддельный клиент вместо Telethon."""
    from app.integrations import telegram

    fake = FakeTelegram()
    fake.path = tmp_path / "private" / "telegram.session"
    env(TG_API_ID=str(API_ID), TG_API_HASH=API_HASH, TG_PHONE=PHONE, TG_SESSION_PATH=str(fake.path))
    monkeypatch.setattr(telegram, "CLIENT_FACTORY", fake.factory)
    return fake


# ---------- помощники ----------

def login(fake: FakeTelegram) -> None:
    from app.integrations import telegram

    telegram.write_session(fake.path, SESSION)


def configure(channels, **values) -> None:
    from app.integrations import service

    with SessionLocal() as db:
        r = service.row(db, "telegram", create=True)
        r.config = {"channels": list(channels)}
        for k, v in values.items():
            setattr(r, k, v)
        db.commit()


def row() -> models.Collector:
    from app.integrations import service

    with SessionLocal() as db:
        return service.row(db, "telegram")


def request(trigger: str = "manual", now=None) -> int:
    from app.integrations import service

    with SessionLocal() as db:
        run, _ = service.request_run(db, "telegram", trigger=trigger, who=AUTHOR, now=now)
        db.commit()
        return run.id


def collect(run_worker, *, trigger: str = "manual", now=None) -> models.CollectionRun:
    run_id = request(trigger, now)
    run_worker(now=now)
    with SessionLocal() as db:
        return db.get(models.CollectionRun, run_id)


def items() -> list:
    with SessionLocal() as db:
        return list(db.scalars(select(models.IngestItem).where(models.IngestItem.collector == "telegram")
                               .order_by(models.IngestItem.id)))


def item(eid: str) -> models.IngestItem:
    with SessionLocal() as db:
        return db.scalar(select(models.IngestItem).where(models.IngestItem.platform == "telegram",
                                                         models.IngestItem.external_id == eid))


def assets(eid: str) -> list:
    it = item(eid)
    with SessionLocal() as db:
        return list(db.scalars(select(models.MediaAsset).where(models.MediaAsset.item_id == it.id,
                                                               models.MediaAsset.parent_id.is_(None))
                               .order_by(models.MediaAsset.position)))


def ad(ad_id: str) -> models.Ad:
    with SessionLocal() as db:
        return db.get(models.Ad, ad_id)


def versions(eid: str) -> int:
    it = item(eid)
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(models.IngestVersion)
                         .where(models.IngestVersion.item_id == it.id))


def job_of(run_id: int) -> models.Job:
    with SessionLocal() as db:
        return db.scalar(select(models.Job).where(models.Job.run_id == run_id).order_by(models.Job.id.desc()))


def connector(client) -> dict:
    r = client.get("/api/sources/connectors/telegram")
    assert r.status_code == 200, r.text
    return r.json()


def channel_state(name: str) -> dict:
    return (row().state or {})["channels"][name]


def three(fake: FakeTelegram) -> None:
    """Канал nord_hills: пост с фото и альбом из двух фото."""
    fake.channel("nord_hills", 1001,
                 Msg(10, "ЖК Nord Hills: старт продаж, рассрочка 0%", photo=png_bytes((1, 0, 0)), views=120),
                 Msg(11, "Альбом: виды с 9 этажа", grouped_id=555, photo=png_bytes((2, 0, 0)), views=80),
                 Msg(12, "", grouped_id=555, photo=png_bytes((3, 0, 0)), views=95, fwd=True))


# ---------- нормализация и файл сессии ----------

def test_normalize_channel_and_ids():
    from app.integrations import telegram

    for raw in ("nord_hills", "@Nord_Hills", "t.me/nord_hills", "https://t.me/nord_hills",
                "https://t.me/s/nord_hills", "https://t.me/nord_hills/123", "http://telegram.me/nord_hills/"):
        assert telegram.normalize_channel(raw) == "nord_hills", raw
    for raw in ("https://t.me/+AbCdEf123", "t.me/joinchat/AbCdEf", "+AbCdEf", "https://t.me/c/1234567890/5"):
        with pytest.raises(ValueError, match="Закрытые каналы не поддерживаются"):
            telegram.normalize_channel(raw)
    for raw in ("abc", "1channel", "https://t.me/", "имя_канала"):
        with pytest.raises(ValueError, match="Неверное имя канала"):
            telegram.normalize_channel(raw)
    assert telegram.external_id(1001, 11) == "1001:11"
    a, b, c = Msg(3, grouped_id=7), Msg(1), Msg(2, grouped_id=7)
    assert [[m.id for m in g] for g in telegram.group_messages([a, b, c, a])] == [[1], [2, 3]]


def test_session_file_states(tg, client):
    from app import logs
    from app.integrations import telegram
    from app.integrations.base import NotConfigured
    from app.settings import get_settings

    c = connector(client)
    assert (c["status"], c["missing"]) == ("not_configured", ["TG_SESSION_PATH"])
    assert c["statusDetail"].startswith(LOGIN)
    path = tg.path
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    os.chmod(path, 0o600)
    with pytest.raises(NotConfigured, match="старом формате"):
        telegram.read_session(path)
    assert connector(client)["statusDetail"] == "Файл сессии в старом формате — войдите заново"
    path.write_text(SESSION)
    os.chmod(path, 0o644)
    with pytest.raises(NotConfigured, match="доступен другим пользователям"):
        telegram.read_session(path)
    rep = telegram.CONNECTOR.report(get_settings())
    assert rep["sessionPresent"] is False and "chmod 600" in rep["problem"]
    assert connector(client)["status"] == "not_configured"
    # API файла не видит - наличие сессии сообщает воркер(state.report.sessionPresent)
    path.unlink()
    configure([], state={"report": {"sessionPresent": True, "checkedAt": models.iso(models.utcnow())}})
    c = connector(client)
    assert (c["status"], c["missing"]) == ("unchecked", [])
    configure([], state={"report": {"sessionPresent": False, "checkedAt": models.iso(models.utcnow())}})
    assert connector(client)["status"] == "not_configured"
    # запись сессии: файл 0600, каталог 0700, значение маскируется
    telegram.write_session(path, SESSION)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert telegram.read_session(path) == SESSION
    assert telegram.CONNECTOR.report(get_settings())["sessionPresent"] is True
    assert SESSION not in logs.redact(f"сессия {SESSION}")
    assert connector(client)["status"] == "unchecked"


def test_session_lock_busy(tg, client, run_worker, capsys):
    from app.integrations import telegram, telegram_login

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    configure(["nord_hills"])
    with telegram.session_lock(tg.path):
        with pytest.raises(telegram.SessionBusy):
            with telegram.session_lock(tg.path):
                pass
        run_id = request()
        run_worker()
        assert telegram_login.main(["--status"]) == 7
    assert "остановите его (docker compose stop worker)" in capsys.readouterr().err
    job = job_of(run_id)
    assert job.status == "queued" and job.attempts == 1  # повтор позже, а не ошибка
    with SessionLocal() as db:
        notes = db.get(models.CollectionRun, run_id).notes
    assert any("Сессия Telegram занята другим процессом" in n["text"] for n in notes)
    assert tg.calls == []  # без блокировки клиент не создаётся


def test_session_file_system_error_not_network(tg, client, run_worker):
    """Сбой файловой системы с файлом сессии(здесь - каталог на месте файла блокировки) - ошибка настроек с причиной,
    а не "Нет связи с Telegram" с повторами задания."""
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    configure(["nord_hills"])
    Path(f"{tg.path}.lock").mkdir()
    run = collect(run_worker)
    assert (run.status, run.error_code, job_of(run.id).status) == ("not_configured", "not_configured", "failed")
    assert (row().status, row().status_detail) == ("not_configured",
                                                   "Каталог сессии Telegram недоступен (IsADirectoryError)")
    assert not any("Нет связи" in n for n in run_notes(run.id)) and tg.calls == []


def test_corrupted_session_expired(tg, client, run_worker, monkeypatch):
    """Испорченный текст сессии(не StringSession) - "сессия недействительна, повторите вход", а не внутренняя ошибка
    сборщика; клиент не создаётся."""
    from app.integrations import telegram
    from app.integrations.base import AuthExpired

    for bad in ("garbage", "1garbage", "1" + "A" * 352):
        with pytest.raises(AuthExpired, match="Сессия Telegram недействительна"):
            telegram.make_client(bad, API_ID, "0123456789abcdef0123456789abcdef")
    monkeypatch.setattr(telegram, "CLIENT_FACTORY", None)  # настоящий make_client: до сети дело не доходит
    telegram.write_session(tg.path, "1garbage-not-a-session")
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.error_code) == ("failed", "auth_expired")
    assert (row().status, row().status_detail) == ("expired", "Сессия Telegram недействительна — повторите вход")


# ---------- проверка подключения ----------

def test_check_unauthorized_ok_and_no_channels(tg, client, run_worker):
    login(tg)
    tg.authorized = False
    run = collect(run_worker, trigger="test")
    assert (run.status, run.error_code) == ("failed", "auth_expired")
    assert (row().status, row().status_detail) == ("expired", "Сессия Telegram недействительна — повторите вход")
    assert "start" not in tg.names() and tg.names()[0] == "connect" and tg.names()[-1] == "disconnect"
    assert tg.factory_args[-1] == (SESSION, API_ID, API_HASH)

    tg.authorized = True
    tg.channel("nord_hills", 1001)
    configure(["nord_hills", "ghost_channel"])
    run = collect(run_worker, trigger="test")
    r = row()
    assert run.status == "succeeded" and r.status == "ready"
    assert r.status_detail == ("Подключено: @saq_reader. Каналов доступно: 1 из 2 (канал @ghost_channel не "
                               "найден)")
    assert (r.facts["account"], r.facts["channels"], r.facts["channelsOk"]) == ("@saq_reader", 2, 1)
    assert PHONE not in json.dumps(r.facts) and channel_state("nord_hills")["accessHash"] == 10017
    # сохранённый адрес канала проверяется без поиска по имени
    tg.calls.clear()
    collect(run_worker, trigger="test")
    assert ("get_entity", ("nord_hills",)) not in tg.calls

    configure(["ghost_channel"])
    run = collect(run_worker, trigger="test")
    assert (run.status, row().status) == ("failed", "error")
    assert row().status_detail == "Ни один канал не доступен: канал @ghost_channel не найден"


# ---------- сбор ----------

def test_first_collect_album(tg, client, run_worker, ocr_stub):
    login(tg)
    three(tg)
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.items_seen, run.items_new, run.media_saved) == ("succeeded", 2, 2, 3)
    assert [i.external_id for i in items()] == ["1001:10", "1001:11"]
    album = item("1001:11")
    assert (album.url, album.account) == ("https://t.me/nord_hills/11", "t.me/nord_hills")
    assert album.raw == {"channelId": 1001, "messageIds": [11, 12], "groupedId": "555", "views": 95,
                         "editDate": None, "fwdFrom": True}
    files = assets("1001:11")
    assert [(a.source_key, a.status, a.role, a.origin_ref) for a in files] == [
        (tg.key(1001, 11), "downloaded", "slide", "telegram:1001/11"),
        (tg.key(1001, 12), "downloaded", "slide", "telegram:1001/12")]
    assert files[0].source_key.startswith("tg:1001:11:") and files[0].source_key != files[1].source_key
    a = ad(album.ad_id)
    assert (a.text, a.reach, a.platform, a.origin, a.status) == ("Альбом: виды с 9 этажа", 95, "telegram",
                                                                 "collected", "new")
    assert a.image == f"/api/media/{files[0].id}"
    st = channel_state("nord_hills")
    assert (st["id"], st["accessHash"], st["lastId"], st["title"]) == (1001, 10017, 12, "Канал nord_hills")
    assert ("iter_messages", (1001, 50, 0, False)) in tg.calls  # первый запуск - последние TG_HISTORY_LIMIT
    assert row().status == "working"


def test_rerun_new_only_and_failed_photo_retried(tg, client, run_worker, ocr_stub):
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост с фото", photo=png_bytes((4, 0, 0))), Msg(11, "Только текст"))
    tg.download_fail[10] = RuntimeError("обрыв загрузки")
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.items_new, run.media_failed) == ("partial", 2, 1)
    assert [(a.status, a.attempts) for a in assets("1001:10")] == [("failed", 1)]
    assert channel_state("nord_hills")["retry"] == {"10": [10]}

    tg.calls.clear()
    tg.add(1001, Msg(12, "Новый пост"))
    run = collect(run_worker)
    assert (run.status, run.items_new, run.items_unchanged, run.media_saved) == ("succeeded", 1, 1, 1)
    assert ("iter_messages", (1001, 500, 11, True)) in tg.calls
    assert "get_entity" not in tg.names()
    assert ("get_messages", (1001, [10])) in tg.calls
    assert [(a.status, a.attempts) for a in assets("1001:10")] == [("downloaded", 2)]
    assert ad(item("1001:10").ad_id).image is not None
    assert [i.external_id for i in items()] == ["1001:10", "1001:11", "1001:12"]
    st = channel_state("nord_hills")
    assert st["lastId"] == 12 and "retry" not in st


def test_edited_message_updated(tg, client, run_worker, ocr_stub):
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Цена от 350 000 ₸ за м²"), Msg(11, "Другой пост"))
    configure(["nord_hills"])
    collect(run_worker)
    tg.add(1001, Msg(10, "Цена от 390 000 ₸ за м²", edit_date=T0 + timedelta(hours=3)))
    run = collect(run_worker)
    assert (run.items_updated, run.items_new) == (1, 0)
    it = item("1001:10")
    assert (it.version, versions("1001:10")) == (2, 2)
    assert ad(it.ad_id).text == "Цена от 390 000 ₸ за м²" and it.raw["editDate"] == "2026-10-01T09:00:00+00:00"
    run = collect(run_worker)  # правка уже учтена
    assert (run.items_updated, run.items_unchanged, versions("1001:10")) == (0, 1, 2)


def test_album_split_at_boundary_completed(tg, client, run_worker, env, ocr_stub):
    env(TG_HISTORY_LIMIT="2")
    login(tg)
    tg.channel("nord_hills", 1001, Msg(1, "Старый пост"),
               *[Msg(i, "Планировки" if i == 2 else "", grouped_id=9, photo=png_bytes((i, 9, 0))) for i in (2, 3, 4)])
    configure(["nord_hills"])
    run = collect(run_worker)
    assert ("iter_messages", (1001, 2, 0, False)) in tg.calls
    gets = [c for c in tg.calls if c[0] == "get_messages"]
    assert gets and 2 in gets[0][1][1]
    assert [i.external_id for i in items()] == ["1001:2"] and run.items_new == 1
    assert [a.source_key for a in assets("1001:2")] == [tg.key(1001, 2), tg.key(1001, 3), tg.key(1001, 4)]
    assert ad(item("1001:2").ad_id).text == "Планировки"


def test_flood_wait_rate_limited_and_state_kept(tg, client, run_worker):
    from telethon import errors

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост 1"), Msg(11, "Пост 2"))
    tg.channel("ertis_invest", 1002, Msg(20, "Пост 3"))
    tg.fail["iter_messages"] = lambda cid, *a: errors.FloodWaitError(request=None, capture=120) if cid == 1002 \
        else None
    configure(["nord_hills", "ertis_invest"])
    now = models.utcnow()
    run = collect(run_worker, now=now)
    r = row()
    assert (run.status, run.error_code, r.status) == ("rate_limited", "rate_limited", "rate_limited")
    assert abs((models.as_utc(r.next_run_at) - now).total_seconds() - 120) < 5
    assert channel_state("nord_hills")["lastId"] == 11 and "lastId" not in channel_state("ertis_invest")
    assert [i.external_id for i in items()] == ["1001:10", "1001:11"]
    assert tg.calls[-2][0] == "iter_messages" and tg.calls[-2][1][0] == 1002 and tg.calls[-1][0] == "disconnect"


def test_auth_key_unregistered_expired(tg, client, run_worker):
    from telethon import errors

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    tg.fail["iter_messages"] = errors.AuthKeyUnregisteredError(request=None)
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.error_code, row().status) == ("failed", "auth_expired", "expired")
    assert row().status_detail == "Сессия Telegram недействительна — повторите вход"


def test_channels_config_and_unknown(tg, client, run_worker, admin_headers, ocr_stub):
    login(tg)
    for bad in ("https://t.me/+AbCdEf123", "https://t.me/joinchat/AbCdEf"):
        r = client.patch("/api/sources/telegram", headers=admin_headers,
                         json={"author": AUTHOR, "config": {"channels": ["nord_hills", bad]}})
        assert r.status_code == 422, r.text
        err = r.json()["detail"][0]
        assert err["loc"] == ["body", "config", "channels"] and "Закрытые каналы не поддерживаются" in err["msg"]
    r = client.patch("/api/sources/telegram", headers=admin_headers,
                     json={"author": AUTHOR, "config": {"channels": ["@Nord_Hills", "https://t.me/s/ertis_invest",
                                                                     "nord_hills", "ghost_channel"]}})
    assert r.status_code == 200, r.text
    assert row().config["channels"] == ["nord_hills", "ertis_invest", "ghost_channel"]
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    tg.channel("ertis_invest", 1002)
    tg.private.add("ghost_channel")
    now = models.utcnow()
    run = collect(run_worker, now=now)
    # недоступный канал - причина "частично", а не запись с ошибкой
    assert (run.status, run.items_new, run.items_failed) == ("partial", 1, 0)
    assert (row().status, row().status_detail) == ("partial", "канал @ghost_channel закрыт")
    with SessionLocal() as db:
        notes = [n for n in db.get(models.CollectionRun, run.id).notes]
    assert {"level": "warn", "text": "канал @ghost_channel закрыт"} in [{k: n[k] for k in ("level", "text")}
                                                                         for n in notes]
    # следующий запуск: в доступных каналах нового нет - по-прежнему "частично", не "все записи с ошибкой",
    # и расписание здоровых каналов не откладывается
    later = now + timedelta(hours=1)
    run = collect(run_worker, now=later)
    r = row()
    assert (run.status, run.error_code, run.items_seen, run.items_failed) == ("partial", None, 0, 0)
    assert (r.status, r.status_detail, r.failures) == ("partial", "канал @ghost_channel закрыт", 0)
    assert abs((models.as_utc(r.next_run_at) - later).total_seconds() - 30 * 60) < 1
    # ни один канал не доступен - ошибка настроек, а не "нет новых"
    configure(["ghost_channel", "missing_one"])
    run = collect(run_worker)
    r = row()
    assert (run.status, run.error_code, r.status, r.facts["stopped"]) == ("failed", "bad_config", "error", True)
    assert r.status_detail == "Ни один канал не доступен: канал @ghost_channel закрыт; канал @missing_one не найден"


def test_channel_closed_between_runs_keeps_position(tg, client, run_worker):
    from telethon import errors

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост 1"))
    configure(["nord_hills"])
    collect(run_worker)
    tg.fail["iter_messages"] = errors.ChannelPrivateError(request=None)
    run = collect(run_worker)
    assert (run.status, run.error_code, row().status_detail) == (
        "failed", "bad_config", "Ни один канал не доступен: канал @nord_hills закрыт")
    st = channel_state("nord_hills")
    assert (st["id"], st["lastId"], "accessHash" in st) == (1001, 10, False)
    # канал снова открыт: адрес ищется по имени, позиция продолжается(тот же id)
    tg.fail.clear()
    tg.add(1001, Msg(11, "Пост 2"))
    tg.calls.clear()
    run = collect(run_worker)
    assert ("get_entity", ("nord_hills",)) in tg.calls and ("iter_messages", (1001, 500, 10, True)) in tg.calls
    assert run.items_new == 1 and [i.external_id for i in items()] == ["1001:10", "1001:11"]


def run_notes(run_id: int) -> list:
    with SessionLocal() as db:
        return [n["text"] for n in db.get(models.CollectionRun, run_id).notes]


def test_telegram_outage_retried_not_unknown_channel(tg, client, run_worker):
    """Сбой серверов Telegram(повторы Telethon исчерпаны) - повтор задания с понятной причиной, а не "канал не
    найден"(ошибка настроек, остановка расписания) и не "внутренняя ошибка сборщика"."""
    from telethon import errors

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    configure(["nord_hills"])
    # так Telethon сообщает об исчерпанных повторах без raise_last_call_error
    tg.fail["get_entity"] = ValueError("Request was unsuccessful 3 time(s)")
    t0 = models.utcnow()
    run = collect(run_worker, now=t0)
    r = row()
    assert (job_of(run.id).status, run.error_code) == ("queued", None)
    assert "stopped" not in (r.facts or {}) and r.status != "error" and connector(client)["status"] == "queued"
    notes = run_notes(run.id)
    assert notes[-1].startswith("Telegram временно не отвечает (повторы запроса исчерпаны) — повтор 2 из 5")
    assert not any("не найден" in n for n in notes)
    # с raise_last_call_error Telethon бросает саму ошибку сервера - тоже повтор
    tg.fail = {"iter_messages": errors.RpcCallFailError(request=None)}
    run_worker(now=t0 + timedelta(minutes=5))
    assert run_notes(run.id)[-1].startswith("Telegram временно не отвечает (RpcCallFailError) — повтор 3 из 5")
    assert ("get_entity", ("nord_hills",)) in tg.calls and channel_state("nord_hills")["id"] == 1001
    # ValueError об исчерпанных повторах при чтении истории - не "внутренняя ошибка сборщика"
    tg.fail = {"iter_messages": ValueError("Request was unsuccessful 3 time(s)")}
    run_worker(now=t0 + timedelta(minutes=15))
    notes = run_notes(run.id)
    assert notes[-1].startswith("Telegram временно не отвечает (повторы запроса исчерпаны) — повтор 4 из 5")
    assert not any("Внутренняя ошибка" in n for n in notes)
    # сбой прошёл - сбор продолжается тем же запуском
    tg.fail.clear()
    run_worker(now=t0 + timedelta(hours=1))
    with SessionLocal() as db:
        run = db.get(models.CollectionRun, run.id)
    assert (run.status, run.items_new, row().status) == ("succeeded", 1, "working")
    # имя, которого нет в Telegram, - по-прежнему "не найден"
    configure(["nord_hills", "ghost_channel"])
    run = collect(run_worker, now=t0 + timedelta(hours=2))
    assert (run.status, row().status_detail) == ("partial", "канал @ghost_channel не найден")


def test_edit_replacing_photo_downloads_new_file(tg, client, run_worker, ocr_stub):
    """Правка с заменой фото(editMessageMedia): новый файл загружается и становится текущим, прежний остаётся в
    истории нетекущим."""
    login(tg)
    old, new = png_bytes((1, 2, 3)), png_bytes((9, 8, 7))
    tg.channel("nord_hills", 1001, Msg(10, "Прайс-лист на октябрь", photo=old))
    configure(["nord_hills"])
    collect(run_worker)
    old_key = tg.key(1001, 10)
    tg.add(1001, Msg(10, "Прайс-лист на октябрь", photo=new, edit_date=T0 + timedelta(hours=1)))
    new_key = tg.key(1001, 10)
    assert new_key != old_key
    tg.calls.clear()
    run = collect(run_worker)
    assert (run.items_updated, run.media_saved) == (1, 1)
    assert [c[1][0] for c in tg.calls if c[0] == "download_media"] == [10]
    it = item("1001:10")
    assert (it.version, versions("1001:10")) == (2, 2)
    files = assets("1001:10")
    assert sorted((a.source_key, a.current, a.sha256) for a in files) == sorted([
        (old_key, False, hashlib.sha256(old).hexdigest()), (new_key, True, hashlib.sha256(new).hexdigest())])
    cur = next(a for a in files if a.current)
    assert ad(it.ad_id).image == f"/api/media/{cur.id}"
    run = collect(run_worker)  # правка учтена: файлы не загружаются повторно
    assert (run.items_updated, run.media_saved) == (0, 0) and versions("1001:10") == 2


def test_retry_after_album_first_message_deleted_no_duplicate(tg, client, run_worker, ocr_stub):
    """Повтор загрузки альбома, первое сообщение которого удалено в канале: публикация под ID другого сообщения была бы
    дубликатом - повтор снимается."""
    login(tg)
    tg.channel("nord_hills", 1001, Msg(11, "Альбом ЖК", grouped_id=5, photo=png_bytes((1, 1, 1))),
               Msg(12, "", grouped_id=5, photo=png_bytes((2, 2, 2))))
    tg.download_fail[12] = RuntimeError("обрыв загрузки")
    configure(["nord_hills"])
    collect(run_worker)
    assert channel_state("nord_hills")["retry"] == {"11": [11, 12]}
    del tg.history[1001][11]
    tg.calls.clear()
    run = collect(run_worker)
    assert ("get_messages", (1001, [11, 12])) in tg.calls and "download_media" not in tg.names()
    assert (run.status, run.items_new, run.items_seen) == ("succeeded", 0, 0)
    assert [i.external_id for i in items()] == ["1001:11"] and "retry" not in channel_state("nord_hills")


def test_video_limits_timeout_and_explicit_path(tg, client, run_worker, env, ocr_stub):
    from app import media

    env(TG_MAX_VIDEO_MB="1", TG_DOWNLOAD_TIMEOUT_S="1")
    login(tg)
    tg.channel("nord_hills", 1001,
               Msg(10, "Видеотур по ЖК", video=mp4_bytes(), size=2 * 1024 * 1024),
               Msg(11, "Короткое видео", video=mp4_bytes(512)),
               Msg(12, "Фото дня", photo=png_bytes((7, 7, 0))),
               Msg(13, "Ссылка на сайт застройщика", web=True))
    tg.download_hang.add(12)
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.items_new, run.media_saved, run.media_failed) == ("partial", 4, 1, 1)
    big, = assets("1001:10")
    assert (big.status, big.error) == ("skipped", "Видео больше 1 МБ — не загружено")
    small, = assets("1001:11")
    assert (small.status, small.kind, small.mime) == ("downloaded", "video", "video/mp4")
    slow, = assets("1001:12")
    assert (slow.status, slow.error) == ("failed", "Файл не загружен из Telegram за 1 с")
    assert assets("1001:13") == []  # предпросмотр ссылки не скачивается
    downloads = {c[1][0]: c[1][1] for c in tg.calls if c[0] == "download_media"}
    assert 10 not in downloads and downloads[12] is bytes
    target = Path(downloads[11])
    assert target.parent == media.tmp_dir() and target.suffix == ".bin"
    assert list(media.tmp_dir().iterdir()) == []


def test_download_saved_elsewhere(tg, client, run_worker, ocr_stub, tmp_path):
    """Telethon сохранил файл не по указанному пути: в том же временном каталоге - принимается, вне его - отклоняется;
    и файл Telethon, и частичный файл по указанному пути удаляются."""
    from app import media

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Видео", video=mp4_bytes(512)), Msg(11, "Ещё видео", video=mp4_bytes(600)))
    outside = tmp_path / "elsewhere.mp4"
    plain = tg.download_media

    async def download_media(msg, file=None):
        await plain(msg, file)  # частичный файл по указанному пути остаётся
        target = Path(file).with_suffix(".mp4") if msg.id == 10 else outside
        target.write_bytes(msg.data)
        return str(target)

    tg.download_media = download_media
    configure(["nord_hills"])
    collect(run_worker)
    ok, = assets("1001:10")
    assert (ok.status, ok.mime) == ("downloaded", "video/mp4")
    bad, = assets("1001:11")
    assert (bad.status, bad.error) == ("failed", "Файл сохранён не во временный каталог — отклонён")
    assert not outside.exists() and list(media.tmp_dir().iterdir()) == []


def test_file_reference_expired_refetched_and_album_edit(tg, client, run_worker, ocr_stub):
    from telethon import errors

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост с фото", photo=png_bytes((8, 0, 0))),
               Msg(11, "Альбом", grouped_id=77, photo=png_bytes((8, 1, 0))),
               Msg(12, "", grouped_id=77, photo=png_bytes((8, 2, 0))))
    tg.download_fail[10] = errors.FileReferenceExpiredError(request=None)
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.media_saved) == ("succeeded", 3)
    assert ("get_messages", (1001, 10)) in tg.calls  # сообщение перечитано, загрузка повторена один раз
    assert [a.status for a in assets("1001:10")] == ["downloaded"]
    # правка подписи альбома: изменено одно сообщение, публикация - весь альбом с теми же файлами
    tg.add(1001, Msg(11, "Альбом: цены обновлены", grouped_id=77, photo=png_bytes((8, 1, 0)),
                     edit_date=T0 + timedelta(hours=2)))
    tg.calls.clear()
    run = collect(run_worker)
    assert (run.items_updated, run.items_new) == (1, 0) and "download_media" not in tg.names()
    it = item("1001:11")
    assert it.version == 2 and it.raw["messageIds"] == [11, 12]
    assert [a.source_key for a in assets("1001:11")] == [tg.key(1001, 11), tg.key(1001, 12)]


def test_foreign_item_not_overwritten(tg, client, run_worker):
    from app import ingest
    from app.integrations.base import Publication

    with SessionLocal() as db:
        ingest.ingest_publication(db, Publication(platform="telegram", external_id="1001:10", account="t.me/nord_hills",
                                                  url="https://t.me/nord_hills/10", text="Прислано через API",
                                                  collected_at=models.utcnow()), collector="intake", note="тест")
        db.commit()
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост с фото", photo=png_bytes((9, 0, 0))), Msg(11, "Другой пост"))
    configure(["nord_hills"])
    run = collect(run_worker)
    assert (run.status, run.items_new, run.items_unchanged) == ("succeeded", 1, 1)
    assert item("1001:10").collector == "intake" and "download_media" not in tg.names()


def test_make_client_offline():
    from app.integrations import telegram

    c = telegram.make_client("", API_ID, "0123456789abcdef0123456789abcdef")
    assert (c.flood_sleep_threshold, c.session.save()) == (30, "")
    # исчерпав повторы, Telethon бросает саму ошибку Telegram(ServerError…), а не ValueError "Request was unsuccessful"
    assert (c._raise_last_call_error, c._request_retries) == (True, 3)


def test_time_up_partial(tg, client, run_worker, monkeypatch):
    from app import jobs

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост"))
    configure(["nord_hills"])
    monkeypatch.setattr(jobs, "SOFT_DEADLINE_S", 0)
    run = collect(run_worker)
    assert run.status == "partial" and "Время запуска истекло — продолжим в следующий раз" in row().status_detail
    assert "iter_messages" not in tg.names()


# ---------- вход(telegram_login) ----------

def pending(fake: FakeTelegram) -> Path:
    return Path(f"{fake.path}.pending.json")


def test_login_two_step_and_password(tg, client, capsys, monkeypatch):
    from app.integrations import telegram_login as tl

    assert tl.main(["--send-code"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == ("Код отправлен (в приложение Telegram). Введите: python -m "
                               "app.integrations.telegram_login --code <код>")
    for secret in (PHONE, tg.code_hash, tg.saved):
        assert secret not in out.out + out.err
    p = pending(tg)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    data = json.loads(p.read_text())
    assert (data["session"], data["phoneCodeHash"], data["needsPassword"]) == (tg.saved, tg.code_hash, False)
    assert data["phoneFp"] == hashlib.sha256(PHONE.encode()).hexdigest()[:12] and PHONE not in p.read_text()
    assert tg.factory_args[-1] == ("", API_ID, API_HASH)

    tg.sign_in_errors = [__import__("telethon").errors.SessionPasswordNeededError(request=None)]
    assert tl.main(["--code", "12345"]) == 3
    assert "python -m app.integrations.telegram_login --password" in capsys.readouterr().err
    assert ("sign_in", (PHONE, "12345", None, tg.code_hash)) in tg.calls
    assert json.loads(p.read_text())["needsPassword"] is True and not tg.path.exists()

    monkeypatch.setattr(tl.getpass, "getpass", lambda prompt="": "секретный-пароль-2fa")
    assert tl.main(["--password"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == f"Вход выполнен: @saq_reader (id 777). Сессия сохранена в {tg.path}"
    assert tg.path.read_text() == tg.saved and not p.exists()
    assert stat.S_IMODE(tg.path.stat().st_mode) == 0o600 and stat.S_IMODE(tg.path.parent.stat().st_mode) == 0o700
    assert tg.factory_args[-1][0] == tg.saved  # пароль - в той же сессии, что и код


def test_login_code_errors_and_guards(tg, client, capsys, env):
    from telethon import errors

    from app.integrations import telegram_login as tl

    assert tl.main(["--code", "12345"]) == 1
    assert "Сначала запросите код: --send-code" in capsys.readouterr().err
    assert tl.main(["--send-code"]) == 0
    tg.sign_in_errors = [errors.PhoneCodeInvalidError(request=None)]
    assert tl.main(["--code", "11111"]) == 4
    assert "Неверный код — повторите --code" in capsys.readouterr().err and pending(tg).exists()
    tg.sign_in_errors = [errors.PhoneCodeExpiredError(request=None)]
    assert tl.main(["--code", "11111"]) == 5 and not pending(tg).exists()
    # ожидание кода старше 15 минут удаляется при любом запуске
    assert tl.main(["--send-code"]) == 0
    data = json.loads(pending(tg).read_text())
    data["sentAt"] = models.iso(models.utcnow() - timedelta(minutes=20))
    pending(tg).write_text(json.dumps(data))
    capsys.readouterr()
    assert tl.main(["--code", "12345"]) == 5
    assert "Код устарел — запросите новый: --send-code" in capsys.readouterr().err and not pending(tg).exists()
    # Telegram просит подождать
    tg.fail["send_code_request"] = errors.FloodWaitError(request=None, capture=300)
    assert tl.main(["--send-code"]) == 6
    assert "Telegram просит подождать 300 с" in capsys.readouterr().err
    # сбой серверов Telegram - "повторите позже", а не "отклонил запрос" и не трассировка
    tg.fail["send_code_request"] = errors.RpcCallFailError(request=None)
    assert tl.main(["--send-code"]) == 1
    assert "Telegram временно не отвечает (RpcCallFailError) — повторите позже" in capsys.readouterr().err
    tg.fail.clear()
    # код успешен
    assert tl.main(["--send-code"]) == 0 and tl.main(["--code", "12345"]) == 0
    assert tg.path.read_text() == tg.saved
    capsys.readouterr()
    # сессия уже есть - повторный вход только с --force
    assert tl.main(["--send-code"]) == 1
    assert "Сессия уже есть: @saq_reader. Чтобы войти заново — --force" in capsys.readouterr().err
    assert tl.main(["--send-code", "--force"]) == 0
    # без ключей - код 2, без сети
    env(TG_PHONE=None)
    calls = len(tg.calls)
    assert tl.main(["--send-code"]) == 2
    assert "Задайте TG_API_ID, TG_API_HASH и TG_PHONE в .env на сервере" in capsys.readouterr().err
    assert len(tg.calls) == calls


def test_login_stdin_modes(tg, client, capsys, monkeypatch):
    import io

    from telethon import errors

    from app.integrations import telegram_login as tl

    assert tl.main(["--send-code"]) == 0
    tg.sign_in_errors = [errors.SessionPasswordNeededError(request=None)]
    monkeypatch.setattr("sys.stdin", io.StringIO("24680\n"))
    assert tl.main(["--code", "-"]) == 3
    assert ("sign_in", (PHONE, "24680", None, tg.code_hash)) in tg.calls
    tg.sign_in_errors = [errors.PasswordHashInvalidError(request=None)]
    monkeypatch.setattr("sys.stdin", io.StringIO("неверный-пароль\n"))
    assert tl.main(["--password-stdin"]) == 4 and pending(tg).exists()
    monkeypatch.setattr("sys.stdin", io.StringIO("секретный-пароль-2fa\n"))
    assert tl.main(["--password-stdin"]) == 0 and tg.path.read_text() == tg.saved
    out = capsys.readouterr()
    assert "секретный-пароль-2fa" not in out.out + out.err and "Неверный пароль — повторите --password" in out.err


def test_login_status_logout_and_interactive(tg, client, capsys, monkeypatch, env, tmp_path):
    from telethon import errors

    from app.integrations import telegram_login as tl

    assert tl.main(["--status"]) == 1 and capsys.readouterr().out.strip() == "Сессии нет"
    assert tg.calls == []
    # каталога сессии нет - "Сессии нет" без создания каталогов
    env(TG_SESSION_PATH=str(tmp_path / "nope" / "deeper" / "telegram.session"))
    assert tl.main(["--status"]) == 1 and not (tmp_path / "nope").exists()
    env(TG_SESSION_PATH=str(tg.path))
    capsys.readouterr()
    # вход в одном процессе: код через input(), пароль через getpass
    tg.sign_in_errors = [errors.SessionPasswordNeededError(request=None)]
    monkeypatch.setattr("builtins.input", lambda prompt="": "54321")
    monkeypatch.setattr(tl.getpass, "getpass", lambda prompt="": "секретный-пароль-2fa")
    assert tl.main([]) == 0
    out = capsys.readouterr().out
    assert "Код отправлен (в приложение Telegram)." in out and "Вход выполнен: @saq_reader" in out
    assert ("sign_in", (None, None, "секретный-пароль-2fa", None)) in tg.calls
    assert tg.path.read_text() == tg.saved
    assert tl.main(["--status"]) == 0 and capsys.readouterr().out.strip() == "Сессия есть: @saq_reader"
    tg.authorized = False
    assert tl.main(["--status"]) == 1 and capsys.readouterr().out.strip() == "Сессия недействительна"
    tg.authorized = True
    assert tl.main(["--logout"]) == 0 and "log_out" in tg.names() and not tg.path.exists()


def test_login_corrupted_session_and_pending(tg, client, capsys, monkeypatch):
    """Испорченная сессия в файле: --status - "Сессия недействительна", новый вход без --force, --logout удаляет файл;
    файл ожидания кода без сессии или phone_code_hash - "сначала запросите код", а не трассировка."""
    from app.integrations import telegram, telegram_login as tl

    bad = "1garbage-not-a-session"

    def factory(session, api_id, api_hash):  # испорченную сессию разбирает настоящий make_client
        return telegram.make_client(session, api_id, api_hash) if session == bad else tg.factory(session, api_id,
                                                                                                 api_hash)

    monkeypatch.setattr(telegram, "CLIENT_FACTORY", factory)
    telegram.write_session(tg.path, bad)
    assert tl.main(["--status"]) == 1 and capsys.readouterr().out.strip() == "Сессия недействительна"
    assert tl.main(["--logout"]) == 0 and not tg.path.exists() and "log_out" not in tg.names()
    telegram.write_session(tg.path, bad)
    assert tl.main(["--send-code"]) == 0 and pending(tg).exists()
    # ожидание кода с испорченной сессией - понятная ошибка
    data = json.loads(pending(tg).read_text())
    telegram.write_private(pending(tg), json.dumps({**data, "session": bad}))
    capsys.readouterr()
    assert tl.main(["--code", "12345"]) == 1
    assert "Сессия Telegram недействительна — повторите вход" in capsys.readouterr().err
    # без phone_code_hash - файл ожидания удаляется
    telegram.write_private(pending(tg), json.dumps({k: v for k, v in data.items() if k != "phoneCodeHash"}))
    assert tl.main(["--code", "12345"]) == 1
    assert "Сначала запросите код: --send-code" in capsys.readouterr().err and not pending(tg).exists()


# ---------- приёмочные проверки подключения ----------

def test_acceptance_repeat_no_duplicate(tg, client, run_worker, accept, ocr_stub):
    login(tg)
    three(tg)
    configure(["nord_hills"])
    collect(run_worker)
    before = accept.snapshot()
    tg.calls.clear()
    run = collect(run_worker)
    assert accept.snapshot() == before and run.items_new == 0
    assert "download_media" not in tg.names()


def test_acceptance_failure_not_empty(tg, client, run_worker, accept):
    login(tg)
    tg.private.update({"closed_one", "closed_two"})
    configure(["closed_one", "closed_two"])
    run = collect(run_worker)
    c = accept.assert_failure_visible("telegram", run.id)
    assert c["status"] == "error" and c["statusDetail"].startswith("Ни один канал не доступен")
    # обрыв связи у работавшего подключения - тоже не "нет новых": сначала успешный сбор("работает")…
    tg.private.clear()
    tg.channel("open_one", 1001, Msg(10, "Пост"))
    configure(["open_one"])
    t0 = models.utcnow()
    run = collect(run_worker, now=t0)
    assert (run.status, connector(client)["status"]) == ("succeeded", "working")
    # …затем сеть недоступна: запуск не "успешно", задание ждёт повтора, подключение не "работает"
    tg.add(1001, Msg(11, "Новый пост"))
    tg.fail["iter_messages"] = ConnectionError("сеть недоступна")
    t1 = t0 + timedelta(hours=1)
    run = collect(run_worker, now=t1)
    out = client.get(f"/api/collection-runs/{run.id}").json()
    assert job_of(run.id).status == "queued" and out["status"] in ("queued", "running") and out["retryAt"]
    assert out["notes"][-1]["text"].startswith("Нет связи с Telegram (ConnectionError) — повтор 2 из 5")
    c = connector(client)
    assert c["status"] == "queued" and c["status"] != "working"
    # повторы исчерпаны - запуск "ошибка", подключение "ошибка" с причиной; новых записей нет
    for minutes in (5, 15, 60, 120):
        run_worker(now=t1 + timedelta(minutes=minutes))
    c = accept.assert_failure_visible("telegram", run.id)
    assert (c["status"], c["statusDetail"]) == ("error", "Нет связи с Telegram (ConnectionError)")
    assert client.get(f"/api/collection-runs/{run.id}").json()["status"] == "failed"
    assert [i.external_id for i in items()] == ["1001:10"]


def test_acceptance_restart_keeps_position(tg, client, run_worker, ocr_stub):
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост 1"), Msg(11, "Пост 2", photo=png_bytes((5, 5, 5))),
               Msg(12, "Пост 3"))
    tg.download_fail[11] = ConnectionError("обрыв связи")  # сбой после первой публикации
    configure(["nord_hills"])
    now = models.utcnow()
    run = collect(run_worker, now=now)
    assert [i.external_id for i in items()] == ["1001:10"] and channel_state("nord_hills")["lastId"] == 10
    assert job_of(run.id).status == "queued"
    tg.calls.clear()
    run_worker(now=now + timedelta(hours=1))  # повтор задания продолжает с сохранённой позиции
    assert ("iter_messages", (1001, 500, 10, True)) in tg.calls
    assert [i.external_id for i in items()] == ["1001:10", "1001:11", "1001:12"]
    assert channel_state("nord_hills")["lastId"] == 12
    assert [a.status for a in assets("1001:11")] == ["downloaded"]


def test_acceptance_media_survives_source(tg, client, run_worker, accept, monkeypatch, ocr_stub):
    from app.integrations import telegram

    login(tg)
    data = png_bytes((6, 6, 6))
    tg.channel("nord_hills", 1001, Msg(10, "Пост с фото", photo=data))
    configure(["nord_hills"])
    collect(run_worker)
    monkeypatch.setattr(telegram, "CLIENT_FACTORY", None)  # клиента больше нет - файл отдаёт хранилище
    tg.history.clear()
    asset, = assets("1001:10")
    assert accept.media_ok(asset.id) == data


def test_acceptance_not_confirmed_automatically(tg, client, run_worker, accept, ocr_stub):
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Старт продаж ЖК Ульба Ривер, бронь без первого взноса"))
    configure(["nord_hills"])
    collect(run_worker)
    ad_id = item("1001:10").ad_id
    accept.assert_not_confirmed(ad_id)
    r = client.post(f"/api/ads/{ad_id}/hypotheses/accept", json={"author": AUTHOR, "complexId": "c3"})
    assert r.status_code == 200, r.text
    assert (r.json()["complexId"], r.json()["linkConfirmedBy"]) == ("c3", AUTHOR)


def test_acceptance_history_kept(tg, client, run_worker):
    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Старт продаж ЖК Ульба Ривер"))
    configure(["nord_hills"])
    collect(run_worker)
    tg.add(1001, Msg(10, "Старт продаж ЖК Ульба Ривер — цены обновлены", edit_date=T0 + timedelta(days=1)))
    collect(run_worker)
    ad_id = item("1001:10").ad_id
    v = client.get(f"/api/ads/{ad_id}/versions").json()
    assert [x["text"] for x in v] == ["Старт продаж ЖК Ульба Ривер — цены обновлены", "Старт продаж ЖК Ульба Ривер"]


def test_acceptance_limit_no_endless_loop(tg, client, run_worker):
    from telethon import errors

    login(tg)
    tg.channel("nord_hills", 1001, Msg(10, "Пост 1"))
    tg.fail["iter_messages"] = errors.FloodWaitError(request=None, capture=600)
    configure(["nord_hills"], enabled=True)
    now = models.utcnow()
    run = collect(run_worker, now=now)
    assert run.status == "rate_limited"
    assert tg.names()[-2:] == ["iter_messages", "disconnect"]  # после FloodWait запросов нет
    tg.calls.clear()
    tg.fail.clear()
    for minutes in (1, 5, 9):  # такты воркера до next_run_at - без обращений к Telegram
        run_worker(now=now + timedelta(minutes=minutes))
    assert tg.calls == []
    run_worker(now=now + timedelta(minutes=11))  # пауза кончилась - сбор по расписанию
    assert "iter_messages" in tg.names() and [i.external_id for i in items()] == ["1001:10"]


def test_flood_wait_seconds_bounded():
    """FloodWait - пауза 1 с…сутки(как http.retry_after): огромное значение Telegram не откладывает сбор на годы."""
    from telethon import errors

    from app.integrations import telegram
    from app.integrations.base import RateLimited

    got = {secs: telegram.map_error(errors.FloodWaitError(request=None, capture=secs)) for secs in (0, 1, 600, 10**9)}
    assert all(isinstance(e, RateLimited) for e in got.values())
    assert {secs: e.retry_after for secs, e in got.items()} == {0: 3600, 1: 1, 600: 600, 10**9: 86400}
    assert "подождать 86400 с" in got[10**9].detail
