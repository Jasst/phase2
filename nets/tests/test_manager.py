# -*- coding: utf-8 -*-
"""Тесты менеджера ансамбля: хранение, опыт, песочница, ограничение графа, логи."""
import json
import os
import tempfile
import unittest

import nets.manager as nm
from nets import NetsManager, Net, tokenize


class IsolatedManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nets_test_")
        self._saved = (nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR)
        nm.STORE_DIR = os.path.join(self.tmp, "store")
        nm.LOG_DIR = os.path.join(self.tmp, "logs")
        nm.SANDBOX_DIR = os.path.join(self.tmp, "sandbox")

    def tearDown(self):
        nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR = self._saved

    def _mgr(self, **kw):
        return NetsManager.open(**kw)


class TestOpenAndThink(IsolatedManagerTest):
    def test_first_run_builds_one_net_per_project_text(self):
        mgr = self._mgr(seed=0)
        names = sorted(n.name for n in mgr.ensemble)
        self.assertEqual(names, ["corpus", "daily", "nature", "tech"])
        self.assertTrue(all(n.n_edges() > 0 for n in mgr.ensemble))

    def test_think_returns_result_and_writes_logs(self):
        mgr = self._mgr(seed=0)
        res = mgr.think("что такое температура сети")
        self.assertIsNotNone(res)
        self.assertIn(res["reason"], ("стабилизация", "бюджет раундов", "max_rounds", "лимит времени"))
        self.assertTrue(os.path.exists(os.path.join(nm.LOG_DIR, "last_chain.txt")))
        with open(os.path.join(nm.LOG_DIR, "chain_history.jsonl"), encoding="utf-8") as f:
            row = json.loads(f.readline())
        self.assertEqual(row["q"], "что такое температура сети")

    def test_think_empty_question(self):
        self.assertIsNone(self._mgr().think("   "))

    def test_chain_is_deterministic(self):
        a = self._mgr(seed=3).think("что такое сеть")["thought"]
        b = self._mgr(seed=3).think("что такое сеть")["thought"]
        self.assertEqual(a, b)


class TestFeedbackAndAdapt(IsolatedManagerTest):
    def test_feedback_below_threshold_only_queues(self):
        mgr = self._mgr(seed=0, adapt_every=4)
        before = [n.n_edges() for n in mgr.ensemble]
        self.assertTrue(mgr.feedback("что такое связь", "связи усиливаются"))
        self.assertEqual(len(mgr.pending), 1)
        self.assertEqual([n.n_edges() for n in mgr.ensemble], before, "граф не меняется до адаптации")
        self.assertTrue(os.path.exists(os.path.join(nm.STORE_DIR, "feedback.jsonl")))

    def test_feedback_rejects_empty_and_zero_weight(self):
        mgr = self._mgr(seed=0)
        self.assertFalse(mgr.feedback("", "ответ"))
        self.assertFalse(mgr.feedback("вопрос", "ответ", weight=0))
        self.assertEqual(mgr.pending, [])

    def test_threshold_triggers_sandbox_and_clears_queue(self):
        mgr = self._mgr(seed=0, adapt_every=3)
        mgr.feedback("что такое сеть", "нейронная сеть связи слова")
        mgr.feedback("что такое температура", "температура делает ответы разнообразнее")
        mgr.feedback("как учится сеть", "сеть учится на примерах")
        self.assertEqual(mgr.pending, [], "очередь очищается после адаптации")
        self.assertEqual(len(mgr.reports), 1)
        r = mgr.reports[0]
        self.assertIn("commit", r)
        self.assertTrue(os.path.exists(os.path.join(nm.SANDBOX_DIR, "last_adapt.json")))

    def test_rejected_adaptation_keeps_live_graph(self):
        mgr = self._mgr(seed=0, adapt_every=99)
        mgr.feedback("что такое сеть", "нейронная сеть")
        mgr.feedback("что такое связь", "связи слова")
        before = [n.to_dict() for n in mgr.ensemble]
        report = mgr.adapt(margin=10.0)     # заведомо недостижимый порог
        self.assertFalse(report["commit"])
        self.assertEqual([n.to_dict() for n in mgr.ensemble], before)

    def test_prune_bounds_out_degree(self):
        n = Net("x", seed=0)
        for j in range(200):
            n.W["a"][f"w{j}"] = float(j + 1)
        removed = n.prune(max_out=60)
        self.assertEqual(len(n.W["a"]), 60)
        self.assertEqual(removed, 140)
        self.assertIn("w199", n.W["a"])      # сильные связи остаются
        self.assertNotIn("w0", n.W["a"])     # слабые уходят

    def test_save_and_reload_keeps_pending(self):
        mgr = self._mgr(seed=0, adapt_every=10)
        mgr.feedback("что такое сеть", "нейронная сеть")
        mgr.save()
        again = self._mgr(seed=0, adapt_every=10)
        self.assertEqual(len(again.pending), 1)
        self.assertEqual(sorted(n.name for n in again.ensemble), ["corpus", "daily", "nature", "tech"])

    def test_status_lines(self):
        mgr = self._mgr(seed=0)
        mgr.think("что такое сеть")
        lines = mgr.status()
        self.assertTrue(lines[0].startswith("сети:"))
        self.assertTrue(any(l.startswith("последний ответ") for l in lines))


if __name__ == "__main__":
    unittest.main()
