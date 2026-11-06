"""Проверка новых команд v4: /help, /ctx, /time, /tokens, /history, /neurons, /assoc.
Запуск из корня проекта:  python tests/test_commands_v4.py"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["BRAIN_LM_PATH"] = "/nonexistent/model.gguf"
sys.path.insert(0, ROOT); os.chdir(ROOT)
import pygame
from phase3._isolate import isolate; isolate()   # тесты не трогают файлы проекта
from phase3.lm_backend import RUNTIME, strip_leaks, clean_reply

def text(t): return pygame.event.Event(pygame.TEXTINPUT, text=t)
def enter(): return pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0)
cmds = ["/help", "/ctx 2500", "/time 45", "/time", "/tokens 300", "/history 5",
        "/neurons 800", "/neurons abc", "/assoc кошка мышка сыр", "/assoc", "/mind"]
script, f = {}, 1
for c in cmds:
    script[f] = [text(c)]; script[f + 1] = [enter()]; f += 4
state = {"frame": 0, "chat": []}
real_get = pygame.event.get
def fake_get(*a, **k):
    real_get(); state["frame"] += 1
    return [pygame.event.Event(pygame.QUIT)] if state["frame"] > f + 5 else script.get(state["frame"], [])
pygame.event.get = fake_get
real_chat = bc.draw_chat
def rec_chat(screen, thoughts, chat, *a, **k):
    state["chat"] = [list(x) for x in chat]
    return real_chat(screen, thoughts, chat, *a, **k)
bc.draw_chat = rec_chat
bc.main()
msgs = [m for _, m in state["chat"]]
def has(s): return any(s in m for m in msgs)
assert has("Напишите вопрос обычными словами"), "нет справки /help"
assert has("контекст диалога: 2500"), "/ctx не сработал"
assert has("время на ответ: 45"), "/time не сработал"
assert RUNTIME["time_budget"] == 45 and RUNTIME["context_chars"] == 2500
assert RUNTIME["max_tokens"] == 300 and RUNTIME["history"] == 5
assert has("Лимита нет"), "/neurons: лимит должен быть снят"
assert has("Связано слов: 3"), "/assoc не сработал"
assert has("модель: контекст"), "/mind не показывает настройки"
assert strip_leaks("Да. Мысль сети (вся сеть): кот. Кот пушистый.") == "Да. Кот пушистый."
assert clean_reply("Ответ: Вода мокрая. /no_think") == "Вода мокрая."
print("commands OK")

# Лимит времени: генерация обрывается по времени и ответ заканчивается точкой.
import time as _t
from phase3 import lm_backend as L


class Fake(L.LMBackend):
    def __init__(self): self.history = []; self._gen_lock = __import__("threading").Lock(); self.last_stats = None; self.last_truncated = False; self.name = "fake"; self.backend_used = "llamacpp"
    def _stream_llamacpp(self, messages, should_stop):
        for part in ["Вода ", "мокрая. ", "Совсем ", "длинный ", "хвост без точки"] * 50:
            if should_stop(): break
            _t.sleep(0.01)
            yield part
RUNTIME["time_budget"] = 0.3
L.BACKEND = "llamacpp"
out = ""
for out, done in Fake().stream("вопрос"):
    pass
assert done and out.endswith("мокрая."), repr(out[-40:])
print("time budget OK:", repr(out[:40]))
