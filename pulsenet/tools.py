# -*- coding: utf-8 -*-
"""Инструменты чата: локальные (память, интернет) и MCP-инструменты pulsenet-unified
(triplex_*, web_*, image_generate, activity_recent).

Каждый инструмент описан JSON-схемой. Модель вызывает их текстовым протоколом
(см. toolcalls.py); результат для модели — короткий читаемый текст с номерами источников,
а для интерфейса — подпись, источники и (для image_generate) вложение с картинкой.

Раньше здесь было только 4 захардкоженных имени — из-за этого модель не знала про
image_generate и отвечала «я текстовая нейросеть». Теперь каталог собирается динамически:
локальные инструменты + инструменты, обнаруженные через MCP list_tools().
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import threading

from .toolcalls import COMMANDS, agent_limits
from .workspace import Workspace, WorkspaceError

MEMORY_TOOLS = ("memory_search", "memory_remember")
WEB_TOOLS = ("web_search", "web_fetch")
IMAGE_TOOL = "image_generate"
SKILL_TOOLS = ("skill_list", "skill_read", "skill_write", "skill_patch")
NOTE_TOOLS = ("note_write", "note_append", "note_read", "note_list")
PLAN_TOOL = "todo_write"
WORKSPACE_TOOLS = SKILL_TOOLS + NOTE_TOOLS + (PLAN_TOOL,)
TODO_STATUS = ("pending", "in_progress", "done")
SUBAGENT_TOOL = "subagent"
# Инструменты, которые меняют общее состояние: в одном шаге они идут строго по очереди.
SEQUENTIAL_TOOLS = frozenset({PLAN_TOOL, "skill_write", "skill_patch", "note_write", "note_append",
                              "memory_remember"})
# Субагенту не даём: рекурсия, план и любые записи — их делает основной агент.
SUBAGENT_DENY = frozenset({SUBAGENT_TOOL, PLAN_TOOL, "skill_write", "skill_patch", "note_write",
                           "note_append", "memory_remember"})

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
    "skill_list": {
        "description": "Список твоих навыков (name — описание). Загрузи нужный через skill_read.",
        "parameters": {"type": "object", "properties": {}},
    },
    "skill_read": {
        "description": "Полная инструкция навыка по имени. Читай перед задачей, похожей на навык.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]},
    },
    "skill_write": {
        "description": "Создать или заменить навык: как делать повторяемую задачу. Прежняя версия "
                       "сохраняется. Пиши короткие пошаговые инструкции, без личных данных и секретов.",
        "parameters": {"type": "object",
                       "properties": {
                           "name": {"type": "string", "description": "slug: a-z, 0-9, дефис"},
                           "description": {"type": "string",
                                           "description": "когда применять навык (одна строка)"},
                           "body": {"type": "string", "description": "инструкция в markdown"}},
                       "required": ["name", "description", "body"]},
    },
    "skill_patch": {
        "description": "Точечно поправить навык: заменить фрагмент old на new (old встречается ровно раз).",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}, "old": {"type": "string"},
                                      "new": {"type": "string"}},
                       "required": ["name", "old", "new"]},
    },
    "note_write": {
        "description": "Записать заметку (markdown). image_url — картинка из image_generate, "
                       "вставляется в заметку. kind=self — самонаблюдение о себе и о работе, "
                       "оно попадёт в твой контекст в следующих сессиях.",
        "parameters": {"type": "object",
                       "properties": {"title": {"type": "string"}, "body": {"type": "string"},
                                      "tags": {"type": "array", "items": {"type": "string"}},
                                      "kind": {"type": "string", "enum": ["note", "self"]},
                                      "image_url": {"type": "string"}},
                       "required": ["title", "body"]},
    },
    "note_append": {
        "description": "Дописать в существующую заметку (и при необходимости добавить картинку).",
        "parameters": {"type": "object",
                       "properties": {"title": {"type": "string"}, "text": {"type": "string"},
                                      "image_url": {"type": "string"}},
                       "required": ["title"]},
    },
    "note_read": {
        "description": "Прочитать заметку по заголовку.",
        "parameters": {"type": "object", "properties": {"title": {"type": "string"}},
                       "required": ["title"]},
    },
    "note_list": {
        "description": "Список заметок (заголовок, тип, теги, дата).",
        "parameters": {"type": "object",
                       "properties": {"kind": {"type": "string", "enum": ["note", "self"]}}},
    },
    SUBAGENT_TOOL: {
        "description": "Делегировать простую самостоятельную подзадачу субагенту (поиск в памяти, "
                       "факты из интернета, картинка и т. п.). Субагент работает сам, его вызовы "
                       "видны в трассе, а в ответ ты получаешь итог. Несколько subagent в одном шаге "
                       "выполняются параллельно. Субагент не пишет в навыки, заметки и план.",
        "parameters": {"type": "object",
                       "properties": {"task": {"type": "string", "description": "что сделать и что вернуть"},
                                      "context": {"type": "string",
                                                  "description": "необязательно: нужный кусок вопроса пользователя"},
                                      "tools": {"type": "array", "items": {"type": "string"},
                                                "description": "необязательно: какие инструменты дать"}},
                       "required": ["task"]},
    },
    "todo_write": {
        "description": "План на текущую задачу: полностью заменяет список. Используй для задач из "
                       "нескольких шагов; отмечай in_progress и done по ходу работы.",
        "parameters": {"type": "object",
                       "properties": {"items": {"type": "array", "items": {
                           "type": "object",
                           "properties": {"text": {"type": "string"},
                                          "status": {"type": "string", "enum": list(TODO_STATUS)}},
                           "required": ["text"]}}},
                       "required": ["items"]},
    },
    IMAGE_TOOL: {
        "description": "Создать изображение по текстовому описанию. Локальный EasyDiffusion, "
                       "при недоступности — онлайн-бэкенд. Картинка сразу приходит в чат вложением; "
                       "промпт формулируй сам(а), улучшаторы не нужны.",
        "parameters": {"type": "object",
                       "properties": {
                           "prompt": {"type": "string", "description": "Описание изображения"},
                           "width": {"type": "integer", "minimum": 64, "maximum": 1536},
                           "height": {"type": "integer", "minimum": 64, "maximum": 1536},
                           "steps": {"type": "integer", "minimum": 1, "maximum": 60},
                           "seed": {"type": "integer"},
                           "negative_prompt": {"type": "string"},
                       },
                       "required": ["prompt"]},
    },
}


# Потолки времени для веб-инструментов: зависший сетевой запрос раньше держал весь ход.
WEB_SEARCH_TIMEOUT = 60.0
WEB_FETCH_TIMEOUT = 45.0


def _run_async(coro, timeout: float):
    """Корутина веб-модуля из рабочего потока чата: свой цикл событий и потолок времени."""
    return asyncio.run(asyncio.wait_for(coro, timeout))


@dataclass
class Outcome:
    ok: bool
    text: str                      # то, что увидит модель
    label: str                     # короткая подпись для интерфейса
    detail: str = ""
    sources: List[dict] = field(default_factory=list)
    image: Optional[dict] = None   # вложение для SSE-события image (только image_generate)


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
                 web: Any = None, mcp_call: Optional[Callable] = None,
                 mcp_specs: Optional[Dict[str, dict]] = None,
                 workspace: Optional[Workspace] = None,
                 on_event: Optional[Callable[[str, Any], None]] = None,
                 subagent: Optional[Callable[[str, List[str]], "Outcome"]] = None):
        self._recall = recall
        self._remember = remember
        # workspace — навыки/заметки под data_unified; on_event(kind, payload) — события интерфейса
        # (план todo_write). Без них инструменты рабочего пространства не предлагаются.
        self.workspace = workspace
        self._on_event = on_event
        # subagent(task, requested_tools) -> Outcome; подключается приложением (см. set_subagent)
        self._subagent = subagent
        # веб-инструменты трогают общий счётчик источников: при параллельном запуске — по очереди
        self._web_lock = threading.Lock()
        self.last_image: Optional[str] = None      # URL последней картинки хода — для note_write
        self.todos: List[dict] = []
        # web: None — взять модуль веб-поиска автоматически; False — веб выключен (тесты, без сети)
        self._web = _web_module() if web is None else (web or None)
        self._src_n = 0
        # mcp_call(name, args, timeout) — мост к pulsenet-unified. mcp_specs — имена и схемы,
        # полученные через list_tools(); если их нет, всё равно предлагаем image_generate.
        self._mcp_call = mcp_call
        self._mcp_specs: Dict[str, dict] = dict(mcp_specs or {})
        # image_generate — ядро: предлагаем его даже если discovery не вернул схемы.
        if mcp_call is not None and IMAGE_TOOL not in self._mcp_specs:
            self._mcp_specs[IMAGE_TOOL] = {"mcp_name": IMAGE_TOOL,
                                            "description": SPECS[IMAGE_TOOL]["description"],
                                            "parameters": SPECS[IMAGE_TOOL]["parameters"],
                                            "timeout": agent_limits()["image_timeout"]}

    # ---------- каталог ----------

    def set_subagent(self, fn: Callable[[str, List[str]], "Outcome"]) -> None:
        self._subagent = fn

    def available(self, web: bool, memory: bool) -> List[str]:
        names: List[str] = list(MEMORY_TOOLS) if memory else []
        if self._subagent is not None:
            names.append(SUBAGENT_TOOL)
        if web and self._web is not None:
            names += list(WEB_TOOLS)
        if self.workspace is not None:
            ws_names = [n for n in WORKSPACE_TOOLS
                        if self.workspace.skills_write or n not in ("skill_write", "skill_patch")]
            if self._on_event is None:
                ws_names = [n for n in ws_names if n != PLAN_TOOL]
            names += ws_names
        if self._mcp_call is not None:
            names += [n for n in self._mcp_specs if n not in names]
        return names

    def specs(self, names: List[str]) -> Dict[str, dict]:
        """Схемы для выбранных имён: локальные из SPECS, MCP-инструменты — из list_tools()."""
        out = {}
        for n in names:
            spec = self._mcp_specs.get(n)
            if spec is not None:
                out[n] = {"description": spec.get("description", ""),
                          "parameters": spec.get("parameters") or {"type": "object", "properties": {}}}
            elif n in SPECS:
                out[n] = {"description": SPECS[n]["description"], "parameters": SPECS[n]["parameters"]}
        return out

    def describe(self, name: str, args: dict) -> str:
        """Подпись до выполнения — показывается в интерфейсе, пока инструмент работает."""
        if name == IMAGE_TOOL:
            return f"Рисую: «{_clip(args.get('prompt'), 50)}»"
        if name == "memory_search":
            return f"Память: «{_clip(args.get('query'), 50)}»"
        if name == "memory_remember":
            return "Запоминаю"
        if name in SKILL_TOOLS or name in NOTE_TOOLS:
            target = args.get("name") or args.get("title") or ""
            verb = {"skill_list": "Навыки", "skill_read": "Читаю навык", "skill_write": "Пишу навык",
                    "skill_patch": "Правлю навык", "note_write": "Пишу заметку",
                    "note_append": "Дополняю заметку", "note_read": "Читаю заметку",
                    "note_list": "Заметки"}[name]
            return f"{verb}: «{_clip(target, 50)}»" if target else verb
        if name == PLAN_TOOL:
            return f"План: {len(args.get('items') or [])} шагов"
        if name == SUBAGENT_TOOL:
            return f"Субагент: «{_clip(args.get('task'), 60)}»"
        if name == "web_search":
            qs = args.get("queries") or [args.get("query", "")]
            first = qs[0] if isinstance(qs, list) and qs else qs
            return f"Веб-поиск: «{_clip(first, 50)}»"
        if name == "web_fetch":
            return f"Открываю: {_clip(urlparse(_str(args.get('url'))).netloc or args.get('url'), 60)}"
        if name in self._mcp_specs:
            return f"{name}: {_clip(json.dumps(args, ensure_ascii=False), 60)}"
        return name

    # ---------- исполнение ----------

    def run(self, name: str, args: dict) -> Outcome:
        """Никогда не бросает исключения: ошибка инструмента становится результатом для модели."""
        args = args if isinstance(args, dict) else {}
        try:
            if name == SUBAGENT_TOOL:
                if self._subagent is None:
                    return Outcome(False, "Субагенты недоступны в этом ходе.", label="Субагент")
                task = _str(args.get("task"))
                if not task:
                    return Outcome(False, "Пустая задача для субагента.", label="Субагент")
                context = _str(args.get("context"))
                if context:
                    task = f"{task}\nКонтекст: {context}"
                requested = args.get("tools")
                requested = [_str(x) for x in requested] if isinstance(requested, list) else []
                return self._subagent(task, requested)
            if name in WEB_TOOLS:
                with self._web_lock:
                    return self._run_web(name, args)
            if name == "memory_search":
                return self._memory_search(args)
            if name == "memory_remember":
                return self._memory_remember(args)
            if name in WORKSPACE_TOOLS and self.workspace is not None:
                return self._workspace_tool(name, args)
            if name in self._mcp_specs and self._mcp_call is not None:
                return self._run_mcp(name, args)
            return Outcome(False, f"Неизвестный инструмент: {name}", label="Инструмент", detail=name)
        except TimeoutError:
            return Outcome(False, f"Инструмент {name} не ответил вовремя. Попробуй другой запрос или инструмент.",
                           label="Таймаут инструмента", detail="таймаут")
        except Exception as exc:                         # noqa: BLE001
            # У части исключений текст пустой (TimeoutError и др.): тогда показываем тип.
            text = str(exc) or type(exc).__name__
            return Outcome(False, f"Ошибка инструмента {name}: {text}", label="Ошибка инструмента",
                           detail=_clip(text, 120))

    # ---------- навыки, заметки, план ----------

    def _workspace_tool(self, name: str, args: dict) -> Outcome:
        ws = self.workspace
        label = {"skill_list": "Навыки", "skill_read": "Навык", "skill_write": "Навык",
                 "skill_patch": "Навык", "note_write": "Заметка", "note_append": "Заметка",
                 "note_read": "Заметка", "note_list": "Заметки"}.get(name, "План")
        try:
            if name == PLAN_TOOL:
                return self._todo(args)
            if name == "skill_list":
                items = ws.skill_list()
                if not items:
                    return Outcome(True, "Навыков пока нет. Создай через skill_write.", label=label)
                text = "\n".join(f"{i['name']}: {i['description']}" for i in items)
                return Outcome(True, text, label=f"Навыки: {len(items)}")
            if name == "skill_read":
                skill = ws.skill_read(_str(args.get("name")))
                return Outcome(True, f"# {skill['name']}\n{skill['description']}\n\n{skill['body']}",
                               label=f"Навык: {skill['name']}", detail=_clip(skill["description"], 120))
            if name == "skill_write":
                res = ws.skill_write(_str(args.get("name")), _str(args.get("description")),
                                     _str(args.get("body")))
                return Outcome(True, f"Навык «{res['name']}» {('создан' if res['status'] == 'created' else 'обновлён')}.",
                               label=f"Навык сохранён: {res['name']}", detail=res["status"])
            if name == "skill_patch":
                res = ws.skill_patch(_str(args.get("name")), _str(args.get("old")), _str(args.get("new")))
                return Outcome(True, f"Навык «{res['name']}» исправлен.", label=f"Навык исправлен: {res['name']}")
            if name == "note_write":
                image = args.get("image_url")
                if image == "last":
                    image = self.last_image
                tags = args.get("tags") or []
                res = ws.note_write(_str(args.get("title")), _str(args.get("body")),
                                    tags=[_str(t) for t in tags] if isinstance(tags, list) else [],
                                    kind=_str(args.get("kind")) or "note",
                                    image_url=_str(image) or None)
                return Outcome(True, f"Заметка сохранена: {res['slug']} ({res['kind']}).",
                               label=f"Заметка: {_clip(args.get('title'), 40)}",
                               detail=res["kind"])
            if name == "note_append":
                image = args.get("image_url")
                if image == "last":
                    image = self.last_image
                res = ws.note_append(_str(args.get("title")), _str(args.get("text")),
                                     image_url=_str(image) or None)
                return Outcome(True, f"Заметка дополнена: {res['slug']}.",
                               label=f"Заметка дополнена: {_clip(args.get('title'), 40)}")
            if name == "note_read":
                note = ws.note_read(_str(args.get("title")))
                return Outcome(True, f"# {note['title']}\n{note['body']}",
                               label=f"Заметка: {_clip(note['title'], 40)}")
            if name == "note_list":
                items = ws.note_list(kind=_str(args.get("kind")) or None)
                if not items:
                    return Outcome(True, "Заметок пока нет.", label="Заметки")
                text = "\n".join(f"{i['title']} [{i['kind']}] {i['tags']}".rstrip() for i in items)
                return Outcome(True, text, label=f"Заметки: {len(items)}")
        except WorkspaceError as exc:
            return Outcome(False, str(exc), label=f"{label}: ошибка", detail=_clip(exc, 120))
        return Outcome(False, f"Неизвестный инструмент: {name}", label="Инструмент", detail=name)

    def _todo(self, args: dict) -> Outcome:
        raw = args.get("items")
        if not isinstance(raw, list) or not raw:
            return Outcome(False, "items: нужен непустой список шагов.", label="План: ошибка")
        items = []
        for it in raw[:20]:
            if not isinstance(it, dict):
                continue
            text = _clip(it.get("text"), 160).strip()
            status = it.get("status") if it.get("status") in TODO_STATUS else "pending"
            if text:
                items.append({"text": text, "status": status})
        self.todos = items
        if self._on_event is not None:
            self._on_event("todo", items)
        done = sum(1 for i in items if i["status"] == "done")
        return Outcome(True, f"План обновлён: {done}/{len(items)} выполнено.",
                       label=f"План: {done}/{len(items)}")

    def _run_web(self, name: str, args: dict) -> Outcome:
        if name == "web_search":
            return self._web_search(args)
        return self._web_fetch(args)

    # ---------- MCP-инструменты ----------

    def _run_mcp(self, name: str, args: dict) -> Outcome:
        spec = self._mcp_specs[name]
        mcp_name = spec.get("mcp_name") or name
        timeout = spec.get("timeout")
        if name == IMAGE_TOOL and timeout is None:
            timeout = agent_limits()["image_timeout"]
        if name == IMAGE_TOOL:
            prompt = _str(args.get("prompt"))
            if not prompt:
                return Outcome(False, "Пустой prompt.", label="Изображение", detail="пустой prompt")
            call_args = {k: v for k, v in args.items() if k in SPECS[IMAGE_TOOL]["parameters"]["properties"]}
            call_args["prompt"] = prompt
            # Картинку доставляем отдельным SSE-событием: двойная отправка (image block + JSON)
            # и 500 КБ base64 в контексте модели ни к чему.
            call_args["return_image"] = False
            data = self._mcp_call(mcp_name, call_args, timeout)
            if not isinstance(data, dict) or data.get("status") != "ok":
                msg = (data or {}).get("message", "неизвестная ошибка") if isinstance(data, dict) else str(data)
                return Outcome(False, f"Не удалось создать изображение: {_clip(msg, 300)}",
                               label="Изображение", detail=_clip(msg, 120))
            image = {
                "url": _str(data.get("url")),
                "mime": "image/png",
                "seed": data.get("seed"),
                "backend": _str(data.get("backend")),
                "width": data.get("width"), "height": data.get("height"),
                "prompt": _str(data.get("prompt")), "bytes": data.get("bytes"),
            }
            self.last_image = image["url"] or self.last_image
            # Модели отдаём короткий факт, а не URL: иначе она выведет ссылку текстом,
            # и картинка нарисуется дважды.
            text = (f"Изображение создано и уже показано пользователю выше. "
                    f"Размер {image['width']}×{image['height']}, backend {image['backend']}, "
                    f"seed {image['seed']}. Кратко опиши результат; не выводи URL и не советуй "
                    f"другие сервисы генерации.")
            return Outcome(True, text, label=f"Изображение: «{_clip(prompt, 40)}»",
                           detail=f"{image['backend']}, {image['bytes']} байт", image=image)
        data = self._mcp_call(mcp_name, args, timeout)
        text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
        return Outcome(True, text, label=name, detail=_clip(text, 120))

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
        res = _run_async(self._web.search(queries, max_results=5), WEB_SEARCH_TIMEOUT) or {}
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
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return Outcome(False, "Адрес должен начинаться с http:// или https:// и содержать домен.",
                           label="Страница", detail="неверный адрес")
        host = parsed.netloc
        res = _run_async(self._web.fetch(url, max_chars=5000), WEB_FETCH_TIMEOUT) or {}
        if res.get("status") != "ok":
            return Outcome(False, f"Не удалось открыть страницу: {res.get('message', 'ошибка')}",
                           label=f"Страница: {_clip(host, 40)}", detail=_clip(res.get("message"), 120))
        self._src_n += 1
        n = self._src_n
        source = {"n": n, "title": _clip(host, 140), "url": url, "snippet": _clip(res.get("text"), 200)}
        return Outcome(True, f"[{n}] {url}\n{res.get('text', '')}", label=f"Страница: {_clip(host, 40)}",
                       detail=f"символов: {len(res.get('text', ''))}", sources=[source])


def instructions(names: List[str], specs: Optional[Dict[str, dict]] = None) -> str:
    """Системная инструкция по протоколу вызова. Пустая строка, если инструментов нет."""
    if not names:
        return ""
    specs = specs or SPECS
    lines = [
        "Инструменты. Чтобы вызвать инструмент, напиши ровно один блок вида:",
        '<tool_call>{"name": "имя", "arguments": {...}}</tool_call>',
        "и сразу остановись. Система выполнит вызов и пришлёт результат в теге <tool_result>. "
        "Вызывай инструмент, только если без него нельзя ответить точно или пользователь просит "
        "поиск, страницу, память или изображение. Результаты инструментов не выдумывай. "
        "Отвечая по источникам, ставь их номера [n] из результата. "
        "Если инструмент вернул ошибку — измени аргументы или выбери другой инструмент, а не выдумывай ответ.",
        "Доступно:",
    ]
    for n in names:
        spec = specs.get(n) or SPECS.get(n)
        if not spec:
            continue
        props = ", ".join((spec.get("parameters") or {}).get("properties", {}).keys())
        lines.append(f"- {n}({props}) — {spec.get('description', '')}")
    return "\n".join(lines)


def catalog() -> dict:
    """Каталог для интерфейса и других клиентов: инструменты и команды."""
    return {
        "tools": [{"name": n, "description": s["description"], "parameters": s["parameters"],
                   "requires_web": n in WEB_TOOLS} for n, s in SPECS.items()],
        "commands": [{"name": n, **{k: v for k, v in c.items()}} for n, c in COMMANDS.items()],
    }
