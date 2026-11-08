# -*- coding: utf-8 -*-
"""v16: кнопка «активировать» в меню нейрона, отрисовка сцены с кэшем связей, подсказка.

Запуск из корня проекта:  PYTHONPATH=. python tests/test_ui_v16.py
"""
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import _isolate  # noqa: E402
_isolate.isolate()

import random  # noqa: E402
import pygame  # noqa: E402
import brain_chat as bc  # noqa: E402
from ui_feedback import NeuronMenu  # noqa: E402


def fonts():
    f = pygame.font.Font(None, 16)
    return {"ui": f, "word": f, "chat": f, "chatb": f, "mono": f, "title": f}


class MenuActivateTests(unittest.TestCase):
    def test_activate_is_first_and_available_for_wordless_neuron(self):
        m = NeuronMenu()
        m.word = None
        acts = [a for _, a in m._items()]
        self.assertEqual(acts[0], "activate")

    def test_activate_is_listed_for_word_neuron(self):
        m = NeuronMenu()
        m.word = "кот"
        self.assertIn("activate", [a for _, a in m._items()])


class SceneTests(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((bc.W, bc.H))
        random.seed(5)
        self.b = bc.Brain()
        self.b._clear()
        self.b.train_words(["кот", "ест", "рыбу", "дома", "кот", "спит"])
        self.layers = (pygame.Surface((bc.NET_W, bc.H), pygame.SRCALPHA),
                       pygame.Surface((bc.NET_W, bc.H)))
        self.stars = [(10.0, 10.0, 0.0)]
        self.cache = {}
        bc.update_view(self.b, 1.0)

    def test_scene_draws_with_focus_and_hot_neurons(self):
        nid = self.b.words["кот"]
        self.b.neurons[nid].fired_at = self.b.tick          # горит сейчас
        bc.draw_scene(self.screen, self.b, self.layers, fonts(), self.cache,
                      self.stars, 0.0, focus=nid)

    def test_cold_layer_is_cached_between_frames(self):
        bc.draw_scene(self.screen, self.b, self.layers, fonts(), self.cache, self.stars, 0.0)
        first = bc._COLD["surf"]
        bc.draw_scene(self.screen, self.b, self.layers, fonts(), self.cache, self.stars, 0.0)
        self.assertIs(bc._COLD["surf"], first)             # тот же слой, без перерисовки

    def test_hint_draws(self):
        bc.draw_hint(self.screen, fonts())


if __name__ == "__main__":
    unittest.main(verbosity=2)
