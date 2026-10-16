# План до полного завершения SAQ Qurylys

Сверка ТЗ (`Кейс Codemasters 8.0. SAQ (1).pdf`, редакция от 15.10.2026) с состоянием репозитория
на коммите `bf03615` (15.10.2026); строки 01–05, раздел 2 и раздел 3 обновлены после блоков A, B, C, D, E и J.
Отсечка кода — 20.10.2026 23:59, защита — 21.10.2026 15:00.

## 1. Что уже готово

| Модуль ТЗ §3 | Состояние | Чего не хватает |
|---|---|---|
| 01 Карта / обзор | готово; слой «Открытые данные» — реальные объекты серыми метками без балла (блок E) | — (риск из `/api/risk`, локальный расчёт только офлайн — T-7) |
| 02 Реестр ЖК | ✅ готово; источник и время фиксации сведений — в журнале объекта (блок C); у каждого документа основания — источник, дата сверки и ссылка, реальные объекты Усть-Каменогорска — вкладка «Открытые данные» без оценки риска (блок E) | — |
| 03 Мониторинг рекламы | ✅ готово; журнал публикации — из API, решения и комментарии с автором (блок C); OCR скриншота на сервере (Tesseract), анализ ИИ — условия оплаты и гипотезы ЖК, принятие и отклонение аналитиком (блок D); сбор публикаций подключениями, приём заявок бота и публикаций по API, воркер с анализом (блок J) | живой сбор Instagram, Telegram-каналов и TikTok — после токенов владельца (блок J) |
| 04 Риск-скоринг | ✅ готово по §5 (блок A); привязка к ЖК — гипотеза ИИ до подтверждения аналитиком, вето R2 решением аналитика по условиям оплаты (блок D) | — |
| 05 Сигнальная карточка | ✅ готово (блоки B, C) | — (автор — вошедший аналитик, события сигнала — в журнале ЖК) |

Прочее готовое: FastAPI + SQLAlchemy + Postgres/SQLite, 14 ЖК / 12 публикаций / 7 застройщиков в сиде,
журнал событий по публикациям в БД, ручное добавление публикации с автопривязкой ЖК, `/api/ocr` на Tesseract,
15 экранов фронта, docker-compose и `dev.sh`, один сквозной тест API.

## 2. Расхождения, которые стоят баллов

1. ✅ **Закрыто блоком A.** **Модель риска не та.** В коде: R1 35 нет основания, R2 30 реклама продажи, R3 10 нет в перечне ГАСК,
   R4 5/10 простой, R5 10 признаки сбора денег, R6 10 профиль застройщика.
   В ТЗ: R1 35, R2 25 прямое предложение внести деньги, R3 10 активная реклама, R4 15 подтверждённое
   коллективное обращение, R5 10 несовпадение готовности от 20 п.п., R6 5 простой/просрочка.
   Контрольные кейсы ТЗ (35 / 70 / 10) на текущей модели не воспроизводятся.
   Сейчас: модель §5 в `shared/risk_model.json`, кейсы A/B/C воспроизводятся на сиде (c14 / c2 / c10).
2. ✅ **Закрыто блоком B.** **Нет модуля 05.** «Создать кейс проверки», «Акт фиксации», «Скачать PDF» — тосты.
   Сейчас: сущность `Signal`, API `/api/signals*`, экраны `/signals`, PDF и печатная версия A4.
3. ✅ **Закрыто блоком D.** **ИИ-сценарий слабый.** Только словари ключевых слов; нет гипотезы с уверенностью и фрагментом,
   нет принятия/отклонения результата аналитиком, OCR из UI не вызывается.
   Сейчас: офлайн-движок `backend/app/ai/` — OCR (Tesseract), условия оплаты и гипотезы ЖК с цитатой и уверенностью;
   аналитик принимает или отклоняет, решения — в журнале; проверка на 16 новых текстах `shared/ai_cases.json`.
4. **Репозиторий приватный** (аноним получает 404) — при недоступном GitHub это 0 из 20 за код.
5. **В репозитории лежит запись встречи** 42 МБ (`docs/14.10.2026 15.32.m4a`) и полная расшифровка —
   публиковать голоса представителей заказчика нельзя.
6. **Нет `.env.example`**, README не покрывает разделы §8 (архитектура, тестовый сценарий, ограничения,
   использование ИИ), схемы архитектуры нет нигде.
   Сейчас: `.env.example` бэкенда с пустыми значениями — блок J (T-59); README по §8 и схема архитектуры — T-37, T-38.
