# -*- coding: utf-8 -*-
"""MCP-сервер ТРИПЛЕКСИИ: JSON-RPC 2.0 поверх Streamable HTTP (stdlib, без SSE).

Сервер отвечает одним JSON-ответом на каждый POST. Серверных уведомлений нет,
поэтому GET /mcp возвращает 405 (это разрешено спецификацией).

Инструменты (см. docs/MCP_TRIPLEX.md): ingest, search, assert, supersede, retract,
confirm, query, link_entity, define_relation, stats, check, consolidate (сухой прогон), whoami.
Аутентификация и проверка Host/Origin — в pulsenet/server.py, здесь только протокол.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

from .journal import ConflictError, FactJournal, InvariantError
from .memory import TriplexMemory

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "pulsenet-triplex", "version": "0.1.0"}

# Коды ошибок JSON-RPC
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_SOURCE_SCHEMA = {
    "type": "object",
    "description": "Происхождение факта: документ и позиция символов. Обязательно (I1).",
    "properties": {
        "doc_id": {"type": "string"},
        "span": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
    },
    "required": ["doc_id", "span"],
}


class _ToolError(Exception):
    """Ошибка аргументов или бизнес-правил: уходит клиенту как isError=true, а не как сбой протокола."""


def _tools_spec() -> List[dict]:
    return [
        {
            "name": "triplex_assert",
            "description": "Записать факт (субъект, отношение, объект) с источником. "
                           "Для functional-отношений новый факт может вытеснить старый; "
                           "если вытеснить нельзя, возвращается status=conflict.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "s": {"type": "string"}, "r": {"type": "string"}, "o": {"type": "string"},
                    "source": _SOURCE_SCHEMA,
                    "valid_from": {"type": "string", "description": "ISO 8601, по умолчанию сейчас"},
                    "valid_to": {"type": "string", "description": "ISO 8601, необязательно"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "functional": {"type": "boolean", "description": "Не более одного действующего объекта на (s, r)"},
                },
                "required": ["s", "r", "o", "source"],
            },
        },
        {
            "name": "triplex_query",
            "description": "Действующие факты по любой комбинации s, r, o на момент as_of (valid-time).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "s": {"type": "string"}, "r": {"type": "string"}, "o": {"type": "string"},
                    "as_of": {"type": "string", "description": "ISO 8601, по умолчанию сейчас"},
                },
            },
        },
        {
            "name": "triplex_retract",
            "description": "Отозвать факт. Запись остаётся в журнале для аудита, но перестаёт быть действующей.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string"},
                    "at": {"type": "string", "description": "ISO 8601, по умолчанию сейчас"},
                },
                "required": ["fact_id"],
            },
        },
        {
            "name": "triplex_stats",
            "description": "Счётчики журнала: всего записей, действующих, отозванных.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "triplex_ingest",
            "description": "Сохранить документ (плоть) и извлечь из него факты (скелет). "
                           "Если экстракция не удалась, документ всё равно сохраняется (status=extraction_failed).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "session_id": {"type": "string"},
                    "role": {"type": "string", "enum": ["user", "assistant", "system"]},
                    "recorded_at": {"type": "string", "description": "ISO 8601, по умолчанию сейчас"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "triplex_search",
            "description": "Гибридный поиск: факты с провенансом и фрагменты документов, "
                           "готовый контекст в поле context. Пусто по фактам → RAG по фрагментам.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {"type": "integer", "minimum": 1, "maximum": 50},
                    "as_of": {"type": "string", "description": "ISO 8601, по умолчанию сейчас"},
                    "include_archived": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "triplex_define_relation",
            "description": "Описать отношение онтологии: синонимы, functional (не более одного объекта на "
                           "субъекта), τ затухания в днях (null — не затухает).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "functional": {"type": "boolean"},
                    "tau_days": {"type": ["number", "null"], "exclusiveMinimum": 0},
                },
                "required": ["canonical"],
            },
        },
        {
            "name": "triplex_supersede",
            "description": "Явно вытеснить действующий факт новым (старый закрывается на valid_from нового).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "old_fact_id": {"type": "string"},
                    "s": {"type": "string"}, "r": {"type": "string"}, "o": {"type": "string"},
                    "valid_from": {"type": "string", "description": "ISO 8601, позже valid_from старого факта"},
                    "source": _SOURCE_SCHEMA,
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "functional": {"type": "boolean"},
                },
                "required": ["old_fact_id", "s", "r", "o", "valid_from", "source"],
            },
        },
        {
            "name": "triplex_link_entity",
            "description": "Привязать написание (например склонение «кота») к существующей сущности.",
            "inputSchema": {
                "type": "object",
                "properties": {"entity_id": {"type": "string"}, "alias": {"type": "string"}},
                "required": ["entity_id", "alias"],
            },
        },
        {
            "name": "triplex_confirm",
            "description": "Подтвердить (yes: +0.15, не выше 0.95) или опровергнуть (no: ×0.5) факт.",
            "inputSchema": {
                "type": "object",
                "properties": {"fact_id": {"type": "string"}, "yes": {"type": "boolean"}},
                "required": ["fact_id", "yes"],
            },
        },
        {
            "name": "triplex_whoami",
            "description": "Кто отвечает: сервер и владелец памяти. Один сервер — один владелец.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "triplex_consolidate",
            "description": "«Сон», сухой прогон: сколько фактов подходит для консолидации и какой датасет получился. "
                           "Обучение не запускается.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "triplex_check",
            "description": "Проверка инвариантов I1–I5 по всему журналу. Пустой список — журнал корректен.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def _fact_dict(f) -> dict:
    return {
        "fact_id": f.fact_id, "s": f.s, "r": f.r, "o": f.o,
        "valid_from": f.valid_from, "valid_to": f.valid_to,
        "recorded_at": f.recorded_at, "invalid_at": f.invalid_at,
        "retracted": f.retracted, "confidence": f.confidence,
        "source": f.source, "supersedes": f.supersedes, "functional": f.functional,
    }


def _require_str(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise _ToolError(f"аргумент {key!r} обязателен и должен быть непустой строкой")
    return v


def _optional_str(args: dict, key: str) -> Optional[str]:
    v = args.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise _ToolError(f"аргумент {key!r} должен быть строкой")
    return v


class TriplexMCP:
    """Диспетчер JSON-RPC. Потокобезопасен: все обращения к журналу идут под одним замком."""

    def __init__(self, backend):
        """backend — TriplexMemory (полный режим) или FactJournal (только журнал, без ingest/search)."""
        if isinstance(backend, TriplexMemory):
            self.memory = backend
            self.journal = backend.journal
        else:
            self.memory = None
            self.journal = backend
        self._lock = threading.RLock()
        self._tools: Dict[str, Callable[[dict], Any]] = {
            "triplex_assert": self._t_assert,
            "triplex_query": self._t_query,
            "triplex_retract": self._t_retract,
            "triplex_stats": self._t_stats,
            "triplex_check": self._t_check,
            "triplex_ingest": self._t_ingest,
            "triplex_search": self._t_search,
            "triplex_define_relation": self._t_define_relation,
            "triplex_supersede": self._t_supersede,
            "triplex_link_entity": self._t_link_entity,
            "triplex_confirm": self._t_confirm,
            "triplex_whoami": self._t_whoami,
            "triplex_consolidate": self._t_consolidate,
        }
        self._specs = {t["name"]: t for t in _tools_spec()}

    # ---------- инструменты ----------

    def _t_assert(self, a: dict) -> dict:
        source = a.get("source")
        if not isinstance(source, dict):
            raise _ToolError("аргумент 'source' обязателен: {doc_id, span}")
        conf = a.get("confidence", 0.8)
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            raise _ToolError("confidence должен быть числом")
        functional = a.get("functional", False)
        if not isinstance(functional, bool):
            raise _ToolError("functional должен быть логическим значением")
        try:
            with self._lock:
                f = self.journal.assert_fact(
                    _require_str(a, "s"), _require_str(a, "r"), _require_str(a, "o"),
                    source=source,
                    valid_from=_optional_str(a, "valid_from"),
                    valid_to=_optional_str(a, "valid_to"),
                    confidence=float(conf), functional=functional,
                )
        except ConflictError as exc:
            return {"status": "conflict", "message": str(exc), "old_fact_id": exc.old_fact_id}
        return {"status": "ok", "fact": _fact_dict(f)}

    def _t_query(self, a: dict) -> dict:
        with self._lock:
            facts = self.journal.query(
                s=_optional_str(a, "s"), r=_optional_str(a, "r"), o=_optional_str(a, "o"),
                as_of=_optional_str(a, "as_of"),
            )
        return {"count": len(facts), "facts": [_fact_dict(f) for f in facts]}

    def _t_retract(self, a: dict) -> dict:
        fid = _require_str(a, "fact_id")
        with self._lock:
            if fid not in self.journal.facts:
                raise _ToolError(f"факт {fid!r} не найден")
            f = self.journal.retract(fid, at=_optional_str(a, "at"))
        return {"status": "ok", "fact": _fact_dict(f)}

    def _need_memory(self) -> TriplexMemory:
        if self.memory is None:
            raise _ToolError("память ТРИПЛЕКСИИ не подключена: этот инструмент требует TriplexMemory")
        return self.memory

    def _t_ingest(self, a: dict) -> dict:
        mem = self._need_memory()
        text = _require_str(a, "text")
        role = a.get("role", "user")
        if role not in ("user", "assistant", "system"):
            raise _ToolError("role: user | assistant | system")
        return mem.ingest(text, session_id=_optional_str(a, "session_id") or "", role=role,
                          recorded_at=_optional_str(a, "recorded_at"))

    def _t_search(self, a: dict) -> dict:
        mem = self._need_memory()
        k = a.get("k", 8)
        if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= 50:
            raise _ToolError("k — целое от 1 до 50")
        return mem.recall(_require_str(a, "query"), as_of=_optional_str(a, "as_of"), k_facts=k,
                          include_archived=bool(a.get("include_archived", False)))

    def _t_define_relation(self, a: dict) -> dict:
        mem = self._need_memory()
        aliases = a.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
            raise _ToolError("aliases — массив строк")
        tau = a.get("tau_days")
        if tau is not None and (not isinstance(tau, (int, float)) or isinstance(tau, bool)):
            raise _ToolError("tau_days — число или null")
        try:
            rel = mem.define_relation(_require_str(a, "canonical"), aliases=aliases,
                                      functional=bool(a.get("functional", False)),
                                      tau_days=None if tau is None else float(tau))
        except ValueError as exc:
            raise _ToolError(str(exc)) from exc
        return {"status": "ok", "relation": rel}

    def _t_supersede(self, a: dict) -> dict:
        mem = self._need_memory()
        source = a.get("source")
        if not isinstance(source, dict):
            raise _ToolError("аргумент 'source' обязателен: {doc_id, span}")
        return mem.supersede(_require_str(a, "old_fact_id"), _require_str(a, "s"), _require_str(a, "r"),
                             _require_str(a, "o"), valid_from=_require_str(a, "valid_from"), source=source,
                             confidence=float(a.get("confidence", 0.8)),
                             functional=bool(a.get("functional", False)))

    def _t_link_entity(self, a: dict) -> dict:
        mem = self._need_memory()
        return mem.merge_alias(_require_str(a, "entity_id"), _require_str(a, "alias"))

    def _t_confirm(self, a: dict) -> dict:
        mem = self._need_memory()
        fid = _require_str(a, "fact_id")
        if fid not in mem.journal.facts:
            raise _ToolError(f"факт {fid!r} не найден")
        if not isinstance(a.get("yes"), bool):
            raise _ToolError("yes — логическое значение")
        return mem.confirm(fid, a["yes"])

    def _t_whoami(self, _a: dict) -> dict:
        return {"server": SERVER_INFO["name"], "version": SERVER_INFO["version"],
                "owner": "local", "note": "один сервер обслуживает одного владельца памяти"}

    def _t_consolidate(self, _a: dict) -> dict:
        from . import sleep                      # импорт здесь: модуль «Сна» не нужен остальным инструментам
        mem = self._need_memory()
        plan = sleep.plan_consolidation(mem)
        return {"status": plan["status"], "eligible": plan["eligible"], "rehearsal": plan["rehearsal"],
                "training": "не запущено (сухой прогон); обучение: python -m pulsenet train-sleep"}

    def _t_stats(self, _a: dict) -> dict:
        with self._lock:
            total = len(self.journal.facts)
            retracted = sum(1 for f in self.journal.facts.values() if f.retracted)
            out = {"total": total, "retracted": retracted, "active_records": total - retracted}
            if self.memory is not None:
                out["memory"] = self.memory.stats()
        return out

    def _t_check(self, _a: dict) -> dict:
        with self._lock:
            violations = self.journal.check_invariants()
        return {"ok": not violations, "violations": violations}

    # ---------- протокол ----------

    def call_tool(self, name: str, args: dict) -> dict:
        if name not in self._tools:
            raise _ToolError(f"неизвестный инструмент {name!r}")
        if not isinstance(args, dict):
            raise _ToolError("arguments должен быть объектом")
        try:
            payload = self._tools[name](args)
        except (InvariantError, KeyError, ValueError) as exc:
            # I1–I4 и некорректные значения: клиент видит причину, сервер не падает
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        except _ToolError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": False,
        }

    def _handle_one(self, msg: Any) -> Optional[dict]:
        """Вернуть ответ на одно сообщение JSON-RPC или None для уведомления."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error(None, INVALID_REQUEST, "ожидается объект JSON-RPC 2.0 с полем method")
        req_id = msg.get("id")
        is_notification = "id" not in msg
        method = msg["method"]
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return None if is_notification else _error(req_id, INVALID_PARAMS, "params должен быть объектом")
        try:
            result = self._dispatch(method, params)
        except _ToolError as exc:
            return None if is_notification else _error(req_id, INVALID_PARAMS, str(exc))
        except _MethodNotFound:
            return None if is_notification else _error(req_id, METHOD_NOT_FOUND, f"метод не найден: {method}")
        except Exception as exc:  # noqa: BLE001 — протокол не должен падать из-за инструмента
            return None if is_notification else _error(req_id, INTERNAL_ERROR, f"внутренняя ошибка: {exc}")
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
            return {"protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO}
        if method in ("notifications/initialized", "notifications/cancelled"):
            return {}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": list(self._specs.values())}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise _ToolError("tools/call: поле name обязательно")
            return self.call_tool(name, params.get("arguments") or {})
        raise _MethodNotFound(method)

    def handle(self, payload: Any) -> Optional[Any]:
        """Обработать тело POST. Батч → список ответов. Если ответов нет → None (HTTP 202)."""
        if isinstance(payload, list):
            if not payload:
                return _error(None, INVALID_REQUEST, "пустой батч")
            replies = [r for r in (self._handle_one(m) for m in payload) if r is not None]
            return replies or None
        return self._handle_one(payload)


class _MethodNotFound(Exception):
    pass


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def parse_body(raw: bytes):
    """Вернуть (payload, error_reply). Ошибка разбора — ответ с кодом -32700."""
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError):
        return None, _error(None, PARSE_ERROR, "некорректный JSON")
