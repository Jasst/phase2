# -*- coding: utf-8 -*-
"""Калибровка весов планировщика на валидации, выделенной из обучающей части.

Тестовая часть не используется для выбора весов: иначе цифра была бы завышена.
  python experiments/modes/calibrate.py
"""
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

import compare as C  # noqa: E402
from pulsenet import BrainNet, Config  # noqa: E402
from pulsenet.planner import Planner  # noqa: E402

GRID = {
    "rel": [0.5, 1.0, 2.0, 4.0],
    "coh": [0.5, 1.0, 2.0],
    "len_alpha": [0.5, 0.7, 1.0],
    "mmr": [0.5, 0.7, 1.0],
}


def planner_score(net, sents, rel, coh, len_alpha, mmr):
    total = []
    for s in sents:
        q, gold = C.make_task(s)
        p = Planner(net, rel_weight=rel, coh_weight=coh, len_alpha=len_alpha, mmr_lambda=mmr)
        total.append(C.recall(p.think(q).answer, gold))
    return sum(total) / len(total)


def main():
    sents = C.load_sentences(os.path.join(HERE, "..", "..", "corpus"))
    train, test = C.split(sents)                  # test не трогаем при выборе весов
    fit, val = C.split(train, test_frac=0.25, seed=1)
    net = BrainNet(Config(seed=0))
    net.pretrain("\n".join(fit), epochs=3)        # учимся только на fit
    results = []
    for rel, coh, la, mmr in itertools.product(GRID["rel"], GRID["coh"], GRID["len_alpha"], GRID["mmr"]):
        results.append((planner_score(net, val, rel, coh, la, mmr), dict(rel=rel, coh=coh, len_alpha=la, mmr=mmr)))
    results.sort(key=lambda x: -x[0])
    best_score, best = results[0]
    print(f"валидация: лучший {best} -> recall {best_score:.4f} (из {len(results)} наборов)")
    print("худший:", round(results[-1][0], 4), results[-1][1])
    # финальная проверка: переобучаем на всём train, меряем на test один раз
    final = BrainNet(Config(seed=0))
    final.pretrain("\n".join(train), epochs=3)
    default = planner_score(final, test, 2.0, 1.0, 0.7, 0.6)
    tuned = planner_score(final, test, best["rel"], best["coh"], best["len_alpha"], best["mmr"])
    print(f"тест: по умолчанию {default:.4f}, калиброванные веса {tuned:.4f}")
    out = os.path.join(HERE, "..", "out", "calibrate.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"best": best, "val": best_score, "test_default": default, "test_tuned": tuned},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
