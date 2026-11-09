# -*- coding: utf-8 -*-
"""v19: логичный порядок мысли (цепочка по связям), адаптивная длина и число вариантов.

Запуск из корня проекта:  PYTHONPATH=. python tests/test_thought_path.py
"""
import collections
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import _isolate  # noqa: E402
_isolate.isolate()

import mind as mind_mod  # noqa: E402
from brain_chat import Brain  # noqa: E402
from mind import Mind, key  # noqa: E402


def chain_brain():
    b = Brain()
    b._clear()
    ids = {w: b._ids_for([w])[0] for w in ("вопрос", "б1", "в1", "г1", "х1")}
    b.links[key(ids["вопрос"], ids["б1"])] = 1.0
    b.links[key(ids["б1"], ids["в1"])] = 1.0
    b.links[key(ids["в1"], ids["г1"])] = 0.6
    return b


class PathOrderTests(unittest.TestCase):
    def test_path_follows_links_not_input_order(self):
        b = chain_brain()
        m = Mind(b)
        ctx = {"б1": 0.1, "в1": 0.1, "г1": 0.1, "х1": 0.0}
        path = m.order_path(["вопрос"], ["г1", "х1", "в1", "б1"], ctx, target=8)
        self.assertEqual(path, ["б1", "в1", "г1"])          # по связям, без х1

    def test_target_is_a_cap(self):
        b = chain_brain()
        m = Mind(b)
        ctx = {"б1": 0.1, "в1": 0.1, "г1": 0.1}
        self.assertEqual(len(m.order_path(["вопрос"], ["б1", "в1", "г1"], ctx, target=2)), 2)

    def test_unconnected_words_are_dropped(self):
        b = chain_brain()
        m = Mind(b)
        self.assertEqual(m.order_path(["вопрос"], ["х1"], {"х1": 0.0}, target=5), [])


class AdaptiveTests(unittest.TestCase):
    def test_more_candidates_when_support_is_weak(self):
        b = chain_brain()
        m = Mind(b)
        b.support_hist = collections.deque([0.1] * 5, maxlen=20)
        self.assertEqual(m._n_candidates(), mind_mod.CANDIDATES_WEAK)
        b.support_hist = collections.deque([0.8] * 5, maxlen=20)
        self.assertEqual(m._n_candidates(), mind_mod.CANDIDATES)

    def test_default_when_history_short(self):
        b = chain_brain()
        m = Mind(b)
        self.assertEqual(m._n_candidates(), mind_mod.CANDIDATES)


class ChainTests(unittest.TestCase):
    def test_chain_follows_text_bigrams(self):
        b = chain_brain()
        b.bigram = {("вопрос", "б1"): 3, ("б1", "в1"): 3, ("в1", "г1"): 2}
        m = Mind(b)
        ctx = {"б1": 0.1, "в1": 0.1, "г1": 0.1}
        self.assertEqual(m.chain_from(["вопрос"], ctx, target=8), ["б1", "в1", "г1"])

    def test_without_bigrams_chain_falls_back_to_links(self):
        b = chain_brain()
        b.bigram = {}
        m = Mind(b)
        self.assertEqual(m.chain_from(["вопрос"], {}, target=8), ["б1", "в1", "г1"])

    def test_chain_empty_without_seeds(self):
        b = chain_brain()
        m = Mind(b)
        self.assertEqual(m.chain_from([], {}, target=8), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
