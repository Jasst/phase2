# -*- coding: utf-8 -*-
"""Инструменты в чате: протокол <tool_call>, команды, агентный цикл, веб-инструменты,
карта нейронов, журнал активности и кэш веб-поиска. Модель подменяется сценарием."""
import asyncio
import json
import os

import pytest

from pulsenet import server
from pulsenet.app import App
from pulsenet.toolcalls import (MAX_STEPS, ToolCallFilter, parse_calls, parse_command,
                                tool_result_block)
from pulsenet.tools import ChatTools, instructions
from pulsenet.triplex import TriplexMemory
from pulsenet.triplex.encoder import HashEncoder


# ---------- протокол ----------

def _feed_in_pieces(text, size):
    flt = ToolCallFilter()
    visible = []
    for i in range(0, len(text), size):
        visible.append(flt.feed(text[i:i + size]))
    visible.append(flt.finish())
    return "".join(visible), flt.calls_raw


@pytest.mark.parametrize("size", [1, 2, 3, 7, 50])
def test_filter_hides_tool_call_for_any_chunking(size):
    text = 'Сейчас проверю. <tool_call>{"name": "memory_search", "arguments": {"query": "q"}}</tool_call> Готово.'
    visible, raw = _feed_in_pieces(text, size)
    assert visible == "Сейчас проверю.  Готово."
    assert len(raw) == 1 and '"memory_search"' in raw[0]


def test_filter_unclosed_call_goes_to_calls():
    visible, raw = _feed_in_pieces('ок <tool_call>{"name": "web_fetch", "arguments": {"url": "https://a.b"}}', 4)
    assert visible == "ок "
    assert len(raw) == 1


def test_parse_calls_reports_errors_instead_of_raising():
    calls = parse_calls(['{"name": "memory_search", "arguments": {"query": "x"}}',
                         "не json", '{"arguments": {}}', '{"name": "web_fetch", "arguments": "нет"}',
                         '```json\n{"name": "web_fetch", "arguments": {"url": "https://a.b"}}\n```'])
    assert calls[0] == {"name": "memory_search", "arguments": {"query": "x"}}
    assert "error" in calls[1] and "error" in calls[2] and "error" in calls[3]
    assert calls[4]["name"] == "web_fetch"


def test_parse_command_known_unknown_and_local():
    assert parse_command("/search погода в Лиссабоне") == ("search", "погода в Лиссабоне")
    assert parse_command("/remember  меня зовут Нел ") == ("remember", "меня зовут Нел")
    assert parse_command("/search") == ("search", "")
    assert parse_command("/graph") is None          # локальная команда интерфейса
    assert parse_command("/usr/bin/python — путь, а не команда") is None
    assert parse_command("обычный вопрос") is None


def test_tool_result_block_is_truncated():
    block = tool_result_block("web_fetch", "я" * 10000)
    assert block.startswith('<tool_result name="web_fetch">') and "обрезано" in block
    assert len(block) < 7000


def test_instructions_empty_without_tools():
    assert instructions([]) == ""
    text = instructions(["memory_search", "web_search"])
    assert "<tool_call>" in text and "web_search(queries)" in text


# ---------- инструменты ----------

class FakeWeb:
    def __init__(self):
        self.calls = []

    async def search(self, queries, max_results=5):
        self.calls.append(queries)
        return {"status": "ok", "sources": [
            {"title": "Первый", "url": "https://one.example/a", "snippet": "выдержка 1"},
            {"title": "Второй", "url": "https://two.example/b"},
        ]}

    async def fetch(self, url, max_chars=8000):
        return {"status": "ok", "url": url, "text": "текст страницы"}


def _tools(web=None, recall=None, remember=None):
    return ChatTools(recall=recall or (lambda q, k: {"status": "ok", "facts": [], "chunks": []}),
                     remember=remember or (lambda t: {"status": "queued"}),
                     web=web)


