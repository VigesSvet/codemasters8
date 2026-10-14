# SAQ Qurylys

Цифровой помощник уполномоченного органа (ДЭР / ГАСК ВКО) для мониторинга рекламы долевого строительства и риск-скоринга жилых комплексов Усть-Каменогорска. Codemasters 8.0.

> ИИ формирует рекомендацию, решение о нарушении принимает аналитик. Все ЖК, застройщики, БИН и объявления в демо-наборе **вымышлены**.

## Экраны

Интерфейс собран на IBM Carbon Design System (`@carbon/react`, иконки `@carbon/icons-react`, шрифт IBM Plex). Паттерны взяты у реальных сервисов:

| Путь | Экран | Образец |
|---|---|---|
| `/login` | Вход сотрудника | главная SIERA (тонкая линейка с датой, знак по центру, фото) |
| `/` | Карта объектов: поиск, фильтр по зонам, карточка объекта | 2ГИС |
| `/ops` | Оперативная сводка: индикаторы, список, карта, графики | ArcGIS Dashboards |
| `/complexes` | Реестр ЖК: таблица или карточки, фильтры-чипы | IBM Carbon Data table, Linear filters, Korter |
| `/complexes/:id` | Карточка ЖК: галерея, этапы, основания, риск R1–R6 | страница ЖК Korter / BS Holding |
| `/ads` | Мониторинг рекламы: список по статусам или карточки | список задач Linear |
| `/ads/:id` | Разбор публикации: текст, OCR, сигналы, активность, свойства, решение | карточка задачи Linear |
| `/ads/new` | Ручное добавление + OCR скриншота | формы Carbon |
| `/queue` | Очередь проверки: доска с drag-and-drop | доска Linear |
| `/developers` | Застройщики (раскрываемые строки) | Carbon Data table |
| `/sites` | Фото-мониторинг (доп. модуль) | — |
| `/method`, `/sources`, `/reports`, `/residents` | Методика, источники, отчёты, идея push жителям | Carbon |

Тема: светлая (Carbon g10) или тёмная (g100) — кнопка в шапке.

## Запуск

### Docker (Postgres + API + фронт)

```bash
docker compose up --build
# фронт  http://localhost:5180
# API    http://localhost:8000/docs
```

### Локально одной командой

```bash
./dev.sh        # API :8000 (SQLite) + фронт :5180
```

Сброс демо-данных: удалить `backend/saq.db` и перезапустить.

### Тесты

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npx tsc -b   # проверка типов
```

### Локально вручную

```bash
# бэкенд (без Postgres можно на SQLite)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DATABASE_URL=sqlite:///./saq.db .venv/bin/uvicorn app.main:app --reload --port 8000

# фронтенд
cd frontend
npm install
npm run dev            # http://localhost:5180, /api проксируется на :8000
```

Фронтенд работает и без бэкенда, на встроенном демо-наборе (`frontend/src/data/demo.ts`).

## Скоринг

| Код | Фактор | Баллы |
|---|---|---|
| R1 | Нет основания для привлечения средств (гарантия ЕО / МИО + БВУ) | 35 |
| R2 | Реклама продажи на стадии строительства | 30 |
| R3 | Нет в опубликованном перечне ГАСК | 10 |
| R4 | Простой стройки по снимкам (30+ / 90+ дней) | 5 / 10 |
| R5 | Признаки сбора денег в тексте (бронь, взнос, ДДУ…) | 10 |
| R6 | Профиль застройщика | 10 |

`red` — score ≥ 60 **и** подтверждены R1 + R2; `yellow` — score ≥ 25; иначе `green`. Нет сведений → 0 баллов и «Нужна проверка».

## Стек

React 19 + TypeScript + Vite, IBM Carbon (`@carbon/react`), MapLibre GL (подложка CARTO / OpenStreetMap) · FastAPI, SQLAlchemy 2, PostgreSQL 16 · Tesseract OCR (rus/kaz) в Docker-образе.

Шрифты: IBM Plex Sans / Mono, Cormorant Garamond (OFL, через @fontsource). Carbon и иконки Carbon — Apache-2.0. Фото: Wikimedia Commons — см. [CREDITS.md](CREDITS.md).
