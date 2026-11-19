# -*- coding: utf-8 -*-
"""Внутренний планировщик: варианты мыслей, выбор ответа, обратная связь по цепочке."""
import pytest

from pulsenet import BrainNet, Config
from pulsenet.planner import Planner

CORPUS = ("Нейронная сеть учится на примерах. Температура делает ответы разнообразнее. "
          "Память хранит опыт сети. Огонь согревает воду и кипятит её. "
          "Связи между словами усиливаются при встрече.")


@pytest.fixture
def net():
    n = BrainNet(Config(seed=0))
    n.pretrain(CORPUS, epochs=3)
    return n


def test_planner_builds_several_scored_variants(net):
    plan = Planner(net, width=4).think("Что такое нейронная сеть?")
    texts = [c.text for c in plan.candidates]
    assert len(texts) >= 2 and len(set(texts)) == len(texts)      # разные варианты, без дублей
    scores = [c.score for c in plan.candidates]
    assert scores == sorted(scores, reverse=True)                  # лучший первым
    assert plan.answer == plan.candidates[0].text


def test_chosen_answer_is_a_thought_and_stays_in_mind(net):
    p = Planner(net)
    plan = p.think("Что делает температура?")
    assert plan.answer in plan.thoughts
    assert net.imprints.short                                       # выбранная мысль — слепок
    assert net.thought is not None                                  # мысль переживает ход


def test_empty_question_gives_no_answer_without_crash(net):
    plan = Planner(net).think("")
    assert plan.answer == "" and plan.candidates == []


def test_bad_feedback_moves_answer_to_another_variant(net):
    p = Planner(net)
    first = p.think("Что такое нейронная сеть?").answer
    changed = False
    for _ in range(5):
        p.feedback("bad")
        if p.think("Что такое нейронная сеть?").answer != first:
            changed = True
            break
    assert changed                                                  # коррекция влияет на выбор


def test_backward_credit_is_stronger_near_the_end_of_chain(net):
    p = Planner(net)
    plan = p.think("Что такое нейронная сеть?")
    words = plan.candidates[0].words
    # переходы, которые реально есть в уроках (ассоциативные связи коррекцию не получают)
    idx = [i for i in range(1, len(words)) if words[i] in net.trans1.get(words[i - 1], {})]
    if len(idx) < 2:
        pytest.skip("в варианте мало переходов из уроков")
    before = {i: net.trans1[words[i - 1]][words[i]] for i in idx}
    p.feedback("bad")
    drop = {i: 1 - net.trans1.get(words[i - 1], {}).get(words[i], 0.0) / before[i] for i in idx}
    assert drop[idx[-1]] > drop[idx[0]]                             # конец цепочки получает больше


def test_feedback_without_plan_is_reported(net):
    assert Planner(net).feedback("bad")["ok"] is False


def test_app_plan_and_feedback_endpoints(tmp_path):
    from pulsenet.app import App
    app = App({"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}, data_dir=str(tmp_path))
    app.net.pretrain(CORPUS, epochs=2)
    out = app.plan("Что такое память?")
    assert out["ok"] and isinstance(out["thoughts"], list) and out["thoughts"]
    assert app.plan_feedback("bad")["ok"] is True
    assert app.plan_feedback("maybe")["ok"] is False
    assert any(i["kind"] == "plan" for i in app.thoughts.recent(20))


def test_pruning_keeps_strongest_and_drops_weak_transitions(net):
    before = sum(len(d) for d in net.trans1.values())
    net.trans1.setdefault(5, {})
    net.trans1[5] = {i: 0.1 for i in range(1, 60)}      # много слабых продолжений
    net.trans1[5][7] = 50.0                              # одно сильное
    removed = net.prune_transitions(max_next=10, min_count=0.5)
    assert removed > 0
    assert len(net.trans1[5]) <= 10 and 7 in net.trans1[5]
    assert sum(len(d) for d in net.trans1.values()) <= before + 10


def test_feedback_also_updates_output_head(net):
    p = Planner(net)
    p.think("Что такое нейронная сеть?")
    updates = net.updates
    p.feedback("bad")
    assert net.updates == updates + 1                    # выходная голова получила урок с весом < 0


def test_align_start_is_default_and_respond_is_deterministic(net):
    assert net.cfg.align_start is True
    q = "Что делает температура?"
    assert net.respond(q, temp=0).text == net.respond(q, temp=0).text


def test_net_mode_answers_with_planner_and_pushes_thoughts(tmp_path):
    from pulsenet.app import App
    app = App({"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}, data_dir=str(tmp_path))
    app.net.pretrain(CORPUS, epochs=2)
    app.set_mode(mode="net")
    events = []
    app.run_generation("Что такое память?", None, "c1", events.append)
    turns = [e["turn"] for e in events if "turn" in e]
    assert turns and turns[-1]["source"] == "net"
    assert any(i["kind"] == "plan" for i in app.thoughts.recent(40))
    assert app._last_plan is not None                    # можно оценить ответ


def test_net_answer_switch_is_validated(tmp_path):
    from pulsenet.app import App
    app = App({"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}, data_dir=str(tmp_path))
    assert app.set_mode(net_answer="respond")["net_answer"] == "respond"
    assert app.set_mode(net_answer="magic")["ok"] is False
