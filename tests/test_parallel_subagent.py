# -*- coding: utf-8 -*-
"""Параллельные вызовы инструментов, субагенты и цепочка идентичности в промпте.

Модель и MCP подменяются сценариями; сеть не нужна.
"""
import json
import os
import threading
import time

from pulsenet.app import App
from pulsenet.toolcalls import agent_limits
from pulsenet.workspace import Workspace


class ScriptedBackend:
    name = "lmstudio"
    loaded = "fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.base_url = "http://fake/v1"
        self.seen = []
        self._lock = threading.Lock()

    def ensure(self, model, params=None):
        return model or "fake"

    def stream(self, model, messages, on_delta, cancelled, **kw):
        with self._lock:
            self.seen.append([dict(m) for m in messages])
            piece_list = self.scripts.pop(0)
        for piece in piece_list:
            on_delta("token", piece)


def _app(tmp_path, scripts, env=None):
    base = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1",
            "AGENT_WORKSPACE_DIR": str(tmp_path / "data_unified")}
    base.update(env or {})
    app = App(base, data_dir=str(tmp_path / "data"))
    app.backend = ScriptedBackend(scripts)
    return app


def _call(name, args):
    return f'<tool_call>{json.dumps({"name": name, "arguments": args}, ensure_ascii=False)}</tool_call>'


