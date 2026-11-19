# -*- coding: utf-8 -*-
"""Сравнение режимов ответа на одном отложенном наборе: respond, plan, voice.

Задача без LLM: из отложенного предложения первая половина — «вопрос», вторая —
эталон. Сеть обучается только на обучающей части корпуса. Метрика — доля
содержательных слов эталона в ответе (keyword_recall). Это прокси, не смысл.

  python experiments/modes/compare.py
"""
import glob
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from pulsenet import BrainNet, Config  # noqa: E402
from pulsenet.planner import Planner  # noqa: E402
from pulsenet.reasoning import Reasoner  # noqa: E402
from pulsenet.vocab import split_sentences, tokenize  # noqa: E402


def load_sentences(corpus_dir):
    sents = []
    for f in sorted(glob.glob(os.path.join(corpus_dir, "*.txt"))):
        with open(f, encoding="utf-8-sig") as fh:
            sents += [s for s in split_sentences(fh.read()) if len(tokenize(s)) >= 4]
    seen, out = set(), []
    for s in sents:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def split(sents, test_frac=0.25, seed=0):
    items = list(sents)
    random.Random(seed).shuffle(items)
    k = max(1, int(len(items) * test_frac))
    return items[k:], items[:k]


def make_task(sentence):
    t = tokenize(sentence)
    h = len(t) // 2
    return " ".join(t[:h]), " ".join(t[h:])


def recall(reply, gold):
    need = {w for w in tokenize(gold) if len(w) > 2}
    if not need:
        return 0.0
    return len(need & set(tokenize(reply or ""))) / len(need)


def train_net(train_sents, seed=0, cfg_over=None):
    net = BrainNet(Config(seed=seed, **(cfg_over or {})))
    net.pretrain("\n".join(train_sents), epochs=3)
    return net


def evaluate(net, test_sents, modes=("respond", "plan", "voice")):
    """Каждый режим — на свежей копии сети: respond и plan меняют мысль и слепки,
    и порядок режимов не должен влиять на результат."""
    import copy
    scores = {}
    for m in modes:
        work = copy.deepcopy(net)
        vals = []
        for s in test_sents:
            q, gold = make_task(s)
            if m == "respond":
                ans = work.respond(q, temp=0).text
            elif m == "plan":
                ans = Planner(work).think(q).answer
            else:
                ans = Reasoner(work).inner_voice(q).text
            vals.append(recall(ans, gold))
        scores[m] = round(sum(vals) / len(vals), 4) if vals else None
    return scores


def main():
    sents = load_sentences(os.path.join(ROOT, "corpus"))
    train, test = split(sents)
    net = train_net(train)
    res = evaluate(net, test)
    print(f"обучение: {len(train)} предложений, проверка: {len(test)}")
    for m, v in res.items():
        print(f"  {m:8s} keyword_recall = {v}")
    out = os.path.join(HERE, "..", "out", "compare.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"train": len(train), "test": len(test), "scores": res}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
