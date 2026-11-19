# -*- coding: utf-8 -*-
"""Экстракция фактов (M2 брифа) без GBNF: промпт + строгая валидация ответа.

Ожидаемый JSON:
  {"entities": [{"name": str, "type": str}],
   "facts": [{"s": str, "r": str, "o": str, "evidence": str, "conf": number}]}

Факт принимается только если его evidence дословно встречается в тексте (иначе span
не может быть валидным по I1). Невалидные факты отбрасываются, остальные сохраняются.
Если JSON не разбирается — ExtractionError, и вызывающий код сохраняет только документ (I8).
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Callable, Dict, List, Optional

ENTITY_TYPES = ("person", "animal", "object", "place", "organization", "concept", "other")

PROMPT = """Извлеки факты из текста. Верни ТОЛЬКО JSON без пояснений и без markdown.
Схема:
{"entities": [{"name": "строка", "type": one of ["person","animal","object","place","organization","concept","other"]}],
 "facts": [{"s": "субъект", "r": "отношение (глагол или связка, например лежит_на)", "o": "объект", "evidence": "ДОСЛОВНАЯ цитата из текста, на которой основан факт", "conf": число от 0 до 1}]}
Правила: evidence должна быть точной подстрокой текста. Не выдумывай факты, которых нет в тексте.
Если фактов нет, верни {"entities": [], "facts": []}.

Текст:
"""


class ExtractionError(RuntimeError):
    pass


def build_prompt(text: str, speaker: Optional[str] = None) -> str:
    head = PROMPT
    if speaker:
        head = (f"Говорящий в тексте — {speaker}. Слова «я», «меня», «мне», «мой» означают {speaker}: "
                f"в поле s и o используй имя «{speaker}», а не местоимение.\n") + head
    return head + text


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    return m.group(1).strip() if m else raw


def parse_extraction(raw: str, text: str) -> Dict[str, List[dict]]:
    """Разобрать и провалидировать ответ модели относительно исходного текста."""
    try:
        data = json.loads(_strip_fences(raw))
    except ValueError as exc:
        raise ExtractionError(f"ответ не является JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtractionError("корень ответа должен быть объектом")

    entities = []
    for e in data.get("entities") or []:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
            continue
        etype = e.get("type") if e.get("type") in ENTITY_TYPES else "other"
        entities.append({"name": e["name"].strip(), "type": etype})

    facts = []
    for f in data.get("facts") or []:
        if not isinstance(f, dict):
            continue
        s, r, o, ev = (f.get(k) for k in ("s", "r", "o", "evidence"))
        if not all(isinstance(x, str) and x.strip() for x in (s, r, o, ev)):
            continue
        if ev not in text:              # дословная проверка: иначе span невалиден
            continue
        conf = f.get("conf", 0.5)
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            conf = 0.5
        facts.append({"s": s.strip(), "r": r.strip(), "o": o.strip(), "evidence": ev,
                      "conf": min(1.0, max(0.0, float(conf)))})
    return {"entities": entities, "facts": facts}


def make_extractor(llm_call: Callable[[str], str], speaker: Optional[str] = None
                   ) -> Callable[[str], Dict[str, List[dict]]]:
    """Собрать экстрактор: промпт → вызов модели → валидация. Одна повторная попытка при ошибке формата.

    speaker — имя пользователя: без него «я» в тексте превращается в субъект «я», и факт не находится по имени.
    """
    def extract(text: str) -> Dict[str, List[dict]]:
        last_err = None
        for _ in range(2):
            raw = llm_call(build_prompt(text, speaker))
            try:
                return parse_extraction(raw, text)
            except ExtractionError as exc:
                last_err = exc
        raise ExtractionError(str(last_err))
    return extract


def openai_chat_caller(base_url: str, model: str, timeout: float = 120.0,
                       temperature: float = 0.0, max_tokens: int = 1024) -> Callable[[str], str]:
    """Вызов /chat/completions без стрима. temperature=0 — детерминированная экстракция."""
    url = base_url.rstrip("/") + "/chat/completions"

    def call(prompt: str) -> str:
        body = json.dumps({"model": model, "temperature": temperature, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"] or ""
    return call
