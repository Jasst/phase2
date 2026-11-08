# -*- coding: utf-8 -*-
"""Тесты LLM-части: судья, учитель, слои, драйвер задач (без реальной модели)."""
import unittest

from nets import Net
from nets.judge import judge_prompt, parse_score
from nets.llm_tasks import LLMTaskRunner, run_sync
from nets.cluster import score_ensemble, score_ensemble_gen
from nets.teach import teacher_from_text, build_layers, plan_from_text, apply_plan, explain_prompt

TEXT = ("Нейрон — клетка мозга. Нейрон передаёт сигналы другим нейронам. "
        "Сигналы проходят через синапсы между нейронами.")


class FakeBrain:
    """Минимальный двойник Brain: links и методы, которые использует apply_plan."""
    def __init__(self):
        self.links = {}
        self.trained = []

    def train_words(self, words, weight=1.0):
        self.trained.append(list(words))
        for w in words:
            self.links.setdefault(("w", w), weight)

    def associate(self, q, a, weight=1.0):
        for x in q:
            for y in a:
                if x != y:
                    self.links[(x, y)] = weight


class FakeLM:
    """Имитирует LMWorker: submit/poll/is_current. Ответы берутся из очереди."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self._id = 0
        self._current = None
        self._ready = {}

    def submit(self, prompt, ctx=(), memory=(), note="", remember=True):
        self._id += 1
        self.sent.append((prompt, remember))
        self._current = self._id
        self._ready[self._id] = self.replies.pop(0) if self.replies else ""
        return self._id

    def is_current(self, rid):
        return rid == self._current

    def poll(self, rid):
        return self._ready.pop(rid, None)


class TestJudge(unittest.TestCase):
    def test_parse_score_variants(self):
        self.assertEqual(parse_score("8"), 0.8)
        self.assertEqual(parse_score("Оценка: 7,5"), 0.75)
        self.assertEqual(parse_score("10"), 1.0)
        self.assertIsNone(parse_score("много"))
        self.assertIsNone(parse_score(""))
        self.assertIsNone(parse_score("42"))        # вне шкалы

    def test_prompt_contains_all_parts(self):
        p = judge_prompt("что такое сеть", "нейронная сеть", "связи слова")
        for part in ("что такое сеть", "нейронная сеть", "связи слова", "0 до 10"):
            self.assertIn(part, p)

    def test_score_uses_llm_when_available(self):
        net = Net("n", seed=0)
        net.learn_sequence(["сеть", "связи", "слова"], window=3, temp=5.0)
        ex = [(["сеть"], ["связи"])]
        s = run_sync(score_ensemble_gen([net], ex, seed=0), lambda p: "9")
        self.assertAlmostEqual(s, 0.9)

    def test_score_falls_back_to_recall_without_llm(self):
        net = Net("n", seed=0)
        net.learn_sequence(["сеть", "связи", "слова"], window=3, temp=5.0)
        ex = [(["сеть"], ["связи"])]
        s_sync = score_ensemble([net], ex, seed=0)
        s_none = run_sync(score_ensemble_gen([net], ex, seed=0), lambda p: None)
        self.assertEqual(s_sync, s_none)


class TestTeacherAndLayers(unittest.TestCase):
    def test_word_links_out_to_every_answer_word(self):
        t = teacher_from_text("нейрон", TEXT, seed=0)
        self.assertIn("клетка", t.W["нейрон"])
        self.assertIn("сигналы", t.W["нейрон"])

    def test_layers_are_recurrent_and_disjoint(self):
        t = teacher_from_text("нейрон", TEXT, seed=0)
        layers = build_layers(t, "нейрон", seed=0, layers=3)
        self.assertTrue(layers, "первый слой должен существовать")
        seen = {"нейрон"}
        for layer in layers:
            self.assertFalse(set(layer) & seen, "слои не повторяют предыдущие слова")
            seen.update(layer)
        self.assertLessEqual(len(layers), 3)

    def test_layers_deterministic(self):
        a = build_layers(teacher_from_text("нейрон", TEXT, seed=1), "нейрон", seed=1)
        b = build_layers(teacher_from_text("нейрон", TEXT, seed=1), "нейрон", seed=1)
        self.assertEqual(a, b)

    def test_apply_plan_creates_links_from_word_and_layers(self):
        plan = plan_from_text("нейрон", TEXT, seed=0)
        fb = FakeBrain()
        added = apply_plan(fb, plan)
        self.assertGreater(added, 0)
        self.assertIn(("нейрон", plan["layers"][0][0]), fb.links)

    def test_explain_prompt_names_word(self):
        self.assertIn("привет", explain_prompt("привет"))


class TestRunner(unittest.TestCase):
    def test_explain_then_judge_then_finish(self):
        def task():
            text = yield ("explain", "объясни")
            s = yield ("judge", "оцени")
            return {"text": text, "judge": s}

        results = []
        runner = LLMTaskRunner()
        runner.add("тест", task, lambda r: (results.append(r), [("sys", "готово")])[1])
        lm = FakeLM(["ОБЪЯСНЕНИЕ", "7"])
        events = []
        for _ in range(10):
            events += runner.tick(lm, busy=False)
            if not runner.active():
                break
        self.assertFalse(runner.active())
        self.assertIn(("explain", "ОБЪЯСНЕНИЕ"), events)       # объяснение показано
        self.assertIn(("sys", "готово"), events)
        self.assertEqual(results[0], {"text": "ОБЪЯСНЕНИЕ", "judge": "7"})
        self.assertFalse(lm.sent[0][1], "служебные запросы не пишутся в память LM")

    def test_waits_while_lm_busy(self):
        def task():
            yield ("explain", "x")
            return "ok"
        runner = LLMTaskRunner()
        runner.add("t", task)
        lm = FakeLM(["ans"])
        runner.tick(lm, busy=True)
        self.assertEqual(lm.sent, [], "при занятой LM задача не отправляется")
        runner.tick(lm, busy=False)
        self.assertEqual(len(lm.sent), 1)

    def test_resubmits_when_superseded(self):
        def task():
            r = yield ("judge", "p")
            return r
        runner = LLMTaskRunner()
        runner.add("t", task)
        lm = FakeLM([])
        runner.tick(lm, busy=False)                 # отправлен запрос id=1
        lm._current = 99                            # имитация: ответ в чат перебил
        runner.tick(lm, busy=False)                 # снимаем rid
        self.assertEqual(runner.resubmits, 1)

    def test_no_lm_means_no_progress(self):
        def task():
            yield ("judge", "p")
            return 1
        runner = LLMTaskRunner()
        runner.add("t", task)
        self.assertEqual(runner.tick(None, busy=False), [])
        self.assertTrue(runner.active())

    def test_teach_gen_end_to_end_with_fake_llm(self):
        from nets import NetsManager
        import tempfile
        import nets.manager as nm
        saved = (nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR)
        tmp = tempfile.mkdtemp()
        nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR = tmp + "/s", tmp + "/l", tmp + "/b"
        try:
            mgr = NetsManager.open(store=tmp + "/s", seed=0)
            runner = LLMTaskRunner()
            done = []
            runner.add("обучение «нейрон»", lambda: mgr.teach_gen("нейрон"),
                       lambda res: (done.append(res), [])[1])
            lm = FakeLM([TEXT])                     # единственный ответ — объяснение
            before = [n.n_edges() for n in mgr.ensemble]
            events = []
            for _ in range(20):
                events += runner.tick(lm, busy=False)
                if not runner.active():
                    break
            self.assertTrue(done and done[0]["ok"])
            self.assertIn("нейрон", done[0]["text"])
            self.assertEqual(events[0][0], "explain")
            self.assertGreaterEqual(sum(n.n_edges() for n in mgr.ensemble), sum(before))
        finally:
            nm.STORE_DIR, nm.LOG_DIR, nm.SANDBOX_DIR = saved


if __name__ == "__main__":
    unittest.main()
