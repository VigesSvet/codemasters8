"""Распознавание скриншотов объявлений(T-20): Tesseract(нейросеть LSTM), языки rus+kaz+eng.

Проверки до распознавания: не изображение или формат, который здесь не читается(HEIC с iPhone, AVIF, SVG) → 415,
больше 10 МБ(или больше 40 Мп) → 413, битый файл → 422; техническая причина - в журнал API, аналитику - понятный текст.
Распознанный текст длиннее предела публикации(schemas.AD_TEXT_MAX) обрезается с пояснением.
Предобработка(Pillow): поворот по EXIF, прозрачность на белом фоне, оттенки серого, увеличение мелких изображений,
автоконтраст. Tesseract вызывается как программа(без обёрток); если его нет на сервере - ответ 200 с engine 'none'
и пояснением, а не 500: текст можно ввести вручную.
"""
import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps

from ..schemas import AD_TEXT_MAX

log = logging.getLogger("saq.ocr")
Obj = dict[str, Any]

TESSERACT = os.getenv("TESSERACT_CMD", "tesseract")
LANGS = ("rus", "kaz", "eng")
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
TIMEOUT = int(os.getenv("SAQ_OCR_TIMEOUT", "60"))
UPSCALE_TO = 1600  # мелкие картинки увеличиваются до этой длинной стороны(не больше чем втрое)
DOWNSCALE_TO = 5000

# сигнатуры форматов, которые читает Pillow и понимает Tesseract
SIGNATURES = ((b"\x89PNG\r\n\x1a\n", "PNG"), (b"\xff\xd8\xff", "JPEG"), (b"GIF87a", "GIF"), (b"GIF89a", "GIF"),
              (b"BM", "BMP"), (b"II*\x00", "TIFF"), (b"MM\x00*", "TIFF"))
TYPES = {"image/png", "image/jpeg", "image/jpg", "image/pjpeg", "image/gif", "image/bmp", "image/x-ms-bmp", "image/webp",
         "image/tiff"}
FORMATS = "PNG, JPEG, WEBP, GIF, BMP или TIFF"
TOO_BIG = f"Файл больше {MAX_BYTES // (1024 * 1024)} МБ — уменьшите или обрежьте скриншот"
# изображения, которые Pillow без расширений не читает: бренды контейнера ISO BMFF(байты 8-12) и SVG
HEIF_BRANDS = {b"heic": "HEIC", b"heix": "HEIC", b"hevc": "HEIC", b"heim": "HEIC", b"heis": "HEIC", b"mif1": "HEIF",
               b"msf1": "HEIF", b"avif": "AVIF", b"avis": "AVIF"}
NOT_INSTALLED = "Tesseract не установлен на сервере — распознавание скриншотов недоступно, текст можно ввести вручную"


