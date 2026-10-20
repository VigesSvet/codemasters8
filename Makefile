# SAQ Qurylys - рутинные команды проекта: установка, запуск, проверки, Docker, стартовый набор.
# make или make help - список целей. CI(.github/workflows/ci.yml) вызывает эти же цели: make check локально
# проверяет то же, что CI(кроме аудита зависимостей - make audit, ему нужна сеть).
#
# Нужны GNU make 4+, bash, Python 3.12 и Docker(для up/down/logs/ps). Node: если npm нет в PATH, цели фронтенда
# выполняются в контейнере $(NODE_IMAGE) с каталогами frontend/ и shared/ и с правами текущего пользователя
# (NODE_DOCKER=1 - всегда в контейнере, NODE_DOCKER=0 - всегда локальный npm).
# Переменные переопределяются в командной строке: make test-backend PYTEST_ARGS="-q -x -k signal".

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
.DELETE_ON_ERROR:
MAKEFLAGS += --no-builtin-rules --no-print-directory

# ---------- переменные ----------

# интерпретатор для создания окружения бэкенда(Python 3.12+) и окружение бэкенда(то же, что у ./dev.sh)
PYTHON ?= python3
VENV ?= backend/.venv
VPY := $(abspath $(VENV))/bin/python
RUFF ?= $(VPY) -m ruff
PYTEST_ARGS ?= -q
NPM ?= npm
NPX ?= npx
# образ для целей фронтенда без локального Node; vitest 5 требует Node ^22.12 || ^24 || >=26
NODE_IMAGE ?= node:22-slim
NODE_DOCKER ?= $(if $(shell command -v $(NPM) 2>/dev/null),0,1)
COMPOSE ?= docker compose
# make logs SERVICE=api - журнал одного сервиса
SERVICE ?=
# БД для snapshot-export: по умолчанию SQLite ./dev.sh(путь - от backend/)
DATABASE_URL ?= sqlite:///./saq.db
SNAPSHOT_DIR ?= $(CURDIR)/shared/snapshot
# pip-audit ставится в отдельное окружение: он и его зависимости не попадают в проверяемый список
PIP_AUDIT ?= pip-audit==2.10.1
AUDIT_VENV ?= $(or $(XDG_CACHE_HOME),$(HOME)/.cache)/saq-pip-audit

ifeq ($(NODE_DOCKER),1)
NODE_RUN = docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -e npm_config_update_notifier=false \
	-v "$(CURDIR)/frontend:/app:z" -v "$(CURDIR)/shared:/shared:ro,z" -w /app $(NODE_IMAGE)
else
NODE_RUN = cd frontend &&
endif

# первая строка рецепта целей бэкенда: без окружения - понятная ошибка, а не "No such file"
need-venv = @test -x "$(VPY)" || { echo "Нет окружения бэкенда $(VENV): сначала make setup-backend" >&2; exit 1; }

.PHONY: help setup setup-backend setup-frontend dev up down logs ps \
	check lint lint-backend lint-frontend typecheck test test-backend test-frontend e2e build \
	audit audit-backend audit-frontend snapshot-export snapshot-export-docker snapshot-views golden clean

##@ Справка

help: ## список целей
	@awk 'BEGIN { FS = ":.*## " } /^##@ / { printf "\n%s\n", substr($$0, 5); next } \
		/^[a-zA-Z0-9_-]+:.*## / { printf "  %-24s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo
	@echo "Node: $(if $(filter 1,$(NODE_DOCKER)),в контейнере $(NODE_IMAGE) - npm нет в PATH,локальный $(NPM))"

##@ Установка и запуск

setup: setup-backend setup-frontend ## зависимости бэкенда и фронтенда

setup-backend: ## окружение backend/.venv и пакеты из requirements.txt и requirements-dev.txt
	@$(PYTHON) -c 'import sys; sys.exit(sys.version_info[:2] < (3, 12))' \
		|| { echo "Нужен Python 3.12+: make setup-backend PYTHON=python3.12" >&2; exit 1; }
	test -x "$(VPY)" || $(PYTHON) -m venv "$(VENV)"
	$(VPY) -m pip install --disable-pip-version-check -r backend/requirements.txt -r backend/requirements-dev.txt
	touch "$(VENV)/.requirements-installed"  # метка ./dev.sh: зависимости поставлены

setup-frontend: ## пакеты фронтенда строго по package-lock.json(npm ci)
	$(NODE_RUN) $(NPM) ci --no-audit --no-fund

dev: ## локальный запуск без Docker(./dev.sh): API :8000 на SQLite, воркер, интерфейс :5180
	@command -v $(NPX) >/dev/null || { echo "./dev.sh запускает Vite локально - нужен Node 22.12+; без него - make up" >&2; exit 1; }
	./dev.sh

