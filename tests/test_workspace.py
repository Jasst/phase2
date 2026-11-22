# -*- coding: utf-8 -*-
"""Рабочее пространство агента (навыки, заметки, правила) и трасса работы.

Сеть и LLM не нужны: модель заменяется сценарием ответов, как в test_agent_tools.py.
"""
import os

import pytest

from pulsenet.app import App, looks_multistep
from pulsenet.server import ChatJob
from pulsenet.tools import PLAN_TOOL, ChatTools
from pulsenet.workspace import Workspace, WorkspaceError, parse_front, slugify


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
    base = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1",
            "AGENT_WORKSPACE_DIR": str(tmp_path / "data_unified")}
    base.update(env or {})
    app = App(base, data_dir=str(tmp_path / "data"))
    app.backend = ScriptedBackend(scripts)
    return app


def _ws(tmp_path, **kw):
    return Workspace(str(tmp_path / "ws"), **kw)


def _tools(ws, on_event=None):
    return ChatTools(recall=lambda q, k: {}, remember=lambda t: {}, web=False,
                     workspace=ws, on_event=on_event)


# ---------- slug и frontmatter ----------

def test_slugify_latin_and_non_latin():
    assert slugify("Git Stash: Rules!") == "git-stash-rules"
    # нелатинский заголовок без ASCII-остатка получает случайный, но валидный slug
    assert slugify("Заметка про кофе").startswith("n-")


def test_frontmatter_roundtrip():
    meta, body = parse_front("---\nname: x\ndescription: d\n---\n\nтело\n")
    assert meta == {"name": "x", "description": "d"}
    assert body.strip() == "тело"


# ---------- навыки ----------

def test_skill_create_read_list(tmp_path):
    ws = _ws(tmp_path)
    assert ws.skill_list() == []
    res = ws.skill_write("release-notes", "Когда пишешь заметки о релизе", "1. Собери коммиты.\n2. Сгруппируй.")
    assert res["status"] == "created"
    skill = ws.skill_read("release-notes")
    assert skill["description"] == "Когда пишешь заметки о релизе"
    assert "Собери коммиты" in skill["body"]
    assert [s["name"] for s in ws.skill_list()] == ["release-notes"]


def test_skill_update_keeps_history_and_patch_is_exact(tmp_path):
    ws = _ws(tmp_path)
    ws.skill_write("deploy-check", "Перед выкладкой проверь здоровье сервиса", "Шаг A\nШаг B")
    assert ws.skill_write("deploy-check", "Перед выкладкой проверь здоровье сервиса",
                          "Шаг A\nШаг C")["status"] == "updated"
    hist = os.listdir(os.path.join(ws.skills_dir, "deploy-check", ".history"))
    assert len(hist) == 1
    ws.skill_patch("deploy-check", "Шаг C", "Шаг C (с логами)")
    assert "Шаг C (с логами)" in ws.skill_read("deploy-check")["body"]
    with pytest.raises(WorkspaceError):
        ws.skill_patch("deploy-check", "Шаг", "X")          # встречается несколько раз


def test_skill_gates_reject_bad_names_secrets_and_size(tmp_path):
    ws = _ws(tmp_path, max_skill=200)
    with pytest.raises(WorkspaceError):
        ws.skill_write("../evil", "Описание достаточно длинное", "тело")
    with pytest.raises(WorkspaceError):
        ws.skill_write("ok-name", "коротко", "тело")          # description < 10
    with pytest.raises(WorkspaceError):
        ws.skill_write("ok-name", "Описание достаточно длинное", "api_key = abcd1234efgh")
    with pytest.raises(WorkspaceError):
        ws.skill_write("ok-name", "Описание достаточно длинное", "x" * 500)
    assert ws.skill_list() == []


def test_skill_write_can_be_disabled(tmp_path):
    ws = _ws(tmp_path, skills_write=False)
    with pytest.raises(WorkspaceError):
        ws.skill_write("ok-name", "Описание достаточно длинное", "тело")


def test_skill_match_selects_relevant_only(tmp_path):
    ws = _ws(tmp_path)
    ws.skill_write("git-stash-cleanup", "Очистка git stash: какие записи удалять и как",
                   "Сначала git stash list.")
    ws.skill_write("pdf-merge", "Склейка PDF файлов в один документ", "Используй pypdf.")
    hits = ws.skill_match("как очистить git stash от старых записей")
    assert [h["name"] for h in hits] == ["git-stash-cleanup"]
    assert ws.skill_match("привет") == []


# ---------- заметки и картинки ----------

def test_note_with_image_and_append(tmp_path):
    ws = _ws(tmp_path)
    ws.note_write("Эскиз логотипа", "Идея: спираль из точек.", tags=["дизайн"],
                  image_url="/images/u1/logo.png")
    note = ws.note_read("Эскиз логотипа")
    assert "![Эскиз логотипа](/images/u1/logo.png)" in note["body"]
    assert note["tags"] == "дизайн"
    ws.note_append("Эскиз логотипа", "Вариант 2: квадрат.", image_url="https://img.example/v2.png")
    body = ws.note_read("Эскиз логотипа")["body"]
    assert "Вариант 2" in body and "https://img.example/v2.png" in body


