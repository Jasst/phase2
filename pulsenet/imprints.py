# -*- coding: utf-8 -*-
"""Слепки мыслей: кратковременная мысль, которая держится «в уме».

Импульс мысли оставляет слепок — короткую цепочку слов (id нейронов-слов) и
важность. Важность — одно число на слепок, а не на нейрон: память слепков растёт
с числом мыслей, а не с размером сети.

  pending   — новые слепки, ещё не сведённые в долговременную память;
  short     — недавние слепки для дедупликации и вспоминания (затухают);
  longterm  — сведённые цепочки. Их не стирает очередной урок: важные
              (importance >= protect_at) не вытесняются и повторяются при обучении.

Каждые `batch` слепков проводится консолидация (см. net.consolidate): дедупликация
и конкатенация мыслей в одну цепочку, которую сеть запоминает как последовательность.
"""
import numpy as np

from .vocab import EOS_ID


def jaccard(a, b):
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class Imprint:
    __slots__ = ("words", "importance", "hits", "step")

    def __init__(self, words, importance, step=0, hits=1):
        self.words = tuple(words)
        self.importance = float(importance)
        self.hits = int(hits)
        self.step = int(step)

    def to_json(self):
        return {"w": list(self.words), "i": round(self.importance, 5), "h": self.hits, "t": self.step}

    @classmethod
    def from_json(cls, d):
        return cls(d["w"], d["i"], d.get("t", 0), d.get("h", 1))


class ImprintCache:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pending = []      # ещё не сведённые слепки
        self.short = []        # недавние, для дедупликации
        self.longterm = []     # сведённые цепочки (Imprint.words — цепочка слов)
        self.step = 0

    # ----- кратковременная память -----

    def add(self, words, importance):
        """Записать слепок. Почти такой же (Жаккар >= dedupe) — не копится, а усиливается."""
        words = [w for w in words if w != EOS_ID]
        if len(words) < 2:
            return None
        self.step += 1
        for old in self.short:
            if jaccard(old.words, words) >= self.cfg.imprint_dedupe:
                old.hits += 1
                old.importance = min(1.0, max(old.importance, importance) + 0.05)
                return old
        imp = Imprint(words, importance, self.step)
        self.short.append(imp)
        self.pending.append(imp)
        del self.short[:-self.cfg.imprint_cap]
        return imp

    def due(self):
        return len(self.pending) >= self.cfg.imprint_batch

    def take_batch(self):
        batch = self.pending[:self.cfg.imprint_batch]
        del self.pending[:len(batch)]
        return batch

    def decay(self):
        """Непрерывность важных мыслей: слабые слепки угасают, защищённые — нет."""
        rate = self.cfg.imprint_decay
        for imp in self.short:
            if imp.importance < self.cfg.protect_at:
                imp.importance *= (1.0 - rate)
        self.short = [i for i in self.short if i.importance >= 0.05 or i in self.pending]

    def best_recent(self, k=1):
        """Самые важные недавние слепки: их масса подмешивается в новый импульс."""
        return sorted(self.short, key=lambda i: -i.importance)[:k]

    # ----- конкатенация и долговременная память -----

    @staticmethod
    def concat(batch, max_len=40):
        """Склейка слепков в одну цепочку: повторные слова и петли убираются."""
        chain, seen = [], set()
        for imp in batch:
            for w in imp.words:
                if w in seen:
                    continue
                chain.append(w)
                seen.add(w)
                if len(chain) >= max_len:
                    return chain
        return chain

    def store_longterm(self, chain, importance):
        if len(chain) < 2:
            return None
        for lt in self.longterm:
            if jaccard(lt.words, chain) >= self.cfg.longterm_dedupe:
                lt.hits += 1
                lt.importance = min(1.0, max(lt.importance, importance) + 0.05)
                return lt
        lt = Imprint(chain, importance, self.step)
        self.longterm.append(lt)
        self._evict()
        return lt

    def _evict(self):
        """Вытеснение по важности, а не по порядку. Защищённые — последними."""
        cap = self.cfg.longterm_cap
        while len(self.longterm) > cap:
            victim = min(self.longterm, key=lambda i: (i.importance >= self.cfg.protect_at, i.importance))
            self.longterm.remove(victim)

    def replay_pick(self, n, rng):
        """Выбор для повторения: вероятность пропорциональна важности (защищённые чаще)."""
        if not self.longterm or n <= 0:
            return []
        w = np.array([i.importance for i in self.longterm], dtype=np.float64) + 1e-6
        idx = rng.choice(len(self.longterm), size=min(n, len(self.longterm)),
                                                      replace=False, p=w / w.sum())
        return [self.longterm[int(i)] for i in idx]

    # ----- хранение -----

    def to_json(self):
        return {"short": [i.to_json() for i in self.short],
                "pending": [i.to_json() for i in self.pending],
                "long": [i.to_json() for i in self.longterm], "step": self.step}

    def load_json(self, d):
        if not d:
            return
        self.short = [Imprint.from_json(x) for x in d.get("short", [])]
        self.pending = [Imprint.from_json(x) for x in d.get("pending", [])]
        self.longterm = [Imprint.from_json(x) for x in d.get("long", [])]
        self.step = int(d.get("step", 0))
