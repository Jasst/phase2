# -*- coding: utf-8 -*-
"""Чат в терминале с онлайн-обучением.

Порядок хода: сеть отвечает собственным импульсом, затем учитель (если подключён)
даёт ответ, и сеть дистиллирует его. Так знания копятся с каждым диалогом.
"""
import os

from . import store
from .net import BrainNet

HELP = """Команды:
  /good         — подтвердить последний ответ сети (закрепить связь вопрос→ответ)
  /bad          — ослабить связь вопрос→ответ
  /teach текст  — явно обучить: текст — правильный ответ на последний вопрос
  /path         — путь импульса последнего ответа по кластерам
  /clusters     — кластеры нейронов
  /stats        — размер сети
  /save         — сохранить состояние
  /reset        — начать с пустой сети
  /quit         — выйти (состояние сохраняется)"""


def load_or_new(state_dir):
    if os.path.exists(os.path.join(state_dir, "meta.json")):
        return store.load(state_dir)
    return BrainNet()


def run(state_dir, teacher=None, show_teacher=False, autosave_every=10,
        input_fn=input, out=print):
    net = load_or_new(state_dir)
    last = None            # (вопрос, ответ сети)
    last_path = []
    turns = 0
    out(f"pulsenet готов: {net.stats()}. /help — команды.")
    while True:
        try:
            line = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            arg = arg.strip()
            if cmd in ("/quit", "/exit"):
                break
            elif cmd == "/help":
                out(HELP)
            elif cmd == "/stats":
                out(str(net.stats()))
            elif cmd == "/clusters":
                for k, words in net.clusters()[:20]:
                    out(f"  кластер {k:>3} ({len(words)}): " + " ".join(words[:10]))
            elif cmd == "/path":
                if not last_path:
                    out("Пути пока нет.")
                for word, k, mass in last_path:
                    out(f"  {word:<14} → кластер {k:>3}  (масса {mass})")
            elif cmd in ("/good", "/bad"):
                if last is None or not last[1]:
                    out("Нет ответа для оценки.")
                else:
                    net.teach(last[0], last[1], 1.0 if cmd == "/good" else -1.0)
                    out("Принято.")
            elif cmd == "/teach":
                if last is None or not arg:
                    out("Использование: /teach правильный ответ (после вопроса).")
                elif net.teach(last[0], arg, 1.0) is None:
                    out("Ответ пуст, урок не засчитан.")
                else:
                    out("Урок принят.")
            elif cmd == "/save":
                store.save(net, state_dir)
                out("Сохранено.")
            elif cmd == "/reset":
                net = BrainNet()
                last, last_path = None, []
                out("Сеть пересоздана.")
            else:
                out("Неизвестная команда. /help — список.")
            continue

        reply = net.respond(line)
        last_path = reply.path
        last = (line, reply.text)
        out(reply.text or "(пока пусто: сеть ещё ничего не знает по этим словам)")

        if teacher is not None:
            t = teacher.answer(line)
            if t:
                net.teach(line, t, 1.0)
                if show_teacher:
                    out(f"[учитель] {t}")
            else:
                out(f"[учитель недоступен: {teacher.error or 'пустой ответ'}]")

        turns += 1
        if turns % autosave_every == 0:
            store.save(net, state_dir)
    store.save(net, state_dir)
    out("Состояние сохранено. До встречи.")
