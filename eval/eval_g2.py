# -*- coding: utf-8 -*-
"""Гейт G2 брифа: recall@K LSH-кандидатов относительно точного float-поиска.

  python eval/eval_g2.py                                # синтетика, офлайн-эмбеддер
  python eval/eval_g2.py --encoder openai --url http://127.0.0.1:8091/v1 --dim 1024

Порог брифа: recall@64 ≥ 0.90. Результат пишется в eval/results/g2.json.
"""
import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from pulsenet.triplex.encoder import HashEncoder, OpenAIEmbedder  # noqa: E402
from pulsenet.triplex.index import LSHIndex, VectorIndex  # noqa: E402

GATE = 0.90


def synthetic_facts(n: int, seed: int = 7):
    rng = random.Random(seed)
    subjects = [f"субъект{i}" for i in range(max(1, n // 20))]
    places = [f"место{i}" for i in range(max(1, n // 10))]
    return [f"{rng.choice(subjects)} лежит на {rng.choice(places)} номер {i}" for i in range(n)]


def recall_at_k(texts, queries, encoder, k: int, bits: int) -> float:
    vecs = encoder.embed(texts)
    qvecs = encoder.embed(queries)
    flat = VectorIndex(vecs.shape[1])
    lsh = LSHIndex(vecs.shape[1], bits=bits)
    for i, v in enumerate(vecs):
        flat.add(i, v)
        lsh.add(i, v)
    hits = total = 0
    for q in qvecs:
        exact = {key for key, _ in flat.topk(q, k)}
        approx = set(lsh.topk(q, k))
        hits += len(exact & approx)
        total += len(exact)
    return hits / total if total else 0.0


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--queries", type=int, default=50)
    p.add_argument("--k", type=int, default=64)
    p.add_argument("--bits", type=int, default=512)
    p.add_argument("--encoder", choices=["hash", "openai"], default="hash")
    p.add_argument("--url", default="http://127.0.0.1:8091/v1")
    p.add_argument("--model", default="bge-m3")
    p.add_argument("--dim", type=int, default=1024)
    a = p.parse_args(argv)

    enc = HashEncoder(512) if a.encoder == "hash" else OpenAIEmbedder(a.url, a.model, a.dim)
    texts = synthetic_facts(a.n)
    queries = synthetic_facts(a.queries, seed=99)
    r = recall_at_k(texts, queries, enc, a.k, a.bits)
    out = {"gate": "G2", "encoder": a.encoder, "n": a.n, "k": a.k, "bits": a.bits,
           "recall_at_k": round(r, 4), "threshold": GATE, "pass": r >= GATE,
           "note": "синтетические факты; для приёмки прогоните на своих данных"}
    os.makedirs(os.path.join(ROOT, "eval", "results"), exist_ok=True)
    with open(os.path.join(ROOT, "eval", "results", "g2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
