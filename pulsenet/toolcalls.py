# -*- coding: utf-8 -*-
"""Протокол вызова инструментов моделью — текстовый, не зависит от бэкенда.

Модель пишет блок <tool_call>{"name": "...", "arguments": {...}}</tool_call> и останавливается.
Приложение исполняет вызов и отдаёт результат в теге <tool_result name="...">...</tool_result>.
Так работает любой OpenAI-совместимый сервер (llama-server, LM Studio), даже без поддержки
нативного function calling. Инструменты описаны в tools.py.

Команды в чате (`/search`, `/fetch`, `/memory`, `/remember`) выполняются приложением напрямую:
модели передаётся уже готовый результат, а не просьба вызвать инструмент.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Iterable, List, Optional, Tuple

OPEN = "<tool_call>"
CLOSE = "</tool_call>"


def _env_int(env: dict, key: str, default: int, lo: int = 1, hi: int = 100) -> int:
    try:
        return max(lo, min(hi, int(str(env.get(key, "")).strip() or default)))
    except ValueError:
        return default


def agent_limits(env: Optional[dict] = None) -> dict:
    """Лимиты агентного цикла. env=None — текущее окружение (значения по умолчанию 6/8/12)."""
    env = os.environ if env is None else env
    rounds = _env_int(env, "AGENT_MAX_ROUNDS", 6, 1, 7)
    return {
        "max_rounds": rounds,
        "hard_cap": _env_int(env, "AGENT_HARD_CAP", max(rounds + 2, 8), rounds, 12),
        "max_calls": _env_int(env, "AGENT_MAX_CALLS", 12, 1, 40),
        "reflection_budget": _env_int(env, "AGENT_REFLECTION_BUDGET", 2, 0, 10),
        "result_chars": _env_int(env, "TOOL_RESULT_CONTEXT_CHARS", 1200, 200, 20000),
        # Отдельный лимит генерации на шаг агента: 1024 не хватало на JSON <tool_call>
        "agent_max_tokens": _env_int(env, "AGENT_MAX_TOKENS", 2048, 512, 8192),
        # Потолки системного промпта и всего запроса, чтобы локальная модель не обрезалась
        "system_max": _env_int(env, "AGENT_SYSTEM_MAX_CHARS", 6000, 1000, 30000),
        "context_max": _env_int(env, "AGENT_CONTEXT_MAX_CHARS", 20000, 4000, 60000),
        "native_max": _env_int(env, "TOOL_NATIVE_MAX", 10, 3, 30),
        "image_timeout": _env_int(env, "MCP_TOOL_TIMEOUT_IMAGE", 300, 30, 900),
        # параллельные вызовы в одном шаге: сколько принимаем и сколько выполняем одновременно
        "calls_per_step": _env_int(env, "AGENT_CALLS_PER_STEP", 4, 1, 8),
        "parallel": _env_int(env, "AGENT_PARALLEL", 4, 1, 8),
        # субагент: раундов на задачу и одновременных LLM-запросов субагентов
        "sub_rounds": _env_int(env, "SUBAGENT_MAX_ROUNDS", 3, 1, 6),
        "llm_parallel": _env_int(env, "AGENT_LLM_PARALLEL", 1, 1, 4),
        # пауза после сбоя сервера памяти: ход не ждёт его каждый раз (сек)
        "mcp_cooldown": _env_int(env, "MCP_COOLDOWN_SECONDS", 45, 0, 600),
    }


LIMITS = agent_limits()
MAX_STEPS = LIMITS["max_rounds"]   # сколько раз модель может вызвать инструменты за один ответ
MAX_CALLS_PER_STEP = LIMITS["calls_per_step"]   # сколько вызовов за один шаг принимаем (как в AGENT_CALLS_PER_STEP)
MAX_RESULT_CHARS = 6000            # потолок текста результата, который уходит в контекст модели

# Команды: ключ — имя без слэша, usage — подсказка, local — выполняется только в интерфейсе.
COMMANDS: Dict[str, dict] = {
    "search": {"usage": "/search запрос — поиск в интернете и ответ по источникам",
               "hint": "поиск в интернете", "local": False},
    "fetch": {"usage": "/fetch адрес — прочитать страницу и ответить по ней",
              "hint": "прочитать страницу по адресу", "local": False},
    "memory": {"usage": "/memory запрос — найти в памяти факты и фрагменты",
               "hint": "поиск по памяти", "local": False},
    "remember": {"usage": "/remember текст — сохранить в память",
                 "hint": "сохранить в память", "local": False},
    "chatmem": {"usage": "/chatmem — загрузить текущий чат в память как документ и извлечь факты",
                "hint": "чат → память (с дедупликацией)", "local": True},
    "graph": {"usage": "/graph — открыть карту нейронов памяти",
              "hint": "карта нейронов", "local": True},
    "new": {"usage": "/new — новый чат", "hint": "новый чат", "local": True},
}

_COMMAND_RE = re.compile(r"^/([a-z]+)(?:\s+(.*))?$", re.S | re.I)


def parse_command(message: str) -> Optional[Tuple[str, str]]:
    """('search', 'текст запроса') для «/search текст запроса»; None — обычное сообщение.
    Неизвестные команды считаются обычным текстом (например, пути вида /usr/bin)."""
    m = _COMMAND_RE.match((message or "").strip())
    if not m:
        return None
    name = m.group(1).lower()
    if name not in COMMANDS or COMMANDS[name]["local"]:
        return None
    return name, (m.group(2) or "").strip()


def _tag_name(tag: str) -> str:
    m = re.match(r"<\s*/?\s*([A-Za-z_][\w-]*)", tag or "")
    return m.group(1).lower() if m else ""


# Основной аргумент инструмента для тегового вызова: <web_search>запрос</web_search>.
# Для MCP-инструментов с другим именем аргумента app передаёт свою карту primary.
_XML_PRIMARY = {
    "memory_search": "query", "memory_remember": "text", "web_search": "queries",
    "web_fetch": "url", "skill_read": "name", "skill_write": "name", "skill_patch": "name",
    "note_write": "title", "note_append": "title", "note_read": "title", "note_list": "",
    "image_generate": "prompt", "subagent": "task", "todo_write": "items",
}


def _loose_json(text: str):
    """JSON из тела тега, даже если вокруг него ```json или лишний текст. None — не JSON."""
    body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", (text or "").strip(), flags=re.I)
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(body):
        if ch in "[{":
            try:
                value, _ = decoder.raw_decode(body, i)
            except ValueError:
                continue
            return value
    return None


def _xml_call_body(name: str, raw: str, primary: Optional[Dict[str, str]] = None) -> str:
    """Тело <web_search>...</web_search> → канонический JSON вида {"name", "arguments"}.

    Модели пишут по-разному: массив запросов, объект аргументов, просто строку.
    Приводим значение к основному аргументу инструмента, чтобы вызов не пропал."""
    text = str(raw or "").strip()
    value = _loose_json(text) if text else None
    args_map = dict(_XML_PRIMARY)
    args_map.update(primary or {})
    arg = args_map.get(name) or "query"
    if isinstance(value, dict):
        args = value
    elif value is None:
        args = {arg: text} if text and arg else {}
    elif arg == "queries":
        items = value if isinstance(value, list) else [value]
        queries = [str(v).strip() for v in items if str(v).strip()]
        args = {"queries": queries[:3]} if queries else {}
    elif isinstance(value, list) and arg == "query":
        queries = [str(v).strip() for v in value if str(v).strip()]
        args = {"query": " ".join(queries)} if queries else {}
    elif isinstance(value, list) and name == "todo_write":
        args = {"items": [{"text": str(v), "status": "pending"} if isinstance(v, str) else v
                          for v in value]}
    elif isinstance(value, list):
        args = {arg: value}
    else:
        args = {arg: value if isinstance(value, str) else str(value)}
    return json.dumps({"name": name, "arguments": args}, ensure_ascii=False)


class ToolCallFilter:
    """Потоковый фильтр: отдаёт пользователю видимый текст и отделяет вызовы инструментов.

    Понимает канонический <tool_call>{"name": ...}</tool_call> и теговый вариант
    <web_search>...</web_search> (имя тега = имя доступного инструмента) — так локальные
    модели тоже вызывают инструменты. feed() возвращает видимую часть очередного куска,
    finish() — остаток после конца потока. Тела вызовов лежат в calls_raw уже каноническим
    JSON, поэтому parse_calls разбирает оба варианта одинаково. Незакрытый блок тоже
    попадает туда (на случай обрыва по лимиту токенов)."""

    def __init__(self, names: Iterable[str] = (), primary: Optional[Dict[str, str]] = None):
        self.buf = ""
        self.inside = False
        self.open_name = ""          # имя тега тегового вызова; "" — канонический <tool_call>
        self._open_tag = ""
        self.names = {str(n) for n in (names or ()) if n}
        self.primary = dict(primary or {})
        self.calls_raw: List[str] = []
        self.fake_results = 0        # сколько поддельных <tool_result> модель написала сама

    def feed(self, text: str) -> str:
        self.buf += text or ""
        out: List[str] = []
        while self.buf:
            if self.inside:
                if not self._consume_call():
                    break
            elif not self._consume_text(out):
                break
        return "".join(out)

    def finish(self) -> str:
        rest, self.buf = self.buf, ""
        if self.inside:
            name, self.open_name = self.open_name, ""
            self.inside = False
            self.calls_raw.append(_xml_call_body(name, rest, self.primary) if name else rest)
            return ""
        return rest

    def _consume_text(self, out: List[str]) -> bool:
        """False — данных мало, ждём; True — нашли начало вызова."""
        while True:
            i = self.buf.find("<")
            if i < 0:
                out.append(self.buf)
                self.buf = ""
                return False
            if i:
                out.append(self.buf[:i])
                self.buf = self.buf[i:]
            j = self.buf.find(">")
            if j < 0:
                if self._possible_tag(self.buf) or self._possible_tool_result(self.buf):
                    return False
                out.append(self.buf[0])          # не тег инструмента — обычный текст
                self.buf = self.buf[1:]
                continue
            tag = self.buf[:j + 1]
            if tag.startswith("</"):
                out.append(tag)
                self.buf = self.buf[j + 1:]
                continue
            name = _tag_name(tag)
            if name == "tool_result":
                consumed = self._consume_tool_result()
                if consumed is None:
                    return False
                if consumed:
                    continue
            if name == "tool_call" or name in self.names:
                self._open_tag = tag
                self.open_name = "" if name == "tool_call" else name
                self.inside = True
                self.buf = self.buf[j + 1:]
                return True
            out.append(tag)
            self.buf = self.buf[j + 1:]

    def _consume_call(self) -> bool:
        close = "</" + (self.open_name or "tool_call") + ">"
        i = self.buf.lower().find(close)
        if i < 0:
            return False
        body, self.buf = self.buf[:i], self.buf[i + len(close):]
        self.inside = False
        name, self.open_name = self.open_name, ""
        tag, self._open_tag = self._open_tag, ""
        if name:
            self.calls_raw.append(_xml_call_body(name, body, self.primary))
        else:
            forced = re.search(r"""(?:name|tool)\s*=\s*["']?([A-Za-z_][\w-]*)""", tag or "", re.I)
            if forced:
                obj = _load_call_body(body)
                if isinstance(obj, dict) and not any(k in obj for k in _NAME_KEYS):
                    body = json.dumps({"name": forced.group(1).lower(), "arguments": obj},
                                      ensure_ascii=False)
            self.calls_raw.append(body)
        return True

    def _consume_tool_result(self) -> Optional[bool]:
        """Убирает поддельный <tool_result>...</tool_result>, написанный моделью.
        Возвращает True — тег проглочен, False — не tool_result, None — ждём конец тега/блока."""
        m = re.match(r"<\s*tool_result\b[^>]*>", self.buf, re.I)
        if not m:
            return False
        end = self.buf.lower().find("</tool_result>", m.end())
        if end < 0:
            return None
        self.buf = self.buf[end + len("</tool_result>"):]
        self.fake_results += 1
        return True

    def _possible_tool_result(self, tail: str) -> bool:
        m = re.match(r"^<\s*/?\s*([A-Za-z_][\w-]*)", tail)
        if m:
            return "tool_result".startswith(m.group(1).lower())
        return bool(re.match(r"^<\s*/?\s*$", tail))

    def _possible_tag(self, tail: str) -> bool:
        """Похож ли хвост на начало тега известного инструмента (теги приходят по частям)."""
        candidates = ["tool_call"] + sorted(self.names)
        m = re.match(r"^<\s*/?\s*([A-Za-z_][\w-]*)", tail)
        if m:
            # Имя может быть ещё не дописано: <too → tool_call, <web → web_search.
            name = m.group(1).lower()
            return any(c.lower().startswith(name) for c in candidates)
        return bool(re.match(r"^<\s*/?\s*$", tail))


_NAME_KEYS = ("name", "tool", "tool_name")
_ARG_KEYS = ("arguments", "parameters", "args", "input")


def _load_call_body(raw: str):
    """JSON из тела <tool_call>. Допускаем обёртку ```json и лишний текст вокруг объекта:
    локальные модели это регулярно делают, и раньше такой вызов сразу становился ошибкой."""
    body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", (raw or "").strip())
    try:
        return json.loads(body)
    except ValueError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(body[start:end + 1])
        except ValueError:
            return None
    return None


def _call_args(value) -> Optional[dict]:
    """arguments как объект. Строка с JSON тоже принимается (так отдают некоторые серверы).
    None — значение не разбирается в объект."""
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _xmlish_call(raw: str) -> Optional[dict]:
    """Фолбэк для моделей, которые пишут <name>..</name><arguments>..</arguments>
    внутри <tool_call> вместо JSON. Возвращает dict вызова или None."""
    text = str(raw or "").strip()
    if "<" not in text or ">" not in text:
        return None

    def grab(keys: Tuple[str, ...]) -> Optional[str]:
        for key in keys:
            m = re.search(rf"<\s*{re.escape(key)}\s*>(.*?)</\s*{re.escape(key)}\s*>", text, re.S | re.I)
            if m:
                return m.group(1).strip()
        return None

    name = grab(_NAME_KEYS)
    if not name:
        return None
    arg_text = grab(_ARG_KEYS)
    if arg_text is None:
        # Тело похоже на XML-вызов с именем, но без блока аргументов — считаем пустым объектом.
        return {"name": name, "arguments": {}}
    args = _loose_json(arg_text)
    if args is None:
        # Не JSON — оставляем строку как есть; дальше _call_args/валидация решит.
        return {"name": name, "arguments": arg_text}
    return {"name": name, "arguments": args}


_CALL_EXAMPLE = '<tool_call>{"name": "web_search", "arguments": {"queries": ["запрос"]}}</tool_call>'


def _format_error(problem: str, raw: str) -> str:
    """Ошибка для модели: что не так, что она написала и как должно быть."""
    snip = " ".join(str(raw or "").split())[:160]
    return f"{problem}. Получено: {snip!r}. Правильный формат: {_CALL_EXAMPLE}"


def parse_calls(raw_bodies: List[str], known: Optional[Iterable[str]] = None) -> List[dict]:
    """Разбирает тела блоков. Ошибка разбора не роняет ход: вернётся поле error для модели.
    Принимаются синонимы полей (tool/parameters/args), формат OpenAI {"function": {...}}.
    known — имена доступных инструментов: если имени нет, но "type" совпадает с известным
    инструментом (модель пишет {"type": "subagent", ...}), берём его как имя; остальные поля — аргументы."""
    known_set = set(known or ())
    calls: List[dict] = []
    for raw in raw_bodies:
        obj = _load_call_body(raw)
        xmlish = _xmlish_call(raw)
        if not isinstance(obj, dict):
            obj = xmlish
        if not isinstance(obj, dict):
            calls.append({"name": "", "arguments": {}, "error": _format_error("некорректный JSON в <tool_call>", raw)})
            continue
        for wrapper in ("function", "tool_call"):
            if isinstance(obj.get(wrapper), dict):
                obj = obj[wrapper]
        name = next((obj[k].strip() for k in _NAME_KEYS
                     if isinstance(obj.get(k), str) and obj[k].strip()), "")
        # _load_call_body может принять XML-подобный <name>...</name> как валидный JSON-объект
        # без name/arguments. В этом случае используем XML-фолбэк целиком.
        if not name and isinstance(xmlish, dict):
            obj = xmlish
            name = str(xmlish.get("name") or "").strip()
        type_alias = False
        if not name and isinstance(obj.get("type"), str) and obj["type"].strip() in known_set:
            name = obj["type"].strip()
            type_alias = True
        if not name and known_set:
            # имя ключом: {"web_search": {"queries": [...]}}
            for key, value in obj.items():
                if key in known_set and isinstance(value, dict):
                    name, obj = key, {"arguments": value}
                    break
        if not name:
            calls.append({"name": "", "arguments": {}, "error": _format_error(
                "в <tool_call> нужны поля name и arguments", raw)})
            continue
        key = next((k for k in _ARG_KEYS if k in obj), None)
        if key is None and type_alias:
            args = {k: v for k, v in obj.items() if k != "type"}
        else:
            args = _call_args(obj.get(key) if key else None)
        if args is None:
            calls.append({"name": name, "arguments": {}, "error": "arguments должен быть объектом"})
            continue
        calls.append({"name": name, "arguments": args})
    return calls


def find_bare_call_span(text: str, known: Iterable[str]) -> Optional[Tuple[dict, int, int]]:
    """(вызов, начало, конец) голого JSON известного инструмента в тексте. None, если нет."""
    known_set = set(known or ())
    if not known_set or not text:
        return None
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        start = text.find("{", pos)
        if start < 0:
            return None
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            pos = start + 1
            continue
        if isinstance(obj, dict):
            call = parse_calls([json.dumps(obj, ensure_ascii=False)], known_set)[0]
            if not call.get("error") and call["name"] in known_set:
                return call, start, end
        pos = end


def find_bare_call(text: str, known: Iterable[str]) -> Optional[dict]:
    """Голый JSON вызова известного инструмента в тексте ответа (без <tool_call>). None, если нет."""
    found = find_bare_call_span(text, known)
    return found[0] if found else None


def _shrink_json(value, left: int):
    """Сжать структуру так, чтобы json.dumps всё ещё давал валидный JSON."""
    if isinstance(value, str):
        if len(value) <= left:
            return value
        keep = max(0, left - len("…(обрезано)"))
        return value[:keep] + "…(обрезано)"
    if isinstance(value, dict):
        out: Dict[str, object] = {}
        for key, item in value.items():
            k = str(key)[:80]
            out[k] = _shrink_json(item, max(40, left // max(1, len(value))))
            if len(json.dumps(out, ensure_ascii=False)) > left:
                out.pop(k, None)
                out["_truncated"] = True
                break
        return out
    if isinstance(value, list):
        out_list: List[object] = []
        room = min(len(value), 20)
        for item in value[:room]:
            out_list.append(_shrink_json(item, max(40, left // max(1, room))))
            if len(json.dumps(out_list, ensure_ascii=False)) > left:
                out_list.append("…(обрезано)")
                break
        return out_list
    return value


def compact_model_text(text: str, limit: Optional[int] = None) -> Tuple[str, bool, bool]:
    """Уложить результат инструмента в лимит контекста, сохранив валидный JSON.

    Возвращает (текст, было_ли_обрезание, был_ли_JSON). Простое «отрезали хвост»
    превращало ответ MCP в битый JSON и модель тратила раунд на разбор ошибки.
    """
    cap = max(40, int(limit or MAX_RESULT_CHARS))
    body = str(text or "")
    if len(body) <= cap:
        stripped = body.strip()
        is_json = stripped[:1] in ("{", "[")
        return body, False, is_json
    stripped = body.strip()
    if stripped[:1] in ("{", "["):
        try:
            data = json.loads(stripped)
        except ValueError:
            pass
        else:
            shrunk = _shrink_json(data, max(80, cap - 40))
            compact = json.dumps(shrunk, ensure_ascii=False)
            if len(compact) <= cap:
                return compact, True, True
    return body[:cap] + "\n…(обрезано)", True, False


def tool_result_block(name: str, text: str, limit: Optional[int] = None) -> str:
    """Текст результата в том виде, в котором модель его получает.

    limit — пер-вызов потолок контекста (AGENT_* настройки); по умолчанию MAX_RESULT_CHARS."""
    cap = int(limit or MAX_RESULT_CHARS)
    body, _, _ = compact_model_text(text, cap)
    return f'<tool_result name="{name}">\n{body}\n</tool_result>'
