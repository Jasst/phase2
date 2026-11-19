# -*- coding: utf-8 -*-
"""Проактивное рассуждение: внутренний голос сети.

Импульс мысли идёт по цепочкам слов и ансамблям, как в net.respond, но с тормозными
синапсами и стоп-паттерном:
  • торможение: недавно сказанное слово временно подавляется (угасает за шаг);
  • синапс-запрет: пара слов, уже пройденная в этом импульсе, не повторяется —
    мысль не ходит по кругу;
  • стоп: конец фразы поощряется с ростом длины, поэтому мысль завершается.

Каждый импульс оставляет слепок (ImprintCache). Новый импульс стартует смесью
прошлого слепка и самого важного недавнего слепка: старая мысль «держится в уме»
и складывается с новой. Рассуждение останавливается на петле (слепки почти
совпадают) или когда слепки исчерпаны. Из слепков собирается ответ пользователю
(inner_voice): ответ генерируется сетью с контекстом из лучших слепков.
"""
import numpy as np

from .imprints import jaccard
from .vocab import EOS_ID, detokenize, tokenize


def _cos(a, b):
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(a @ b) / (na * nb)


class Reasoner:
    def __init__(self, net):
        self.net = net

    def impulse(self, seed, start_word=None):
        """Один импульс мысли. Возвращает (слова, сколько шагов подтверждено цепочками) или None."""
        net, cfg = self.net, self.net.cfg
        cx = net.cortex
        V = len(net.vocab)
        s = net._norm(seed.copy())
        anchor = s.copy()
        visited = np.zeros(cx.cap)
        inhib = np.zeros(V)                      # тормозные синапсы: недавние слова
        hist = [start_word] if start_word is not None else []
        last = start_word if start_word is not None else EOS_ID
        out, pairs, supported = [], set(), 0
        for t in range(cfg.reason_words):
            ctx = (cfg.alpha * (cx.M.T @ s) + (1 - cfg.alpha) * cx.E[last]).astype(np.float32)
            logits = (cx.O[:V] @ ctx).astype(np.float64)
            logits -= cfg.freq_penalty * np.log1p(np.asarray(net.vocab.counts[:V], dtype=np.float64))
            mass = np.where(cx.label[:V] >= 0, s[np.maximum(cx.label[:V], 0)], 0.0)
            logits += cfg.ensemble_gain * np.log(mass + 1e-3)
            if hist:
                tp = net._trans_dist(hist, V)
                if tp is not None:
                    logits += cfg.trans_gain * np.log(tp + cfg.trans_floor)
                    supported += 1
                # синапс-запрет: пары, уже пройденные в этом импульсе, блокируются
                for a, b in pairs:
                    if a == hist[-1] and b < V:
                        logits[b] -= cfg.cycle_penalty
            logits -= cfg.inhib_gain * inhib
            if t == 0:
                logits[EOS_ID] = -np.inf         # мысль не бывает пустой
                for i in range(1, V):
                    if not net.vocab.words[i][:1].isalnum():
                        logits[i] = -np.inf
            else:
                logits[EOS_ID] += cfg.stop_gain * (t / cfg.reason_words)   # стоп-паттерн
            if not np.isfinite(logits).any():
                break
            z = (logits - logits.max()) / max(cfg.temperature, 1e-3)
            p = np.exp(z)
            p /= p.sum()
            tok = int(net.rng.choice(V, p=p))
            if tok == EOS_ID:
                break
            if hist:
                pairs.add((hist[-1], tok))
            out.append(tok)
            hist.append(tok)
            inhib *= cfg.inhib_decay
            inhib[tok] += 1.0
            s = net._advance(s, cx.cluster_of(tok), anchor, visited)
            last = tok
        if len(out) < 2:
            return None
        return out, supported

    def _seed(self, prev_words):
        """Новый импульс = смесь прошлого слепка и самого важного недавнего слепка."""
        net, cfg = self.net, self.net.cfg
        parts = []
        if prev_words:
            d = net._dist(prev_words)
            if d is not None:
                parts.append((1.0, d))
        best = self.net.imprints.best_recent(1)
        if best:
            d = net._dist(list(best[0].words))
            if d is not None:
                parts.append((0.5, d))
        if not parts:
            d = net._start_thought()
            return d if d is not None else None
        seed = sum(w * d for w, d in parts)
        return net._norm(seed)

    def _novelty(self, words):
        best = max((jaccard(words, i.words) for i in self.net.imprints.short), default=0.0)
        return 1.0 - best

    def run(self, steps=None):
        """Проактивное рассуждение: несколько импульсов, каждый оставляет слепок.
        Возвращает тексты мыслей. Останавливается на петле или при пустом импульсе."""
        net, cfg = self.net, self.net.cfg
        steps = cfg.reason_steps if steps is None else steps
        made, prev_words, prev_mass = [], None, None
        for _ in range(max(1, int(steps))):
            seed = self._seed(prev_words)
            if seed is None:
                break
            start = prev_words[-1] if prev_words else None
            res = self.impulse(seed, start)
            if res is None:
                break
            words, supported = res
            mass = net._dist(words)
            if prev_mass is not None and mass is not None and _cos(mass, prev_mass) >= cfg.reason_cycle_sim:
                break                              # петля мыслей: новый слепок повторяет старый
            importance = (supported / max(1, len(words))) * self._novelty(words)
            net.imprints.add(words, round(float(importance), 4))
            while net.imprints.due():
                net.consolidate()
            made.append(detokenize([net.vocab.words[i] for i in words]))
            prev_words, prev_mass = words, mass
        return made

    def inner_voice(self, question, k=4):
        """Ответ пользователю из слепков: сеть отвечает на вопрос, опираясь на лучшие мысли."""
        net = self.net
        q = net.vocab.encode(tokenize(question), grow=False)
        pool = self._recall(q, k)
        context = " ".join(detokenize([net.vocab.words[i] for i in imp.words]) for imp in pool)
        return net.respond(question, context=context or None)

    def _recall(self, q, k):
        """Слепки, близкие к вопросу, ранжированные по важности."""
        net = self.net
        anchor = net._dist(q) if q else None
        cands = list(net.imprints.short) + list(net.imprints.longterm)
        scored = []
        for imp in cands:
            overlap = 0.0
            if anchor is not None:
                d = net._dist(list(imp.words))
                overlap = _cos(anchor, d) if d is not None else 0.0
            scored.append((overlap * 0.5 + imp.importance, imp))
        scored.sort(key=lambda x: -x[0])
        return [imp for _, imp in scored[:k]]

