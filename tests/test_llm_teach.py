# -*- coding: utf-8 -*-
"""Интеграция: обучение слова через LLM на настоящем Brain, меню нейрона, легенда групп.

Запуск из корня проекта:  python test_llm_teach.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

from _isolate import isolate  # noqa: E402
isolate()

import pygame  # noqa: E402
import brain_chat as bc  # noqa: E402
from ui_feedback import NeuronMenu  # noqa: E402
from nets import NetsManager  # noqa: E402
from nets.llm_tasks import LLMTaskRunner  # noqa: E402
from nets.teach import apply_plan, plan_from_text  # noqa: E402

TEXT = ("Привет — это приветствие. Привет говорят при встрече. "
        "Люди говорят привет друзьям и знакомым.")


class FakeLM:
    def __init__(self, replies):
        self.replies, self._id, self._cur, self._ready = list(replies), 0, None, {}

    def submit(self, prompt, ctx=(), memory=(), note="", remember=True):
        self._id += 1
        self._cur = self._id
        self._ready[self._id] = self.replies.pop(0) if self.replies else ""
        return self._id

    def is_current(self, rid):
        return rid == self._cur

    def poll(self, rid):
        return self._ready.pop(rid, None)


def fonts():
    pygame.init()
    return {"word": pygame.font.SysFont(None, 18)}


class TeachOnRealBrain(unittest.TestCase):
    def setUp(self):
        self.brain = bc.Brain()
        self.brain.train_words(["привет", "друг", "встреча", "знакомые"])
        self.brain.regroup()

    def test_llm_explanation_creates_outgoing_links(self):
        plan = plan_from_text("привет", TEXT, seed=0)
        before = len(self.brain.links)
        added = apply_plan(self.brain, plan)
        self.assertGreater(added, 0)
        self.assertEqual(len(self.brain.links), before + added)
        word_id = self.brain.words["привет"]
        out = [k for k in self.brain.links if word_id in k]
        self.assertTrue(out, "от слова «привет» должны идти связи")

    def test_runner_teaches_word_through_llm(self):
        import tempfile
        import nets.manager as nm
        saved = (nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR)
        tmp = tempfile.mkdtemp()
        nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR = tmp + "/s", tmp + "/l", tmp + "/b"
        try:
            mgr = NetsManager.open(store=tmp + "/s", seed=0)
            runner = LLMTaskRunner()
            out = []

            def done(res):
                added = apply_plan(self.brain, res["plan"]) if res["ok"] else 0
                out.append(added)
                return [("sys", f"связей {added}")]

            runner.add("обучение «привет»", lambda: mgr.teach_gen("привет"), done)
            lm = FakeLM([TEXT, "8"])                 # объяснение, затем оценка дистилляции
            events = []
            for _ in range(30):
                events += runner.tick(lm, busy=False)
                if not runner.active():
                    break
            self.assertEqual(events[0][0], "explain")
            self.assertIn("приветствие", events[0][1])
            self.assertTrue(out and out[0] > 0)
            self.assertIn(("sys", f"связей {out[0]}"), events)
        finally:
            nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR = saved


class NeuronMenuTeach(unittest.TestCase):
    def setUp(self):
        self.brain = bc.Brain()
        self.brain.train_words(["привет", "друг"])

    def test_menu_offers_llm_for_word_neuron_only(self):
        word_n = next(n for n in self.brain.neurons.values() if n.word == "привет")
        m = NeuronMenu()
        m.open(word_n, (10, 10))
        self.assertIn("llm_teach", [a for _, a in m._items()])
        blank = next((n for n in self.brain.neurons.values() if not n.word), None)
        if blank is not None:
            m2 = NeuronMenu()
            m2.open(blank, (10, 10))
            self.assertNotIn("llm_teach", [a for _, a in m2._items()])


class GroupLegend(unittest.TestCase):
    def test_every_group_is_listed_including_singletons(self):
        brain = bc.Brain()
        brain.train_words(["кот", "собака", "небо", "море", "солнце", "луна"])
        brain.regroup()
        rows = bc.group_rows(brain)
        self.assertEqual(len(rows), len({g for g in brain.wgroup.values()}))
        self.assertEqual(sum(n for _, n, _ in rows), len(brain.wgroup))

    def test_legend_draws_without_error(self):
        brain = bc.Brain()
        brain.train_words(["кот", "собака", "небо", "море"])
        brain.regroup()
        surf = pygame.Surface((800, 600))
        bc.draw_group_legend(surf, brain, fonts())
        self.assertIsNotNone(surf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
