# Развёртывание и сопровождение

Запуск SAQ Qurylys в Docker и без него, сетевой доступ, переменные окружения, обновление версии и схемы БД, резервные копии, воркер, служебные команды и типовые сбои. Документ для оператора сервера и для того, кто проверяет чистый запуск по README. Основной способ вызова команд - цели корневого `Makefile`, рядом один раз приведена прямая команда.

## Требования

| Что | Для чего | Примечания |
|---|---|---|
| Docker Engine и Docker Compose v2 | `make up` | `env_file` с `format: raw` и `healthcheck.start_interval` требуют свежего выпуска(Engine 25+, Compose 2.30+) |
| Свободные порты 5180, 8000, 5432 | `web`, `api`, `db` | 8000 и 5432 - только на 127.0.0.1, 5180 - на всех адресах IPv4 |
| Интернет | сборка образа, `npm ci` при старте `web`, подложка карты | ключи внешних сервисов не нужны |
| Python 3.12+ | запуск без Docker, тесты | образ API - `python:3.12-slim-trixie` |
| Node 22.12+ или 24 | `./dev.sh`, цели фронтенда | vitest 5 требует `^22.12 \|\| ^24 \|\| >=26`; `web` - `node:22-alpine`, CI - Node 24; без Node цели `make` идут в контейнере `node:22-slim` |
| GNU make 4+, bash | `Makefile` | |
| Tesseract с `rus` и `kaz`, ffmpeg, `prlimit`(util-linux) | OCR и видео без Docker | в образе API есть; без Tesseract OCR отвечает `engine: none`, без `prlimit` ffmpeg идёт без ограничений ресурсов |

Место на диске: стартовый набор около 34 МБ, образ API, файлы публикаций(видео до `MEDIA_MAX_VIDEO_MB` каждое), модели речи(`base` около 145 МБ) и запас `MEDIA_MIN_FREE_MB`.

## Makefile

`make` или `make help` печатает цели по разделам и режим Node: локальный `npm` или контейнер. Переменные задаются в командной строке: `make test-backend PYTEST_ARGS="-q -x -k signal"`.

