# -*- coding: utf-8 -*-
"""Парсер .env: inline-комментарии не должны попадать в значения (это ломало Silero)."""
import os

from pulsenet.envfile import load_dotenv


def _load(tmp_path, text, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    for key in ("SILERO_MODEL_PATH", "SILERO_DEVICE", "TOKEN_WITH_HASH", "QUOTED"):
        monkeypatch.delenv(key, raising=False)
    assert load_dotenv(str(path)) > 0
    return path


def test_inline_comment_after_empty_value_gives_empty_string(tmp_path, monkeypatch):
    _load(tmp_path, "SILERO_MODEL_PATH=          # пусто — data_unified/tts/v4_ru.pt\n", monkeypatch)
    assert os.environ["SILERO_MODEL_PATH"] == ""


def test_inline_comment_after_value_is_stripped(tmp_path, monkeypatch):
    _load(tmp_path, "SILERO_DEVICE=cpu # или cuda\n", monkeypatch)
    assert os.environ["SILERO_DEVICE"] == "cpu"


def test_hash_without_space_stays_in_value(tmp_path, monkeypatch):
    _load(tmp_path, "TOKEN_WITH_HASH=abc#def\n", monkeypatch)
    assert os.environ["TOKEN_WITH_HASH"] == "abc#def"


def test_hash_inside_quotes_is_preserved(tmp_path, monkeypatch):
    _load(tmp_path, "QUOTED='abc # def'\n", monkeypatch)
    assert os.environ["QUOTED"] == "abc # def"
