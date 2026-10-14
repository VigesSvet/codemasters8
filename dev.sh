#!/usr/bin/env bash
# Локальный запуск без Docker: API на :8000 (SQLite) + фронт на :5180
set -e
cd "$(dirname "$0")"
if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt
fi
[ -d frontend/node_modules ] || (cd frontend && npm install)
(cd backend && DATABASE_URL=${DATABASE_URL:-sqlite:///./saq.db} .venv/bin/uvicorn app.main:app --reload --port 8000) &
API=$!
trap 'kill $API' EXIT
cd frontend && npx vite --port 5180
