# -*- coding: utf-8 -*-
import http.client
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pulsenet import Config, BrainNet
from pulsenet.app import App
from pulsenet.envfile import load_dotenv
from pulsenet.llm import LMStudioBackend, LlamaCppBackend, ThinkSplitter, make_backend
from pulsenet.server import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


class _FakeLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = ["<think>", "думаю", "</think>", "Огонь это ", "горячее пламя", "."]
        for c in chunks:
            payload = {"choices": [{"delta": {"content": c}}]}
            self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture()
def running(tmp_path):
    llm = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    env = {"LLM_BACKEND": "lmstudio",
           "LMSTUDIO_URL": f"http://127.0.0.1:{llm.server_address[1]}/v1",
           "LMSTUDIO_MODEL": "fake-model"}
    app = App(env, data_dir=str(tmp_path / "data"))
    srv = make_server(app, "127.0.0.1", 0, web_dir=WEB)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    llm.shutdown()
    llm.server_close()


def _req(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(method, path, body=data, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type") or "", raw


def test_pages_and_static_assets_are_served(running):
    _, port = running
    st, ct, raw = _req(port, "GET", "/")
    assert st == 200 and "text/html" in ct and "AI-ассистент".encode() in raw
    st, _, raw = _req(port, "GET", "/network")
    assert st == 200 and "Сеть импульсов".encode() in raw
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", "/memory")
    r = conn.getresponse(); r.read(); conn.close()
    assert r.status == 302 and r.getheader("Location") == "/network"
    st, ct, _ = _req(port, "GET", "/static/js/ai-app.js")
    assert st == 200 and "javascript" in ct
    st, _, _ = _req(port, "GET", "/static/vendor/marked.min.js")
    assert st == 200


def test_static_path_traversal_is_blocked(running):
    _, port = running
    st, _, _ = _req(port, "GET", "/static/../app.py")
    assert st in (403, 404)


def test_models_and_settings_come_from_backend(running):
    _, port = running
    st, _, raw = _req(port, "GET", "/ai/models")
    data = json.loads(raw)
    assert st == 200 and data["models"] == ["fake-model"] and data["backend"] == "lmstudio"
    st, _, raw = _req(port, "GET", "/ai/model-settings")
    s = json.loads(raw)
    assert s["editable"] is False and s["models"][0]["id"] == "fake-model"


def test_chat_streams_tokens_then_done_and_learns(running):
    app, port = running
    st, ct, raw = _req(port, "POST", "/ai/chat", {"message": "что такое огонь", "model": "fake-model", "chat_id": "c1"})
    assert st == 200 and "text/event-stream" in ct
    events = [json.loads(line[5:]) for line in raw.decode("utf-8").split("\n\n")
              if line.startswith("data:") and "[DONE]" not in line and line[5:].strip()]
    tokens = "".join(e["token"] for e in events if "token" in e)
    reasoning = "".join(e["reasoning_token"] for e in events if "reasoning_token" in e)
    assert tokens == "Огонь это горячее пламя."
    assert reasoning == "думаю"
    assert any("gen_phase" in e for e in events), "фазы генерации должны показываться в UI"
    assert raw.decode().rstrip().endswith("data: [DONE]")
    assert app.net.updates >= 1, "после ответа сеть должна дистиллировать пару"

    from urllib.parse import quote
    st, _, raw = _req(port, "GET", "/ai/chat/last_response?user_text=" + quote("что такое огонь"))
    assert json.loads(raw)["assistant"] == "Огонь это горячее пламя."


def test_impulse_trace_appears_after_learning(running):
    app, port = running
    app.net.teach("что такое огонь", "огонь это горячее пламя", 1.0, replay=False)
    st, _, raw = _req(port, "POST", "/ai/chat", {"message": "что такое огонь", "model": "fake-model", "chat_id": "c2"})
    assert '"trace"' in raw.decode("utf-8") and '"pulsenet"' in raw.decode("utf-8")


def test_network_teach_respond_graph_and_neurons(running):
    app, port = running
    st, _, raw = _req(port, "POST", "/net/teach", {"question": "что такое огонь", "answer": "огонь это горячее пламя"})
    res = json.loads(raw)
    assert st == 200 and res["ok"] and res["loss"] > 0
    assert res["stats"]["слов"] > 0
    for _ in range(3):
        _req(port, "POST", "/net/teach", {"question": "что такое огонь", "answer": "огонь это горячее пламя"})
    st, _, raw = _req(port, "POST", "/net/respond", {"question": "что такое огонь"})
    reply = json.loads(raw)
    assert st == 200 and reply["path"], "после уроков импульс должен пройти по ансамблям"
    assert all({"word", "ensemble", "mass"} <= set(step) for step in reply["path"])
    st, _, raw = _req(port, "GET", "/net/graph")
    graph = json.loads(raw)
    assert graph["nodes"] and graph["total_ensembles"] == len(graph["nodes"])
    assert all(e["weight"] > 0 for e in graph["edges"])
    k = graph["nodes"][0]["id"]
    st, _, raw = _req(port, "GET", f"/net/neurons?ensemble={k}")
    neurons = json.loads(raw)
    assert neurons["neurons"] and all("word" in n for n in neurons["neurons"])


def test_teach_is_online_and_persists_across_restart(tmp_path):
    from pulsenet.app import App
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}
    app = App(env, data_dir=str(tmp_path / "data"))
    before = app.net_state()["lessons"]
    app.teach("что такое огонь", "огонь это горячее пламя")
    app.teach("что такое вода", "вода это прозрачная жидкость")
    after = app.net_state()
    assert after["lessons"] == before + 2
    assert after["on_disk"] and after["size_bytes"] > 0
    assert len(after["history"]) == 2
    # перезапуск приложения: сеть подгружается с диска
    app2 = App(env, data_dir=str(tmp_path / "data"))
    assert app2.net_state()["stats"] == after["stats"]
    # сравниваем детерминированный ответ (temp=0): сэмплирование зависит от состояния RNG
    assert app2.respond("что такое огонь", temp=0)["text"] == app.respond("что такое огонь", temp=0)["text"]


def test_reset_keeps_backup_of_previous_network(tmp_path):
    from pulsenet import store
    from pulsenet.app import App
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}
    app = App(env, data_dir=str(tmp_path / "data"))
    app.teach("alpha beta", "gamma delta")
    saved = app.net_state()["stats"]
    state = app.reset_net()
    assert app.net_state()["stats"]["слов"] == 0 and state["backup"]
    assert app.load_net()["ok"] is False  # сохранённой сети на диске больше нет
    restored = store.load(state["backup"])
    assert restored.stats() == saved
    back = app.restore_backup()
    assert back["ok"] and app.net_state()["stats"] == saved


