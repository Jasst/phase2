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


def test_development_is_gradual_per_lesson():
    n = make()
    n.pretrain("Огонь горит и греет дом. Вода течёт и холодная. Солнце светит днём. Ветер дует в поле. "
               "Птица поёт в саду. Рыба плывёт в реке. Дерево растёт в лесу.", epochs=1)
    before = len(n.cortex.members)
    n.teach("огонь горит", "огонь греет дом", replay=False)
    after = len(n.cortex.members)
    assert before - after <= n.cfg.max_merges_per_lesson


def test_thought_persists_between_turns():
    n = make()
    for _ in range(5):
        n.teach("что такое огонь", "огонь горячее пламя", 1.0, replay=False)
    assert n.thought is None
    first = n.respond("что такое огонь", temp=0)
    assert n.thought is not None, "после ответа мысль должна сохраниться"
    kept = n.thought.copy()
    n.respond("что такое огонь", temp=0)
    assert n.thought is not None and n.thought.shape == kept.shape
    assert first.confidence > 0


def test_capacity_grows_without_fixed_limit():
    n = BrainNet(Config(seed=2, max_clusters=4))
    n.pretrain("Огонь горит и греет дом. Вода течёт и холодная. Солнце светит днём. "
               "Ветер дует в поле. Птица поёт в саду. Рыба плывёт в реке.", epochs=1)
    assert n.cortex.cap > 4
    assert n.chain.T.shape == (n.cortex.cap, n.cortex.cap)
    assert len(n.cortex.members) <= n.cortex.cap


def test_self_prediction_error_and_confidence_are_reported():
    n = make()
    for _ in range(4):
        n.teach("alpha beta", "gamma delta", 1.0, replay=False)
    r = n.respond("alpha beta", temp=0)
    assert 0.0 <= r.confidence <= 1.0
    assert r.self_error is None or 0.0 <= r.self_error <= 2.0
    assert n.self_error_avg() is None or isinstance(n.self_error_avg(), float)


def test_unknown_question_is_reported_as_unknown_not_guessed():
    n = make()
    n.teach("alpha beta", "gamma delta", 1.0, replay=False)
    r = n.respond("совсем чужие слова", temp=0)
    assert r.known is False and r.text == ""
