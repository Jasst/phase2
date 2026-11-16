# -*- coding: utf-8 -*-
"""Нейроны и ансамбли.

Нейрон — слово. У него три вектора:
  E — входной рецептор;
  C — контекст совместной встречи (как в word2vec, обучается SGNS);
  O — строка выходной головы, которая предсказывает следующее слово.

Ансамбль (кластер) — группа нейронов со сходным окружением (распределительная
гипотеза: слова с одними и теми же соседями играют одну роль). Каждый нейрон
ведёт профиль соседей — счётчики слов слева и справа. Онлайн-правило: после
урока для каждого нового слова ищем кандидатов с общими соседями и сливаем его
с самым похожим (косинус профилей >= merge_sim), если размер ансамбля не
превышает max_cluster_size. Слова одной фразы с разным окружением остаются
разными звеньями цепи, а синонимы и темы объединяются.

Центроид M[k] — средний входной вектор членов ансамбля k, нужен для контекста.
"""
import numpy as np


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


class Cortex:
    def __init__(self, cfg, rng):
        self.cfg = cfg
        self.rng = rng
        self.cap = cfg.max_clusters
        self.E = self._init_rows(256)
        self.C = np.zeros((256, cfg.dim), np.float32)
        self.O = np.zeros((256, cfg.dim), np.float32)
        self.label = np.full(256, -1, np.int64)     # нейрон → номер ансамбля (-1 — ещё нет)
        self.members = {}                           # ансамбль → [нейроны]
        self.free = list(range(self.cap))           # свободные номера ансамблей
        self.prof = {}                              # нейрон → {сосед: счётчик}
        self.by_nb = {}                             # сосед → нейроны, которые его знают
        self.M = np.zeros((self.cap, cfg.dim), np.float32)

    def _init_rows(self, n):
        return self.rng.normal(0.0, 1.0 / np.sqrt(self.cfg.dim),
                               (n, self.cfg.dim)).astype(np.float32)

    def ensure_words(self, n):
        cap = len(self.E)
        if n <= cap:
            return
        add = max(n, cap * 2) - cap
        d = self.cfg.dim
        self.E = np.vstack([self.E, self._init_rows(add)])
        self.C = np.vstack([self.C, np.zeros((add, d), np.float32)])
        self.O = np.vstack([self.O, np.zeros((add, d), np.float32)])
        self.label = np.concatenate([self.label, np.full(add, -1, np.int64)])

    # ---------- ансамбли ----------

    def cluster_of(self, word_id):
        return int(self.label[word_id])

    def attach_seq(self, ids):
        """Гарантирует, что каждый нейрон входит в ансамбль. Если номера кончились,
        нейрон берёт ансамбль предыдущего слова в тексте (или 0)."""
        out, prev = [], -1
        for i in ids:
            k = int(self.label[i])
            if k < 0:
                if self.free:
                    k = self.free.pop(0)
                    self.members[k] = []
                else:
                    k = prev if prev >= 0 else 0
                self.members.setdefault(k, []).append(i)
                self.label[i] = k
            out.append(k)
            prev = k
        return out

    def _merge(self, keep, drop):
        """Сливает ансамбль drop в keep. Возвращает (keep, drop) для переноса цепи."""
        for w in self.members.pop(drop, []):
            self.label[w] = keep
            self.members[keep].append(w)
        self.free.append(drop)
        return keep, drop

    def refresh(self):
        """Пересчитать центроиды ансамблей из входных векторов их членов."""
        self.M[:] = 0.0
        for k, words in self.members.items():
            self.M[k] = _unit(self.E[words].mean(axis=0))

    # ---------- совместная встреча ----------

    def cooccur(self, ids, weight, vocab_size):
        """Слова рядом сближаются (SGNS). weight < 0 — расталкивание (негативная обратная связь)."""
        cfg = self.cfg
        if len(ids) < 2 or weight == 0:
            return
        lr = cfg.lr_sgns * weight
        for i, a in enumerate(ids):
            for d in range(1, cfg.emb_window + 1):
                if i + d >= len(ids):
                    break
                b = ids[i + d]
                if a == b:
                    continue
                self._pair(a, b, 1.0, lr)
                for _ in range(cfg.emb_negatives):
                    n = int(self.rng.integers(0, vocab_size))
                    if n != a and n != b:
                        self._pair(a, n, 0.0, lr)

    def update_profiles(self, ids, weight):
        """Профили соседей: слова слева и справа в тексте."""
        for i, a in enumerate(ids):
            for j in (i - 1, i + 1):
                if j < 0 or j >= len(ids) or ids[j] == a:
                    continue
                b = ids[j]
                p = self.prof.setdefault(a, {})
                if weight > 0:
                    p[b] = p.get(b, 0.0) + 1.0
                    self.by_nb.setdefault(b, set()).add(a)
                elif b in p:
                    p[b] = max(0.0, p[b] - 1.0)

    def _sim(self, pa, pb):
        dot = sum(v * pb[k] for k, v in pa.items() if k in pb)
        if dot <= 0:
            return 0.0
        na = sum(v * v for v in pa.values()) ** 0.5
        nb = sum(v * v for v in pb.values()) ** 0.5
        return dot / (na * nb)

    def merge_step(self, ids, weight):
        """Слияния ансамблей по сходству профилей. Возвращает [(keep, drop)] для цепи."""
        merges = []
        if weight <= 0:
            return merges
        for a in dict.fromkeys(ids):
            pa = self.prof.get(a)
            if not pa:
                continue
            cands = set()
            for n in pa:
                cands |= self.by_nb.get(n, set())
            cands.discard(a)
            best, best_sim = None, self.cfg.merge_sim
            for u in cands:
                if self.label[u] < 0 or u not in self.prof:
                    continue
                sim = self._sim(pa, self.prof[u])
                if sim >= best_sim:
                    best, best_sim = u, sim
            if best is None:
                continue
            ka, kb = int(self.label[a]), int(self.label[best])
            if ka == kb or ka < 0 or kb < 0:
                continue
            if len(self.members[ka]) + len(self.members[kb]) > self.cfg.max_cluster_size:
                continue
            big, small = (ka, kb) if len(self.members[ka]) >= len(self.members[kb]) else (kb, ka)
            merges.append(self._merge(big, small))
        return merges

    def _pair(self, a, b, label, lr):
        ea = self.E[a].copy()
        cb = self.C[b].copy()
        s = float(np.clip(ea @ cb, -20.0, 20.0))
        g = (1.0 / (1.0 + np.exp(-s)) - label) * lr
        self.E[a] -= g * cb
        self.C[b] -= g * ea

    # ---------- дистилляция ----------

    def distill(self, ctx, target, vocab_size, lr, smooth):
        """Шаг дистилляции: кросс-энтропия к сглаженной мягкой цели учителя.
        Для softmax с мягкой целью q градиент по логитам равен p - q.
        Возвращает (loss, градиент по контексту)."""
        O = self.O[:vocab_size]            # вид на память: правки попадают в self.O
        z = O @ ctx
        z = z - z.max()
        p = np.exp(z)
        p = p / p.sum()
        q = np.full(vocab_size, smooth / vocab_size, dtype=np.float32)
        q[target] += 1.0 - smooth
        g = (p - q).astype(np.float32)
        grad_ctx = O.T @ g
        O -= lr * np.outer(g, ctx)
        np.clip(O, -5.0, 5.0, out=O)
        loss = float(-np.log(max(float(p[target]), 1e-12)))
        return loss, grad_ctx
