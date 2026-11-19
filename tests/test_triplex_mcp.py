# -*- coding: utf-8 -*-
"""MCP-слой ТРИПЛЕКСИИ: протокол JSON-RPC, инструменты и HTTP-маршрут /mcp с авторизацией."""
import http.client
import json
import threading

import pytest

from pulsenet.app import App
from pulsenet.server import make_server
from pulsenet.triplex import FactJournal, TriplexMCP

TOKEN = "test-token-123"
SRC = {"doc_id": "d:1", "span": [0, 10]}
T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-02-01T00:00:00+00:00"
T2 = "2026-03-01T00:00:00+00:00"


def rpc(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return msg


def call(name, **arguments):
    return rpc("tools/call", {"name": name, "arguments": arguments})


def structured(reply):
    assert "error" not in reply, reply
    return reply["result"]["structuredContent"]


# ---------- протокол (без HTTP) ----------

@pytest.fixture()
def mcp(tmp_path):
    return TriplexMCP(FactJournal(str(tmp_path / "j.jsonl")))


def test_initialize_negotiates_version_and_declares_tools(mcp):
    r = mcp.handle(rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "1"}}))
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert "tools" in r["result"]["capabilities"]
    assert r["result"]["serverInfo"]["name"] == "pulsenet-triplex"


def test_unknown_version_gets_server_default(mcp):
    r = mcp.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))
    assert r["result"]["protocolVersion"] == "2025-06-18"


def test_notification_gets_no_reply(mcp):
    assert mcp.handle(rpc("notifications/initialized", id_=None)) is None


def test_tools_list_contains_m0_tools(mcp):
    names = {t["name"] for t in mcp.handle(rpc("tools/list"))["result"]["tools"]}
    m0 = {"triplex_assert", "triplex_query", "triplex_retract", "triplex_stats", "triplex_check"}
    assert m0 <= names                      # M0 не сломан; M1–M4 добавляют инструменты сверху
    assert {"triplex_ingest", "triplex_search", "triplex_define_relation"} <= names


def test_unknown_method_is_jsonrpc_error(mcp):
    r = mcp.handle(rpc("resources/list"))
    assert r["error"]["code"] == -32601


def test_invalid_envelope_is_rejected(mcp):
    assert mcp.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600   # нет jsonrpc
    assert mcp.handle("строка")["error"]["code"] == -32600


def test_batch_returns_only_replies_for_requests(mcp):
    replies = mcp.handle([rpc("ping", id_=1), rpc("notifications/initialized", id_=None)])
    assert len(replies) == 1 and replies[0]["id"] == 1


def test_assert_then_query_roundtrip(mcp):
    a = structured(mcp.handle(call("triplex_assert", s="кот", r="лежит_на", o="коврик",
                                   source=SRC, valid_from=T0, functional=True)))
    assert a["status"] == "ok" and a["fact"]["o"] == "коврик"
    q = structured(mcp.handle(call("triplex_query", s="кот", r="лежит_на", as_of=T1)))
    assert q["count"] == 1 and q["facts"][0]["fact_id"] == a["fact"]["fact_id"]


def test_functional_conflict_is_reported_not_raised(mcp):
    structured(mcp.handle(call("triplex_assert", s="кот", r="лежит_на", o="коврик",
                               source=SRC, valid_from=T1, functional=True)))
    c = structured(mcp.handle(call("triplex_assert", s="кот", r="лежит_на", o="диван",
                                   source={"doc_id": "d:2", "span": [0, 3]},
                                   valid_from=T0, functional=True)))
    assert c["status"] == "conflict" and c["old_fact_id"]


def test_missing_provenance_is_tool_error_not_protocol_error(mcp):
    r = mcp.handle(call("triplex_assert", s="кот", r="любит", o="рыба", valid_from=T0))
    assert "error" not in r                      # не сбой протокола
    assert r["result"]["isError"] is True
    assert "source" in r["result"]["content"][0]["text"]


def test_retract_hides_fact_and_stats_count_it(mcp):
    a = structured(mcp.handle(call("triplex_assert", s="кот", r="любит", o="рыба",
                                   source=SRC, valid_from=T0)))
    structured(mcp.handle(call("triplex_retract", fact_id=a["fact"]["fact_id"], at=T1)))
    assert structured(mcp.handle(call("triplex_query", s="кот", as_of=T2)))["count"] == 0
    st = structured(mcp.handle(call("triplex_stats")))
    assert st == {"total": 1, "retracted": 1, "active_records": 0}


