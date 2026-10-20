"""Распознавание речи в роликах(T-60): звуковая дорожка видео → текст для правил saq-ai-1 и этапа llm, как OCR кадров.

Движок - faster-whisper(Whisper на CTranslate2: бесплатно, локально, CPU, int8). Пакет необязательный: нет пакета,
ffmpeg или модели - итог "unavailable" с пояснением, анализ не падает. Модель SAQ_ASR_MODEL(tiny | base | small |
medium | large-v3 …; off - этап выключен) скачивается с HuggingFace один раз командой
"python -m app.speech download" в SAQ_ASR_MODEL_DIR(по умолчанию backend/data/models - вне Git). Задание анализа в
сеть не ходит(local_files_only): загрузка сотен мегабайт без предела времени(зависшее IPv6-соединение к
huggingface.co висит без конца) держала бы аренду воркера. Модель держится в памяти процесса воркера; время её загрузки
с диска в тайм-аут не входит. "python -m app.speech <файл>" - расшифровка одного ролика из консоли(проверка, замер).

Звук извлекает ffmpeg с теми же ограничениями, что и кадры(app.video: только локальный файл, формат контейнера по MIME,
один поток, prlimit, тайм-аут): моно 16 кГц, float32 PCM в stdout, не длиннее SAQ_ASR_MAX_S + 1 с - недоверенный
контейнер не разбирается в процессе воркера(PyAV не используется). Ролик длиннее SAQ_ASR_MAX_S не распознаётся
(skipped); распознавание дольше SAQ_ASR_TIMEOUT_S прерывается между сегментами(skipped, частичный текст не сохраняется).

Язык - русский или казахский: определяется по речи(VAD) и выбирается из двух - у Whisper русская речь с акцентом или
под музыку иногда определяется как украинская или белорусская. VAD(Silero) пропускает музыку без речи; известные
"галлюцинации" Whisper на музыке и тишине("Субтитры сделал…", "Продолжение следует…") отбрасываются.

Итог transcribe - {status, text, engine, model, language, audioS, durationMs, detail}; status - как ocr_status: done |
empty(нет звука или речи) | unavailable | failed | skipped. Исключения наружу не выходят.
"""
import importlib.util
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

from . import video
from .ai.ocr import clip
from .settings import get_settings

log = logging.getLogger("saq.speech")
Obj = dict[str, Any]

ENGINE = "faster-whisper"
LANGS = ("ru", "kk")
LANG_LABELS = {"ru": "русский", "kk": "казахский"}
RATE = 16000  # Гц: вход Whisper
TEXT_MAX = 4000  # символов расшифровки одного ролика(ads.ocr - не длиннее AD_TEXT_MAX вместе с OCR)
THREADS = 4  # потоков CTranslate2: воркер один, кадры и OCR в это время не идут
EXTRACT_TIMEOUT = 90
DISABLED = "Распознавание речи выключено (SAQ_ASR_MODEL=off)"
NOT_INSTALLED = "faster-whisper не установлен — речь в ролике не распознавалась"
FFMPEG_MISSING = "ffmpeg не установлен — звук ролика не извлечён"
NO_AUDIO = "В ролике нет звуковой дорожки"
NO_SPEECH = "Речь в ролике не найдена"
AUDIO_FAILED = "Звук ролика не извлечён (ffmpeg завершился с ошибкой)"
MONOTONIC: Callable[[], float] = time.monotonic  # тайм-аут распознавания(тесты подменяют)

# Фразы, которые Whisper выдумывает на музыке и тишине(субтитры из обучающих данных) - не речь ролика
_HALLUCINATIONS = re.compile(
    r"субтитр|продолжение следует|спасибо за (просмотр|внимание)|подписывайтесь на канал|ставьте лайк|"
    r"редактор субтитров|корректор|dimatorzok|amara\.org", re.I)

_lock = threading.Lock()
_models: dict[tuple[str, str], Any] = {}


