# -*- coding: utf-8 -*-
"""Мутации, кластеризация и эволюционный отбор ансамбля сетей.

Оценка качества — судья LLM (judge.py). Если LLM не ответила, для этого примера
берётся совпадение слов (recall). Все функции оценки — генераторы: они отдают
запрос судье и получают ответ, поэтому работают и в главном цикле без блокировок.
"""
import random

from .chain import Chain
from .core import top
from .judge import judge_prompt, parse_score
from .llm_tasks import run_sync


def recall(words, target):
    t = set(target)
    if not t:
        return 0.0
    return len(set(words) & t) / len(t)


def score_ensemble_gen(ensemble, examples, temp=1.0, seed=0, k=6):
    """Средняя оценка по примерам [(вопрос_токены, эталон_токены), ...].
    Каждый пример оценивает LLM-судья; при отсутствии ответа — recall."""
    if not examples:
        return 0.0
    chain = Chain(ensemble, temp=temp, seed=seed)
    total = 0.0
    for q, a in examples:
        res = chain.run(q, max_rounds=6, min_rounds=2)
        thought = res["thought"][:k]
        reply = yield ("judge", judge_prompt(" ".join(q), " ".join(a), " ".join(thought)))
        score = parse_score(reply)
        total += score if score is not None else recall(thought, a)
    return total / len(examples)


def score_ensemble(ensemble, examples, temp=1.0, seed=0, k=6):
    """Синхронная оценка без LLM (recall)."""
    return run_sync(score_ensemble_gen(ensemble, examples, temp, seed, k))


def mutate_ensemble(ensemble, rate=0.05, sigma=0.3):
    return [net.mutate(rate=rate, sigma=sigma) for net in ensemble]


def signature(net, probes, temp=1.0, k=6):
    """Отклик сети на пробные вопросы: набор топ-слов (для кластеризации)."""
    sig = set()
    rng = random.Random(0)
    for q in probes:
        act = {w: 1.0 for w in q}
        for _ in range(2):
            net.sweep(act, temp=temp, rng=rng)
        sig.update(top(act, k))
    return sig


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a | b else 0.0


def cluster_nets(nets, probes, temp=1.0, threshold=0.3):
    """Жадная кластеризация по откликам. Возвращает [[индексы сетей], ...]."""
    sigs = [signature(n, probes, temp) for n in nets]
    clusters, centers = [], []
    for idx, s in enumerate(sigs):
        best, best_sim = None, 0.0
        for c, cs in enumerate(centers):
            sim = _jaccard(s, cs)
            if sim > best_sim:
                best, best_sim = c, sim
        if best is not None and best_sim >= threshold:
            clusters[best].append(idx)
            centers[best] = centers[best] | s
        else:
            clusters.append([idx])
            centers.append(set(s))
    return clusters


def evolve_gen(ensemble, examples, generations=5, pop=6, rate=0.05,
               temp=1.0, seed=0, probes=None):
    """(1+λ) эволюция как генератор. Возвращает (лучший ансамбль, история, кластеры)."""
    rng = random.Random(seed)
    best = list(ensemble)
    best_score = yield from score_ensemble_gen(best, examples, temp, seed)
    history = [round(best_score, 4)]
    for _ in range(generations):
        cands = []
        for _ in range(pop):
            cand = mutate_ensemble(best, rate=rate)
            cands.append((cand, (yield from score_ensemble_gen(cand, examples, temp, seed))))
        cand, score = max(cands, key=lambda x: x[1])
        if score >= best_score:              # равные принимаем: дрейф по плато
            best, best_score = cand, score
        history.append(round(best_score, 4))
    clusters = cluster_nets(best, probes or [q for q, _ in examples], temp) if best else []
    return best, history, clusters


def evolve(ensemble, examples, generations=5, pop=6, rate=0.05,
           temp=1.0, seed=0, probes=None):
    return run_sync(evolve_gen(ensemble, examples, generations, pop, rate,
                               temp, seed, probes))
