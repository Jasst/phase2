# -*- coding: utf-8 -*-
"""Единая память: перенос журналов, чат через настоящий MCP-сервер (общая память, автор факта),
поведение при недоступном сервере."""
import os
import shutil
import socket
import tempfile
import threading
import time

import pytest

pytest.importorskip("mcp.server.fastmcp")

from pulsenet import migrate_memory  # noqa: E402
from pulsenet.app import App  # noqa: E402
from pulsenet.mcp_client import MCPClient, MemoryUnavailable, _http_detail  # noqa: E402
from pulsenet.triplex import TriplexMemory  # noqa: E402
from pulsenet.triplex.encoder import HashEncoder  # noqa: E402
from pulsenet.unified_mcp.server import Settings, create_app  # noqa: E402


# ---------- перенос ----------

def _seed(root, text, facts):
    mem = TriplexMemory(root, encoder=HashEncoder(64))
    doc = mem.ingest(text, session_id="s", role="user", recorded_at="2026-01-01T00:00:00+00:00")
    for s, r, o in facts:
        mem.journal.assert_fact(s, r, o, source={"doc_id": doc["doc_id"], "span": [0, 5]})
    return doc["doc_id"]


def test_migration_merges_and_is_idempotent(tmp_path):
    a = str(tmp_path / "a")
    b = str(tmp_path / "b")
    dst = str(tmp_path / "dst")
    _seed(a, "Нел работает над BiChat.", [("Нел", "работает над", "BiChat")])
    _seed(b, "Проект на Python 3.12.", [("BiChat", "написан на", "Python")])

    rep = migrate_memory.merge_into(a, dst)
    assert rep["docs.jsonl"]["added"] == 1
    rep = migrate_memory.merge_into(b, dst)
    assert rep["docs.jsonl"]["added"] == 1 and rep["journal.jsonl"]["added"] == 1

    again = migrate_memory.merge_into(b, dst)                 # повторный запуск ничего не добавляет
    assert again["docs.jsonl"]["added"] == 0 and again["journal.jsonl"]["added"] == 0

    mem = TriplexMemory(dst, encoder=HashEncoder(64))
    assert len(mem.docs.docs) == 2
    assert len(mem.journal.query()) == 2
    assert migrate_memory.check(dst) == []


def test_migration_keeps_source_untouched(tmp_path):
    src = str(tmp_path / "src")
    _seed(src, "Тест сохранности.", [("A", "r", "B")])
    before = open(os.path.join(src, "journal.jsonl"), encoding="utf-8").read()
    migrate_memory.merge_into(src, str(tmp_path / "dst"))
    assert open(os.path.join(src, "journal.jsonl"), encoding="utf-8").read() == before


def test_dry_run_writes_nothing(tmp_path):
    src = str(tmp_path / "src")
    dst = str(tmp_path / "dst")
    _seed(src, "Пробный запуск.", [("X", "r", "Y")])
    rep = migrate_memory.merge_into(src, dst, dry_run=True)
    assert rep["docs.jsonl"]["added"] == 1
    assert not os.path.exists(os.path.join(dst, "docs.jsonl"))


# ---------- чат через настоящий MCP-сервер ----------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_stack():
    uvicorn = pytest.importorskip("uvicorn")
    data = tempfile.mkdtemp()
    cfg = Settings(data_dir=data, owner_token="OWNERTOKEN", owner_id="owner", memory_scope="shared",
                   env={"TRIPLEX_EXTRACT": "0"})
    app = create_app(cfg)
    chat_token = app.state.tokens.issue("chat")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}/mcp", "chat": chat_token, "owner": "OWNERTOKEN"}
    srv.should_exit = True


def _chat_app(tmp_path, url, token):
    env = {"LLM_BACKEND": "lmstudio", "PULSENET_MEMORY": "mcp", "PULSENET_MCP_URL": url,
           "PULSENET_CHAT_MCP_TOKEN": token, "TRIPLEX_EXTRACT": "0"}
    return App(env, data_dir=str(tmp_path))


