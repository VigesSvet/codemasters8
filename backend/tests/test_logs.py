"""Журнал доступа uvicorn без chat_id жителей(logs.AccessPathFilter): пути подписок и заявок бота."""
import io
import logging

import pytest
from uvicorn.logging import AccessFormatter
from uvicorn.protocols.utils import get_path_with_query_string

from app import logs

CHAT = "987654321"


@pytest.fixture
def access_log():
    """Строки журнала доступа так, как их пишет uvicorn: его формат записи и AccessFormatter."""
    logs.install(handler=False)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    lg = logging.getLogger("uvicorn.access")
    level = lg.level
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)

    def request(method: str, path: str, query: bytes = b"", status: int = 200) -> str:
        stream.seek(0)
        stream.truncate()
        full = get_path_with_query_string({"path": path, "query_string": query})
        lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", method, full, "1.1", status)
        return stream.getvalue().strip()

    yield request
    lg.removeHandler(handler)
    lg.setLevel(level)


@pytest.mark.parametrize("method", ["PUT", "GET", "DELETE"])
def test_subscription_chat_id_hidden(access_log, method):
    line = access_log(method, f"/api/intake/telegram/subscriptions/{CHAT}", status=204)
    assert line == f'127.0.0.1:50000 - "{method} /api/intake/telegram/subscriptions/*** HTTP/1.1" 204 No Content'


def test_submission_external_id_hidden_query_kept(access_log):
    """externalId заявки бывает "{chat_id}:{message_id}"(uvicorn выводит ":" как %3A); запрос после пути остаётся."""
    line = access_log("GET", f"/api/intake/telegram/submissions/{CHAT}:42", b"lang=kk")
    assert CHAT not in line and "42" not in line
    assert '"GET /api/intake/telegram/submissions/***?lang=kk HTTP/1.1"' in line
    # отрицательный chat_id группы и регистр пути - тоже
    line = access_log("GET", f"/API/Intake/Telegram/Subscriptions/-100{CHAT}")
    assert CHAT not in line and "/Subscriptions/***" in line


def test_other_paths_untouched(access_log):
    for path in ("/api/intake/telegram/districts", f"/api/complexes/{CHAT}", "/api/intake/telegram/subscriptions/"):
        assert f'"GET {path} HTTP/1.1"' in access_log("GET", path)
    assert '"POST /api/intake/telegram HTTP/1.1"' in access_log("POST", "/api/intake/telegram")


def test_filter_installed_once_and_keeps_record():
    logs.install(handler=False, force=True)
    logs.install(handler=False, force=True)
    filters = [f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, logs.AccessPathFilter)]
    assert len(filters) == 1
    # чужая запись логгера(другой формат args) проходит без изменений
    for args in ((), ("x",), ("a", "b", 3), ({"k": "v"},)):
        rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "msg", args, None)
        before = rec.args
        assert filters[0].filter(rec) is True and rec.args == before