7. ✅ **Закрыто блоком E.** **Реальные данные не подключены.** `Усть-Каменогорск_объекты.xlsx` — 103 записи Homeportal / Казреестра /
   Ashyq Data с документами, БИН, координатами и датой сбора — в приложении не используются.
   Сейчас: импортёр `backend/app/importer.py` → `shared/registry_ust_kamenogorsk.json` (103 записи, 15 объектов) → таблица
   `source_records`; слой «Открытые данные» в реестре и на карте, импорт xlsx/csv на `/sources`; риск, зоны и сигналы для
   реальных объектов не считаются.
8. ✅ **Закрыто блоком A.** Логика риска продублирована на Python и TypeScript — разойдутся при первой же правке.
   Сейчас: одно описание `shared/risk_model.json`, TS-порт сверяется с эталоном Python по `shared/risk_golden.json` (`npm test`).

## 3. Задачи

Приоритеты: **P0** — без этого теряем баллы по §5/§8, **P1** — вес в критериях, **P2** — креатив.

Покрытие разделов ТЗ: §3 и §5 — блоки A, B, D, E; §4 — блоки E, J; §6 — блоки D, J; §7 — блоки C, E, F, G;
§8 — блок G; §9 — блок I.

### A. Риск-модель по §5

- ✅ **T-1** (P0) Единый источник правды по модели: коды, названия, веса, условия, тексты пояснений в одном модуле — сделано: `shared/risk_model.json`, загрузка — `backend/app/risk_model.py`, фронт импортирует тот же JSON; `GET /api/risk/model`.
- ✅ **T-2** (P0) Переписать расчёт ЖК: R1 35 / R2 25 / R3 10 / R4 15 / R5 10 / R6 5, балл = min(100, сумма подтверждённых), неизвестное → 0 и «Нужна проверка» — сделано: `backend/app/risk.py` (эталон), порт — `frontend/src/lib/risk.ts`.
- ✅ **T-3** (P0) Данные под R4: сущность «Обращение» (ЖК, дата, статус проверки, источник) + записи в сиде — сделано: модель `Appeal`, `appeals[]` в `shared/seed_data.json`, `GET /api/appeals`.
- ✅ **T-4** (P0) Данные под R5: заявленная и подтверждённая готовность, у каждой источник и дата; правило «разница от 20 п.п.» — сделано: `complexes[].readiness` (`declared` / `confirmed` с источником и датой) в сиде, правило R5 — в `risk.py`.
- ✅ **T-5** (P1) Нынешние «нет в перечне ГАСК» и «профиль застройщика» вынести в блок дополнительных наблюдений вне балла, с пояснением в методике — сделано: наблюдения O1–O3 в ответе риска, вне балла.
- ✅ **T-6** (P0) Тесты контрольных кейсов A/B/C (35 жёлтая, 70 красная, 10 зелёная) и границ зон 24/25/59/60, правило «красная только при R1+R2» — сделано: `backend/tests/test_risk.py`, `shared/risk_fixtures.json`, `frontend/src/lib/risk.test.ts`.
- ✅ **T-7** (P1) Фронт берёт риск из `/api/complexes/{id}/risk`; локальный расчёт остаётся только офлайн-фоллбэком из того же описания модели — сделано: стор берёт `/api/risk`; офлайн — TS-порт, паритет по `shared/risk_golden.json`.
- ✅ **T-8** (P1) Обновить экран «Методика» и раздел «Скоринг» в README под новую модель — сделано: `frontend/src/pages/Method.tsx`, раздел «Скоринг» в README.

### B. Сигнальная карточка (модуль 05)

- ✅ **T-9** (P0) Модель `Signal`: объект, балл, снимок факторов, доказательства, дата создания, автор, статус, файл выгрузки — сделано: `models.Signal` в `backend/app/models.py`.
- ✅ **T-10** (P0) API сигналов: создание, список, карточка, `confirm` / `return` с автором, комментарием и временем, запись в журнал — сделано: `backend/app/signals.py`, `backend/tests/test_signals.py`.
- ✅ **T-11** (P0) Снимок доказательств при создании: факторы с цитатами, связанные публикации, документы-основания, снимки стройки — сделано: `signals.snapshot()` — снимок фиксируется при создании и не пересчитывается.
- ✅ **T-12** (P0) Кнопка «Создать сигнал» в карточке ЖК ведёт на созданный сигнал (было: тост) — сделано: `frontend/src/components/SignalPanel.tsx` в карточке ЖК.
- ✅ **T-13** (P0) Экран сигнала: состав, «Подтвердить» / «Вернуть на доработку» с обязательным комментарием, журнал решений — сделано: `frontend/src/pages/SignalCard.tsx`.
- ✅ **T-14** (P1) Реестр сигналов `/signals`, связь с очередью и оперативной сводкой — сделано: `frontend/src/pages/Signals.tsx`, переходы из очереди и оперативной сводки.
- ✅ **T-15** (P0) Выгрузка: печатная версия A4 (print-CSS) и скачиваемый PDF вместо тоста — сделано: `backend/app/pdf.py` (reportlab + Noto Sans), `frontend/src/pages/SignalPrint.tsx`.

