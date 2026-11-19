# -*- coding: utf-8 -*-
"""Внутренний планировщик: думаем, потом отвечаем.

Цикл одного хода:
  1. Фокус. Вопрос превращается в якорь (ансамбли), смешивается с текущей мыслью.
  2. Варианты. Лучевой поиск (beam search) строит несколько цепочек слов по импульсу.
     Каждый шаг использует те же механизмы, что respond(): выходная голова, цепочки,
     ансамбли, торможение повторов и синапс-запрет на пройденную пару.
  3. Обдумывание. Варианты оцениваются: логарифм вероятности (с нормировкой по длине),
     релевантность вопросу (близость ансамблей), связность (доля переходов, подтверждённых
     уроками). Все варианты — это мысли, их можно показать.
  4. Ответ. Выбранный вариант — ответ. Он сформирован из перебора, а не скопирован.
  5. Обратная связь. Негативная оценка «распространяется назад» по цепочке: переходы
     ослабляются с затуханием gamma^k от конца. Позитивная — усиливает. Так коррекция
     происходит внутри той же цепочки импульса, без отдельной модели.

Состояние мысли (net.thought) и слепки сохраняются между ходами, как и в reasoning.
"""
from dataclasses import dataclass, field

import numpy as np

from .vocab import EOS_ID, detokenize, tokenize


@dataclass
class Candidate:
    words: list
    text: str
    logprob: float            # средний логарифм вероятности шага (нормированный по длине)
    relevance: float          # близость ансамблей варианта к вопросу
    coherence: float          # доля переходов, подтверждённых уроками
    score: float = 0.0


@dataclass
class Plan:
    question: str
    candidates: list = field(default_factory=list)   # все варианты, лучший первым
    answer: str = ""
    confidence: float = 0.0
    focus: list = field(default_factory=list)        # топ ансамблей мысли

    @property
    def thoughts(self):
        return [c.text for c in self.candidates if c.text]


def _logsumexp(x):
    m = np.max(x)
    if not np.isfinite(m):
        return -np.inf
    return m + np.log(np.sum(np.exp(x - m)))