def test_web_tools_absent_when_web_disabled():
    t = _tools(web=FakeWeb())
    assert t.available(web=False, memory=True) == ["memory_search", "memory_remember"]
    assert "web_search" in t.available(web=True, memory=False)


def test_web_search_numbers_sources_across_calls():
    web = FakeWeb()
    t = _tools(web=web)
    first = t.run("web_search", {"queries": ["python"]})
    second = t.run("web_search", {"queries": ["rust"]})
    assert first.ok and [s["n"] for s in first.sources] == [1, 2]
    assert [s["n"] for s in second.sources] == [3, 4]
    assert "[1] Первый" in first.text and "https://one.example/a" in first.text
    assert web.calls == [["python"], ["rust"]]


def test_web_fetch_returns_source_and_text():
    t = _tools(web=FakeWeb())
    out = t.run("web_fetch", {"url": "https://one.example/a"})
    assert out.ok and out.sources[0]["url"] == "https://one.example/a"
    assert "текст страницы" in out.text


def test_tool_errors_become_results_not_exceptions():
    def boom(q, k):
        raise RuntimeError("индекс недоступен")

    t = _tools(recall=boom, web=False)
    out = t.run("memory_search", {"query": "что-нибудь"})
    assert not out.ok and "индекс недоступен" in out.text
    assert not t.run("web_search", {"queries": ["x"]}).ok        # веб выключен
    assert not t.run("no_such_tool", {}).ok


def test_memory_search_formats_facts_for_model():
    t = _tools(recall=lambda q, k: {"status": "ok", "chunks": [], "facts": [
        {"text": "Нел — разработчик", "confidence": 0.9}]})
    out = t.run("memory_search", {"query": "кто я"})
    assert out.ok and "Нел — разработчик" in out.text and "Факты:" in out.text


# ---------- агентный цикл в App ----------

class ScriptedBackend:
    """Подмена бэкенда: каждый вызов stream() отдаёт очередной сценарий ответа по частям."""
    name = "lmstudio"
    loaded = "fake"

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.base_url = "http://fake/v1"
        self.seen = []                       # сообщения, которые получила модель на каждом шаге

    def ensure(self, model, params=None):
        return model or "fake"

    def stream(self, model, messages, on_delta, cancelled):
        self.seen.append([dict(m) for m in messages])
        self.models_used = getattr(self, "models_used", []) + [model]
        script = self.scripts.pop(0)
        for piece in script:
            on_delta("token", piece)


def _app(tmp_path, monkeypatch, scripts):
    env = {"LLM_BACKEND": "lmstudio", "TRIPLEX_EXTRACT": "0", "TRIPLEX_MEMORY": "1"}
    app = App(env, data_dir=str(tmp_path))
    app.backend = ScriptedBackend(scripts)
    return app


def test_build_extractor_honors_explicit_model(monkeypatch):
    import pulsenet.memory_service as ms

    seen = []
    monkeypatch.setattr(ms, "openai_chat_caller",
                        lambda url, model: seen.append((url, model)) or (lambda prompt: "{}"))
    env = {"TRIPLEX_LLM_URL": "http://llm/v1", "TRIPLEX_LLM_MODEL": "env-model"}
    assert ms.build_extractor(env) is not None
    assert seen[-1][1] == "env-model"
    assert ms.build_extractor(env, model="chat/model") is not None
    assert seen[-1][1] == "chat/model"                    # модель чата перекрывает .env


def test_ingest_uses_one_off_extractor(tmp_path):
    mem = TriplexMemory(str(tmp_path / "m"), encoder=HashEncoder(64))
    mem.extractor = lambda text: (_ for _ in ()).throw(AssertionError("не тот экстрактор"))
    seen = []

    def other(text):
        seen.append(text)
        return {"entities": [], "facts": []}

    out = mem.ingest("текст", extractor=other)
    assert out["status"] == "ok" and seen == ["текст"]


