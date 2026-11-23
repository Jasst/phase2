# -*- coding: utf-8 -*-
"""Регрессионные тесты устойчивости агентного цикла: обрывы модели, бюджет контекста,
повторы и кэш, разбор вызовов, MCP-пауза и веб-инструменты."""
import json

import pytest

from pulsenet import app as app_module
from pulsenet.app import App, _fit_messages
from pulsenet.llm import BackendError
from pulsenet.mcp_client import MCPClient, MemoryUnavailable, READ_TOOLS
from pulsenet.toolcalls import ToolCallFilter, parse_calls
from pulsenet.tools import ChatTools


class Scripted:
    """Бэкенд-заглушка: каждый шаг — список кусков текста или исключение, которое кидается в потоке."""
    name = "lmstudio"
    base_url = "http://x/v1"
    last_native_unsupported = False

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def ensure(self, model, params=None):
        return model or "m"

    def stream(self, model, messages, on_delta, cancelled=None, max_tokens=None, tools=None, **kw):
        self.seen.append([dict(m) for m in messages])
        step = self.script.pop(0) if self.script else ["готово."]
        for piece in step:
            if isinstance(piece, BaseException):
                raise piece
            on_delta("token", piece)


def _app(tmp_path, script, env_extra=None):
    env = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1"}
    env.update(env_extra or {})
    app = App(env, data_dir=str(tmp_path))
    app.backend = Scripted(script)
    return app


def _call(name, args):
    return "<tool_call>" + json.dumps({"name": name, "arguments": args}, ensure_ascii=False) + "</tool_call>"


def _run(app, message="вопрос"):
    trace = []

    def on_delta(kind, payload):
        if kind == "trace":
            trace.append(payload)
        if kind == "step" and payload.get("calls") is not None:
            trace.append(payload)                   # шаги с перечнем вызовов и статусами
    answer = app.chat(message, "c1", on_delta)
    return answer, trace


# ---------- обрывы модели ----------

def test_stream_drop_mid_answer_does_not_raise(tmp_path):
    app = _app(tmp_path, [["Думаю над ответом", ConnectionResetError("connection reset by peer")]])
    answer, trace = _run(app)                       # исключения наружу быть не должно
    assert "Думаю" in answer                        # уже показанный текст сохраняется
    assert trace[-1]["stop"] == "llm_error"


def test_stream_drop_on_native_style_reader_becomes_backend_error():
    """llm.OpenAICompat.stream: обрыв чтения ответа превращается в BackendError."""
    from pulsenet import llm

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            yield 'data: {"choices": [{"delta": {"content": "часть"}}]}\n'.encode("utf-8")
            raise ConnectionResetError("reset")

    backend = llm.LMStudioBackend({"LMSTUDIO_URL": "http://x:1/v1"})
    original = llm.urllib.request.urlopen
    llm.urllib.request.urlopen = lambda req, timeout=None: Resp()
    try:
        out = []
        with pytest.raises(BackendError):
            backend.stream("m", [{"role": "user", "content": "q"}], lambda k, p: out.append((k, p)),
                           cancelled=lambda: False)
        assert ("token", "часть") in out            # частичный текст уже был отдан
    finally:
        llm.urllib.request.urlopen = original


def test_model_not_loaded_ends_turn_with_text(tmp_path):
    app = _app(tmp_path, [])

    def broken_ensure(model, params=None):
        raise BackendError("модель 'x' не найдена")
    app.backend.ensure = broken_ensure
    answer, trace = _run(app)
    assert trace[-1]["stop"] == "llm_error"
    assert "Модель не ответила" in answer


# ---------- служебные подсказки и бюджет контекста ----------

def test_error_hint_is_user_message_not_system(tmp_path):
    app = _app(tmp_path, [[_call("memory_search", {"query": ""})],
                          [_call("memory_search", {"query": "ok"})],
                          ["ответ."]])
    _run(app)
    for request in app.backend.seen:
        for i, m in enumerate(request):
            if i > 0:
                assert m["role"] != "system", "system-сообщение посреди диалога"


