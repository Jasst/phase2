# -*- coding: utf-8 -*-
"""Эмбеддер bge-m3: процесс стартует один раз, кэш не пересчитывает тексты после перезапуска."""
import os
import socket
import sys

import pytest

from pulsenet.app import App
from pulsenet.triplex.embed_server import EmbeddingServer, EmbeddingServerError
from pulsenet.triplex.encoder import CachedEncoder, OpenAIEmbedder

STUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stub_embed_server.py")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _counter_lines(path):
    if not os.path.exists(path):
        return []
    return [l.strip() for l in open(path, encoding="utf-8") if l.strip()]


@pytest.fixture()
def stub_env(tmp_path, monkeypatch):
    counter = str(tmp_path / "counter.txt")
    monkeypatch.setenv("STUB_COUNTER", counter)
    model = tmp_path / "bge-m3.gguf"
    model.write_bytes(b"not a real model: the stub does not read it")
    return {"counter": counter, "model": str(model)}


def _server(stub_env, tmp_path, port):
    return EmbeddingServer(sys.executable, stub_env["model"], port, prefix=[STUB],
                           log_path=str(tmp_path / "embed.log"), startup_timeout=30)


def test_embedding_process_starts_once_and_is_reused(stub_env, tmp_path):
    srv = _server(stub_env, tmp_path, _free_port())
    try:
        url1 = srv.start()
        url2 = srv.start()                      # повторный вызов — тот же процесс
        assert url1 == url2 == srv.base_url
        assert srv.starts == 1
        pid = srv.proc.pid
        assert srv.start() and srv.proc.pid == pid
        starts = [l for l in _counter_lines(stub_env["counter"]) if l.startswith("start")]
        assert len(starts) == 1                  # модель «загружена» один раз
    finally:
        srv.stop()
    assert not srv.is_running()


def test_cache_avoids_recomputing_after_restart(stub_env, tmp_path):
    port = _free_port()
    srv = _server(stub_env, tmp_path, port)
    srv.start()
    try:
        cache = str(tmp_path / "cache.jsonl")
        texts = [f"факт номер {i}" for i in range(5)]
        enc1 = CachedEncoder(OpenAIEmbedder(srv.base_url, "bge-m3", 16), cache, tag="bge-m3|16|cls")
        v1 = enc1.embed(texts)
        assert v1.shape == (5, 16)
        sent_first = sum(int(l.split()[1]) for l in _counter_lines(stub_env["counter"]) if l.startswith("texts"))
        assert sent_first == 5

        # «перезапуск приложения»: новый объект читает тот же файл кэша
        enc2 = CachedEncoder(OpenAIEmbedder(srv.base_url, "bge-m3", 16), cache, tag="bge-m3|16|cls")
        v2 = enc2.embed(texts)
        sent_total = sum(int(l.split()[1]) for l in _counter_lines(stub_env["counter"]) if l.startswith("texts"))
        assert sent_total == 5                   # второй раз ни одного текста в модель не ушло
        assert (v1 == v2).all()
    finally:
        srv.stop()


def test_cache_key_changes_with_model_tag(stub_env, tmp_path):
    port = _free_port()
    srv = _server(stub_env, tmp_path, port)
    srv.start()
    try:
        cache = str(tmp_path / "cache.jsonl")
        CachedEncoder(OpenAIEmbedder(srv.base_url, "bge-m3", 16), cache, tag="A").embed(["x"])
        before = sum(int(l.split()[1]) for l in _counter_lines(stub_env["counter"]) if l.startswith("texts"))
        CachedEncoder(OpenAIEmbedder(srv.base_url, "bge-m3", 16), cache, tag="B").embed(["x"])
        after = sum(int(l.split()[1]) for l in _counter_lines(stub_env["counter"]) if l.startswith("texts"))
        assert after == before + 1               # другая модель — свой кэш, пересчёт обязателен
    finally:
        srv.stop()


def test_missing_model_file_is_a_clear_error(tmp_path):
    srv = EmbeddingServer(sys.executable, str(tmp_path / "нет.gguf"), _free_port())
    with pytest.raises(EmbeddingServerError):
        srv.start()


def test_app_falls_back_to_offline_encoder_and_logs_when_embedder_fails(tmp_path):
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1",
           "TRIPLEX_EMBED_MODEL_PATH": str(tmp_path / "нет.gguf"), "TRIPLEX_EXTRACT": "0"}
    app = App(env, data_dir=str(tmp_path / "data"))
    assert app.memory.encoder.name == "hash"            # чат остаётся рабочим
    assert any(e["where"] == "эмбеддер (запуск)" for e in app.recent_errors())
    assert app.status()["embedder"]["server_running"] is False