def test_distill_fact_rewrites_through_supersede(tmp_path, monkeypatch):
    import pulsenet.app as appmod

    app = _app(tmp_path, monkeypatch, [["ok"]])
    doc = app.memory.ingest("Нел живёт в Амстердаме", session_id="s", role="user")
    f = app.memory.journal.assert_fact("Нел", "живёт в", "Амстердаме",
                                       source={"doc_id": doc["doc_id"], "span": [0, 5]})
    monkeypatch.setattr(app, "model_id", lambda: "fake")
    monkeypatch.setattr(appmod, "openai_chat_caller",
                        lambda *a, **kw: lambda prompt:
                        '{"verdict":"update","s":"Нел","r":"живёт в","o":"Роттердаме",'
                        '"confidence":0.9,"reason":"переехала"}')
    out = app.distill_fact(f.fact_id)
    assert out["verdict"] == "update"
    current = app.memory.list_facts(10)
    assert any(x["o"] == "Роттердаме" for x in current)
    assert all(x["fact_id"] != f.fact_id for x in current)  # старый факт закрыт supersede


def test_neuron_tools_dedupe_recombine_edit_and_forget(tmp_path, monkeypatch):
    """Управление нейронами поверх общего memory-интерфейса: работает и local, и mcp."""
    import pulsenet.app as appmod

    app = _app(tmp_path, monkeypatch, [["ok"]])
    doc = app.memory.ingest("Нел живёт в Амстердаме и любит кофе", session_id="s", role="user")
    src = {"doc_id": doc["doc_id"], "span": [0, 8]}
    f1 = app.memory.journal.assert_fact("Нел", "живёт в", "Амстердаме", source=src)
    f2 = app.memory.journal.assert_fact("Нел", "проживает в", "Амстердаме", source=src)
    f3 = app.memory.journal.assert_fact("Нел", "любит", "кофе", source=src)
    monkeypatch.setattr(app, "model_id", lambda: "fake")
    answers = iter([
        json.dumps({"groups": [{"ids": [f1.fact_id, f2.fact_id],
                                "reason": "один смысл", "canonical": "Нел живёт в Амстердаме"}]}),
        json.dumps({"s": "Нел", "r": "живёт в", "o": "Амстердаме",
                    "confidence": 0.9, "reason": "объединили переформулировки"}),
        json.dumps({"s": "Нел", "r": "любит", "o": "чай",
                    "confidence": 0.95, "reason": "уточнено вручную"}),
    ])
    monkeypatch.setattr(appmod, "openai_chat_caller",
                        lambda *a, **kw: (lambda prompt: next(answers)))

    dup = app.dedupe_facts()
    assert dup["groups"] and set(dup["groups"][0]["ids"]) == {f1.fact_id, f2.fact_id}

    preview = app.recombine_facts([f1.fact_id, f2.fact_id])
    assert preview["applied"] is False and preview["preview"]["o"] == "Амстердаме"

    merged = app.recombine_facts([f1.fact_id, f2.fact_id], apply=True)
    assert merged["applied"] and merged["superseded"] == f1.fact_id
    assert f2.fact_id in merged["retracted"]
    alive = {f["fact_id"]: f for f in app.memory.list_facts(50)}
    assert f1.fact_id not in alive and f2.fact_id not in alive and f3.fact_id in alive
    assert merged["fact_id"] in alive

    edited = app.edit_fact(f3.fact_id, o="чай", reason="уточнено вручную")
    alive = {f["fact_id"]: f for f in app.memory.list_facts(50)}
    assert f3.fact_id not in alive and alive[edited["new_fact_id"]]["o"] == "чай"

    node = next(n for n in app.memory.graph(50)["nodes"] if n["label"] == "Нел")
    gone = app.forget_facts(node_id=node["id"])
    assert gone["retracted"]
    assert not any(f["s"] == "Нел" or f["o"] == "Нел" for f in app.memory.list_facts(50))


