"""Сквозной тест главного цикла: вопросы → /good ×4 → адаптация сетей через LLM-судью.
LM заменена фейком (submit/poll/is_current), сеть и конвейер настоящие.
Запуск из корня проекта:  python test_llm_main.py
"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, ROOT); os.chdir(ROOT)
import pygame
import brain_chat as bc
from _isolate import isolate
isolate()

class FakeLM:
    def __init__(self):
        self.backend = type("B", (), {"speed_text": staticmethod(lambda: "")})()
        self.name = "fake"; self.last_logprobs = []
        self.calls = []; self.judge_calls = 0; self._id = 0; self._cur = None; self._out = {}
    def submit(self, q, ctx=(), memory=(), note="", remember=True):
        self._id += 1; self._cur = self._id
        self.calls.append({"q": q, "remember": remember})
        if q.startswith("Ты оцениваешь"):
            self.judge_calls += 1
            self._out[self._id] = "7"
        else:
            self._out[self._id] = "Нейрон — клетка мозга, она передаёт сигналы."
        return self._id
    def is_current(self, rid): return rid == self._cur
    def partial(self, rid): return ""
    def poll(self, rid): return self._out.pop(rid, None)

fake = FakeLM()
bc.start_lm = lambda: (fake, "фейковая LM")

import rl_policy
rl_policy.Bandit.choose = lambda self, arms, rng=None: ("whole", True)

captured = {}
real_open = bc.NetsManager.open
def open_and_capture(*a, **k):
    m = real_open(*a, **k)
    m.adapt_every = 4
    captured["mgr"] = m
    return m
bc.NetsManager.open = staticmethod(open_and_capture)

def text(t): return pygame.event.Event(pygame.TEXTINPUT, text=t)
def enter(): return pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0)

script = {}
frame = 1
for cycle in range(4):
    script[frame] = [text(f"что такое нейрон {cycle}")]; script[frame + 1] = [enter()]
    script[frame + 10] = [text("/good")]; script[frame + 11] = [enter()]
    frame += 22
# Пункт «обучить через LLM» ищем по списку действий меню, а не по жёсткой координате:
# отступ 8 + заголовок 18 + индекс * 24 + половина строки 11.
from ui_feedback import NeuronMenu as _NM  # noqa: E402
_m = _NM(); _m.word = "нейрон"
MENU_LLM_Y = 8 + 18 + [a for _, a in _m._items()].index("llm_teach") * 24 + 11
MENU_AT = frame + 5          # правый клик по слову «нейрон», затем пункт «обучить через LLM»
QUIT_AT = frame + 400

state = {"frame": 0, "chat": []}
real_get = pygame.event.get
def fake_get(*a, **k):
    real_get(); state["frame"] += 1
    f = state["frame"]
    if f > QUIT_AT:
        return [pygame.event.Event(pygame.QUIT)]
    if f == MENU_AT:
        b = captured["brain"]
        n = next(n for n in b.neurons.values() if n.word == "нейрон")
        x, y = bc.cell_xy(n.r, n.c)
        state["menu_pos"] = (int(x), int(y))
        return [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=state["menu_pos"])]
    if f == MENU_AT + 2:
        x, y = state["menu_pos"]
        # пункт «обучить через LLM» — 4-й в меню слова (индекс 3)
        return [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(x + 20, y + MENU_LLM_Y))]
    return script.get(f, [])
pygame.event.get = fake_get
real_chat = bc.draw_chat
def rec_chat(screen, thoughts, chat, *a, **k):
    state["chat"] = [list(x) for x in chat]
    return real_chat(screen, thoughts, chat, *a, **k)
bc.draw_chat = rec_chat
real_scene = bc.draw_scene
def rec_scene(screen, brain, *a, **k):
    captured['brain'] = brain
    return real_scene(screen, brain, *a, **k)
bc.draw_scene = rec_scene

bc.main()

mgr = captured["mgr"]
msgs = [e[1] for e in state["chat"] if len(e) > 1]
assert fake.judge_calls > 0, "адаптация должна оценивать через LLM-судью"
assert mgr.reports, "адаптация должна была отработать"
assert any("Адаптация сетей" in m for m in msgs), msgs[-8:]
assert not mgr.pending or len(mgr.pending) < 4, "очередь должна очиститься"
assert any("Нейрон — клетка мозга" in m for m in msgs), "объяснение LLM должно показаться в чате"
assert any("связей в граф добавлено" in m for m in msgs), msgs[-6:]
print("llm main OK: судья вызван", fake.judge_calls, "раз; адаптаций", len(mgr.reports))
