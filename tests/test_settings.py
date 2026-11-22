# -*- coding: utf-8 -*-
"""Настройки интерфейса: серверное хранилище и API GET/PUT /api/settings."""
import http.client
import json
import os
import socket
import threading

import pytest

from pulsenet.app import App
from pulsenet.server import make_server
from pulsenet.settings_store import SettingsStore


def test_settings_store_roundtrip_and_uid_isolation(tmp_path):
    store = SettingsStore(str(tmp_path / "settings.sqlite"))
    assert store.get("u1") == {"settings": {}, "updated": 0}
    store.put("u1", {"ttsVoice": "aidar", "model": "qwen"})
    store.put("u2", {"ttsVoice": "baya"})
    assert store.get("u1")["settings"]["ttsVoice"] == "aidar"
    assert store.get("u2")["settings"]["ttsVoice"] == "baya"
    assert store.get("u2")["updated"] > 0
    store.put("u1", {"ttsVoice": "eugene"})
    assert store.get("u1")["settings"] == {"ttsVoice": "eugene"}
    # имитация перезапуска сервера: новое соединение с той же базой видит настройки
    reopened = SettingsStore(str(tmp_path / "settings.sqlite"))
    assert reopened.get("u1")["settings"]["ttsVoice"] == "eugene"
    assert reopened.get("u2")["settings"]["ttsVoice"] == "baya"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    env = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "AUTH_DISABLED": "1"}
    app = App(env, data_dir=str(tmp_path / "data"))
    port = _free_port()
    srv = make_server(app, "127.0.0.1", port, web_dir=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()


def _req(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, json.loads(data or b"{}")


def test_settings_api_saves_and_cleans(server):
    port = server
    status, data = _req(port, "GET", "/api/settings")
    assert status == 200 and data["settings"] == {}
    payload = {
        "compact": True, "autoScroll": False, "enterSends": True,
        "showReasoning": True, "webSearch": False, "fontSize": "xl",
        "model": "qwen2.5", "ttsVoice": "aidar", "updatedAt": 1700000000000,
        "junk": "не должно попасть в базу",
    }
    status, data = _req(port, "PUT", "/api/settings", payload)
    assert status == 200
    status, data = _req(port, "GET", "/api/settings")
    saved = data["settings"]
    assert status == 200
    assert saved["compact"] is True and saved["showReasoning"] is True
    assert saved["model"] == "qwen2.5" and saved["ttsVoice"] == "aidar"
    assert saved["fontSize"] == "m"                    # некорректный размер исправлен
    assert saved["updatedAt"] == 1700000000000
    assert "junk" not in saved


def test_settings_api_rejects_unknown_voice(server):
    port = server
    status, _ = _req(port, "PUT", "/api/settings", {"ttsVoice": "robot", "updatedAt": 1})
    assert status == 200
    _, data = _req(port, "GET", "/api/settings")
    assert data["settings"]["ttsVoice"] == ""