def test_model_choice_follows_loaded_and_active_model(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [["ok"]])
    app.backend.refresh_loaded = lambda: "loaded-in-lmstudio"
    assert app.default_rel() == "loaded-in-lmstudio"   # не дёргаем модель из .env
    app.active_model = "explicit/model"
    assert app.model_id() == "explicit/model"          # экстракция идёт моделью чата


def _run(app, message, web=False, chat_id="c1"):
    events, tokens = [], []

    def on_delta(kind, payload):
        if kind == "token":
            tokens.append(payload)
        elif kind in ("tool", "sources"):
            events.append((kind, payload))

    answer = app.chat(message, chat_id, on_delta, web=web)
    return answer, "".join(tokens), events


def test_agent_loop_runs_tool_and_hides_call(tmp_path, monkeypatch):
    call = '<tool_call>{"name": "memory_search", "arguments": {"query": "Python"}}</tool_call>'
    app = _app(tmp_path, monkeypatch, [["Сейчас проверю. ", call], ["Ответ: Python 3.12."]])
    answer, shown, events = _run(app, "Какой у нас Python?")

    assert "<tool_call>" not in shown and "tool_call" not in answer
    assert answer == "Сейчас проверю. Ответ: Python 3.12."
    statuses = [p["status"] for k, p in events if k == "tool"]
    assert statuses == ["running", "done"]
    second_step = app.backend.seen[1]
    assert second_step[-1]["role"] == "user"
    assert '<tool_result name="memory_search">' in second_step[-1]["content"]
    assert app._history("c1")[-1]["content"] == answer          # в историю — итоговый ответ


def test_agent_loop_stops_after_max_steps(tmp_path, monkeypatch):
    call = '<tool_call>{"name": "memory_search", "arguments": {"query": "x"}}</tool_call>'
    scripts = [[call] for _ in range(MAX_STEPS + 3)]
    app = _app(tmp_path, monkeypatch, scripts)
    _run(app, "вопрос")
    assert len(app.backend.seen) == MAX_STEPS + 1


def test_unknown_tool_is_reported_to_model(tmp_path, monkeypatch):
    bad = '<tool_call>{"name": "web_search", "arguments": {"queries": ["x"]}}</tool_call>'
    app = _app(tmp_path, monkeypatch, [[bad], ["Понял, без интернета."]])
    answer, _, events = _run(app, "найди", web=False)
    assert answer.endswith("Понял, без интернета.")
    assert "недоступен" in app.backend.seen[1][-1]["content"]
    assert any(k == "tool" and p["status"] == "error" for k, p in events)


def test_web_flag_enables_web_tools_and_sources(tmp_path, monkeypatch):
    call = '<tool_call>{"name": "web_search", "arguments": {"queries": ["погода"]}}</tool_call>'
    app = _app(tmp_path, monkeypatch, [[call], ["Источник [1]."]])
    monkeypatch.setattr("pulsenet.tools._web_module", lambda: FakeWeb())
    answer, _, events = _run(app, "что с погодой", web=True)
    assert answer == "Источник [1]."
    sources = [p for k, p in events if k == "sources"]
    assert sources and sources[0][0]["url"] == "https://one.example/a"
    assert "web_search" in app.backend.seen[0][0]["content"]        # инструкция видна модели


def test_command_search_runs_before_model(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [["Вот ответ по [1]."]])
    fake = FakeWeb()
    monkeypatch.setattr("pulsenet.tools._web_module", lambda: fake)
    answer, _, events = _run(app, "/search курс доллара")
    assert fake.calls == [["курс доллара"]]
    user_msg = app.backend.seen[0][-1]["content"]
    assert "Найди в интернете и ответь по источникам: курс доллара" in user_msg
    assert '<tool_result name="web_search">' in user_msg
    assert answer == "Вот ответ по [1]."


def test_command_without_argument_replies_with_usage(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [])
    answer, shown, _ = _run(app, "/memory")
    assert "/memory запрос" in answer and shown == answer
    assert app.backend.seen == []                                   # модель не вызывалась


