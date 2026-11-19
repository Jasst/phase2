# -*- coding: utf-8 -*-
"""Проверка инструментов масштабирования на заглушке учителя (LLM не нужна).
Запуск: python -m pytest experiments/scaling/test_scaling.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import curve  # noqa: E402
import generate_pairs as gp  # noqa: E402

CORPUS = ["Нейронная сеть учится на примерах из текста.",
          "Связи между словами усиливаются при совместной встрече.",
          "Температура делает ответы разнообразнее и смелее.",
          "Память хранит опыт сети между запусками программы.",
          "Огонь согревает воду и доводит её до кипения."]


def fake_ask(prompt):
    sentence = prompt.split("Предложение: ", 1)[1]
    return "Вопрос: о чём говорит фраза «" + sentence.split()[0] + "»"


def test_generate_writes_pairs_and_is_idempotent(tmp_path):
    out = tmp_path / "pairs.jsonl"
    first = gp.generate(CORPUS, fake_ask, str(out))
    assert first["новых"] == len(CORPUS)
    again = gp.generate(CORPUS, fake_ask, str(out))
    assert again["новых"] == 0                     # повторный запуск ничего не дублирует
    rows = [json.loads(l) for l in open(out, encoding="utf-8")]
    assert all(r["question"].endswith("?") and r["answer"] in CORPUS for r in rows)


def test_bad_questions_are_skipped(tmp_path):
    out = tmp_path / "pairs.jsonl"
    stats = gp.generate(CORPUS, lambda p: "", str(out))
    assert stats["пропущено"] == len(CORPUS) and stats["новых"] == 0


def test_teacher_reasoning_never_becomes_question():
    assert gp.clean_question("<think>думаю о том, как</think>") is None
    assert gp.clean_question("Вопрос: Что такое память") == "Что такое память?"


def test_split_is_deterministic_and_disjoint():
    pairs = [{"id": f"id{i:03d}", "question": f"q{i}?", "answer": "a"} for i in range(50)]
    tr1, te1 = curve.split(pairs)
    tr2, te2 = curve.split(pairs)
    assert [p["id"] for p in te1] == [p["id"] for p in te2]
    assert not set(p["id"] for p in tr1) & set(p["id"] for p in te1)
    assert len(tr1) + len(te1) == 50


def test_curve_runs_and_reports_rows(tmp_path):
    pairs = [{"id": gp.sentence_id(s), "question": fake_ask("Предложение: " + s), "answer": s} for s in CORPUS * 4]
    # id должны быть уникальны для разбиения; повторы допустимы в тесте структуры
    for i, p in enumerate(pairs):
        p["id"] = f"{p['id']}-{i}"
    train, test = curve.split(pairs, 0.25)
    rows = curve.run_curve(train, test, curve.checkpoints_for(len(train)))
    assert rows and rows[0]["pairs"] <= rows[-1]["pairs"]
    assert all(0.0 <= r["keyword_recall"] <= 1.0 for r in rows)


def test_keyword_recall_counts_only_content_words():
    assert curve.keyword_recall("память хранит опыт", "Память хранит опыт сети") == 0.75
    assert curve.keyword_recall("", "а и в") == 0.0   # одни короткие слова — нечего проверять
