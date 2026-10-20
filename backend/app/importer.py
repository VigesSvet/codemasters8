"""Импорт открытых данных об объектах долевого строительства: xlsx/csv → нормализованные записи источников.

Каждая запись сохраняет провенанс: код и подпись источника, раздел источника, ссылку, ID записи в источнике, время сбора
(UTC) и дату загрузки у поставщика. Значения не оцениваются и не дополняются: пусто в файле - null, "0" в координатах -
нет координат, ссылка не http(s) - не сохраняется. Записи группируются в объекты по нормализованному названию(без
"ЖК/МЖК", кавычек, регистра, "блок А", "пятно N", "очередь N", "здание N", "(ГС: …)"); ключ объекта - устойчивый
латинский slug этого названия.

Это сведения открытых источников, а не выводы системы: риск, зоны и сигналы для них не рассчитываются(ТЗ §7).

CLI: python -m app.importer <xlsx|csv> [--out shared/registry_ust_kamenogorsk.json] [--dry-run]
"""
import argparse
import contextlib
import csv
import hashlib
import io
import itertools
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Iterator
from xml.parsers import expat

from .ai.nlp import _CYR, _LAT, norm, skeleton_text  # таблицы транслитерации ИИ-модуля - для slug и поиска
from .risk_model import SHARED
from .schemas import is_http_url

Obj = dict[str, Any]

IMPORTER = "saq-import-1"
REGISTRY = SHARED / "registry_ust_kamenogorsk.json"
MAX_BYTES = 5 * 1024 * 1024
MAX_UNZIPPED = 64 * 1024 * 1024  # xlsx - zip: предел после распаковки, чтобы "zip-бомба" не съела память
MAX_ROWS = 20000
ROWS_CAP = MAX_ROWS + 50  # строк листа при чтении: запас на строки над заголовком и пустые строки
MAX_COLS = 100  # колонок листа xlsx: строка с ячейкой в XFD не раздувается до 16384 значений
# Структура XML книги до openpyxl(_prescan): пределы с запасом над настоящими файлами Excel
MAX_ROW_CELLS = 16384  # ячеек в строке листа - предел Excel(XFD)
MAX_CHILDREN = 65536  # детей у одного элемента(кроме строк листа и общих строк): стилей в Excel не больше 64000
MAX_DEPTH = 64  # вложенность элементов: у OOXML она в пределах пары десятков
MAX_ELEMENTS = 4_000_000  # элементов во всех частях книги: у листа на MAX_ROWS строк по 26 колонок - около 2 млн
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
TOO_BIG = "Файл больше 5 МБ — разделите его на части или сохраните без лишних листов и форматирования"
TOO_MANY_ROWS = f"Слишком много строк (больше {MAX_ROWS}) — разделите файл на части"
XLSX_UNREADABLE = "Книгу Excel не удалось прочитать: файл повреждён или это не .xlsx"
WRONG_TYPE = "Поддерживаются файлы .xlsx (Excel) и .csv (UTF-8, разделитель «,» или «;»)"

# Источники, правила которых известны(ссылки и условия - CREDITS.md). Запись другого источника не импортируется.
SOURCES: dict[str, Obj] = {
    "homeportal": {"name": "Homeportal — карточки ЖК", "url": "https://homeportal.kz",
                   "note": "Жилищный портал КЖК: публичные карточки ЖК — документ-основание, сроки, координаты. "
                           "Сведения источника на момент сбора."},
    "homeportal_section": {"name": "Homeportal — отдельный раздел", "url": "https://homeportal.kz",
                           "note": "Запись из отдельного раздела портала; название раздела — как у источника. "
                                   "Сведения источника на момент сбора, система их не оценивает."},
    "ashyq": {"name": "Казреестр — CSV Ashyq Data",
              "url": "https://ashyq.data.gov.kz/dataset/magda-ds-b80110e9-218a-42ba-b8c8-01fa5e4c82c8",
              "note": "Открытый набор разрешений на привлечение денег дольщиков (Ashyq Data). БИН в наборе нет; "
                      "дата загрузки у поставщика отличается от даты скачивания."},
}
SOURCE_ORDER = list(SOURCES)
# Разделы Homeportal по пути ссылки - если название раздела не указано в примечании
SECTIONS = {"illegal-projects": "Без разрешения на дольщиков"}
DISCLAIMER = ("Сведения открытых источников (Homeportal, Ashyq Data / Казреестр) как есть, на момент сбора — не выводы "
              "системы. Риск, зоны и сигналы для них не рассчитываются: нет зафиксированной рекламы и проверенных данных. "
              "Сценарии нарушений — только на вымышленных ЖК демо-набора.")
