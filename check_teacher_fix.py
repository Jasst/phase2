# -*- coding: utf-8 -*-
"""Проверка разбора ответа учителя. Результат — в check_teacher_fix_result.txt.

Главное правило: рассуждения (<think>…</think>) никогда не попадают в обучение.
"""
import json
from unittest.mock import patch

from pulsenet.teacher import HTTPTeacher, clean_answer


class FakeResp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def call(content):
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    with patch("urllib.request.urlopen", return_value=FakeResp(body)):
        t = HTTPTeacher("http://127.0.0.1:1/v1/chat/completions", "m", timeout=1)
        return t.answer("что ты умеешь?"), t.error


cases = [
    ("закрытые теги + текст", "<think>думаю</think>Я умею учиться на диалогах.", "Я умею учиться на диалогах."),
    ("только закрывающий тег", "мне надо подумать.</think>Сеть учится на примерах.", "Сеть учится на примерах."),
    ("обрезано внутри рассуждения", "<think>мне нужно подумать о том, что", None),
    ("без тегов", "Просто ответ.", "Просто ответ."),
    ("пусто", "", None),
]
lines = []
ok = True
for name, raw, expected in cases:
    got = clean_answer(raw)
    good = (got == expected) or (expected is None and got == "")
    ok &= good
    lines.append(f"{'OK  ' if good else 'FAIL'} {name}: {got!r}")

ans, err = call("<think>мне нужно подумать</think>Я умею учиться на диалогах.")
ok &= ans == "Я умею учиться на диалогах." and err is None
lines.append(f"ответ через HTTP: {ans!r} error={err!r}")

ans, err = call("<think>мне нужно подумать, ответ такой что")
ok &= ans is None and err is not None
lines.append(f"обрезанное рассуждение не становится ответом: ans={ans!r} error={err!r}")

with patch("urllib.request.urlopen", side_effect=OSError("подключение разорвано")):
    t = HTTPTeacher("http://127.0.0.1:9/v1/chat/completions", "m", timeout=1)
    ok &= t.answer("привет") is None and t.error == "подключение разорвано"
lines.append(f"недоступен: error={t.error!r}")

lines.append("ВСЕ СЦЕНАРИИ ОК" if ok else "НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ")
with open("check_teacher_fix_result.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("\n".join(lines))
