# -*- coding: utf-8 -*-
"""Обучение слова через LLM: объяснение → учитель → слои связей.

1. LLM подробно объясняет слово (где и как используется, какие слова рядом).
2. Из объяснения строится сеть-учитель: слово соединяется с каждым словом ответа,
   а слова ответа связываются между собой по предложениям.
3. Рекуррентное воссоздание слоёв: L1 — слова ответа, отобранные учителем по
   активации от слова; L2, L3… — проход учителя от предыдущего слоя. Каждый слой
   даёт исходящие связи от предыдущего, вес убывает с глубиной.
4. Граф получает связи через brain.associate (исходящие от слова и от слоёв), а сети
   ансамбля — дистилляцией распределения учителя (в песочнице, см. manager.py).
"""
import random
import re

from .build import tokenize
from .core import Net, top

LAYERS = 3          # сколько слоёв воссоздаётся от слова
ANSWER_K = 12       # сколько слов ответа становятся первым слоем
LAYER_K = 6         # слов в каждом следующем слое
DECAY = 0.6         # вес связей убывает с глубиной слоя
_SENT = re.compile(r"[.!?\n;]+")


def explain_prompt(word):
    return (f"Объясни подробно слово «{word}»: что оно значит, где и как его используют, "
            "какие слова обычно стоят рядом с ним и с какими понятиями оно связано. "
            "Пиши 4–6 коротких предложений по-русски, без списков и заголовков.")


def teacher_from_text(word, text, seed=0, temp=4.0):
    """Сеть-учитель из объяснения: слово → каждое слово ответа, и порядок внутри предложений."""
    net = Net("llm:" + word, seed=seed)
    for sent in _SENT.split(text or ""):
        toks = tokenize(sent)
        if not toks:
            continue
        if word not in toks:
            toks = [word] + toks
        net.learn_sequence(toks, window=len(toks), temp=temp, rate=1.0)
        row = net.W[word]
        for t in toks:
            if t != word:
                row[t] = row.get(t, 0.0) + 1.0 / len(toks)
    net._touch()
    return net


def _sweep(net, act, rng, temp=1.0):
    for _ in range(2):
        net.sweep(act, temp=temp, rng=rng)
    return act


def build_layers(teacher, word, seed=0, layers=LAYERS, answer_k=ANSWER_K, layer_k=LAYER_K):
    """Рекуррентные слои от слова. layers[0] — слова ответа."""
    rng = random.Random(seed)
    act = _sweep(teacher, {word: 1.0}, rng)
    first = [w for w in top(act, answer_k + 1) if w != word][:answer_k]
    out = [first] if first else []
    used = {word, *first}
    current = {w: act.get(w, 0.0) for w in first}
    for _ in range(layers - 1):
        if not current:
            break
        act = _sweep(teacher, dict(current), rng)
        nxt = [w for w in top(act, layer_k + len(used)) if w not in used][:layer_k]
        if not nxt:
            break
        out.append(nxt)
        used.update(nxt)
        current = {w: act.get(w, 0.0) for w in nxt}
    return out


def plan_from_text(word, text, seed=0):
    teacher = teacher_from_text(word, text, seed=seed)
    layers = build_layers(teacher, word, seed=seed)
    answer = [w for w in dict.fromkeys(tokenize(text or "")) if w != word]
    return {"word": word, "text": text or "", "answer": answer,
            "layers": layers, "teacher": teacher}


def apply_plan(brain, plan, weight=1.0):
    """Записывает план в граф (только из главного потока). Возвращает число новых связей."""
    before = len(brain.links)
    word = plan["word"]
    answer = plan["answer"][:ANSWER_K * 2]
    if answer:
        brain.train_words([word] + answer, weight=weight)
    prev = [word]
    for depth, layer in enumerate(plan["layers"]):
        if not layer:
            continue
        brain.associate(prev, layer, weight=weight * (DECAY ** depth))
        prev = layer
    return len(brain.links) - before
