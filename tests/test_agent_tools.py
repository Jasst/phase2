# -*- coding: utf-8 -*-
"""Агентный цикл 2.0: динамический каталог MCP, image_generate, лимиты и доставка картинки.

Сеть не нужна: MCP-клиент и генератор подменяются простыми объектами.
"""
import json

from pulsenet.app import ASSISTANT_PERSONA, App
from pulsenet.mcp_client import MCPClient, _content_text
from pulsenet.server import ChatJob
from pulsenet.toolcalls import agent_limits, tool_result_block
from pulsenet.tools import IMAGE_TOOL, ChatTools, catalog, instructions
from pulsenet.triplex import TriplexMemory
from pulsenet.triplex.encoder import HashEncoder


class ScriptedBackend:
    name = "lmstudio"
    loaded = "fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.base_url = "http://fake/v1"
        self.seen = []

    def ensure(self, model, params=None):
        return model or "fake"

    def stream(self, model, messages, on_delta, cancelled):
        self.seen.append([dict(m) for m in messages])
        for piece in self.scripts.pop(0):
            on_delta("token", piece)


def _app(tmp_path, scripts, env=None):
    base = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1"}
    base.update(env or {})
    app = App(base, data_dir=str(tmp_path))
    app.backend = ScriptedBackend(scripts)
    return app


def _image_data():
    return {"status": "ok", "backend": "easydiffusion", "url": "/images/u1/a.png",
            "prompt": "a cat", "seed": 42, "width": 512, "height": 768,
            "steps": 30, "bytes": 123456, "attempts": []}


# ---------- лимиты и протокол ----------

def test_agent_limits_defaults_and_overrides():
    lim = agent_limits({})
    assert (lim["max_rounds"], lim["hard_cap"], lim["max_calls"]) == (6, 8, 12)
    assert lim["reflection_budget"] == 2 and lim["result_chars"] == 1200
    lim = agent_limits({"AGENT_MAX_ROUNDS": "9", "AGENT_MAX_CALLS": "3",
                        "TOOL_RESULT_CONTEXT_CHARS": "50"})
    assert lim["max_rounds"] == 7          # потолок параметра
    assert lim["hard_cap"] >= lim["max_rounds"]
    assert lim["max_calls"] == 3 and lim["result_chars"] == 200


def test_tool_result_block_custom_limit():
    block = tool_result_block("image_generate", "x" * 5000, 80)
    assert "обрезано" in block and len(block) < 150


def test_content_text_skips_image_blocks():
    class Block:
        def __init__(self, text=None):
            if text is not None:
                self.text = text

    blocks = [Block(), Block('{"status": "ok"}'), {"text": "второй"}]
    assert _content_text(blocks) == '{"status": "ok"}\nвторой'
    assert _content_text([Block()]) == "{}"


# ---------- каталог инструментов ----------

def test_image_tool_is_in_catalog_and_instructions():
    names = [t["name"] for t in catalog()["tools"]]
    assert IMAGE_TOOL in names
    text = instructions([IMAGE_TOOL])
    assert "image_generate(prompt" in text and "изображение" in text.lower()


def test_chat_tools_offers_mcp_tools_only_with_bridge():
    recall = lambda q, k: {"facts": [], "chunks": []}
    plain = ChatTools(recall=recall, remember=lambda t: {"status": "queued"}, web=False)
    assert IMAGE_TOOL not in plain.available(web=False, memory=True)

    bridged = ChatTools(recall=recall, remember=lambda t: {"status": "queued"}, web=False,
                        mcp_call=lambda *a: {"status": "ok"},
                        mcp_specs={"triplex_facts": {"mcp_name": "triplex_facts",
                                                     "description": "факты",
                                                     "parameters": {"type": "object", "properties": {}}}})
    offered = bridged.available(web=False, memory=True)
    assert IMAGE_TOOL in offered and "triplex_facts" in offered
    assert "prompt" in bridged.specs(offered)[IMAGE_TOOL]["parameters"]["properties"]