def test_note_rejects_unsafe_image_url_and_bad_kind(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.note_write("t", "текст", image_url="javascript:alert(1)")
    with pytest.raises(WorkspaceError):
        ws.note_write("t", "текст", kind="secret")


def test_self_notes_feed_identity_context_and_stay_separate(tmp_path):
    ws = _ws(tmp_path)
    ws.note_write("Про работу с логами", "Пользователь просит кратко; ошибки лучше сразу показывать.",
                  kind="self")
    ws.note_write("Список покупок", "Молоко, хлеб.", kind="note")
    ctx = ws.self_notes()
    assert "Про работу с логами" in ctx
    assert "Молоко" not in ctx
    assert [n["title"] for n in ws.note_list(kind="note")] == ["Список покупок"]


def test_instructions_user_file_only_when_present(tmp_path):
    ws = _ws(tmp_path)
    assert ws.instructions() == ""
    os.makedirs(ws.root, exist_ok=True)
    with open(ws.user_agents, "w", encoding="utf-8") as fh:
        fh.write("# Правила\nВсегда отвечай кратко.")
    assert "Всегда отвечай кратко" in ws.instructions()


# ---------- инструменты ChatTools ----------

def test_workspace_tools_offered_and_routed(tmp_path):
    ws = _ws(tmp_path)
    events = []
    tools = _tools(ws, on_event=lambda k, p: events.append((k, p)))
    names = tools.available(web=False, memory=False)
    for n in ("skill_list", "skill_read", "skill_write", "skill_patch",
              "note_write", "note_read", "note_list", PLAN_TOOL):
        assert n in names
    out = tools.run("skill_write", {"name": "greet", "description": "Как здороваться с пользователем",
                                    "body": "Коротко и тепло."})
    assert out.ok and "создан" in out.text
    out = tools.run("skill_read", {"name": "greet"})
    assert out.ok and "Коротко и тепло" in out.text
    out = tools.run("skill_read", {"name": "missing-one"})
    assert not out.ok


def test_todo_emits_plan_event_and_counts(tmp_path):
    events = []
    tools = _tools(_ws(tmp_path), on_event=lambda k, p: events.append((k, p)))
    out = tools.run(PLAN_TOOL, {"items": [{"text": "собрать данные", "status": "done"},
                                          {"text": "написать отчёт", "status": "in_progress"},
                                          {"text": "проверить"}]})
    assert out.ok and out.label == "План: 1/3"
    kind, items = events[-1]
    assert kind == "todo" and items[2]["status"] == "pending"


def test_todo_hidden_without_event_channel(tmp_path):
    names = _tools(_ws(tmp_path)).available(web=False, memory=False)
    assert PLAN_TOOL not in names


def test_note_write_uses_last_image_of_turn(tmp_path):
    ws = _ws(tmp_path)
    tools = _tools(ws)
    tools.last_image = "/images/u/last.png"
    out = tools.run("note_write", {"title": "Картинка дня", "body": "Смотри.", "image_url": "last"})
    assert out.ok
    assert "/images/u/last.png" in ws.note_read("Картинка дня")["body"]


def test_skill_write_disabled_hides_write_tools(tmp_path):
    tools = _tools(_ws(tmp_path, skills_write=False), on_event=lambda k, p: None)
    names = tools.available(web=False, memory=False)
    assert "skill_read" in names and "skill_write" not in names and "skill_patch" not in names


def test_looks_multistep():
    assert looks_multistep("сначала найди файлы, затем собери отчёт и потом отправь")
    assert not looks_multistep("сколько будет два плюс два")


# ---------- события сервера ----------

def test_job_keeps_todo_and_trace_events_out_of_text():
    job = ChatJob("c", "m")
    job.add_delta("token", "Готово")
    job.add_delta("todo", [{"text": "a", "status": "done"}])
    job.add_delta("trace", {"round": 1, "state": "running"})
    snap = job.snapshot()
    assert snap["text"] == "Готово"
    assert [e["kind"] for e in snap["events"]] == ["todo", "trace"]


# ---------- агентный цикл целиком ----------

def test_agent_writes_skill_and_trace_is_reported(tmp_path):
    write = ('<tool_call>{"name": "skill_write", "arguments": {"name": "pdf-tips",'
             ' "description": "Советы по работе с PDF файлами", "body": "Шаг 1: проверь текст."}}'
             '</tool_call>')
    plan = ('<tool_call>{"name": "todo_write", "arguments": {"items": [{"text": "навык",'
            ' "status": "done"}]}}</tool_call>')
    app = _app(tmp_path, [[write], [plan], ["Навык сохранён."]])
    events = []
    answer = app.chat("сохрани навык про PDF", "ws1", lambda k, p: events.append((k, p)))
    assert answer == "Навык сохранён."
    assert app.workspace.skill_read("pdf-tips")["name"] == "pdf-tips"

    tool_done = [p for k, p in events if k == "tool" and p.get("status") == "done"]
    assert [t["name"] for t in tool_done] == ["skill_write", PLAN_TOOL]
    assert [t["step"] for t in tool_done] == [1, 2]
    assert all("ms" in t and "preview" in t for t in tool_done)
    todo = [p for k, p in events if k == "todo"]
    assert todo and todo[-1][0]["status"] == "done"
    traces = [p for k, p in events if k == "trace"]
    assert traces[-1]["state"] == "done" and traces[-1]["stop"] == "model_done"
    assert traces[-1]["calls"] == 2


def test_agent_sees_skill_catalog_and_matched_skill(tmp_path):
    ws = Workspace(str(tmp_path / "data_unified"))
    ws.skill_write("pdf-tips", "Советы по работе с PDF файлами", "Шаг 1: проверь текст слоя.")
    app = _app(tmp_path, [["Ок."]])
    app.chat("как вытащить текст из PDF файлами", "ws2", lambda k, p: None)
    system = app.backend.seen[0][0]["content"]
    assert "pdf-tips" in system
    assert "Подходящий навык «pdf-tips»" in system and "Шаг 1: проверь текст слоя" in system
    assert "Как работать с навыками и заметками" in system


def test_project_agents_md_not_injected_by_default(tmp_path):
    app = _app(tmp_path, [["Ок."]])
    assert app.project_agents is None
