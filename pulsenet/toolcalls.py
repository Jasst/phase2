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
from typing import Dict, List, Optional, Tuple

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
MAX_CALLS_PER_STEP = 3             # сколько вызовов за один шаг принимаем
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


def _partial_suffix(text: str, tag: str) -> int:
    """Длина хвоста text, который может оказаться началом tag (теги приходят по частям)."""
    for k in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[-k:]):
            return k
    return 0


class ToolCallFilter:
    """Потоковый фильтр: отдаёт пользователю видимый текст и отделяет блоки <tool_call>.

    feed() возвращает видимую часть очередного куска; finish() — остаток после конца потока.
    Сырые тела блоков лежат в calls_raw. Незакрытый блок тоже попадает туда (на случай обрыва)."""

    def __init__(self):
        self.buf = ""
        self.inside = False
        self.calls_raw: List[str] = []

    def feed(self, text: str) -> str:
        self.buf += text
        out: List[str] = []
        while True:
            if self.inside:
                i = self.buf.find(CLOSE)
                if i < 0:
                    break
                self.calls_raw.append(self.buf[:i])
                self.buf = self.buf[i + len(CLOSE):]
                self.inside = False
            else:
                i = self.buf.find(OPEN)
                if i < 0:
                    keep = _partial_suffix(self.buf, OPEN)
                    out.append(self.buf[:len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                out.append(self.buf[:i])
                self.buf = self.buf[i + len(OPEN):]
                self.inside = True
        return "".join(out)

    def finish(self) -> str:
        rest, self.buf = self.buf, ""
        if self.inside:
            self.inside = False
            self.calls_raw.append(rest)
            return ""
        return rest


def parse_calls(raw_bodies: List[str]) -> List[dict]:
    """Разбирает тела блоков. Ошибка разбора не роняет ход: вернётся поле error для модели."""
    calls: List[dict] = []
    for raw in raw_bodies:
        body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
        try:
            obj = json.loads(body)
        except ValueError:
            calls.append({"name": "", "arguments": {}, "error": "некорректный JSON в <tool_call>"})
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str) or not obj["name"].strip():
            calls.append({"name": "", "arguments": {}, "error": "в <tool_call> нужны поля name и arguments"})
            continue
        args = obj.get("arguments") or {}
        if not isinstance(args, dict):
            calls.append({"name": obj["name"].strip(), "arguments": {}, "error": "arguments должен быть объектом"})
            continue
        calls.append({"name": obj["name"].strip(), "arguments": args})
    return calls


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