FUNDING = ("guarantee", "mio", "bvu")
CITY = (49.948, 82.628)  # центр Усть-Каменогорска - для пометки далёких координат
FAR_KM = 30

# Колонки файла: заголовок → поле записи, тип значения. Заголовки сравниваются без регистра, ё/е и лишних пробелов.
COLUMNS: list[tuple[str, str, str]] = [
    ("Источник", "sourceName", "text"),
    ("ЖК / объект", "name", "text"),
    ("Адрес", "address", "text"),
    ("Застройщик", "developer", "text"),
    ("БИН застройщика", "developerBin", "bin"),
    ("Уполномоченная компания", "agent", "text"),
    ("БИН уполномоченной компании", "agentBin", "bin"),
    ("Стадия по источнику", "stage", "text"),
    ("Способ привлечения средств", "fundingText", "text"),
    ("Номер документа", "docNumber", "text"),
    ("Дата выдачи документа", "docDate", "date"),
    ("Начало строительства", "start", "date"),
    ("Плановое завершение", "plannedEnd", "date"),
    ("Номер акта ввода", "actNumber", "text"),
    ("Дата акта ввода", "actDate", "date"),
    ("Готовность по отчёту, %", "readiness", "percent"),
    ("Период отчёта", "reportPeriod", "period"),
    ("Широта", "lat", "coord"),
    ("Долгота", "lng", "coord"),
    ("Кадастровый номер", "cadastral", "text"),
    ("Сайт ЖК", "site", "url"),
    ("Ссылка на источник", "sourceUrl", "url"),
    ("Когда собраны данные", "collectedAt", "utc"),
    ("Дата загрузки у поставщика", "providerLoadedAt", "local"),
    ("ID в источнике", "sourceRecordId", "text"),
    ("Примечание", "note", "text"),
]
REQUIRED = ("sourceName", "name", "sourceRecordId")
LABELS = {f: h for h, f, _ in COLUMNS}
# Поля записи, которые сравниваются при повторном импорте(провенанс импорта - row, importFile - не данные источника)
FIELDS = ["key", "source", "sourceSection"] + [f for _, f, _ in COLUMNS] + ["fundingMethod"]
LIMITS = {"sourceRecordId": 64}


class ImportFailed(Exception):
    """Файл нельзя импортировать: status - HTTP-код(413/415/422), detail - понятное сообщение."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass
class Parsed:
    file: str
    format: str
    records: list[Obj] = field(default_factory=list)
    read: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    preamble: list[str] = field(default_factory=list)  # строки над заголовком: название и пояснение набора
    sheet: str | None = None


# ---------- нормализация значений ----------

_WS = re.compile(r"\s+")


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, datetime):
        v = v.isoformat(sep=" ")
    t = _WS.sub(" ", str(v).replace("\x00", "")).strip()
    return t or None


def _bin(v: Any) -> tuple[str | None, str | None]:
    """БИН - строка из 12 цифр. Числовая ячейка Excel теряет ведущий ноль - дополняется до 12 знаков."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 10 ** 12 and float(v).is_integer():
        return str(int(v)).zfill(12), None
    t = re.sub(r"[\s -]", "", str(v))
    if re.fullmatch(r"\d{12}", t):
        return t, None
    return None, f"БИН «{_text(v)}» — не 12 цифр, не сохранён"


