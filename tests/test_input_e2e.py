# -*- coding: utf-8 -*-
"""v18, сквозной: поле ввода и буфер в настоящем main() (клавиатура и мышь).

Запуск из корня проекта:  PYTHONPATH=. python tests/test_input_e2e.py
"""
import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import pygame  # noqa: E402
import _isolate  # noqa: E402
import brain_chat as bc  # noqa: E402

CTRL = pygame.KMOD_CTRL
INP = (bc.INPUT_RECT.x + 40, bc.INPUT_RECT.y + 20)
CHAT = (bc.CHAT_RECT.x + 60, bc.CHAT_RECT.y + 40)


class Stop(Exception):
    pass


def run_script(script, last_frame):
    _isolate.isolate()
    bc.SEL.update(panel=None, a=None, b=None, drag=False)   # выделение не переживает тест
    pygame.init()
    pygame.display.set_mode((bc.W, bc.H))
    pygame.event.clear()
    state = {"field": None, "msgs": []}
    mods = {"v": 0}
    frame = {"n": 0}
    pygame.key.get_mods = lambda: mods["v"]
    real_chat = bc.draw_chat
    real_flip = pygame.display.flip

    def rec_chat(screen, thoughts, chat, field, *a, **k):
        state["field"] = (field.text, field.caret, field.sel_range())
        state["msgs"] = [e[1] for e in chat if len(e) > 1]
        return real_chat(screen, thoughts, chat, field, *a, **k)

    def flip():
        frame["n"] += 1
        n = frame["n"]
        mods["v"] = 0
        for ev in script.get(n, []):
            if ev[0] == "text":
                pygame.event.post(pygame.event.Event(pygame.TEXTINPUT, text=ev[1]))
            elif ev[0] == "key":
                mods["v"] = ev[2]
                pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=ev[1], mod=ev[2],
                                                     unicode="", scancode=0))
            elif ev[0] == "mouse":
                pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=ev[2], pos=ev[1]))
            elif ev[0] == "up":
                pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(0, 0)))
        if n == last_frame:
            raise Stop()
        return real_flip()

    bc.draw_chat = rec_chat
    pygame.display.flip = flip
    try:
        bc.main()
    except Stop:
        pass
    finally:
        bc.draw_chat = real_chat
        pygame.display.flip = real_flip
    return state


class InputE2E(unittest.TestCase):
    def test_type_move_select_copy_cut_paste(self):
        script = {
            10: [("text", "котик")],
            12: [("key", pygame.K_LEFT, 0)],
            13: [("text", "-")],                        # коти-к
            15: [("key", pygame.K_a, CTRL)],
            16: [("key", pygame.K_c, CTRL)],            # копия 6 симв.
            18: [("key", pygame.K_END, 0)],
            19: [("key", pygame.K_v, CTRL)],            # вставка в конец
            21: [("key", pygame.K_a, CTRL)],
            22: [("key", pygame.K_x, CTRL)],            # вырезали всё
            24: [("key", pygame.K_v, CTRL)],            # вставили обратно
        }
        st = run_script(script, last_frame=30)
        text, caret, sel = st["field"]
        self.assertEqual(text, "коти-ккоти-к")
        self.assertIsNone(sel)
        msgs = " | ".join(st["msgs"])
        self.assertIn("Скопировано: 6 симв.", msgs)
        self.assertIn("Вырезано: 12 симв.", msgs)

    def test_click_places_caret_and_typing_goes_there(self):
        script = {
            10: [("text", "abcd")],
            12: [("mouse", INP, 1)],
            13: [("up",)],
            15: [("text", "X")],
        }
        st = run_script(script, last_frame=20)
        text, caret, _ = st["field"]
        self.assertEqual(len(text), 5)
        self.assertIn("X", text)
        self.assertNotEqual(text, "abcdX")          # курсор стоит не в конце

    def test_double_click_panel_selects_word_and_copies(self):
        script = {
            10: [("mouse", CHAT, 1)], 11: [("up",)],
            12: [("mouse", CHAT, 1)], 13: [("up",)],
            15: [("key", pygame.K_c, CTRL)],
        }
        st = run_script(script, last_frame=20)
        msgs = " | ".join(st["msgs"])
        self.assertIn("Скопировано: ", msgs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
