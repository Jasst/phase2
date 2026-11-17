# -*- coding: utf-8 -*-
"""Поток мыслей — внутренний нарратив сети (небольшая память).

Состав потока:
  turn  — сжатая мысль о ходе: вопрос и ключевые слова ответа;
  inner — спонтанная мысль: импульс блуждает по цепи от текущей мысли,
          слова выбираются из активных ансамблей.

Мысли сохраняются в data/thoughts.json. Последние подмешиваются в якорь
следующего вопроса (respond), а шаги нарратива становятся уроками сети
(«из предыдущего контекста следует следующая мысль») — так из мыслей
постепенно строится своя сеть, которая участвует в ответе.
"""
import json
import os
import time

LIMIT = 80


def _read(path):
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
    return []


class ThoughtStream:
    def __init__(self, path):
        self.path = path
        self.items = _read(path)[-LIMIT:]

    def push(self, text, kind):
        """Добавить мысль; подряд идущая одинаковая не дублируется."""
        text = (text or "").strip()
        if not text:
            return None
        if self.items and self.items[-1]["text"] == text and self.items[-1]["kind"] == kind:
            return None
        item = {"t": round(time.time(), 1), "text": text, "kind": kind}
        self.items.append(item)
        del self.items[:-LIMIT]
        self._save()
        return item

    def recent(self, k=10):
        """Последние мысли, новые первыми."""
        return self.items[-k:][::-1]

    def context_text(self, k=3):
        """Склейка последних мыслей — контекст для следующего ответа или урока."""
        return " ".join(i["text"] for i in self.items[-k:])

    def clear(self):
        self.items = []
        self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass
