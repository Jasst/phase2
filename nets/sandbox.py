# -*- coding: utf-8 -*-
"""Изолированная среда адаптации.

Живой ансамбль не меняется напрямую. Адаптация идёт на глубокой копии:
  1. обучение копии на train-примерах (Hebb по парам вопрос→ответ);
  2. эволюционный отбор мутаций на копии (оценка судьёй LLM);
  3. дистилляция: сильнейшая сеть копии передаёт распределение переходов
     остальным сетям (знание переносится мягко);
  4. проверка на val-примерах, которых копия не видела (тоже судья LLM).
Коммит в живой ансамбль — только если качество выросло больше margin.
Отчёт пишется в _sandbox/last_adapt.json.

adapt_gen — генератор (работает с LLM-драйвером), adapt — синхронная обёртка.
"""
import copy
import json
import os

from .cluster import evolve_gen, score_ensemble, score_ensemble_gen
from .llm_tasks import run_sync

SANDBOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sandbox")


class Sandbox:
    def __init__(self, live, root=None):
        self.live = live                       # список Net; напрямую не меняется
        self.root = root

    def adapt_gen(self, train, val, temp=1.0, generations=2, margin=0.02, seed=0, pop=6):
        base = yield from score_ensemble_gen(self.live, val, temp, seed)
        work = copy.deepcopy(self.live)        # полная изоляция
        for q, a in train:
            for net in work:
                net.learn_sequence(list(q) + list(a), window=3, temp=temp)
        work, hist, clusters = yield from evolve_gen(
            work, train, generations=generations, pop=pop, temp=temp, seed=seed,
            probes=[q for q, _ in val] or None)
        if len(work) > 1:                      # дистилляция от лучшей по совпадению слов
            scores = [score_ensemble([n], val, temp, seed) for n in work]
            teacher = work[max(range(len(work)), key=lambda i: scores[i])]
            for net in work:
                if net is not teacher:
                    net.distill_from(teacher, alpha=0.05, temp=2.0)
        after = yield from score_ensemble_gen(work, val, temp, seed)
        commit = after >= base + margin
        report = {"before": round(base, 4), "after": round(after, 4),
                  "commit": commit, "history": hist, "clusters": clusters}
        root = self.root or SANDBOX_DIR
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "last_adapt.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        if commit:
            self.live[:] = work
        return report

    def adapt(self, train, val, temp=1.0, generations=2, margin=0.02, seed=0):
        return run_sync(self.adapt_gen(train, val, temp, generations, margin, seed))
