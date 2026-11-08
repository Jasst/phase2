# -*- coding: utf-8 -*-
"""Подключает слои cortex.py к brain_chat.py точечными заменами.

Файл brain_chat.py в CRLF; замены сохраняют окончания строк. Каждая строка-якорь
должна встречаться ровно один раз, иначе скрипт останавливается и ничего не пишет.

Запуск из корня проекта:  python patch_cortex.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "brain_chat.py")

REPLACEMENTS = [
    # 1. импорт слоёв
    ("from nets.teach import apply_plan\r\n",
     "from nets.teach import apply_plan\r\n"
     "from cortex import Cortex\r\n"),
    # 2. период фонового прохода слоёв (сек)
    ("AUTOSAVE_SEC = 60.0\r\n",
     "AUTOSAVE_SEC = 60.0\r\n"
     "CORTEX_EVERY = 45.0     # сек между фоновыми проходами слоёв и вопросами сети\r\n"),
    # 3. состояние слоёв рядом с coh_acc (инициализация до цикла)
    ("\r\n    coh_acc = 0.0\r\n",
     "\r\n    coh_acc = 0.0\r\n"
     "    cortex = Cortex()\r\n"
     "    cortex_acc = 0.0\r\n"),
    # 4. фоновый проход: только когда пользователь не ждёт ответа
    ("        coh_acc += dt\r\n",
     "        cortex_acc += dt\r\n"
     "        if cortex_acc >= CORTEX_EVERY and resp is None and pending is None:\r\n"
     "            cortex_acc = 0.0\r\n"
     "            for q in cortex.idle(brain, list(mind.stream)):\r\n"
     "                chat.append([\"net\", q])\r\n"
     "                scroll[\"chat\"] = 0\r\n"
     "        coh_acc += dt\r\n"),
    # 5. команда /cortex
    ("                    elif msg == \"/groups\":\r\n",
     "                    elif msg == \"/cortex\":\r\n"
     "                        for line in cortex.report(brain):\r\n"
     "                            sys_msg(line)\r\n"
     "                    elif msg == \"/groups\":\r\n"),
    # 6. строка справки
    ("/groups — смысловые группы; /facts",
     "/groups — смысловые группы; /cortex — слои и вопросы сети; /facts"),
]


def main():
    with open(PATH, "rb") as f:
        text = f.read().decode("utf-8")
    for old, new in REPLACEMENTS:
        n = text.count(old)
        if n != 1:
            print(f"СТОП: якорь встречается {n} раз(а): {old[:60]!r}")
            return 1
        text = text.replace(old, new)
    with open(PATH, "wb") as f:
        f.write(text.encode("utf-8"))
    print(f"ok: применено {len(REPLACEMENTS)} замен в brain_chat.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