def _date(v: Any) -> tuple[str | None, str | None]:
    """"07.10.2026", "2024-06-19", дата Excel → ISO-дата."""
    if v is None:
        return None, None
    if isinstance(v, datetime):
        return v.date().isoformat(), None
    if isinstance(v, date):
        return v.isoformat(), None
    t = _text(v)
    if not t:
        return None, None
    m = (re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?", t)
         or re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?", t))
    if m:
        a, b, c = (int(x) for x in m.groups())
        y, mo, d = (c, b, a) if a <= 31 and c > 31 else (a, b, c)
        try:
            return date(y, mo, d).isoformat(), None
        except ValueError:
            pass
    return None, f"дата «{t}» не распознана — не сохранена"


_UTC = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?\s*(UTC|GMT|Z)?([+-]\d{2}:?\d{2})?", re.I)


def _utc(v: Any) -> tuple[str | None, str | None]:
    """"15.10.2026 11:17 UTC", ISO со смещением, время Excel → ISO UTC. Без пояса - UTC(так помечена колонка)."""
    if v is None:
        return None, None
    dt: datetime | None = v if isinstance(v, datetime) else None
    t = _text(v) or ""
    if dt is None and t:
        m = _UTC.fullmatch(t)
        if m:
            d, mo, y, h, mi, s, _, off = m.groups()
            try:
                dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
                if off:  # смещение вне ±24 ч - ValueError
                    dt = datetime.fromisoformat(f"{dt.isoformat()}{off[:3]}:{off[-2:]}")
            except ValueError:
                dt = None
        else:
            try:
                dt = datetime.fromisoformat(re.sub(r"\s*(UTC|Z)$", "+00:00", t, flags=re.I))
            except ValueError:
                dt = None
    if dt is not None:
        try:  # "0001-01-01T00:00+05:00" в UTC - раньше 1-го года
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except OverflowError:
            dt = None
    if dt is None:
        return None, (f"время сбора «{t}» не распознано — не сохранено" if t else None)
    return dt.isoformat(timespec="seconds"), None


def _local(v: Any) -> tuple[str | None, str | None]:
    """"2026-06-15 17:35:09" → "2026-06-15T17:35:09": время поставщика как есть(пояс не указан - без смещения)."""
    if v is None:
        return None, None
    if isinstance(v, datetime):
        return v.isoformat(timespec="seconds"), None
    t = _text(v)
    if not t:
        return None, None
    try:
        out = datetime.fromisoformat(t).isoformat(timespec="seconds")
        if len(out) <= 32:  # столбец provider_loaded_at - String(32): смещение с секундами в него не помещается
            return out, None
    except ValueError:
        pass
    d, _ = _date(t)
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if d and m:
        return f"{d}T{int(m[1]):02d}:{m[2]}:{m[3] or '00'}", None
    if d:
        return d, None
    return None, f"дата загрузки у поставщика «{t}» не распознана — не сохранена"


def _number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    # _text схлопывает пробелы(и неразрывные) в " " - разделитель тысяч "1 234,5" убирается одной заменой
    t = v if isinstance(v, (int, float)) else (_text(v) or "").replace(" ", "").replace(",", ".").rstrip("%")
    try:
        x = float(t)
    except (ValueError, OverflowError):  # OverflowError - целое из xlsx больше предела float
        return None
    return x if math.isfinite(x) else None


def _percent(v: Any) -> tuple[float | None, str | None]:
    if v is None or _text(v) is None:
        return None, None
    x = _number(v)
    if x is None or not 0 <= x <= 100:
        return None, f"готовность «{_text(v)}» — не процент 0–100, не сохранена"
    return round(x, 2), None


def _url(v: Any) -> tuple[str | None, str | None]:
    """Ссылка - только http(s) с хостом(schemas.is_http_url): "javascript:", "data:" и адрес без схемы не сохраняются -
    ссылку показывают жителям на странице проверки ЖК."""
    t = _text(v)
    if t is None or is_http_url(t):
        return t, None
    return None, f"ссылка «{t}» не http(s), не сохранена"


def _period(v: Any) -> tuple[str | None, str | None]:
    """"08.2026" → "2026-08"; другое написание - как есть."""
    if isinstance(v, (datetime, date)):
        return f"{v.year:04d}-{v.month:02d}", None
    t = _text(v)
    if not t:
        return None, None
    m = re.fullmatch(r"(\d{1,2})[./](\d{4})", t) or re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        a, b = m.groups()
        y, mo = (int(b), int(a)) if len(b) == 4 else (int(a), int(b))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}", None
    return t, None


def source_code(label: str | None, url: str | None = None) -> str | None:
    """"Homeportal - карточка ЖК" → homeportal, "Homeportal - отдельный раздел" → homeportal_section,
    "Казреестр - CSV Ashyq Data" → ashyq; уже код - как есть; иначе None."""
    t, u = norm(label), (url or "").lower()
    if t in SOURCES and t != "homeportal":
        return t
    if "ashyq" in t or "казреестр" in t or "qazreestr" in t:
        return "ashyq"
    if "homeportal" in t:
        return "homeportal_section" if "раздел" in t or any(f"/{s}/" in u for s in SECTIONS) else "homeportal"
    return None


