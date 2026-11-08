# -*- coding: utf-8 -*-
"""Слои: контракт ролей, направленность прямого слоя, рекуррентное создание связей,
вопросы внутреннего слоя.

Запуск из корня проекта:  python tests/test_cortex.py
"""
import os
import random
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import _isolate  # noqa: E402  пишем во временную папку, не в проект
_isolate.isolate()

import cortex  # noqa: E402
from brain_chat import Brain, G_IN, G_INTEG  # noqa: E402
from mind import key  # noqa: E402
from cortex import Cortex, WorldModel, forward, recurrent  # noqa: E402


def fresh_brain():
    b = Brain()
    b._clear()          # без шумовых нейронов: граф в тесте полностью управляем
    return b


def add_word(b, w, layer=G_IN):
    nid = b._ids_for([w])[0]
    b.neurons[nid].layer = layer
    return nid


def link(b, a_id, b_id, w=1.0):
    b.links[key(a_id, b_id)] = w


class LayerContractTests(unittest.TestCase):
    def test_forward_does_not_write_links(self):
        b = fresh_brain()
        a, x, c = add_word(b, "кот"), add_word(b, "ест"), add_word(b, "рыба")
        link(b, a, x)
        link(b, x, c)
        before = dict(b.links)
        forward(b, {"кот": 1.0})
        self.assertEqual(before, b.links)

    def test_world_model_does_not_write_links(self):
        b = fresh_brain()
        for w in ("кот", "ест", "рыба", "дом"):
            add_word(b, w)
        b.word_seen.update({"кот": 3, "ест": 2, "рыба": 2, "дом": 1})
        before = dict(b.links)
        WorldModel().questions(b, limit=3)
        self.assertEqual(before, b.links)


class FeedForwardDirectionTests(unittest.TestCase):
    def test_signal_flows_up_layers_only(self):
        b = fresh_brain()
        a = add_word(b, "кот", G_IN)          # слой 0
        bb = add_word(b, "дом", G_IN)         # слой 0
        c = add_word(b, "рыба", G_INTEG)      # слой 2
        link(b, a, bb)
        link(b, a, c)
        up = forward(b, {"кот": 1.0})
        self.assertIn("рыба", up)             # вверх по слоям — проходит
        self.assertIn("дом", up)
        down = forward(b, {"рыба": 1.0})
        self.assertNotIn("кот", down)         # вниз — нет
        self.assertEqual(down, {})


def connected(b, x, y):
    """Прямая связь или путь через один скрытый нейрон-мост (как в connect_words)."""
    if key(x, y) in b.links:
        return True
    for m in b.neurons:
        if m not in (x, y) and key(x, m) in b.links and key(m, y) in b.links:
            return True
    return False


class RecurrentTests(unittest.TestCase):
    def _cluster(self):
        """x и y связаны каждое с z, но между собой связи нет."""
        b = fresh_brain()
        x, y, z = add_word(b, "лодка"), add_word(b, "море"), add_word(b, "берег")
        link(b, x, z)
        link(b, y, z)
        return b

    def test_creates_missing_link_between_coactive_words(self):
        created_any = False
        for seed in range(60):
            b = self._cluster()
            created, _ = recurrent(b, {"лодка": 1.0, "море": 1.0}, random.Random(seed))
            if created:
                created_any = True
                self.assertTrue(connected(b, b.words["лодка"], b.words["море"]))
                break
        self.assertTrue(created_any, "рекуррентный слой не создал ни одной связи")

    def test_budget_zero_creates_nothing(self):
        old = cortex.NEW_MAX
        cortex.NEW_MAX = 0
        try:
            b = self._cluster()
            created, _ = recurrent(b, {"лодка": 1.0, "море": 1.0}, random.Random(0))
            self.assertEqual(created, [])
            self.assertNotIn(key(b.words["лодка"], b.words["море"]), b.links)
        finally:
            cortex.NEW_MAX = old

    def test_existing_link_is_strengthened_not_duplicated(self):
        b = self._cluster()
        x, y = b.words["лодка"], b.words["море"]
        link(b, x, y, 0.5)
        n_before = len(b.links)
        recurrent(b, {"лодка": 1.0, "море": 1.0}, random.Random(0))
        self.assertGreater(b.links[key(x, y)], 0.5)
        self.assertEqual(len(b.links), n_before)   # существующая пара не дублируется


class WorldModelTests(unittest.TestCase):
    def test_frequent_unlinked_word_becomes_question_once(self):
        b = fresh_brain()
        add_word(b, "кот")
        b.word_seen["кот"] = 3
        wm = WorldModel()
        first = wm.questions(b, limit=5)
        self.assertTrue(any("кот" in q for q in first))
        second = wm.questions(b, limit=5)
        self.assertFalse(any("кот" in q and "обычно" in q for q in second))


class CortexIntegrationTests(unittest.TestCase):
    def test_idle_on_real_brain_returns_questions_and_report(self):
        b = fresh_brain()
        words = ["кот", "ест", "рыбу", "дома"]
        b.train_words(words)
        c = Cortex(seed=1)
        qs = c.idle(b, words)
        self.assertIsInstance(qs, list)
        self.assertEqual(len(c.report(b)), 4)

    def test_idle_with_too_few_seeds_only_updates_picture(self):
        b = fresh_brain()
        add_word(b, "кот")
        c = Cortex(seed=1)
        c.idle(b, ["кот"])
        self.assertEqual(c.last, {"ff": {}, "rec": {}, "created": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