def test_context_budget_is_enforced_on_every_round(tmp_path):
    limit = 4000
    script = [[_call("memory_search", {"query": f"q{i}"})] for i in range(6)] + [["итог."]]
    app = _app(tmp_path, script, {"AGENT_CONTEXT_MAX_CHARS": str(limit), "AGENT_MAX_ROUNDS": "6"})
    app._tool_recall = lambda q, k: {"facts": [{"text": "ф" * 1100, "confidence": 1}], "chunks": []}
    _run(app)
    sizes = [sum(len(str(m["content"])) for m in req) for req in app.backend.seen]
    assert max(sizes) <= limit + 1500, sizes        # системный блок и последние реплики — сверх лимита


def test_fit_messages_keeps_tool_pairs_valid():
    msgs = [{"role": "system", "content": "s" * 100},
            {"role": "user", "content": "u" * 500},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "t" * 500},
            {"role": "user", "content": "последний вопрос"}]
    _fit_messages(msgs, 300)
    for i, m in enumerate(msgs):
        if m["role"] == "tool":
            assert i > 0 and msgs[i - 1]["role"] in ("assistant", "tool")
    assert msgs[0]["role"] == "system" and msgs[-1]["content"] == "последний вопрос"


# ---------- повторы, кэш, счётчики ----------

def test_cached_error_stays_an_error(tmp_path):
    app = _app(tmp_path, [[_call("memory_search", {"query": ""})],
                          [_call("memory_search", {"query": ""})],
                          ["ответ."]])
    _, trace = _run(app)
    steps = [t for t in trace if isinstance(t.get("calls"), list)]
    assert steps, trace
    statuses = [c["status"] for st in steps for c in st["calls"]]
    # второй раунд повторяет ошибку из кэша: это тоже ошибка, а не «успех»
    assert statuses.count("cache") == 1
    assert "ok" not in statuses


def test_duplicates_across_rounds_do_not_stop_turn(tmp_path):
    dup = _call("memory_search", {"query": "x"})
    script = [[dup, dup], [_call("memory_search", {"query": "y"}), _call("memory_search", {"query": "y"})],
              [_call("memory_search", {"query": "z"})], ["ответ."]]
    app = _app(tmp_path, script)
    answer, trace = _run(app)
    stops = [t.get("stop") for t in trace if t.get("stop")]
    assert "no_progress" not in stops
    assert answer.endswith("ответ.")


def test_consecutive_failures_stop_turn(tmp_path):
    script = [[_call("memory_search", {"query": "", "n": i})] for i in range(3)] + [["вывод."]]
    app = _app(tmp_path, script)
    _, trace = _run(app)
    assert trace[-1]["stop"] == "failures"


# ---------- разбор вызовов ----------

def test_parse_accepts_common_model_variants():
    calls = parse_calls([
        '{"tool": "memory_search", "parameters": {"query": "a"}}',
        '{"name": "web_fetch", "arguments": "{\\"url\\": \\"https://a.b\\"}"}',
        '{"function": {"name": "memory_search", "arguments": {"query": "c"}}}',
        'Вот вызов: {"name": "memory_search", "arguments": {"query": "d"}} готово',
    ])
    assert calls[0] == {"name": "memory_search", "arguments": {"query": "a"}}
    assert calls[1] == {"name": "web_fetch", "arguments": {"url": "https://a.b"}}
    assert calls[2] == {"name": "memory_search", "arguments": {"query": "c"}}
    assert calls[3] == {"name": "memory_search", "arguments": {"query": "d"}}


def test_parse_still_reports_real_errors():
    calls = parse_calls(["совсем не json", '{"name": "web_fetch", "arguments": "нет"}'])
    assert all("error" in c for c in calls)