def funding_code(text: str | None) -> str | None:
    """Способ привлечения средств: гарантия ЕО(КЖК) / разрешение МИО / участие БВУ."""
    t = norm(text)
    if not t:
        return None
    if "гарант" in t:
        return "guarantee"
    if "бву" in t or "банк" in t:
        return "bvu"
    if "мио" in t or "разрешен" in t:
        return "mio"
    return None


_SECTION = re.compile(r"раздел\w*\s+(?:homeportal\s+)?«([^»]+)»", re.I)


def source_section(code: str, note: str | None, url: str | None) -> str | None:
    """Название раздела источника - из примечания("в разделе Homeportal "…"") или по пути ссылки."""
    if code != "homeportal_section":
        return None
    m = _SECTION.search(note or "")
    if m:
        return m.group(1).strip()
    u = (url or "").lower()
    return next((name for path, name in SECTIONS.items() if f"/{path}/" in u), "отдельный раздел")


# ---------- объект: нормализованное название и ключ ----------

_GS = re.compile(r"\(\s*гс\s*:[^)]*\)")
_PART = re.compile(r"[,(]?\s*\b(?:блок|пятно|очередь|здание|корпус)\s*№?\s*[0-9a-zа-яәғқңөұүһі]{1,3}\b\s*\)?")
_STOP = re.compile(r"\b(?:жк|мжк|ж\s*\.\s*к\s*\.?|жилой комплекс|zhk)(?=\s|$)")


def object_name(name: str | None) -> str:
    """Название объекта для группировки: без "ЖК/МЖК", кавычек, регистра, "блок А", "пятно N", "очередь N", "здание N",
    "(ГС: …)" - очереди и блоки одного ЖК становятся одним объектом."""
    t = _PART.sub(" ", _GS.sub(" ", norm(name)))
    t = _STOP.sub(" ", t)
    t = re.sub(r"[^\w\s-]", " ", t).replace("_", " ")
    return _WS.sub(" ", t).strip(" -")


def slug(text: str) -> str:
    """Устойчивый латинский ключ: "жаным" → zhanym, "золотой квадрат" → zolotoi-kvadrat."""
    t = "".join(_CYR.get(ch, _LAT.get(ch, ch)) for ch in text.lower())
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80].strip("-")
    return t or "obj-" + hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:8]


def object_key(name: str | None) -> str:
    return slug(object_name(name) or norm(name))


_ORG = re.compile(r"\b(?:товарищество с ограниченной ответственностью|тоо|too|ао|ип|ltd|llp)\b")


def org_name(name: str | None) -> str:
    """Название организации для сравнения: без формы собственности, кавычек и регистра(латинское "TOO" - тоже)."""
    return _WS.sub(" ", re.sub(r"[^\w\s-]", " ", _ORG.sub(" ", norm(name)))).strip()


# ---------- чтение файла ----------

def _head(v: Any) -> str:
    return _WS.sub(" ", norm(_text(v) or "")).strip(" *:")


HEADS = {_head(h): f for h, f, _ in COLUMNS}
KINDS = {f: k for _, f, k in COLUMNS}


def kind_of(name: str, content_type: str | None = None) -> str:
    """xlsx | csv по расширению(иначе по типу содержимого); другое - 415."""
    ext = Path(name or "").suffix.lower()
    ct = (content_type or "").split(";")[0].strip().lower()
    if ext == ".xlsx" or (not ext and "spreadsheetml" in ct):
        return "xlsx"
    if ext == ".csv" or (not ext and ct in ("text/csv", "application/csv")):
        return "csv"
    if ext == ".xls":
        raise ImportFailed(415, "Формат .xls (Excel 97–2003) не поддерживается — сохраните файл как .xlsx или .csv")
    raise ImportFailed(415, WRONG_TYPE)


