# -*- coding: utf-8 -*-
"""Сохранение состояния сети: папка с двумя файлами.

arrays.npz — веса и структура:
  E, C, O   — векторы нейронов (входной, контекстный, выходная голова);
  label     — номер ансамбля каждого нейрона;
  T         — матрица направленных связей между ансамблями (cap × cap);
  thought   — текущая мысль (распределение по ансамблям), если есть.
meta.json — словарь, частоты, профили соседей, конфиг, буфер опыта, счётчики,
            ёмкость ансамблей и история самопредсказания.
Запись атомарная. Старые версии (2–4) читаются.
"""
import dataclasses
import json
import os

import numpy as np

from .config import Config
from .net import BrainNet
from .vocab import Vocab

VERSION = 5


def save(net, directory):
    os.makedirs(directory, exist_ok=True)
    cx = net.cortex
    V = len(net.vocab)
    arrays = {"E": cx.E[:V], "C": cx.C[:V], "O": cx.O[:V],
              "label": cx.label[:V], "T": net.chain.T}
    if net.thought is not None:
        arrays["thought"] = np.asarray(net.thought, dtype=np.float32)
    npz = os.path.join(directory, "arrays.npz")
    tmp = npz + ".tmp.npz"
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, npz)
    meta = {"version": VERSION, "config": dataclasses.asdict(net.cfg),
            "words": net.vocab.words, "counts": net.vocab.counts, "buffer": net.buffer,
            "updates": net.updates, "lessons": net.lessons, "cap": cx.cap,
            "self_errors": net.self_errors[-200:],
            "prof": [[w, [[n, c] for n, c in p.items()]] for w, p in cx.prof.items()]}
    js = os.path.join(directory, "meta.json")
    tmp = js + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, js)


def load(directory):
    with open(os.path.join(directory, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("version") not in (2, 3, 4, VERSION):
        raise ValueError(f"неизвестная версия состояния: {meta.get('version')}")
    data = np.load(os.path.join(directory, "arrays.npz"))
    cap = int(meta.get("cap", data["T"].shape[0]))
    cfg = dict(meta["config"])
    cfg["max_clusters"] = max(cap, int(cfg.get("max_clusters", cap)))
    net = BrainNet(Config(**cfg))
    net.vocab = Vocab(meta["words"], meta.get("counts"))
    V = len(net.vocab)
    cx = net.cortex
    cx.ensure_words(V)
    if cap > cx.cap:
        cx.grow(cap)
    cx.E[:V] = data["E"]
    cx.C[:V] = data["C"]
    cx.O[:V] = data["O"]
    cx.label[:V] = data["label"]
    net.chain.T = np.array(data["T"], dtype=np.float32)
    if "thought" in data.files:
        net.thought = np.array(data["thought"], dtype=np.float64)
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
    net.lessons = meta.get("lessons", meta["updates"])
    net.self_errors = list(meta.get("self_errors", []))
    return net
