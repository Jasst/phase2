# -*- coding: utf-8 -*-
"""Гейт G3 брифа: F1 экстракции фактов на размеченном наборе (порог 0.80).

Формат набора (JSONL): {"text": "...", "facts": [{"s": "...", "r": "...", "o": "..."}]}
Сравниваются тройки (s, r, o) без учёта регистра и пробелов по краям.

  python eval/eval_g3.py --gold eval/gold.jsonl --url http://127.0.0.1:8080/v1 --model local
  python eval/eval_g3.py --gold eval/gold.jsonl --predictions preds.jsonl   # уже извлечённые факты

Набор gold_example.jsonl — только пример формата, на нём измерение не делать.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pulsenet.triplex.extract import make_extractor, openai_chat_caller  # noqa: E402

GATE = 0.80


def norm(t):
    return tuple(x.strip().lower() for x in (t["s"], t["r"], t["o"]))


def f1(pred_by_doc, gold_by_doc):
    tp = fp = fn = 0
    for text, gold in gold_by_doc.items():
        g = {norm(t) for t in gold}
        p = {norm(t) for t in pred_by_doc.get(text, [])}
        tp += len(g & p)
        fp += len(p - g)
        fn += len(g - p)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    score = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(score, 4),
            "tp": tp, "fp": fp, "fn": fn}


def load_gold(path):
    gold = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                gold[row["text"]] = row["facts"]
    return gold


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--gold", required=True)
    p.add_argument("--predictions", default="")
    p.add_argument("--url", default="http://127.0.0.1:8080/v1")
    p.add_argument("--model", default="local")
    a = p.parse_args(argv)

    gold = load_gold(a.gold)
    if a.predictions:
        preds = {}
        with open(a.predictions, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    preds[row["text"]] = row["facts"]
    else:
        extract = make_extractor(openai_chat_caller(a.url, a.model))
        preds = {}
        for text in gold:
            try:
                preds[text] = extract(text)["facts"]
            except Exception as exc:          # noqa: BLE001 — сбой документа = нет фактов (I8)
                print(f"экстракция не удалась: {text[:40]}… ({exc})", file=sys.stderr)
                preds[text] = []
    res = f1(preds, gold)
    out = {"gate": "G3 (экстракция F1)", "docs": len(gold), **res, "threshold": GATE,
           "pass": res["f1"] >= GATE,
           "note": "связывание сущностей (acc ≥ 0.90) этим скриптом не измеряется"}
    os.makedirs(os.path.join(ROOT, "eval", "results"), exist_ok=True)
    with open(os.path.join(ROOT, "eval", "results", "g3.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
