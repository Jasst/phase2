# -*- coding: utf-8 -*-
"""Интеграция: цепочка сетей как кандидат мысли в Answerer.

Запуск из корня проекта:  python test_nets_integration.py
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from _isolate import isolate  # noqa: E402
isolate()                    # все файлы приложения и сетей — во временной папке

from mind import Mind        # noqa: E402
from reasoning import Answerer, CHAIN, _where  # noqa: E402
from nets import NetsManager  # noqa: E402
import brain_chat as bc      # noqa: E402


class ChainCandidateTest(unittest.TestCase):
    def setUp(self):
        self.brain = bc.Brain()
        self.brain.train_words(["нейрон", "клетка", "мозга", "сигналы", "передаёт"])
        self.brain.associate(["нейрон"], ["клетка", "мозга"])
        self.mind = Mind(self.brain)
        self.mgr = NetsManager.open(store=tempfile.mkdtemp(), seed=0)

    def test_without_nets_behaviour_unchanged(self):
        ans = Answerer(self.brain, self.mind)           # nets=None — старый путь
        by_mode, seeds = ans.think("что такое нейрон", 4)
        for t in by_mode.values():
            self.assertNotEqual(t.group, CHAIN)

    def test_with_nets_chain_is_considered(self):
        ans = Answerer(self.brain, self.mind, self.mgr)
        by_mode, seeds = ans.think("что такое нейрон", 4)
        self.assertTrue(seeds)
        self.assertIsNotNone(self.mgr.last, "цепочка должна была запуститься")
        whole = by_mode.get("whole")
        if whole is not None and whole.group == CHAIN:
            self.assertIn("цепочка сетей", whole.describe())

    def test_where_helper(self):
        self.assertEqual(_where(None), "вся сеть")
        self.assertEqual(_where(CHAIN), "цепочка сетей")
        self.assertEqual(_where(2), "группа 2")

    def test_begin_and_final_do_not_crash_with_chain(self):
        ans = Answerer(self.brain, self.mind, self.mgr)
        st = ans.begin("что такое нейрон", 4, policy=None)
        self.assertIn("q", st)
        self.assertIsNotNone(ans.final(st) or ans.unknown_reply(st))

    def test_feedback_loop_reaches_sandbox_and_status(self):
        """Цикл: ответ → награда (/good) → очередь → песочница → статус."""
        mgr = NetsManager.open(store=tempfile.mkdtemp(), seed=0, adapt_every=2)
        mgr.think("что такое нейрон")
        mgr.feedback("что такое нейрон", "нейрон клетка мозга")
        mgr.feedback("что такое сигнал", "нейрон передаёт сигналы")
        self.assertEqual(mgr.pending, [], "после порога очередь очищается")
        self.assertEqual(len(mgr.reports), 1)
        lines = mgr.status()
        self.assertTrue(any(l.startswith("адаптация:") for l in lines), lines)
        self.assertTrue(any(l.startswith("последний ответ") for l in lines), lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