# ---------- MCP: пауза после сбоя и повтор чтения ----------

class _Down:
    """Клиент, у которого соединение всегда падает; считаем реальные попытки подключения."""
    opened = 0

    def __init__(self, *a, **kw):
        _Down.opened += 1

    async def __aenter__(self):
        raise ConnectionError("connection refused")

    async def __aexit__(self, *a):
        return False


def test_mcp_cooldown_fails_fast_after_outage(monkeypatch):
    client = MCPClient("http://x/mcp", "tok", cooldown=60)
    monkeypatch.setattr(client, "_session", lambda: (object, _Down))
    _Down.opened = 0
    with pytest.raises(MemoryUnavailable):
        client.call("triplex_ingest", {"text": "t"})          # запись: одна попытка
    assert _Down.opened == 1
    with pytest.raises(MemoryUnavailable, match="недавно не отвечал"):
        client.call("triplex_ingest", {"text": "t"})          # пауза: сеть даже не трогаем
    assert _Down.opened == 1


def test_mcp_read_tool_is_retried_once(monkeypatch):
    client = MCPClient("http://x/mcp", "tok", cooldown=0)
    monkeypatch.setattr(client, "_session", lambda: (object, _Down))
    monkeypatch.setattr("pulsenet.mcp_client.RETRY_DELAY", 0)
    assert "triplex_search" in READ_TOOLS
    _Down.opened = 0
    with pytest.raises(MemoryUnavailable):
        client.call("triplex_search", {"query": "q"})
    assert _Down.opened == 2


# ---------- веб-инструменты ----------

def test_web_fetch_rejects_non_http_scheme():
    tools = ChatTools(recall=lambda q, k: {}, remember=lambda t: {}, web=object())
    out = tools.run("web_fetch", {"url": "file:///etc/passwd"})
    assert out.ok is False and "http" in out.text


def test_timeout_message_is_readable(monkeypatch):
    class Slow:
        async def fetch(self, url, max_chars=0):
            raise TimeoutError()
    tools = ChatTools(recall=lambda q, k: {}, remember=lambda t: {}, web=Slow())
    out = tools.run("web_fetch", {"url": "https://a.b/x"})
    assert out.ok is False and "не ответил вовремя" in out.text


# ---------- алиас type, голый JSON, однократные пояснения ----------

def test_type_alias_is_taken_as_tool_name_when_known():
    from pulsenet.toolcalls import parse_calls as pc
    call = pc(['{"type": "subagent", "task": "найти курс", "tools": ["web_search"]}'], ["subagent"])[0]
    assert call["name"] == "subagent"
    assert call["arguments"] == {"task": "найти курс", "tools": ["web_search"]}
    unknown = pc(['{"type": "nonsense", "x": 1}'], ["subagent"])[0]
    assert "error" in unknown                          # неизвестный type не превращается в инструмент


def test_bare_json_call_is_executed_without_retry(tmp_path):
    bare = 'Сейчас проверю. ```json\n{"name": "memory_search", "arguments": {"query": "курс"}}\n```'
    app = _app(tmp_path, [[bare], ["Ответ по памяти."]])
    answer, _ = _run(app)
    # вызов выполнен сразу: второй запрос уже содержит результат инструмента
    assert len(app.backend.seen) == 2
    assert "<tool_result name=\"memory_search\">" in app.backend.seen[1][-1]["content"]
    # в итоговом ответе нет JSON-вызова и пустого код-блока
    assert answer == "Сейчас проверю.\n\nОтвет по памяти."
    assert "memory_search" not in answer and "```" not in answer


