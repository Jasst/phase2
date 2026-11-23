# -*- coding: utf-8 -*-
"""Выбор модели детерминирован: пользователь / явный запрос / .env, но не «что загружено»."""
import pytest

import pulsenet.llm as llm
from pulsenet.app import App


class FakeBackend:
    name = "lmstudio"
    loaded = "fake"
    base_url = "http://fake/v1"

    def __init__(self):
        self.ensured = []
        self.unsupported_refresh = False

    def ensure(self, model, params=None):
        self.ensured.append(model)
        self.loaded = model or "fake"
        return self.loaded

    def refresh_loaded(self):
        if self.unsupported_refresh:
            raise AssertionError(" refresh_loaded не должен вызываться при выборе модели")
        return "other-model-in-lmstudio"

    def list_models(self):
        return ["configured/model", "other-model-in-lmstudio"]


def _app(tmp_path, model="configured/model"):
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_MODEL": model,
           "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1"}
    app = App(env, data_dir=str(tmp_path))
    app.backend = FakeBackend()
    return app


def test_default_is_configured_not_loaded(tmp_path):
    app = _app(tmp_path)
    app.backend.unsupported_refresh = True
    assert app.default_rel() == "configured/model"
    assert app.resolve_model() == "configured/model"
    assert app.extraction_model() == "configured/model"


def test_memory_operations_do_not_change_chosen_model(tmp_path):
    app = _app(tmp_path)
    app.memory_backend = "mcp"       # в MCP-режиме модель передаётся явно
    app.active_model = "chat/model"
    seen = []

    def fake_ingest(text, **kwargs):
        seen.append(kwargs)
        return {"status": "ok"}

    app.memory.ingest = fake_ingest

    app.ingest("текст", model="explicit/for-this-call")
    assert app.active_model == "chat/model"          # глобальный выбор не тронут
    assert seen[-1]["model"] == "explicit/for-this-call"

    app.ingest("ещё текст")
    assert seen[-1]["model"] == "chat/model"         # память идёт моделью чата
    assert app.backend.ensured == []                 # запись не загружает модель


def test_status_does_not_probe_loaded_model(tmp_path):
    app = _app(tmp_path)
    app.active_model = "chat/model"
    app.backend.loaded = "chat/model"
    app.backend.unsupported_refresh = True
    app.memory.stats = lambda: {}
    status = app.status()
    assert status["model"] == "chat/model"
    assert status["fact_model"] == "chat/model"


def test_lmstudio_list_models_is_read_only(monkeypatch):
    backend = llm.LMStudioBackend({"LMSTUDIO_URL": "http://fake/v1",
                                   "LMSTUDIO_MODEL": "configured/model"})
    backend.loaded = "configured/model"

    def fake_http_json(url, timeout=5):
        if url.endswith("/api/v0/models"):
            return {"data": [
                {"id": "other-model-in-lmstudio", "type": "llm", "state": "loaded"},
                {"id": "embedding", "type": "embeddings", "state": "loaded"},
            ]}
        return {"data": [{"id": "configured/model"}, {"id": "other-model-in-lmstudio"},
                         {"id": "embedding"}]}

    monkeypatch.setattr(llm, "http_json", fake_http_json)
    assert backend.list_models() == ["configured/model", "other-model-in-lmstudio"]
    assert backend.loaded == "configured/model"       # список не меняет выбор


def test_chat_uses_explicit_model_and_keeps_it(tmp_path):
    app = _app(tmp_path)

    def script(model, messages, on_delta, cancelled, **kwargs):
        app.seen_models.append(model)
        on_delta("token", "ok")

    app.seen_models = []
    app.backend.stream = script
    app.chat("первый", "c1", lambda kind, payload: None, model="user/choice")
    assert app.seen_models[-1] == "user/choice"
    assert app.active_model == "user/choice"

    # Второй ход без model использует уже выбранную модель, а не «первую загруженную».
    app.chat("второй", "c1", lambda kind, payload: None)
    assert app.seen_models[-1] == "user/choice"
    assert app.active_model == "user/choice"
    assert app.default_rel() == "configured/model"


def test_lmstudio_ensure_does_not_pick_loaded_model_without_choice(monkeypatch):
    backend = llm.LMStudioBackend({"LMSTUDIO_URL": "http://fake/v1", "LMSTUDIO_MODEL": ""})
    backend.loaded = None

    def forbidden_refresh(*_a, **_kw):
        raise AssertionError("ensure не должен опрашивать LM Studio и брать первую загруженную модель")

    monkeypatch.setattr(backend, "refresh_loaded", forbidden_refresh)
    monkeypatch.setattr(backend, "_known_ids", lambda: ["other-model-in-lmstudio"])
    with pytest.raises(llm.BackendError, match="модель не выбрана"):
        backend.ensure("")
    assert backend.loaded is None

    assert backend.ensure("other-model-in-lmstudio") == "other-model-in-lmstudio"
    assert backend.loaded == "other-model-in-lmstudio"


def test_llamacpp_external_does_not_invent_model(monkeypatch):
    backend = llm.LlamaCppBackend({"LLAMACPP_DEFAULT_MODEL": ""})
    backend.healthy = lambda: True
    backend.proc = None
    backend.loaded = None
    with pytest.raises(llm.BackendError, match="модель не выбрана"):
        backend.ensure("")
    assert backend.loaded is None

    assert backend.ensure("user/choice") == "user/choice"
    assert backend.loaded == "user/choice"
    assert backend.external is True


def test_status_falls_back_to_configured_not_backend_loaded(tmp_path):
    app = _app(tmp_path)
    app.backend.loaded = "other-model-in-lmstudio"
    app.memory.stats = lambda: {}
    assert app.status()["model"] == "configured/model"
    assert app.status()["fact_model"] == "configured/model"