def test_ui_state_roundtrip(running):
    _, port = running
    _req(port, "PUT", "/ai/ui-state", {"chats": {"c1": {"title": "огонь"}}})
    st, _, raw = _req(port, "GET", "/ai/ui-state")
    assert json.loads(raw)["chats"]["c1"]["title"] == "огонь"
    st, _, raw = _req(port, "GET", "/ai/ui-state/version")
    assert json.loads(raw)["updated"] >= 1


def test_busy_generation_returns_409(running):
    app, port = running
    assert app.begin_generation()
    try:
        st, _, raw = _req(port, "POST", "/ai/chat", {"message": "привет"})
        assert st == 409
    finally:
        app.end_generation()


def test_think_splitter_handles_split_tags():
    s = ThinkSplitter()
    out = []
    for piece in ["<thi", "nk>раз", "мышляю</thi", "nk>Ответ"]:
        out += s.feed(piece)
    out += s.flush()
    assert "".join(t for k, t in out if k == "token") == "Ответ"
    assert "".join(t for k, t in out if k == "reasoning") == "размышляю"


def test_backend_selection_from_env():
    assert isinstance(make_backend({"LLM_BACKEND": "lmstudio"}), LMStudioBackend)
    assert isinstance(make_backend({"LLM_BACKEND": "llamacpp"}), LlamaCppBackend)
    assert isinstance(make_backend({}), LlamaCppBackend)
    with pytest.raises(Exception):
        make_backend({"LLM_BACKEND": "unknown"})


def test_llamacpp_model_path_uses_models_dir(tmp_path):
    env = {"MODELS_DIR": str(tmp_path), "LLAMACPP_PORT": "8090",
           "LLAMACPP_DEFAULT_MODEL": "lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q8_0.gguf"}
    b = LlamaCppBackend(env)
    assert b.port == 8090 and b.base_url == "http://127.0.0.1:8090/v1"
    assert b.model_path(env["LLAMACPP_DEFAULT_MODEL"]) == os.path.join(
        str(tmp_path), "lmstudio-community", "Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q8_0.gguf")


def test_dotenv_does_not_override_existing_env(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('LLM_BACKEND=lmstudio\nMODELS_DIR="E:\\models"\n# комментарий\nNEW_KEY=1\n', encoding="utf-8")
    monkeypatch.setenv("LLM_BACKEND", "llamacpp")
    monkeypatch.delenv("MODELS_DIR", raising=False)
    monkeypatch.delenv("NEW_KEY", raising=False)
    load_dotenv(str(f))
    assert os.environ["LLM_BACKEND"] == "llamacpp"
    assert os.environ["MODELS_DIR"] == "E:\\models"
    assert os.environ["NEW_KEY"] == "1"
