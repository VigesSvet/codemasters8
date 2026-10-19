"""Кадры видео для OCR (ffmpeg): недоверенный файл разбирается с ограничениями.

ffmpeg запускается списком аргументов (без оболочки), читает только локальный файл (-protocol_whitelist file, без
сетевых протоколов и вложенных плейлистов), формат контейнера задан явно по MIME из сигнатуры, один поток (-threads 1
до -i — для декодера: иначе декодер H.264 заводит поток на каждое ядро и на 1080p упирается в ограничение адресного
пространства; -filter_threads 1 и -threads 1 вывода — для фильтра и кодера PNG), кадр не шире 1280 px, не больше
max_frames кадров, общий тайм-аут. Ресурсы процесса ограничивает prlimit (util-linux: память, CPU,
размер файла); preexec_fn не используется — у воркера есть поток-пульс, а fork с preexec_fn в многопоточном процессе
небезопасен. Сбой или тайм-аут — пустой список (причина — в журнал воркера), не исключение.
"""
import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import media

log = logging.getLogger("saq.video")

AS_LIMIT = 1610612736  # 1,5 ГиБ адресного пространства: с запасом на разделяемые библиотеки ffmpeg
FSIZE_LIMIT = 64 * 1024 * 1024
_warned = threading.Event()


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _limits(timeout: int) -> list[str]:
    if shutil.which("prlimit"):
        return ["prlimit", f"--as={AS_LIMIT}", f"--cpu={timeout + 10}", f"--fsize={FSIZE_LIMIT}", "--"]
    if not _warned.is_set():
        _warned.set()
        log.warning("prlimit не найден — ffmpeg запускается без ограничений ресурсов")
    return []


def container(mime: str) -> str:
    """Формат контейнера для -f по MIME из сигнатуры (ffmpeg не угадывает формат недоверенного файла)."""
    return "mov" if mime in ("video/mp4", "video/quicktime") else "matroska"


def command(path: Path, mime: str, out_dir: Path, *, every_s: float = 2.0, max_frames: int = 8,
            timeout: int = 90) -> list[str]:
    """Команда извлечения кадров (список аргументов)."""
    fmt = container(mime)
    return [*_limits(timeout), "ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f", fmt,
            "-threads", "1", "-i", f"file:{path}", "-filter_threads", "1", "-threads", "1",
            "-vf", f"fps=1/{every_s},scale='min(1280,iw)':-2", "-frames:v", str(max_frames), f"{out_dir}/f_%03d.png"]


def _reason(stderr: bytes) -> str:
    """Последняя содержательная строка stderr ffmpeg (без «Last message repeated N times»)."""
    lines = [ln.strip() for ln in (stderr or b"").decode("utf-8", "replace").splitlines()]
    lines = [ln for ln in lines if ln and "Last message repeated" not in ln]
    return lines[-1][:300] if lines else ""


def extract_frames(path: Path, mime: str, *, every_s: float = 2.0, max_frames: int = 8,
                   timeout: int = 90) -> list[tuple[int, bytes]]:
    """[(время кадра в мс, PNG)] — кадр раз в every_s секунд, не больше max_frames; сбой/тайм-аут — []."""
    tmp = Path(tempfile.mkdtemp(dir=media.tmp_dir(), prefix="frames-"))
    try:
        cmd = command(Path(path), mime, tmp, every_s=every_s, max_frames=max_frames, timeout=timeout)
        try:
            res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg: кадры не извлечены за %s с", timeout)
            return []
        except OSError as e:
            log.warning("ffmpeg не запускается: %s", type(e).__name__)
            return []
        if res.returncode != 0:
            reason = _reason(res.stderr)
            log.warning("ffmpeg завершился с кодом %s%s", res.returncode, f": {reason}" if reason else "")
            return []
        frames = sorted(tmp.glob("f_*.png"))[:max_frames]
        return [(int(i * every_s * 1000), p.read_bytes()) for i, p in enumerate(frames)]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
