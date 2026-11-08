# -*- coding: utf-8 -*-
"""Сохранение ансамбля: один JSON на сеть в папке _store/."""
import json
import os

from .core import Net

STORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_store")


def save_ensemble(nets, root=None):
    root = root or STORE_DIR
    os.makedirs(root, exist_ok=True)
    for net in nets:
        tmp = os.path.join(root, net.name + ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(net.to_dict(), f, ensure_ascii=False)
        os.replace(tmp, os.path.join(root, net.name + ".json"))


RESERVED = frozenset({"pending.json"})   # служебные файлы менеджера, не сети


def load_ensemble(root=None, seed=0):
    root = root or STORE_DIR
    nets = []
    if not os.path.isdir(root):
        return nets
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".json") or fn in RESERVED:
            continue
        with open(os.path.join(root, fn), encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and "name" in payload and "W" in payload:
            nets.append(Net.from_dict(payload, seed=seed))
    return nets
