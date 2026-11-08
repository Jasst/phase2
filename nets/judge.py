# -*- coding: utf-8 -*-
"""Оценка ответа сети через LLM (судья).

Судья получает вопрос, эталон (подтверждённый пользователем ответ) и мысль сети,
и отвечает числом 0..10. Если LLM недоступна или ответ не разобран, вызывающий код
откатывается на совпадение слов (recall), поэтому система работает и без LLM.
"""
import re

_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def judge_prompt(question, reference, thought):
    return ("Ты оцениваешь ответ небольшой нейросети. Оцени по шкале от 0 до 10, "
            "насколько ответ сети передаёт смысл эталона на данный вопрос. "
            "10 — полностью верно, 0 — бессмысленно или мимо. Ответь ОДНИМ числом.\n\n"
            f"Вопрос: {question}\n"
            f"Эталон: {reference}\n"
            f"Ответ сети: {thought}\n"
            "Оценка:")


def parse_score(reply):
    """0..1 или None, если в ответе нет числа от 0 до 10."""
    if not reply:
        return None
    m = _NUM.search(str(reply).replace(",", "."))
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if v > 10:
        return None
    return v / 10.0