# ---------- префиксы задач (nomic, e5) ----------

from pulsenet.triplex.encoder import default_prefixes  # noqa: E402


def test_default_prefixes_by_model_name():
    assert default_prefixes("nomic-embed-text-v1.5") == ("search_document: ", "search_query: ")
    assert default_prefixes("multilingual-e5-large") == ("passage: ", "query: ")
    assert default_prefixes("bge-m3") == ("", "")


def test_query_and_document_get_different_prefixes(monkeypatch):
    sent = []
    enc = OpenAIEmbedder("http://x/v1", "nomic", 4, doc_prefix="search_document: ", query_prefix="search_query: ")
    monkeypatch.setattr(enc, "_post", lambda items: (sent.append(items) or
                        {"data": [{"index": i, "embedding": [1.0, 0.0, 0.0, 0.0]} for i in range(len(items))]}))
    enc.embed(["кот"])
    enc.embed_query(["кот"])
    assert sent == [["search_document: кот"], ["search_query: кот"]]


class _CountingInner:
    name = "count"
    dim = 4

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(("doc", list(texts)))
        import numpy as np
        return np.ones((len(texts), 4), dtype=np.float32)

    def embed_query(self, texts):
        self.calls.append(("query", list(texts)))
        import numpy as np
        return np.ones((len(texts), 4), dtype=np.float32) * 2


def test_cache_keeps_query_and_document_vectors_separate(tmp_path):
    inner = _CountingInner()
    enc = CachedEncoder(inner, str(tmp_path / "c.jsonl"), tag="t")
    d = enc.embed(["кот"])
    q = enc.embed_query(["кот"])
    assert not (d == q).all()                       # один и тот же текст — два разных вектора
    assert inner.calls == [("doc", ["кот"]), ("query", ["кот"])]
    enc.embed(["кот"]); enc.embed_query(["кот"])    # повторно — из кэша
    assert len(inner.calls) == 2


# ---------- автоопределение модели и размерности ----------

from pulsenet.triplex.encoder import list_model_ids, pick_embedding_model, probe_dim  # noqa: E402


def test_pick_embedding_model_skips_chat_and_rerankers():
    assert pick_embedding_model(["qwen-chat", "text-embedding-bge-m3"]) == "text-embedding-bge-m3"
    assert pick_embedding_model(["bge-reranker-v2", "nomic-embed-text-v1.5"]) == "nomic-embed-text-v1.5"
    assert pick_embedding_model(["llama-3-8b", "qwen-chat"]) is None


def test_probe_dim_and_model_list_from_running_server(stub_env, tmp_path):
    srv = _server(stub_env, tmp_path, _free_port())
    srv.start()
    try:
        assert "text-embedding-bge-m3" in list_model_ids(srv.base_url)
        assert probe_dim(srv.base_url, "text-embedding-bge-m3") == 16     # размерность заглушки
    finally:
        srv.stop()


def test_app_detects_model_and_dim_without_manual_settings(stub_env, tmp_path):
    srv = _server(stub_env, tmp_path, _free_port())
    srv.start()
    try:
        env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1", "TRIPLEX_EXTRACT": "0",
               "TRIPLEX_EMBED_URL": srv.base_url}                      # ни модели, ни размерности
        app = App(env, data_dir=str(tmp_path / "data"))
        assert app.memory.encoder.name == "openai+cache"
        assert app.memory.encoder.dim == 16
        assert app.embed_model == "text-embedding-bge-m3"
        assert app.status()["embedder"]["model"] == "text-embedding-bge-m3"
        assert app.recent_errors() == []
    finally:
        srv.stop()


def test_app_warns_when_manual_dim_disagrees(stub_env, tmp_path):
    srv = _server(stub_env, tmp_path, _free_port())
    srv.start()
    try:
        env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1", "TRIPLEX_EXTRACT": "0",
               "TRIPLEX_EMBED_URL": srv.base_url, "TRIPLEX_EMBED_DIM": "1024"}
        app = App(env, data_dir=str(tmp_path / "data"))
        assert app.memory.encoder.dim == 16                             # модель главнее ручного значения
        assert any(e["where"] == "эмбеддер (размерность)" for e in app.recent_errors())
    finally:
        srv.stop()
