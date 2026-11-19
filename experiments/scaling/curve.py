# -*- coding: utf-8 -*-
"""Кривая обучения: качество ответов сети на отложенных вопросах при росте числа пар.

Пары делятся на обучающие и отложенные по id (фиксированный seed, без утечки).
Сеть учится по парам по одной — как онлайн-уроки в приложении — и в контрольных
точках (10, 25, 50, ...) проверяется на отложенных вопросах.

Метрики:
  keyword_recall — доля содержательных слов эталонного ответа, встреченных в ответе сети;
  exact          — доля ответов, совпадающих с эталоном целиком.

  python experiments/scaling/curve.py --pairs experiments/out/pairs.jsonl
"""
import argparse
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from pulsenet import BrainNet, Config  # noqa: E402
from pulsenet.vocab import tokenize  # noqa: E402

OUT_DEFAULT = os.path.join(HERE, "..", "out", "curve.json")


def load_pairs(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def split(pairs, test_frac=0.2, seed=0):
    """Детерминированное разбиение: один и тот же вопрос никогда не попадает в обе части."""
    items = sorted(pairs, key=lambda p: p["id"])
    random.Random(seed).shuffle(items)
    k = max(1, math.ceil(len(items) * test_frac))
    return items[k:], items[:k]


def keyword_recall(reply, gold):
    need = {w for w in tokenize(gold) if len(w) > 2}
    if not need:
        return 0.0
    return len(need & set(tokenize(reply))) / len(need)


def evaluate(net, test):
    if not test:
        return {"keyword_recall": None, "exact": None}
    recalls, exact = [], 0
    for p in test:
        text = net.respond(p["question"], temp=0).text or ""
        recalls.append(keyword_recall(text, p["answer"]))
        exact += tokenize(text) == tokenize(p["answer"])
    return {"keyword_recall": round(sum(recalls) / len(recalls), 3),
            "exact": round(exact / len(test), 3)}


def run_curve(train, test, checkpoints, seed=0):
    """Одна сеть, учится по парам подряд; на контрольных точках — замер."""
    net = BrainNet(Config(seed=seed))
    rows, learned = [], 0
    for n in sorted(set(c for c in checkpoints if c <= len(train))):
        for p in train[learned:n]:
            net.teach(p["question"], p["answer"], replay=False)
        learned = n
        rows.append({"pairs": n, **evaluate(net, test)})
    return rows


def checkpoints_for(total):
    pts, n = [], 10
    while n < total:
        pts.append(n)
        n = int(n * 2)
    pts.append(total)
    return pts


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pairs", default=os.path.join(HERE, "..", "out", "pairs.jsonl"))
    p.add_argument("--out", default=OUT_DEFAULT)
    p.add_argument("--test-frac", type=float, default=0.2)
    a = p.parse_args(argv)
    pairs = load_pairs(a.pairs)
    train, test = split(pairs, a.test_frac)
    rows = run_curve(train, test, checkpoints_for(len(train)))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"train": len(train), "test": len(test), "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"обучение: {len(train)} пар, проверка: {len(test)} пар")
    print(f"{'пар':>6}  {'слова':>8}  {'точно':>8}")
    for r in rows:
        print(f"{r['pairs']:>6}  {r['keyword_recall']!s:>8}  {r['exact']!s:>8}")


if __name__ == "__main__":
    main()