class OcrError(Exception):
    """Ошибка входного файла: status - HTTP-код ответа(413, 415, 422), detail - понятный текст по-русски."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _run(args: list[str], data: bytes | None = None, timeout: float = 10) -> subprocess.CompletedProcess:
    """Запуск tesseract(точка подмены в тестах). OMP_THREAD_LIMIT=1 - без перегрузки CPU при параллельных запросах."""
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    return subprocess.run(args, input=data, capture_output=True, timeout=timeout, env=env, check=False)


def _which() -> str | None:
    return shutil.which(TESSERACT)


def status() -> Obj:
    """Доступен ли OCR на сервере: движок, версия, языки(из rus, kaz, eng - те, что установлены)."""
    exe = _which()
    if not exe:
        return {"available": False, "engine": "none", "languages": [], "detail": NOT_INSTALLED}
    try:
        ver = _run([exe, "--version"])
        langs = _run([exe, "--list-langs"])
    except (OSError, subprocess.SubprocessError) as e:
        return {"available": False, "engine": "none", "languages": [], "detail": f"Tesseract не запускается: {e}"[:300]}
    out = (ver.stdout or b"").decode("utf-8", "replace") + (ver.stderr or b"").decode("utf-8", "replace")
    first = next((ln for ln in out.splitlines() if ln.lower().startswith("tesseract")), "")
    version = first.split()[1].lstrip("v") if len(first.split()) > 1 else None
    have = {ln.strip() for ln in (langs.stdout or b"").decode("utf-8", "replace").splitlines()[1:] if ln.strip()}
    use = [lang for lang in LANGS if lang in have]
    if not use:
        return {"available": False, "engine": "none", "version": version, "languages": [],
                "detail": "У Tesseract нет языковых пакетов rus, kaz или eng — распознавание недоступно"}
    res = {"available": True, "engine": "tesseract", "version": version, "languages": use}
    missing = [lang for lang in LANGS if lang not in have]
    if missing:
        res["detail"] = f"Нет языковых пакетов: {', '.join(missing)} — распознаём на {'+'.join(use)}"
    return res


# ---------- файл ----------

def _sniff(raw: bytes) -> str | None:
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "WEBP"
    return next((fmt for sig, fmt in SIGNATURES if raw.startswith(sig)), None)


def _unsupported(raw: bytes, ctype: str) -> str | None:
    """Это изображение, но в формате, который здесь не распознаётся: "HEIC", "SVG", "image/x-icon"…; иначе None."""
    if raw[4:8] == b"ftyp" and raw[8:12] in HEIF_BRANDS:
        return HEIF_BRANDS[raw[8:12]]
    head = raw[:1024].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "SVG"
    return ctype if ctype.startswith("image/") and ctype not in TYPES else None


def load(raw: bytes, content_type: str | None = None) -> Image.Image:
    """Проверенное и декодированное изображение или OcrError(413/415/422)."""
    if not raw:
        raise OcrError(422, "Файл пустой — загрузите скриншот объявления")
    if len(raw) > MAX_BYTES:
        raise OcrError(413, TOO_BIG)
    fmt = _sniff(raw)
    ctype = (content_type or "").split(";")[0].strip().lower()
    if fmt is None:
        other = _unsupported(raw, ctype)
        if other:  # фото с iPhone(HEIC) и т. п.: изображение, но не в том формате
            raise OcrError(415, f"Формат {other} не поддерживается — сохраните скриншот как PNG или JPEG")
        if ctype in TYPES:
            raise OcrError(422, f"Файл повреждён или это не изображение (заявлен тип {ctype}) — нужен {FORMATS}")
        raise OcrError(415, f"Это не изображение{f' ({ctype})' if ctype else ''} — загрузите скриншот в формате {FORMATS}")
    try:
        im = Image.open(BytesIO(raw))
        w, h = im.size
        if w * h > MAX_PIXELS:
            raise OcrError(413, f"Изображение слишком большое: {w}×{h} пикселей (до {MAX_PIXELS // 1_000_000} Мп) — "
                                "уменьшите его")
        im.load()
    except OcrError:
        raise
    except Image.DecompressionBombError:
        raise OcrError(413, f"Изображение слишком большое (до {MAX_PIXELS // 1_000_000} Мп) — уменьшите его") from None
    except Exception as e:
        # текст Pillow("cannot identify image file <_io.BytesIO …>", "image file is truncated") - для журнала, не для аналитика
        log.warning("OCR: файл %s (%s, %d байт) не декодируется: %s", fmt, ctype or "тип не указан", len(raw), e)
        raise OcrError(422, f"Файл {fmt} повреждён и не читается — сохраните скриншот заново (PNG или JPEG)") from None
    return im


def preprocess(im: Image.Image) -> tuple[Image.Image, list[str]]:
    """Подготовка для Tesseract: поворот по EXIF, прозрачность → белый фон, оттенки серого, увеличение мелких,
    уменьшение огромных, автоконтраст. Возвращает изображение и список шагов(по-русски) для показа аналитику."""
    steps: list[str] = []
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, rgba)
        steps.append("прозрачность на белом фоне")
    im = im.convert("L")
    steps.append("оттенки серого")
    side = max(im.size)
    if side < UPSCALE_TO:
        k = min(3.0, UPSCALE_TO / side)
        if k >= 1.25:
            im = im.resize((round(im.width * k), round(im.height * k)), Image.Resampling.LANCZOS)
            steps.append(f"увеличение ×{k:.1f}".replace(".0", "").replace(".", ","))
    elif side > DOWNSCALE_TO:
        k = DOWNSCALE_TO / side
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.Resampling.LANCZOS)
        steps.append(f"уменьшение до {DOWNSCALE_TO} px")
    im = ImageOps.autocontrast(im, cutoff=1)
    steps.append("автоконтраст")
    return im, steps


def parse_tsv(tsv: str) -> tuple[str, float | None]:
    """TSV Tesseract → текст по строкам(абзацы - через пустую строку) и средняя уверенность слов 0…1."""
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    confs: list[float] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not word or conf < 0:
            continue
        key = tuple(int(c) for c in cols[1:5])
        lines.setdefault(key, []).append(word)
        confs.append(conf)
    out, prev = [], None
    for key in sorted(lines):
        if prev is not None and key[:3] != prev[:3]:
            out.append("")
        out.append(" ".join(lines[key]))
        prev = key
    conf = round(sum(confs) / len(confs) / 100, 2) if confs else None
    return "\n".join(out).strip(), conf


def clip(text: str, limit: int = AD_TEXT_MAX) -> str:
    """Текст не длиннее limit: по последнему переводу строки(или пробелу) до предела - без обрубка на полуслове."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("\n"), head.rfind(" ") if head.rfind("\n") < limit // 2 else -1)
    return (head[:cut] if cut > 0 else head).rstrip()