def test_retract_unknown_fact_is_tool_error(mcp):
    r = mcp.handle(call("triplex_retract", fact_id="f:none"))
    assert r["result"]["isError"] is True


def test_check_reports_clean_journal(mcp):
    structured(mcp.handle(call("triplex_assert", s="кот", r="любит", o="рыба",
                               source=SRC, valid_from=T0)))
    assert structured(mcp.handle(call("triplex_check"))) == {"ok": True, "violations": []}


def test_bad_argument_types_are_tool_errors(mcp):
    r = mcp.handle(call("triplex_assert", s="кот", r="любит", o="рыба", source=SRC, confidence="много"))
    assert r["result"]["isError"] is True
    r = mcp.handle(call("triplex_assert", s="кот", r="любит", o="рыба", source=SRC, functional="да"))
    assert r["result"]["isError"] is True


def test_unknown_tool_is_protocol_error_per_spec(mcp):
    r = mcp.handle(call("triplex_drop_all"))   # спецификация MCP: неизвестный инструмент → -32602
    assert r["error"]["code"] == -32602


def test_persistence_survives_restart(tmp_path):
    p = str(tmp_path / "j.jsonl")
    m1 = TriplexMCP(FactJournal(p))
    structured(m1.handle(call("triplex_assert", s="кот", r="любит", o="рыба", source=SRC, valid_from=T0)))
    m2 = TriplexMCP(FactJournal(p))
    assert structured(m2.handle(call("triplex_stats")))["total"] == 1


# ---------- HTTP-маршрут /mcp ----------

@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSENET_MCP_TOKEN", TOKEN)
    monkeypatch.setenv("PULSENET_MCP_HOSTS", "blockcoin.ru")
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1",
           "PULSENET_MCP_TOKEN": TOKEN, "PULSENET_MCP_HOSTS": "blockcoin.ru"}
    app = App(env, data_dir=str(tmp_path / "data"))
    srv = make_server(app, "127.0.0.1", 0, web_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _request(port, method, body, headers=None, path="/mcp"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Content-Type": "application/json", "Host": "localhost",
         "Authorization": f"Bearer {TOKEN}"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, hdrs, raw


def test_http_requires_bearer_token(server):
    status, hdrs, _ = _request(server, "POST", rpc("ping"), headers={"Authorization": "Bearer wrong"})
    assert status == 401 and "Bearer" in hdrs.get("WWW-Authenticate", "")
    status, _, _ = _request(server, "POST", rpc("ping"), headers={"Authorization": ""})
    assert status == 401


def test_http_ping_and_tool_call(server):
    status, _, raw = _request(server, "POST", rpc("ping"))
    assert status == 200 and json.loads(raw)["result"] == {}
    status, _, raw = _request(server, "POST", call("triplex_stats"))
    assert status == 200 and json.loads(raw)["result"]["isError"] is False


def test_http_notification_is_202_without_body(server):
    status, _, raw = _request(server, "POST", rpc("notifications/initialized", id_=None))
    assert status == 202 and raw == b""


def test_http_accepts_trailing_slash_as_nginx_rewrites(server):
    status, _, raw = _request(server, "POST", rpc("ping"), path="/mcp/")
    assert status == 200 and json.loads(raw)["id"] == 1


def test_http_parse_error_is_400_with_jsonrpc_error(server):
    status, _, raw = _request(server, "POST", b"{not json")
    assert status == 400 and json.loads(raw)["error"]["code"] == -32700


def test_http_host_and_origin_are_checked(server):
    status, _, _ = _request(server, "POST", rpc("ping"), headers={"Host": "evil.example"})
    assert status == 403
    status, _, _ = _request(server, "POST", rpc("ping"), headers={"Origin": "https://evil.example"})
    assert status == 403
    status, _, _ = _request(server, "POST", rpc("ping"),
                            headers={"Host": "blockcoin.ru", "Origin": "https://blockcoin.ru"})
    assert status == 200


def test_http_get_is_405_and_delete_is_200(server):
    status, hdrs, _ = _request(server, "GET", None)
    assert status == 405 and "POST" in hdrs.get("Allow", "")
    status, _, _ = _request(server, "DELETE", None)
    assert status == 200


def test_http_disabled_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("PULSENET_MCP_TOKEN", raising=False)
    app = App({"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"},
              data_dir=str(tmp_path / "data"))
    srv = make_server(app, "127.0.0.1", 0, web_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, _, _ = _request(srv.server_address[1], "POST", rpc("ping"))
        assert status == 503
    finally:
        srv.shutdown()
        srv.server_close()