def test_image_generate_returns_attachment_and_short_model_text():
    seen = []

    def fake_call(name, args, timeout=None):
        seen.append((name, args, timeout))
        return _image_data()

    tools = ChatTools(recall=lambda q, k: {}, remember=lambda t: {}, web=False,
                      mcp_call=fake_call)
    out = tools.run(IMAGE_TOOL, {"prompt": "a cat", "width": 512, "enhance_prompt": True})
    assert out.ok and out.image is not None
    assert out.image["url"] == "/images/u1/a.png" and out.image["seed"] == 42
    assert "/images/" not in out.text                 # модель не должна видеть URL
    assert "показано пользователю" in out.text
    assert seen[0][0] == IMAGE_TOOL and seen[0][1]["return_image"] is False
    assert "enhance_prompt" not in seen[0][1]
    assert seen[0][2] == 300                          # таймаут генерации


def test_image_generate_error_becomes_result():
    tools = ChatTools(recall=lambda q, k: {}, remember=lambda t: {}, web=False,
                      mcp_call=lambda *a: {"status": "error", "message": "нет бэкендов"})
    out = tools.run(IMAGE_TOOL, {"prompt": "a cat"})
    assert not out.ok and out.image is None and "нет бэкендов" in out.text


def test_discovery_fallback_keeps_image_tool(tmp_path):
    app = _app(tmp_path, [["ok"]], env={"PULSENET_CHAT_TOOLS": "1",
                                        "PULSENET_CHAT_MCP_TOKEN": "fake"})

    class Broken:
        token = "fake"

        def list_tools(self):
            raise RuntimeError("сервер выключен")

    app._mcp = Broken()
    specs = app._mcp_tool_specs()
    assert IMAGE_TOOL in specs and specs[IMAGE_TOOL]["timeout"] == 300
    assert any("обнаружение" in e["where"] for e in app.recent_errors())


def test_discovery_limits_and_skips_code_tools(tmp_path):
    app = _app(tmp_path, [["ok"]], env={"PULSENET_CHAT_TOOLS": "1",
                                        "PULSENET_CHAT_MCP_TOKEN": "fake",
                                        "TOOL_NATIVE_MAX": "4"})

    class Live:
        token = "fake"

        def list_tools(self):
            return [{"name": n, "description": n, "inputSchema": {"type": "object", "properties": {}}}
                    for n in ("triplex_search", "code_read", "activity_recent", "triplex_facts",
                              "web_search")]

    app._mcp = Live()
    specs = app._mcp_tool_specs()
    assert IMAGE_TOOL in specs
    assert "triplex_search" in specs and "activity_recent" in specs
    assert not any(n.startswith("code_") for n in specs)
    assert "mcp__web_search" not in specs            # локальное имя не переопределяем
    assert len(specs) <= 4


# ---------- агентный цикл с картинкой ----------

def test_chat_calls_image_tool_and_emits_sse_image(tmp_path):
    call = ('<tool_call>{"name": "image_generate", '
            '"arguments": {"prompt": "a cat"}}</tool_call>')
    app = _app(tmp_path, [["Рисую. ", call], ["Готово, вот котик."]])
    app.mcp_tools_enabled = True
    app._mcp_tool_specs = lambda: {}
    calls = []

    def fake_call(name, args, timeout=None):
        calls.append((name, args, timeout))
        return _image_data()

    app._mcp_tool_call = fake_call
    events, tokens = [], []

    def on_delta(kind, payload):
        if kind == "token":
            tokens.append(payload)
        else:
            events.append((kind, payload))

    answer = app.chat("нарисуй котика", "img", on_delta)
    assert answer == "Рисую. Готово, вот котик."
    assert calls and calls[0][0] == IMAGE_TOOL
    images = [p for k, p in events if k == "image"]
    assert len(images) == 1 and images[0]["url"] == "/images/u1/a.png"
    assert any(k == "tool" and p["status"] == "done" for k, p in events)
    assert "/images/" not in answer

    # персона и схема image_generate видны модели в первом сообщении
    system = app.backend.seen[0][0]["content"]
    assert "ассистент ТРИПЛЕКСИИ" in system
    assert "image_generate(prompt" in system
    assert ASSISTANT_PERSONA in system


