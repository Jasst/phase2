# -*- coding: utf-8 -*-
"""Направленные цепи импульсов между ансамблями.

T[i, j] — сила связи «ансамбль i → ансамбль j» (несимметрична). Импульс — это
распределение массы по ансамблям. Шаг (hop) переносит массу по связям из тех
ансамблей, где масса сейчас есть. Поскольку масса сосредоточена в нескольких
ансамблях, шаг считается только по их строкам: это быстро и не зависит от общего
числа ансамблей (рост без лимита).

Конкуренция: проходит только top-k ансамблей.
Рефрактерность: пройденные ансамбли временно ослабляются, чтобы цепь не зацикливалась.
"""
import numpy as np


class Chain:
    def __init__(self, cap):
        self.T = np.zeros((cap, cap), np.float32)

    @property
    def cap(self):
        return self.T.shape[0]

    def grow(self, new_cap):
        if new_cap <= self.cap:
            return
        T = np.zeros((new_cap, new_cap), np.float32)
        T[:self.cap, :self.cap] = self.T
        self.T = T

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
        nz = np.flatnonzero(s > 1e-9)
        if nz.size == 0:
            return None
        scores = (s[nz] @ self.T[nz]) / (1.0 + visited)
        if float(scores.sum()) <= 1e-12:
            return None
        k = min(topk, scores.shape[0])
        idx = np.argpartition(-scores, k - 1)[:k]
        nxt = np.zeros(self.cap)
        nxt[idx] = scores[idx]
        tot = nxt.sum()
        if tot <= 1e-12:
            return None
        return nxt / tot

    def edges(self):
        return int((self.T > 1e-3).sum())