### C. Персистентность, журнал, ошибки

- ✅ **T-16** (P1) Журнал публикации тянуть из `/api/ads/{id}/audit`, а не из демо-массива и локальных заметок — сделано: «История» в `frontend/src/pages/AdDetail.tsx` из `GET /api/ads/{id}/audit`, комментарии — `POST /api/ads/{id}/comments`, решения с основанием — `PATCH /api/ads/{id}/status` (`backend/app/main.py`, `backend/app/schemas.py`); демо-массив `audit` убран из `frontend/src/data/demo.ts`, исходная история — `audit` в `shared/seed_data.json`, засев журнала — `backend/app/seed.py`; тесты — `backend/tests/test_audit.py`.
- ✅ **T-17** (P1) События по ЖК и сигналу в `AuditEvent`, показ в карточке объекта — сделано: у `AuditEvent` вид `kind` и ссылки на ЖК и сигнал (`backend/app/models.py`), `GET /api/complexes/{id}/audit`, `GET /api/audit`, `POST /api/complexes/{id}/requests` (`backend/app/main.py`); вкладка «Журнал» и «Запросить сведения» — `frontend/src/pages/ComplexCard.tsx`, «Последние действия» — `frontend/src/pages/Ops.tsx`, список событий — `frontend/src/components/Audit.tsx`, `frontend/src/lib/audit.ts`.
- ✅ **T-18** (P1) Индикатор режима «данные из API» / «демо без бэкенда» и баннер при ошибке запроса вместо молчаливого `catch` — сделано: `mode`, `apiError`, `reconnect`, `lastSync` в `frontend/src/lib/store.tsx`, индикатор и баннер — `frontend/src/components/Shell.tsx`, перевод ошибок 422 — `frontend/src/lib/api.ts` (`api.test.ts`), 500 в JSON — `backend/app/main.py`.
- ✅ **T-19** (P1) Автор действий — вход аналитика вместо зашитого «Нестерова А.»; имя попадает в журнал и в сигнал — сделано: `frontend/src/lib/session.ts` (`session.test.ts`), `frontend/src/pages/Login.tsx`, вход обязателен — `frontend/src/App.tsx`, `GET /api/analysts` (`analysts` в сиде, `models.Analyst`); `author` обязателен в `PATCH /api/ads/{id}/status`.

### D. ИИ-сценарий (§6)

