# Разработка

Документ для тех, кто меняет код SAQ Qurylys: раскладка репозитория, окружение, команды, тесты, CI, правила оформления кода и текстов и порядок типовых изменений. Запуск и сопровождение системы - [DEPLOYMENT.md](DEPLOYMENT.md), устройство - [ARCHITECTURE.md](ARCHITECTURE.md).

## Раскладка кода

| Каталог | Что там |
|---|---|
| `backend/app/` | бэкенд FastAPI: модули предметной области(`risk.py`, `screening.py`, `signals.py`, `verification.py` и другие), приложение и общие маршруты(`main.py`), схемы API(`schemas.py`), модели(`models.py`), обновление схемы(`migrate.py`), настройки(`settings.py`), воркер(`worker.py`, `jobs.py`) |
| `backend/app/ai/` | правила `saq-ai-1`: OCR, нормализация, условия оплаты, сопоставление с ЖК, оценка на кейсах |
| `backend/app/integrations/` | подключения: каркас(`base.py`, `context.py`, `http.py`), реестр, сервис и маршруты, модуль на подключение, `smoke.py` |
| `backend/tests/` | pytest, `conftest.py`, загрузчик демо-набора тестов(`demo_seed.py`), страницы Krisha(`fixtures/krisha/`) |
| `frontend/src/` | `pages/` - экраны, `components/` - блоки экранов, `lib/` - логика экранов, запросы к API(`api.ts`), состояние(`store.tsx`), словари(`i18n/`) и тесты рядом, `data/` - типы API(`types.ts`), офлайн-копия и открытый реестр без бэкенда, `ui/` - иконки, `styles/` - стили |
| `frontend/scripts/` | `wayback-index.mjs` - индекс спутниковых снимков |
| `shared/` | общее для бэкенда и фронтенда: модель риска(`risk_model.json`), нормы(`law.json`), открытый реестр, кейсы ИИ(`ai_cases.json`), названия настроек(`env_labels.json`), стадии по снимкам(`sat_stage.json`), стартовый набор(`snapshot/`), тестовые данные(`testdata/`) |

Правила размещения:

- Бизнес-логика - в модулях `backend/app/`; обработчик маршрута проверяет вход, вызывает модуль и возвращает схему.
- Маршруты области - в `APIRouter` своего модуля, подключаются в `main.py`; в самом `main.py` - маршруты справочников, ЖК, публикаций, журнала и анализа. Маршруты подключений - `integrations/routes.py`.
- Таблицы - только в `models.py`, схемы запросов и ответов - только в `schemas.py`.
- Фронтенд: экран - `pages/`, переиспользуемый блок - `components/`, всё, что проверяется без React(расчёты, подписи, разбор ответов), - `lib/` с тестом рядом.