def test_chat_writes_to_shared_memory_visible_to_owner_agents(mcp_stack, tmp_path):
    app = _chat_app(tmp_path, mcp_stack["url"], mcp_stack["chat"])
    assert app.memory_backend == "mcp"
    st = app.status()
    assert st["memory_online"] is True and st["memory_backend"] == "mcp"

    out = app.memory.ingest("Нел работает над BiChat. Проект на Python.", session_id="c1", role="user")
    assert out["status"] == "no_extractor"                   # на MCP-сервере экстрактор не задан
    doc_id = out["doc_id"]

    app.memory.client.call("triplex_assert", {"s": "Нел", "r": "работает над", "o": "BiChat",
                                              "doc_id": doc_id, "span": [0, 20]})
    facts = app.memory.list_facts(50)
    mine = [f for f in facts if f["text"] == "Нел работает над BiChat"]
    assert mine and mine[0]["agent"] == "chat"

    # владелец-агент (opencode и т. п.) видит тот же факт: память общая
    owner = MCPClient(mcp_stack["url"], mcp_stack["owner"]).call("triplex_facts", {"limit": 50})
    assert any(f["text"] == "Нел работает над BiChat" for f in owner["facts"])


def test_chat_ingest_forwards_selected_model_to_mcp(mcp_stack, tmp_path, monkeypatch):
    app = _chat_app(tmp_path, mcp_stack["url"], mcp_stack["chat"])
    app.active_model = "model-from-chat"
    calls = []
    real_call = app.memory.client.call

    def spy(name, args):
        calls.append((name, dict(args)))
        return real_call(name, args)

    monkeypatch.setattr(app.memory.client, "call", spy)
    app.ingest("Нел работает над BiChat.", session_id="c1", role="user")
    ingest = [a for n, a in calls if n == "triplex_ingest"][-1]
    assert ingest["model"] == "model-from-chat"
    assert app.status()["fact_model"] == "model-from-chat"


def test_chat_stats_and_status_through_mcp(mcp_stack, tmp_path):
    app = _chat_app(tmp_path, mcp_stack["url"], mcp_stack["chat"])
    stats = app.memory.stats()
    assert "facts_active" in stats and stats["facts_active"] >= 1
    assert app.status()["embedder"]["name"] is not None


def test_remote_rejects_bad_input_as_value_error(mcp_stack, tmp_path):
    app = _chat_app(tmp_path, mcp_stack["url"], mcp_stack["chat"])
    with pytest.raises(ValueError):
        app.memory.retract("нет-такого-факта")


def test_consolidation_says_it_is_local_only(mcp_stack, tmp_path):
    app = _chat_app(tmp_path, mcp_stack["url"], mcp_stack["chat"])
    out = app.consolidate_dry_run()
    assert out["status"] == "unavailable"


# ---------- недоступный сервер ----------

def test_offline_memory_does_not_break_chat(tmp_path, monkeypatch):
    from tests.test_agent import ScriptedBackend
    dead = f"http://127.0.0.1:{_free_port()}/mcp"            # никто не слушает
    app = _chat_app(tmp_path, dead, "any-token")
    app.backend = ScriptedBackend([["Отвечаю без памяти."]])

    st = app.status()
    assert st["memory_online"] is False

    assert app.memory_context("что угодно") == ""            # память молча пустая, ошибка — в журнале
    answer = app.chat("привет", "offline", lambda kind, payload: None)
    assert answer == "Отвечаю без памяти."


def test_client_without_token_is_unavailable():
    client = MCPClient("http://127.0.0.1:9/mcp", "")
    with pytest.raises(MemoryUnavailable):
        client.call("triplex_stats")


def test_http_error_is_unwrapped_from_taskgroup():
    import httpx

    request = httpx.Request("POST", "http://127.0.0.1:8010/mcp")
    response = httpx.Response(401, request=request)
    err = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)
    group = ExceptionGroup("unhandled errors in a TaskGroup", [err])
    detail = _http_detail(group)
    assert "401" in detail and "токен" in detail
