# Изображения

Все фото в `frontend/public/img` взяты с Wikimedia Commons и уменьшены до 1600 px.

| Файл | Источник | Лицензия |
|---|---|---|
| city-panorama.jpg | [Панорама города Усть-Каменогорск](https://commons.wikimedia.org/wiki/File:Панорама_города_Усть-Каменогорск.jpg) | CC BY-SA 3.0 |
| city-mountain.jpg | [Ust-Kamenogorsk Wikivoyage banner mountain](https://commons.wikimedia.org/wiki/File:Ust-Kamenogorsk_Wikivoyage_banner_mountain.jpg) | CC BY 4.0 |
| city-3.jpg, city-4.jpg | [Ust'-Kamenogorsk 070000, Kazakhstan — panoramio (3), (4)](https://commons.wikimedia.org/wiki/File:Ust%27-Kamenogorsk_070000,_Kazakhstan_-_panoramio_(3).jpg) | CC BY 3.0 |
| site-a-2025-05.jpg | [Moscow, Shlyuzovaya Embankment, Monblan construction site, May 2025 04](https://commons.wikimedia.org/wiki/File:Moscow,_Shlyuzovaya_Embankment,_Monblan_construction_site,_May_2025_04.jpg) | CC0 |
| site-a-2026-02.jpg | [Moscow, Monblan construction site, Feb 2026 02](https://commons.wikimedia.org/wiki/File:Moscow,_Monblan_construction_site,_Feb_2026_02.jpg) | CC0 |
| site-a-2026-08.jpg | [Moscow, Monblan construction site, Aug 2026 02](https://commons.wikimedia.org/wiki/File:Moscow,_Monblan_construction_site,_Aug_2026_02.jpg) | CC0 |
| site-b.jpg | [Moscow, Level-Yuzhnoportovaya tower construction, April 2025 01](https://commons.wikimedia.org/wiki/File:Moscow,_Level-Yuzhnoportovaya_tower_construction,_April_2025_01.jpg) | CC0 |
| site-c.jpg | [Residential construction in Sompasaari, Helsinki, 2020](https://commons.wikimedia.org/wiki/File:Residential_construction_in_Sompasaari,_Helsinki,_Finland,_2020.jpg) | CC BY-SA 4.0 |
| site-d.jpg | [Moscow, Ryazansky 2, construction of Akvilon B-side-2, June 2025 01](https://commons.wikimedia.org/wiki/File:Moscow,_Ryazansky_2,_construction_of_Akvilon_B-side-2,_June_2025_01.jpg) | CC0 |
| site-f.jpg | [Moscow, construction of Mypriority Paveletskaya, Oct 2025 12](https://commons.wikimedia.org/wiki/File:Moscow,_construction_of_Mypriority_Paveletskaya,_Oct_2025_12.jpg) | CC0 |
| site-g.jpg | [Construction crane in Villejuif 1](https://commons.wikimedia.org/wiki/File:Construction_crane_in_Villejuif_1.jpg) | Public domain |
| site-h.jpg | [Berlin — construction at Humboldthafen, Hilda-Geiringer-Weg](https://commons.wikimedia.org/wiki/File:Berlin_-_construction_of_residential_and_commercial_buildings_at_Humboldthafen,_Hilda-Geiringer-Weg.jpg) | CC BY-SA 3.0 |
| site-i.jpg | [Moscow, Khodynsky Boulevard, 'Stone' construction site April 2025 02](https://commons.wikimedia.org/wiki/File:Moscow,_Khodynsky_Boulevard,_%27Stone%27_construction_site_April_2025_02.jpg) | CC0 |
| facade-a.jpg | [Black and white facade (Unsplash bL-IWrEV-Zk)](https://commons.wikimedia.org/wiki/File:Black_and_white_facade_(Unsplash_bL-IWrEV-Zk).jpg) | CC0 |
| facade-b.jpg | [Residential Building (216845175)](https://commons.wikimedia.org/wiki/File:Residential_Building_(216845175).jpeg) | CC BY 3.0 |
| facade-c.jpg | [Tiled Mid-Century Apartment Building with Balconies in Porto](https://commons.wikimedia.org/wiki/File:Tiled_Mid-Century_Apartment_Building_with_Balconies_in_Porto,_Portugal_(54821673190).jpg) | CC BY 4.0 |

Карта: подложка © Яндекс Карты (тайлы core-renderer-tiles.maps.yandex.net).

# Ассеты Яндекса — внимание

Для демонстрации интерфейс повторяет визуальный язык сервисов Яндекса и использует их оригинальные ассеты:

- `frontend/src/ui/yandex-svg.ts` — SVG-иконки, снятые со страниц realty.yandex.ru, taxi.yandex.ru, yandex.ru/maps, disk.yandex.ru, eda/market/lavka.yandex.ru;
- `frontend/public/fonts/ys/*` — шрифты YS Text / YS Display / YS Display Cond с yastatic.net;
- подложка карты — тайлы Яндекс Карт.

Всё это — собственность ООО «Яндекс», открытой лицензии на эти файлы нет. Перед публикацией репозитория или коммерческим использованием их нужно заменить (Onest вместо YS, Gravity UI Icons вместо SVG со страниц, CARTO/OSM вместо тайлов) либо получить разрешение правообладателя.

# Иконки и логотипы

- Интерфейсные иконки — `@gravity-ui/icons` (MIT, Yandex LLC).
- Логотипы Instagram, TikTok, Telegram, Facebook — `simple-icons` (CC0). Товарные знаки принадлежат правообладателям, используются только для обозначения площадки.
- `frontend/public/brands/krisha.png`, `olx.png`, `2gis.png` — фавиконки с сайтов krisha.kz, olx.kz, 2gis.kz; используются только для обозначения источника публикации.

# PDF сигнальной карточки и тесты

- `backend/app/fonts/NotoSans-Regular.ttf`, `NotoSans-Bold.ttf` — Noto Sans, © The Noto Project Authors, SIL Open Font License 1.1 (текст лицензии — `backend/app/fonts/OFL.txt`). Используется в PDF сигнальной карточки: кириллица, казахские буквы, ₸.
- `reportlab` (BSD, ReportLab Inc.) — сборка PDF сигнальной карточки на бэкенде.
- `vitest` (MIT) — dev-зависимость фронтенда: проверка паритета TS-движка риска с эталоном Python.

# ИИ-анализ и OCR

- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (Apache License 2.0) — распознавание скриншотов объявлений. Ставится в Docker-образ API пакетами Debian `tesseract-ocr`, `tesseract-ocr-rus`, `tesseract-ocr-kaz` вместе с языковыми данными tessdata (Apache License 2.0) и вызывается как отдельная программа; в репозиторий не входит.
- `pillow` (MIT-CMU) — проверка и предобработка изображения перед OCR.
- Извлечение условий оплаты и сопоставление публикаций с ЖК — собственный код (`backend/app/ai/`), без сторонних моделей и внешних API.

# Открытые данные

- **Homeportal** — [homeportal.kz](https://homeportal.kz), жилищный портал КЖК: публичные карточки ЖК (документ-основание, сроки, координаты) и одна запись отдельного раздела портала.
- **Ashyq Data** — [ashyq.data.gov.kz](https://ashyq.data.gov.kz/dataset/magda-ds-b80110e9-218a-42ba-b8c8-01fa5e4c82c8), портал открытых данных: открытый набор разрешений на привлечение денег дольщиков (строки Казреестра, CSV).

Сведения собраны 15.10.2026 в `docs/Усть-Каменогорск_объекты.xlsx` и загружены импортом в `shared/registry_ust_kamenogorsk.json`. Используются сведения открытых источников как есть, у каждой записи указаны источник, ссылка, ID в источнике, время сбора и дата загрузки у поставщика. Выводов о компаниях система по ним не делает: риск, зоны и сигналы для реальных объектов не рассчитываются, сценарии нарушений строятся только на вымышленных ЖК демо-набора. Автоматической синхронизации с порталами нет. Права на сведения принадлежат их владельцам, использование — по правилам источников.

- `openpyxl` (MIT) — чтение xlsx при импорте открытых данных (`backend/app/importer.py`, `POST /api/sources/import`).