def _track_concurrency(app, delay=0.3):
    """Оборачивает _call_tool: замеряет пиковую параллельность и добавляет задержку."""
    original = app._call_tool
    state = {"now": 0, "peak": 0, "count": 0}
    lock = threading.Lock()

    def wrapped(tools, name, args, on_delta):
        with lock:
            state["now"] += 1
            state["count"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            time.sleep(delay)
            return original(tools, name, args, on_delta)
        finally:
            with lock:
                state["now"] -= 1

    app._call_tool = wrapped
    return state


def _ws_with_skills(tmp_path):
    ws = Workspace(str(tmp_path / "data_unified"))
    ws.skill_write("alpha-one", "Описание первого навыка", "ALPHA-BODY текст первого")
    ws.skill_write("beta-two", "Описание второго навыка", "BETA-BODY текст второго")
    return ws


# ---------- параллельные вызовы ----------

def test_independent_calls_run_in_parallel_and_keep_order(tmp_path):
    _ws_with_skills(tmp_path)
    batch = _call("skill_read", {"name": "alpha-one"}) + _call("skill_read", {"name": "beta-two"})
    app = _app(tmp_path, [[batch], ["Готово."]])
    state = _track_concurrency(app, delay=0.4)
    started = time.monotonic()
    answer = app.chat("прочитай оба навыка", "par1", lambda k, p: None)
    elapsed = time.monotonic() - started
    assert answer == "Готово."
    assert state["peak"] == 2                       # оба шли одновременно
    assert elapsed < 0.4 * 2 - 0.1                  # а не 0.8 с по очереди
    # результаты уходят модели в порядке вызовов, даже если завершились в другом порядке
    last = app.backend.seen[1][-1]["content"]
    assert last.index("ALPHA-BODY") < last.index("BETA-BODY")


def test_write_tools_run_strictly_in_sequence(tmp_path):
    batch = (_call("note_write", {"title": "Первая", "body": "раз"})
             + _call("note_write", {"title": "Вторая", "body": "два"}))
    app = _app(tmp_path, [[batch], ["Ок."]])
    state = _track_concurrency(app, delay=0.1)
    app.chat("запиши две заметки", "seq1", lambda k, p: None)
    assert state["peak"] == 1
    assert state["count"] == 2


def test_identical_calls_in_one_batch_execute_once(tmp_path):
    _ws_with_skills(tmp_path)
    batch = _call("skill_read", {"name": "alpha-one"}) + _call("skill_read", {"name": "alpha-one"})
    app = _app(tmp_path, [[batch], ["Ок."]])
    state = _track_concurrency(app, delay=0.05)
    app.chat("дважды навык", "dup1", lambda k, p: None)
    assert state["count"] == 1
    last = app.backend.seen[1][-1]["content"]
    assert last.count("ALPHA-BODY") == 2            # второй вызов получил тот же результат


def test_per_step_limit_is_configurable():
    assert agent_limits({})["calls_per_step"] == 4
    assert agent_limits({"AGENT_CALLS_PER_STEP": "2"})["calls_per_step"] == 2
    assert agent_limits({"AGENT_PARALLEL": "99"})["parallel"] == 8   # потолок


# ---------- субагенты ----------

def test_subagent_runs_own_loop_and_returns_its_answer(tmp_path):
    _ws_with_skills(tmp_path)
    parent_call = _call("subagent", {"task": "прочитай навык alpha-one и кратко перескажи"})
    sub_call = _call("skill_read", {"name": "alpha-one"})
    app = _app(tmp_path, [
        [parent_call],                # родитель: делегирует
        [sub_call],                   # субагент: читает навык
        ["Навык про альфу."],         # субагент: итог
        ["Субагент сказал: навык про альфу."],   # родитель: финальный ответ
    ])
    events = []
    answer = app.chat("сделай через субагента", "sub1", lambda k, p: events.append((k, p)))
    assert answer.startswith("Субагент сказал")
    subs = [p for k, p in events if k == "tool" and p.get("sub")]
    assert subs and subs[0]["name"] == "skill_read"
    parent_tools = [p for k, p in events if k == "tool" and p.get("name") == "subagent"
                    and p.get("status") == "done"]
    assert parent_tools and parent_tools[0]["detail"].startswith("вызовов")
    # в контекст родителя ушёл итог субагента
    back = app.backend.seen[3][-1]["content"]
    assert "Навык про альфу." in back


def test_subagent_cannot_write_or_recurse(tmp_path):
    parent_call = _call("subagent", {"task": "запиши заметку"})
    sub_bad = _call("note_write", {"title": "Х", "body": "тест"})
    app = _app(tmp_path, [[parent_call], [sub_bad], ["Не смог записать."], ["Итог родителя."]])
    events = []
    app.chat("через субагента записать", "sub2", lambda k, p: events.append((k, p)))
    errors = [p for k, p in events if k == "tool" and p.get("status") == "error" and p.get("sub")]
    assert errors and errors[0]["name"] == "note_write"     # отказ виден в трассе
    assert not (tmp_path / "data_unified" / "notes").exists()


def test_subagent_schema_offered_to_model(tmp_path):
    app = _app(tmp_path, [["Ок."]])
    app.chat("привет", "sub3", lambda k, p: None)
    system = app.backend.seen[0][0]["content"]
    assert "subagent(" in system


# ---------- цепочка идентичности ----------

def test_committed_identity_is_injected_for_its_user_only(tmp_path):
    root = str(tmp_path / "data_unified")
    os.makedirs(os.path.join(root, "users", "alice"), exist_ok=True)
    chain = {"chain": [{"id": "c_1", "content": "Я отвечаю спокойно и по делу.", "kind": "style",
                        "committed_at": 1.0}], "candidates": []}
    with open(os.path.join(root, "users", "alice", "identity.json"), "w", encoding="utf-8") as fh:
        json.dump(chain, fh, ensure_ascii=False)

    app = _app(tmp_path, [["Ок."], ["Ок."]])
    app.chat("привет", "id1", lambda k, p: None, uid="alice")
    assert "Я отвечаю спокойно и по делу." in app.backend.seen[0][0]["content"]

    app.chat("привет", "id2", lambda k, p: None, uid="bob")
    assert "спокойно и по делу" not in app.backend.seen[1][0]["content"]


def test_identity_skips_pending_candidates(tmp_path):
    root = str(tmp_path / "data_unified")
    os.makedirs(os.path.join(root, "users", "alice"), exist_ok=True)
    state = {"chain": [], "candidates": [{"id": "c_x", "content": "Я чего-то жду.", "status": "pending",
                                          "days": ["2026-11-20"]}]}
    with open(os.path.join(root, "users", "alice", "identity.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    app = _app(tmp_path, [["Ок."]])
    app.chat("привет", "id3", lambda k, p: None, uid="alice")
    assert "Я чего-то жду." not in app.backend.seen[0][0]["content"]