def test_filter_hides_xml_style_call_for_any_chunking():
    text = 'Проверю. <web_search>["курс", "доллар"]</web_search> Готово.'
    for size in (1, 2, 3, 7, 50):
        flt = ToolCallFilter(["web_search"])
        visible = []
        for i in range(0, len(text), size):
            visible.append(flt.feed(text[i:i + size]))
        visible.append(flt.finish())
        assert "".join(visible) == "Проверю.  Готово."
        assert len(flt.calls_raw) == 1
        assert parse_calls(flt.calls_raw, ["web_search"])[0] == {
            "name": "web_search", "arguments": {"queries": ["курс", "доллар"]}}


def test_xml_style_call_runs_tool_and_hides_markup(tmp_path):
    app = _app(tmp_path, [["<memory_search>люблю</memory_search>"], ["Ответ по памяти."]])
    app._tool_recall = lambda q, k: {"facts": [{"text": "любит чай", "confidence": 1}], "chunks": []}
    answer, trace = _run(app)
    assert answer == "Ответ по памяти."              # тег не попал в видимый ответ
    assert len(app.backend.seen) == 2                # лишнего раунда на «повтори формат» нет
    assert "<tool_result name=\"memory_search\">" in app.backend.seen[1][-1]["content"]
    steps = [t for t in trace if isinstance(t.get("calls"), list)]
    assert steps and steps[0]["calls"][0]["name"] == "memory_search"
    assert steps[0]["calls"][0]["status"] == "ok"


def test_trace_reason_matches_real_model_answer(tmp_path):
    app = _app(tmp_path, [["Вот ответ."]])
    answer, trace = _run(app)
    assert answer == "Вот ответ."
    assert trace[-1]["stop"] == "model_done"
    assert "без текста" not in trace[-1]["reason"]
    assert "ответ" in trace[-1]["reason"].lower()


def test_trace_reason_is_honest_when_model_is_silent(tmp_path):
    app = _app(tmp_path, [["   "]])
    answer, trace = _run(app)
    assert trace[-1]["stop"] == "model_done"
    assert "без текста" in trace[-1]["reason"]
    assert "не дала итогового текста" in answer


def test_context_notes_are_shown_once_per_turn(tmp_path):
    script = [[_call("memory_search", {"query": f"q{i}"})] for i in range(5)] + [["итог."]]
    app = _app(tmp_path, script, {"AGENT_CONTEXT_MAX_CHARS": "3000", "AGENT_MAX_ROUNDS": "5"})
    app._tool_recall = lambda q, k: {"facts": [{"text": "ф" * 1100, "confidence": 1}], "chunks": []}
    shown = []

    def on_delta(kind, payload):
        if kind == "step" and payload.get("label") == "Контекст":
            shown.append(payload["detail"])
    app.chat("вопрос", "c1", on_delta)
    assert shown, "пояснения о сокращении не появились — проверка бессмысленна"
    assert len(shown) == len(set(shown))


# ---------- плохие вызовы: понятная ошибка и отдельная причина останова ----------

def test_name_as_key_and_wrapper_are_accepted():
    from pulsenet.toolcalls import parse_calls as pc
    known = ["web_search"]
    assert pc(['{"web_search": {"queries": ["курс"]}}'], known)[0] == \
        {"name": "web_search", "arguments": {"queries": ["курс"]}}
    assert pc(['{"tool_call": {"name": "web_search", "arguments": {"queries": ["x"]}}}'], known)[0]["name"] == "web_search"


def test_parse_error_shows_what_model_wrote_and_example():
    from pulsenet.toolcalls import parse_calls as pc
    err = pc(['{"query": "курс доллара"}'], ["web_search"])[0]["error"]
    assert "курс доллара" in err and "<tool_call>" in err


def test_repeated_bad_calls_stop_with_own_reason(tmp_path):
    bad = '<tool_call>{"query": "курс"}</tool_call>'
    app = _app(tmp_path, [[bad], [bad], ["ответ по данным."]])
    answer, trace = _run(app)
    stops = [t.get("stop") for t in trace if t.get("stop")]
    assert stops and stops[-1] == "bad_calls"
    assert "без прогресса" not in answer
