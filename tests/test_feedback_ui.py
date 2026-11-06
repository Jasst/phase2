# -*- coding: utf-8 -*-
"""Обратная связь: переименование, оценка нейрона, меню и кнопки.
Запуск из корня проекта: python test_feedback_ui.py
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase3 import _isolate, brain_chat as bc

_isolate.isolate()

import pygame  # noqa: E402
from phase3.ui_feedback import FeedbackButtons, NeuronMenu  # noqa: E402


def _brain():
    b = bc.Brain()
    b.train_words("кошка пьёт молоко из миски".split())
    return b


def test_rename_keeps_neuron_and_links():
    b = _brain()
    nid = b.words["молоко"]
    links_before = {k for k in b.links if nid in k}
    assert b.rename_word("молоко", "сливки")
    assert "молоко" not in b.words and b.words["сливки"] == nid
    assert b.neurons[nid].word == "сливки"
    assert {k for k in b.links if nid in k} == links_before


def test_rename_refuses_taken_or_missing_word():
    b = _brain()
    assert not b.rename_word("молоко", "кошка")      # слово уже занято
    assert not b.rename_word("нет_такого", "новое")  # старого слова нет
    assert not b.rename_word("молоко", "")           # пустое имя


def test_feedback_good_strengthens_bad_weakens():
    b = _brain()
    nid = b.words["кошка"]
    k = next(k for k in b.links if nid in k)
    w0 = b.links[k]
    k0 = b.kconf.get("кошка", 0.0)
    b.feedback_neuron(nid, True)
    assert b.links[k] > w0 and b.kconf.get("кошка", 0.0) > k0
    b.feedback_neuron(nid, False)
    b.feedback_neuron(nid, False)
    assert b.links.get(k, 0.0) < w0


def test_feedback_unknown_neuron_is_ignored():
    b = _brain()
    b.feedback_neuron(10**9, True)          # не падает
    b.feedback_neuron(10**9, False)


def test_menu_hides_rename_for_wordless_neuron():
    b = _brain()
    menu = NeuronMenu()
    noise = next(n for n in b.neurons.values() if n.word is None)
    menu.open(noise, (10, 10))
    acts = [a for _, a in menu._items()]
    assert "rename" not in acts and "delete" in acts and "good" in acts
    word_n = b.neurons[b.words["кошка"]]
    menu.open(word_n, (10, 10))
    assert "rename" in [a for _, a in menu._items()]


def test_menu_returns_action_and_closes():
    pygame.init()
    screen = pygame.Surface((800, 600))
    font = pygame.font.Font(None, 18)
    b = _brain()
    menu = NeuronMenu()
    menu.open(b.neurons[b.words["кошка"]], (100, 100))
    menu.draw(screen, font, (800, 600))
    r, act = menu.rects[0]
    assert menu.hit(r.center) == act and not menu.active


def test_buttons_hit_and_disabled_state():
    pygame.init()
    screen = pygame.Surface((800, 600))
    font = pygame.font.Font(None, 18)
    fb = FeedbackButtons()
    fb.draw(screen, font, 20, 500, 384, enabled=False, mouse=(0, 0))
    assert set(fb.rects) == {"good", "bad", "teach"}
    assert fb.hit(fb.rects["good"].center) == "good"
    assert fb.hit(fb.rects["bad"].center) == "bad"
    assert fb.hit(fb.rects["teach"].center) == "teach"
    assert fb.hit((0, 0)) is None
    # кнопки не выходят за панель и не перекрывают друг друга
    rs = sorted(fb.rects.values(), key=lambda r: r.x)
    assert rs[0].left >= 20 and rs[-1].right <= 20 + 384 + 1
    assert all(a.right <= b.left for a, b in zip(rs, rs[1:]))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("OK  ", t.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, "->", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
