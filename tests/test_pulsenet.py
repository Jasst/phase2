# -*- coding: utf-8 -*-
import numpy as np

from pulsenet import BrainNet, Config, HTTPTeacher, store
from pulsenet.vocab import tokenize


def make(seed=1):
    return BrainNet(Config(seed=seed))


def test_tokenize_handles_cyrillic_and_punctuation():
    assert tokenize("Кошка ловит мышь.") == ["кошка", "ловит", "мышь", "."]


def test_online_distillation_reduces_loss():
    n = make()
    losses = [n.learn("что делает кошка", "кошка ловит мышь") for _ in range(25)]
    assert losses[-1] < losses[0] * 0.75


def test_neighbouring_neurons_move_closer():
    n = make()
    n.learn("alpha beta", "gamma delta")
    a, b = n.vocab.index["alpha"], n.vocab.index["beta"]

    def cos():
        x, y = n.cortex.E[a], n.cortex.E[b]
        return float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y)))

    before = cos()
    for _ in range(40):
        n.learn("alpha beta", "gamma delta")
    assert cos() > before


def test_new_facts_spawn_clusters():
    n = make()
    n.learn("one two", "three four")
    k1 = len(n.cortex.members)
    n.learn("red green", "blue yellow")
    assert len(n.cortex.members) > k1


def test_chain_is_directed_question_to_answer():
    n = make()
    for _ in range(3):
        n.learn("alpha beta", "gamma delta")
    kq = n.cortex.cluster_of(n.vocab.index["alpha"])
    ka = n.cortex.cluster_of(n.vocab.index["gamma"])
    assert kq != ka
    T = n.chain.T
    assert T[kq, ka] > T[ka, kq]


def test_respond_reproduces_taught_answer():
    n = make()
    for _ in range(20):
        n.learn("what is the fox", "the red fox runs fast")
    r = n.respond("what is the fox", temp=0)
    assert "red fox" in r.text.lower()
    assert r.path, "импульс должен пройти хотя бы один шаг"


def test_unknown_question_gives_empty_reply():
    n = make()
    n.learn("кошка ловит мышь", "мышь бежит")
    assert n.respond("совсем неизвестные слова", temp=0).text == ""


def test_negative_feedback_keeps_chain_non_negative():
    n = make()
    n.teach("alpha beta", "gamma delta", 1.0, replay=False)
    n.teach("alpha beta", "gamma delta", -1.0, replay=False)
    assert (n.chain.T >= 0).all()


def test_replay_buffer_is_bounded():
    n = BrainNet(Config(seed=2, replay_size=5))
    for i in range(12):
        n.teach(f"вопрос {i} слово", f"ответ {i} слово")
    assert len(n.buffer) == 5


def test_pretrain_builds_clusters_from_text():
    n = make()
    count = n.pretrain("Огонь горит. Огонь греет дом. Вода течёт. Вода холодная.")
    assert count >= 3
    assert len(n.cortex.members) > 1


def test_save_and_load_give_same_state_and_reply(tmp_path):
    n = make()
    for _ in range(5):
        n.teach("что такое огонь", "огонь горячее пламя")
    d = str(tmp_path / "state")
    store.save(n, d)
    m = store.load(d)
    assert m.stats() == n.stats()
    assert m.respond("что такое огонь", temp=0).text == n.respond("что такое огонь", temp=0).text


def test_unreachable_teacher_returns_none_without_raising():
    t = HTTPTeacher("http://127.0.0.1:9/v1/chat/completions", "m", timeout=1)
    assert t.answer("привет") is None
    assert t.error
