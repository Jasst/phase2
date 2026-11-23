# -*- coding: utf-8 -*-
"""Озвучка ответов: Silero TTS, полностью локально (CPU или GPU).

Что здесь:
  clean_for_speech   — убирает то, что нельзя читать вслух: код, таблицы, ссылки, разметку.
  split_segments     — режет текст на фразы: каждая озвучивается отдельно, пока генерация продолжается.
  pcm_to_wav         — упаковывает звук в WAV (браузер проигрывает напрямую).
  SileroTTS          — ленивая загрузка модели из файла SILERO_MODEL_PATH и синтез.

Модель: русский чекпойнт v4_ru (файл .pt). Скачать вручную с models.silero.ai или включить
SILERO_AUTODOWNLOAD=1 — тогда файл возьмётся один раз при первом обращении.
"""
from __future__ import annotations

import io
import os
import re
import threading
import urllib.request
import wave
from typing import List

import numpy as np

SAMPLE_RATE = 48000
SPEAKERS_RU = ("aidar", "baya", "kseniya", "xenia", "eugene")
DEFAULT_SPEAKER = "xenia"
# Подписи и короткие фразы для прослушивания в выпадающем списке голосов.
VOICE_INFO = {
    "aidar": {"label": "Айдар", "description": "низкий мужской",
              "preview": "Привет! Это Айдар. Мой голос низкий и спокойный."},
    "baya": {"label": "Бая", "description": "мягкий женский",
             "preview": "Привет! Это Бая. Мой голос мягкий и тёплый."},
    "kseniya": {"label": "Ксения", "description": "ровный женский",
                "preview": "Привет! Это Ксения. Мой голос ясный и ровный."},
    "xenia": {"label": "Ксения 2", "description": "живой женский",
              "preview": "Привет! Это Ксения. Мой голос звонкий и выразительный."},
    "eugene": {"label": "Евгений", "description": "мужской",
               "preview": "Привет! Это Евгений. Мой голос уверенный и глубокий."},
}
MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
MAX_SEGMENT = 240          # символов на одну фразу: короче — быстрее первая озвучка
MAX_TEXT = 6000            # потолок на весь ответ


class TTSError(RuntimeError):
    """Озвучка недоступна: выключена, модель не найдена или не загрузилась."""


def voice_list() -> List[dict]:
    """Голоса для интерфейса: id, подпись, описание и фраза-пример."""
    return [{"id": v, **VOICE_INFO.get(v, {"label": v, "description": "", "preview": ""})}
            for v in SPEAKERS_RU]


def normalize_speaker(value=None, default=None) -> str:
    """Проверить голос из запроса. Пустое значение = голос по умолчанию."""
    raw = str(value or "").strip().lower()
    if not raw:
        return default or DEFAULT_SPEAKER
    if raw not in SPEAKERS_RU:
        raise ValueError(f"неизвестный голос {raw!r}. Доступны: {', '.join(SPEAKERS_RU)}")
    return raw


# ---------- текст для речи ----------

_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_HEADER_MARK = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MARKS = re.compile(r"[*_~>|]+")
_TOOL_NOTE = re.compile(r"\[инструменты:[^\]]*\]\s*\(\w+\)")
_SOURCE_REF = re.compile(r"\[\d+\]")


def clean_for_speech(text: str) -> str:
    """Оставляет только текст, который осмысленно читать вслух."""
    t = str(text or "")[:MAX_TEXT * 2]
    t = _CODE_BLOCK.sub(" ", t)
    t = _IMAGE.sub(" ", t)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub(" ", t)
    t = _TABLE_LINE.sub(" ", t)
    t = _HEADER_MARK.sub("", t)
    t = _TOOL_NOTE.sub(" ", t)
    t = _SOURCE_REF.sub("", t)          # [1], [2] — номера источников
    t = _MARKS.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_TEXT]


_SENT_END = re.compile(r"(?<=[.!?…])\s+")


def split_segments(text: str, max_len: int = MAX_SEGMENT) -> List[str]:
    """Фразы для поочерёдной озвучки. Короткие склеиваются, длинные режутся по запятым и пробелам."""
    clean = clean_for_speech(text)
    if not clean:
        return []
    pieces: List[str] = []
    for sent in _SENT_END.split(clean):
        sent = sent.strip()
        if not sent:
            continue
        while len(sent) > max_len:
            cut = max(sent.rfind(", ", 0, max_len), sent.rfind(" ", 0, max_len))
            if cut < max_len // 3:
                cut = max_len
            pieces.append(sent[:cut].strip(" ,"))
            sent = sent[cut:].strip(" ,")
        if sent:
            pieces.append(sent)
    # склейка слишком коротких фраз (например «Да.»), чтобы не плодить запросов
    merged: List[str] = []
    for p in pieces:
        if merged and len(merged[-1]) < 40 and len(merged[-1]) + len(p) + 1 <= max_len:
            merged[-1] = merged[-1] + " " + p
        else:
            merged.append(p)
    return merged