def _prescan(z: zipfile.ZipFile) -> None:
    """Части книги до openpyxl - потоковым expat, без дерева в памяти; нарушение - 422. DTD(и сущностей) в OOXML не
    бывает - отказ, как у defusedxml. Пределы MAX_* - против книги в десятки КБ, которую openpyxl разворачивает в
    гигабайты: строку листа он собирает целиком(~0.5 КБ на ячейку - 1 млн ячеек из 30 КБ файла это ~0.5 ГБ), прочие
    элементы листа держит в дереве до конца разбора, стили превращает в объекты. Часть не XML(картинки, VML)
    пропускается - её разбирает или отвергает сам openpyxl."""
    # теги - "<uri> <имя>"(namespace_separator=" "), как у openpyxl; у строки листа детьми считаются все элементы.
    # Без предела детей(но в MAX_ELEMENTS): строки листа(их режет ROWS_CAP при чтении), общие строки(openpyxl держит
    # только список строк), цепочка вычислений и записи сводной таблицы(openpyxl в read_only их не читает)
    row_tag = f"{_SHEET_NS} row"
    unlimited = {(f"{_SHEET_NS} {parent}", f"{_SHEET_NS} {child}")
                 for parent, child in (("sheetData", "row"), ("sst", "si"), ("calcChain", "c"),
                                       ("pivotCacheRecords", "r"))}
    total = 0
    stack: list[list] = []  # открытые элементы: [тег, детей]

    def fail(*_args: Any) -> None:
        raise ImportFailed(422, XLSX_UNREADABLE)

    def start(tag: str, _attrs: Any) -> None:
        nonlocal total
        total += 1
        if total > MAX_ELEMENTS or len(stack) >= MAX_DEPTH:
            fail()
        if stack:
            parent = stack[-1]
            parent[1] += 1
            limit = MAX_ROW_CELLS if parent[0] == row_tag else MAX_CHILDREN
            if parent[1] > limit and (parent[0], tag) not in unlimited:
                fail()
        stack.append([tag, 0])

    def end(_tag: str) -> None:
        stack.pop()

    for info in z.infolist():
        stack.clear()
        p = expat.ParserCreate(namespace_separator=" ")
        p.StartElementHandler, p.EndElementHandler, p.StartDoctypeDeclHandler = start, end, fail
        try:
            with z.open(info) as f:
                while chunk := f.read(1 << 16):
                    p.Parse(chunk, False)
                p.Parse(b"", True)
        except expat.ExpatError:
            pass


