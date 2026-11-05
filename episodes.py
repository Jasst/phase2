# -*- coding: utf-8 -*-
"""Эпизодическая память: подтверждённые пары «вопрос → ответ».

Граф хранит ассоциации, а эпизоды — конкретные проверенные ответы
(/teach и /good). Перед ответом сеть ищет похожий прошлый вопрос
(TF-IDF косинус). Сильное совпадение — ответ берётся из памяти;
слабое — передаётся LM как доверенная подсказка.
"""

import json
import math
import os
import re
import time
from collections import Counter

from mind import STOPWORDS

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "episodes.json")
MAX_EPISODES = 2000
RECALL_STRONG = 0.7      # ответ из памяти без обращения к LM (в режиме графа)
RECALL_WEAK = 0.3        # подсказка для LM

_WORD = re.compile(r"[^\W_]+")


def _words(text):
    return [w for w in _WORD.findall(str(text).lower())
            if len(w) >= 3 and w not in STOPWORDS]


def _cos(a, b):
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _key(question):
    return " ".join(_words(question))


class EpisodeMemory:
    def __init__(self, path=PATH):
        self.path = path
        self.items = []
        self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.items = [e for e in data
                          if isinstance(e, dict) and e.get("q") and e.get("a")]
        except (OSError, ValueError):
            self.items = []

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, question, answer, score=1):
        """Запоминает пару. Тот же вопрос (по словам) — заменяет ответ."""
        q, a = str(question).strip(), str(answer).strip()
        if not _words(q) or not a:
            return False
        key = _key(q)
        for e in self.items:
            if _key(e["q"]) == key:
                e["a"] = a
                e["t"] = time.time()
                e["score"] = e.get("score", 0) + score
                break
        else:
            self.items.append({"q": q, "a": a, "score": score, "t": time.time()})
        if len(self.items) > MAX_EPISODES:
            self.items.sort(key=lambda e: (e.get("score", 0), e.get("t", 0)))
            self.items = self.items[-MAX_EPISODES:]
        self.save()
        return True

    def drop(self, question):
        """Удаляет эпизод с таким вопросом (после /bad)."""
        key = _key(question)
        before = len(self.items)
        self.items = [e for e in self.items if _key(e["q"]) != key]
        if len(self.items) != before:
            self.save()
        return before - len(self.items)

    def recall(self, query, k=2):
        """Список (сходство, эпизод) по убыванию сходства, сходство > 0."""
        qw = _words(query)
        if not qw or not self.items:
            return []
        docs = [Counter(_words(e["q"])) for e in self.items]
        n = len(docs)
        df = Counter()
        for d in docs:
            df.update(d.keys())

        def vec(counter):
            return {w: c * (math.log((n + 1) / (df.get(w, 0) + 1)) + 1.0)
                    for w, c in counter.items()}

        qv = vec(Counter(qw))
        scored = []
        for e, d in zip(self.items, docs):
            sim = _cos(qv, vec(d))
            if sim > 0:
                scored.append((sim, e))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]
