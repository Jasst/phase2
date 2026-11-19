# -*- coding: utf-8 -*-
"""Индексы ТРИПЛЕКСИИ (Stage 1 брифа).

LSHIndex  — бинарные коды sign(P·e), P 512×dim, фиксированный seed (матрица P из брифа).
            Кандидаты по расстоянию Хэмминга.
VectorIndex — точный косинус по матрице (flat). HNSW в брифе — оптимизация поверх этого же
            интерфейса; здесь exact-поиск даёт эталон для измерения recall (G2).
rrf         — Reciprocal Rank Fusion, score(d)=Σ 1/(k+rank), k=60 по брифу.
"""
from __future__ import annotations

from typing import Dict, Hashable, List, Optional, Sequence, Tuple

import numpy as np

_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.int32)


class LSHIndex:
    def __init__(self, dim: int, bits: int = 512, seed: int = 20260101):
        self.dim = dim
        self.bits = bits
        rng = np.random.default_rng(seed)           # фиксированный seed обязателен для I6
        self.P = rng.standard_normal((bits, dim)).astype(np.float32)
        self.codes: Dict[Hashable, np.ndarray] = {}

    def encode(self, vec: np.ndarray) -> np.ndarray:
        bits = (self.P @ vec) > 0
        return np.packbits(bits)                    # bits/8 байт

    def add(self, key: Hashable, vec: np.ndarray) -> None:
        self.codes[key] = self.encode(vec)

    def remove(self, key: Hashable) -> None:
        self.codes.pop(key, None)

    def topk(self, vec: np.ndarray, k: int, allowed: Optional[set] = None) -> List[Hashable]:
        if not self.codes:
            return []
        q = self.encode(vec)
        keys = [k_ for k_ in self.codes if allowed is None or k_ in allowed]
        if not keys:
            return []
        mat = np.stack([self.codes[k_] for k_ in keys])
        dist = _POP[np.bitwise_xor(mat, q)].sum(axis=1)   # расстояние Хэмминга
        order = np.argsort(dist, kind="stable")[:k]
        return [keys[i] for i in order]


class VectorIndex:
    def __init__(self, dim: int):
        self.dim = dim
        self.keys: List[Hashable] = []
        self.pos: Dict[Hashable, int] = {}
        self.mat = np.zeros((0, dim), dtype=np.float32)

    def add(self, key: Hashable, vec: np.ndarray) -> None:
        v = vec.astype(np.float32).reshape(1, -1)
        if key in self.pos:
            self.mat[self.pos[key]] = v[0]
            return
        self.pos[key] = len(self.keys)
        self.keys.append(key)
        self.mat = np.vstack([self.mat, v]) if len(self.keys) > 1 else v.copy()

    def __len__(self) -> int:
        return len(self.keys)

    def topk(self, vec: np.ndarray, k: int, allowed: Optional[set] = None) -> List[Tuple[Hashable, float]]:
        if not self.keys:
            return []
        sims = self.mat @ vec.astype(np.float32)
        idx = np.arange(len(self.keys))
        if allowed is not None:
            mask = np.array([key in allowed for key in self.keys], dtype=bool)
            idx = idx[mask]
        if idx.size == 0:
            return []
        order = idx[np.argsort(-sims[idx], kind="stable")][:k]
        return [(self.keys[i], float(sims[i])) for i in order]


def rrf(rank_lists: Sequence[Sequence[Hashable]], k: int = 60) -> List[Tuple[Hashable, float]]:
    """Reciprocal Rank Fusion. Ранги с 1. Порядок при равенстве — по первому появлению (детерминизм)."""
    scores: Dict[Hashable, float] = {}
    first: Dict[Hashable, int] = {}
    n = 0
    for lst in rank_lists:
        for rank, key in enumerate(lst, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in first:
                first[key] = n
                n += 1
    return sorted(scores.items(), key=lambda kv: (-kv[1], first[kv[0]]))