class Planner:
    def __init__(self, net, width=4, max_len=None, rel_weight=None, coh_weight=None, len_alpha=None,
                 gamma=0.8, mmr_lambda=None):
        cfg = net.cfg
        self.net = net
        self.width = width
        self.max_len = max_len or cfg.max_answer
        self.rel_weight = cfg.plan_rel if rel_weight is None else rel_weight
        self.coh_weight = cfg.plan_coh if coh_weight is None else coh_weight
        self.len_alpha = cfg.plan_len_alpha if len_alpha is None else len_alpha
        self.mmr_lambda = cfg.mmr_lambda if mmr_lambda is None else mmr_lambda
        self.gamma = gamma                  # затухание обратного распространения по цепочке
        self.last_plan = None

    # ----- 1. фокус -----

    def _focus(self, question):
        net = self.net
        q = net.vocab.encode(tokenize(question), grow=False)
        cx = net.cortex
        cx.refresh()
        anchor = net._dist(q)
        if anchor is None:
            return None, [], q
        focus = net._focus(anchor)                  # мысль + вопрос (фокус)
        return focus, anchor, q

    # ----- 2. варианты: лучевой поиск -----

    def _step_logits(self, beam, anchor):
        net, cfg, cx = self.net, self.net.cfg, self.net.cortex
        V = len(net.vocab)
        last = beam["last"]
        ctx = (cfg.alpha * (cx.M.T @ beam["s"]) + (1 - cfg.alpha) * cx.E[last]).astype(np.float32)
        logits = (cx.O[:V] @ ctx).astype(np.float64)
        logits -= cfg.freq_penalty * np.log1p(np.asarray(net.vocab.counts[:V], dtype=np.float64))
        mass = np.where(cx.label[:V] >= 0, beam["s"][np.maximum(cx.label[:V], 0)], 0.0)
        logits += cfg.ensemble_gain * np.log(mass + 1e-3)
        hist = beam["hist"]
        tp = net._trans_dist(hist, V) if hist else None
        if tp is not None:
            logits += cfg.trans_gain * np.log(tp + cfg.trans_floor)
        # синапс-запрет: уже пройденная пара не повторяется
        if hist:
            for a, b in beam["pairs"]:
                if a == hist[-1] and b < V:
                    logits[b] -= cfg.cycle_penalty
        # повторы слов в пределах ответа — блокируются
        for w in set(beam["out"][-cfg.no_repeat:]):
            if w != EOS_ID:
                logits[w] = -np.inf
        if not beam["out"]:
            logits[EOS_ID] = -np.inf                # ответ не бывает пустым
            for i in range(1, V):
                if not net.vocab.words[i][:1].isalnum():
                    logits[i] = -np.inf
        return logits

    def _expand(self, beam, anchor, V):
        net = self.net
        logits = self._step_logits(beam, anchor)
        lse = _logsumexp(logits)
        if not np.isfinite(lse):
            return []
        logp = logits - lse
        top = np.argsort(-logp)[:self.width]
        children = []
        for tok in top:
            tok = int(tok)
            if not np.isfinite(logp[tok]):
                continue
            child = {"s": beam["s"].copy(), "visited": beam["visited"].copy(), "last": beam["last"],
                     "hist": list(beam["hist"]), "out": list(beam["out"]),
                     "pairs": set(beam["pairs"]), "lp": beam["lp"] + float(logp[tok]), "done": False}
            if tok == EOS_ID:
                child["done"] = True
            else:
                if child["hist"]:
                    child["pairs"].add((child["hist"][-1], tok))
                child["out"].append(tok)
                child["hist"].append(tok)
                k_e = net.cortex.cluster_of(tok)
                child["s"] = net._advance(child["s"], k_e, anchor, child["visited"])
                child["last"] = tok
            children.append(child)
        return children

    def candidates(self, question):
        """Лучевой поиск: до width вариантов (цепочек), каждый — законченная мысль."""
        net, cfg = self.net, self.net.cfg
        focus, anchor, q = self._focus(question)
        if focus is None or not q:
            return [], []
        V = len(net.vocab)
        root = {"s": focus.copy(), "visited": np.zeros(net.cortex.cap), "last": q[-1],
                "hist": [q[-1]], "out": [], "pairs": set(), "lp": 0.0, "done": False}
        beams, finished = [root], []
        for _ in range(self.max_len):
            pool = []
            for b in beams:
                pool.extend(self._expand(b, anchor, V))
            if not pool:
                break
            pool.sort(key=lambda c: -self._norm_lp(c))
            beams = []
            for c in pool:
                if c["done"]:
                    if c["out"]:
                        finished.append(c)
                elif len(beams) < self.width:
                    beams.append(c)
                if len(beams) >= self.width and len(finished) >= self.width:
                    break
            if not beams:
                break
        finished += [b for b in beams if b["out"]]          # недописанные — тоже варианты
        finished.sort(key=lambda c: -self._norm_lp(c))
        seen, unique = set(), []
        for c in finished:
            key = tuple(c["out"])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique[:self.width], anchor

    def _norm_lp(self, c):
        n = max(1, len(c["out"]))
        return c["lp"] / (n ** self.len_alpha)

    # ----- 3. обдумывание: оценка вариантов -----

    def _score(self, words, anchor, lp):
        net = self.net
        d = net._dist(words)
        rel = 0.0
        if d is not None and anchor is not None:
            na, nd = np.linalg.norm(anchor), np.linalg.norm(d)
            rel = float(anchor @ d / (na * nd)) if na > 1e-12 and nd > 1e-12 else 0.0
        # связность: средняя вероятность перехода по уроку (не факт наличия, а частота).
        # Переход, которого не было в уроках, даёт 0 — вариант с выдуманными связями проигрывает.
        # где есть триграмма (два предыдущих слова), берём её, иначе биграмму
        probs = []
        for i in range(1, len(words)):
            a, b = words[i - 1], words[i]
            d = None
            if i >= 2:
                d3 = net.trans2.get((words[i - 2], a))
                if d3 and b in d3:
                    d = d3
            if d is None:
                d = net.trans1.get(a, {})
            tot = sum(d.values())
            probs.append(d.get(b, 0.0) / tot if tot > 0 else 0.0)
        coh = float(np.mean(probs)) if probs else 0.0
        return rel, coh

    def _mmr(self, cands):
        """Maximal Marginal Relevance: следующий вариант — хорошо оценённый и непохожий
        на уже выбранные. Без этого лучевой поиск даёт варианты, отличающиеся хвостом."""
        lam = self.mmr_lambda
        if lam >= 1.0 or len(cands) <= 1:
            return cands
        from .imprints import jaccard
        rest, chosen = list(cands), []
        while rest:
            def value(c):
                sim = max((jaccard(c.words, s.words) for s in chosen), default=0.0)
                return lam * c.score - (1.0 - lam) * sim
            best = max(rest, key=value)
            rest.remove(best)
            chosen.append(best)
        return chosen

    def think(self, question):
        """Полный ход: варианты, оценка, выбор. Возвращает Plan и сохраняет мысль."""
        net = self.net
        cands, anchor = self.candidates(question)
        plan = Plan(question=question)
        if not cands:
            self.last_plan = plan
            return plan
        out = []
        for c in cands:
            lp = self._norm_lp(c)
            rel, coh = self._score(c["out"], anchor, lp)
            total = lp + self.rel_weight * rel + self.coh_weight * coh
            text = detokenize([net.vocab.words[i] for i in c["out"]])
            out.append(Candidate(words=list(c["out"]), text=text, logprob=round(lp, 4),
                                 relevance=round(rel, 4), coherence=round(coh, 4), score=round(total, 4)))
        out.sort(key=lambda x: -x.score)
        plan.candidates = self._mmr(out)
        best = out[0]
        plan.answer = best.text
        plan.confidence = round(float(np.exp(best.logprob)), 4)
        plan.focus = net.top_focus(5)
        # мысль: выбранный вариант становится слепком; сеть держит её в уме
        importance = best.coherence * max(best.relevance, 0.1)
        net.imprints.add(best.words, round(float(importance), 4))
        d = net._dist(best.words)
        if d is not None:
            net.thought = net._norm(d)
        self.last_plan = plan
        return plan

    # ----- 5. обратная связь: обратное распространение по цепочке -----

    def feedback(self, verdict, weight=1.0, rate=0.5):
        """verdict: 'good' или 'bad' — оценка последнего ответа. Коррекция идёт назад по цепочке.

        Для перехода на расстоянии k от конца шаг коррекции равен rate * weight * gamma^k.
        Переход меняется мультипликативно: count *= (1 ± шаг). Абсолютная поправка здесь не
        годится: счётчики после предобучения — сотни, и поправка на единицу ничего не меняет.
        Негатив ослабляет переходы, позитив усиливает. Изменения лежат в trans-таблицах,
        поэтому сохраняются и влияют на следующие варианты.
        """
        plan = self.last_plan
        if not plan or not plan.candidates:
            return {"ok": False, "error": "нет последнего плана для оценки"}
        sign = 1.0 if verdict == "good" else -1.0
        # выходная голова: тот же урок с отрицательным весом, что и «Плохой ответ»
        if plan.question and plan.answer:
            self.net.learn(plan.question, plan.answer, sign * weight * 0.5)
        seq = plan.candidates[0].words
        if not seq:
            return {"ok": False, "error": "пустой ответ"}
        n = len(seq)
        pairs = [(seq[i - 1], seq[i]) for i in range(1, n)] + [(seq[-1], EOS_ID)]
        changed = 0
        for i, (a, b) in enumerate(pairs):
            k = (n - 1 - i) if i < n - 1 else 0          # расстояние от конца цепочки
            step = min(0.9, rate * weight * (self.gamma ** k))
            d = self.net.trans1.get(a)
            if not d or b not in d:
                continue
            d[b] = d[b] * (1.0 + sign * step) if sign > 0 else d[b] * (1.0 - step)
            if d[b] <= 1e-6:
                d.pop(b, None)
                if not d:
                    self.net.trans1.pop(a, None)
            changed += 1
        return {"ok": True, "verdict": verdict, "changed": changed}
