# -*- coding: utf-8 -*-
"""v18: поле ввода (курсор, выделение, правка), окно прокрутки и буфер обмена.

Запуск из корня проекта:  PYTHONPATH=. python tests/test_textedit.py
"""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import clipboard  # noqa: E402
from textedit import Field, index_at, window  # noqa: E402


class FieldTests(unittest.TestCase):
    def test_insert_in_middle(self):
        f = Field("котик")
        f.move(2)
        f.insert("-")
        self.assertEqual((f.text, f.caret), ("ко-тик", 3))

    def test_shift_left_selects_and_typing_replaces(self):
        f = Field("привет")
        f.left(extend=True)
        f.left(extend=True)
        self.assertEqual(f.selected(), "ет")
        f.insert("о")
        self.assertEqual(f.text, "приво")

    def test_plain_arrow_collapses_selection(self):
        f = Field("abcdef")
        f.move(1)
        f.move(4, extend=True)
        f.left()                       # без Shift — к левому краю выделения
        self.assertEqual((f.caret, f.anchor), (1, None))

    def test_backspace_and_delete_respect_selection(self):
        f = Field("abcdef")
        f.select_all()
        f.backspace()
        self.assertEqual(f.text, "")
        g = Field("abc")
        g.home()
        g.delete()
        self.assertEqual(g.text, "bc")

    def test_cut_returns_selection_and_removes_it(self):
        f = Field("один два")
        f.move(0)
        f.move(4, extend=True)
        self.assertEqual(f.cut(), "один")
        self.assertEqual(f.text, " два")

    def test_select_all_and_empty_selection(self):
        f = Field("xyz")
        f.select_all()
        self.assertEqual(f.selected(), "xyz")
        f.move(3)
        self.assertIsNone(f.sel_range())

    def test_word_selection_handles_cyrillic_and_underscore(self):
        f = Field("скажи нейрон_1 сейчас")
        f.select_word_at(9)            # внутри «нейрон_1»
        self.assertEqual(f.selected(), "нейрон_1")

    def test_caret_clamped(self):
        f = Field("ab")
        f.move(99)
        self.assertEqual(f.caret, 2)
        f.move(-5)
        self.assertEqual(f.caret, 0)


class WindowTests(unittest.TestCase):
    def width(self, s):                # 10 px на символ
        return 10 * len(s)

    def test_window_keeps_caret_visible(self):
        s = "а" * 50
        start, end = window(self.width, s, caret=50, avail=100)
        self.assertLessEqual(self.width(s[start:50]), 100)
        self.assertEqual(end, 50)

    def test_index_at_maps_x_to_caret(self):
        s = "abcdef"
        self.assertEqual(index_at(self.width, s, 0, 6, x=25), 2)


class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self._old = clipboard._commands
        clipboard._commands = lambda: (None, None)     # системного буфера нет

    def tearDown(self):
        clipboard._commands = self._old

    def test_internal_fallback_roundtrip(self):
        self.assertEqual(clipboard.copy("тест\nвторой"), "internal")
        self.assertEqual(clipboard.paste(), "тест\nвторой")


if __name__ == "__main__":
    unittest.main(verbosity=2)
