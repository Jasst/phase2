# -*- coding: utf-8 -*-
"""MCP-слой ТРИПЛЕКСИИ: протокол JSON-RPC и инструменты (HTTP-маршрут живёт в unified MCP-сервере)."""
import pytest

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
