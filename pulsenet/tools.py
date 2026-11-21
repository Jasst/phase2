# -*- coding: utf-8 -*-
"""Инструменты чата: память и интернет. Те же операции, что у MCP-сервера pulsenet-unified
(triplex_search, web_search, web_fetch), но исполняются внутри приложения.

Каждый инструмент описан JSON-схемой в SPECS. Модель вызывает их текстовым протоколом
(см. toolcalls.py); результат для модели — короткий читаемый текст с номерами источников,
а для интерфейса — подпись и список источников.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .toolcalls import COMMANDS

MEMORY_TOOLS = ("memory_search", "memory_remember")
WEB_TOOLS = ("web_search", "web_fetch")

SPECS: Dict[str, dict] = {
    "memory_search": {
        "description": "Поиск по памяти пользователя: факты и фрагменты документов с источниками.",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"},
                                      "k": {"type": "integer", "minimum": 1, "maximum": 20}},
                       "required": ["query"]},
    },
    "memory_remember": {
        "description": "Сохранить в память важный факт или заметку. Факты извлекаются в фоне.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]},
    },
    "web_search": {
        "description": "Поиск в интернете до 3 запросов сразу. Возвращает пронумерованные источники "
                       "с выдержками; в ответе ссылайся на них как [1], [2].",
        "parameters": {"type": "object",
                       "properties": {"queries": {"type": "array", "items": {"type": "string"},
                                                  "minItems": 1, "maxItems": 3}},
                       "required": ["queries"]},
    },
    "web_fetch": {
        "description": "Прочитать публичную веб-страницу по адресу http(s).",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]},
    },
}


@dataclass
class Outcome:
    ok: bool
    text: str                      # то, что увидит модель
    label: str                     # короткая подпись для интерфейса
    detail: str = ""
    sources: List[dict] = field(default_factory=list)


def _clip(value: Any, n: int) -> str:
    s = str(value or "").replace("\r", " ").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _web_module():
    """Модуль веб-поиска. Импорт ленивый: без httpx чат работает, просто без интернета."""
    try:
        from .unified_mcp import web          # noqa: WPS433 (импорт внутри функции намеренно)
        return web
    except ImportError:
        return None


class ChatTools:
    """Набор инструментов на один ход чата. Нумерация источников сквозная внутри хода."""

    def __init__(self, recall: Callable[[str, int], dict], remember: Callable[[str], dict],
                 web: Any = None):
        self._recall = recall
        self._remember = remember
        # web: None — взять модуль веб-поиска автоматически; False — веб выключен (тесты, без сети)
        self._web = _web_module() if web is None else (web or None)
        self._src_n = 0

    # ---------- каталог ----------

    def available(self, web: bool, memory: bool) -> List[str]:
        names: List[str] = list(MEMORY_TOOLS) if memory else []
        if web and self._web is not None:
            names += list(WEB_TOOLS)
        return names

    def describe(self, name: str, args: dict) -> str:
        """Подпись до выполнения — показывается в интерфейсе, пока инструмент работает."""
        if name == "memory_search":
            return f"Память: «{_clip(args.get('query'), 50)}»"
        if name == "memory_remember":
            return "Запоминаю"
        if name == "web_search":
            qs = args.get("queries") or [args.get("query", "")]
            first = qs[0] if isinstance(qs, list) and qs else qs
            return f"Веб-поиск: «{_clip(first, 50)}»"
        if name == "web_fetch":
            return f"Открываю: {_clip(urlparse(_str(args.get('url'))).netloc or args.get('url'), 60)}"
        return name

    # ---------- исполнение ----------

    def run(self, name: str, args: dict) -> Outcome:
        """Никогда не бросает исключения: ошибка инструмента становится результатом для модели."""
        args = args if isinstance(args, dict) else {}
        try:
            if name == "memory_search":
                return self._memory_search(args)
            if name == "memory_remember":
                return self._memory_remember(args)
            if name == "web_search":
                return self._web_search(args)
            if name == "web_fetch":
                return self._web_fetch(args)
            return Outcome(False, f"Неизвестный инструмент: {name}", label="Инструмент", detail=name)
        except Exception as exc:                         # noqa: BLE001
            return Outcome(False, f"Ошибка инструмента {name}: {exc}", label="Ошибка инструмента",
                           detail=_clip(exc, 120))

    def _memory_search(self, args: dict) -> Outcome:
        query = _str(args.get("query"))
        if not query:
            return Outcome(False, "Пустой запрос.", label="Память", detail="пустой запрос")
        k = _int(args.get("k"), 8, 1, 20)
        r = self._recall(query, k)
        facts = [f"- {f.get('text', '')} (уверенность {f.get('confidence', '?')})"
                 for f in r.get("facts", [])]
        chunks = [f"> {_clip(c.get('text', ''), 400)}" for c in r.get("chunks", [])]
        if not facts and not chunks:
            text = "В памяти ничего не найдено."
        else:
            parts = []
            if facts:
                parts += ["Факты:"] + facts
            if chunks:
                parts += ["Фрагменты документов:"] + chunks
            text = "\n".join(parts)
        return Outcome(True, text, label=f"Память: «{_clip(query, 50)}»",
                       detail=f"фактов: {len(facts)}, фрагментов: {len(chunks)}")

    def _memory_remember(self, args: dict) -> Outcome:
        text = _str(args.get("text"))
        if not text:
            return Outcome(False, "Пустой текст.", label="Запомнить", detail="пустой текст")
        r = self._remember(text)
        if r.get("status") == "queued":
            msg = "Принято: факты будут извлечены в фоне."
        else:
            msg = "Память выключена, сохранить нельзя."
        return Outcome(r.get("status") == "queued", msg, label="Запомнить", detail=_clip(text, 60))

    def _web_search(self, args: dict) -> Outcome:
        if self._web is None:
            return Outcome(False, "Веб-поиск недоступен на сервере.", label="Веб-поиск",
                           detail="нет модуля web")
        queries = args.get("queries")
        if isinstance(queries, str):
            queries = [queries]
        if not queries and args.get("query"):
            queries = [args.get("query")]
        queries = [_str(q) for q in (queries or []) if _str(q)][:3]
        if not queries:
            return Outcome(False, "Пустой запрос.", label="Веб-поиск", detail="пустой запрос")
        res = asyncio.run(self._web.search(queries, max_results=5))
        sources: List[dict] = []
        for s in (res.get("sources") or [])[:6]:
            url = _str(s.get("url"))
            if not url:
                continue
            self._src_n += 1
            sources.append({"n": self._src_n, "title": _clip(s.get("title") or url, 140),
                            "url": url, "snippet": _clip(s.get("snippet"), 300)})
        if not sources:
            return Outcome(True, "Источников не найдено.", label=f"Веб-поиск: «{_clip(queries[0], 50)}»",
                           detail="0 источников")
        lines = [f"[{s['n']}] {s['title']}\n{s['url']}\n{s['snippet']}".rstrip() for s in sources]
        return Outcome(True, "\n\n".join(lines), label=f"Веб-поиск: «{_clip(queries[0], 50)}»",
                       detail=f"источников: {len(sources)}", sources=sources)

    def _web_fetch(self, args: dict) -> Outcome:
        if self._web is None:
            return Outcome(False, "Чтение страниц недоступно на сервере.", label="Страница", detail="нет модуля web")
        url = _str(args.get("url"))
        host = urlparse(url).netloc or url
        res = asyncio.run(self._web.fetch(url, max_chars=5000))
        if res.get("status") != "ok":
            return Outcome(False, f"Не удалось открыть страницу: {res.get('message', 'ошибка')}",
                           label=f"Страница: {_clip(host, 40)}", detail=_clip(res.get("message"), 120))
        self._src_n += 1
        n = self._src_n
        source = {"n": n, "title": _clip(host, 140), "url": url, "snippet": _clip(res.get("text"), 200)}
        return Outcome(True, f"[{n}] {url}\n{res.get('text', '')}", label=f"Страница: {_clip(host, 40)}",
                       detail=f"символов: {len(res.get('text', ''))}", sources=[source])


def instructions(names: List[str]) -> str:
    """Системная инструкция по протоколу вызова. Пустая строка, если инструментов нет."""
    if not names:
        return ""
    lines = [
        "Инструменты. Чтобы вызвать инструмент, напиши ровно один блок вида:",
        '<tool_call>{"name": "имя", "arguments": {...}}</tool_call>',
        "и сразу остановись. Система выполнит вызов и пришлёт результат в теге <tool_result>. "
        "Вызывай инструмент, только если без него нельзя ответить точно или пользователь просит "
        "поиск, страницу или память. Результаты инструментов не выдумывай. Отвечая по источникам, "
        "ставь их номера [n] из результата.",
        "Доступно:",
    ]
    for n in names:
        spec = SPECS[n]
        props = ", ".join(spec["parameters"]["properties"].keys())
        lines.append(f"- {n}({props}) — {spec['description']}")
    return "\n".join(lines)


def catalog() -> dict:
    """Каталог для интерфейса и других клиентов: инструменты и команды."""
    return {
        "tools": [{"name": n, "description": s["description"], "parameters": s["parameters"],
                   "requires_web": n in WEB_TOOLS} for n, s in SPECS.items()],
        "commands": [{"name": n, **{k: v for k, v in c.items()}} for n, c in COMMANDS.items()],
    }
