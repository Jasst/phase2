# -*- coding: utf-8 -*-
"""v17: понятные подписи мыслей, ответов и статуса сети (модуль explain.py).

Меняет brain_chat.py точечными CRLF-безопасными заменами; каждая замена проверяет
число совпадений. Запуск из корня проекта:  python patch_explain_v17.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def crlf(s):
    return s.replace("\r\n", "\n").replace("\n", "\r\n")


STATUS_FUNC = '''def draw_status(screen, fonts, text):
    """Строка «что делает сеть» под информационной панелью (v17)."""
    surf = fonts["ui"].render(text, True, ACCENT_HI)
    rect = pygame.Rect(NET_W - surf.get_width() - 32, 44, surf.get_width() + 20, 26)
    rounded(screen, rect, PANEL, BORDER, 8)
    screen.blit(surf, (rect.x + 10, rect.y + 5))


def nearest_neuron(brain, mx, my, radius=12.0):'''

EDITS = [
    # (старое, новое, сколько раз должно встретиться)
    ("from cortex import Cortex\n",
     "from cortex import Cortex\n"
     "from explain import HELP, answer_label, describe_stream, describe_thought, status_text\n", 1),
    ('st["thought"].describe()', 'describe_thought(st["thought"], brain)', 2),
    ('thoughts.append(["thought", thought])', 'thoughts.append(["thought", describe_stream(thought)])', 1),
    ('label = f"Сеть · опора {score:.2f}"', 'label = answer_label(score)', 1),
    ('"выделите мышью · Ctrl+C копирует"', 'HELP["thoughts"]', 1),
    ("        draw_hint(screen, fonts)\n",
     "        draw_hint(screen, fonts)\n"
     "        draw_status(screen, fonts, status_text(answering=resp is not None,\n"
     "                                               waiting=pending is not None,\n"
     "                                               learning=llm_tasks.active()))\n", 1),
    ("def nearest_neuron(brain, mx, my, radius=12.0):", STATUS_FUNC, 1),
    ('    "Значение без числа показывает текущую настройку, например /time.",\n',
     '    "Значение без числа показывает текущую настройку, например /time.",\n'
     '    "Как читать: мысли — ассоциации, не ответы; «уверенность» — насколько ответ опирается на граф; "\n'
     '    "строка вверху справа — чем занята сеть.",\n', 1),
    ('("ЛЕВОЕ", "анализ",', '("ЛЕВОЕ", "связные слова",', 1),
    ('("ПРАВОЕ", "блуждание",', '("ПРАВОЕ", "ассоциации",', 1),
    ('render("согласованность полей", True, DIM)', 'render("согласованность полушарий", True, DIM)', 1),
]


def main():
    path = os.path.join(ROOT, "brain_chat.py")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8")
    for old, new, expected in EDITS:
        old_c, new_c = crlf(old), crlf(new)
        n = text.count(old_c)
        if n != expected:
            print(f"СТОП: якорь встречается {n} раз, ожидалось {expected}: {old[:60]!r}")
            return 1
        text = text.replace(old_c, new_c)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))
    print(f"ok: brain_chat.py, замен {len(EDITS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