Карта модулей - [ARCHITECTURE.md, "Модули бэкенда"](ARCHITECTURE.md#модули-бэкенда) и ["Фронтенд"](ARCHITECTURE.md#фронтенд).

## Окружение

- **Бэкенд** - `make setup-backend`: Python 3.12+(CI и образ API - 3.12), окружение `backend/.venv`, пакеты из `requirements.txt` и `requirements-dev.txt`(pytest, ruff). Если `python3` старше 3.12 - `make setup-backend PYTHON=python3.12`.
- **Фронтенд** - `make setup-frontend`(`npm ci` по `package-lock.json`). Node `^22.12`, `^24` или 26+: этого требует vitest 5.
- **Без Node** цели фронтенда выполняются в контейнере `node:22-slim` с правами текущего пользователя: `frontend/` монтируется в `/app`, `shared/` - в `/shared` только для чтения(фронтенд импортирует `../../../shared/*.json`). Режим выбирается по наличию `npm` в `PATH`, `NODE_DOCKER=1` или `0` задаёт его явно; `make help` показывает действующий.
- `make setup` - обе части. `./dev.sh`(`make dev`) ставит зависимости сам, но запускает Vite локально: без Node вместо него - `make up`. Подробно - [DEPLOYMENT.md, "Локальный запуск"](DEPLOYMENT.md#локальный-запуск).

Системные программы для полного набора тестов бэкенда(как в образе API и CI): Tesseract с языками `rus` и `kaz`, ffmpeg с libx264, `prlimit` из util-linux. Без них соответствующие тесты пропускаются. Debian и Ubuntu: `sudo apt-get install tesseract-ocr tesseract-ocr-rus tesseract-ocr-kaz ffmpeg util-linux`. macOS: `brew install tesseract tesseract-lang ffmpeg`; `prlimit` там нет, и тест кадров 1080p под ограничением памяти пропускается.

По желанию: `PYTHONDONTWRITEBYTECODE=1` и `PYTEST_ARGS="-q -p no:cacheprovider"` - прогон не оставляет `__pycache__` и `.pytest_cache`. Кэши и сборку убирает `make clean`; данные, `.env`, окружения, `node_modules` и тома Docker она не трогает.

Для разработки `.env` не обязателен: без ключей подключения показываются "не настроен", остальное работает. Переменные - [DEPLOYMENT.md, "Переменные окружения"](DEPLOYMENT.md#переменные-окружения).

## Команды разработки

Список целей - `make help`. Цели только оборачивают команды из правого столбца.

| Цель | Что делает | Прямая команда |
|---|---|---|
| `check` | `lint`, `typecheck`, `test`, `build` - всё, что проверяет CI, кроме аудита | - |
| `lint-backend` | ruff, правила - `backend/pyproject.toml` | `cd backend && .venv/bin/python -m ruff check app tests` |
| `lint-frontend` | oxlint, падает только на ошибках | `cd frontend && npx oxlint src` |
| `typecheck` | типы фронтенда | `cd frontend && npx tsc -b` |
| `test-backend` | pytest на временной SQLite, без сети | `cd backend && .venv/bin/python -m pytest -q` |
| `test-frontend` | vitest | `cd frontend && npm test` |
| `e2e` | сквозной сценарий README | `cd backend && .venv/bin/python -m pytest -q -m scenario tests/test_scenario.py` |
| `build` | сборка в `frontend/dist` | `cd frontend && npx vite build` |
| `audit-backend` | pip-audit ровно установленных версий | `pip freeze` окружения бэкенда в `pip-audit -r /dev/stdin --no-deps --disable-pip`; pip-audit 2.10.1 - в отдельном окружении |
| `audit-frontend` | уязвимости зависимостей сборки | `cd frontend && npm audit --omit=dev` |
| `golden` | эталон `shared/testdata/risk_golden.json` | `cd backend && .venv/bin/python -m app.golden` |
| `snapshot-views` | ответы API офлайн-копии `shared/snapshot/api/*.json` по строкам набора | - |
| `clean` | кэши и результаты сборки | - |

`lint`, `test` и `audit` объединяют пары целей бэкенда и фронтенда; `audit` нужна сеть. Цели установки, запуска и выгрузки стартового набора - [DEPLOYMENT.md, "Makefile"](DEPLOYMENT.md#makefile).

Переменные Makefile для разработки: `PYTEST_ARGS`(по умолчанию `-q`) - аргументы pytest для `test-backend` и `e2e`; `PYTHON`(`python3`) - интерпретатор для окружений; `VENV`(`backend/.venv`); `NODE_DOCKER` и `NODE_IMAGE`(`node:22-slim`) - режим фронтенда; `AUDIT_VENV`(`~/.cache/saq-pip-audit`) - окружение pip-audit.

```bash
make test-backend PYTEST_ARGS="-q -x -k signal"        # до первой ошибки, только тесты со словом signal
make test-backend PYTEST_ARGS="-q tests/test_risk.py"  # один файл
make lint-frontend typecheck test-frontend build       # проверки фронтенда подряд
```

`npm run build` - это `tsc -b && vite build`, а `make build` - только `vite build`: типы проверяет `make typecheck`.

## Тесты бэкенда

Тесты - `backend/tests/test_*.py`. Окружение задаёт `conftest.py` до импорта приложения:

- **База** - временная SQLite в каталоге `saq-test-*`, удаляется после прогона; `SAQ_TEST_DATABASE_URL` подменяет её.
- **Настройки** - `SAQ_ENV_FILE` пустой, `.env` репозитория не читается; остальные переменные приложения удаляются из окружения, ключи разработчика в тесты не попадают. `MEDIA_ROOT` и `TG_SESSION_PATH` - во временном каталоге, `SAQ_WEB_HOSTS=testserver`(имя хоста `TestClient`). Фикстура `env` меняет переменные на время теста.
- **Данные** - пустая БД получает вымышленный демо-набор(`shared/testdata/demo_seed.json`), а не стартовый набор. `client` - `TestClient` на заново засеянной БД у каждого теста. Стартовый набор подключают `test_snapshot.py` и `test_scenario.py`.
- **Права** - подключения и запуски читаются как администратором; вид посетителя проверяют тесты с меткой `visitor`.
- **Без сети** - сокеты только к loopback и `AF_UNIX`(для PostgreSQL на localhost), транспорт и DNS `integrations/http.py` падают, паузы мгновенные, фабрики клиентов Telegram, Anthropic и браузера Krisha - заглушки-ошибки. Ответы внешних сервисов задаёт фикстура `http_mock`: так подменяются Graph API, Apify, 2ГИС, OLX, Krisha, Homeportal и Bot API. Настоящий клиент SDK Anthropic(`test_llm.py`, `test_satstage.py`) ходит в транспорт-заглушку через подменённый `CLIENT_FACTORY`. Страницы Krisha - синтетические файлы, Telethon - подменный клиент.
- **Речь** - движок распознавания "не установлен"(фикстура `_no_asr`), модель Whisper не скачивается; тесты речи подменяют движок сами. CI вдобавок задаёт `HF_HUB_OFFLINE=1`.
- **Помощники** - фикстуры `run_ctx`(контекст запуска подключения), `run_worker`, `ocr_stub`(OCR без Tesseract), `accept`(приёмочные проверки подключений), `admin_headers`(заголовок `X-Admin-Token` с тестовым токеном) и функции `png_bytes`, `mp4_bytes`.

Пропуски: нет Tesseract, ffmpeg, libx264 или `prlimit`; нет `docs/Усть-Каменогорск_объекты.xlsx`; проверки только для PostgreSQL на SQLite и наоборот; проверка Krisha без Playwright, если Playwright установлен. Несколько пропусков на SQLite ожидаемы.

**PostgreSQL 16** - на отдельном временном сервере, не на базе Docker Compose: тесты пересоздают таблицы приложения.

```bash
docker run -d --rm --name saq-pgtest -e POSTGRES_PASSWORD=t -p 127.0.0.1:55432:5432 postgres:16-alpine
SAQ_TEST_DATABASE_URL=postgresql+psycopg://postgres:t@localhost:55432/postgres make test-backend
docker stop saq-pgtest
```

| Файл | Область |
|---|---|
| `test_risk.py` | модель риска: кейсы A/B/C, границы зон, R1-R6, наблюдения, оценка публикации, фикстуры, актуальность эталона |
| `test_ai.py` | правила `saq-ai-1`, кейсы `shared/ai_cases.json`, маршруты анализа и решений по выводам ИИ, OCR |
| `test_llm.py` | Claude: форма запроса, проверка ответа, ошибки API, бюджет, повторное использование, дозапрос |
| `test_speech.py`, `test_satstage.py` | речь в роликах; стадия стройки по снимку |
| `test_api.py`, `test_ads_service.py` | справочники, риск через API, привязка публикации, защита Host, CORS и межсайтовых записей; общая запись публикации |
| `test_signals.py`, `test_pdf.py`, `test_route.py`, `test_letters.py` | сигнал, его PDF, маршрут рассмотрения, проект запроса сведений |
| `test_audit.py`, `test_logs.py` | журнал действий и запросы сведений; журнал доступа без `chat_id` |
| `test_law.py` | нормы `shared/law.json` и ссылки факторов на них |
| `test_snapshot.py`, `test_scenario.py` | стартовый набор и офлайн-ответы; сквозной сценарий |
| `test_registry.py`, `test_demo.py` | открытый реестр и импорт; карточки ЖК из реестра, демо-набор прежних версий |
| `test_discovery.py`, `test_decisions.py` | объекты из рекламы и "Проверка по рекламе"; решения "Объект сдан" и "Проверено - замечаний нет" |
| `test_verification.py`, `test_gallery.py` | сверка сведений аналитиком; фото объекта |
| `test_sellers.py`, `test_changes.py`, `test_alerts.py` | продавцы, цена и связи застройщика; изменения в источнике; предупреждения жителям |
| `test_media.py`, `test_intake.py`, `test_worker.py` | хранилище и загрузчик файлов; приём публикаций; задания и воркер |
| `test_connectors_api.py`, `test_integration_<код>.py` | API подключений; по файлу на подключение |
| `test_settings.py`, `test_migrate.py`, `test_db_lock.py` | настройки и маскирование; обновление схемы; блокировка строки |

## Тесты фронтенда

`make test-frontend`(`vitest run`). Тесты лежат рядом с модулями(`*.test.ts`) и идут в Node без браузера: функции `lib/` и `data/`, разметка части компонентов - через `renderToStaticMarkup`. Тесты читают `shared/`(модель риска, нормы, эталон и тестовые данные, стартовый набор, открытый реестр).

| Область | Тесты |
|---|---|
| паритет TS-движка риска с эталоном Python | `lib/risk.test.ts` |
| "Проверка по рекламе", фильтры карты | `lib/screening.test.ts`, `lib/mapFilters.test.ts` |
| выводы ИИ и Claude | `lib/ai.test.ts`, `lib/llm.test.ts`, `components/Hypothesis.test.ts` |
| ошибки API, сессия | `lib/api.test.ts`, `lib/session.test.ts` |
| подключения, тексты сервера без имён переменных и путей | `lib/connectors.test.ts`, `lib/plain.test.ts` |
| 2ГИС, объекты из рекламы | `lib/geo.test.ts`, `lib/discovered.test.ts` |
| файлы и фото | `lib/media.test.ts`, `lib/viewer.test.ts`, `components/Gallery.test.ts` |
| площадки, ссылки только http(s) | `lib/platform.test.ts`, `lib/check.test.ts`, `components/Provenance.test.ts` |
| полнота словарей RU и KZ | `lib/i18n.test.ts` |
| маршрут, решения, сверка, журнал | `lib/route.test.ts`, `lib/decision.test.ts`, `lib/verification.test.ts`, `lib/audit.test.ts` |
| продавцы, изменения, предупреждения, нормы | `lib/sellers.test.ts`, `lib/changes.test.ts`, `lib/alerts.test.ts`, `lib/law.test.ts` |
| публичные страницы, поиск | `lib/public.test.ts`, `lib/find.test.ts`, `lib/search.test.ts`, `lib/range.test.ts` |
| офлайн-копия | `data/snapshot.test.ts` |

Кроме vitest: `make typecheck`(`tsc -b`, тесты типизируются через `tsconfig.test.json`) и `make lint-frontend`.

## Сквозная проверка

`make e2e` запускает `backend/tests/test_scenario.py`(метка `scenario`): шаги [README, "Тестовый сценарий"](../README.md#тестовый-сценарий) через API на стартовом наборе `shared/snapshot`(строки БД без файлов). Путь: обзор ЖК и риска, карточка жёлтого ЖК, публикация с предложением внести деньги и её условия оплаты, вето R2 и его отмена, анализ нового текста и сохранение публикации, сигнал, решение человеком, PDF. "Сегодня" для риска и "Проверки по рекламе" - дата `api.asOf` из `shared/snapshot/manifest.json`, поэтому результат не зависит от дня прогона. Сеть не нужна, прогон занимает секунды.

Тест опирается на конкретные записи набора(ЖК, баллы до и после вето). После перевыгрузки набора `make e2e` показывает, верен ли сценарий README; при намеренном изменении данных правятся README и константы теста. `make test-backend` включает сценарий, CI запускает его и отдельным шагом.

Браузерного теста интерфейса нет: экраны сценария проверяются вручную по README.

## CI

GitHub Actions, `.github/workflows/ci.yml`; шаги вызывают цели Makefile.

- **Запуск** - push в `master`, pull request, вручную(`workflow_dispatch`). Права - `contents: read`. Новый прогон той же ветки отменяет предыдущий(`concurrency`).
- **backend**(`ubuntu-latest`, до 30 минут, `PYTHONDONTWRITEBYTECODE=1`, `HF_HUB_OFFLINE=1`): из apt - Tesseract с `rus` и `kaz`, ffmpeg, util-linux; Python 3.12 с кэшем pip; `make setup-backend`, `lint-backend`, `e2e`, `test-backend`(SQLite), `audit-backend`.
- **frontend**(`ubuntu-latest`, до 15 минут): Node 24 с кэшем npm, цели идут без контейнера; `make setup-frontend`, `lint-frontend`, `typecheck`, `test-frontend`, `build`, `audit-frontend`.

CI не проверяет PostgreSQL(вручную, раздел "Тесты бэкенда"), сборку образов и стек Docker, живые подключения(`python -m app.integrations.smoke`, [INTEGRATIONS.md](INTEGRATIONS.md)) и интерфейс в браузере. Версии инструментов закреплены: ruff - в `backend/requirements-dev.txt`, pip-audit - в Makefile, oxlint, TypeScript, vitest и Vite - в `frontend/package-lock.json`.

## Стиль кода и текстов

**Python 3.12.** Аннотации `X | None`, встроенные `list`, `dict`, `tuple`, `datetime.UTC`, `collections.abc`. У модуля - docstring о назначении и главных правилах. ruff проверяет ошибки, а не оформление: pyflakes, ошибки и предупреждения pycodestyle, bugbear, ошибки pylint, flake8-pie, забытый отладчик, ошибки вызова logging, точечные `noqa` и `type: ignore`, аннотации в стиле 3.12 и опасные вызовы flake8-bandit(`exec`, `eval`, `pickle`, `yaml.load`, `shell=True`, слабые хэши, `verify=False`, запрос без тайм-аута). Исключения `B008`, `E731`, `E741` и исключения отдельных файлов пояснены в `pyproject.toml`.

**Соглашения бэкенда:**

- JSON API - camelCase: схемы наследуют `schemas.Camel`(алиасы `to_camel`). Типы ответов фронтенда - `frontend/src/data/types.ts`, меняются вместе со схемой.
- Время - UTC: `models.utcnow()`, `models.as_utc()` для каждого значения из SQL(SQLite отбрасывает пояс), `models.iso()` в ответе. Местное время(`risk_model.ALMATY`, UTC+5) - только в текстах людям и границах суток.
- Ошибка для человека - `HTTPException` с понятным `detail` по-русски. Ошибки проверки 422 фронтенд переводит сам(`lib/api.ts`).
- Логи и тексты ошибок проходят `logs.redact`; значения ключей не пишутся в ответы и журнал - [SECURITY.md, "Секреты"](SECURITY.md#секреты). Файлы извне - только через `integrations/http.py` и `media.py`.

**Тексты.** Комментарии, docstring, ошибки, журнал и документация - по-русски, английский - для идентификаторов и API. Типографика: дефис с пробелами " - " вместо тире; прямые кавычки "так", подписи интерфейса тоже: "Проверка по рекламе"; пометка в скобках без пробела: слово(пометка); диапазон через дефис без пробелов: 25-59.

Комментарий объясняет, почему код устроен так, и при необходимости ссылается на модуль или раздел `docs/`. Номера задач рабочего плана команды(`docs/PLAN.md`) и ссылки на документы вне репозитория в коде не используются: они не описывают систему.

**Интерфейс.** Подписи - через i18n: `lib/i18n/ru.ts`(основной словарь, по нему типы ключей) и `lib/i18n/kk.ts`, одинаковые ключи и подстановки проверяет `lib/i18n.test.ts`. Имена переменных, пути и команды интерфейс не показывает: тексты сервера проходят `lib/plain.ts`, он подставляет название переменной из `shared/env_labels.json`.

**Новая переменная окружения** - `_Var` в `settings.VARS` и поле класса `Settings`, пустая строка в `.env.example`, название в `shared/env_labels.json`(строку в `.env.example` и название проверяет `test_settings.py`), строка в [DEPLOYMENT.md, "Переменные окружения"](DEPLOYMENT.md#переменные-окружения). Значения по умолчанию и границы - только в `settings.py`.

## Шаг схемы БД

Схема обновляется на месте шагами `backend/app/migrate.py`, версия - `models.SCHEMA_VERSION`; устройство - [ARCHITECTURE.md, "Схема БД и её обновление"](ARCHITECTURE.md#схема-бд-и-её-обновление).

1. Изменить `models.py` и поднять `SCHEMA_VERSION`(имя вида `2026.10-<область>-<n>`, не из `migrate.RETIRED`).
2. Функция шага и запись `(имя, функция)` в конце `migrate.STEPS`. Последний шаг обязан совпадать с `SCHEMA_VERSION`, иначе импорт `migrate` падает.
3. Шаг идемпотентен и только добавляет: таблицы(`create(conn, checkfirst=True)`), nullable-столбцы или столбцы с константным `server_default`(`add_column`), снятие `NOT NULL`(`relax_not_null`, на SQLite - пересборка таблицы в транзакции шага). Перевод прежних строк в новое значение - единственное допустимое изменение данных, он регистрируется в `STEP_DATA`.
4. Таблицы шага - в `STEP_TABLES`: стартовый набор прежней версии загружается с ними пустыми, переводы `STEP_DATA` выполняются после вставки его строк.
5. Невосстановимые данные(собранное, решения людей, оплаченные ответы ИИ) - в `DURABLE_TABLES` или `DURABLE_WHERE`: при неизвестной версии схемы таблицы пересоздаются, только если таких данных в базе нет.
6. Новая таблица - в `snapshot.TABLES` или в `snapshot.EXCLUDED` с причиной; `test_snapshot.py` требует ровно одно из двух.
7. Тест в `test_migrate.py`: база прежней версии -> `init_db` -> новые объекты на месте, данные сохранены, повтор шага безопасен, на SQLite есть резервная копия. Новую таблицу добавить в `_to_baseline`. Прогнать на SQLite и PostgreSQL.

## Новое подключение

Как устроены подключения - [INTEGRATIONS.md](INTEGRATIONS.md).

1. **Модуль** `backend/app/integrations/<модуль>.py` с `CONNECTOR` - экземпляром класса на базе `base.Connector`, его `code` совпадает с кодом в реестре. Атрибуты класса описывают подключение(`code`, `kind`, `group`, `name`, `via`, `platform`, `required_env`, `config_fields`, `capabilities`, `max_attempts`, `paid`), работу делают `check(ctx)` и `collect(ctx)`. Свои маршруты модуль отдаёт в `router`(`APIRouter`), реестр подключает его сам. Сервис(`service`) импортируется только внутри функций: реестр загружается при импорте `service`, ранний импорт дал бы цикл.
2. **Ошибки** - исключения `base.py`(`NotConfigured`, `AuthExpired`, `AccessDenied`, `AccessBlocked`, `RateLimited`, `BadConfig`, `SourceError`, отсрочка `Deferred`) определяют статус: сбой не выглядит пустой выдачей. Защита сайтов не обходится: CAPTCHA и проверка доступа - `AccessBlocked`.
3. **Запись** - только `ctx.ingest(Publication(...))`: единый приём(`ingest.py`, `ads_service.py`) дедуплицирует, ведёт версии и не берёт решений аналитика из внешних данных. Файлы - `ctx.download_media`, `ctx.store_media`; API источника - `ctx.client()`; позиция сбора - `ctx.save_state`; квоты - `ctx.reserve_quota`.
4. **Регистрация** - `registry.MODULES`(порядок - порядок на странице), `ConnectorCode`, `_FALLBACK`. Модуль, который не загрузился, становится `BrokenConnector`, остальные работают. `smoke.py` берёт подключения из реестра.
5. **Настройки** - по правилу "Новая переменная окружения" раздела "Стиль кода и текстов".
6. **Фронтенд** - `ConnectorCode` в `src/data/types.ts`, значок - `iconPlatform`, поля настройки - `CONFIG_META` в `lib/connectors.ts`. Новая площадка - `Platform` и `lib/platform.ts`, на бэкенде - `schemas.IntakePlatform`, `pdf.PLATFORMS`, `gallery.PLATFORMS`.
7. **Тест** `test_integration_<код>.py` без сети с проверками `accept`: повтор без дублей(`accept.snapshot()`), правка - новая версия, ошибка видна(`assert_failure_visible`), нет автоподтверждения(`assert_not_confirmed`), файл отдаёт `/api/media/{id}`(`media_ok`), секретов нет в ответах и журналах.
8. **Документация** - раздел в [INTEGRATIONS.md](INTEGRATIONS.md).

## Изменение модели риска

Модель описана в `shared/risk_model.json`: Python читает его через `risk_model.py`, фронтенд импортирует в `lib/risk.ts`. Описание - [RISK_MODEL.md](RISK_MODEL.md), паритет - [RISK_MODEL.md, "Реализация и паритет"](RISK_MODEL.md#реализация-и-паритет).

1. `shared/risk_model.json`; при изменении правил - новая `version`(она попадает в ответ риска как `modelVersion` и в эталон).
2. `backend/app/risk.py` - эталонная реализация, затем `frontend/src/lib/risk.ts`.
3. Случай на каждую новую ветку правил - `shared/testdata/risk_fixtures.json`.
4. `make golden`, затем `make test`: в `test_risk.py` ожидания записаны явно, `lib/risk.test.ts` требует ровно эталон.
5. `make snapshot-views` - пересчитать офлайн-ответы(иначе падает `test_offline_views_match_api`), затем `make e2e`.
6. Ссылки факторов на нормы - поле `law` в `risk_model.json` и `shared/law.json`, их проверяют `test_law.py` и `lib/law.test.ts`.
7. [RISK_MODEL.md](RISK_MODEL.md) и, если изменились баллы сценария, README.

"Проверка по рекламе" меняется так же парой `backend/app/screening.py` и `frontend/src/lib/screening.ts` с тестами `test_discovery.py`, `test_decisions.py`, `lib/screening.test.ts` и `make snapshot-views`. Правила - [RISK_MODEL.md, "Проверка по рекламе"](RISK_MODEL.md#проверка-по-рекламе).

## Изменение промпта Claude

Промпт, схема ответа(`SCHEMA`) и его серверная проверка(`Output`) - в `backend/app/llm.py`; поведение - [AI.md, "Анализ Claude"](AI.md#анализ-claude).

1. Любое изменение промпта или схемы поднимает `llm.PROMPT_VERSION`: ответ переиспользуется по хешу материала, модели и версии промпта.
2. Новая версия запускает дозапрос: воркер отправляет собранные публикации без ответа по этой версии партиями(`ANTHROPIC_BACKFILL_BATCH` раз в `ANTHROPIC_BACKFILL_INTERVAL_MIN`), новые первыми, в пределах дневного бюджета. Ответы прежних версий хранятся строками истории и показываются, пока нет нового. Смена `ANTHROPIC_MODEL` дозапроса не вызывает.
3. Новое поле - в `SCHEMA` и `Output` вместе; сервер проверяет его независимо от модели(цитата дословно из материала, ЖК из отправленного списка), не прошедшее проверку отбрасывается. Отображение - `lib/llm.ts`, тип - `data/types.ts`.
4. Тесты без сети: `test_llm.py`(форма запроса, проверка ответа, бюджет, дозапрос) и `lib/llm.test.ts`. Описание - [AI.md](AI.md).

Стадия стройки по снимку(`backend/app/satstage.py`) ведёт свою `PROMPT_VERSION`: те же снимки той же версией промпта повторно не отправляются.

## Перегенерация данных

Что это за данные - [DATA.md](DATA.md). Все файлы ниже хранятся в Git.

| Данные | Команда |
|---|---|
| эталон риска `shared/testdata/risk_golden.json` | `make golden` |
| стартовый набор `shared/snapshot/` | `make snapshot-export` или `make snapshot-export-docker` - [DATA.md, "Загрузка и обновление стартового набора"](DATA.md#загрузка-и-обновление-стартового-набора) |
| офлайн-ответы `shared/snapshot/api/*.json` | `make snapshot-views` после правки риска или "Проверки по рекламе": виды из `snapshot.VIEWS` на дату `api.asOf` манифеста; `media.json` и список `api.files` манифеста обновляет только полная выгрузка |
| открытый реестр `shared/registry_ust_kamenogorsk.json` | импортёр `app.importer` - [DATA.md, "Открытые реестры"](DATA.md#открытые-реестры) |
| индекс снимков `frontend/public/wayback-index.json` | `frontend/scripts/wayback-index.mjs` при работающем API, нужна сеть - [DEPLOYMENT.md, "Служебные команды"](DEPLOYMENT.md#служебные-команды) |
| стадии по снимкам `shared/sat_stage.json` | `app.satstage`, нужен `ANTHROPIC_API_KEY`, запросы платные - [AI.md, "Стадия стройки по спутниковому снимку"](AI.md#стадия-стройки-по-спутниковому-снимку) |

Не коммитятся(`.gitignore`): базы SQLite и их резервные копии, `backend/data/`(файлы публикаций, сессия Telegram), `.env` кроме `.env.example`, окружения, `node_modules`, `frontend/dist`. После перевыгрузки набора - `make test` и `make e2e`.

## Документация

Каждая тема описана в одном документе, остальные ссылаются на его раздел: [README](../README.md) - вход и сценарий; [ARCHITECTURE](ARCHITECTURE.md) - устройство; [FEATURES](FEATURES.md) - экраны; [RISK_MODEL](RISK_MODEL.md) - модель риска и "Проверка по рекламе"; [AI](AI.md) - ИИ; [DATA](DATA.md) - наборы данных; [INTEGRATIONS](INTEGRATIONS.md) - подключения; [API](API.md) - маршруты; [DEPLOYMENT](DEPLOYMENT.md) - запуск, Makefile и переменные; [SECURITY](SECURITY.md) - безопасность; этот документ - разработка; [frontend/README](../frontend/README.md) - интерфейс; [CREDITS](../CREDITS.md) - лицензии.

- Система описывается такой, какая она есть, в настоящем времени: без истории правок, номеров задач и счётчиков тестов. Замеры и проверки вживую привязаны к стартовому набору или к дате проверки. Число тестов показывает `make check`, состояние подключений - страница `/sources` и `python -m app.integrations.smoke`.
- Изменение поведения правит документ-владелец в том же коммите, что и код.
- Утверждения сверяются с кодом: значения по умолчанию - `settings.py`, маршруты - роутеры и OpenAPI на `/docs`, числа о данных - `shared/snapshot/manifest.json`. При расхождении правится текст.
- Типографика - как в разделе "Стиль кода и текстов". Заголовки - без кавычек, скобок, "§" и "/": на их якоря ссылаются другие документы. Ссылки относительные. В начале документа - 2-3 предложения о том, что внутри и для кого.
