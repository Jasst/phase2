# -*- coding: utf-8 -*-
"""Одна сеть: узлы-слова, разреженные веса связей, температурные переходы.

Веса хранятся сырыми (счётчики совместной встречи). Распределение переходов
строится на лету: p(j|i) ∝ exp(log(1+w_ij) / T). Так вес распределяется
по связям без ручной нормировки, а температура T управляет остротой:
  T → 0   — жадно, только самая сильная связь;
  T ~ 1   — обычная выборка по весам;
  T → ∞   — почти равномерно, мысль становится разнообразнее.
"""
import math
import random
from collections import defaultdict

EPS = 1e-6
HUB_DAMP = 0.5          # 0 — без гашения; больше — сильнее штраф за частые слова


class Net:
    def __init__(self, name, seed=None):
        self.name = name
        self.W = defaultdict(dict)          # i -> {j: сырой вес связи}
        self.rng = random.Random(seed)
        self._indeg = None                  # кэш входящих связей (сбрасывается при записи)

    # ---------- структура ----------

    def nodes(self):
        s = set(self.W)
        for row in self.W.values():
            s.update(row)
        return s

    def _in_degree(self):
        if self._indeg is None:
            deg = defaultdict(int)
            for row in self.W.values():
                for j in row:
                    deg[j] += 1
            self._indeg = deg
        return self._indeg

    def _touch(self):
        self._indeg = None

    def n_edges(self):
        return sum(len(r) for r in self.W.values())

    def transitions(self, i, temp=1.0):
        """Распределение переходов из узла i при температуре temp."""
        row = self.W.get(i)
        if not row:
            return {}
        t = max(temp, 1e-3)
        deg = self._in_degree()
        # гашение хабов: слово, связанное со всем подряд, не должно доминировать
        logits = {j: (math.log1p(w) - HUB_DAMP * math.log1p(deg[j])) / t
                  for j, w in row.items()}
        mx = max(logits.values())
        ex = {j: math.exp(v - mx) for j, v in logits.items()}
        z = sum(ex.values())
        return {j: v / z for j, v in ex.items()}

    # ---------- обучение ----------

    def learn_sequence(self, tokens, window=3, temp=1.0, rate=1.0):
        """Связи между словами в пределах окна. Ближайшие сильнее (rate/d).
        Температура определяет, охотно ли создаются новые связи:
        вероятность создания = 1 - exp(-T * gain). Усиление существующих
        связей детерминировано."""
        for i, a in enumerate(tokens):
            for d, b in enumerate(tokens[i + 1:i + 1 + window], start=1):
                if a == b:
                    continue
                gain = rate / d
                self._touch()
                row = self.W[a]
                if b in row:
                    row[b] += gain
                elif self.rng.random() < 1.0 - math.exp(-temp * gain):
                    row[b] = gain

    def distill_from(self, teacher, alpha=0.1, temp=2.0):
        """Дистилляция знаний: мягкие цели учителя (распределение переходов
        при высокой температуре) переносятся в связи ученика с долей alpha.
        Жёсткого копирования нет: знание передаётся распределением."""
        for i in list(teacher.W):
            soft = teacher.transitions(i, temp)
            for j, p in soft.items():
                self.W[i][j] = self.W[i].get(j, 0.0) + alpha * p
        self._touch()

    # ---------- проход сигнала ----------

    def sweep(self, act, temp=1.0, damp=0.5, rng=None):
        """Асинхронный проход: узлы обходятся в СЛУЧАЙНОМ порядке. Масса,
        переданная узлу раньше в этом же проходе, уже видна следующим узлам,
        поэтому порядок влияет на результат — это и есть случайный порядок
        нейронов. Масса сохраняется (нет потерь), damp — доля, которая
        уходит по связям за проход."""
        order = list(act)
        (rng or self.rng).shuffle(order)
        for i in order:
            v = act.get(i, 0.0)
            if v < EPS:
                continue
            out = v * damp
            act[i] = v - out
            for j, p in self.transitions(i, temp).items():
                act[j] = act.get(j, 0.0) + out * p
        for k in [k for k, v in act.items() if v < EPS]:
            del act[k]
        return act

    def prune(self, max_out=60):
        """Ограничивает число исходящих связей узла: слабые связи уходят.
        Опыт (feedback.jsonl) растёт без предела, активный граф — нет."""
        removed = 0
        for i, row in self.W.items():
            if len(row) > max_out:
                keep = sorted(row.items(), key=lambda x: -x[1])[:max_out]
                removed += len(row) - max_out
                self.W[i] = dict(keep)
        self._touch()
        return removed

    # ---------- мутации ----------

    def mutate(self, rate=0.05, sigma=0.3):
        """Мутант: часть связей ослабляется/удаляется, часть меняется
        по log-normal шуму, добавляются новые случайные связи."""
        child = Net(self.name, seed=self.rng.random())
        child.W = defaultdict(dict, {i: dict(r) for i, r in self.W.items()})
        child._touch()
        for i, row in child.W.items():
            for j in list(row):
                r = self.rng.random()
                if r < rate * 0.5:
                    del row[j]
                elif r < rate:
                    row[j] = max(0.01, row[j] * math.exp(self.rng.gauss(0.0, sigma)))
        nodes = list(child.W)
        if len(nodes) > 1:
            for _ in range(max(1, int(rate * len(nodes)))):
                i, j = self.rng.choice(nodes), self.rng.choice(nodes)
                if i != j:
                    child.W[i][j] = self.rng.random() * 0.5
                    child._touch()
        return child

    # ---------- сериализация ----------

    def to_dict(self):
        return {"name": self.name,
                "W": {i: {j: round(w, 5) for j, w in r.items()}
                      for i, r in self.W.items() if r}}

    @classmethod
    def from_dict(cls, d, seed=None):
        net = cls(d["name"], seed=seed)
        for i, r in d.get("W", {}).items():
            net.W[i] = {j: float(w) for j, w in r.items()}
        return net


def top(act, k=6):
    """k самых активных узлов (при равенстве — по алфавиту, для повторяемости)."""
    return [w for w, _ in sorted(act.items(), key=lambda x: (-x[1], x[0]))[:k]]
