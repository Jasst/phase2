# -*- coding: utf-8 -*-
"""Сквозной тест чата по HTTP: фейковая модель (OpenAI-совместимый SSE) и фейковый SearXNG.
Проверяем порядок событий в потоке: текст → tool (running/done) → sources → текст → done."""
import http.client
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import pytest

from pulsenet.app import App
from pulsenet.server import make_server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeLLM(BaseHTTPRequestHandler):
    """Первый запрос модели — вызов web_search; после результата — ответ со ссылкой [1]."""
    requests_seen = []

    def log_message(self, *_a):
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            return self._json({"data": [{"id": "fake-model", "state": "loaded"}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode())
        _FakeLLM.requests_seen.append(body)
        has_result = any(m["role"] == "user" and "<tool_result" in m.get("content", "")
                         for m in body["messages"])
        if has_result:
            pieces = ["По источнику ", "[1] ответ готов."]
        else:
            pieces = ["Ищу. ", '<tool_call>{"name": "web_search", ',
                      '"arguments": {"queries": ["курс"]}}</tool_call>']
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in pieces:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


class _FakeSearx(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
        body = json.dumps({"results": [
            {"title": f"Результат по {q}", "url": "https://example.org/rate", "content": "выдержка"},
        ]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", _free_port()), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def stack(tmp_path, monkeypatch):
    llm = _serve(_FakeLLM)
    searx = _serve(_FakeSearx)
    _FakeLLM.requests_seen = []
    monkeypatch.setenv("SEARXNG_URL", f"http://127.0.0.1:{searx.server_address[1]}")
    monkeypatch.setenv("SEARCH_BACKENDS_ORDER", "searxng")
    env = {"LLM_BACKEND": "lmstudio",
           "LMSTUDIO_URL": f"http://127.0.0.1:{llm.server_address[1]}/v1",
           "LMSTUDIO_MODEL": "fake-model",
           "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1", "AUTH_DISABLED": "1"}
    app = App(env, data_dir=str(tmp_path))
    from pulsenet.unified_mcp import web
    web.clear_cache()
    srv = make_server(app, "127.0.0.1", _free_port(), web_dir=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    llm.shutdown()
    searx.shutdown()


def _post_chat(port, payload):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("POST", "/api/chat", body=json.dumps(payload, ensure_ascii=False).encode(),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read().decode("utf-8")
    conn.close()
    events = []
    for line in raw.splitlines():
        if line.startswith("data:") and line.strip() != "data: [DONE]":
            events.append(json.loads(line[5:].strip()))
    return events


def test_chat_with_web_streams_tool_then_sources_then_answer(stack):
    port = stack.server_address[1]
    events = _post_chat(port, {"message": "какой курс доллара?", "chat_id": "e2e", "web": True})

    kinds = []
    for ev in events:
        for k in ("token", "tool", "sources", "done", "error"):
            if k in ev:
                kinds.append(k)
                break
    assert kinds[-1] == "done", kinds
    assert "tool" in kinds and "sources" in kinds
    first_tool = kinds.index("tool")
    assert "token" in kinds[:first_tool]                  # «Ищу. » пришёл до вызова инструмента
    assert kinds.index("sources") > first_tool

    tools = [ev["tool"] for ev in events if "tool" in ev]
    assert [t["status"] for t in tools] == ["running", "done"]
    assert tools[0]["id"] == tools[1]["id"]

    sources = next(ev["sources"] for ev in events if "sources" in ev)
    assert sources[0]["url"] == "https://example.org/rate" and sources[0]["n"] == 1

    text = "".join(ev.get("token", "") for ev in events)
    assert "<tool_call>" not in text and "tool_result" not in text
    assert text == "Ищу. По источнику [1] ответ готов."

    done = next(ev["done"] for ev in events if "done" in ev)
    assert done["status"] == "done"

    # модель во втором запросе получила результат поиска с номером источника
    second = _FakeLLM.requests_seen[1]["messages"][-1]["content"]
    assert '<tool_result name="web_search">' in second and "[1] Результат по курс" in second


def test_chat_without_web_flag_does_not_offer_web_tools(stack):
    port = stack.server_address[1]
    _post_chat(port, {"message": "привет", "chat_id": "e2e2", "web": False})
    system = _FakeLLM.requests_seen[0]["messages"][0]["content"]
    assert "web_search" not in system
    assert "memory_search" in system