| Цель | Что делает | Прямая команда |
|---|---|---|
| `setup` | `setup-backend` и `setup-frontend` | - |
| `setup-backend` | проверяет Python 3.12+, создаёт `backend/.venv`, ставит `requirements*.txt` и отметку `.requirements-installed` для `./dev.sh` | `python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt` |
| `setup-frontend` | пакеты строго по `package-lock.json` | `cd frontend && npm ci --no-audit --no-fund` |
| `dev` | запуск без Docker; без `npx` подсказывает `make up` | `./dev.sh` |
| `up` / `down` | собрать и запустить стек в фоне / остановить, тома остаются | `docker compose up -d --build` / `docker compose down` |
| `logs` | журнал, `SERVICE=api` - одного сервиса | `docker compose logs -f --tail=200 [сервис]` |
| `ps` | состояние сервисов | `docker compose ps` |
| `check` | `lint`, `typecheck`, `test`, `build` - всё, что проверяет CI, кроме аудита | - |
| `lint-backend`, `lint-frontend`(`lint`) | ruff по `app` и `tests`; oxlint по `src`, падает только на ошибках | `cd backend && .venv/bin/python -m ruff check app tests`; `cd frontend && npx oxlint src` |
| `typecheck` | типы фронтенда | `cd frontend && npx tsc -b` |
| `test-backend`, `test-frontend`(`test`) | pytest на временной SQLite без сети; vitest | `cd backend && .venv/bin/python -m pytest -q`; `cd frontend && npm test` |
| `e2e` | сквозной сценарий README на стартовом наборе | `cd backend && .venv/bin/python -m pytest -q -m scenario tests/test_scenario.py` |
| `build` | сборка в `frontend/dist` | `cd frontend && npx vite build` |
| `audit-backend`, `audit-frontend`(`audit`) | известные уязвимости, нужна сеть: `pip-audit` установленных версий из отдельного `AUDIT_VENV`; `npm audit --omit=dev` | `cd frontend && npm audit --omit=dev` |
| `snapshot-export` | выгрузка стартового набора из `DATABASE_URL` в `SNAPSHOT_DIR` | `cd backend && DATABASE_URL=sqlite:///./saq.db .venv/bin/python -m app.snapshot export` |
| `snapshot-export-docker` | то же из PostgreSQL и тома `media` работающего стека | [Служебные команды](#служебные-команды) |
| `snapshot-views` | пересчёт ответов API офлайн-копии `SNAPSHOT_DIR/api` без выгрузки из БД | - |
| `golden` | эталон модели риска `shared/testdata/risk_golden.json` | `cd backend && .venv/bin/python -m app.golden` |
| `clean` | кэши и `frontend/dist`; данные, `.env`, окружения, `node_modules` и тома не трогает | - |

Переменные `Makefile`: `PYTHON`(`python3`) - интерпретатор для окружения; `VENV`(`backend/.venv`, общее с `./dev.sh`); `PYTEST_ARGS`(`-q`); `NODE_DOCKER` - `1`, если `npm` нет в PATH: цели фронтенда в контейнере `NODE_IMAGE`(`node:22-slim`) от текущего пользователя, `0` - локальный `npm`; `SERVICE` - для `logs`; `DATABASE_URL`(`sqlite:///./saq.db` от `backend/`) и `SNAPSHOT_DIR`(`shared/snapshot`) - для целей набора. Проверки и CI подробно - [DEVELOPMENT.md, "Команды разработки"](DEVELOPMENT.md#команды-разработки).

## Docker Compose

```bash
cp .env.example .env    # по желанию: без .env всё работает, подключения "не настроены"
make up                 # интерфейс http://localhost:5180, OpenAPI http://127.0.0.1:8000/docs
```

| Сервис | Образ и команда | Порт | Сети и тома | Особенности |
|---|---|---|---|---|
| `db` | `postgres:16-alpine` | `127.0.0.1:5432` | `backend`; `pgdata` | healthcheck `pg_isready` |
| `api` | сборка `backend/Dockerfile` из корня репозитория; `uvicorn app.main:app --host 0.0.0.0 --port 8000` | `127.0.0.1:8000` | `backend`, `frontend`; `media` | после готовности `db`; healthcheck `GET /api/health` |
| `worker` | тот же образ, `python -m app.worker` | нет | `backend`; `media`, `tgsession`, `asrmodels` | после готовности `api`; `stop_grace_period: 120s` |
| `web` | `node:22-alpine`, `npm ci && npx vite --host 0.0.0.0 --port 5180` | `0.0.0.0:5180` | `frontend`; `./frontend`, `./shared`(только чтение), анонимный том `/app/node_modules` | dev-сервер Vite, `/api` проксируется на `api:8000` |

Общее у `api` и `worker`: `.env` из корня через `env_file`(необязателен; `format: raw` - значения буквально, без подстановок `$`), `init: true`(tini передаёт SIGTERM и собирает осиротевшие процессы), `restart: unless-stopped`, `no-new-privileges`, `cap_drop: [ALL]`, пользователь `10001:10001`. Журналы всех сервисов ротируются: 3 файла по 10 МБ. Сеть `backend` закрывает БД от контейнера `web`.

**Что задаёт compose.** Блок `environment` важнее `env_file`, поэтому из `.env` в Docker не действуют: `DATABASE_URL`(`postgresql+psycopg://saq:<SAQ_DB_PASSWORD>@db:5432/saq`), `MEDIA_ROOT`(`/data/media`), `TG_SESSION_PATH`(`/data/private/telegram.session`), `SAQ_ENV_FILE`(пусто: `.env` в контейнере не ищется), у воркера `SAQ_ASR_MODEL_DIR`(`/data/models`) и `SAQ_WORKER_ID`(`worker-1`), у `web` - `API_PROXY`(`http://api:8000`). Переменные, которые приложение читает только из окружения процесса(`TESSERACT_CMD`, `SAQ_OCR_TIMEOUT`), в Docker действуют и из `.env`: `env_file` кладёт его в окружение.

**Пароль БД.** `SAQ_DB_PASSWORD` подставляет только compose - в `POSTGRES_PASSWORD` и `DATABASE_URL`. Только латинские буквы и цифры: пароль входит в адрес БД; не задан - `saq`. Действует при создании тома `pgdata`; у существующей базы:

```bash
docker compose exec db psql -U saq -c "ALTER USER saq PASSWORD '<новый пароль>'"
# затем новый пароль - в SAQ_DB_PASSWORD в .env и docker compose up -d
```

**Тома.** `pgdata` - база, `media` - файлы публикаций, `tgsession` - сессия Telegram(только `worker`), `asrmodels` - модели речи(только `worker`, пересборка образа их не теряет). `docker compose down -v` удаляет все четыре.

**Образ API.** Белый список `.dockerignore` пропускает в контекст только `backend/requirements.txt`, `backend/app` и `shared/` без `shared/testdata`, `.env`, баз и сессий. В образе Tesseract с `rus` и `kaz`, ffmpeg, util-linux, байт-код. Код принадлежит root, `/data/*` - пользователю `saq`(10001), `/data/private` - 0700.

**Первый старт.** API создаёт схему, пустая БД получает стартовый набор(строка журнала API `Пустая БД: загружен стартовый набор от …`), воркер ждёт схему. После правки `.env` - `docker compose up -d api worker`.

`web` - dev-сервер Vite. Раздача `frontend/dist` общим веб-сервером в репозиторий не входит, и у неё нет маршрута картинок офлайн-копии `/snapshot-media/…`.

## Локальный запуск

```bash
make dev                 # ./dev.sh: API :8000 на SQLite, воркер, интерфейс :5180
SAQ_WORKER=0 make dev    # без воркера
```

`./dev.sh` создаёт `backend/.venv` и ставит `requirements*.txt`, если они новее отметки `.requirements-installed`; выполняет `npm install`, если нет `frontend/node_modules`; запускает uvicorn на 127.0.0.1:8000 с `--reload` и `DATABASE_URL=sqlite:///./saq.db`(файл `backend/saq.db`), если `DATABASE_URL` не задан; после ответа `/api/health` запускает воркер(`SAQ_WORKER_ID` по умолчанию `dev-worker`); запускает Vite на localhost:5180 и при выходе останавливает все процессы.

Приложение само читает `.env` из корня(или `SAQ_ENV_FILE`), в окружение оболочки он не загружается. Файлы, сессия Telegram и модели речи - в `backend/data/`(вне Git).

Вручную, в трёх терминалах:

```bash
cd backend && DATABASE_URL=sqlite:///./saq.db .venv/bin/uvicorn app.main:app --reload --port 8000
cd backend && DATABASE_URL=sqlite:///./saq.db .venv/bin/python -m app.worker     # после старта API
cd frontend && npx vite --port 5180                                               # API_PROXY=http://… - другой адрес API
```

Без `DATABASE_URL` приложение подключается к PostgreSQL `localhost:5432` с пользователем и паролем `saq` - это порт `db` запущенного стека compose с паролем по умолчанию.

Интерфейс на другом домене, чем API: сборка с абсолютным `VITE_API_URL`(в `frontend/.env`, образец - `frontend/.env.example`), адрес интерфейса - в `SAQ_CORS_ORIGINS`, имя хоста API - в `SAQ_WEB_HOSTS`. Пустое `VITE_API_URL=` - пустой адрес, а не "не задано": строку раскомментируют только со значением.

## Сетевой доступ

| Точка | Docker | `./dev.sh` |
|---|---|---|
| БД | `127.0.0.1:5432` хоста | файл `backend/saq.db` |
| API | `127.0.0.1:8000` хоста; в контейнере uvicorn слушает `0.0.0.0:8000`, `web` ходит на `api:8000` по сети `frontend` | `127.0.0.1:8000` |
| Интерфейс и прокси `/api` | `0.0.0.0:5180` с фильтром клиентов | localhost:5180 |

**Кто открывает интерфейс в Docker.** `web` публикует 5180 на всех адресах IPv4, но принимает соединения только с этой машины(loopback и шлюз сети контейнера: обращения хоста к 127.0.0.1:5180 docker-proxy пересылает от адреса шлюза) и с адресов из `SAQ_ALLOWED_CLIENTS`(IP или подсети через запятую, обычно устройства Tailscale). Остальные соединения Vite рвёт до HTTP с записью `Соединение с <IP> отклонено` в журнале `web`; значение не вида IP или подсети не даёт dev-серверу стартовать. Фильтр - в `frontend/vite.config.ts`, а не в межсетевом экране: опубликованный порт Docker ufw и firewalld обычно не закрывают.

**Только IPv4.** При `5180:5180` Docker слушает и `[::]`, а соединения по IPv6 docker-proxy пересылает от адреса шлюза - фильтр принял бы их за эту машину.

**Имена сервера.** `SAQ_WEB_HOSTS` - имена интерфейса кроме localhost и IP(например имя машины в Tailscale MagicDNS) через запятую, `.домен` - с поддоменами. Запрос с другим именем в `Host` отклоняют и Vite(403), и API(400). API принимает ещё `*.localhost`, имя сервиса `api` и хост из `OLX_REDIRECT_URI`. Имя вышестоящего сервера, которое обратный прокси подставляет в `Host`, - тоже в `SAQ_WEB_HOSTS`. При `./dev.sh` Vite берёт `SAQ_ALLOWED_CLIENTS`, `SAQ_WEB_HOSTS` и `API_PROXY` только из окружения процесса, API читает `SAQ_WEB_HOSTS` и из `.env`.

**Ограничения.** Где Docker подменяет адрес клиента адресом шлюза(обычно rootless Docker, Podman без root, Docker Desktop), фильтр пропускает всех - там 5180 публикуют только на 127.0.0.1(`"127.0.0.1:5180:5180"` в `docker-compose.yml`). Пропущенным клиентам без входа доступны все маршруты, кроме требующих токена(администратор, приём публикаций, бот заявок); TLS у порта 5180 нет, и токен администратора, введённый на странице, идёт по HTTP(в Tailscale канал шифрует сама сеть). Обоснование и принятые риски - [SECURITY.md, "Сетевой периметр"](SECURITY.md#сетевой-периметр).

После правки: `SAQ_ALLOWED_CLIENTS` - `docker compose up -d web`, `SAQ_WEB_HOSTS` - `docker compose up -d web api`, `SAQ_CORS_ORIGINS` - `docker compose up -d api`.

## Переменные окружения

Единственная полная таблица переменных в документации; образцы - `.env.example` в корне и `frontend/.env.example`.

- Порядок: непустое значение окружения процесса, затем непустое из `.env`, затем значение по умолчанию; пустое - то же, что не задано.
- Значения в `.env` - без кавычек и буквально, `$` экранировать не нужно; в Docker кавычки стали бы частью значения.
- Неверное значение или выход за границы - значение по умолчанию и предупреждение в журнале с именем переменной, без значения. Неверные `OLX_REDIRECT_URI`, `DGIS_QUOTA_PERIOD_START`, `TG_API_ID` не используются, неверные элементы списков пропускаются.
- Токены доступа - не короче 24 символов и не меньше 12 разных; токен администратора не совпадает с двумя другими; слабый или совпадающий считается не заданным. Генерация: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, по значению на токен.
- Логические: `1/0`, `true/false`, `yes/no`, `on/off`; другое - выключено с предупреждением.
- Настройки читаются при старте процесса: после правки - перезапуск API и воркера. Интерфейс вместо имён переменных показывает названия из `shared/env_labels.json`. `SAQ_DEMO_DATA` не читается, заданное значение даёт предупреждение.

**Переменные приложения** - их читают API и воркер из окружения и `.env`(`app.settings`). Секреты отмечены `*`.

| Переменная | По умолчанию | Значения | Назначение |
|---|---|---|---|
| `SAQ_ADMIN_TOKEN`* | | токен | администратор подключений; без него маршруты администратора - 503 |
| `INTEGRATION_INGEST_TOKEN`* | | токен | внешние сборщики, `POST /api/intake/publications` |
| `SERVICE_API_TOKEN`* | | токен | приёмник Telegram-бота заявок |
| `SAQ_WEB_HOSTS` | | имена, `.домен` | имена интерфейса кроме localhost и IP; читает и Vite |
| `SAQ_CORS_ORIGINS` | CORS выключен | `http(s)://хост[:порт]` без пути через запятую, `*` | адреса интерфейса на другом домене |
| `MEDIA_ROOT` | `backend/data/media` | путь | хранилище файлов |
| `TG_SESSION_PATH`* | `backend/data/private/telegram.session` | путь | сессия Telegram |
| `SAQ_ASR_MODEL_DIR` | `backend/data/models` | путь | модели речи |
| `MEDIA_MAX_IMAGE_MB` | 10 | 1-10 | предел изображения, МБ(больше 10 - 10, предел OCR) |
| `MEDIA_MAX_VIDEO_MB`, `MEDIA_MIN_FREE_MB` | 200, 1024 | ≥ 1, ≥ 0 | предел видео и запас свободного места, МБ |
| `META_APP_ID`, `META_APP_SECRET`* | | | приложение Meta |
| `META_USER_ACCESS_TOKEN`* | | | токен Graph API |
| `META_GRAPH_VERSION` | `v26.0` | | версия Graph API |
| `META_BUSINESS_ID`, `META_PAGE_ID`, `META_IG_USER_ID` | | | бизнес-портфолио, Страница, аккаунт Instagram |
| `IG_PAGE_SIZE`, `IG_FIRST_LOAD_MAX` | 25, 50 | ≥ 1 | публикаций на запрос и при первой загрузке |
| `TG_API_ID`, `TG_API_HASH`* | | целое ≥ 1, строка | приложение Telegram |
| `TG_PHONE`* | | | телефон аккаунта, только для входа |
| `TG_HISTORY_LIMIT`, `TG_EDIT_WINDOW` | 50, 30 | ≥ 1, ≥ 0 | сообщений канала за запуск, окно правок |
| `TG_MAX_VIDEO_MB`, `TG_DOWNLOAD_TIMEOUT_S` | 50, 300 | ≥ 0, ≥ 1 | предел видео, МБ; срок загрузки, с |
| `BOT_TOKEN`* | | `<число>:<ключ>` | бот заявок, отправка предупреждений жителям |
| `TG_ALERTS_CHANNEL` | | `@имя`, `-100…` | канал ГАСК для предупреждений |
| `APIFY_TOKEN`* | | | Apify(TikTok) |
| `APIFY_ACTOR_ID` | `clockworks~tiktok-scraper` | | Actor |
| `APIFY_MAX_TOTAL_CHARGE_USD`, `APIFY_DAILY_BUDGET_USD` | 0.5, 1.0 | ≥ 0 | лимит на запуск и на день, USD |
| `APIFY_MAX_ITEMS_PER_PROFILE`, `APIFY_RUN_TIMEOUT_S` | 5, 300 | ≥ 1 | публикаций на профиль; срок запуска, с |
| `DGIS_API_KEY`* | | | геокодер 2ГИС |
| `DGIS_QUOTA_LIMIT`, `DGIS_DAILY_LIMIT` | 900, 50 | ≥ 0 | квота за период, дневной предел |
| `DGIS_QUOTA_PERIOD_START` | | `ГГГГ-ММ-ДД` | начало периода квоты |
| `DGIS_CACHE_TTL_DAYS` | 7 | ≥ 1 | срок кэша кандидатов, дней |
| `KRISHA_MIN_DELAY_S` | 5.0 | ≥ 0 | пауза между запросами, с |
| `KRISHA_MAX_PAGES`, `KRISHA_MAX_CARDS` | 2, 10 | ≥ 1, ≥ 0 | страниц выдачи и карточек за запуск |
| `KRISHA_PLAYWRIGHT` | выкл. | логическое | закрытая карточка через Playwright(пакет ставится отдельно) |
| `OLX_CLIENT_ID`, `OLX_CLIENT_SECRET`* | | | приложение OLX |
| `OLX_REDIRECT_URI` | | `https://<хост>/api/integrations/olx/callback` | обратный вызов OAuth |
| `OLX_SCOPES` | `read v2` | | права доступа |
| `ANTHROPIC_API_KEY`* | | | Claude; без ключа этап `llm` не выполняется |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | идентификатор | модель |
| `ANTHROPIC_EFFORT` | `medium` | `low` `medium` `high` `xhigh` `max` | уровень размышления |
| `ANTHROPIC_MAX_TOKENS` | 16000 | 1024-64000 | предел ответа с размышлением |
| `ANTHROPIC_TIMEOUT_S` | 120 | 10-600 | срок ответа, с |
| `ANTHROPIC_DAILY_BUDGET_USD` | 5.0 | 0-1000 | дневной бюджет, USD; 0 - этап выключен |
| `ANTHROPIC_WORKSPACE_ID` | | | рабочее пространство для ключа организации |
| `ANTHROPIC_BACKFILL_BATCH`, `ANTHROPIC_BACKFILL_INTERVAL_MIN` | 5, 10 | 0-100, 1-1440 | дозапрос: публикаций за проход(0 - выключен); пауза, мин |
| `SAQ_ASR_MODEL` | `base` | `tiny` `base` `small` `medium` `large-v3` `off` | модель faster-whisper |
| `SAQ_ASR_MAX_S`, `SAQ_ASR_TIMEOUT_S` | 300, 300 | 5-3600, 10-3600 | предел длины ролика и времени распознавания, с |
| `SAQ_GALLERY_SYNC_HOURS` | 24 | 0-720 | период подгрузки фото Homeportal, ч; 0 - выключено |
| `SAQ_WORKER_POLL_S` | 5.0 | ≥ 0.1 | пауза опроса очереди, с |
| `SAQ_WORKER_ID` | имя хоста | строка, длиннее 48 символов обрезается | имя воркера |
| `SAQ_JOB_LEASE_S`, `SAQ_JOB_MAX_S` | 1800, 3600 | ≥ 60 | блокировка задания и предел её продления, с |
| `SAQ_USER_AGENT` | `SAQ-Qurylys-monitor/0.3 …` | печатные ASCII | User-Agent сборщиков |
| `SAQ_ALLOW_SCHEMA_RESET` | выкл. | логическое | пересоздать таблицы при неизвестной схеме даже с данными |
| `SAQ_COLLECTED_AUTOLINK` | выкл. | логическое | привязка-гипотеза собранных публикаций к ЖК автоматически |
| `SAQ_SCREENING_DAYS` | 120 | 1-3650 | окно свежей рекламы "Проверки по рекламе", дней |

**Остальные переменные** - вне `app.settings`, из `.env` приложение их не читает:

| Переменная | Читает | По умолчанию | Назначение |
|---|---|---|---|
| `SAQ_DB_PASSWORD`* | compose | `saq` | пароль PostgreSQL, см. [Docker Compose](#docker-compose) |
| `SAQ_ALLOWED_CLIENTS` | compose, Vite | только эта машина | IP или подсети клиентов интерфейса |
| `DATABASE_URL`* | API, воркер | `postgresql+psycopg://saq:saq@localhost:5432/saq` | адрес БД SQLAlchemy |
| `SAQ_ENV_FILE` | API, воркер | `<репозиторий>/.env` | файл настроек; пусто - не читается |
| `SAQ_SHARED_DIR` | API, воркер | ближайший `shared/` с `risk_model.json` | общие данные; в образе `/app/shared` |
| `TESSERACT_CMD` | API, воркер | `tesseract` | программа OCR |
| `SAQ_OCR_TIMEOUT` | API, воркер | 60 | предел распознавания скриншота, с |
| `VITE_API_URL` | сборка Vite | `/api` | абсолютный адрес API для интерфейса на другом домене; виден в браузере |
| `API_PROXY` | Vite | `http://localhost:8000` | куда dev-сервер проксирует `/api` |
| `SAQ_WORKER` | `./dev.sh` | 1 | `0` - без воркера |
| `SAQ_TEST_DATABASE_URL` | тесты | временная SQLite | другая БД для тестов, таблицы приложения в ней пересоздаются |
| `HF_HOME`, `HF_HUB_OFFLINE` | faster-whisper | `HF_HOME=/data/models/.hf` в образе | служебные файлы HuggingFace; `HF_HUB_OFFLINE=1` задаёт CI |

`ADMIN_USER_IDS`, `ADMIN_CHAT_ID`, `SERVICE_URL` в `.env.example` - справочно: их вместе с `SERVICE_API_TOKEN` и `BOT_TOKEN` читает проект saq-telegram-bot([INTEGRATIONS.md, "Telegram-бот заявок"](INTEGRATIONS.md#telegram-бот-заявок)). Настройка подключений - [INTEGRATIONS.md, "Общая настройка"](INTEGRATIONS.md#общая-настройка).

## Данные при развёртывании

- Пустая БД при первом старте API получает стартовый набор `shared/snapshot` - в Docker(новый том `pgdata`) и при `./dev.sh`(новый `backend/saq.db`). В непустую БД набор не загружается никогда: рабочая база при обновлении остаётся как была.
- Подключения из набора приходят с настройками и "включено", но в статусе "не проверено": ключи берутся только из `.env` нового сервера. Включённые сборщики начнут собирать, когда появятся их ключи; публикации, которые уже есть в наборе, распознаются по внешнему ID и не дублируются, а ответы Claude из набора повторно не запрашиваются.
- Ручная загрузка в пустую БД - `docker compose exec -T api python -m app.snapshot import`; в непустую - отказ без изменений.
- Чистый лист: в Docker - `docker compose down -v`(удаляет все тома) и `make up`; локально - остановить `./dev.sh`, удалить `backend/saq.db`, `saq.db-wal`, `saq.db-shm` и `backend/data/media`. Следующий старт загрузит набор заново.

Состав набора, выгрузка и открытые реестры - [DATA.md, "Стартовый набор"](DATA.md#стартовый-набор).

## Обновление версии

1. Резервная копия БД и файлов - [Резервные копии](#резервные-копии).
2. Новая версия кода, например `git pull`.
3. `make up`. API при старте обновляет схему БД на месте; воркер стартует после готовности API, а если схема ещё не та, ждёт её и пишет `Воркер ждёт схему БД …`.
4. Журнал API - `make logs SERVICE=api`.

Локально достаточно перезапустить `./dev.sh`: зависимости доустановятся, файловая SQLite перед обновлением схемы копируется в `backend/saq.db.bak-<старая версия>-<UTC>`(занятое имя - с суффиксом `-2`, `-3`) с правами 0600(в копии токены подключений).

Версия схемы - `meta.schema_version`, текущая `2026.10-audit-2`. Поведение API при старте:

| Состояние БД | Что происходит |
|---|---|
| нет таблиц приложения | таблицы создаются, загружается стартовый набор |
| известная прошлая версия | шаги обновления на месте, данные сохраняются |
| неизвестная версия без невосстановимых данных | таблицы приложения пересоздаются, пустая БД снова получает набор |
| неизвестная версия с собранными или внесёнными данными | API не стартует и называет таблицы с данными |

В последнем случае делают резервную копию и переносят данные вручную или запускают API с `SAQ_ALLOW_SCHEMA_RESET=1` - таблицы пересоздаются с потерей данных. На PostgreSQL два процесса не обновляют схему одновременно(advisory lock). Механика шагов и перечень невосстановимых данных - [ARCHITECTURE.md, "Схема БД и её обновление"](ARCHITECTURE.md#схема-бд-и-её-обновление).

## Резервные копии

**PostgreSQL(Docker)** - перед каждым обновлением:

```bash
docker compose exec -T db pg_dump -U saq saq > backup-$(date +%F).sql
docker compose exec -T api tar -C /data -cf - media > media-$(date +%F).tar
```

Восстановление в пустую базу(`tar` в контейнере `api` работает от пользователя `saq`, файлы получают того же владельца):

```bash
docker compose stop api worker
docker compose exec -T db dropdb -U saq saq
docker compose exec -T db createdb -U saq saq
docker compose exec -T db psql -U saq -d saq -v ON_ERROR_STOP=1 < backup-YYYY-MM-DD.sql
docker compose start api
docker compose exec -T api tar -C /data -xf - < media-YYYY-MM-DD.tar
docker compose start worker
```

**SQLite(`./dev.sh`).** База `backend/saq.db`, файлы `backend/data/media`. Базу копируют при остановленном `./dev.sh` или на ходу через backup API SQLite:

```bash
cd backend && .venv/bin/python -c "import sqlite3; sqlite3.connect('saq.db').backup(sqlite3.connect('saq-backup.db'))"
```

Копии `saq.db.bak-…`(0600) сами делают API перед обновлением схемы и `app.demo purge` перед удалением.

**Что секретно.** В БД и копиях без шифрования лежат токены OLX, данные заявителей бота и подписки жителей: копии и `.env` хранят как секрет. Сессию Telegram(том `tgsession`, `backend/data/private`) в копию не включают - при потере входят заново. Стартовый набор - не резервная копия: в нём нет токенов, заявок, видео и служебных таблиц.

## Воркер

Воркер выполняет очередь заданий: проверки и сборы, загрузку файлов, анализ публикаций, отправку предупреждений. Устройство очереди - [ARCHITECTURE.md, "Очередь заданий и воркер"](ARCHITECTURE.md#очередь-заданий-и-воркер). Запуск: в Docker - сервис `worker` после готовности API; `./dev.sh` - сам после ответа API(`SAQ_WORKER=0` - без него); вручную - `cd backend && .venv/bin/python -m app.worker`. Флаги: `--once` - выполнить задания из очереди и выйти; `--schema-wait S` - сколько секунд ждать схему(с `--once` по умолчанию 0: схема не готова - выход с кодом 0); `--id ID` - имя вместо `SAQ_WORKER_ID` и имени хоста. Схему воркер не меняет, а ждёт, пока её применит API.

- **Имя.** Стабильный `SAQ_WORKER_ID` при старте возвращает в очередь задания упавшего прошлого запуска с тем же именем. Второй живой процесс с тем же именем ждёт остановки первого, `--once` в этом случае пропускается. Разовый проход в Docker - с другим именем: `docker compose run --rm worker python -m app.worker --once --id manual-1`.
- **Несколько воркеров** допустимы с разными именами: задания выполняет любой, сборы по расписанию, плановые проверки и дозапрос Claude планирует только держатель аренды `scheduler`. `docker compose up --scale worker=2` даёт обоим имя `worker-1`, второй будет ждать.
- **Остановка.** SIGTERM или SIGINT: текущее задание дорабатывается, затем выход с кодом 0. В Docker на это 120 с, загрузка файла прерывается через 30 с.
- **Пульс** раз в 30 с продлевает отметку воркера, аренду планировщика и блокировку задания(не дольше `SAQ_JOB_MAX_S`). Нет пульса 120 с - на `/sources` "Воркер сбора не запущен".
- **Уборка** раз в час у держателя аренды `scheduler`: непривязанные ручные загрузки и файлы хранилища без записей старше суток, кандидаты 2ГИС старше `DGIS_CACHE_TTL_DAYS`, истёкшие state OAuth, брошенные временные файлы.
- **Журнал** - `make logs SERVICE=worker`; значения ключей маскируются, адреса с ключами не пишутся.
- **После смены ключей** воркер перезапускают, иначе карточка подключения покажет "Ключи на сервере изменились".

## Служебные команды

Локально - `cd backend && .venv/bin/python -m <модуль> …`. В Docker - `docker compose exec -T api python -m <модуль> …`; команды, которым нужны сессия Telegram или модели речи, - в `worker`: `docker compose exec worker python -m app.integrations.smoke`, `docker compose exec worker python -m app.speech download`, вход в Telegram - `docker compose run --rm worker python -m app.integrations.telegram_login …` при остановленном воркере.

| Команда | Что делает |
|---|---|
| `app.snapshot export [--out DIR] [--max-px 1024] [--quality 75]` | выгрузка стартового набора(`make snapshot-export`, `make snapshot-export-docker`): БД только читается, каталог заменяется целиком при успехе |
| `app.snapshot import [--dir DIR]` | загрузка набора в пустую БД |
| `app.demo purge --dry-run` / `--yes` | удаление прежнего демо-набора, см. [ниже](#базы-с-прежним-демо-набором) |
| `app.demo import-registry` | создать или дополнить карточки ЖК по открытому реестру; API делает это при каждом старте |
| `app.demo add-advertised --file F [--author ИМЯ] [--note ТЕКСТ]` | карточки ЖК по рекламе из JSON; в Docker файл передают через `docker compose cp F api:/tmp/` |
| `app.importer <xlsx\|csv> [--out FILE] [--dry-run]` | разбор открытого реестра в `shared/registry_ust_kamenogorsk.json` |
| `app.golden` | эталон модели риска для тестов(`make golden`) |
| `app.integrations.smoke [коды…]` | живая проверка подключений без сохранения публикаций |
| `app.integrations.telegram_login` с `--send-code`, `--code КОД`, `--password`, `--status`, `--logout` | вход аккаунта Telegram |
| `app.speech download [--model M]`, `app.speech <видео>` | скачать модель речи в `SAQ_ASR_MODEL_DIR`; расшифровать один ролик |
| `app.gallery fetch [--only ID,…]` | фото Homeportal для ЖК реестра |
| `app.satstage [--only] [--limit] [--max-usd] [--force] [--dry-run] [--save-images DIR]` | стадия стройки по спутниковым снимкам в `shared/sat_stage.json`, нужен `ANTHROPIC_API_KEY` |
| `node frontend/scripts/wayback-index.mjs [адрес API]` | индекс снимков `frontend/public/wayback-index.json`, нужен запущенный API |

`make snapshot-export-docker` выгружает набор в одноразовом контейнере образа `api` с подключёнными `backend/` и `shared/`, потому что файлы лежат в томе `media`:

```bash
docker compose run --rm --no-deps -v "$PWD/backend:/repo/backend:z" -v "$PWD/shared:/repo/shared:z" \
  -e SAQ_SHARED_DIR=/repo/shared -e PYTHONDONTWRITEBYTECODE=1 -w /repo/backend --user "$(id -u):$(id -g)" \
  api python -m app.snapshot export --out /repo/shared/snapshot
```

`app.importer`, `app.golden`, `app.satstage` и `wayback-index.mjs` пишут файлы репозитория и выполняются локально. Коды smoke: `instagram`, `telegram`, `tiktok`, `krisha`, `telegram_bot`, `intake`, `dgis`, `anthropic`, `olx`; код выхода 0 - отчёт, 2 - неизвестный код. Оценка ИИ на кейсах - маршрут `GET /api/ai/eval`, отдельной команды нет.

По темам: вход в Telegram - [INTEGRATIONS.md, "Вход по коду"](INTEGRATIONS.md#вход-по-коду); речь и спутник - [AI.md, "Речь в роликах"](AI.md#речь-в-роликах), [AI.md, "Стадия стройки по спутниковому снимку"](AI.md#стадия-стройки-по-спутниковому-снимку); набор и реестр - [DATA.md, "Загрузка и обновление стартового набора"](DATA.md#загрузка-и-обновление-стартового-набора).

## Базы с прежним демо-набором

База, засеянная вымышленным демо-набором прежних версий, очищается командой `app.demo purge`. Пока такие строки есть, API при каждом старте пишет в журнал `В БД остались демо-данные прежних версий …`.

```bash
docker compose stop worker                                            # сбор и анализ стоят
docker compose exec -T db pg_dump -U saq saq > backup-$(date +%F).sql  # резервная копия
docker compose up -d --build api                                      # новая версия обновит схему
docker compose exec -T api python -m app.demo purge --dry-run         # что удалится и что останется
docker compose exec -T api python -m app.demo purge --yes             # удаление
docker compose up -d                                                  # воркер и интерфейс
```

Локально - `cd backend && .venv/bin/python -m app.demo purge --dry-run`, затем `--yes`; перед удалением SQLite копируется в `saq.db.bak-demo-purge-<UTC>`.

- **Удаляется** только демо-набор(`origin = demo`): публикации со всем, что на них висит, обращения, сигналы и события журнала по демо-ЖК, демо-ЖК и демо-застройщики. С собранных и внесённых публикаций снимается привязка к демо-ЖК с записью в журнале.
- **Остаются** аналитики, открытый реестр и карточки ЖК из него, подключения и запуски сбора, файлы, задания, анализы ИИ собранных публикаций, счётчики квот.
- **Отказ.** Есть обращение не из демо-набора по демо-ЖК - команда ничего не удаляет и называет его, код 2; схема не текущая - сначала запускают API, код 2; без `--yes` ничего не меняется, код 1.
- **Флаг** `meta.demo_data = purged` ставится в той же транзакции; повторный запуск ничего не удаляет.

## Диагностика

| Симптом | Причина | Что сделать |
|---|---|---|
| Интерфейс не открывается с другого устройства, соединение сбрасывается | адреса нет в `SAQ_ALLOWED_CLIENTS`; в журнале `web` - `Соединение с <IP> отклонено` | добавить IP или подсеть, `docker compose up -d web` |
| Vite: `Blocked request. This host … is not allowed` | имени нет в `SAQ_WEB_HOSTS` | добавить имя, `docker compose up -d web api` |
| 400 "Запрос по неизвестному имени сервера отклонён" | API не знает имя из `Host`, `X-Forwarded-Host` или `Forwarded` | имя в `SAQ_WEB_HOSTS`; через туннель OLX открыт только путь обратного вызова |
| 403 "Запрос со страницы другого сайта отклонён" | изменяющий запрос со страницы другого адреса | открыть интерфейс по тому же адресу, что и API; другой домен - `SAQ_CORS_ORIGINS` |
| `web` перезапускается, в журнале `SAQ_ALLOWED_CLIENTS: "…" - не IP-адрес и не подсеть` | неверное значение | исправить, `docker compose up -d web` |
| `web` не стартует без сети | `npm ci` при старте контейнера | доступ к реестру npm или `./dev.sh` |
| Подключение долго "в очереди", на `/sources` - "Воркер сбора не запущен" | воркер остановлен или ждёт схему | `make ps`, `make logs SERVICE=worker` |
| "Ключи на сервере изменились" | ключ в `.env` сменился после последней проверки | `docker compose up -d api worker`, затем "Проверить подключение" |
| `GET /api/ocr/status` - `engine: none` | нет Tesseract или его языков | Tesseract с `rus` и `kaz` или `TESSERACT_CMD`; в Docker они есть |
| Журнал API: `Пустая БД, а стартового набора нет …`, войти некем | нет `shared/snapshot/manifest.json` | вернуть каталог `shared/snapshot` из Git(в Docker набор входит в образ - `make up`) и начать с чистого листа([Данные при развёртывании](#данные-при-развёртывании)): карточки открытого реестра, созданные этим стартом, делают БД непустой |
| API не стартует: `Схема БД неизвестной версии …` | неизвестная версия с невосстановимыми данными | [Обновление версии](#обновление-версии) |
| 507 "Мало места для файлов на сервере" | свободно меньше `MEDIA_MIN_FREE_MB`, у ручной загрузки - меньше запаса плюс 1024 МБ | освободить место или изменить запас |
| 429 на ручной загрузке файла | общий предел ручных загрузок(скриншоты, снимки площадки, фото объекта): 50 файлов и 200 МБ за час, 300 файлов и 1 ГБ за сутки UTC | подождать; удаление фото предел не возвращает |
| 503 на маршрутах администратора | `SAQ_ADMIN_TOKEN` не задан или слабый(предупреждение в журнале API) | задать токен по правилам, перезапустить API |

Статусы подключений и действия по каждому - [INTEGRATIONS.md, "Статусы подключения и запуска"](INTEGRATIONS.md#статусы-подключения-и-запуска); поведение интерфейса при недоступном сервере - [FEATURES.md, "Режим работы и ошибки"](FEATURES.md#режим-работы-и-ошибки).
