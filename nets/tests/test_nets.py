# -*- coding: utf-8 -*-
"""Тесты пакета nets. Запуск из корня проекта:  python -m unittest nets.tests.test_nets -v"""
import copy
import os
import tempfile
import unittest

from nets import Net, Chain, transfer_matrix, dump_matrix, evolve, Sandbox, build_net, tokenize, save_ensemble, load_ensemble
from nets.core import top


def small_net(name, seed=1, temp=1.0):
    n = Net(name, seed=seed)
    n.learn_sequence(["привет", "мир", "как", "дела"], window=3, temp=temp)
    n.learn_sequence(["привет", "друг", "мир"], window=3, temp=temp)
    return n


class TestTemperature(unittest.TestCase):
    def test_transitions_sum_to_one(self):
        n = small_net("a")
        for T in (0.1, 1.0, 3.0):
            p = n.transitions("привет", T)
            self.assertAlmostEqual(sum(p.values()), 1.0, places=9)

    def test_low_temp_is_sharper_high_temp_flatter(self):
        n = Net("x", seed=0)
        n.W["a"] = {"b": 5.0, "c": 1.0}
        sharp = n.transitions("a", 0.1)
        flat = n.transitions("a", 5.0)
        self.assertGreater(sharp["b"], flat["b"])
        self.assertLess(flat["b"] - flat["c"], sharp["b"] - sharp["c"])

    def test_zero_temperature_creates_no_new_edges(self):
        n = Net("x", seed=0)
        n.learn_sequence(["а", "б", "в"], window=2, temp=0.0)
        self.assertEqual(n.n_edges(), 0)
        n.learn_sequence(["а", "б", "в"], window=2, temp=5.0)
        self.assertGreater(n.n_edges(), 0)


class TestSweep(unittest.TestCase):
    def test_mass_conserved(self):
        n = small_net("a")
        act = {"привет": 1.0}
        for _ in range(3):
            n.sweep(act, temp=1.0)
        self.assertAlmostEqual(sum(act.values()), 1.0, places=3)

    def test_random_order_changes_result(self):
        outs = set()
        for seed in range(8):
            n = small_net("a", seed=seed)
            act = {"привет": 1.0, "мир": 0.7, "как": 0.4}
            n.sweep(act, temp=1.0, damp=0.7)
            outs.add(tuple(sorted((k, round(v, 4)) for k, v in act.items())))
        self.assertGreater(len(outs), 1, "порядок узлов должен влиять на результат")


class TestChain(unittest.TestCase):
    def setUp(self):
        self.nets = [small_net("n1", seed=1), small_net("n2", seed=2), small_net("n3", seed=3)]

    def test_runs_and_traces_every_net(self):
        ch = Chain(self.nets, temp=1.0)
        res = ch.run(["привет"], max_rounds=4, min_rounds=1)
        self.assertGreaterEqual(res["rounds"], 1)
        self.assertLessEqual(res["rounds"], 4)
        self.assertEqual(len(res["trace"]), res["rounds"] * len(self.nets))
        self.assertTrue(res["thought"])

    def test_deterministic_with_fixed_seeds(self):
        a = Chain([copy.deepcopy(n) for n in self.nets], temp=1.0).run(["привет"])
        b = Chain([copy.deepcopy(n) for n in self.nets], temp=1.0).run(["привет"])
        self.assertEqual(a["thought"], b["thought"])

    def test_empty_input(self):
        res = Chain(self.nets).run([])
        self.assertEqual(res["thought"], [])
        self.assertEqual(res["rounds"], 0)

    def test_time_budget_stops(self):
        res = Chain(self.nets, temp=1.0).run(["привет"], max_rounds=50, min_rounds=50,
                                             time_budget=0.0)
        self.assertLessEqual(res["rounds"], 2)


class TestTransfer(unittest.TestCase):
    def test_rows_bounded_by_one(self):
        a, b = small_net("a", 1), small_net("b", 2)
        M = transfer_matrix(a, b, temp=1.0)
        self.assertTrue(M)
        for i, row in M.items():
            self.assertLessEqual(sum(row.values()), 1.0 + 1e-9)

    def test_no_shared_nodes_gives_empty_matrix(self):
        a = Net("a", 0)
        a.learn_sequence(["один", "два"], window=1)
        b = Net("b", 0)
        b.learn_sequence(["три", "четыре"], window=1)
        self.assertEqual(transfer_matrix(a, b), {})

    def test_dump_writes_files(self):
        a, b = small_net("a", 1), small_net("b", 2)
        with tempfile.TemporaryDirectory() as d:
            j, t = dump_matrix(transfer_matrix(a, b), "a", "b", 1.0, out_dir=d)
            self.assertTrue(os.path.getsize(j) > 0)
            with open(t, encoding="utf-8") as fh:
                self.assertIn("Матрица перерасчёта", fh.read())


class TestMutationAndDistill(unittest.TestCase):
    def test_mutation_does_not_touch_parent(self):
        parent = small_net("p")
        before = copy.deepcopy(parent.W)
        parent.mutate(rate=0.5)
        self.assertEqual({k: dict(v) for k, v in parent.W.items()},
                         {k: dict(v) for k, v in before.items()})

    def test_distill_adds_teacher_knowledge(self):
        teacher = small_net("t", 1)
        student = Net("s", 2)
        student.distill_from(teacher, alpha=0.5, temp=2.0)
        self.assertTrue(student.n_edges() > 0)
        self.assertGreater(student.W["привет"].get("мир", 0.0), 0.0)


class TestSandboxAndStore(unittest.TestCase):
    def test_sandbox_does_not_change_live_without_commit(self):
        live = [small_net("a", 1), small_net("b", 2)]
        before = [n.to_dict() for n in live]
        sb = Sandbox(live, root=tempfile.mkdtemp())
        train = [(["привет"], ["мир"])]
        val = [(["привет"], ["мир", "друг"])]
        report = sb.adapt(train, val, margin=10.0)   # заведомо недостижимый порог
        self.assertFalse(report["commit"])
        self.assertEqual([n.to_dict() for n in live], before)
        self.assertTrue(os.path.exists(os.path.join(sb.root, "last_adapt.json")))

    def test_store_roundtrip(self):
        nets = [small_net("a", 1), small_net("b", 2)]
        with tempfile.TemporaryDirectory() as d:
            save_ensemble(nets, root=d)
            loaded = load_ensemble(root=d)
        self.assertEqual(sorted(n.name for n in loaded), ["a", "b"])
        self.assertEqual(loaded[0].to_dict()["W"], [n for n in nets if n.name == "a"][0].to_dict()["W"])

    def test_evolve_returns_ensemble(self):
        ens = [small_net("a", 1), small_net("b", 2)]
        ex = [(["привет"], ["мир"])]
        best, hist, clusters = evolve(ens, ex, generations=2, pop=2, seed=0)
        self.assertEqual(len(best), 2)
        self.assertEqual(len(hist), 3)
        self.assertTrue(clusters)


class TestBuild(unittest.TestCase):
    def test_tokenize_and_build(self):
        self.assertEqual(tokenize("Привет, мир! Я — тест."), ["привет", "мир", "тест"])
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("Привет мир. Привет друг.\n")
        try:
            n = build_net("tmp", [f.name])
            self.assertIn("мир", n.W["привет"])
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
