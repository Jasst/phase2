# -*- coding: utf-8 -*-
"""Устойчивость агентного цикла: индексация пакетами, повторы LLM, изоляция ошибок,
контекст субагента, пауза MCP и сохранение трассы. Сеть и модель — сценарии."""
import json
import threading
import time

import pulsenet.llm as llm
from pulsenet.app import App
from pulsenet.chat_store import ChatStore
from pulsenet.mcp_client import MemoryUnavailable


class ScriptedBackend:
    name = "lmstudio"
    loaded = "fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.base_url = "http://fake/v1"
        self.seen = []

    def ensure(self, model, params=None):
        return model or "fake"

    def stream(self, model, messages, on_delta, cancelled, **kw):
        self.seen.append([dict(m) for m in messages])
        item = self.scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        for piece in item:
            on_delta("token", piece)


def _app(tmp_path, scripts, env=None):
    base = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1",
            "INDEX_DEBOUNCE_SECONDS": "0.3",
            "AGENT_WORKSPACE_DIR": str(tmp_path / "data_unified")}
    base.update(env or {})
    app = App(base, data_dir=str(tmp_path / "data"))
    app.backend = ScriptedBackend(scripts)
    return app


def _record_ingest(app):
    calls = []
    lock = threading.Lock()

    def fake(text, session_id="", role="user", **kw):
        with lock:
            calls.append((session_id, text))
        return {"status": "ok"}

    app.ingest = fake
    return calls


# ---------- индексация ----------

def test_short_and_trivial_messages_are_not_queued(tmp_path):
    app = _app(tmp_path, [])
    app.index_async("привет!", "c1")
    app.index_async("ок", "c1")
    app.index_async("Я живу в Амстердаме.", "c1")
    assert app._index_q.qsize() == 1                 # факт сохраняется, служебное — нет


def test_duplicate_message_in_same_chat_is_skipped(tmp_path):
    app = _app(tmp_path, [])
    app.index_async("Я работаю над BiChat каждый день.", "c1")
    app.index_async("Я работаю над BiChat каждый день.", "c1")
    assert app._index_q.qsize() == 1


def test_remember_is_always_queued_even_if_short(tmp_path):
    app = _app(tmp_path, [])
    app.index_async("ок", "c1", force=True)
    assert app._index_q.qsize() == 1


def test_messages_are_batched_into_one_ingest_per_chat(tmp_path):
    app = _app(tmp_path, [], env={"INDEX_DEBOUNCE_SECONDS": "0.5"})
    calls = _record_ingest(app)
    app.index_async("Первый длинный факт про проект номер один.", "chatA")
    app.index_async("Второй длинный факт про проект номер два.", "chatA")
    app.index_async("Третий факт из другого чата, совсем другой.", "chatB")
    assert app.wait_index(timeout=5)
    assert len(calls) == 2                            # по одному вызову на чат
    texts = {sid: txt for sid, txt in calls}
    assert "Первый" in texts["chatA"] and "Второй" in texts["chatA"]


def test_indexing_waits_while_chat_turn_is_busy(tmp_path):
    app = _app(tmp_path, [])
    calls = _record_ingest(app)
    with app._busy_cv:
        app._chat_busy = 1                            # ход чата идёт
    app.index_async("Длинное сообщение с фактом про работу над проектом.", "c2")
    time.sleep(1.0)
    assert calls == []                                # модель занята — не пишем
    with app._busy_cv:
        app._chat_busy = 0
        app._busy_cv.notify_all()
    assert app.wait_index(timeout=5)
    assert len(calls) == 1


def test_index_mode_off_disables_automatic_indexing(tmp_path):
    app = _app(tmp_path, [], env={"INDEX_MODE": "off"})
    app.index_async("Длинное сообщение с фактом про работу над проектом.", "c3")
    assert app._index_q.qsize() == 0
    app.index_async("Запомни это важное правило работы.", "c3", force=True)
    assert app._index_q.qsize() == 1


# ---------- устойчивость модели ----------

