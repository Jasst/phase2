# -*- coding: utf-8 -*-
"""Озвучка Silero: очистка текста, фразы, WAV, маршруты. Модель подменяется заглушкой:
настоящие веса в тестах не нужны (и в этой среде недоступны)."""
import http.client
import io
import json
import os
import socket
import threading
import wave

import numpy as np
import pytest

from pulsenet.app import App
from pulsenet.server import make_server
from pulsenet.tts import (SPEAKERS_RU, TTSError, SileroTTS, clean_for_speech, pcm_to_wav,
                          split_segments)


# ---------- текст для речи ----------

def test_code_links_urls_and_tables_are_not_read_aloud():
    text = ("Вот пример:\n```python\nprint('hi')\n```\nСмотри [документацию](https://x.example/doc) "
            "или `git stash`.\n| a | b |\n|---|---|\nИсточник [1] подтверждает.")
    out = clean_for_speech(text)
    assert "print" not in out and "https" not in out and "|" not in out
    assert "документацию" in out and "git stash" in out and "[1]" not in out


def test_markdown_marks_and_tool_trace_removed():
    out = clean_for_speech("## Итог\n**важно** и _курсив_ [инструменты: skill_read✓] (model_done)")
    assert "#" not in out and "*" not in out and "инструменты" not in out
    assert "Итог" in out and "важно" in out


def test_segments_keep_sentences_and_respect_max_length():
    long = "Это очень длинное предложение, в котором много запятых, и оно должно быть разрезано. " * 8
    segs = split_segments(long, max_len=120)
    assert segs and all(len(s) <= 120 for s in segs)
    assert " ".join(segs).replace("  ", " ").count("предложение") == 8


def test_short_fragments_are_merged():
    segs = split_segments("Да. Нет. Хорошо. Давайте так сделаем, пожалуйста, и посмотрим результат.")
    assert len(segs) <= 2


def test_empty_text_gives_no_segments():
    assert split_segments("```\nкод\n```") == []


# ---------- WAV ----------

def test_wav_has_correct_format_and_length():
    samples = np.sin(np.linspace(0, 6.28, 4800)).astype(np.float32)
    data = pcm_to_wav(samples, 48000)
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        assert w.getnframes() == 4800


def test_wav_clips_out_of_range_samples():
    data = pcm_to_wav(np.array([5.0, -5.0], dtype=np.float32), 16000)
    with wave.open(io.BytesIO(data), "rb") as w:
        frames = np.frombuffer(w.readframes(2), dtype="<i2")
    assert frames[0] == 32767 and frames[1] == -32767


# ---------- загрузка модели и статус ----------

def test_disabled_by_default_and_reports_status(tmp_path):
    tts = SileroTTS({}, str(tmp_path))
    assert tts.status()["enabled"] is False
    with pytest.raises(TTSError):
        tts.synth("привет")


def test_missing_model_gives_clear_message_without_download(tmp_path):
    tts = SileroTTS({"SILERO_ENABLED": "1"}, str(tmp_path))
    with pytest.raises(TTSError) as exc:
        tts.load()
    assert "SILERO_AUTODOWNLOAD" in str(exc.value)
    assert tts.status()["error"]


def test_unknown_speaker_falls_back_to_default(tmp_path):
    tts = SileroTTS({"SILERO_SPEAKER": "nobody"}, str(tmp_path))
    assert tts.speaker == "xenia"