def recognize(raw: bytes, content_type: str | None = None) -> Obj:
    """OcrResult: {text, engine, confidence?, languages, durationMs, version?, preprocess, detail?}."""
    started = time.perf_counter()
    im = load(raw, content_type)
    st = status()
    ms = lambda: math.floor((time.perf_counter() - started) * 1000)
    if not st["available"]:
        return {"text": "", "engine": "none", "languages": [], "durationMs": ms(), "detail": st.get("detail")}
    im, steps = preprocess(im)
    with tempfile.TemporaryDirectory(prefix="saq-ocr-") as tmp:
        path = os.path.join(tmp, "page.png")
        im.save(path, "PNG")
        args = [_which() or TESSERACT, path, "stdout", "-l", "+".join(st["languages"]), "--psm", "3", "tsv"]
        try:
            res = _run(args, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return {"text": "", "engine": "tesseract", "version": st.get("version"), "languages": st["languages"],
                    "durationMs": ms(), "preprocess": steps,
                    "detail": f"Распознавание не уложилось в {TIMEOUT} с — уменьшите или обрежьте скриншот"}
        except OSError as e:
            return {"text": "", "engine": "none", "languages": [], "durationMs": ms(),
                    "detail": f"Tesseract не запускается: {e}"[:300]}
    if res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return {"text": "", "engine": "tesseract", "version": st.get("version"), "languages": st["languages"],
                "durationMs": ms(), "preprocess": steps,
                "detail": "Tesseract завершился с ошибкой" + (f": {err[-1][:200]}" if err else "")}
    text, conf = parse_tsv((res.stdout or b"").decode("utf-8", "replace"))
    long = len(text) > AD_TEXT_MAX  # больше не примут ни анализ, ни сохранение публикации
    text = clip(text)
    out = {"text": text, "engine": "tesseract", "version": st.get("version"), "languages": st["languages"],
           "durationMs": ms(), "preprocess": steps}
    if conf is not None:
        out["confidence"] = conf
    detail = [st["detail"]] if st.get("detail") else []
    if not text:
        detail.append("Текст на изображении не найден")
    if long:
        limit = f"{AD_TEXT_MAX:,}".replace(",", " ")
        detail.append(f"Распознанный текст длиннее {limit} символов — обрезан, проверьте его")
    if detail:
        out["detail"] = ". ".join(detail)
    return out