- ✅ **T-20** (P0) Настоящий OCR из интерфейса: `POST /api/ocr`, показ движка и ошибки, убрать таймер-заглушку — сделано: `backend/app/ai/ocr.py` (Tesseract rus+kaz+eng, предобработка Pillow, 413/415/422, без Tesseract — 200 с `engine: none`), `POST /api/ocr` и `GET /api/ocr/status` в `backend/app/main.py`, `pillow` в `backend/requirements.txt`, `backend/Dockerfile`; загрузка, индикатор, движок, уверенность, время и ошибки, правка распознанного текста — `frontend/src/pages/AdNew.tsx`, поле OCR при смене скриншота — `frontend/src/lib/ai.ts`, `ocr` / `ocrStatus` в `frontend/src/lib/store.tsx`; тесты — `backend/tests/test_ai.py`, `frontend/src/lib/ai.test.ts`.
- ✅ **T-21** (P0) Извлечение условий оплаты: бронь, первый взнос, рассрочка, сумма и валюта → структура + цитата-фрагмент + уверенность — сделано: `backend/app/ai/terms.py` (8 видов условий, суммы, валюты, цена за м², проценты, сроки, дословная цитата, источник `text`/`ocr`, уверенность и `low`), нормализация — `backend/app/ai/nlp.py`, `POST /api/analyze`; живой анализ в `frontend/src/pages/AdNew.tsx`, `TermList` — `frontend/src/components/Hypothesis.tsx`.
- ✅ **T-22** (P0) Сопоставление публикации с ЖК: нормализация названий, латиница/кириллица, адрес, аккаунт, телефон; три гипотезы с уверенностью вместо поиска подстроки — сделано: `backend/app/ai/matching.py` (название с транслитерацией KZ/RU/латиница и нечётким сходством, адрес, аккаунт и сайт застройщика, телефон, история аккаунта, район; подборки снижают уверенность), справочник — `world.catalog()` в `backend/app/world.py`, `POST /api/ads` без `complexId` берёт лучшую гипотезу как привязку-гипотезу. Правило «привязка — факт только после подтверждения аналитиком»: `linkConfirmedBy` / `linkConfirmedAt` (`backend/app/models.py`, `shared/seed_data.json`), `link_confirmed()` в `backend/app/risk.py` и `frontend/src/lib/risk.ts`; пороги `lowConfidence` / `hypothesisMin` / `hypothesesTop` вместо `linkConfirmed` / `autoLinkConfidence` в `shared/risk_model.json`.
- ✅ **T-23** (P0) UI гипотез: бейдж «гипотеза ИИ», уверенность, «Принять» / «Отклонить», решение в журнал, низкая уверенность помечается — сделано: карточка «Анализ ИИ» в `frontend/src/pages/AdDetail.tsx`, `AiBadge` / `Confidence` / `ReasonList` / `TermList` — `frontend/src/components/Hypothesis.tsx`, `frontend/src/styles/ai.css`, решения в сторе — `frontend/src/lib/ai.ts`, `frontend/src/lib/store.tsx` (тесты — `frontend/src/lib/ai.test.ts`, `frontend/src/components/Hypothesis.test.ts`); API — `GET /api/ads/{id}/analysis`, `POST /api/ads/{id}/hypotheses/accept|reject`, `POST /api/ads/{id}/terms-review` (`backend/app/main.py`, `backend/app/schemas.py`), события журнала вида `ai`; вето R2 решением «Это не предложение денег» — `backend/app/risk.py`, `frontend/src/lib/risk.ts`; новое правило привязки в текстах — `SignalCard.tsx`, `SignalPrint.tsx`, `Ads.tsx`, `ComplexCard.tsx`, `Sources.tsx`, `backend/app/pdf.py`; раздел «ИИ-анализ публикаций» — `frontend/src/pages/Method.tsx`.
- ✅ **T-24** (P1) Объяснение риска с привязкой к фрагментам: у каждого подтверждённого фактора конкретная цитата, документ или снимок с датой — сделано: доказательства с источником и датой у факторов — с блока A (`backend/app/risk.py`); у цитаты публикации — источник `quoteFrom` (`text` / `ocr`), пометка «из скриншота (OCR)» — `frontend/src/components/Factors.tsx`, `frontend/src/pages/SignalPrint.tsx`, `backend/app/pdf.py`; фикстуры — `shared/risk_fixtures.json`, тесты — `backend/tests/test_risk.py`, `frontend/src/lib/risk.test.ts`.
- ✅ **T-25** (P0) Тест на новых данных: три текста, которых нет в сиде, → ожидаемые признаки и привязка к ЖК — сделано: 26 текстов (вместо трёх, включая три из плана) в `shared/ai_cases.json`, прогон — `backend/app/ai/evaluate.py`, `GET /api/ai/eval`, таблица кейсов и метрики на `/method` (`frontend/src/pages/Method.tsx`), проверка каждого кейса и метрик — `backend/tests/test_ai.py`. Текущий прогон: 26 из 26 кейсов, условий 40 из 40, `termsPrecision` 1,0, `linkTop1` 1,0 (17 кейсов), ложных привязок 0.

### E. Данные и источники (§4)

