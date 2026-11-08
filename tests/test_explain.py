# -*- coding: utf-8 -*-
"""v17: человеческие подписи мыслей, ответов и статуса (explain.py).

Запуск из корня проекта:  PYTHONPATH=. python tests/test_explain.py
"""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import explain  # noqa: E402


class FakeBrain:
    def __init__(self, groups, seen):
        self.wgroup = groups
        self.word_seen = seen


class FakeThought:
    def __init__(self, words, group, score):
        self.words, self.group, self.score = words, group, score


class ConfidenceTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(explain.confidence_word(0.05), "низкая")
        self.assertEqual(explain.confidence_word(0.2), "средняя")
        self.assertEqual(explain.confidence_word(0.9), "высокая")
        self.assertEqual(explain.confidence_word(None), "неизвестна")

    def test_answer_label_has_no_service_terms(self):
        label = explain.answer_label(0.21)
        self.assertIn("уверенность средняя", label)
        self.assertNotIn("опора", label)
        self.assertNotIn("группа", label)


class TopicTests(unittest.TestCase):
    def test_group_is_named_by_most_seen_word(self):
        b = FakeBrain({"кот": 5, "ест": 5, "рыба": 5}, {"кот": 2, "ест": 9, "рыба": 4})
        self.assertEqual(explain.topic_name(b, 5), "тема «ест»")

    def test_whole_net_and_chain(self):
        b = FakeBrain({}, {})
        self.assertEqual(explain.topic_name(b, None), "вся сеть")
        self.assertEqual(explain.topic_name(b, "chain"), "цепочка сетей")


class ThoughtTests(unittest.TestCase):
    def test_thought_reads_as_topic_confidence_and_path(self):
        b = FakeBrain({"кот": 1, "рыба": 1}, {"кот": 3, "рыба": 1})
        text = explain.describe_thought(FakeThought(["кот", "рыба"], 1, 0.3), b)
        self.assertEqual(text, "Мысль · тема «кот» · уверенность средняя: кот → рыба")

    def test_stream_is_marked_as_not_an_answer(self):
        self.assertTrue(explain.describe_stream("слово слово").startswith("Ход мыслей"))


class StatusTests(unittest.TestCase):
    def test_priority_answering_then_waiting_then_learning(self):
        self.assertEqual(explain.status_text(True, True, True), "отвечаю…")
        self.assertEqual(explain.status_text(False, True, True), "думаю над ответом…")
        self.assertEqual(explain.status_text(False, False, True), "учусь…")
        self.assertIn("жду вопрос", explain.status_text(False, False, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