def _xlsx_sheets(raw: bytes) -> Iterator[tuple[str, list[tuple]]]:
    """Листы книги по очереди - пары(название, строки шириной MAX_COLS). Размер листа(dimension) из файла не
    учитывается - неверный не обрезает строки ниже него и не раздувает их вширь. openpyxl в read_only разбирает лист
    лениво, поэтому битая ячейка видна только при чтении строк - тоже 422."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise ImportFailed(422, "Книга Excel слишком большая после распаковки — оставьте в ней только лист с "
                                        "данными")
            _prescan(z)
    except ImportFailed:
        raise
    except Exception:  # zipfile и zlib: битый архив, неверная CRC, неподдерживаемое сжатие или шифрование
        raise ImportFailed(422, "Файл .xlsx повреждён или это не книга Excel — откройте и сохраните его заново") from None
    import openpyxl  # тяжёлый импорт - только когда нужен

    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:  # openpyxl бросает разные исключения на битые книги
        raise ImportFailed(422, XLSX_UNREADABLE) from None
    try:
        for ws in wb.worksheets:
            ws.reset_dimensions()
            rows = []
            try:
                for row in ws.iter_rows(max_col=MAX_COLS, values_only=True):
                    rows.append(row)
                    if len(rows) > ROWS_CAP:
                        raise ImportFailed(422, TOO_MANY_ROWS)
            except ImportFailed:
                raise
            except Exception:  # разбор XML листа: неверное число в ячейке, битая разметка и т. п.
                raise ImportFailed(422, XLSX_UNREADABLE) from None
            yield ws.title, rows
    finally:
        wb.close()


def _csv_rows(raw: bytes) -> list[tuple]:
    if raw[:4] == b"PK\x03\x04":
        raise ImportFailed(422, "Файл с расширением .csv похож на книгу Excel — сохраните его как .csv или загрузите .xlsx")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportFailed(422, "CSV должен быть в кодировке UTF-8 — пересохраните файл («CSV UTF-8»)") from None
    best: list[tuple] = []
    best_score = -1
    for delim in (";", ","):  # разделитель - тот, при котором узнаётся больше колонок заголовка
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
            # больше ROWS_CAP строк не нужно - будет 422; колонки - не дальше MAX_COLS, как у xlsx(строка из 5 млн ";"
            # иначе - секунды разбора заголовка)
            rows = [tuple(r[:MAX_COLS]) for r in itertools.islice(reader, ROWS_CAP + 1)]
        except csv.Error:
            continue
        score = max((sum(_head(v) in HEADS for v in r) for r in rows[:30]), default=0)
        if score > best_score:
            best, best_score = rows, score
    if len(best) > ROWS_CAP:
        raise ImportFailed(422, TOO_MANY_ROWS)
    return best


def _sheets(raw: bytes, fmt: str) -> Iterator[tuple[str | None, list[tuple]]]:
    if fmt == "xlsx":
        yield from _xlsx_sheets(raw)
    else:
        yield None, _csv_rows(raw)


def _header(rows: list[tuple]) -> tuple[int, dict[int, str]] | None:
    """Строка заголовка - первая, где узнаются "Источник", "ЖК / объект" и "ID в источнике"."""
    for i, row in enumerate(rows[:50]):
        cols = {j: HEADS[_head(v)] for j, v in enumerate(row) if _head(v) in HEADS}
        if all(f in cols.values() for f in REQUIRED):
            return i, cols
    return None


def _normalize(cells: dict[str, Any], n: int, p: Parsed) -> Obj | None:
    rec: Obj = {}
    for f, kind in KINDS.items():
        v = cells.get(f)
        if kind == "text":
            rec[f], note = _text(v), None
        elif kind == "bin":
            rec[f], note = _bin(v)
        elif kind == "date":
            rec[f], note = _date(v)
        elif kind == "utc":
            rec[f], note = _utc(v)
        elif kind == "local":
            rec[f], note = _local(v)
        elif kind == "percent":
            rec[f], note = _percent(v)
        elif kind == "period":
            rec[f], note = _period(v)
        elif kind == "url":
            rec[f], note = _url(v)
        else:  # coord
            rec[f], note = _number(v), None
            if _text(v) is not None and rec[f] is None:
                note = f"координата «{_text(v)}» не распознана"
        if note:
            p.warnings.append(f"Строка {n}: {LABELS[f]} — {note}")
    missing = [LABELS[f] for f in REQUIRED if not rec.get(f)]
    if missing:
        p.errors.append(f"Строка {n}: нет «{'», «'.join(missing)}» — пропущена")
        return None
    code = source_code(rec["sourceName"], rec["sourceUrl"])
    if code is None:
        p.errors.append(f"Строка {n}: неизвестный источник «{rec['sourceName']}» — пропущена (известны: "
                        "Homeportal, Казреестр / Ashyq Data)")
        return None
    for f, limit in LIMITS.items():
        if len(rec[f]) > limit:
            p.errors.append(f"Строка {n}: «{LABELS[f]}» длиннее {limit} символов — пропущена")
            return None
    lat, lng = rec["lat"], rec["lng"]
    if not (lat and lng and -90 <= lat <= 90 and -180 <= lng <= 180):
        if lat or lng:
            p.warnings.append(f"Строка {n}: координаты {lat}, {lng} неполные или вне допустимых значений — не сохранены")
        rec["lat"] = rec["lng"] = None  # "0" в источнике - нет координат
    elif _km(lat, lng) > FAR_KM:
        p.warnings.append(f"Строка {n}: координаты {lat}, {lng} — в {round(_km(lat, lng))} км от центра Усть-Каменогорска; "
                          "оставлены как в источнике")
    rec["fundingMethod"] = funding_code(rec["fundingText"])
    if rec["fundingText"] and not rec["fundingMethod"]:
        p.warnings.append(f"Строка {n}: способ привлечения «{rec['fundingText']}» не распознан — сохранён текстом")
    if not rec["collectedAt"]:
        p.warnings.append(f"Строка {n}: нет времени сбора («{LABELS['collectedAt']}»)")
    return {"key": object_key(rec["name"]), "origin": "source", "source": code,
            "sourceSection": source_section(code, rec["note"], rec["sourceUrl"]), **rec, "row": n}


def parse(raw: bytes, name: str, content_type: str | None = None) -> Parsed:
    """Файл → нормализованные записи и отчёт(прочитано, пропущено, повторы внутри файла, ошибки и предупреждения)."""
    if len(raw) > MAX_BYTES:
        raise ImportFailed(413, TOO_BIG)
    fmt = kind_of(name, content_type)
    if not raw.strip():
        raise ImportFailed(422, "Файл пустой")
    found = None
    with contextlib.closing(_sheets(raw, fmt)) as sheets:  # книга закрывается сразу, а не при сборке мусора
        for title, rows in sheets:
            h = _header(rows)
            if h:
                found = title, rows, h
                break
    if not found:
        raise ImportFailed(422, "Не найдена строка заголовка: нужны колонки «Источник», «ЖК / объект» и «ID в источнике» "
                                "(формат — как в docs/Усть-Каменогорск_объекты.xlsx)")
    title, rows, (hi, cols) = found
    p = Parsed(file=(_text(Path((name or "").replace("\\", "/")).name) or "файл")[:255], format=fmt, sheet=title)
    p.preamble = [t for t in (_text(r[0]) if r else None for r in rows[:hi]) if t]
    absent = [h for h, f, _ in COLUMNS if f not in cols.values()]
    if absent:
        p.warnings.append(f"В файле нет колонок: {', '.join(absent)} — значения пустые")
    extra = [_text(v) for j, v in enumerate(rows[hi]) if j not in cols and _text(v)]
    if extra:
        p.warnings.append(f"Неизвестные колонки пропущены: {', '.join(extra)}")
    seen: dict[tuple[str, str], int] = {}
    for i, row in enumerate(rows[hi + 1:], start=hi + 2):
        if all(_text(v) is None for v in row):
            continue
        p.read += 1
        rec = _normalize({f: row[j] if j < len(row) else None for j, f in cols.items()}, i, p)
        if rec is None:
            p.skipped += 1
            continue
        k = (rec["source"], rec["sourceRecordId"])
        if k in seen:
            p.duplicates += 1
            p.warnings.append(f"Строка {i}: повтор записи {rec['source']} #{rec['sourceRecordId']} (как в строке {seen[k]}) "
                              "— оставлена первая")
            continue
        seen[k] = i
        p.records.append(rec)
    if p.read > MAX_ROWS:
        raise ImportFailed(422, TOO_MANY_ROWS)
    return p


# ---------- объекты ----------

def _km(lat: float, lng: float) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (CITY[0], CITY[1], lat, lng))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _rank(r: Obj) -> tuple:
    """Основная запись объекта: карточка Homeportal важнее строки Казреестра; с координатами; без "блок/очередь"."""
    part = bool(_PART.search(norm(r["name"])))
    return (SOURCE_ORDER.index(r["source"]) if r["source"] in SOURCE_ORDER else 9, r.get("lat") is None, part,
            r.get("row") or 0, r.get("sourceRecordId") or "")


def _first(rs: list[Obj], f: str) -> Any:
    return next((r[f] for r in rs if r.get(f) not in (None, "")), None)


def group(records: Iterable[Obj]) -> list[Obj]:
    """Записи → объекты(RegistryObject) по ключу; основная запись - по _rank. Риска, зоны, балла здесь нет."""
    by: dict[str, list[Obj]] = {}
    for r in records:
        by.setdefault(r["key"], []).append(r)
    out = []
    for key, group_rs in by.items():
        rs = sorted(group_rs, key=_rank)
        main = rs[0]
        geo = next((r for r in rs if r.get("lat") is not None and r.get("lng") is not None), None)
        flags: Obj = {}
        section = _first(rs, "sourceSection")
        if section:
            flags["sourceSection"] = section
        if geo and _km(geo["lat"], geo["lng"]) > FAR_KM:
            flags["coordsFarKm"] = round(_km(geo["lat"], geo["lng"]))
        times = [r["collectedAt"] for r in rs if r.get("collectedAt")]
        out.append({
            "key": key, "origin": "source", "name": main["name"],
            "address": main.get("address") or _first(rs, "address"),
            "developer": main.get("developer") or _first(rs, "developer"),
            "developerBin": _first(rs, "developerBin"),
            "agent": main.get("agent") or _first(rs, "agent"),
            "fundingMethods": [m for m in FUNDING if any(r.get("fundingMethod") == m for r in rs)],
            "stage": main.get("stage") or _first(rs, "stage"),
            "plannedEnd": main.get("plannedEnd") or _first(rs, "plannedEnd"),
            "lat": geo["lat"] if geo else None, "lng": geo["lng"] if geo else None,
            "records": len(rs),
            "sources": [s for s in SOURCE_ORDER if any(r["source"] == s for r in rs)],
            "collectedAt": max(times) if times else None,
            "flags": flags,
        })
    return sorted(out, key=lambda o: (object_name(o["name"]), o["key"]))


def split(objs: Iterable[Obj]) -> Obj:
    """{objects: с координатами, withoutCoords: без координат} - форма GET /api/registry и поля в JSON реестра."""
    objs = list(objs)
    return {"objects": [o for o in objs if o["lat"] is not None], "withoutCoords": [o for o in objs if o["lat"] is None]}


def search_text(o: Obj, rs: Iterable[Obj]) -> tuple[str, str]:
    """Строка поиска объекта: названия(в т. ч. очередей), адрес, застройщик, БИН, документы, кадастр - нормализованная
    и в латинском скелете(латиница ≈ кириллица, как в ИИ-сопоставлении)."""
    parts = [o["name"], o.get("address"), o.get("developer"), o.get("agent"), o["key"]]
    for r in rs:
        parts += [r.get(f) for f in ("name", "address", "developer", "developerBin", "agent", "agentBin", "docNumber",
                                     "cadastral", "sourceRecordId")]
    t = " | ".join(norm(x) for x in parts if x)
    return t, skeleton_text(t)


def matches(q: str, hay: tuple[str, str]) -> bool:
    nq = norm(q)
    if not nq:
        return True
    sq = skeleton_text(nq)
    return nq in hay[0] or (bool(sq) and sq in hay[1])


# ---------- JSON реестра(shared/registry_ust_kamenogorsk.json) ----------

def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(SHARED.parent.resolve()).as_posix()
    except ValueError:
        return path.name


def report(p: Parsed) -> Obj:
    objects = group(p.records)
    return {"file": p.file, "format": p.format, "read": p.read, "accepted": len(p.records), "skipped": p.skipped,
            "duplicates": p.duplicates, "objects": len(objects),
            "withoutCoords": sum(o["lat"] is None for o in objects),
            "sources": {s: n for s in SOURCE_ORDER if (n := sum(r["source"] == s for r in p.records))},
            "errors": p.errors, "warnings": p.warnings}


def build(path: Path) -> Obj:
    """Файл → содержимое shared/registry_ust_kamenogorsk.json: пометка происхождения, отчёт импорта, записи и готовые
    объекты(как GET /api/registry после сида - для офлайн-режима фронтенда)."""
    raw = path.read_bytes()
    p = parse(raw, path.name)
    times = [r["collectedAt"] for r in p.records if r.get("collectedAt")]
    loaded = [r["providerLoadedAt"] for r in p.records if r.get("providerLoadedAt")]
    rep = report(p)
    return {
        "_note": DISCLAIMER,
        "meta": {
            "title": p.preamble[0] if p.preamble else "Открытые данные об объектах долевого строительства",
            "description": " ".join(p.preamble[1:]) or None,
            "origin": "source",
            "builtFrom": _rel(path), "sheet": p.sheet, "sha256": hashlib.sha256(raw).hexdigest(),
            "builtAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "builtBy": f"python -m app.importer ({IMPORTER})",
            "collectedFrom": min(times) if times else None, "collectedAt": max(times) if times else None,
            "providerLoadedAt": max(loaded) if loaded else None,
            "report": {k: v for k, v in rep.items() if k not in ("file", "format")},
        },
        "sources": [{"code": code, **SOURCES[code]} for code in rep["sources"]],
        "records": [{k: v for k, v in r.items() if k != "origin"} for r in p.records],
        **split(group(p.records)),
    }


def load_registry(path: Path | None = None) -> Obj | None:
    """Содержимое shared/registry_ust_kamenogorsk.json или None, если файла нет(тогда слоя открытых данных нет)."""
    path = path or REGISTRY
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.importer",
                                 description="Импорт открытых данных (xlsx/csv) → shared/registry_ust_kamenogorsk.json")
    ap.add_argument("file", type=Path, help="xlsx или csv в формате docs/Усть-Каменогорск_объекты.xlsx")
    ap.add_argument("--out", type=Path, default=REGISTRY, help=f"куда записать JSON (по умолчанию {_rel(REGISTRY)})")
    ap.add_argument("--dry-run", action="store_true", help="только отчёт, без записи файла")
    a = ap.parse_args(argv)
    try:
        data = build(a.file)
    except ImportFailed as e:
        print(f"Ошибка ({e.status}): {e.detail}", file=sys.stderr)
        return 2
    rep = data["meta"]["report"]
    print(f"{a.file.name}: прочитано строк {rep['read']}, принято {rep['accepted']}, пропущено {rep['skipped']}, "
          f"повторов {rep['duplicates']}; объектов {rep['objects']}, из них без координат {rep['withoutCoords']}; "
          f"по источникам: {', '.join(f'{k} — {v}' for k, v in rep['sources'].items())}")
    for line in rep["errors"] + rep["warnings"]:
        print(" ·", line)
    if not a.dry_run:
        a.out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Записано: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