- ✅ **T-26** (P1) Импортёр `Усть-Каменогорск_объекты.xlsx` в таблицу источников: источник, ссылка, ID записи, дата сбора — сделано: `backend/app/importer.py` (`python -m app.importer <xlsx|csv> [--out …] [--dry-run]`; даты двух форматов → ISO, время сбора → UTC, координаты «0» → нет, БИН — 12 цифр, способ привлечения — код + исходный текст, код источника; провенанс — источник, раздел, ссылка, ID в источнике, время сбора, дата загрузки у поставщика; отчёт — прочитано, пропущено, повторы; группировка в объекты по нормализованному названию), результат — `shared/registry_ust_kamenogorsk.json` (103 записи: Homeportal 16 + 1 из отдельного раздела, Казреестр / Ashyq Data 86; 15 объектов); таблица `source_records` — `models.SourceRecord` в `backend/app/models.py` (схема `2026.10-sources-1`), засев — `registry.seed()` из `backend/app/seed.py`; `GET /api/sources` и `POST /api/sources/import` (xlsx/csv до 5 МБ, сверка по паре «источник + ID», 413/415/422, событие журнала `data`) — `backend/app/registry.py`, предел тела до чтения — `UploadLimit` в `backend/app/main.py`, `openpyxl` — `backend/requirements.txt`; форма импорта с отчётом и честной пометкой «синхронизации с реестрами нет» — `frontend/src/pages/Sources.tsx`; тесты — `backend/tests/test_registry.py`.
- ✅ **T-27** (P1) Реальные объекты Усть-Каменогорска в реестре и на карте; записи без координат — отдельным списком — сделано: `GET /api/registry` (`{objects, withoutCoords}`, поиск `q`) и `GET /api/registry/{key}` — `backend/app/registry.py`, `backend/app/schemas.py`; переключатель «Сценарии (демо) / Открытые данные» (`/complexes?layer=open`) — `frontend/src/pages/Complexes.tsx`, список с разделом «Без координат» — `frontend/src/components/OpenData.tsx`; слой «Открытые данные» на карте — серые метки без балла (`frontend/src/pages/Dashboard.tsx`, `frontend/src/components/MapView.tsx`); карточка объекта со всеми записями источников `/registry/:key` — `frontend/src/pages/RegistryCard.tsx`; офлайн — встроенная копия JSON (`frontend/src/data/openData.ts`), загрузка — `frontend/src/lib/provenance.ts`, `frontend/src/lib/store.tsx`. 12 объектов с координатами (на карте 11: у одного координаты источника в 188 км от города), 3 без.
- ✅ **T-28** (P0) Разметка на уровне поля и бейджа: факт из источника / демонстрационные данные / гипотеза ИИ — сделано: `Origin` — бейдж и вариант «у поля» («Факт из источника», «Демонстрационные данные», «Гипотеза ИИ», «Решение аналитика») — `frontend/src/components/Provenance.tsx`, `frontend/src/styles/provenance.css`; поле `origin` у ЖК, застройщиков, публикаций и обращений (`demo` / `analyst`) и у записей источников (`source`) — `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/main.py`, `shared/seed_data.json`, `frontend/src/data/types.ts`; метки — `Complexes.tsx`, `ComplexCard.tsx`, `Dashboard.tsx`, `Ads.tsx`, `AdDetail.tsx`, `Developers.tsx`, `SignalCard.tsx`, `SignalPrint.tsx`, `RegistryCard.tsx`; подписи «вымышленный ЖК (демо)» / «вымышленная организация (демо)» — и в `backend/app/pdf.py`.
- ✅ **T-29** (P1) У каждого статуса основания — ссылка на источник и время фиксации — сделано: `basis.sources` (`guarantee` / `mio` / `bvu`: источник, ссылка, дата сверки) в `shared/seed_data.json`; R1 «установлено отсутствие» — только при источнике и дате и у гарантии, и у разрешения МИО, документ «нет данных» — без источника и даты: `doc_source()` и `factor_r1()` в `backend/app/risk.py`, порт — `frontend/src/lib/risk.ts`, тексты — `shared/risk_model.json`, фикстуры и эталон — `shared/risk_fixtures.json`, `shared/risk_golden.json`; источник, дата и ссылка на плитке каждого документа, «не проверялся» — `DocSourceLine` в `frontend/src/components/Provenance.tsx`, `frontend/src/pages/ComplexCard.tsx`; в доказательствах R1 — `frontend/src/components/Factors.tsx`, в печатной версии и PDF — `frontend/src/pages/SignalPrint.tsx`, `backend/app/pdf.py` (ссылка кликабельна); тесты — `backend/tests/test_risk.py`, `backend/tests/test_pdf.py`, `frontend/src/lib/risk.test.ts`.
- ✅ **T-30** (P1) Три контрастных сценария A/B/C как явные демо-кейсы с быстрым переходом из методики — сделано: баннер «Демо-сценарий A/B/C из ТЗ §5» с ожидаемой зоной и баллом и отметкой совпадения в карточке ЖК `c14` / `c2` / `c10` — `frontend/src/pages/ComplexCard.tsx`; чипы сценариев в шапке методики, кнопки «Открыть сценарий» у кейсов, якорь `/method#cases` — `frontend/src/pages/Method.tsx`, прокрутка к якорю — `frontend/src/components/Shell.tsx`; блок «Демо-сценарии ТЗ §5» — `frontend/src/pages/Ops.tsx`; кнопки A / B / C «войти и открыть сценарий» — `frontend/src/pages/Login.tsx`. Без новых данных — те же ЖК сида.
- ✅ **T-31** (P0) Проверка §7: сценарии нарушений только на вымышленных ЖК, у реальных записей — никаких обвинений — сделано: у объектов и записей открытых данных нет балла, зоны, «нужна проверка» и сигналов; риск, карточка ЖК и сигнал по ключу объекта → 404 с пояснением — `registry.not_found()` в `backend/app/registry.py`, `backend/app/main.py`, `backend/app/signals.py`; запись раздела Homeportal «Без разрешения на дольщиков» — только атрибутированный факт источника (раздел, ссылка, дата сбора); плашка «риск не оценивается» — `OpenDataNote` в `frontend/src/components/Provenance.tsx`; тесты — `backend/tests/test_registry.py`: `test_no_risk_for_source_records`, `test_source_section_is_attributed_fact`, `test_demo_does_not_match_open_data` (названия демо-ЖК и демо-застройщиков и их БИН не совпадают с открытыми данными); README — разделы «Открытые данные и источники», «Достоверность и безопасность данных».