def available() -> bool:
    """Пакет faster-whisper установлен(без импорта: он тянет CTranslate2 и onnxruntime)."""
    return importlib.util.find_spec("faster_whisper") is not None


def command(path: Path, mime: str, max_s: int, timeout: int = EXTRACT_TIMEOUT) -> list[str]:
    """Команда извлечения звука: первая звуковая дорожка → моно 16 кГц float32 в stdout, не длиннее max_s + 1 с."""
    return [*video._limits(timeout), "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
            "-f", video.container(mime), "-threads", "1", "-i", f"file:{path}", "-map", "0:a:0", "-vn",
            "-filter_threads", "1", "-ac", "1", "-ar", str(RATE), "-t", str(max_s + 1), "-f", "f32le", "pipe:1"]


def extract_audio(path: Path, mime: str, max_s: int, timeout: int = EXTRACT_TIMEOUT) -> tuple[bytes | None, str]:
    """(PCM float32 моно 16 кГц, пояснение): нет дорожки - (None, NO_AUDIO), сбой или тайм-аут - (None, причина)."""
    try:
        res = subprocess.run(command(Path(path), mime, max_s, timeout), stdin=subprocess.DEVNULL, capture_output=True,
                             timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg: звук не извлечён за %s с", timeout)
        return None, f"Звук ролика не извлечён за {timeout} с"
    except OSError as e:
        log.warning("ffmpeg не запускается: %s", type(e).__name__)
        return None, AUDIO_FAILED
    reason = video._reason(res.stderr)
    if res.returncode != 0:
        if b"matches no streams" in (res.stderr or b""):  # -map 0:a:0 без звуковой дорожки(не последняя строка)
            return None, NO_AUDIO
        log.warning("ffmpeg (звук) завершился с кодом %s%s", res.returncode, f": {reason}" if reason else "")
        return None, AUDIO_FAILED
    if not res.stdout:
        return None, NO_AUDIO
    return res.stdout, ""


def _model(name: str, root: Path):
    """Модель процесса(кэш по имени и каталогу) - только с диска, без сети(download - отдельной командой)."""
    key = (name, str(root))
    with _lock:
        m = _models.get(key)
        if m is None:
            from faster_whisper import WhisperModel

            started = time.perf_counter()
            m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=THREADS, download_root=str(root),
                             local_files_only=True)
            log.info("Модель распознавания речи %s загружена за %.1f с", name, time.perf_counter() - started)
            _models[key] = m
        return m


def download(name: str | None = None) -> Path:
    """Скачать модель SAQ_ASR_MODEL(или name) с HuggingFace в SAQ_ASR_MODEL_DIR; уже скачанная не скачивается заново."""
    from faster_whisper.utils import download_model

    s = get_settings()
    root = Path(s.asr_model_dir)
    root.mkdir(parents=True, exist_ok=True)
    return Path(download_model(name or s.asr_model, cache_dir=str(root)))


def _language(model, audio) -> str:
    """ru или kk - что вероятнее по речи(VAD); речи нет или сбой определения - ru."""
    try:
        _, _, probs = model.detect_language(audio, vad_filter=True)
    except Exception as e:
        log.info("Язык речи не определён (%s) — распознаётся как русская", type(e).__name__)
        return "ru"
    p = dict(probs or [])
    return max(LANGS, key=lambda lang: p.get(lang, 0.0))


def _clean(segments, deadline: float) -> list[str] | None:
    """Тексты сегментов без "галлюцинаций" и повторов подряд; None - не уложилось до deadline(MONOTONIC)."""
    out: list[str] = []
    for seg in segments:
        if MONOTONIC() > deadline:
            return None
        t = " ".join((seg.text or "").split())
        if not t or _HALLUCINATIONS.search(t) or (out and out[-1] == t):
            continue
        if len({c for c in t.casefold() if c.isalpha()}) <= 2:  # "Аааааа…", "Ммм" - пение и звуки, не слова
            continue
        if getattr(seg, "no_speech_prob", 0.0) > 0.8 and getattr(seg, "avg_logprob", 0.0) < -1.0:
            continue  # почти наверняка не речь
        out.append(t)
    return out


