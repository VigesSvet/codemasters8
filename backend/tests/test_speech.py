"""Распознавание речи в роликах (T-60, app.speech): движок подменён — модель не скачивается и не загружается.

Проверяется: этап пропускается с пояснением без пакета и при SAQ_ASR_MODEL=off; расшифровка хранится у видео
(meta.speech), попадает в ads.ocr абзацем «Речь в ролике: …», в правила saq-ai-1, материал Claude и в API; слишком
длинный ролик и тайм-аут — skipped с пояснением, текст не сохраняется; галлюцинации Whisper отбрасываются.
"""
import shutil
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy import select

from conftest import mp4_bytes

from app import analysis, llm, media, models, speech, video
from app.db import SessionLocal
from app.integrations.base import MediaIn
from app.settings import get_settings
from test_worker import ad_events, ig_pub, take

SAID = "Старт продаж в ЖК Ульба Ривер, бронь всего 100 000 тенге"


class FakeModel:
    """WhisperModel: язык — по вероятностям probs, сегменты — по текстам; calls — вызовы transcribe."""

    def __init__(self, texts=(SAID,), probs=(("uk", 0.5), ("ru", 0.3), ("kk", 0.2))):
        self.texts, self.probs, self.calls = list(texts), list(probs), []

    def detect_language(self, audio, vad_filter=False):
        return self.probs[0][0], self.probs[0][1], self.probs

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        segs = (SimpleNamespace(text=f" {t} ", no_speech_prob=0.1, avg_logprob=-0.3) for t in self.texts)
        return segs, SimpleNamespace(language=kw.get("language"))


