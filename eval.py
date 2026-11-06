# -*- coding: utf-8 -*-
"""Eval качества ответов: метрики по набору эталонных вопросов.

Формат tests/eval_set.jsonl (одна строка — один пример):
  {"q": "как ты?", "keys": ["хорошо", "в порядке"], "kind": "small_talk"}
  keys — слова, любое из которых должно встретиться в ответе (пусто = не проверяется).

Два режима:
  python eval.py --outputs run.jsonl        # готовые ответы: {"q": ..., "a": ...} на строку
  python eval.py --run                      # прогнать LM (lm_backend) сам

Результат дописывается в eval_log.jsonl: одна строка на запуск, чтобы видеть динамику.
Метрики:
  leak_rate   — доля ответов с утечкой служебного текста (рассуждение, подсказки, «черновик»)
  empty_rate  — доля пустых ответов
  hit_rate    — доля ответов с хотя бы одним ключевым словом (только примеры с keys)
  mean_words  — средняя длина ответа в словах
"""
import argparse
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEAK = re.compile(
    r"thinking process|analyze the request|черновик|мысль сети|проверь|вопрос:|"
    r"служебн|/no_think|<think>|role:|constraints:", re.I)


def load_jsonl(path):
    with open(path, encoding="utf-8-sig") as f:
        return [json.loads(line) for line in f if line.strip()]


def score(items, answers):
    """items — эталон, answers — {q: ответ}. Возвращает словарь метрик."""
    n = len(items)
    leaks = empties = words = 0
    keyed = hits = 0
    per_item = []
    for it in items:
        a = (answers.get(it["q"]) or "").strip()
        leak = bool(LEAK.search(a))
        empty = not a
        leaks += leak
        empties += empty
        words += len(a.split())
        hit = None
        if it.get("keys"):
            keyed += 1
            hit = any(k.lower() in a.lower() for k in it["keys"])
            hits += bool(hit)
        per_item.append({"q": it["q"], "a": a, "leak": leak, "empty": empty, "hit": hit})
    return {
        "n": n,
        "leak_rate": round(leaks / n, 3) if n else 0.0,
        "empty_rate": round(empties / n, 3) if n else 0.0,
        "hit_rate": round(hits / keyed, 3) if keyed else None,
        "mean_words": round(words / n, 2) if n else 0.0,
        "items": per_item,
    }


def run_lm(items):
    sys.path.insert(0, HERE)
    from lm_backend import LMBackend
    lm = LMBackend()
    return {it["q"]: lm.generate(it["q"], remember=False) for it in items}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=os.path.join(HERE, "tests", "eval_set.jsonl"))
    ap.add_argument("--outputs", help="jsonl с полями q и a")
    ap.add_argument("--run", action="store_true", help="прогнать LM напрямую")
    ap.add_argument("--tag", default="", help="метка версии, например v5d")
    ap.add_argument("--log", default=os.path.join(HERE, "eval_log.jsonl"))
    ap.add_argument("--show", action="store_true", help="печатать ответы по пунктам")
    args = ap.parse_args()

    items = load_jsonl(args.set)
    if args.outputs:
        answers = {r["q"]: r.get("a", "") for r in load_jsonl(args.outputs)}
    elif args.run:
        answers = run_lm(items)
    else:
        ap.error("укажите --outputs или --run")

    res = score(items, answers)
    row = {k: v for k, v in res.items() if k != "items"}
    row.update(ts=datetime.datetime.now().isoformat(timespec="seconds"), tag=args.tag)
    with open(args.log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(json.dumps(row, ensure_ascii=False, indent=2))
    if args.show:
        for it in res["items"]:
            flag = "LEAK " if it["leak"] else ("EMPTY" if it["empty"] else "     ")
            print(f"[{flag}] {it['q']} -> {it['a'][:160]}")


if __name__ == "__main__":
    main()
