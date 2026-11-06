"""Конвейер ответа с LM: мысль -> ответ по мысли -> перепроверка и переписывание.
LM заменён фейком, который записывает запросы; сеть и конвейер настоящие."""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, ROOT); os.chdir(ROOT)
import pygame
import brain_chat as bc
from _isolate import isolate; isolate()   # тесты не трогают файлы проекта

class FakeLM:
    def __init__(self):
        self.calls, self._n, self._out = [], 0, {}
        self.backend = type("B", (), {"speed_text": staticmethod(lambda: "")})()
        self.name = "fake"
        self.last_logprobs = []   # как у настоящего LMWorker (пустые логиты у фейка)
    def submit(self, q, ctx=(), memory=(), note="", remember=True):
        self._n += 1
        self.calls.append({"q": q, "note": note, "remember": remember})
        self._out[self._n] = ("Нейрон — клетка мозга." if len(self.calls) == 1
                              else "Нейрон — это клетка мозга, она передаёт сигналы.")
        return self._n
    def partial(self, rid): return ""
    def poll(self, rid): return self._out.pop(rid, None)

fake = FakeLM()
bc.start_lm = lambda: (fake, "фейковая LM")

def text(t): return pygame.event.Event(pygame.TEXTINPUT, text=t)
def enter(): return pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0)
script = {1: [text("что такое нейрон")], 2: [enter()],
          6: [text("/teach нейрон это клетка мозга")], 7: [enter()],
          12: [text("что такое нейрон")], 13: [enter()]}
state = {"frame": 0, "chat": []}
real_get = pygame.event.get
def fake_get(*a, **k):
    real_get(); state["frame"] += 1
    return [pygame.event.Event(pygame.QUIT)] if state["frame"] > 160 else script.get(state["frame"], [])
pygame.event.get = fake_get
real_chat = bc.draw_chat
def rec_chat(screen, thoughts, chat, *a, **k):
    state["chat"] = [list(x) for x in chat]
    return real_chat(screen, thoughts, chat, *a, **k)
bc.draw_chat = rec_chat
# Конвейер проверяем при фиксированной стратегии (вся сеть + переписывание).
# Сам выбор стратегии бандитом проверяется в test_rl_proactive.py.
import rl_policy
rl_policy.Bandit.choose = lambda self, arms, rng=None: ("whole", True)
bc.main()

# первый вопрос (до /teach) — без мысли, второй — с мыслью группы сети
q_calls = [c for c in fake.calls]
assert len(q_calls) >= 3, q_calls                       # ответ, переписывание, ответ на второй вопрос
assert q_calls[0]["remember"] is True
second = [c for c in q_calls if "Мысль сети" in c["note"]]
assert second, "ответ не получил мысль сети"
rewrite = [c for c in q_calls if c["q"].startswith("Вопрос:")]
assert rewrite and rewrite[0]["remember"] is False, "переписывание не выполнено"
msgs = [e[1] for e in state["chat"] if len(e) > 1]   # запись чата: [кто, текст, (оценка)]
assert any("клетка мозга" in m for m in msgs), msgs
print("pipeline OK: запросов", len(q_calls), "переписываний", len(rewrite))
