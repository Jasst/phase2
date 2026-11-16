# -*- coding: utf-8 -*-
"""Сохранение состояния: папка с arrays.npz (веса, ансамбли, цепь) и meta.json
(словарь, конфиг, опыт, профили соседей). Запись атомарная."""
import dataclasses
import json
import os

import numpy as np

from .config import Config
from .net import BrainNet
from .vocab import Vocab

VERSION = 4


def save(net, directory):
    os.makedirs(directory, exist_ok=True)
    cx = net.cortex
    V = len(net.vocab)
    arrays = {"E": cx.E[:V], "C": cx.C[:V], "O": cx.O[:V],
              "label": cx.label[:V], "T": net.chain.T}
    npz = os.path.join(directory, "arrays.npz")
    tmp = npz + ".tmp.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, npz)
    meta = {"version": VERSION, "config": dataclasses.asdict(net.cfg),
            "words": net.vocab.words, "counts": net.vocab.counts, "buffer": net.buffer, "updates": net.updates,
            "prof": [[w, [[n, c] for n, c in p.items()]] for w, p in cx.prof.items()]}
    js = os.path.join(directory, "meta.json")
    tmp = js + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, js)


def load(directory):
    with open(os.path.join(directory, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("version") != VERSION:
        raise ValueError(f"неизвестная версия состояния: {meta.get('version')}")
    net = BrainNet(Config(**meta["config"]))
    net.vocab = Vocab(meta["words"], meta.get("counts"))
    V = len(net.vocab)
    cx = net.cortex
    cx.ensure_words(V)
    data = np.load(os.path.join(directory, "arrays.npz"))
    cx.E[:V] = data["E"]
    cx.C[:V] = data["C"]
    cx.O[:V] = data["O"]
    cx.label[:V] = data["label"]
    net.chain.T[:, :] = data["T"]
    # восстановить состав ансамблей и список свободных номеров по меткам нейронов
    cx.members = {}
    for i in range(V):
        k = int(cx.label[i])
        if k >= 0:
            cx.members.setdefault(k, []).append(i)
    cx.free = [k for k in range(cx.cap) if k not in cx.members]
    for w, pairs in meta["prof"]:
        cx.prof[w] = {n: c for n, c in pairs}
        for n in cx.prof[w]:
            cx.by_nb.setdefault(n, set()).add(w)
    net.buffer = [tuple(x) for x in meta["buffer"]]
    net.updates = meta["updates"]
    return net
