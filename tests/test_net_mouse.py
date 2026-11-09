# -*- coding: utf-8 -*-
"""v20: управление сетью мышью — масштаб у курсора, сдвиг, выбор нейрона, сброс вида.

Запуск из корня проекта:  PYTHONPATH=. python tests/test_net_mouse.py
"""
import glob
import os
import random
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import _isolate  # noqa: E402
import brain_chat as bc  # noqa: E402
_ORIG_SAVE = bc.save_brain          # до изоляции: изоляция подменяет save_brain заглушкой
_isolate.isolate()

import pygame  # noqa: E402


def screen_x(world_x):
    """Экранная координата мировой точки по текущему пользовательскому виду."""
    v = bc.VIEW_USER
    return bc.NET_W / 2 + (world_x - v["wcx"]) * v["base"] * v["zoom"]


def screen_y(world_y):
    v = bc.VIEW_USER
    return bc.H / 2 + (world_y - v["wcy"]) * v["base"] * v["zoom"]


class ZoomPanMathTests(unittest.TestCase):
    def setUp(self):
        bc.reset_view()
        bc.VIEW_USER.update(base=40.0, auto=(10.0, 10.0))

    def test_wheel_keeps_point_under_cursor(self):
        bc._ensure_user_view()
        mx, my = 300.0, 220.0
        wx = bc.VIEW_USER["wcx"] + (mx - bc.NET_W / 2) / (bc.VIEW_USER["base"] * bc.VIEW_USER["zoom"])
        wy = bc.VIEW_USER["wcy"] + (my - bc.H / 2) / (bc.VIEW_USER["base"] * bc.VIEW_USER["zoom"])
        bc.zoom_at((mx, my), 2)
        self.assertAlmostEqual(screen_x(wx), mx, places=6)
        self.assertAlmostEqual(screen_y(wy), my, places=6)
        self.assertGreater(bc.VIEW_USER["zoom"], 1.0)

    def test_zoom_is_clamped(self):
        bc.zoom_at((100.0, 100.0), 500)
        self.assertLessEqual(bc.VIEW_USER["zoom"], bc.ZOOM_MAX)
        bc.zoom_at((100.0, 100.0), -500)
        self.assertGreaterEqual(bc.VIEW_USER["zoom"], bc.ZOOM_MIN)

    def test_pan_moves_world_opposite_to_drag(self):
        bc._ensure_user_view()
        before = bc.VIEW_USER["wcx"]
        bc.pan_by(40.0, 0.0)
        self.assertLess(bc.VIEW_USER["wcx"], before)      # тянем вправо — центр уходит влево

    def test_reset_returns_to_auto(self):
        bc.zoom_at((10.0, 10.0), 3)
        bc.reset_view()
        self.assertFalse(bc.VIEW_USER["on"])


class SelectionTests(unittest.TestCase):
    def test_select_describes_neuron_and_handles_empty(self):
        random.seed(1)
        b = bc.Brain()
        b._clear()
        b.train_words(["кот", "ест", "рыбу"])
        nid = b.words["кот"]
        msg = bc.select_neuron(b, nid)
        self.assertIn("«кот»", msg)
        self.assertEqual(bc.NET_SEL["nid"], nid)
        self.assertIsNone(bc.select_neuron(b, None))
        self.assertIsNone(bc.NET_SEL["nid"])


class NetE2E(unittest.TestCase):
    """Сквозной прогон настоящего main(): колесо, перетаскивание, клик по нейрону."""

    def run_script(self, script, last_frame):
        _isolate.isolate()
        pygame.init()
        pygame.display.set_mode((bc.W, bc.H))
        pygame.event.clear()
        bc.reset_view()
        bc.NET_SEL["nid"] = None
        state = {"frame": 0, "pos": (0, 0), "zoom": None, "on": None, "wcx": None, "sel": None}
        mouse_pos = {"p": (0, 0)}
        real_get = pygame.mouse.get_pos
        real_flip = pygame.display.flip
        real_chat = bc.draw_chat
        pygame.mouse.get_pos = lambda: mouse_pos["p"]
        class Stop(Exception):
            pass

        real_scene = bc.draw_scene

        def scene(screen, brain, *a, **k):
            state["brain"] = brain           # мозг main(), по нему считаем позиции нейронов
            return real_scene(screen, brain, *a, **k)
        bc.draw_scene = scene

        def flip():
            state["frame"] += 1
            n = state["frame"]
            for ev in script(n, state, mouse_pos):
                pygame.event.post(ev)
            state["zoom"] = bc.VIEW_USER["zoom"]
            state["on"] = bc.VIEW_USER["on"]
            state["wcx"] = bc.VIEW_USER["wcx"]
            state["sel"] = bc.NET_SEL["nid"]
            if n >= last_frame:
                raise Stop()
            return real_flip()

        pygame.display.flip = flip
        try:
            bc.main()
        except Stop:
            pass
        finally:
            pygame.display.flip = real_flip
            pygame.mouse.get_pos = real_get
            bc.draw_chat = real_chat
            bc.draw_scene = real_scene
        return state

    def test_wheel_zooms_drag_pans_click_selects(self):
        P = (300, 300)

        def script(n, st, mp):
            evs = []
            if n == 5:
                mp["p"] = P
                evs.append(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=2, flipped=False))
            if n == 10:
                mp["p"] = P
                evs.append(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=P))
            if n == 11:
                mp["p"] = (340, 300)
                evs.append(pygame.event.Event(pygame.MOUSEMOTION, pos=(340, 300), rel=(40, 0),
                                              buttons=(1, 0, 0)))
            if n == 12:
                evs.append(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(340, 300)))
            return evs

        st = self.run_script(script, last_frame=20)
        self.assertIsNotNone(st["zoom"])
        self.assertGreater(st["zoom"], 1.0)               # колесо сработало
        self.assertTrue(st["on"])                         # пользовательский вид включён
        self.assertIsNone(st["sel"])                      # это было перетаскивание, не клик

    def test_click_on_neuron_selects_it(self):
        target = {}

        def script(n, st, mp):
            evs = []
            if n == 30:
                # берём нейрон из того мозга, который реально использует main()
                nb = next(iter(st["brain"].neurons.values()))
                target["nid"] = nb.id
                x, y = bc.cell_xy(nb.r, nb.c)
                target["p"] = (int(x), int(y))
                mp["p"] = target["p"]
                evs.append(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=target["p"]))
            if n == 31:
                evs.append(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=target["p"]))
            return evs

        self.run_script_with_brain(script, last_frame=40)
        self.assertEqual(self.sel_after, target["nid"])

    def run_script_with_brain(self, script, last_frame):
        """Тот же прогон, но с известным мозгом: main() загрузит его из SAVE_PATH."""
        b = bc.Brain()
        b._clear()
        random.seed(2)
        for f in glob.glob(os.path.join(ROOT, "corpus", "*.txt"))[:1]:
            b.train_file(f)
        _ORIG_SAVE(b, bc.SAVE_PATH)
        bc.brain_ref = b
        st = self.run_script(script, last_frame)
        self.sel_after = st["sel"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
