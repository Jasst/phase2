# -*- coding: utf-8 -*-
"""Видимость агентного цикла: step-события, бюджет, финальный ответ и безопасное усечение."""
import json

from pulsenet.app import App
from pulsenet.server import ChatJob
from pulsenet.toolcalls import agent_limits, compact_model_text, tool_result_block

CALL = ('<tool_call>{"name": "memory_search", "arguments": '
        '{"query": "проверка"}}</tool_call>')


class TracingBackend:
    """Фейковый бэкенд: запоминает сообщения и параметры генерации каждого шага."""

    name = "lmstudio"
    loaded = "fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.base_url = "http://fake/v1"
        self.seen = []
        self.kwargs = []

    def ensure(self, model, params=None):
        return model or "fake"

    def stream(self, model, messages, on_delta, cancelled, max_tokens=None, tools=None):
        self.seen.append([dict(m) for m in messages])
        self.kwargs.append({"max_tokens": max_tokens, "tools": tools})
        for piece in self.scripts.pop(0):
            on_delta("token", piece)


def _app(tmp_path, scripts, **extra):
    env = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1",
           "AGENT_MAX_ROUNDS": "2", "AGENT_MAX_TOKENS": "1024", "TOOL_NATIVE": "0", **extra}
    app = App(env, data_dir=str(tmp_path))
    app.backend = TracingBackend(scripts)
    return app


def test_limits_expose_agent_token_budget():
    lim = agent_limits({"AGENT_MAX_TOKENS": "4096", "AGENT_MAX_ROUNDS": "6"})
    assert lim["agent_max_tokens"] == 4096
    assert lim["system_max"] > 1000 and lim["context_max"] > lim["system_max"]


def test_agent_max_tokens_reaches_backend(tmp_path):
    app = _app(tmp_path, [["Готово."]])
    events = []
    app.chat("вопрос", "tokens", lambda kind, payload: events.append((kind, payload)))
    assert app.backend.kwargs and all(k["max_tokens"] == 1024 for k in app.backend.kwargs)


def test_step_trace_and_final_answer_after_round_limit(tmp_path):
    app = _app(tmp_path, [[CALL], [CALL], ["Итоговый ответ по результатам."]])
    events = []
    answer = app.chat("вопрос", "trace", lambda kind, payload: events.append((kind, payload)))

    # Два раунда инструментов + один финальный запрос без инструментов.
    assert len(app.backend.seen) == 3
    assert "Ответь ТОЛЬКО текстом" in app.backend.seen[-1][-1]["content"]
    assert answer == "Итоговый ответ по результатам."

    steps = [p for k, p in events if k == "step"]
    phases = [(p["phase"], p["state"]) for p in steps]
    assert ("start", "running") in phases
    assert ("end", "done") in phases
    assert ("final", "done") in phases
    final = next(p for p in steps if p["phase"] == "final")
    assert final["stop"] == "round_limit"
    assert final["budget"]["round"] == 2
    assert final["budget"]["max_rounds"] == 2

    traces = [p for k, p in events if k == "trace"]
    assert traces[-1]["stop"] == "round_limit"
    assert traces[-1]["reason"]
    assert traces[-1]["budget"]["calls"] == 2


def test_model_tool_calls_on_final_round_are_not_dropped(tmp_path):
    # Модель упрямо продолжает звать инструмент на последнем раунде:
    # ход обязан закончиться текстом, а не оборваться после <tool_call>.
    app = _app(tmp_path, [[CALL], [CALL], ["Итог."]])
    events = []
    answer = app.chat("вопрос", "limit", lambda kind, payload: events.append((kind, payload)))
    assert answer == "Итог."
    assert not any(k == "step" and p.get("state") == "error" for k, p in events)


def test_compact_model_text_keeps_json_valid():
    payload = json.dumps({"items": [{"text": "д" * 500} for _ in range(5)]}, ensure_ascii=False)
    body, truncated, is_json = compact_model_text(payload, 400)
    assert truncated and is_json and len(body) <= 400
    data = json.loads(body)
    assert isinstance(data, dict)

    block = tool_result_block("mcp", payload, 400)
    inner = block.split("\n", 1)[1].rsplit("\n", 1)[0]
    assert isinstance(json.loads(inner), dict)


def test_chat_job_keeps_step_events_out_of_text():
    job = ChatJob("c1", "привет")
    job.add_delta("step", {"id": "s1", "phase": "start", "state": "running"})
    job.add_delta("token", "Ответ")
    snap = job.snapshot()
    assert snap["text"] == "Ответ"
    assert [e["kind"] for e in snap["events"]] == ["step"]
