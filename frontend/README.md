# Интерфейс SAQ Qurylys

Веб-интерфейс SAQ Qurylys на React 19, TypeScript и Vite: экраны аналитика, руководителя, специалиста ГАСК и публичная проверка ЖК. Данные приходят из API бэкенда(`/api`), модель риска, нормативная база и справочники берутся из общего каталога [`shared/`](../shared). Если API не ответил при запуске, интерфейс открывает офлайн-копию стартового набора только для чтения(`shared/snapshot/api/*.json`).

## Содержимое

| Путь | Содержимое |
|---|---|
| `src/App.tsx`, `src/main.tsx` | точка входа и маршруты |
| `src/pages/` | экраны |
| `src/components/` | общие блоки и UI-кит(`ui.tsx`) |
| `src/lib/` | данные и режим работы(`store.tsx`), запросы к API(`api.ts`), порт модели риска(`risk.ts`), логика экранов; рядом - тесты vitest |
| `src/data/` | типы данных API, офлайн-копия стартового набора и открытого реестра |
| `src/styles/` | токены и компоненты UI-кита(`go.css`), стили экранов |
| `public/` | шрифты, логотипы, изображения, индекс снимков Esri Wayback(`wayback-index.json`, строит `scripts/wayback-index.mjs`) |
| `vite.config.ts` | порт 5180, прокси `/api`, фильтр клиентов dev-сервера, отдача `/snapshot-media/` из `shared/snapshot/media` |
| `.env.example` | переменные сборки(`VITE_API_URL`) и переменные окружения dev-сервера |

## Запуск

Нужен Node 22.12+ или 24(этого требует vitest 5); без Node цели `make` для фронтенда выполняются в контейнере `node:22-slim`.

Из корня репозитория: `make dev`(API на :8000, воркер и интерфейс на http://localhost:5180) или `make up`(Docker). Только интерфейс: `npm ci`, затем `npm run dev` - dev-сервер слушает порт 5180 и проксирует `/api` на `http://localhost:8000`(другой адрес - переменная окружения `API_PROXY`). Без запущенного API интерфейс работает на офлайн-копии.

Dev-сервер принимает соединения только с этой машины и с адресов из `SAQ_ALLOWED_CLIENTS`, имена хоста кроме localhost и IP - из `SAQ_WEB_HOSTS`. `VITE_API_URL`(по умолчанию `/api`) задаёт адрес API при сборке и нужен, только если интерфейс и API на разных доменах. Пример и пояснения - [`.env.example`](.env.example).

## Проверки

`make lint-frontend typecheck test-frontend build` или напрямую `npx oxlint src`, `npx tsc -b`, `npm test`(`vitest run`), `npx vite build`(результат - `dist/`).

## Документация

Устройство интерфейса и режимы работы - [ARCHITECTURE.md, "Фронтенд"](../docs/ARCHITECTURE.md#фронтенд) и ["Офлайн-режим"](../docs/ARCHITECTURE.md#офлайн-режим); экраны и сценарии - [FEATURES.md](../docs/FEATURES.md); окружение, тесты и стиль кода - [DEVELOPMENT.md](../docs/DEVELOPMENT.md); запуск целиком и сетевой доступ - [DEPLOYMENT.md](../docs/DEPLOYMENT.md); лицензии шрифтов и иконок - [CREDITS.md](../CREDITS.md).
