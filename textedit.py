# -*- coding: utf-8 -*-
"""Поле ввода: текст, курсор, выделение, правка. Без pygame, легко проверять тестами.

Курсор — индекс между символами (0..len). Выделение — от anchor до caret.
Shift + стрелки/Home/End расширяют выделение; без Shift выделение сворачивается.
"""


def _is_word_char(ch):
    return ch.isalnum() or ch == "_"


class Field:
    def __init__(self, text=""):
        self.text = ""
        self.caret = 0
        self.anchor = None
        self.set_text(text)

    # ---------- состояние ----------

    def set_text(self, text):
        self.text = text
        self.caret = len(text)
        self.anchor = None

    def sel_range(self):
        """(lo, hi) выделенного диапазона или None."""
        if self.anchor is None or self.anchor == self.caret:
            return None
        return (min(self.anchor, self.caret), max(self.anchor, self.caret))

    def selected(self):
        r = self.sel_range()
        return self.text[r[0]:r[1]] if r else ""

    # ---------- правка ----------

    def _delete_selection(self):
        r = self.sel_range()
        if not r:
            return False
        lo, hi = r
        self.text = self.text[:lo] + self.text[hi:]
        self.caret = lo
        self.anchor = None
        return True

    def insert(self, s):
        if not s:
            return
        self._delete_selection()
        self.text = self.text[:self.caret] + s + self.text[self.caret:]
        self.caret += len(s)
        self.anchor = None

    def backspace(self):
        if self._delete_selection():
            return
        if self.caret > 0:
            self.text = self.text[:self.caret - 1] + self.text[self.caret:]
            self.caret -= 1

    def delete(self):
        if self._delete_selection():
            return
        if self.caret < len(self.text):
            self.text = self.text[:self.caret] + self.text[self.caret + 1:]

    def cut(self):
        s = self.selected()
        if s:
            self._delete_selection()
        return s

    # ---------- курсор и выделение ----------

    def move(self, pos, extend=False):
        pos = max(0, min(len(self.text), pos))
        if extend:
            if self.anchor is None:
                self.anchor = self.caret
            self.caret = pos
            if self.anchor == self.caret:
                self.anchor = None
        else:
            self.anchor = None
            self.caret = pos

    def left(self, extend=False):
        r = self.sel_range()
        if r and not extend:
            self.move(r[0])
        else:
            self.move(self.caret - 1, extend)

    def right(self, extend=False):
        r = self.sel_range()
        if r and not extend:
            self.move(r[1])
        else:
            self.move(self.caret + 1, extend)

    def home(self, extend=False):
        self.move(0, extend)

    def end(self, extend=False):
        self.move(len(self.text), extend)

    def select_all(self):
        self.anchor = 0
        self.caret = len(self.text)
        if self.anchor == self.caret:
            self.anchor = None

    def word_span(self, i):
        """Границы слова вокруг позиции i (для двойного щелчка)."""
        t = self.text
        i = max(0, min(len(t), i))
        lo, hi = i, i
        while lo > 0 and _is_word_char(t[lo - 1]):
            lo -= 1
        while hi < len(t) and _is_word_char(t[hi]):
            hi += 1
        return lo, hi

    def select_word_at(self, i):
        lo, hi = self.word_span(i)
        self.anchor, self.caret = lo, hi
        if lo == hi:
            self.anchor = None


def word_span(text, i):
    """Границы слова в произвольной строке (для панелей с текстом)."""
    f = Field(text)
    return f.word_span(i)


def window(width, shown, caret, avail, start=0):
    """Видимое окно строки: (start, end) так, чтобы курсор был виден.

    width(s) — ширина строки в пикселях. avail — доступная ширина.
    """
    if caret < start:
        start = caret
    while start < caret and width(shown[start:caret]) > avail:
        start += 1
    end = start
    while end < len(shown) and width(shown[start:end + 1]) <= avail:
        end += 1
    return start, end


def index_at(width, shown, start, end, x):
    """Индекс символа (курсора) у горизонтальной координаты x внутри окна."""
    best, best_d = start, None
    for i in range(start, end + 1):
        d = abs(width(shown[start:i]) - x)
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best