up: ## собрать образы и запустить стек Docker в фоне: db, api, worker, web(http://localhost:5180)
	$(COMPOSE) up -d --build
	@echo "Интерфейс - http://localhost:5180, журнал - make logs, остановка - make down"

down: ## остановить стек Docker; тома с данными остаются
	$(COMPOSE) down

logs: ## журнал сервисов Docker(SERVICE=api - одного сервиса)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

ps: ## состояние сервисов Docker
	$(COMPOSE) ps

##@ Проверки(CI)

check: lint typecheck test build ## всё, что проверяет CI, кроме аудита: линтеры, типы, тесты, сборка

lint: lint-backend lint-frontend ## линтеры бэкенда и фронтенда

lint-backend: ## ruff по backend/app и backend/tests(правила - backend/pyproject.toml)
	$(need-venv)
	cd backend && $(RUFF) check app tests

lint-frontend: ## oxlint по frontend/src: падает только на ошибках, предупреждения - в выводе
	$(NODE_RUN) $(NPX) oxlint src

typecheck: ## проверка типов фронтенда(tsc -b)
	$(NODE_RUN) $(NPX) tsc -b

test: test-backend test-frontend ## все автотесты

test-backend: ## pytest бэкенда на временной SQLite, без сети(PYTEST_ARGS - аргументы pytest)
	$(need-venv)
	cd backend && $(VPY) -m pytest $(PYTEST_ARGS)

test-frontend: ## vitest фронтенда
	$(NODE_RUN) $(NPM) test

e2e: ## сквозной сценарий README на стартовом наборе через API(backend/tests/test_scenario.py)
	$(need-venv)
	cd backend && $(VPY) -m pytest $(PYTEST_ARGS) -m scenario tests/test_scenario.py

build: ## сборка фронтенда(vite build) в frontend/dist
	$(NODE_RUN) $(NPX) vite build

audit: audit-backend audit-frontend ## известные уязвимости зависимостей(нужна сеть)

audit-backend: ## pip-audit ровно тех версий, что стоят в окружении бэкенда
	$(need-venv)
	test -x "$(AUDIT_VENV)/bin/python" || $(PYTHON) -m venv "$(AUDIT_VENV)"
	"$(AUDIT_VENV)/bin/python" -m pip install --quiet --disable-pip-version-check "$(PIP_AUDIT)"
	$(VPY) -m pip freeze --exclude-editable --disable-pip-version-check \
		| "$(AUDIT_VENV)/bin/pip-audit" -r /dev/stdin --no-deps --disable-pip --progress-spinner off

audit-frontend: ## npm audit зависимостей, которые попадают в сборку(--omit=dev)
	$(NODE_RUN) $(NPM) audit --omit=dev

##@ Данные

snapshot-export: ## выгрузить стартовый набор из DATABASE_URL(по умолчанию SQLite ./dev.sh) в SNAPSHOT_DIR
	$(need-venv)
	cd backend && DATABASE_URL='$(DATABASE_URL)' $(VPY) -m app.snapshot export --out "$(SNAPSHOT_DIR)"

snapshot-export-docker: ## то же из PostgreSQL и тома media стека Docker в shared/snapshot(стек запущен: make up)
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/backend:/repo/backend:z" -v "$(CURDIR)/shared:/repo/shared:z" \
		-e SAQ_SHARED_DIR=/repo/shared -e PYTHONDONTWRITEBYTECODE=1 -w /repo/backend --user "$$(id -u):$$(id -g)" \
		api python -m app.snapshot export --out /repo/shared/snapshot

snapshot-views: ## пересчитать ответы API офлайн-копии(SNAPSHOT_DIR/api) по строкам набора, без выгрузки из БД
	$(need-venv)
	cd backend && $(VPY) -c 'import sys; from datetime import date; from pathlib import Path; \
		from app import snapshot as s; d = Path(sys.argv[1]); \
		s.build_views_subprocess(d, date.fromisoformat(s.manifest(d)["api"]["asOf"]))' "$(SNAPSHOT_DIR)"

golden: ## эталон модели риска shared/testdata/risk_golden.json после правки модели или тестовых данных
	$(need-venv)
	cd backend && $(VPY) -m app.golden

##@ Обслуживание

clean: ## удалить кэши и результаты сборки; данные, .env, окружения, node_modules и тома Docker не трогает
	rm -rf frontend/dist frontend/node_modules/.tmp backend/.ruff_cache backend/.pytest_cache .pytest_cache .vite
	find backend/app backend/tests -type d -name __pycache__ -prune -exec rm -rf {} +
