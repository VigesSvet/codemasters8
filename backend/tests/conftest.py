import atexit
import os
import shutil
import tempfile

# Временная SQLite — до импорта app (engine создаётся при импорте app.db)
_TMP = tempfile.mkdtemp(prefix="saq-test-")
# SAQ_TEST_DATABASE_URL — прогон на другой БД (например, PostgreSQL из docker compose; её таблицы приложения пересоздаются)
os.environ["DATABASE_URL"] = os.getenv("SAQ_TEST_DATABASE_URL") or f"sqlite:///{_TMP}/test.db"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

import pytest  # noqa: E402


@pytest.fixture
def client():
    """Клиент API на чистой пересеянной БД — у каждого теста своя."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.seed import reset

    reset()
    with TestClient(app) as c:
        yield c
