# HTTP API

Справочник маршрутов API SAQ Qurylys по областям: назначение, главное в теле и ответе, коды ошибок, требования к доступу. Для разработчиков интерфейса, внешних клиентов(сборщики публикаций, бот заявок) и технической проверки. Полная схема - OpenAPI сервера на `/docs`; смысл данных - в документах по ссылкам из разделов.

## Общие правила

### Адрес и формат

- Все маршруты - под `/api`. Интерфейс обращается к `/api` своего адреса через прокси Vite, API слушает порт 8000 - [DEPLOYMENT.md, "Сетевой доступ"](DEPLOYMENT.md#сетевой-доступ).
- OpenAPI: `http://127.0.0.1:8000/docs` и `/openapi.json` - только напрямую на машине с API, не через прокси интерфейса.
- JSON с полями в camelCase(как в `frontend/src/data/types.ts`). Кроме JSON: загрузки файлов(multipart/form-data), PDF и DOCX, страница возврата из OLX.
- Моменты событий(`at`, `createdAt`, `collectedAt` и подобные) - ISO 8601 с UTC-смещением. Дата публикации `date` - местное время Усть-Каменогорска(UTC+5) без смещения: `ГГГГ-ММ-ДД` или `ГГГГ-ММ-ДДTЧЧ:ММ`. Даты сверок, актов, обращений и снимков на входе - `ГГГГ-ММ-ДД`, с 2000 года и не позже сегодня.
- `asOf`(`ГГГГ-ММ-ДД`) - дата расчёта риска и "Проверки по рекламе", по умолчанию сегодня по Усть-Каменогорску; от неё зависят просрочка срока сдачи(R6) и окно свежей рекламы. Эталон `shared/testdata/risk_golden.json` посчитан на `2026-10-15`.
- Пустые необязательные поля у доказательств риска, выводов ИИ и OCR не выводятся; у публикации, внесённой вручную, нет `collectedAt`, `media`, `processing`, `llm`.
- `origin` у ЖК, застройщиков, публикаций и обращений - [DATA.md, "Происхождение записей"](DATA.md#происхождение-записей).
- Событие журнала: `{id, at, who, what, kind, adId, complexId, signalId}`; `who` у автоматических записей - "Система"; `kind` - `system`, `data`, `status`, `link`, `comment`, `request`, `signal`, `export`, `ai`, `alert`, `connector`.

### Ошибки

Ошибка - JSON `{"detail": ...}`, `detail` - текст, кроме случаев в таблице. Id несуществующей записи в пути или ЖК в теле(`complexId`) - 404, ниже не повторяется; на ключ объекта открытых данных вместо id ЖК 404 поясняет, что риск и сигналы по сведениям источников не рассчитываются.

| Код | Когда |
|---|---|
| 400 | Неизвестное имя сервера в Host |
| 401, 403, 503 | Нет токена, неверный токен, токен не задан на сервере. 403 - ещё изменяющий запрос со страницы другого сайта и действие не по роли |
| 409 | Конфликт состояния: решение тем временем принял другой аналитик, запись уже есть, действие недоступно. У сигнала `detail` - `{message, signalId}`, у предупреждения жителям - `{message, alertId}` |
| 413, 415 | Тело или файл больше предела; тип файла не поддерживается |
| 422 | Проверка полей, пути и параметров - список FastAPI `[{type, loc, msg, input}]`: сообщения собственных проверок - по-русски, стандартные сообщения Pydantic(`missing`, `string_too_long` и подобные) - по-английски, интерфейс переводит их по `type` и называет поле из `loc`. Отказ по смыслу(ЖК не предлагался ИИ, файл пустой, кандидата 2ГИС нет) и ошибки OCR - текст. NUL(`\0`) в пути, параметрах или теле - тоже 422 |
| 429, 507 | Лимит загрузок или 2ГИС; мало места для файлов |
| 500 | Необработанная ошибка: общий текст, трассировка - в журнале API |
| 502, 503 | Внешний сервис не ответил; функция не настроена на сервере |

Ответы защит(400, 403, 404 на публичном адресе) - [SECURITY.md, "Защиты HTTP"](SECURITY.md#защиты-http).

### Пределы тела

| Маршрут | Предел |
|---|---|
| `POST /api/ocr`, `/api/media`, `/api/complexes/{id}/photos`, `/api/complexes/{id}/gallery` | файл 10 МБ(тело - плюс 64 КБ на разметку multipart) |
| `POST /api/sources/import` | файл 5 МБ |
| `POST /api/intake/publications` | 2 МБ |
| `POST /api/intake/telegram` | 60 МБ; JSON заявки - 1 МБ; до 10 файлов по 20 МБ, изображение - не больше `MEDIA_MAX_IMAGE_MB`(по умолчанию 10 МБ) |
| остальные | 16 МБ |

Предел проверяется по `Content-Length` до чтения тела, без заголовка - по мере чтения; превышение - 413.

### Доступ

| Кто | Заголовок | Маршруты |
|---|---|---|
| Аналитик | нет; автор - поле `author` | все, кроме перечисленных ниже |
| Администратор | `X-Admin-Token` или `Authorization: Bearer` со значением `SAQ_ADMIN_TOKEN` | настройка и запуск подключений, повторный анализ ИИ, импорт ЖК по рекламе, сверка 2ГИС мимо кэша(`force`), старт и отключение OLX, согласование, отклонение и повтор отправки предупреждений, проверка бота, `/api/admin/check` |
| Клиент приёма | только `Authorization: Bearer` с `INTEGRATION_INGEST_TOKEN` | `POST /api/intake/publications` |
| Бот заявок | `Authorization: Bearer` или `X-API-Key` с `SERVICE_API_TOKEN` | `/api/intake/telegram` и вложенные маршруты |

- Вход пилотный: учётная запись из `GET /api/analysts`, без пароля. `author` - строка 1-128 символов, сервер не сверяет её со списком аналитиков; "Система" в любом регистре - 422. По учётной записи проверяется только роль согласующего в маршруте сигнала.
- Токен не задан или слабый(короче 24 символов, меньше 12 разных символов, токен администратора совпадает с другим) - 503; заголовка нет - 401 с `WWW-Authenticate: Bearer`; неверный - 403. У приёма и бота токен проверяется до разбора тела. Переменные - [DEPLOYMENT.md, "Переменные окружения"](DEPLOYMENT.md#переменные-окружения), модель доступа - [SECURITY.md, "Доступ"](SECURITY.md#доступ).

### Вид для посетителя

Без верного токена администратора(неверный - не отказ) `GET /api/sources/connectors`, `/connectors/{code}`, `/api/collection-runs` и `/collection-runs/{id}` отдают вид для посетителя, `admin: false`. Пусты: у подключения - `via`, `statusDetail`, `missing`, `secretRefs`, `config`, `lastError`, `lastErrorAt`, `facts`; у запуска - `requestedBy`, `externalRunId`, `costUsd`, `errorCode`, `error`, `notes`; у воркера - `id`. События журнала `connector` выдаются только администратору - в `GET /api/audit` и в журналах публикации и ЖК.

## Служебное и справочники

| Маршрут | Что делает |
|---|---|
| `GET /api/health` | `{ok: true, time}`; по нему проверяется состояние контейнера api |
| `GET /api/analysts` | Учётные записи входа `[{id, name, fullName, short, role}]`; `name` пишется автором в журнал |
| `GET /api/risk/model` | Модель риска - содержимое `shared/risk_model.json` |
| `GET /api/admin/check` | Проверка токена администратора: `{ok: true}` или 401, 403, 503 |

## ЖК и риск

| Маршрут | Что делает |
|---|---|
| `GET /api/complexes?zone=&screening=&asOf=` | ЖК со сведениями карточки. `zone` - `green`, `yellow`, `red`(ЖК без оценки риска в зоны не входит); `screening` - `ok`, `check`, `suspect`, `unknown` |
| `GET /api/complexes/{id}?asOf=` | Один ЖК в той же форме |
| `GET /api/risk?asOf=` | Риск всех ЖК в форме ниже, с `complexId` |
| `GET /api/complexes/{id}/risk?asOf=` | `{score, zone, needsCheck, assessed, factors, observations, summary, modelVersion}`; у фактора - доказательства `items`; `assessed: false` - риск не оценивается |
| `GET /api/developers?asOf=` | Застройщики со `screening`: статус по худшему ЖК, счётчики ЖК по статусам, до трёх худших ЖК |
| `GET /api/stats?asOf=` | `zones`, `notAssessed`, `queue`("Новая" и "Нужна проверка"), `ads`, `signals`(`new`, `confirmed`, `returned`), `screening` - ЖК и застройщики по статусам "Проверки по рекламе", решения аналитика `decisions`(`commissioned`, `analyst`, снятые рекламой `reopened`), объекты из рекламы по статусам, окно свежей рекламы `windowDays` |
| `GET /api/appeals?complexId=` | Обращения граждан, новые первыми; у внесённых аналитиком - `createdBy`, `createdAt`, `comment`, `decidedBy`, `decisionComment` |

Поля ЖК(ответ `GET /api/complexes` и изменяющих маршрутов ЖК), кроме паспортных:

| Поле | Содержание |
|---|---|
| `lat`, `lng` | Координаты или `null` |
| `basis` | `guarantee`, `mio`, `bvu`: `yes`, `no`, `unknown`; `basis.sources` - сверка каждого документа |
| `readiness` | `{declared, confirmed}` с источником и датой |
| `inPublishedList`, `publishedListCheck`, `appealsCheck` | Перечень ГАСК и отметка "обращений нет - сверено" |
| `permit` | Основание по реестру или сверке: `permitted`, `not_permitted`, `unknown` |
| `screening` | "Проверка по рекламе": `status`, `label`, `variant`, `reasons`, `counts`, `basis`(`hypothesis`, `confirmed`), `inRegistry`, `disclaimer` |
| `decision` | Решение аналитика(`active` или `reopened`) или `null` |
| `advert` | Привязанные объекты из рекламы |
| `basisSuggest` | Подсказка сверки из открытого реестра |
| `photos`, `gallery`, `cover`, `homeportal` | Снимки площадки; фото объекта, обложка первой; закреплённая обложка; есть ли карточка Homeportal |

Правила - [RISK_MODEL.md, "Формула и зоны"](RISK_MODEL.md#формула-и-зоны), ["Риск не оценивается"](RISK_MODEL.md#риск-не-оценивается), ["Проверка по рекламе"](RISK_MODEL.md#проверка-по-рекламе).

## Сверка сведений

Изменяющие маршруты пишут событие `data` и отвечают ЖК(маршруты обращений - обращением); повтор тех же значений без комментария журнал не засоряет. `sourceUrl` - `http(s)://` до 512 символов, `source` - до 255, `comment` - до 2000.

| Маршрут | Тело и ответ | Ошибки |
|---|---|---|
| `GET /api/complexes/{id}/basis` | `{complexId, documents, publishedList, checked, total}`: по документу - значение, сверка, подсказка реестра `suggestion`; `total` - 4 | |
| `POST /api/complexes/{id}/basis` | `{author, doc: guarantee\|mio\|bvu, value: yes\|no\|unknown, ref?, source?, sourceUrl?, checkedAt?, comment?}`. Для `yes` и `no` нужен `source`; `checkedAt` по умолчанию - сегодня; `unknown` снимает сверку | |
| `POST /api/complexes/{id}/published-list` | Перечень ГАСК: `{author, value, source?, sourceUrl?, checkedAt?, comment?}`, правила те же | |
| `POST /api/complexes/{id}/readiness` | `{author, kind: declared\|confirmed, value: 0-100, source, date, comment?}`; подтверждённая готовность меняет и `progress` | |
| `POST /api/complexes/{id}/appeals` | `{author, date, subject, applicants?, collective?, source, comment?}` → 201, обращение `pending`. `applicants` - 1-100000; коллективное - от двух заявителей | |
| `POST /api/appeals/{id}/verify`, `/reject` | Подтвердить или отклонить обращение: `{author, comment?}` → обращение | 409 |
| `POST /api/complexes/{id}/appeals-check` | "Обращений нет - сверено": `{author, source, checkedAt?, comment?}` | |
| `POST /api/complexes/{id}/photos` | Снимок площадки, multipart: `file`, `author`, `date`, `source`, `activity`(`active`, `idle`, `unknown`), `note?` → 201, снимок в `photos` с `src: /api/media/{id}` | 413, 415, 422, 429, 507 |

Снимки и фото принимаются по сигнатуре: JPEG, PNG, WEBP, GIF. Лимит ручных загрузок общий для `POST /api/media`, снимков и фото объекта: 50 файлов и 200 МБ за час, 300 файлов и 1 ГБ за сутки UTC.

## Решения по Проверке по рекламе

"Объект сдан" или "Проверено - замечаний нет" делает оценку ЖК `ok`, пока решение не снимет новая реклама; правила - [RISK_MODEL.md, "Проверка по рекламе"](RISK_MODEL.md#проверка-по-рекламе). Событие `data`, ответ - ЖК.

| Маршрут | Тело и ответ | Ошибки |
|---|---|---|
| `POST /api/complexes/{id}/commissioned` | `{author, actNumber?, actDate, source, sourceUrl?, comment?}` → ЖК со `screening.variant: commissioned`, `decision`, стадией "введён"; прежнее решение заменяется | |
| `DELETE /api/complexes/{id}/commissioned` | Отменить "сдан": `{author, reason}`, стадия - прежняя | 404 - решения нет; 409 - действует "проверено" |
| `POST /api/complexes/{id}/screening-decision` | `{author, decision: "no_issues", reason, source, sourceUrl?, comment?}` → ЖК со `screening.variant: analyst` | |
| `DELETE /api/complexes/{id}/screening-decision` | Отменить решение любого вида, в том числе снятое рекламой: `{author, reason}` | 404 - решения нет |
| `GET /api/complexes/{id}/screening-decisions` | История, новые первыми: `active`, `cancelled`, `superseded`; акт, источник, кто записал и закрыл, причина | |

Проверки(422): `actDate` обязательна, с 2000 года и не позже сегодня; `actNumber` - до 128 символов; `source` обязателен; `reason` решения - от 10 символов, причина отмены - от 5, обе до 2000.

## Фото объекта

Виды фото в `gallery[].kind`: `operator` - загрузил оператор, `site` - снимок площадки, `homeportal` - карточка Homeportal, `ad` - изображение рекламы(`hypothesis: true` при неподтверждённой привязке). Ответ - ЖК.

| Маршрут | Тело и ответ | Ошибки |
|---|---|---|
| `POST /api/complexes/{id}/gallery` | Multipart: `file`, `author`, `note?` → 201; без закреплённой обложки фото становится обложкой | 413, 415, 422, 429, 507 |
| `POST /api/complexes/{id}/cover` | `{author, src}`: `src` - одно из фото объекта; пустой или `null` - автоматическая обложка | 422 - такого фото нет |
| `DELETE /api/complexes/{id}/gallery/{mediaId}` | Удалить фото оператора: `{author}` | |
| `POST /api/complexes/{id}/gallery/fetch` | Подгрузить фото с Homeportal: `{author}` | 422 - нет записи Homeportal со ссылкой(`homeportal: false`); 502 - Homeportal не отдал фото |

## Публикации

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `GET /api/ads?status=&platform=` | Публикации, новые первыми | |
| `GET /api/ads/{id}` | Одна публикация | |
| `POST /api/ads` | Ручное добавление(правила ниже) → 201 | 409 - такой `id` есть |
| `GET /api/ads/{id}/score` | `{score, zone, needsCheck, factors, signals: {sale, funds, safe}, complexId, matchConfidence}` | |
| `PATCH /api/ads/{id}/status` | `{status, author, assignee?, comment?}`; `status` - `new`, `needs_check`, `in_review`, `confirmed`, `dismissed`; для `confirmed` нужен `comment`(основание, до 4000 символов). Событие `status`; повтор без изменений и комментария не пишется | 409 |
| `POST /api/ads/{id}/comments` | `{author, text}`(до 2000 символов) → 201, событие `comment` | |
| `PATCH /api/ads/{id}/complex` | `{complexId, author}`: привязка аналитиком становится фактом(`linkConfirmedBy`, `linkConfirmedAt`, уверенность 100%); `complexId: null` - снять привязку. Событие `link` | 409 |
| `GET /api/ads/{id}/versions` | Версии в источнике, новые первыми: `{version, collectedAt, text, price, mediaKeys, current}`; у ручных - `[]` | |
| `GET /api/ads/{id}/source-status` | `status`(`present`, `deleted`, `unknown`), `how`, `checkedAt`, `lastSeenAt`, `savedAt`, `missedRuns` | |
| `GET /api/changes?days=30&limit=100` | Изменения собранных публикаций по версиям(`kinds`: `price`, `text_removed`, `text_added`, `terms_removed`, `media`; `hunks` - правки с контекстом), удалённые в источнике, счётчики `sourceStatus`. `days` 1-3650, `limit` 1-500 | |
| `POST /api/ads/{id}/reanalyze` | Администратор, `{author}`: повторный анализ Claude, прежний результат - в истории. 202 - задание поставлено, 200 - анализ уже идёт или в очереди; ответ - публикация | 409 - не собрана, нет текста и OCR, ключ отклонён, бюджет 0 или исчерпан; 503 - Claude не настроен |

Тело `POST /api/ads`: `{platform, account, text, url?, date?, ocr?, image?, price?, complexId?, linkConfirmed?, status?, id?, author?}`. Ошибка поля - 422 с именем поля в `loc`.

| Поле | Правило |
|---|---|
| `author` | Пишется в журнал; без него автор - "Система" |
| `complexId`, `linkConfirmed` | Без `complexId` ЖК подбирает ИИ: лучшая гипотеза с уверенностью от порога `lowConfidence` становится привязкой-гипотезой. `linkConfirmed: true` - ЖК выбрал аналитик, привязка сразу факт; нужны `complexId` и `author` |
| `status` | `new` или `needs_check`; остальные - через `PATCH /api/ads/{id}/status` |
| `url` | Пусто или `http(s)://` с хостом, до 512 символов, без логина и пароля, пробелов, управляющих символов и `` \ < > " ` ``, порт 1-65535 |
| `image` | Пусто, `/api/media/<32 hex>` - скриншот из `POST /api/media`(привязывается к публикации) или ссылка по правилам `url` |
| `date` | Пусто - время добавления. `ГГГГ-ММ-ДД` или дата со временем `ЧЧ:ММ[:СС[.доли]]` и необязательным `Z` или `±ЧЧ:ММ`; с 2000 года до "сейчас + 1 сутки". Время хранится местным: `ГГГГ-ММ-ДДTЧЧ:ММ` |
| `id` | Необязателен: `[A-Za-z0-9_-]`, до 32 символов |
| `text`, `ocr` | До 20000 символов |

Поля собранной публикации: `collectedAt` - когда её получил сервер; `media` - файлы текущей версии `{id, kind, mime, url, role}`, у видео с распознанной речью - `speech`; `processing` - ход анализа: `status`(`queued`, `running`, `done`, `failed`), `stage`, `error`(подробности - в журнале воркера); `llm` - последний анализ Claude: `status`(`done`, `refused`, `failed`, `skipped`), признаки продажи, суммы, кандидаты ЖК, стадия, нормы закона, `promptVersion`, токены, `costUsd` - [AI.md, "Анализ Claude"](AI.md#анализ-claude).

## ИИ и OCR

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `POST /api/analyze` | Без записи в БД: `{text, ocr?, account?, platform?, url?}` → `{engine, terms, hypotheses, signals, phones, addresses, note?}`: условия оплаты с цитатой, источником `from`(`text`, `ocr`) и уверенностью; гипотезы ЖК с уверенностью и причинами | |
| `GET /api/ads/{id}/analysis` | Анализ сохранённой публикации без отклонённых гипотез, с решениями `termsReview*`, `linkConfirmed*`, `rejectedLinks` | |
| `POST /api/ads/{id}/hypotheses/accept` | `{author, complexId}`: привязка - факт с уверенностью гипотезы; событие `ai` | 422 - этот ЖК ИИ не предлагал; 409 |
| `POST /api/ads/{id}/hypotheses/reject` | `{author, complexId, comment?}`: ЖК - в `rejectedLinks`, привязка-гипотеза к нему снимается; событие `ai` | 409 - привязка подтверждена(снимается через `PATCH /api/ads/{id}/complex`) или решение принял другой |
| `POST /api/ads/{id}/terms-review` | `{author, decision: accepted\|rejected, comment?}`; `rejected` - "это не предложение внести деньги", публикация не даёт R2; событие `ai` | 409 |
| `GET /api/ocr/status` | `{available, engine: tesseract\|none, version?, languages, detail?}` | |
| `POST /api/ocr` | Multipart `file` → `{text, engine, confidence?, languages, durationMs, version?, preprocess, detail?}`; без Tesseract - 200 с `engine: none` | 413, 415, 422 - текстом; 503 - распознавание занято дольше 3 с, `Retry-After: 5` |
| `GET /api/ai/eval` | Прогон `shared/ai_cases.json`: `{engine, cases, metrics: {termsFound, termsExpected, termsPrecision, linkTop1, linkCases, falsePositives}}` | |

Работа аналитика с выводами - [AI.md, "Решения аналитика по выводам ИИ"](AI.md#решения-аналитика-по-выводам-ии).

## Журнал и запросы сведений

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `GET /api/audit?adId=&complexId=&signalId=&kind=&limit=&human=` | Последние `limit` событий(200, до 500) по возрастанию времени. `kind` - через запятую или повтором. `human=true` - без автора "Система", а без `kind` ещё и без `system`, `data`, `connector` | 422 - неизвестный `kind` |
| `GET /api/ads/{id}/audit`, `/api/complexes/{id}/audit` | Журнал публикации; журнал ЖК - сведения, публикации, сигналы, запросы | |
| `POST /api/complexes/{id}/requests` | `{author, topics, comment?}`, темы(1-5) - `basis`, `readiness`, `appeals`, `list`, `other` → 201, событие `request`. Запрос никуда не отправляется | |
| `POST /api/complexes/{id}/request-letter?format=pdf\|docx` | `{addressee: mio\|operator\|gask, author, topics, comment?}`, для темы `other` нужен комментарий → файл, событие `request`. Черновик без отправки | |
| `POST /api/complexes/{id}/request-letter/preview` | То же тело → письмо JSON(`subject`, `addresseeName`, `lawRefs`, `attachments`, `signature`, `signalCode`, `blocks`, `text`) без записи в журнал | |

## Сигналы и маршрут

Сигнал фиксирует риск ЖК на момент создания; решения - события `signal`, выгрузка PDF - событие `export`.

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `POST /api/signals` | `{complexId, author}` → 201, сигнал со снимком риска и доказательств | 422 - риск не оценивается; 409 `{message, signalId}` - по ЖК есть нерассмотренный сигнал |
| `GET /api/signals?status=&complexId=` | Реестр, новые первыми; `status` - `new`, `confirmed`, `returned` | |
| `GET /api/signals/{id}`, `/audit` | Сигнал: факторы, наблюдения, доказательства, журнал; журнал отдельно | |
| `POST /api/signals/{id}/confirm`, `/return` | `{author, comment}`, комментарий обязателен(до 4000 символов) | 409 `{message, signalId}` - уже рассмотрен |
| `GET /api/signals/{id}/pdf?author=` | PDF карточки `signal-<код>.pdf`, отмечает `exportedAt`; `author` - кто выгрузил(пусто - "Аналитик", "Система" - 422) | |
| `GET /api/signals/routes` | Маршрут всех сигналов кратко: `{signalId, code, status, stage, stageLabel, since, dueAt, slaDays, overdue}` | |
| `GET /api/signals/{id}/route` | `stage`(`analysis`, `approval`, `confirmed`, `returned`), `since`, `dueAt`, `slaDays`, `overdue`, `now`, `actions`, `approver`, `sla`, `stages` | |
| `POST /api/signals/{id}/route` | `{action: submit\|approve\|return, author, comment?}` → маршрут. `submit` - на согласование; `approve` - согласовать, сигнал подтверждается как через `/confirm`; `return` - вернуть аналитику с комментарием, сигнал остаётся `new`, срок разбора - заново | 403 - `approve` и `return` только роли "Ведущий аналитик"; 409 - не тот этап или сигнал рассмотрен; 422 - `return` без комментария |

Сроки - рабочие дни(пн-пт по Алматы, без праздников): разбор - 2, согласование - 1.

## Объекты из рекламы

ЖК, которые называет реклама, но которых нет в справочнике и открытом реестре.

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `GET /api/discovered?status=new\|linked\|dismissed` | Новые первыми, затем по числу публикаций: название и `aliases`, аккаунты, число упоминаний `adsCount`, первое и последнее упоминание, до трёх цитат, `stageHints`, `saleSignals`, сверки 2ГИС `geo`, `screening` | |
| `GET /api/discovered/{id}` | Один объект | |
| `POST /api/discovered/{id}/geo` | `{author, force?}` → `{checks, errors}` по названию и адресу; 200 - всё из кэша, 201 - был запрос к 2ГИС; `force` - только администратор. Ошибка одного из двух запросов - в `errors`, обоих - ответ ошибкой | 401, 403 при `force`; 429, 502, 503 |
| `POST /api/discovered/{id}/create-complex` | `{author, name, address?, lat?, lng?, developerName?, developerBin?, candidateId?}` → 201, ЖК с `origin: advertised`. Координаты - парой или точка кандидата 2ГИС `candidateId`; БИН - 12 цифр | 409 - объект привязан, ЖК уже есть, решение принято тем временем; 422 - кандидата нет, пустое название |
| `POST /api/discovered/{id}/link` | Привязать к ЖК: `{author, complexId}` | 409 |
| `POST /api/discovered/{id}/dismiss` | Отклонить: `{author, reason}`, привязка снимается | 409 |
| `POST /api/discovered/import` | Администратор: `{author, note?, items}`(1-200) → `{created, updated, unchanged, skipped, developersCreated, eventIds}`; повтор того же списка ничего не меняет | 401, 403, 503 |

Откуда берутся такие объекты - [DATA.md, "Карточки ЖК из реестра и по рекламе"](DATA.md#карточки-жк-из-реестра-и-по-рекламе).

## Открытые данные

Объект открытых данных - группа записей источников по названию; риска, зоны и сигналов у него нет.

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `GET /api/sources` | `[{code, name, url, records, objects, collectedAt, providerLoadedAt, note}]` по источникам с записями(`homeportal`, `homeportal_section`, `ashyq`). Строка `demo` со счётчиками `counts` - только если в БД есть записи демо-набора | |
| `GET /api/registry?withCoords=&q=` | `{objects, withoutCoords}`; `withCoords=true` или `false` оставляет один из списков. Объект: `key`, `origin: source`, `name`, `address`, `developer`, `developerBin`, `agent`, `fundingMethods`, `stage`, `plannedEnd`, `lat`, `lng`, `records`, `sources`, `collectedAt`, `flags`. `q`(до 200 символов) ищет без учёта регистра по названию и очередям, адресу, застройщику, уполномоченной компании, БИН, номеру документа, кадастровому номеру; латиница сопоставляется с кириллицей | |
| `GET /api/registry/{key}` | Объект и записи с провенансом(`source`, `sourceRecordId`, `importedAt`, `importFile`, `row`) | |
| `POST /api/sources/import` | Multipart `file`(xlsx, csv); `author` - поле формы или `?author=`, иначе "Система" → отчёт: прочитано, создано, обновлено, без изменений, пропущено, дубли, ошибки, предупреждения, итоги реестра, записи по источникам, `eventId`; событие `data` | 413; 415; 422 - файл повреждён, без заголовка, не UTF-8; 409 - параллельный импорт тех же записей |

Формат файла - [DATA.md, "Открытые реестры"](DATA.md#открытые-реестры).

## Подключения и сбор

Сбор и проверку выполняет воркер: маршрут только ставит задание. Коды подключений: `instagram`, `telegram`, `tiktok`, `krisha`, `telegram_bot`, `intake`, `dgis`, `anthropic`, `olx`. Неизвестный код в `GET /api/sources/connectors/{code}` - 404, в `PATCH`, `/test` и `/collect` - 422.

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `GET /api/sources/connectors` | `{connectors, worker, adminConfigured, admin}`: статус, недостающие переменные(только имена), настройки, счётчики, последний запуск, `capabilities`; жив ли воркер | |
| `GET /api/sources/connectors/{code}` | Одно подключение | |
| `PATCH /api/sources/{code}` | Администратор: `{author, enabled?, intervalMin?(5-10080), config?}`; `config` - `accounts`, `channels`(до 50), `profiles`(до 20), `searchUrls`, `cardUrls`(до 10), `resultsPerPage`(1-50), `downloadVideos` | 409 - не настроено или настройка недоступна; 422 |
| `POST /api/sources/{code}/test`, `/collect` | Администратор, `{author}`: проверить или собрать сейчас → запуск; 202 - поставлен, 200 - уже идёт | 409 - действия нет или не настроено |
| `POST /api/sources/collect-all` | Администратор, `{author}` → `{queued, skipped}`, у пропущенных - причина | |
| `GET /api/collection-runs?source=&limit=` | Запуски, новые первыми(`limit` 20, до 100): статус, счётчики, заметки, ошибка, ID запуска Apify, стоимость | |
| `GET /api/collection-runs/{id}` | Один запуск | |

Статусы - [INTEGRATIONS.md, "Статусы подключения и запуска"](INTEGRATIONS.md#статусы-подключения-и-запуска).

## Приём и бот заявок

Контракты - [INTEGRATIONS.md, "Приём публикаций API"](INTEGRATIONS.md#приём-публикаций-api) и ["Telegram-бот заявок"](INTEGRATIONS.md#telegram-бот-заявок). Все отвечают 401, 403, 503 по правилам токенов.

| Маршрут | Что делает | Ответы |
|---|---|---|
| `POST /api/intake/publications` | `INTEGRATION_INGEST_TOKEN`. Публикация внешнего сборщика → `{status, adId, itemId, version, mediaQueued, analysisQueued}` | 201 `created`; 200 `updated`, `unchanged`; 409 - собрана другим источником; 413; 422 - поля решений аналитика, неизвестные поля |
| `POST /api/intake/telegram` | `SERVICE_API_TOKEN`, как и все маршруты ниже. Заявка жителя, JSON или multipart с файлами → `{externalId, adId, status, submissionStatus, statusLabel}` | 201; 200; 409, 413, 415, 422, 507 |
| `GET /api/intake/telegram/submissions/{externalId}` | Статус заявки `{externalId, adId, status, statusLabel, updatedAt}`; ID может содержать "/" | 404 |
| `GET /api/intake/telegram/updates?since=&limit=` | Заявки, полученные или изменившие статус строго позже `since`(ISO 8601 с часовым поясом); `limit` - 100, до 500 | 422 |
| `GET /api/intake/telegram/districts` | Районы ЖК `[{district, complexes}]` | |
| `PUT /api/intake/telegram/subscriptions/{chatId}` | `{districts}` заменяет подписку; `[]` или `["*"]` - все районы → `{chatId, districts, everywhere, status}` | 422 - неизвестный район |
| `GET /api/intake/telegram/subscriptions/{chatId}` | Подписка | 404 - подписки нет |
| `DELETE /api/intake/telegram/subscriptions/{chatId}` | Отписка; ожидающие доставки этому жителю пропускаются | 204, и при повторе |

`chatId` - целое число Telegram. Персональных данных заявителя в ответах нет; подписка хранит только `chat_id` и район.

## Предупреждения жителям

Готовит сотрудник, согласует администратор, отправляет воркер в канал ГАСК и подписчикам района; события `alert`. Настройка - [INTEGRATIONS.md, "Предупреждения жителям через Bot API"](INTEGRATIONS.md#предупреждения-жителям-через-bot-api).

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `GET /api/alerts?complexId=&limit=` | Новые первыми(`limit` 50, до 200). `status` - `pending`, `rejected`, `sending`, `sent`, `partial`, `failed`; `channel` - `{target, status, error, sentAt, link}`; `subscribers` - счётчики, `errors` - до трёх причин недоставки | |
| `GET /api/alerts/audience?district=` | `configured`(задан `BOT_TOKEN`), `adminConfigured`, `workerAlive`, `problem`, `channel`, `channelProblem`, `subscribers`, `everywhere`, `districts` | |
| `POST /api/alerts` | `{complexId, template: basis\|idle\|ok, title, text, author}`; `title` - до 80, `text` - до 240 символов; район берётся из карточки → 201 | 409 `{message, alertId}` - по ЖК уже есть предупреждение на согласовании |
| `POST /api/alerts/{id}/approve` | Администратор, `{author, comment?}`: получатели фиксируются, отправляет воркер | 409 - не на согласовании, согласует сам автор, некому отправить; 503 - `BOT_TOKEN` не задан или не вида `<число>:<ключ>` |
| `POST /api/alerts/{id}/reject` | Администратор, `{author, comment?}` | 409 |
| `POST /api/alerts/{id}/retry` | Администратор, `{author, comment?}`: повтор доставок "ожидает" у `failed` или `partial` | 409 - нечего повторять; 503 |
| `POST /api/alerts/check` | Администратор: `getMe`, `getChat`, `getChatMember` без отправки → `{ok, detail, bot, channel, channelTitle}` | 503 - `BOT_TOKEN` не задан |

## Файлы, 2ГИС, OLX

| Маршрут | Что делает | Ошибки |
|---|---|---|
| `POST /api/media` | Скриншот формы ввода, multipart `file` → 201 `{id, url, mime, size}`; привязывается через `image` в `POST /api/ads`, непривязанный удаляется через сутки | 413; 415; 422 - пустой; 429; 507 |
| `GET /api/media/{id}` | Файл по ID(32 hex). Тип - по сигнатуре из разрешённых, иначе `application/octet-stream`; заголовки `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'` | |
| `GET /api/geo/status` | `{configured, used, limit, periodStart, usedToday, dailyLimit, cacheDays}` без обращения к 2ГИС | |
| `GET /api/geo/checks?targetKind=&targetId=&limit=` | Сохранённые сверки, новые первыми(`limit` 50, до 200) | |
| `POST /api/geo/checks` | "Сверить адрес": `{targetKind: complex\|registry\|discovered, targetId, query?, author, force?}`; без `query` - "Казахстан, Усть-Каменогорск, {адрес}". 200 с `cached: true` - кэш, 201 - запрос к 2ГИС; `force` - администратор | 422 - нет адреса; 429 - сверка объекта меньше 10 минут назад или идёт в другом запросе, квота, пауза 2ГИС; 502; 503 - не настроен или ключ отклонён |
| `POST /api/geo/checks/{id}/accept` | `{candidateId, author, apply?, comment?}`; `apply: true` переносит координаты в карточку ЖК, "было → стало" - в журнал | 409 - уже решено, кандидаты удалены по сроку, координаты изменились; 422 - нет кандидата, `apply` не для ЖК |
| `POST /api/geo/checks/{id}/reject` | Отклонить всех кандидатов: `{author, comment?}` | 409 |
| `POST /api/integrations/olx/start` | Администратор, `{author}` → `{authorizeUrl, expiresAt}`: адрес авторизации с одноразовым `state` на 10 минут | 409 - ключей OLX нет |
| `GET /api/integrations/olx/callback` | Возврат из OLX: сервер меняет код на токен и отвечает страницей HTML без токенов. Единственный путь, открытый через публичный адрес `OLX_REDIRECT_URI`(только GET и HEAD) | страница с кодом 400, 409, 502 |
| `POST /api/integrations/olx/disconnect` | Администратор, `{author}`: токены удаляются → подключение | |

Квота 2ГИС - [INTEGRATIONS.md, "2ГИС"](INTEGRATIONS.md#2гис), подключение OLX - ["OLX.kz"](INTEGRATIONS.md#olxkz).

## Продавцы, цены, спутник

Ответы раздела - гипотезы и контекст для аналитика, в балл риска не входят.

| Маршрут | Что делает |
|---|---|
| `GET /api/sellers?linked=` | Группы аккаунтов по общим телефонам, БИН, имени аккаунта, упоминаниям, похожим картинкам `{groups, stats, note, ...}`; связи - с доказательствами. `linked=true` - только группы из двух и более аккаунтов |
| `GET /api/sellers/{key}` | Группа по ключу группы или аккаунта, схема `graph {nodes, edges}`, публикации; 404 - продавец не найден |
| `GET /api/sellers/by-ad/{adId}` | `{seller, price, note}`: группа продавца, где ещё встречаются номера, цена за м² |
| `GET /api/price-anomalies?all=` | Цена за м²: `{items, median, byRooms, threshold, anomalies, fullPayment, ...}`; без `all` - только дешевле 70% медианы или с отметкой "100% оплата" |
| `GET /api/developers/{id}/affiliations` | Застройщик по БИН в данных системы: ЖК, публикации, телефоны, `agents`, совпадения `related` с причинами, `lookups` - официальные сервисы |
| `GET /api/complexes/{id}/sat-stage` | `images` - стадия на снимке(гипотеза ИИ из `shared/sat_stage.json`: `stage`, `stageLabel`, `confidence`, `reasoning`, дата съёмки `acquired`), `claims` - заявления рекламы с цитатой, `hints`(`ahead`, `money_early`, `behind`, `before_start`, `stale`, `match`, `unclear`) с фактором, `discrepancy`, `disclaimer` |

Стадия на снимке - [AI.md, "Стадия стройки по спутниковому снимку"](AI.md#стадия-стройки-по-спутниковому-снимку).
