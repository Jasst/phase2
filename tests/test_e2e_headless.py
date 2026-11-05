"""Полный цикл main() без окна, режим без LM: обучение, память, ответ из графа.
Запуск:  python tests/test_e2e_headless.py   (файлы сохранений создаются в папке проекта)"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.environ["SDL_VIDEODRIVER"] = "dummy"; os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["BRAIN_LM_PATH"] = "/nonexistent/model.gguf"
sys.path.insert(0, ROOT); os.chdir(ROOT)
import pygame
import brain_chat as bc

def text(t): return pygame.event.Event(pygame.TEXTINPUT, text=t)
def enter(): return pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0)
script = {1: [text("что такое нейрон")], 2: [enter()],
          6: [text("/teach нейрон это клетка мозга")], 7: [enter()],
          12: [text("что такое нейрон")], 13: [enter()],
          18: [text("/mind")], 19: [enter()]}
state = {"frame": 0, "chat": [], "thoughts": []}
real_get = pygame.event.get
def fake_get(*a, **k):
    real_get(); state["frame"] += 1
    return [pygame.event.Event(pygame.QUIT)] if state["frame"] > 160 else script.get(state["frame"], [])
pygame.event.get = fake_get
real_chat = bc.draw_chat
def rec_chat(screen, thoughts, chat, *a, **k):
    state["chat"] = [list(x) for x in chat]; state["thoughts"] = [list(x) for x in thoughts]
    return real_chat(screen, thoughts, chat, *a, **k)
bc.draw_chat = rec_chat
bc.main()
msgs = [m for _, m in state["chat"]]
assert any(m.startswith("нейрон это клетка") for m in msgs), msgs   # ответ из памяти/графа
assert not any("Вспомнила" in m or "Уверенность" in m for m in msgs), "служебный вывод в чате"
assert any(who == "thought" for who, _ in state["thoughts"]), "мысль групп не показана"
print("e2e OK (режим без LM)")
