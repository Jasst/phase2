# -*- coding: utf-8 -*-
"""Слепки мыслей, консолидация, забывание, проактивное рассуждение с торможением."""
import os
import tempfile

import pytest

from pulsenet import BrainNet, Config, store
from pulsenet.imprints import ImprintCache, jaccard
from pulsenet.reasoning import Reasoner

CORPUS = ("Нейронная сеть учится на примерах. Связи между словами усиливаются при встрече. "
          "Температура делает ответы разнообразнее. Память хранит опыт сети. "
          "Огонь согревает воду и кипятит её. Море полно солёной воды. Сеть помнит порядок слов.")


def trained(**kw):
    net = BrainNet(Config(seed=3, **kw))
    net.pretrain(CORPUS, epochs=3)
    return net


def test_similar_imprints_merge_instead_of_piling_up():
    cache = ImprintCache(Config())
    a = cache.add([5, 6, 7, 8], 0.4)
    b = cache.add([5, 6, 7, 8], 0.5)
    assert a is b and a.hits == 2 and len(cache.short) == 1
    assert a.importance > 0.4


def test_consolidation_runs_every_batch_and_clears_pending():
    net = BrainNet(Config(seed=1, imprint_batch=10))
    for i in range(10):
        net.imprints.add([10 + 2 * i, 11 + 2 * i, 12 + 2 * i], 0.8)   # разные мысли
    assert net.imprints.due()
    net.consolidate()
    assert len(net.imprints.pending) == 0
    assert len(net.imprints.longterm) >= 1
    assert net.trans1                              # цепочка закреплена в памяти порядка


def test_eviction_is_by_importance_and_protects_important_thoughts():
    cfg = Config(longterm_cap=3, protect_at=0.5)
    cache = ImprintCache(cfg)
    cache.store_longterm([1, 2, 3], 0.9)           # защищённая
    cache.store_longterm([4, 5, 6], 0.1)           # слабая — должна уйти первой
    cache.store_longterm([7, 8, 9], 0.7)           # защищённая
    cache.store_longterm([10, 11, 12], 0.3)        # вытесняет слабую
    kept = sorted(round(i.importance, 2) for i in cache.longterm)
    assert kept == [0.3, 0.7, 0.9]
    assert all(i.words != (4, 5, 6) for i in cache.longterm)


def test_important_lesson_leaves_an_imprint_that_gets_replayed():
    net = trained()
    net.teach("Какой цвет у неба?", "Небо голубое днём.", weight=1.0, replay=False)
    assert any(i.words for i in net.imprints.short + net.imprints.pending)
    # защищённая мысль повторяется при уроках и не пропадает из долговременной памяти
    net.imprints.store_longterm(list(net.vocab.encode(["небо", "голубое"], grow=False)), 0.9)
    before = len(net.imprints.longterm)
    net.replay_protected()
    assert len(net.imprints.longterm) == before


def test_reasoning_does_not_repeat_pairs_within_an_impulse():
    net = trained()
    r = Reasoner(net)
    seed = net._dist(net.vocab.encode(["сеть"], grow=False))
    res = r.impulse(seed, net.vocab.index["сеть"])
    assert res is not None
    words, _ = res
    pairs = list(zip(words, words[1:]))
    assert len(pairs) == len(set(pairs))           # синапс-запрет: петли внутри мысли нет
    assert len(words) <= net.cfg.reason_words       # стоп-паттерн: мысль конечна


def test_reasoning_run_stops_on_loop_and_leaves_imprints():
    net = trained()
    before = net.imprints.step
    thoughts = Reasoner(net).run(steps=6)
    assert len(thoughts) <= 6
    assert all(t.strip() for t in thoughts)
    assert net.imprints.step - before == len(thoughts)   # каждая мысль оставила слепок


def test_inner_voice_answers_from_imprints():
    net = trained()
    Reasoner(net).run(steps=3)
    reply = Reasoner(net).inner_voice("Что такое сеть?")
    assert isinstance(reply.text, str)


def test_imprints_survive_save_and_load():
    net = trained()
    net.imprints.add(list(net.vocab.encode(["сеть", "помнит", "порядок"], grow=False)), 0.8)
    net.imprints.store_longterm(list(net.vocab.encode(["огонь", "согревает"], grow=False)), 0.9)
    with tempfile.TemporaryDirectory() as d:
        store.save(net, d)
        loaded = store.load(d)
    assert [i.words for i in loaded.imprints.longterm] == [i.words for i in net.imprints.longterm]
    assert [i.importance for i in loaded.imprints.short] == pytest.approx([i.importance for i in net.imprints.short])


def test_app_reason_and_voice_endpoints(tmp_path):
    from pulsenet.app import App
    app = App({"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}, data_dir=str(tmp_path))
    app.net.pretrain(CORPUS, epochs=2)
    out = app.reason_step(2)
    assert out["ok"] and "imprints" in out
    v = app.voice("Что такое память?")
    assert v["ok"] and isinstance(v["text"], str)
    assert "longterm" in app.imprints_state()
    assert app.set_mode(proactive=True)["proactive"] is True


def test_proactive_mode_runs_after_each_turn(tmp_path):
    from pulsenet.app import App
    app = App({"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "http://127.0.0.1:9/v1"}, data_dir=str(tmp_path))
    app.net.pretrain(CORPUS, epochs=2)
    app.set_mode(proactive=True)
    events = []
    before = app.net.imprints.step
    app._after_turn("сеть помнит порядок", events.append)
    phases = [e["gen_phase"]["phase"] for e in events if "gen_phase" in e]
    assert "Сеть рассуждает" in phases
    assert app.net.imprints.step > before                 # рассуждение оставило слепки
