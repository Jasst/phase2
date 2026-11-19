# -*- coding: utf-8 -*-
"""Учитель — внешняя LLM по OpenAI-совместимому API (LM Studio, llama-server).

Учитель отвечает текстом; сеть дистиллирует этот ответ (sequence-level
дистилляция). Если учитель недоступен или ответил только рассуждениями,
он возвращает None и не падает: сеть не учится на «мыслях» модели.
"""
import json
import re
import urllib.request

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
SYSTEM = ("Отвечай кратко: 1–3 предложения, на языке вопроса, "
          "без markdown, без списков и без рассуждений.")
# Рассуждающие модели (Qwen3 и т.п.) тратят токены на <think>; 300 не хватало,
# и ответ обрезался внутри рассуждения. Запас нужен, иначе ответа не будет.
MAX_TOKENS = 800


def clean_answer(raw):
    """Видимый ответ модели без рассуждений.

    Рассуждения никогда не возвращаются как ответ:
      • есть закрывающий </think> — берём текст после последнего него;
      • есть открывающий <think>, но нет закрывающего — рассуждение обрезано,
        видимого ответа нет (возвращаем пустую строку);
      • тегов нет — весь текст и есть ответ.
    """
    text = raw or ""
    low = text.lower()
    if "</think>" in low:
        text = text[low.rfind("</think>") + len("</think>"):]
    elif "<think>" in low:
        return ""
    return _THINK.sub("", text).strip()


class Teacher:
    error = None

    def answer(self, question):
        return None


class HTTPTeacher(Teacher):
    def __init__(self, url, model, timeout=120):
        self.url = url
        self.model = model
        self.timeout = timeout
        self.error = None

    def answer(self, question):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": question}],
            "temperature": 0.3,
            "max_tokens": MAX_TOKENS,
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"] or ""
        except (OSError, KeyError, IndexError, TypeError, ValueError) as exc:
            self.error = str(exc)
            return None
        self.error = None
        text = clean_answer(raw)
        if not text and raw.strip():
            self.error = "модель вернула только рассуждения: ответ не закончен (мало max_tokens)"
        return text or None
