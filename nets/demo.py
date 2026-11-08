# -*- coding: utf-8 -*-
"""Демо: по одной сети на текстовый файл, цепочка на вопрос, дамп матриц.

Запуск из корня проекта:  python -m nets.demo "привет"
"""
import glob
import os
import sys

from nets import Chain, build_net, tokenize, transfer_matrix, dump_matrix, save_ensemble
from nets.store import STORE_DIR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ["corpus.txt", "tech.txt", "nature.txt", "daily.txt"]


def main(question, temp=1.0):
    paths = [os.path.join(ROOT, s) for s in SOURCES if os.path.exists(os.path.join(ROOT, s))]
    nets = [build_net(os.path.splitext(os.path.basename(p))[0], [p], seed=i)
            for i, p in enumerate(paths)]
    print("сети:", ", ".join(f"{n.name}({n.n_edges()} связей)" for n in nets))

    chain = Chain(nets, temp=temp, seed=0)
    res = chain.run(tokenize(question), max_rounds=8, min_rounds=2, time_budget=5.0)
    print(chain.format_trace(res))

    for a, b in zip(nets, nets[1:]):
        M = transfer_matrix(a, b, temp=temp)
        dump_matrix(M, a.name, b.name, temp)
    save_ensemble(nets, root=STORE_DIR)
    print(f"\nматрицы перерасчёта: nets/_matrices/ ({len(nets)-1} пар), ансамбль: nets/_store/")


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or "привет")
