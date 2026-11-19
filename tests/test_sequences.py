# -*- coding: utf-8 -*-
"""Память порядка (цепочки слов), обработка ошибок LM Studio/llama и учителя."""
import json
import os
import tempfile
import urllib.error
from unittest.mock import patch

import pytest

from pulsenet import store
from pulsenet.config import Config
from pulsenet.llm import BackendError, LlamaCppBackend, LMStudioBackend
from pulsenet.net import BrainNet
from pulsenet.teacher import clean_answer


def _net(**kw):
    return BrainNet(Config(seed=0, **kw))


def test_taught_answer_is_reproduced_in_order():
    net = _net()
    net.teach("Что такое нейронная сеть?", "Сеть учится на примерах и помнит порядок слов.", replay=False)
    r = net.respond("Что такое нейронная сеть?", temp=0)
    assert r.text == "Сеть учится на примерах и помнит порядок слов."


def test_negative_feedback_removes_taught_sequence():
    net = _net()
    net.teach("Что такое сеть?", "Сеть хранит связи.", replay=False)
    assert net.respond("Что такое сеть?", temp=0).text == "Сеть хранит связи."
    for _ in range(3):
        net.learn("Что такое сеть?", "Сеть хранит связи.", weight=-2.0)
    sv, sa = net.vocab.index["связи"], net.vocab.index["хранит"]
    assert net.trans2.get((net.vocab.index["сеть"], net.vocab.index["сеть"]), {}).get(sa) is None
    assert sa not in net.trans1.get(sv, {})
    assert net.respond("Что такое сеть?", temp=0).text != "Сеть хранит связи."


def test_sequences_survive_save_and_load():
    net = _net()
    net.teach("Что делает память?", "Память хранит опыт сети.", replay=False)
    before = net.respond("Что делает память?", temp=0).text
    with tempfile.TemporaryDirectory() as d:
        store.save(net, d)
        loaded = store.load(d)
    assert loaded.trans1 == net.trans1
    assert loaded.trans2 == net.trans2
    assert loaded.respond("Что делает память?", temp=0).text == before


def test_old_state_without_sequences_still_loads():
    net = _net()
    net.teach("Что такое ток?", "Ток это движение зарядов.", replay=False)
    with tempfile.TemporaryDirectory() as d:
        store.save(net, d)
        meta_path = os.path.join(d, "meta.json")
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta.pop("trans1")
        meta.pop("trans2")
        meta["version"] = 5
        json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False)
        loaded = store.load(d)
    assert loaded.trans1 == {} and loaded.trans2 == {}


# ---------- LM Studio ----------

def test_lmstudio_rejects_model_id_it_does_not_know():
    b = LMStudioBackend({"LMSTUDIO_URL": "http://localhost:1234/v1"})
    with patch("pulsenet.llm.http_json", return_value={"data": [{"id": "qwen-7b"}]}):
        with pytest.raises(BackendError, match="не найдена в LM Studio"):
            b.ensure("lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q8_0.gguf")


def test_lmstudio_accepts_known_model_id():
    b = LMStudioBackend({"LMSTUDIO_URL": "http://localhost:1234/v1"})
    with patch("pulsenet.llm.http_json", return_value={"data": [{"id": "qwen-7b"}]}):
        assert b.ensure("qwen-7b") == "qwen-7b"
    assert b.loaded == "qwen-7b"


def test_lmstudio_http_error_reports_server_message():
    from pulsenet.llm import OpenAICompat

    class Backend(OpenAICompat):
        name = "lmstudio"
        base_url = "http://localhost:1234/v1"

    body = json.dumps({"error": {"message": "Model is not loaded"}}).encode()
    err = urllib.error.HTTPError("u", 400, "Bad Request", {}, None)
    err.read = lambda: body
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(BackendError, match="Model is not loaded"):
            Backend().stream("m", [], lambda k, t: None, lambda: False)


def test_llamacpp_log_goes_to_data_dir_not_models_dir(tmp_path):
    b = LlamaCppBackend({"MODELS_DIR": str(tmp_path / "models")}, log_dir=str(tmp_path / "data"))
    assert b.log_path == os.path.join(str(tmp_path / "data"), "llama-server.log")


def test_flash_attention_passes_value():
    b = LlamaCppBackend({"MODELS_DIR": "m", "LLAMACPP_SERVER_BIN": "srv"})
    args = b._args("a.gguf", {"flash_attn": True})
    i = args.index("-fa")
    assert args[i + 1] == "on"


# ---------- учитель ----------

def test_truncated_reasoning_never_becomes_teacher_answer():
    assert clean_answer("<think>нужно подумать о том, что сеть") == ""
    assert clean_answer("рассуждения</think>Сеть учится.") == "Сеть учится."