def test_chat_dedups_repeated_tool_calls(tmp_path):
    call = '<tool_call>{"name": "memory_search", "arguments": {"query": "x"}}</tool_call>'
    app = _app(tmp_path, [[call], [call], ["Ответ."]])
    calls = []
    original = app._call_tool

    def counting(tools, name, args, on_delta):
        calls.append(name)
        return original(tools, name, args, on_delta)

    app._call_tool = counting
    events = []
    answer = app.chat("вопрос", "dedup", lambda k, p: events.append((k, p)))
    assert answer == "Ответ."
    assert calls == ["memory_search"]                # второй вызов не исполнялся
    assert any(k == "tool" and p.get("detail") == "результат из кэша" for k, p in events)


def test_chat_multistep_chain_runs_to_answer(tmp_path):
    mem = '<tool_call>{"name": "memory_search", "arguments": {"query": "факт"}}</tool_call>'
    img = '<tool_call>{"name": "image_generate", "arguments": {"prompt": "p"}}</tool_call>'
    app = _app(tmp_path, [[mem], [img], ["Собрал всё: факт и картинка."]])
    app.mcp_tools_enabled = True
    app._mcp_tool_specs = lambda: {}
    app._mcp_tool_call = lambda *a, **kw: _image_data()
    events = []
    answer = app.chat("найди и нарисуй", "chain",
                      lambda k, p: events.append((k, p)))
    assert answer == "Собрал всё: факт и картинка."
    chips = [p for k, p in events if k == "tool" and p["status"] == "done"]
    assert [c["name"] for c in chips] == ["memory_search", IMAGE_TOOL]
    assert any(k == "image" for k, _ in events)
    assert len(app.backend.seen) == 3


def test_chat_stops_at_round_limit_with_text_answer(tmp_path):
    call = '<tool_call>{"name": "memory_search", "arguments": {"query": "x"}}</tool_call>'
    app = _app(tmp_path, [[call] for _ in range(9)])
    answer = app.chat("вопрос", "limit", lambda k, p: None)
    assert len(app.backend.seen) == app.limits["max_rounds"] + 1
    assert "лимит" in answer.lower()                  # пользователь не видит пустой ответ
    assert "(round_limit)" in app._history("limit")[-2]["content"]


def test_chat_reflection_on_tool_error(tmp_path):
    bad = '<tool_call>{"name": "unknown_tool", "arguments": {}}</tool_call>'
    app = _app(tmp_path, [[bad], ["Понял, отвечаю без инструмента."]])
    answer = app.chat("вопрос", "err", lambda k, p: None)
    assert answer.endswith("без инструмента.")
    assert any("ошибку" in m["content"] for m in app.backend.seen[1]
               if m["role"] == "system")


# ---------- SSE ----------

def test_job_streams_image_event_with_position():
    job = ChatJob("c", "m")
    job.add_delta("token", "Рисую ")
    job.add_delta("image", {"url": "/images/u/a.png", "seed": 1})
    job.add_delta("token", "готово")
    snap = job.snapshot()
    assert snap["text"] == "Рисую готово"
    assert snap["events"][0]["kind"] == "image"
    assert snap["events"][0]["at"] == len("Рисую ")


# ---------- память не сломана добавлением моста ----------

def test_local_memory_still_works_without_mcp_bridge(tmp_path):
    mem = TriplexMemory(str(tmp_path), encoder=HashEncoder(32))
    doc = mem.ingest("тест", session_id="s", role="user")
    assert doc["status"] == "ok"