class _StubModel:
    def __init__(self):
        self.calls = []

    def apply_tts(self, text, speaker, sample_rate):
        self.calls.append((text, speaker, sample_rate))
        return np.zeros(sample_rate // 10, dtype=np.float32)   # 0.1 с тишины


def _stub_tts(tmp_path, **env):
    tts = SileroTTS({"SILERO_ENABLED": "1", **env}, str(tmp_path))
    tts._model = _StubModel()
    return tts


def test_synth_returns_wav_and_uses_speaker_and_rate(tmp_path):
    tts = _stub_tts(tmp_path, SILERO_SPEAKER="baya")
    data = tts.synth("**Привет**, `код` не читается")
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getframerate() == 48000
    text, speaker, rate = tts._model.calls[0]
    assert speaker == "baya" and rate == 48000
    assert "код" in text and "**" not in text


def test_synth_uses_requested_voice_without_changing_default(tmp_path):
    tts = _stub_tts(tmp_path, SILERO_SPEAKER="baya")
    tts.synth("Привет, как дела?", speaker="aidar")
    assert tts._model.calls[0][1] == "aidar"
    assert tts.speaker == "baya"          # общий голос не изменился: запросы не мешают друг другу


def test_synth_rejects_unknown_voice(tmp_path):
    tts = _stub_tts(tmp_path)
    with pytest.raises(ValueError, match="неизвестный голос"):
        tts.synth("Привет", speaker="robot")


def test_status_exposes_voices_with_previews(tmp_path):
    tts = SileroTTS({"SILERO_ENABLED": "1"}, str(tmp_path))
    voices = tts.status()["voices"]
    assert {v["id"] for v in voices} == set(SPEAKERS_RU)
    assert all(v.get("label") and v.get("preview") for v in voices)


def test_synth_rejects_text_that_is_only_code(tmp_path):
    tts = _stub_tts(tmp_path)
    with pytest.raises(TTSError):
        tts.synth("```print(1)```")


def test_synth_error_becomes_tts_error(tmp_path):
    tts = _stub_tts(tmp_path)

    def broken(*_args, **_kwargs):
        raise RuntimeError("apply_tts упал")

    tts._model.apply_tts = broken
    with pytest.raises(TTSError, match="apply_tts упал"):
        tts.synth("Обычный текст")
    assert "apply_tts упал" in tts.status()["error"]


def test_status_available_ignores_lazy_loading_but_shows_errors(tmp_path):
    tts = SileroTTS({"SILERO_ENABLED": "1"}, str(tmp_path))
    assert tts.status()["ready"] is False            # модель ещё не грузилась
    assert tts.status()["available"] is False        # файла нет и автозагрузка выключена
    tts.autodownload = True
    assert tts.status()["available"] is True         # первый синтез скачает модель сам
    tts.error = "не удалось загрузить Silero"
    assert tts.status()["available"] is False
    assert tts.status()["error"] == "не удалось загрузить Silero"


# ---------- HTTP маршруты ----------

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path, monkeypatch):
    env = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1",
           "AUTH_DISABLED": "1", "SILERO_ENABLED": "1",
           "AGENT_WORKSPACE_DIR": str(tmp_path / "data_unified")}
    app = App(env, data_dir=str(tmp_path / "data"))
    app.tts._model = _StubModel()
    port = _free_port()
    srv = make_server(app, "127.0.0.1", port, web_dir=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, app
    srv.shutdown()


def _req(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read()
    ctype = resp.getheader("Content-Type")
    conn.close()
    return resp.status, ctype, data


def test_status_route(server):
    port, _ = server
    status, _, data = _req(port, "GET", "/api/tts/status")
    body = json.loads(data)
    assert status == 200 and body["enabled"] is True and body["speaker"] == "xenia"


def test_status_retry_clears_stale_error(server):
    port, app = server
    app.tts.autodownload = True
    app.tts.error = "нет модуля torch"
    _, _, data = _req(port, "GET", "/api/tts/status")
    assert json.loads(data)["available"] is False
    _, _, data = _req(port, "GET", "/api/tts/status?retry=1")
    body = json.loads(data)
    assert body["available"] is True and body["error"] == ""


def test_segments_route_cleans_text(server):
    port, _ = server
    status, _, data = _req(port, "POST", "/api/tts/segments",
                           {"text": "Смотри `код` и ```x``` всё. Готово."})
    segs = json.loads(data)["segments"]
    assert status == 200 and segs and "x" not in " ".join(segs).split()


def test_speak_route_returns_wav(server):
    port, _ = server
    status, ctype, data = _req(port, "POST", "/api/tts", {"text": "Привет, как дела?"})
    assert status == 200 and ctype == "audio/wav"
    assert data[:4] == b"RIFF"


def test_speak_route_accepts_voice_and_rejects_unknown(server):
    port, _ = server
    status, _, data = _req(port, "POST", "/api/tts",
                           {"text": "Привет", "speaker": "aidar"})
    assert status == 200 and data[:4] == b"RIFF"
    status, _, data = _req(port, "POST", "/api/tts",
                           {"text": "Привет", "speaker": "robot"})
    assert status == 400 and "неизвестный голос" in json.loads(data).get("error", "")


def test_speak_route_unavailable_is_503_not_crash(tmp_path):
    env = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "AUTH_DISABLED": "1",
           "AGENT_WORKSPACE_DIR": str(tmp_path / "data_unified")}
    app = App(env, data_dir=str(tmp_path / "data"))          # озвучка выключена
    port = _free_port()
    srv = make_server(app, "127.0.0.1", port, web_dir=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _, data = _req(port, "POST", "/api/tts", {"text": "привет"})
        body = json.loads(data)
        assert status == 503 and body["error"] == "tts_unavailable"
    finally:
        srv.shutdown()


def test_default_model_path_is_next_to_agent_data(tmp_path):
    env = {"AGENT_WORKSPACE_DIR": str(tmp_path / "data_unified"), "LLM_BACKEND": "lmstudio",
           "TRIPLEX_EXTRACT": "0", "AUTH_DISABLED": "1"}
    app = App(env, data_dir=str(tmp_path / "data"))
    assert app.tts.model_path == os.path.join(str(tmp_path / "data_unified"), "tts", "v4_ru.pt")
