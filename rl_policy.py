# -*- coding: utf-8 -*-
"""Обучение с подкреплением: выбор стратегии ответа по оценкам пользователя.

Стратегия («рука бандита») — это пара (режим подсказки графа, переписывание):
  mode   = "whole" — мысль по всей сети;
           "group" — мысль по лучшей группе нейронов;
           "none"  — LM отвечает без подсказки графа;
  revise = True/False — включено ли переписывание черновика LM.

Награда — явная оценка пользователя:
  /good  -> 1.0     (ответ верный)
  /bad   -> 0.0     (ответ неверный)
  /teach -> 0.0 для стратегии, которая дала неверный ответ, на который пользователь
            ответил сам.

Алгоритм — Thompson sampling с бета-апостериорным распределением для каждой
стратегии: успех = сумма наград, провал = число оценок минус сумма наград.
Это простейший корректный RL-алгоритм для задачи «одно решение — одна награда»:
он сам балансирует между проверенными стратегиями и исследованием новых.

Состояние сохраняется в policy.json. Значения «забывания» (DECAY) не дают
старым оценкам навсегда доминировать, если поведение пользователя меняется.
"""

import json
import os
import random

POLICY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "policy.json"
)
DECAY = float(os.environ.get("BRAIN_RL_DECAY", "0.995"))   # забывание на каждом обновлении
PRIOR = (1.0, 1.0)                                          # равномерное начальное знание
MODES = ("whole", "group", "none")


def arms_for(revise_available):
    """Все стратегии. Без переписывания (BRAIN_REVISE=0) остаются только режимы."""
    revises = (True, False) if revise_available else (False,)
    return [(mode, rev) for mode in MODES for rev in revises]


def arm_key(arm):
    return f"{arm[0]}|{int(bool(arm[1]))}"


class Bandit:
    def __init__(self, path=None):
        self.path = path or POLICY_PATH
        self.stats = {}          # arm_key -> [alpha, beta]
        self.updates = 0
        self._load()

    # ---------- выбор ----------

    def choose(self, arms, rng=random):
        """Thompson sampling: случайная выборка из апостериора каждой стратегии."""
        best, best_v = None, -1.0
        for arm in arms:
            a, b = self.stats.get(arm_key(arm), PRIOR)
            v = rng.betavariate(a, b)
            if v > best_v:
                best, best_v = arm, v
        return best

    # ---------- обучение ----------

    def reward(self, arm, r):
        """Обновить апостериор стратегии по награде r in [0, 1]."""
        if arm is None:
            return
        r = max(0.0, min(1.0, float(r)))
        a, b = self.stats.get(arm_key(arm), PRIOR)
        a, b = a + r, b + (1.0 - r)
        self.stats[arm_key(arm)] = [a, b]
        self.updates += 1
        self._decay_others(arm_key(arm))
        self.save()

    def _decay_others(self, updated):
        for key, (a, b) in list(self.stats.items()):
            if key == updated:
                continue
            # Тянем неактуальные оценки к априорному знанию, а не к нулю.
            self.stats[key] = [
                PRIOR[0] + (a - PRIOR[0]) * DECAY,
                PRIOR[1] + (b - PRIOR[1]) * DECAY,
            ]

    # ---------- диагностика ----------

    def mean(self, arm):
        a, b = self.stats.get(arm_key(arm), PRIOR)
        return a / (a + b)

    def summary(self, arms):
        lines = []
        for arm in arms:
            a, b = self.stats.get(arm_key(arm), PRIOR)
            n = (a - PRIOR[0]) + (b - PRIOR[1])
            lines.append(f"{arm[0]:>5} revise={int(bool(arm[1]))}: "
                         f"оценка {self.mean(arm):.2f}, наблюдений {n:.1f}")
        return lines

    # ---------- хранение ----------

    def save(self):
        tmp = self.path + ".tmp"
        data = {"stats": self.stats, "updates": self.updates}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self.stats = {k: [float(v[0]), float(v[1])]
                          for k, v in data.get("stats", {}).items()}
            self.updates = int(data.get("updates", 0))
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            # Повреждённый файл не должен ронять запуск: начинаем с априора.
            self.stats, self.updates = {}, 0
