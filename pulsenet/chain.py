# -*- coding: utf-8 -*-
"""Направленные цепи импульсов между ансамблями.

T[i, j] — сила направленной связи «ансамбль i → ансамбль j» (несимметрична).
Мысль — распределение массы по ансамблям. Один шаг (hop) переносит массу
по связям из текущих ансамблей в следующие. Направление задаёт только T,
поэтому импульс идёт от вопроса к ответу, а не расходится по всей сети.

Конкуренция: проходит только top-k ансамблей.
Рефрактерность: уже пройденные ансамбли временно ослабляются,
чтобы цепь не зацикливалась.
"""
import numpy as np


class Chain:
    def __init__(self, cap):
        self.T = np.zeros((cap, cap), np.float32)

    def add(self, i, j, amount):
        if i == j:
            return
        self.T[i, j] = max(0.0, float(self.T[i, j]) + amount)

    def merge(self, keep, drop):
        """Ансамбль drop слит в keep: связи переезжают к нему."""
        self.T[keep, :] += self.T[drop, :]
        self.T[:, keep] += self.T[:, drop]
        self.T[drop, :] = 0.0
        self.T[:, drop] = 0.0
        self.T[keep, keep] = 0.0

    def decay(self, rate):
        self.T *= (1.0 - rate)
        self.T[self.T < 1e-4] = 0.0

    def hop(self, s, visited, topk):
        """Следующее распределение по ансамблям или None, если импульс некуда идти."""
        scores = (s @ self.T) / (1.0 + visited)
        if float(scores.sum()) <= 1e-12:
            return None
        k = min(topk, scores.shape[0])
        idx = np.argpartition(-scores, k - 1)[:k]
        nxt = np.zeros(scores.shape[0])
        nxt[idx] = scores[idx]
        tot = nxt.sum()
        if tot <= 1e-12:
            return None
        return nxt / tot

    def edges(self):
        return int((self.T > 1e-3).sum())