### F. Интерфейс аналитика (§7)

- ✅ **T-32** (P1) Проход по сквозным сценариям после блоков B и D: быстрый поиск ЖК, читаемость статусов публикации и сигнала, видимость действий «Создать сигнал», «Подтвердить», «Вернуть», «Принять гипотезу», переходы ЖК ↔ публикация ↔ сигнал ↔ очередь; найденное исправить — сделано: аудит семи сценариев в браузере на 1440 и 1280 (API и демо) и исправления: быстрый поиск в шапке по ЖК, открытым данным, публикациям и сигналам с транслитом и без казахских букв (`frontend/src/components/QuickSearch.tsx`, `frontend/src/lib/{search,find}.ts`, клавиша «/»); шапка не уезжает при прокрутке; фильтры реестра и ленты, вкладка карточки ЖК и фильтр очереди — в адресе, «назад» их сохраняет; «← откуда пришёл» в публикации и ЖК (`frontend/src/lib/nav.ts`); кнопки решения в разборе публикации — на первом экране, единые названия («Принять привязку», «Условия верны», «Подтвердить нарушение», «Подтвердить сигнал»); пометка «гипотеза ИИ» в очереди, сводке и заголовке публикации; «Почему N баллов →» в карточке ЖК; понятные «не найдено»; без горизонтальной прокрутки; убраны мёртвые элементы и фальшивые заглушки — выдуманные цифры в сводке, отчётах и «Жителям» считаются из данных или убраны, «Скачать PDF» отчёта — печать страницы, заявки и рассылки, которых нет, неактивны с пояснением; в галерее ЖК — только снимки этого объекта.

### G. Репозиторий и сдача (§8)

- **T-33** (P0) Убрать запись встречи и полную расшифровку из репозитория и вычистить их из истории.
- **T-34** (P0) Одним заходом с T-33: скан всей истории на ключи, токены и настоящие персональные данные; проверить сид и `demo.ts` на реальные ФИО, телефоны и почты; результат зафиксировать в README.
- **T-35** (P0) Сделать репозиторий публичным и проверить анонимный клон — только после T-33 и T-34.
- **T-36** (P0) `.env.example` для бэкенда и фронта, без секретов.
- **T-37** (P0) README по §8: проблема и ценность, возможности, стек, архитектура, запуск, тестовый сценарий, известные ограничения, использование ИИ.
- **T-38** (P1) Схема архитектуры: компоненты данных, анализа и интерфейса, поток «публикация → факторы → балл → сигнал», по абзацу на слой — чтобы команда могла объяснить устройство на защите.
- **T-39** (P1) Скрипт проверки: pytest, `tsc -b`, сборка фронта, быстрый прогон сквозного сценария.
- **T-40** (P1) Ассеты: шрифты YS Text/Display и SVG Яндекса — заменить на свободные либо подтвердить право использования; привести CREDITS.md в соответствие.
- **T-41** (P0) Чистый прогон по README: `docker compose up --build` и `./dev.sh` с нуля.

### H. Дополнительная ценность

- **T-42** (P2) Двуязычный интерфейс RU/KZ хотя бы на ключевых экранах и в сигнальной карточке.
- ✅ **T-43** (P2) Локальная копия публикации (текст + скриншот) на случай удаления оригинала — сделано (блок J): скриншот формы ручного ввода загружается на сервер — `POST /api/media` (`backend/app/integrations/routes.py`, `backend/app/media.py`), `frontend/src/lib/media.ts`, `frontend/src/pages/AdNew.tsx`; файлы собранных публикаций скачиваются при сборе и хранятся на сервере (`MEDIA_ROOT`, `GET /api/media/{id}`), текст и его изменения в источнике — версии `GET /api/ads/{id}/versions`; тесты — `backend/tests/test_media.py`, `frontend/src/lib/media.test.ts`.

