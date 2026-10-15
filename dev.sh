#!/usr/bin/env bash
# Локальный запуск без Docker: API на :8000 (SQLite) + фронт на :5180
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
(cd backend && DATABASE_URL=${DATABASE_URL:-sqlite:///./saq.db} .venv/bin/uvicorn app.main:app --reload --port 8000) &
API=$!
trap 'kill $API' EXIT
cd frontend && npx vite --port 5180