def transcribe(path: Path, mime: str) -> Obj:
    """Расшифровка речи ролика(SpeechResult в meta.speech видео): всегда словарь, не исключение."""
    s = get_settings()
    name = (s.asr_model or "").strip()
    out: Obj = {"engine": ENGINE, "model": name}
    if name.lower() == "off":
        return {"status": "skipped", "engine": None, "model": None, "detail": DISABLED}
    if not available():
        return {**out, "status": "unavailable", "detail": NOT_INSTALLED}
    if not video.ffmpeg_available():
        return {**out, "status": "unavailable", "detail": FFMPEG_MISSING}
    started = time.perf_counter()
    try:
        pcm, why = extract_audio(path, mime, s.asr_max_s)
        if pcm is None:
            return {**out, "status": "empty" if why == NO_AUDIO else "failed", "detail": why}
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.float32)
        seconds = round(len(audio) / RATE, 1)
        out["audioS"] = seconds
        if len(audio) > s.asr_max_s * RATE:
            return {**out, "status": "skipped",
                    "detail": f"Ролик длиннее {s.asr_max_s} с (SAQ_ASR_MAX_S) — речь не распознавалась"}
        try:
            model = _model(name, Path(s.asr_model_dir))
        except Exception as e:
            log.warning("Модель распознавания речи %s не загружена: %s", name, type(e).__name__)
            return {**out, "status": "unavailable",
                    "detail": f"Модель faster-whisper «{name}» не найдена в SAQ_ASR_MODEL_DIR ({type(e).__name__}) — "
                              "скачайте её: python -m app.speech download"}
        t0 = MONOTONIC()
        lang = _language(model, audio)
        segments, _ = model.transcribe(audio, language=lang, beam_size=1, vad_filter=True,
                                       condition_on_previous_text=False)
        texts = _clean(segments, t0 + s.asr_timeout_s)
        out["language"] = lang
        out["durationMs"] = int((time.perf_counter() - started) * 1000)
        if texts is None:
            return {**out, "status": "skipped",
                    "detail": f"Распознавание речи не уложилось в {s.asr_timeout_s} с (SAQ_ASR_TIMEOUT_S) — текст "
                              "не сохранён"}
        text = clip(" ".join(texts), TEXT_MAX)
        if not text:
            return {**out, "status": "empty", "detail": NO_SPEECH}
        return {**out, "status": "done", "text": text, "detail": None}
    except Exception as e:
        log.warning("Речь в ролике не распознана: %s", type(e).__name__)
        return {**out, "status": "failed", "detail": f"Ошибка распознавания речи ({type(e).__name__})"}


def main(argv: list | None = None) -> int:
    import argparse
    import json

    from . import logs, media

    p = argparse.ArgumentParser(prog="python -m app.speech",
                                description="Распознавание речи в роликах: загрузка модели и проверка на файле")
    p.add_argument("target", help="download — скачать модель SAQ_ASR_MODEL в SAQ_ASR_MODEL_DIR; иначе путь к видео")
    p.add_argument("--model", help="модель вместо SAQ_ASR_MODEL (только для download)")
    args = p.parse_args(argv)
    logs.install()
    if args.target == "download":
        if not available():
            print(NOT_INSTALLED + ": pip install -r requirements.txt")
            return 1
        started = time.perf_counter()
        path = download(args.model)
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        print(f"Модель {args.model or get_settings().asr_model}: {path} — {size / 1e6:.0f} МБ, "
              f"{time.perf_counter() - started:.0f} с")
        return 0
    path = Path(args.target)
    try:
        with path.open("rb") as f:
            mime = media.sniff(f.read(64))
    except OSError as e:
        print(f"Файл {path} не прочитан: {type(e).__name__}")
        return 1
    started = time.perf_counter()
    r = transcribe(path, mime or "video/mp4")
    r["wallS"] = round(time.perf_counter() - started, 1)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r.get("status") in ("done", "empty") else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
