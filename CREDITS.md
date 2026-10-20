# Права и лицензии

В этом файле перечислено стороннее, что использует SAQ Qurylys: код, шрифты, иконки, изображения, модели ИИ, внешние сервисы и данные. Для каждого пункта указаны лицензия или условия использования. Документ адресован жюри(ТЗ §8: сторонние библиотеки, модели и шаблоны указываются честно) и владельцу репозитория перед публикацией. Версии библиотек фронтенда взяты из `frontend/package-lock.json`, бэкенда - из окружения `backend/.venv`, а для `ruff` и `pip-audit` - из закреплённых версий в `backend/requirements-dev.txt` и `Makefile`. Лицензии взяты из метаданных пакетов.

## Библиотеки фронтенда

Зависимости перечислены в `frontend/package.json`. В сборку интерфейса попадают только пакеты из `dependencies` и их зависимости.

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| `react`, `react-dom` | 19.3.1 | MIT | интерфейс |
| `react-router-dom`(с `react-router`) | 7.18.4 | MIT | маршрутизация экранов |
| `maplibre-gl` | 6.14.0 | BSD-3-Clause | карта, спутник и 3D |
| `@dnd-kit/core`(с `@dnd-kit/utilities` 3.2.2 и `@dnd-kit/accessibility` 3.1.1) | 6.3.1 | MIT | перетаскивание публикаций в очереди |
| `@gravity-ui/icons` | 2.23.0 | MIT | иконки интерфейса, раздел [Иконки и логотипы](#иконки-и-логотипы) |
| `simple-icons` | 16.35.0 | CC0-1.0 | логотипы площадок |
| `@fontsource/ibm-plex-mono` | 5.3.0 | OFL-1.1 | шрифт IBM Plex Mono; объявлен в зависимостях, но код интерфейса его не импортирует |

Транзитивные зависимости сборки(по `package-lock.json`), в основном зависимости `maplibre-gl` - `earcut`, `kdbush`, `pbf`, `@mapbox/*`, `@maplibre/*` и другие, - распространяются под MIT, ISC, BSD-2-Clause, BSD-3-Clause, 0BSD(`tslib`) и MIT OR Apache-2.0(`@maplibre/mlt`).

Инструменты разработки(`devDependencies`, в сборку не входят):

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| `vite` | 8.3.4 | MIT | dev-сервер и сборка |
| `@vitejs/plugin-react` | 6.1.2 | MIT | поддержка React в Vite |
| `typescript` | 6.0.3 | Apache-2.0 | проверка типов |
| `oxlint` | 1.88.0 | MIT | линтер |
| `vitest` | 5.0.5 | MIT | тесты фронтенда, в том числе паритет TS-движка риска с эталоном Python |
| `@types/node`, `@types/react`, `@types/react-dom` | 24.19.2, 19.3.0, 19.3.0 | MIT | типы |

Каркас фронтенда создан шаблоном Vite `react-ts`(create-vite, MIT).

## Библиотеки бэкенда

Зависимости перечислены в `backend/requirements.txt` и `backend/requirements-dev.txt`.

| Пакет | Версия | Лицензия | Назначение |
|---|---|---|---|
| `fastapi` | 0.143.2 | MIT | HTTP API |
| `starlette` | 1.8.0 | BSD-3-Clause | основа FastAPI; напрямую используется в приёме заявок Telegram-бота |
| `uvicorn[standard]` | 0.54.0 | BSD-3-Clause | ASGI-сервер; вместе с ним ставятся `uvloop`(MIT или Apache-2.0), `httptools` и `watchfiles`(MIT), `websockets`(BSD-3-Clause) |
| `sqlalchemy` | 2.1.4 | MIT | ORM, SQLite и PostgreSQL |
| `psycopg[binary]` | 3.3.6 | LGPL-3.0-only | драйвер PostgreSQL; используется как библиотека без изменений |
| `pydantic` | 2.14.1 | MIT | схемы запросов и ответов |
| `python-multipart` | 0.0.32 | Apache-2.0 | загрузка файлов в формах |
| `reportlab` | 5.0.1 | BSD | PDF сигнальной карточки |
| `pillow` | 12.3.0 | MIT-CMU | проверка и предобработка изображений перед OCR |
| `openpyxl` | 3.1.5 | MIT | чтение xlsx при импорте открытых данных(`backend/app/importer.py`) |
| `httpx` | 0.28.1 | BSD-3-Clause | HTTP-клиент сборщиков и загрузчика файлов |
| `python-dotenv` | 1.2.4 | BSD-3-Clause | чтение `.env` |
| `telethon` | 1.45.0 | MIT | Telegram MTProto, сбор публичных каналов; вместе с ним ставятся `pyaes`(MIT) и `rsa`(Apache-2.0) |
| `beautifulsoup4` | 4.15.0 | MIT | разбор HTML выдачи Krisha.kz; вместе с ним ставится `soupsieve`(MIT) |
| `faster-whisper` | 1.2.1 | MIT | распознавание речи в роликах; вместе с ним ставятся `ctranslate2`(MIT), `onnxruntime`(MIT), `av`(BSD-3-Clause), `tokenizers` и `huggingface-hub`(Apache-2.0) |
| `anthropic` | 1.15.0 | MIT | официальный Python SDK Claude API; вместе с ним ставятся `httpx2` и `httpcore2`(BSD-3-Clause), `jiter`, `anyio`, `docstring-parser` и `truststore`(MIT), `sniffio`(MIT или Apache-2.0), `opentelemetry-api`(Apache-2.0) |
| `pytest` | 9.1.1 | MIT | тесты, только для разработки |
| `ruff` | 0.17.1 | MIT | линтер, только для разработки |
| `pip-audit` | 2.10.1 | Apache-2.0 | проверка зависимостей на известные уязвимости(`make audit-backend`); ставится в отдельное окружение вне `backend/.venv` |

Среди транзитивных зависимостей есть пакеты под MPL-2.0: `certifi` и `tqdm`(MPL-2.0 и MIT). Они используются без изменений.

## Программы в Docker-образах

Эти программы ставятся при сборке образов из пакетов Debian или берутся готовыми образами Docker Hub. Они вызываются как отдельные программы и в репозиторий не входят.

| Программа | Лицензия | Где и зачем |
|---|---|---|
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) с языковыми данными tessdata(пакеты `tesseract-ocr`, `tesseract-ocr-rus`, `tesseract-ocr-kaz`; английский `tesseract-ocr-eng` ставится зависимостью) | Apache-2.0 | образ API: распознавание текста скриншотов и кадров видео(языки rus, kaz, eng) |
| [FFmpeg](https://ffmpeg.org)(пакет Debian `ffmpeg`) | LGPL-2.1+; сборка Debian включает GPL-компоненты и распространяется под GPL-2+ | образ API: кадры и звук видео для OCR и распознавания речи |
| `prlimit` из пакета Debian `util-linux` | GPL-2.0+ | образ API: ограничение ресурсов ffmpeg |
| Python 3.12(образ `python:3.12-slim-trixie`) | PSF License | основа образа API и воркера |
| PostgreSQL 16(образ `postgres:16-alpine`) | PostgreSQL License | база данных в Docker Compose |
| Node.js 22(образы `node:22-alpine` и `node:22-slim`) | MIT | dev-сервер интерфейса в Docker Compose(`node:22-alpine`); команды фронтенда в `Makefile` на машине без Node(`node:22-slim`) |

## Модели ИИ

- Модели Whisper(OpenAI, MIT) в формате CTranslate2 из репозиториев `Systran/faster-whisper-*` на HuggingFace(MIT). Они скачиваются отдельной командой `python -m app.speech download` в каталог `SAQ_ASR_MODEL_DIR`(по умолчанию `backend/data/models`, вне Git) и работают локально: звук во внешние сервисы не отправляется. Модель по умолчанию - `base`(`SAQ_ASR_MODEL`).
- Claude(Anthropic) через Messages API - внешний платный сервис, см. раздел [Внешние API и площадки](#внешние-api-и-площадки).
- Правила `saq-ai-1`(условия оплаты, сопоставление публикаций с ЖК, оценка качества) - собственный код в `backend/app/ai/`, без сторонних моделей и внешних API.

Как ИИ используется в продукте и при разработке, описано в [README.md, "Использование ИИ"](README.md#использование-ии) и [AI.md](docs/AI.md).

## Шрифты

Шрифты интерфейса лежат в `frontend/public/fonts`. Рядом с каждым семейством лежит полный текст лицензии(`OFL.txt`). Файлы взяты без изменений из пакетов Fontsource(`@fontsource-variable/*` 5.3.0, cdn.jsdelivr.net). Это вариативные woff2(ось wght), разбитые по наборам символов cyrillic, cyrillic-ext, latin и latin-ext.

| Семейство | Где используется | Авторы | Лицензия |
|---|---|---|---|
| [Golos Text](https://github.com/googlefonts/golos-text) | основной текст и заголовки интерфейса; казахские буквы и знак тенге(₸) | The Golos Text Project Authors | SIL Open Font License 1.1 |
| [Roboto Condensed](https://github.com/googlefonts/RobotoCondensed) | узкие прописные заголовки(`--font-cond`) | Google | SIL Open Font License 1.1 |
| [Noto Sans](https://github.com/notofonts/latin-greek-cyrillic)(`backend/app/fonts/NotoSans-Regular.ttf`, `NotoSans-Bold.ttf`) | PDF сигнальной карточки: кириллица, казахские буквы, ₸ | The Noto Project Authors | SIL Open Font License 1.1, текст лицензии - `backend/app/fonts/OFL.txt` |

## Иконки и логотипы

- Иконки интерфейса - [Gravity UI Icons](https://github.com/gravity-ui/icons), пакет `@gravity-ui/icons`(MIT, © YANDEX LLC). Они подключаются как React-компоненты в `frontend/src/ui/icons.tsx`.
- Иконки `IcCameraAdd`, `IcBank` и `IcCrane` - контуры camera-plus, building-bank и crane из [Tabler Icons](https://github.com/tabler/tabler-icons) 3.49.0(MIT, © Paweł Kuna). Они перенесены в `frontend/src/ui/icons.tsx` как данные путей.
- Логотипы Instagram, TikTok, Telegram и Facebook взяты из пакета `simple-icons`(CC0-1.0). Товарные знаки принадлежат правообладателям и используются только для обозначения площадки.
- `frontend/public/brands/krisha.png`, `olx.png`, `2gis.png` - значки сайтов krisha.kz, olx.kz и 2gis.kz. Они используются только для обозначения источника публикации, товарные знаки принадлежат правообладателям.
- Логотип и значки SAQ Qurylys(`frontend/public/logo.svg`, `favicon.*`, `favicon-*.png`, `apple-touch-icon.png`) - собственная работа команды.

## Изображения

Кадры в `frontend/public/landing` - снимки экранов самой системы на стартовом наборе. В них видны тексты и обложки публикаций из стартового набора(условия - в разделе [Стартовый набор данных](#стартовый-набор-данных)), а в `map-3d.jpg` и `satellite.jpg` - снимки Esri и данные OpenStreetMap с подписью атрибуции на кадре(раздел [Карта и снимки](#карта-и-снимки)).

Фото в `frontend/public/img` взяты с Wikimedia Commons и уменьшены до 1600 px по длинной стороне. Они используются в интерфейсе и в тестовых данных как условные снимки города, стройплощадок и фасадов. К реальным ЖК стартового набора эти фото не относятся.

| Файл | Источник | Лицензия |
|---|---|---|
| city-panorama.jpg | [Панорама города Усть-Каменогорск](https://commons.wikimedia.org/wiki/File:Панорама_города_Усть-Каменогорск.jpg) | CC BY-SA 3.0 |
| city-mountain.jpg | [Ust-Kamenogorsk Wikivoyage banner mountain](https://commons.wikimedia.org/wiki/File:Ust-Kamenogorsk_Wikivoyage_banner_mountain.jpg) | CC BY 4.0 |
| city-3.jpg | [Ust'-Kamenogorsk 070000, Kazakhstan - panoramio(3)](https://commons.wikimedia.org/wiki/File:Ust%27-Kamenogorsk_070000,_Kazakhstan_-_panoramio_(3).jpg) | CC BY 3.0 |
| city-4.jpg | [Ust'-Kamenogorsk 070000, Kazakhstan - panoramio(4)](https://commons.wikimedia.org/wiki/File:Ust%27-Kamenogorsk_070000,_Kazakhstan_-_panoramio_(4).jpg) | CC BY 3.0 |
| site-a-2025-05.jpg | [Moscow, Shlyuzovaya Embankment, Monblan construction site, May 2025 04](https://commons.wikimedia.org/wiki/File:Moscow,_Shlyuzovaya_Embankment,_Monblan_construction_site,_May_2025_04.jpg) | CC0 |
| site-a-2026-02.jpg | [Moscow, Monblan construction site, Feb 2026 02](https://commons.wikimedia.org/wiki/File:Moscow,_Monblan_construction_site,_Feb_2026_02.jpg) | CC0 |
| site-a-2026-08.jpg | [Moscow, Monblan construction site, Aug 2026 02](https://commons.wikimedia.org/wiki/File:Moscow,_Monblan_construction_site,_Aug_2026_02.jpg) | CC0 |
| site-b.jpg | [Moscow, Level-Yuzhnoportovaya tower construction, April 2025 01](https://commons.wikimedia.org/wiki/File:Moscow,_Level-Yuzhnoportovaya_tower_construction,_April_2025_01.jpg) | CC0 |
| site-c.jpg | [Residential construction in Sompasaari, Helsinki, Finland, 2020](https://commons.wikimedia.org/wiki/File:Residential_construction_in_Sompasaari,_Helsinki,_Finland,_2020.jpg) | CC BY-SA 4.0 |
| site-d.jpg | [Moscow, Ryazansky 2, construction of Akvilon B-side-2, June 2025 01](https://commons.wikimedia.org/wiki/File:Moscow,_Ryazansky_2,_construction_of_Akvilon_B-side-2,_June_2025_01.jpg) | CC0 |
| site-f.jpg | [Moscow, construction of Mypriority Paveletskaya, Oct 2025 12](https://commons.wikimedia.org/wiki/File:Moscow,_construction_of_Mypriority_Paveletskaya,_Oct_2025_12.jpg) | CC0 |
| site-g.jpg | [Construction crane in Villejuif 1](https://commons.wikimedia.org/wiki/File:Construction_crane_in_Villejuif_1.jpg) | Public domain |
| site-h.jpg | [Berlin - construction of residential and commercial buildings at Humboldthafen, Hilda-Geiringer-Weg](https://commons.wikimedia.org/wiki/File:Berlin_-_construction_of_residential_and_commercial_buildings_at_Humboldthafen,_Hilda-Geiringer-Weg.jpg) | CC BY-SA 3.0 |
| site-i.jpg | [Moscow, Khodynsky Boulevard, 'Stone' construction site April 2025 02](https://commons.wikimedia.org/wiki/File:Moscow,_Khodynsky_Boulevard,_%27Stone%27_construction_site_April_2025_02.jpg) | CC0 |
| facade-a.jpg | [Black and white facade(Unsplash bL-IWrEV-Zk)](https://commons.wikimedia.org/wiki/File:Black_and_white_facade_(Unsplash_bL-IWrEV-Zk).jpg) | CC0 |
| facade-b.jpg | [Residential Building(216845175)](https://commons.wikimedia.org/wiki/File:Residential_Building_(216845175).jpeg) | CC BY 3.0 |
| facade-c.jpg | [Tiled Mid-Century Apartment Building with Balconies in Porto, Portugal(54821673190)](https://commons.wikimedia.org/wiki/File:Tiled_Mid-Century_Apartment_Building_with_Balconies_in_Porto,_Portugal_(54821673190).jpg) | CC BY 4.0 |

## Карта и снимки

Подложки браузер загружает с внешних сервисов во время работы, в репозиторий они не входят. Подпись атрибуции показывает сама карта(кнопка "i" в углу).

- `maplibre-gl`(BSD-3-Clause, MapLibre contributors) отрисовывает карту.
- Режим "Схема" использует стиль Liberty проекта [OpenFreeMap](https://openfreemap.org)(без ключа, с указанием атрибуции) с его шрифтами подписей и значками. Векторные тайлы построены по схеме [OpenMapTiles](https://openmaptiles.org)(© OpenMapTiles, CC BY 4.0) из данных [OpenStreetMap](https://www.openstreetmap.org/copyright)(© участники OpenStreetMap, ODbL). Подписи стиля в приложении заменяются на русские названия OSM(`name:ru`, иначе `name`).
- Режим "Спутник" показывает снимки Esri World Imagery(© Esri, Maxar, Earthstar Geographics) и поверх них подписи Esri World Boundaries and Places. Используются по условиям Esri, атрибуция выводится на карте.
- Режим "3D" показывает тот же снимок Esri World Imagery и объёмные дома из векторных тайлов OpenFreeMap(© OpenStreetMap, OpenFreeMap).
- Архив снимков в карточке ЖК и стадия стройки по спутниковому снимку используют Esri World Imagery Wayback(снимки © Esri, Maxar, Earthstar Geographics) по условиям Esri. `frontend/public/wayback-index.json` - индекс метаданных выпусков Wayback по точкам ЖК(дата выпуска, дата съёмки, разрешение, сенсор). Его собирает `frontend/scripts/wayback-index.mjs`. Самих снимков в репозитории нет.
- Команда `python -m app.satstage` склеивает по точке ЖК фрагмент снимка Wayback(3 × 3 тайла масштаба 17, обрезка 512 × 512 px) и отправляет его в Claude(Anthropic) для гипотезы о стадии стройки. Ответы модели хранятся в `shared/sat_stage.json`, сами снимки в файл не входят. Подробнее - [AI.md, "Стадия стройки по спутниковому снимку"](docs/AI.md#стадия-стройки-по-спутниковому-снимку).

## Открытые данные и нормативная база

- **Homeportal** - [homeportal.kz](https://homeportal.kz), жилищный портал КЖК: публичные карточки ЖК(документ-основание, сроки, координаты) и одна запись отдельного раздела портала. Фото карточек ЖК загружаются по команде аналитика во время работы и в репозиторий не входят.
- **Ashyq Data** - [ashyq.data.gov.kz](https://ashyq.data.gov.kz/dataset/magda-ds-b80110e9-218a-42ba-b8c8-01fa5e4c82c8), портал открытых данных: набор разрешений на привлечение денег дольщиков(строки Казреестра, CSV).
- **ИПС "Әділет"** - [adilet.zan.kz](https://adilet.zan.kz/rus/docs/Z1600000486): статьи Закона РК "О долевом участии в жилищном строительстве" в `shared/law.json`. Нормативный правовой акт - официальный документ. Ссылка на источник и редакцию хранится в файле.

Сведения Homeportal и Ashyq Data собраны 15.10.2026 в `docs/Усть-Каменогорск_объекты.xlsx` и загружены импортом в `shared/registry_ust_kamenogorsk.json`. Они используются как есть: у каждой записи указаны источник, ссылка, ID в источнике, время сбора и дата загрузки у поставщика. Выводов о компаниях система по этим сведениям не делает. Если по карточке ЖК нет проверенных сведений, риск не оценивается. Вымышленные сценарии нарушений есть только в тестовых данных(`shared/testdata`). Автоматической синхронизации с порталами нет. Права на сведения принадлежат их владельцам, использование - по правилам источников. Подробнее о наборах - в [DATA.md, "Открытые реестры"](docs/DATA.md#открытые-реестры).

## Внешние API и площадки

Сервисы используются по правилам их владельцев. Ключи и токены в репозиторий не входят. Настройка и ограничения каждого подключения описаны в [INTEGRATIONS.md](docs/INTEGRATIONS.md).

- Meta Graph API(Instagram API with Facebook Login, Business Discovery) - по условиям платформы Meta.
- Telegram API(MTProto) - по условиям использования Telegram API; читаются только публичные каналы.
- Telegram Bot API - отправка предупреждений жителям, по условиям Telegram.
- Apify, Actor `clockworks/tiktok-scraper` - платный сервис, по условиям Apify и автора Actor. Расход ограничен настройками `APIFY_*`.
- Krisha.kz - API нет. HTML-страницы читаются с соблюдением robots.txt и правил сайта, проверки доступа не обходятся.
- 2ГИС Catalog API(геокодер) - данные © 2ГИС. Кандидаты адресов хранятся временно, по условиям API.
- OLX Partner API - только ресурсы собственного авторизованного аккаунта.
- Anthropic Claude API(Messages API, модель `ANTHROPIC_MODEL`, по умолчанию `claude-sonnet-5-5`) - платный сервис, по условиям Anthropic API. При анализе публикации в Anthropic отправляются текст публикации, распознанный текст(OCR) вместе с расшифровкой речи роликов, площадка, аккаунт(у заявки жителя - без аккаунта) и ограниченный список ЖК; изображения публикаций не отправляются. Расход анализа ограничен `ANTHROPIC_DAILY_BUDGET_USD`. Команда `python -m app.satstage` отправляет фрагменты спутниковых снимков Esri Wayback, её расход ограничен 25 запросами и `--max-usd` за запуск.
- Esri, OpenFreeMap и HuggingFace - см. разделы [Карта и снимки](#карта-и-снимки) и [Модели ИИ](#модели-ии).

## Стартовый набор данных

Каталог `shared/snapshot/` содержит публичную рекламу третьих лиц - застройщиков и агентств Усть-Каменогорска. В нём лежат тексты, изображения(обложки и слайды, уменьшенные до 1024 px), аккаунты и ссылки на публикации Instagram, Krisha.kz и TikTok, собранные 18.10.2026 подключениями сервиса. Там же хранятся распознанный текст(OCR) и ответы Claude(Anthropic) по этим публикациям.

- Назначение. Материалы собраны и хранятся только для мониторинга соблюдения требований к рекламе долевого строительства уполномоченным органом. Права на тексты и изображения принадлежат правообладателям. В набор они включены для этой цели, а не для распространения.
- Чего в наборе нет. Видео, кадров видео, заявок жителей из бота, токенов и ключей. Объявления частных продавцов Krisha хранятся без имён и телефонов. Телефоны и почта в наборе - контакты отделов продаж из публичных аккаунтов застройщиков.
- Характер выводов. "Проверка по рекламе" и риск по этим данным - предварительная автоматическая оценка и гипотезы ИИ, а не вывод о нарушении.
- Учётные записи. Учётные записи аналитиков в наборе пилотные(без пароля), их имена условные.
- Удаление по запросу. По запросу правообладателя материал удаляется из БД, после чего набор выгружается заново командой `make snapshot-export`(или `python -m app.snapshot export` в `backend/`). Порядок выгрузки описан в [DATA.md, "Выгрузка набора"](docs/DATA.md#выгрузка-набора).

## Материалы организаторов

- `docs/Кейс Codemasters 8.0. SAQ (1).pdf` - условие кейса от организаторов Codemasters 8.0. Права на него принадлежат организаторам, файл хранится как исходное ТЗ.
- `docs/14.10.2026 15.32.m4a` - аудиозапись встречи по кейсу 14.10.2026(44 минуты) с голосами её участников. Хранится как исходный материал к ТЗ.
- Текстовые материалы встречи в `docs/`: полная расшифровка(`Полная расшифровка SAQ QURYLYS.txt`) - автоматическая выгрузка сервиса XiaoAI с исправлением названий и терминов; выжимка дополнений к ТЗ и перечень пожеланий(`Выжимка_встречи_—_дополнения_к_ТЗ_SAQ_QURYLYS.txt`, `Что_они_хотят_видеть,_то_чего_нет_в_тз.txt`) составлены по этой расшифровке.

## Изменения состава

Новая зависимость, шрифт, иконка, изображение, модель или внешний сервис вносится в соответствующий раздел этого файла вместе с лицензией. Версии сверяются с `frontend/package-lock.json` и установленным окружением бэкенда. Лицензии берутся из метаданных пакета или из файла лицензии рядом с ассетом. Ассеты без открытой лицензии в репозиторий не добавляются.