def test_command_remember_stores_text(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [["Запомнила."]])
    queued = []
    monkeypatch.setattr(app, "index_async",
                        lambda text, chat_id, role="user", force=False: queued.append(text))
    _run(app, "/remember Проект живёт в pulsenet")
    assert queued == ["Проект живёт в pulsenet"]


# ---------- карта нейронов ----------

def test_graph_contains_entities_and_active_facts(tmp_path):
    mem = TriplexMemory(str(tmp_path), encoder=HashEncoder(64))
    doc = mem.ingest("Нел работает над BiChat. Проект на Python.", session_id="s", role="user")
    doc_id = doc["doc_id"]
    mem.journal.assert_fact("Нел", "работает над", "BiChat", source={"doc_id": doc_id, "span": [0, 10]})
    mem.journal.assert_fact("BiChat", "написан на", "Python", source={"doc_id": doc_id, "span": [0, 10]})
    g = mem.graph(max_edges=10)
    ids = {n["id"] for n in g["nodes"]}
    assert {"Нел", "BiChat", "Python"} <= ids
    assert len(g["edges"]) == 2
    bichat = next(n for n in g["nodes"] if n["id"] == "BiChat")
    assert bichat["degree"] == 2 and bichat["created"]
    assert g["growth"] and g["growth"][-1]["facts"] == 2
    assert g["totals"]["facts"] == 2


def test_graph_limits_edges_to_freshest(tmp_path):
    mem = TriplexMemory(str(tmp_path), encoder=HashEncoder(64))
    doc = mem.ingest("текст", session_id="s", role="user")
    for i in range(5):
        mem.journal.assert_fact(f"A{i}", "связан с", f"B{i}", source={"doc_id": doc["doc_id"], "span": [0, 4]})
    g = mem.graph(max_edges=3)
    assert len(g["edges"]) == 3 and g["truncated"] is True


# ---------- SSE: порядок событий относительно текста ----------

def test_job_events_keep_position_in_text():
    job = server.ChatJob("c", "m", web=True)
    job.add_delta("token", "Ищу ")
    job.add_delta("tool", {"id": "t1", "status": "running"})
    job.add_delta("token", "готово")
    snap = job.snapshot()
    assert snap["text"] == "Ищу готово"
    assert snap["events"][0]["at"] == len("Ищу ")
    assert job.web is True


# ---------- журнал активности и кэш веб-поиска ----------

def test_activity_log_round_trip(tmp_path):
    from pulsenet.unified_mcp.activity import ActivityLog
    log = ActivityLog(str(tmp_path))
    log.record("alice", "web_search", "python 3.12", True)
    log.record("alice", "triplex_assert", "Нел — разработчик", False)
    items = log.recent("alice", 10)
    assert [i["tool"] for i in items] == ["triplex_assert", "web_search"]
    assert items[0]["ok"] is False
    assert log.recent("bob") == []


def test_web_search_cache_avoids_second_upstream_call(monkeypatch):
    from pulsenet.unified_mcp import web
    web.clear_cache()
    calls = []

    async def fake_uncached(queries, max_results):
        calls.append(queries)
        return {"status": "ok", "sources": [{"title": "t", "url": "https://x.example/"}]}

    monkeypatch.setattr(web, "_search_uncached", fake_uncached)
    first = asyncio.run(web.search(["кэш"], max_results=3))
    second = asyncio.run(web.search(["кэш"], max_results=3))
    assert len(calls) == 1
    assert second["cached"] is True and second["sources"] == first["sources"]
    web.clear_cache()


def test_chat_uses_selected_model(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [["ответ"]])
    _run_model = app.chat("привет", "m1", lambda k, p: None, model="other-model")
    assert _run_model == "ответ"
    assert app.backend.models_used == ["other-model"]


def test_chat_defaults_to_configured_model(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, [["ответ"]])
    monkeypatch.setattr(app, "default_rel", lambda: "configured-model")
    app.chat("привет", "m2", lambda k, p: None)
    assert app.backend.models_used == ["configured-model"]
