# -*- coding: utf-8 -*-
"""Поток мыслей: внутренний нарратив, спонтанные мысли, участие в ответе."""
import json

import pytest

from pulsenet import BrainNet, Config
from pulsenet.thoughts import ThoughtStream


def make(seed=1):
    return BrainNet(Config(seed=seed))


def test_stream_pushes_dedups_and_trims(tmp_path):
    st = ThoughtStream(str(tmp_path / "thoughts.json"))
    assert st.push("  ", "inner") is None
    a = st.push("мысль первая", "turn")
    b = st.push("мысль вторая", "inner")
    assert a and b and st.items[-1]["text"] == "мысль вторая"
    # подряд идущая одинаковая не дублируется
    assert st.push("мысль вторая", "inner") is None
    for i in range(100):
        st.push(f"мысль номер {i}", "inner")
    assert len(st.items) == 80
    assert st.recent(1)[0]["text"] == "мысль номер 99"


def test_stream_persists_and_context(tmp_path):
    path = str(tmp_path / "thoughts.json")
    st = ThoughtStream(path)
    st.push("кошка ловит мышь", "turn")
    st.push("мышь сидит в траве", "inner")
    st2 = ThoughtStream(path)
    assert [i["text"] for i in st2.items] == ["кошка ловит мышь", "мышь сидит в траве"]
    assert "мышь" in st2.context_text(2)
    st2.clear()
    assert ThoughtStream(path).items == []


def test_think_needs_knowledge_then_wanders():
    n = make()
    assert n.think() is None                      # пустая сеть думать не о чем
    n.learn("что делает кошка", "кошка ловит мышь")
    text = n.think()
    assert text is not None and len(tokenize_ok(text)) >= 1


def tokenize_ok(text):
    from pulsenet.vocab import tokenize
    return [w for w in tokenize(text) if w.isalnum()]


def test_think_survives_dead_end_chain():
    """Маленькая сеть: ансамбль мысли без исходящих связей не должен останавливать нарратив."""
    n = make()
    n.learn("alpha beta", "gamma delta")
    n.learn("alpha beta", "gamma delta")
    n.respond("alpha beta", temp=0)
    # убираем все исходящие связи — импульс в тупике
    n.chain.T[n.chain.T > 0] = 0.0
    text = n.think()
    assert text is not None and text != ""


def test_respond_accepts_narrative_context():
    n = make()
    for _ in range(10):
        n.learn("what is the fox", "the red fox runs fast")
    r = n.respond("what is the fox", temp=0, context="red fox runs")
    assert r.text != ""
    assert r.confidence >= 0


def test_thought_moves_with_ensemble_merge():
    """При слиянии ансамблей масса мысли переезжает, мёртвой массы от слияний нет."""
    n = make(seed=3)
    n.learn("alpha beta", "gamma delta")
    n.respond("alpha beta", temp=0)
    before = None if n.thought is None else float(n.thought.sum())
    for _ in range(6):
        n.learn("alpha beta", "gamma delta")     # провоцирует merge_step
    if n.thought is not None:
        assert abs(float(n.thought.sum()) - before) < 1e-6


def test_app_think_step_and_feedback(tmp_path):
    from pulsenet.app import App
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}
    app = App(env, data_dir=str(tmp_path / "d"))
    assert app.think_step(1)["thoughts"] == []          # без знаний мыслей нет
    app.teach("какой город столица франции", "париж красивый город франции", 1.0, "manual")
    r = app.think_step(2)
    assert len(r["narrative"]) >= 1
    st0 = app.net.stats()["уроков"]
    app.think_step(1)
    assert app.net.stats()["уроков"] == st0             # шаги нарратива не считаются уроками
    bad = app.feedback("какой город столица франции", "париж красивый город франции", "bad")
    good = app.feedback("какой город столица франции", "париж красивый город франции", "good")
    assert bad["ok"] and good["ok"]
    res = app.respond("какой город столица франции")
    assert res["confidence"] >= 0


def test_net_mode_turn_adds_thoughts_without_lessons(tmp_path):
    from pulsenet.app import App
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}
    app = App(env, data_dir=str(tmp_path / "d"))
    app.teach("где находится париж", "париж это город франции", 1.0, "manual")
    app.set_mode(mode="net")
    lessons = app.net.lessons
    events = []
    app.run_generation("где находится париж", "", "c1", lambda ev: events.append(ev))
    kinds = [e["thought"]["kind"] for e in events if "thought" in e]
    assert "turn" in kinds
    assert app.net.lessons == lessons                   # урок от хода не добавляется


def test_thoughts_survive_restart(tmp_path):
    from pulsenet.app import App
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}
    d = str(tmp_path / "d")
    app = App(env, data_dir=d)
    app.teach("вопрос про кошку", "кошка ловит мышь", 1.0, "manual")
    app.think_step(1)
    n_before = len(app.thoughts.items)
    app2 = App(env, data_dir=d)
    assert len(app2.thoughts.items) == n_before
    assert app2.thoughts_state()["auto_think"] is True
