import json
from unittest.mock import patch
from pulsenet.teacher import HTTPTeacher, _THINK


class FakeResp:
    def __init__(self, body, status=200):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


lines = []
lines.append("think-regex pattern: " + repr(_THINK.pattern))

# Как ведут себя теги в вырезке — паттерн <?.*?... вырезает блоки вида ...текст...
samples = [
    "думаю тут <<рассуждения>> ответ тут",  # не вырезается (не заканчивается на ...)
    "...я думаю что сеть учится...",           # вырезается целиком
    "...мне нужно подумать. ответ: четыре...",   # вырезается, остаётся пустота
    "без тегов просто ответ",                   # без изменений
]
for s in samples:
    lines.append("in:  " + repr(s))
    lines.append("out: " + repr(_THINK.sub("", s).strip()))


def call(content):
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    with patch("urllib.request.urlopen", return_value=FakeResp(body)):
        t = HTTPTeacher("http://127.0.0.1:1/v1/chat/completions", "qwopus3.5-9b-coder-mtp", timeout=1)
        return t.answer("что ты умеешь ?"), t.error


# 1) только рассуждения в тегах ... — раньше возвращалось None с error=None
ans, err = call("...мне нужно подумать. сеть учится на диалогах...")
lines.append("только теги: ans=" + repr(ans) + " error=" + repr(err))

# 2) рассуждения + текст после них — текст остаётся
ans, err = call("...мне нужно подумать. я умею общаться и учиться на диалогах")
lines.append("теги+текст: ans=" + repr(ans) + " error=" + repr(err))

# 3) пустой контент — ошибка должна быть понятной
body = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
with patch("urllib.request.urlopen", return_value=FakeResp(body)):
    t2 = HTTPTeacher("http://127.0.0.1:1/v1/chat/completions", "m", timeout=1)
    ans2 = t2.answer("привет")
lines.append("пусто: ans=" + repr(ans2) + " error=" + repr(t2.error))

# 4) недоступный учитель — ошибка заполнена
with patch("urllib.request.urlopen", side_effect=OSError("подключение разорвано")):
    t4 = HTTPTeacher("http://127.0.0.1:9/v1/chat/completions", "m", timeout=1)
    ans4 = t4.answer("привет")
lines.append("недоступен: ans=" + repr(ans4) + " error=" + repr(t4.error))

# Проверки
ok = True
if not (ans is not None and err is None):
    lines.append("FAIL: только теги")
    ok = False
if not (ans and "умею" in ans):
    lines.append("FAIL: теги+текст")
    ok = False
if not (ans2 is None and t2.error is None):
    lines.append("FAIL: пусто — error должен быть falsy")
    ok = False
if not (ans4 is None and t4.error == "подключение разорвано"):
    lines.append("FAIL: недоступен")
    ok = False

lines.append("ВСЕ СЦЕНАРИИ ОК" if ok else "НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ")
open("check_teacher_fix_result.txt", "w", encoding="utf-8").write("\n".join(lines))
