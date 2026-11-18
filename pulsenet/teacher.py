# -*- coding: utf-8 -*-
"""Учитель — внешняя LLM по OpenAI-совместимому API (LM Studio, llama-server).

Учитель отвечает текстом; сеть дистиллирует этот ответ (sequence-level
дистилляция). Если учитель недоступен, он возвращает None и не падает:
сеть продолжает учиться по диалогу и по /good, /bad, /teach.
"""
import json
import re
import urllib.request

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
SYSTEM = ("Отвечай кратко: 1–3 предложения, на языке вопроса, "
          "без markdown, без списков и без рассуждений.")


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
            "max_tokens": 300,
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
        text = _THINK.sub("", raw).strip()
        if not text and raw.strip():
            # Модель ответила, но только рассуждениями (без видимого текста):
            # не выбрасываем ответ целиком — сеть всё равно может использовать его как цель.
            text = raw.strip()
        return text or None
