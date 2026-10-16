#!/usr/bin/env bash
# Локальный запуск без Docker: API на :8000 (SQLite) + воркер сбора + фронт на :5180. SAQ_WORKER=0 — без воркера
set -e
cd "$(dirname "$0")"
[ -d backend/.venv ] || python3 -m venv backend/.venv
# Зависимости ставятся заново, если requirements новее последней установки (например, добавился reportlab для PDF)
STAMP=backend/.venv/.requirements-installed
if [ ! -f "$STAMP" ] || [ backend/requirements.txt -nt "$STAMP" ] || [ backend/requirements-dev.txt -nt "$STAMP" ]; then
  backend/.venv/bin/pip install -q -r backend/requirements.txt -r backend/requirements-dev.txt
  touch "$STAMP"
fi
[ -d frontend/node_modules ] || (cd frontend && npm install)
DB=${DATABASE_URL:-sqlite:///./saq.db}
# Файлы публикаций и сессия Telegram — вне Git (backend/data/media, backend/data/private; другие пути — MEDIA_ROOT и
# TG_SESSION_PATH в .env). Секреты приложение читает из .env само: в окружение оболочки .env не загружается
(cd backend && DATABASE_URL=$DB exec .venv/bin/uvicorn app.main:app --reload --port 8000) &
API=$!
WORKER=
if [ "${SAQ_WORKER:-1}" != 0 ]; then
  # воркер стартует после API: схему БД создаёт и обновляет только API (init_db)
  (cd backend && until .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=1)" 2>/dev/null; do sleep 0.5; done
   DATABASE_URL=$DB SAQ_WORKER_ID=${SAQ_WORKER_ID:-dev-worker} exec .venv/bin/python -m app.worker) &
  WORKER=$!
fi
trap 'kill $API $WORKER 2>/dev/null' EXIT
cd frontend && npx vite --port 5180
