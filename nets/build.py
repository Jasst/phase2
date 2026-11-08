# -*- coding: utf-8 -*-
"""Построение сетей из текстовых файлов: одна сеть — один файл (один «слой знаний»)."""
import re

from .core import Net

_WORD = re.compile(r"[^\W_]+")
_SENT = re.compile(r"[.!?\n]+")


def tokenize(text):
    return [w.lower() for w in _WORD.findall(text) if len(w) >= 2]


def build_net(name, paths, seed=0, temp=4.0, window=3):
    net = Net(name, seed=seed)
    for p in paths:
        with open(p, encoding="utf-8-sig") as f:
            text = f.read()
        for sent in _SENT.split(text):
            toks = tokenize(sent)
            if len(toks) > 1:
                net.learn_sequence(toks, window=window, temp=temp)
    return net