def test_transient_llm_failure_is_retried_before_anyone_sees_it(tmp_path):
    app = _app(tmp_path, [llm.BackendError("нет связи"), ["Ответ после повтора."]])
    answer = app.chat("вопрос", "r1", lambda k, p: None)
    assert answer == "Ответ после повтора."
    assert len(app.backend.seen) == 2


def test_persistent_llm_failure_ends_turn_gracefully(tmp_path):
    err = llm.BackendError("сервер упал")
    app = _app(tmp_path, [err, err, err])
    events = []
    answer = app.chat("вопрос", "r2", lambda k, p: events.append((k, p)))   # не бросает исключение
    assert "Модель не ответила" in answer
    traces = [p for k, p in events if k == "trace"]
    assert traces[-1]["stop"] == "llm_error"


def test_failing_parallel_tool_becomes_a_result_not_a_crash(tmp_path):
    batch = ('<tool_call>{"name": "skill_list", "arguments": {}}</tool_call>'
             '<tool_call>{"name": "skill_read", "arguments": {"name": "нет-такого"}}</tool_call>')
    app = _app(tmp_path, [[batch], ["Ответ без инструмента."]])
    original = app._call_tool

    def flaky(tools, name, args, on_delta):
        if name == "skill_list":
            raise RuntimeError("внезапный сбой")
        return original(tools, name, args, on_delta)

    app._call_tool = flaky
    answer = app.chat("сделай два вызова", "r3", lambda k, p: None)
    assert answer == "Ответ без инструмента."
    assert "внезапный сбой" in app.backend.seen[1][-1]["content"]


# ---------- субагент: контекст вопроса ----------

def test_subagent_receives_the_user_question_as_context(tmp_path):
    parent = '<tool_call>{"name": "subagent", "arguments": {"task": "найди факт", "context": "про Амстердам"}}</tool_call>'
    app = _app(tmp_path, [[parent], ["Факт найден."], ["Итог родителя."]])
    question = "Расскажи, где я живу и что там важного"
    app.chat(question, "r4", lambda k, p: None)
    sub_system = app.backend.seen[1][0]["content"]
    assert question in sub_system                     # субагент знает, зачем он нужен
    sub_user = app.backend.seen[1][1]["content"]
    assert "Контекст: про Амстердам" in sub_user


# ---------- MCP: пауза после сбоя памяти ----------

class _DownMemory:
    def __init__(self):
        self.calls = 0

    def recall(self, query, **kw):
        self.calls += 1
        raise MemoryUnavailable("сервер памяти недоступен")


def test_memory_recall_pauses_after_mcp_failure(tmp_path):
    app = _app(tmp_path, [], env={"MCP_COOLDOWN_SECONDS": "60"})
    down = _DownMemory()
    app.memory = down
    app.memory_context("первый вопрос")
    app.memory_context("второй вопрос")
    app.memory_context("третий вопрос")
    assert down.calls == 1                            # дальше не ждём недоступный сервер


# ---------- трасса сохраняется в хранилище ----------

def test_trace_todos_and_tools_survive_chat_store_roundtrip(tmp_path):
    store = ChatStore(str(tmp_path / "chats.sqlite"))
    msg = {"id": "a1", "role": "assistant", "text": "Готово.", "time": time.time(),
           "tools": [{"id": "t1", "name": "skill_read", "status": "done", "step": 1,
                      "label": "Навык", "ms": 12, "preview": "тело"}],
           "todos": [{"text": "шаг", "status": "done"}],
           "trace": {"round": 1, "calls": 1, "state": "done", "stop": "model_done"}}
    store.append("alice", "chat-1", [msg], title="проверка")
    back = [m for m in store.chat_messages("alice", "chat-1") if m.get("id") == "a1"]
    assert back, "сообщение не сохранилось"
    assert back[0]["trace"]["stop"] == "model_done"
    assert back[0]["todos"][0]["status"] == "done"
    assert back[0]["tools"][0]["step"] == 1
    store.close()
