# -*- coding: utf-8 -*-
"""Тесты сервера и приложения: API чата и памяти, SSE, журнал ошибок, MCP поверх HTTP, «Сон»."""
import http.client
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pulsenet.app import App
from pulsenet.server import make_server
from pulsenet.triplex import sleep
from pulsenet.triplex.encoder import HashEncoder

TOKEN = "t-123"
import os
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
T0 = "2026-01-01T00:00:00+00:00"


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
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            request = json.loads(raw or b"{}")
        except ValueError:
            request = {}
        if not request.get("stream"):
            # обычный (не потоковый) ответ для HTTPTeacher / дистилляции
            body = json.dumps({"choices": [{"message": {
                "content": "Огонь — это горячее пламя."}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = ["<think>", "думаю", "</think>", "Огонь это ", "горячее пламя", "."]
        for c in chunks:
            payload = {"choices": [{"delta": {"content": c}}]}
            self.wfile.write(("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")




@pytest.fixture()
def running(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSENET_MCP_TOKEN", TOKEN)
    llm = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": f"http://127.0.0.1:{llm.server_address[1]}/v1",
           "LMSTUDIO_MODEL": "fake-model", "PULSENET_MCP_TOKEN": TOKEN, "PULSENET_MCP_HOSTS": "localhost",
           "TRIPLEX_EMBED_URL": ""}
    app = App(env, data_dir=str(tmp_path / "data"))
    srv = make_server(app, "127.0.0.1", 0, web_dir=WEB_DIR)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    llm.shutdown()
    llm.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, raw


def _json(port, method, path, body=None):
    status, raw = _req(port, method, path, body)
    return status, json.loads(raw or b"{}")


def test_health_and_index_page(running):
    app, port = running
    assert _json(port, "GET", "/health")[1]["ok"] is True
    for path in ("/", "/ai", "/ai.html"):
        status, raw = _req(port, "GET", path)
        assert status == 200 and "ТРИПЛЕКСИЯ".encode("utf-8") in raw
    status, raw = _req(port, "GET", "/static/app.css")
    assert status == 200 and b"--bg-primary" in raw
    status, raw = _req(port, "GET", "/sw.js")
    assert status == 200 and b"unregister" in raw
    status, _ = _req(port, "GET", "/static/../../pulsenet/server.py")
    assert status == 404


def test_status_shows_memory_and_backend(running):
    app, port = running
    status, body = _json(port, "GET", "/api/status")
    assert status == 200 and body["backend"] == "lmstudio"
    assert body["memory"]["facts_active"] == 0 and body["errors"] == []


def test_chat_streams_tokens_and_stores_turn(running):
    app, port = running
    status, raw = _req(port, "POST", "/api/chat", {"message": "привет", "chat_id": "c1"})
    assert status == 200
    text = raw.decode("utf-8")
    assert "data: " in text and "[DONE]" in text
    assert '"done"' in text
    assert app.wait_index()
    assert [t["role"] for t in app.history["c1"]] == ["user", "assistant"]


def test_stop_endpoint(running):
    app, port = running
    status, body = _json(port, "POST", "/api/chat/stop")
    assert status == 200 and body["status"] == "stopping"
    assert app.stop_flag is True


def test_failed_extraction_is_visible_in_status(running):
    """Фейковая LLM отвечает не JSON: экстракция падает, ошибка должна быть видна в /api/status."""
    app, port = running
    _req(port, "POST", "/api/chat", {"message": "Я живу в Амстердаме.", "chat_id": "c2"})
    assert app.wait_index()
    _, body = _json(port, "GET", "/api/status")
    assert any(e["where"] == "экстракция фактов" for e in body["errors"])
    assert body["memory"]["docs"] >= 1          # документ сохранён (I8)


def test_ingest_search_confirm_retract_roundtrip(running):
    app, port = running
    # экстрактор подменяем детерминированным: без модели
    from pulsenet.triplex import make_extractor
    text = "Кот лежит на коврике."
    app.memory.extractor = make_extractor(lambda p: json.dumps({"facts": [{
        "s": "кот", "r": "лежит_на", "o": "коврик", "evidence": "лежит на коврике", "conf": 0.9}]}))
    status, out = _json(port, "POST", "/api/memory/ingest", {"text": text, "recorded_at": T0})
    assert status == 200 and out["status"] == "ok"
    fid = out["facts"][0]["fact_id"]
    _, facts = _json(port, "GET", "/api/memory/facts")
    assert facts["facts"][0]["fact_id"] == fid
    _, res = _json(port, "POST", "/api/memory/confirm", {"fact_id": fid, "yes": True})
    assert res["confidence"] == pytest.approx(0.87)        # 0.9·0.8 = 0.72, +0.15
    _, res = _json(port, "POST", "/api/memory/retract", {"fact_id": fid})
    assert res["retracted"] == fid
    _, facts = _json(port, "GET", "/api/memory/facts")
    assert facts["facts"] == []


def test_confirm_caps_at_095_and_deny_halves(tmp_path):
    from pulsenet.triplex import TriplexMemory
    mem = TriplexMemory(str(tmp_path / "t"), encoder=HashEncoder(64))
    f = mem.journal.assert_fact("а", "б", "в", source={"doc_id": "d", "span": [0, 1]},
                                valid_from=T0, confidence=0.9)
    assert mem.confirm(f.fact_id, True)["confidence"] == pytest.approx(0.95)    # I9: потолок
    assert mem.confirm(f.fact_id, False)["confidence"] == pytest.approx(0.475)  # отказ ×0.5


def test_bad_json_and_unknown_fact_return_proper_codes(running):
    app, port = running
    status, body = _json(port, "POST", "/api/memory/retract", {"fact_id": "f:none"})
    assert status == 404
    status, _ = _req(port, "POST", "/api/memory/search", None)
    assert status == 400


def test_mcp_over_http_requires_token(running):
    app, port = running
    status, _ = _req(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                     headers={"Host": "localhost", "Authorization": "Bearer nope"})
    assert status == 401
    status, raw = _req(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                              "params": {"name": "triplex_whoami", "arguments": {}}},
                       headers={"Host": "localhost", "Authorization": f"Bearer {TOKEN}"})
    assert status == 200 and json.loads(raw)["result"]["isError"] is False


def test_consolidate_dry_run_writes_nothing_without_eligible(running):
    app, port = running
    _, body = _json(port, "POST", "/api/memory/consolidate")
    assert body["status"] == "nothing_eligible" and body["dataset"] is None


# ---------- «Сон» ----------

def _old_fact(mem, s, r, o, days_ago, accesses=3, conf=0.9, functional=False):
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    f = mem.journal.assert_fact(s, r, o, source={"doc_id": "d:x", "span": [0, 1]}, valid_from=when,
                                confidence=conf, functional=functional)
    for _ in range(accesses):
        mem.journal.access([f.fact_id], at=when)
    return f


def test_sleep_selects_old_confident_semantic_facts(tmp_path):
    mem = __import__("pulsenet.triplex", fromlist=["TriplexMemory"]).TriplexMemory(
        str(tmp_path / "t"), encoder=HashEncoder(64))
    mem.define_relation("родился_в", tau_days=None)
    mem.define_relation("видит", tau_days=30)            # эпизодическое: никогда не консолидируется
    good = _old_fact(mem, "Анна", "родился_в", "Казань", days_ago=30)
    _old_fact(mem, "кот", "видит", "мышь", days_ago=30)           # τ конечный
    _old_fact(mem, "Иван", "родился_в", "Пермь", days_ago=2)      # слишком молодой
    _old_fact(mem, "Пётр", "родился_в", "Тула", days_ago=30, accesses=1)   # мало обращений
    plan = sleep.plan_consolidation(mem)
    new_ids = [r["fact_id"] for r in plan["rows"] if r["kind"] == "new"]
    assert new_ids == [good.fact_id]
    assert plan["status"] == "dataset_ready"


def test_sleep_dataset_is_deterministic(tmp_path):
    from pulsenet.triplex import TriplexMemory
    mem = TriplexMemory(str(tmp_path / "t"), encoder=HashEncoder(64))
    mem.define_relation("родился_в", tau_days=None)
    for i in range(10):
        _old_fact(mem, f"п{i}", "родился_в", f"г{i}", days_ago=30)
    _old_fact(mem, "старый", "родился_в", "город", days_ago=30, accesses=0, conf=0.5)
    a = sleep.plan_consolidation(mem)["rows"]
    b = sleep.plan_consolidation(mem)["rows"]
    assert a == b and len(a) > 0


def test_adapter_gate_rejects_forgetting_and_ppl_rise():
    assert sleep.accept_adapter(0.90, 0.90, 10.0, 10.05)["accept"] is True
    assert sleep.accept_adapter(0.90, 0.85, 10.0, 10.0)["accept"] is False    # забыл 5%
    assert sleep.accept_adapter(0.90, 0.90, 10.0, 10.2)["accept"] is False    # ppl +2%


def test_client_disconnect_is_not_an_error():
    from pulsenet import server

    stopped = []

    class FakeApp:
        def stop(self):
            stopped.append(True)

        def chat(self, *_args, **_kwargs):
            raise ConnectionAbortedError(10053, "client aborted")

    class FakeHandler:
        app = FakeApp()
        wfile = None

        def send_response(self, *_args): pass
        def send_header(self, *_args): pass
        def end_headers(self): pass

    server._chat(FakeHandler(), {"message": "привет"}, None)
    assert stopped == [True]

    handler = object.__new__(server.Handler)

    def fail(_method):
        raise ConnectionAbortedError(10053, "client aborted")

    handler.dispatch = fail
    handler._safe("GET")


def test_train_without_gpu_stack_is_clear_error(tmp_path):
    with pytest.raises(sleep.SleepUnavailable):
        sleep.train("x.jsonl", str(tmp_path), str(tmp_path / "out"))
    with pytest.raises(sleep.SleepUnavailable):
        sleep.train("x.jsonl", "/нет/такой/папки", str(tmp_path / "out"))