### I. Питч и фиксация (§9)

- **T-44** (P0) Сквозной демо-сценарий: новая публикация → OCR → гипотеза → подтверждённый фактор → балл → сигнал → PDF.
- **T-45** (P0) Слайды: проблема, пользователь, ценность, архитектура, где ИИ, развитие после хакатона.
- **T-46** (P1) Резервная запись демонстрации на случай проблем с сетью.
- **T-47** (P0) Фиксация версии для жюри до 20.10 23:59, отправка ссылки, проверка доступности репозитория и демо.

### J. Подключение источников рекламы (§4, план подключения от 16.10.2026)

✅ — сделано и проверено тестами (и живым запуском, где указано); ⏳ — код и тесты готовы, нужен токен или действие владельца аккаунта; ❌ — не сделано. Инструкция — `docs/INTEGRATIONS.md`. Тесты на 17.10.2026: бэкенд 1092 пройдено и 2 пропущено на SQLite, 1093 и 1 на PostgreSQL 16 (до блока — 647); фронтенд — vitest 293, `tsc`, oxlint, сборка.

- ✅ **T-48** (P0) Фундамент: миграции без потери данных, хранилище файлов, единый приём с дедупликацией и версиями, задания и воркер, анализ собранной публикации в очереди — сделано: схема `2026.10-intake-1` с обновлением на месте, копией SQLite и защитой собранных данных (`SAQ_ALLOW_SCHEMA_RESET`) — `backend/app/migrate.py`, `backend/app/seed.py`; хранилище по SHA-256 — `backend/app/media.py`; загрузчик только на публичные адреса — `backend/app/integrations/http.py`; кадры видео — `backend/app/video.py`; единый приём — `backend/app/ingest.py`; общая запись публикации для ручного ввода, сборщиков и бота — `backend/app/ads_service.py`; задания, воркер (пульс, аренда планировщика, `--once`) — `backend/app/jobs.py`, `backend/app/worker.py`; анализ (файлы → кадры → OCR → правила → журнал, привязка к ЖК не назначается) — `backend/app/analysis.py`; настройки, маскирование секретов, токены — `backend/app/settings.py`, `backend/app/logs.py`, `backend/app/auth.py`; тесты — `test_settings.py`, `test_migrate.py`, `test_ads_service.py`, `test_media.py`, `test_intake.py`, `test_worker.py`. В Docker база прошлой схемы обновилась с сохранением данных.
- ✅ **T-49** (P0) API подключений и приёма публикаций — сделано: `GET /api/sources/connectors`, `PATCH /api/sources/{code}`, `POST /api/sources/{code}/test`, `POST /api/sources/{code}/collect`, `POST /api/sources/collect-all`, `GET /api/collection-runs`, `POST /api/intake/publications`, `GET /api/ads/{id}/versions`, `POST /api/media`, `GET /api/media/{id}`, `GET /api/admin/check` — `backend/app/integrations/routes.py`, `service.py`, `registry.py`; живая проверка `python -m app.integrations.smoke` — `smoke.py`; `PublicHostGuard` — `backend/app/main.py`; тесты — `test_connectors_api.py`, `test_intake.py`. В Docker на PostgreSQL: приём → анализ выполнен, повтор → `unchanged`, неверный токен → 403 и отметка в статусе подключения.
- ⏳ **T-50** (P1) Instagram через Meta Graph API · Business Discovery: список аккаунтов, файлы, обход страниц, срок токена — код и тесты: `backend/app/integrations/instagram.py`, `backend/tests/test_integration_instagram.py`; нужен токен Meta (`META_USER_ACCESS_TOKEN`) — живого сбора не было.
- ⏳ **T-51** (P1) Telegram-каналы через MTProto (Telethon): сессия, позиция по каналу, альбомы, правки, FloodWait — код и тесты: `backend/app/integrations/telegram.py`, `telegram_login.py` (`--send-code`, `--code`, `--password`, `--status`), `backend/tests/test_integration_telegram.py`; `TG_API_ID` / `TG_API_HASH` заданы, нужен однократный вход по коду, который получит владелец аккаунта.
- ⏳ **T-52** (P1) Приёмник Telegram-бота заявок — код и тесты: `backend/app/integrations/telegram_bot.py` (`POST /api/intake/telegram`, статус заявки, изменения статусов; данные заявителя отдельно от публикации), `backend/tests/test_integration_telegram_bot.py`; статус «ожидает заявок»; контракт с адаптером `saq-telegram-bot` не сверен — нужен проект бота.
- ⏳ **T-53** (P1) TikTok через Apify: запуск, опрос, Dataset, файлы из хранилища Apify, лимиты расходов, остановка после ошибки, без Apify Schedule — код и тесты: `backend/app/integrations/tiktok.py`, `tiktok_input.json`, `backend/tests/test_integration_tiktok.py`; нужен `APIFY_TOKEN`; скачивание MP4 с токеном вживую не проверено.
- ✅ **T-54** (P1) 2ГИС — кандидаты адреса, квота и кэш, выбор аналитика, перенос координат только явным решением — сделано: `backend/app/integrations/dgis.py` (`/api/geo/*`), `frontend/src/components/GeoCheck.tsx` в `ComplexCard.tsx` и `RegistryCard.tsx`, `frontend/src/lib/geo.ts`; тесты — `test_integration_dgis.py`, `geo.test.ts`; живой запрос «Увалиева 8/3» — 2 кандидата (точный дом и «8/3а»). Подложка карты 2ГИС в `MapView.tsx` не подключалась.
- ✅ **T-55** (P1) Krisha.kz — выдача и карточки по HTML, robots.txt, паузы, явная ошибка при проверке доступа — сделано: `backend/app/integrations/krisha.py`, синтетические страницы `backend/tests/fixtures/krisha/`, `backend/tests/test_integration_krisha.py`; живой сбор 17.10.2026 на временной БД: 23 новые публикации и 23 фото, запуск «частично» — карточки закрыты SafeLine (HTTP 468), сохранены данные выдачи. Обход не выполняется; загрузка через Playwright не проверялась.
- ⏳ **T-56** (P2) OLX — OAuth собственного аккаунта, обновление токена, статус «подключено · ограничено» — код и тесты: `backend/app/integrations/olx.py`, `backend/tests/test_integration_olx.py`; нужны одобрение заявки и `OLX_CLIENT_ID` / `OLX_CLIENT_SECRET`. Мониторинга чужих объявлений Partner API не даёт.
- ❌ **T-57** (P2) OpenAI и локальный классификатор в общей очереди анализа — не сделано: подключение `openai` показывает только статус (`backend/app/integrations/analyzer_openai.py`), анализ выполняют правила `saq-ai-1`.
- ✅ **T-58** (P0) Реальные статусы на `/sources` вместо демо-списка, режим администратора, собранные публикации в ленте — сделано: раздел «Подключения» — `frontend/src/components/Connectors.tsx`, `frontend/src/lib/connectors.ts`, `frontend/src/styles/sources.css`, `frontend/src/pages/Sources.tsx`; токен администратора только в `sessionStorage` вкладки — `frontend/src/lib/admin.ts`; метки «собрано автоматически» / «прислано через бота», файлы, ход обработки и «Версии в источнике» — `frontend/src/pages/Ads.tsx`, `frontend/src/pages/AdDetail.tsx`, `frontend/src/lib/platform.ts`; тесты — `connectors.test.ts`, `platform.test.ts`.
- ✅ **T-59** (P1) Запуск и документация — сделано: сервис `worker`, тома `media` и `tgsession`, порты `db` и `api` на 127.0.0.1, `env_file` — `docker-compose.yml`; ffmpeg и пользователь uid 10001 — `backend/Dockerfile`; воркер в `dev.sh`; `.env.example` (бэкенд-часть T-36); `.gitignore`, `.dockerignore`; инструкция `docs/INTEGRATIONS.md`, раздел README «Подключение источников рекламы», CREDITS.md.
- ❌ **T-60** (P2) Распознавание речи в роликах — не сделано: OCR читает только кадры видео.

## 4. Порядок

1. Сегодня–16.10: T-33…T-36 (сдача не должна зависеть от кода), затем блок A целиком.
2. 17.10: блок B и T-16…T-19.
3. 18.10: блок D вместе с T-25.
4. 19.10: блок E, затем T-32 и T-37…T-41.
5. 20.10: T-44…T-47, запас на доделки; блок H — только если остаётся время.

## 5. Вопросы к заказчику на ближайшем созвоне

- Окончательная ли редакция PDF после обсуждения 14.10?
- Где Instagram-перечень ЖК, кто подтверждает его полноту?
- Достаточно ли ручного ввода для модуля мониторинга или жюри ждёт сбор из соцсетей? (Сбор подключён блоком J, ручной ввод остался.)
- Передадут ли размеченные примеры ранее выявленных случаев?