# ---------- WAV ----------

def pcm_to_wav(samples, rate: int = SAMPLE_RATE) -> bytes:
    """Float-сэмплы в диапазоне [-1, 1] → 16-бит mono WAV."""
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ---------- Silero ----------

class SileroTTS:
    """Одна модель на процесс. Загрузка ленивая и под замком: первый запрос грузит, остальные ждут."""

    def __init__(self, env: dict, data_dir: str):
        self.enabled = str(env.get("SILERO_ENABLED", "0")).strip() == "1"
        try:
            self.speaker = normalize_speaker(env.get("SILERO_SPEAKER"), DEFAULT_SPEAKER)
        except ValueError:
            self.speaker = DEFAULT_SPEAKER
        self.device = str(env.get("SILERO_DEVICE", "cpu")).strip() or "cpu"
        self.autodownload = str(env.get("SILERO_AUTODOWNLOAD", "0")).strip() == "1"
        self.model_path = (env.get("SILERO_MODEL_PATH") or "").strip() or \
            os.path.join(data_dir, "tts", "v4_ru.pt")
        self._model = None
        self._lock = threading.Lock()
        self.error = ""

    # ----- состояние -----

    @property
    def ready(self) -> bool:
        return self._model is not None

    def reset_error(self) -> None:
        """Сбросить старую ошибку: зависимость или модель могли починить без перезапуска сервера."""
        self.error = ""

    def status(self) -> dict:
        model_exists = os.path.isfile(self.model_path)
        return {"enabled": self.enabled, "ready": self.ready, "speaker": self.speaker,
                "device": self.device, "model_path": self.model_path,
                "model_exists": model_exists, "autodownload": self.autodownload,
                "voices": voice_list(),
                # ready=false до первого синтеза (модель грузится лениво) — это не отказ сервера
                "available": self.enabled and not self.error and (model_exists or self.autodownload),
                "error": self.error}

    # ----- загрузка -----

    def _fetch_model(self) -> None:
        os.makedirs(os.path.dirname(self.model_path) or ".", exist_ok=True)
        tmp = self.model_path + ".part"
        with urllib.request.urlopen(MODEL_URL, timeout=120) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, self.model_path)

    def load(self):
        """Возвращает модель или бросает TTSError. Повторная попытка возможна после ошибки."""
        if not self.enabled:
            raise TTSError("озвучка выключена (SILERO_ENABLED=1 включает её)")
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                if not os.path.isfile(self.model_path):
                    if not self.autodownload:
                        raise TTSError(f"файл модели не найден: {self.model_path}. "
                                       f"Скачайте {MODEL_URL} или включите SILERO_AUTODOWNLOAD=1")
                    self._fetch_model()
                import torch  # импорт тяжёлый, поэтому только при первом запросе
                importer = torch.package.PackageImporter(self.model_path)
                model = importer.load_pickle("tts_models", "model")
                model.to(torch.device(self.device))
                self._model = model
                self.error = ""
                return model
            except TTSError as exc:
                self.error = str(exc)
                raise
            except Exception as exc:              # noqa: BLE001 — любая ошибка загрузки → статус, не падение
                self.error = f"не удалось загрузить Silero: {exc}"
                raise TTSError(self.error) from exc

    # ----- синтез -----

    def synth(self, text: str, speaker: str | None = None) -> bytes:
        """WAV для одной фразы. Голос можно передать в запросе — он не меняет общий self.speaker."""
        clean = clean_for_speech(text)
        if not clean:
            raise TTSError("нечего озвучивать")
        voice = normalize_speaker(speaker, self.speaker)  # неизвестный голос → ValueError → 400
        model = self.load()
        with self._lock:
            try:
                audio = model.apply_tts(text=clean[:MAX_SEGMENT * 2], speaker=voice,
                                        sample_rate=SAMPLE_RATE)
            except Exception as exc:              # noqa: BLE001 — ошибка синтеза → 503, а не 500
                self.error = f"не удалось синтезировать речь: {exc}"
                raise TTSError(self.error) from exc
        if hasattr(audio, "detach"):              # torch.Tensor
            audio = audio.detach().cpu().numpy()
        return pcm_to_wav(audio, SAMPLE_RATE)