@pytest.fixture
def asr(monkeypatch):
    """Движок «установлен»: ffmpeg и модель подменены. set(seconds=…, model=…) — длина звука и модель."""
    state = {"seconds": 20.0, "model": FakeModel()}
    monkeypatch.setattr(speech, "available", lambda: True)
    monkeypatch.setattr(video, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(video, "extract_frames", lambda path, mime, **kw: [])
    monkeypatch.setattr(speech, "extract_audio", lambda path, mime, max_s, timeout=90: (
        np.zeros(int(state["seconds"] * speech.RATE), dtype=np.float32).tobytes(), ""))
    monkeypatch.setattr(speech, "_model", lambda name, root: state["model"])

    def set_(**kw):
        state.update(kw)
        return state["model"]

    return set_


def video_ad(n=1, text="Новая квартира, звоните", size=300):
    return take(ig_pub(n, text=text, media=[MediaIn(key=f"v{n}", stored=media.store_bytes(mp4_bytes(size + n)),
                                                    status="downloaded")]))


def the_video(ad_id: str) -> models.MediaAsset:
    with SessionLocal() as db:
        return db.scalars(select(models.MediaAsset).where(models.MediaAsset.ad_id == ad_id,
                                                          models.MediaAsset.kind == "video")).one()


def test_skipped_without_package(client, run_worker, ocr_stub):
    res = video_ad()
    run_worker()
    sp = the_video(res.ad_id).meta["speech"]
    assert (sp["status"], sp["detail"]) == ("unavailable", speech.NOT_INSTALLED)
    assert f"Речь в ролике не распознана: {speech.NOT_INSTALLED}" in ad_events(res.ad_id, "system")
    with SessionLocal() as db:
        assert db.get(models.Ad, res.ad_id).ocr is None
        assert db.get(models.IngestItem, res.item_id).analysis_status == "done"
    item = client.get(f"/api/ads/{res.ad_id}").json()["media"][0]
    assert item["speech"]["status"] == "unavailable" and not item["speech"]["text"]


def test_off_and_no_ffmpeg(client, run_worker, ocr_stub, env, monkeypatch):
    env(SAQ_ASR_MODEL="off")
    monkeypatch.setattr(speech, "available", lambda: True)
    off = video_ad()
    run_worker()
    assert the_video(off.ad_id).meta["speech"] == {"status": "skipped", "engine": None, "model": None,
                                                   "detail": speech.DISABLED}
    assert not [e for e in ad_events(off.ad_id) if e.startswith("Речь")]  # выключено — без события
    env(SAQ_ASR_MODEL=None)
    with SessionLocal() as db:  # включили снова — ролик снова «есть работа»
        assert analysis._has_work(analysis._assets(db, off.item_id), models.utcnow())
    nf = video_ad(2)  # ocr_stub: ffmpeg «не установлен»
    run_worker()
    assert the_video(nf.ad_id).meta["speech"]["detail"] == speech.FFMPEG_MISSING


def test_transcript_stored_fed_to_rules_llm_and_api(client, run_worker, ocr_stub, asr):
    model = asr()
    res = video_ad()
    run_worker()
    v = the_video(res.ad_id)
    sp = v.meta["speech"]
    assert (sp["status"], sp["text"], sp["language"], sp["engine"], sp["audioS"]) == (
        "done", SAID, "ru", "faster-whisper", 20.0)
    # язык — из двух (ru/kk), хотя Whisper счёл речь украинской; VAD включён, без опоры на предыдущий текст
    assert model.calls == [{"language": "ru", "beam_size": 1, "vad_filter": True, "condition_on_previous_text": False}]
    with SessionLocal() as db:
        ad = db.get(models.Ad, res.ad_id)
        assert ad.ocr == f"{analysis.SPEECH_MARK} {SAID}"
        mat = llm.build_material(db, ad, [])
        assert SAID in mat.ocr and "Речь в ролике:" in llm.prompt(mat)[1]
        assert "Речь в ролике:" in llm.SYSTEM_PROMPT  # модель знает, что это речь, а не надпись
        job = db.scalars(select(models.Job).where(models.Job.ad_id == res.ad_id)).one()
        assert job.result["speech"] == {"done": 1}
    # правила saq-ai-1 нашли ЖК и условия оплаты только по речи (в тексте публикации их нет)
    ai_events = ad_events(res.ad_id, "ai")
    assert ai_events[0].startswith("Гипотеза ИИ: ЖК «Ульба Ривер»")
    assert any(e.startswith("ИИ: найдены условия оплаты — бронь") for e in ai_events)
    assert "Речь в ролике распознана (русский) — текст добавлен к OCR" in ad_events(res.ad_id, "system")
    item = client.get(f"/api/ads/{res.ad_id}").json()
    assert item["ocr"] == f"Речь в ролике: {SAID}"
    assert item["media"][0]["speech"] == {"status": "done", "text": SAID, "engine": "faster-whisper",
                                          "model": get_settings().asr_model, "language": "ru", "audioS": 20.0,
                                          "detail": None}
    # повтор по той же версии — ролик не распознаётся заново; тот же файл у другой публикации — из готовой расшифровки
    analysis.run_analysis(None, res.ad_id, now=models.utcnow())
    assert len(model.calls) == 1
    same = take(ig_pub(7, text="Другая", media=[MediaIn(key="v7", stored=media.store_bytes(mp4_bytes(301)),
                                                         status="downloaded")]))
    run_worker()
    assert len(model.calls) == 1 and the_video(same.ad_id).meta["speech"]["text"] == SAID


def test_kazakh_and_hallucinations(client, run_worker, ocr_stub, asr):
    asr(model=FakeModel(texts=["Субтитры сделал DimaTorzok", "Пәтерлер сатылымы басталды", "Пәтерлер сатылымы басталды",
                               "Продолжение следует..."], probs=[("kk", 0.6), ("ru", 0.3)]))
    res = video_ad()
    run_worker()
    sp = the_video(res.ad_id).meta["speech"]
    assert (sp["language"], sp["text"]) == ("kk", "Пәтерлер сатылымы басталды")
    assert "Речь в ролике распознана (казахский) — текст добавлен к OCR" in ad_events(res.ad_id, "system")
    asr(model=FakeModel(texts=["Аааааааа...", "Спасибо за просмотр!"]))  # пение и музыка — «речи нет», без события
    quiet = video_ad(2)
    run_worker()
    assert (the_video(quiet.ad_id).meta["speech"]["status"], the_video(quiet.ad_id).meta["speech"]["detail"]) == (
        "empty", speech.NO_SPEECH)
    assert not [e for e in ad_events(quiet.ad_id) if e.startswith("Речь")]


def test_too_long_and_timeout_skipped(client, run_worker, ocr_stub, asr, env, monkeypatch):
    env(SAQ_ASR_MAX_S="30", SAQ_ASR_TIMEOUT_S="10")
    model = asr(seconds=31.0)
    long = video_ad()
    run_worker()
    sp = the_video(long.ad_id).meta["speech"]
    assert (sp["status"], sp["detail"]) == ("skipped", "Ролик длиннее 30 с (SAQ_ASR_MAX_S) — речь не распознавалась")
    assert model.calls == [] and "text" not in sp
    assert ("Речь в ролике не распознана: Ролик длиннее 30 с (SAQ_ASR_MAX_S) — речь не распознавалась"
            in ad_events(long.ad_id, "system"))
    # тайм-аут: часы уходят вперёд на 6 с на каждый сегмент — второй сегмент уже за пределом
    asr(seconds=20.0, model=FakeModel(texts=["первая фраза", "вторая фраза", "третья"]))
    tick = iter(range(0, 1000, 6))
    monkeypatch.setattr(speech, "MONOTONIC", lambda: float(next(tick)))
    slow = video_ad(2)
    run_worker()
    sp = the_video(slow.ad_id).meta["speech"]
    assert (sp["status"], sp["detail"]) == (
        "skipped", "Распознавание речи не уложилось в 10 с (SAQ_ASR_TIMEOUT_S) — текст не сохранён")
    with SessionLocal() as db:
        assert db.get(models.Ad, slow.ad_id).ocr is None
    # «Проанализировать ещё раз» распознаёт пропущенный ролик заново
    monkeypatch.setattr(speech, "MONOTONIC", lambda: 0.0)
    analysis.run_analysis(None, slow.ad_id, now=models.utcnow(), force=True)
    assert the_video(slow.ad_id).meta["speech"]["text"] == "первая фраза вторая фраза третья"


def test_engine_errors_not_fatal(client, run_worker, ocr_stub, asr, monkeypatch):
    def broken(name, root):
        raise RuntimeError("no model.bin")

    monkeypatch.setattr(speech, "_model", broken)
    res = video_ad()
    run_worker()
    sp = the_video(res.ad_id).meta["speech"]
    assert sp["status"] == "unavailable" and "python -m app.speech download" in sp["detail"]
    monkeypatch.setattr(speech, "extract_audio", lambda *a, **kw: (None, speech.NO_AUDIO))
    mute = video_ad(2)
    run_worker()
    assert the_video(mute.ad_id).meta["speech"]["status"] == "empty"
    with SessionLocal() as db:
        assert db.get(models.IngestItem, mute.item_id).analysis_status == "done"


def test_compose_ocr_keeps_speech_within_limit():
    from app.schemas import AD_TEXT_MAX

    out = analysis.compose_ocr(["слово " * 5000], ["бронь 100 000"])
    assert len(out) <= AD_TEXT_MAX and out.endswith("Речь в ролике: бронь 100 000")
    assert analysis.compose_ocr([], []) is None


def test_audio_command_limits(tmp_path):
    cmd = speech.command(tmp_path / "v.mp4", "video/mp4", 300)
    i = cmd.index("ffmpeg")
    assert cmd[i:i + 9] == ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-f", "mov",
                            "-threads"]
    assert cmd[cmd.index("-t") + 1] == "301" and cmd[-3:] == ["-f", "f32le", "pipe:1"]
    assert ["-map", "0:a:0"] == cmd[cmd.index("-map"):cmd.index("-map") + 2]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg не установлен (проверяется в Docker-образе)")
def test_extract_audio_real(tmp_path):
    import subprocess

    with_audio, silent = tmp_path / "a.mp4", tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-f", "lavfi",
                    "-i", "color=c=black:s=64x64:d=4", "-shortest", "-pix_fmt", "yuv420p", str(with_audio)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-pix_fmt", "yuv420p",
                    str(silent)], check=True)
    pcm, why = speech.extract_audio(with_audio, "video/mp4", 300)
    assert why == "" and abs(len(pcm) / 4 / speech.RATE - 4.0) < 0.1
    pcm, why = speech.extract_audio(with_audio, "video/mp4", 1)  # не длиннее max_s + 1 с
    assert abs(len(pcm) / 4 / speech.RATE - 2.0) < 0.1
    assert speech.extract_audio(silent, "video/mp4", 300) == (None, speech.NO_AUDIO)
    junk = tmp_path / "j.mp4"
    junk.write_bytes(mp4_bytes(300))
    assert speech.extract_audio(junk, "video/mp4", 300) == (None, speech.AUDIO_FAILED)
