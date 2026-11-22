# -*- coding: utf-8 -*-
"""Клиент MCP-сервера памяти для чата и адаптер RemoteMemory.

Чат не открывает журнал сам: все операции с памятью идут через pulsenet-unified, который
единственный владеет журналом. RemoteMemory повторяет интерфейс TriplexMemory, поэтому остальной
код приложения не меняется.

Ошибки связи — MemoryUnavailable (память недоступна, чат продолжает работать без неё).
Отказ сервера по существу (например, неизвестный факт) — ValueError с текстом сервера.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional


class MemoryUnavailable(RuntimeError):
    """MCP-сервер памяти не отвечает или не принимает токен."""


def _http_detail(exc: BaseException) -> str:
    """Достать HTTP-причину из ExceptionGroup, который поднимает MCP-клиент.

    streamablehttp_client оборачивает ошибки запроса в anyio.TaskGroup, поэтому
    наружу видно только «unhandled errors in a TaskGroup». Без распаковки
    пользователь не отличит 401 (плохой токен) от обычного недоступного сервера.
    """
    try:
        from httpx import HTTPStatusError
    except ImportError:  # httpx — зависимость mcp, но клиент не должен падать без неё
        return ""
    seen: set[int] = set()
    stack = [exc]
    while stack:
        cur = stack.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if isinstance(cur, HTTPStatusError):
            code = cur.response.status_code
            if code in (401, 403):
                hint = "неверный или отозванный токен (PULSENET_CHAT_MCP_TOKEN)"
            elif code == 503:
                hint = "сервер памяти не настроен (нет PULSENET_MCP_TOKEN)"
            else:
                hint = f"HTTP {code}"
            return f"HTTP {code}: {hint}"
        stack.extend(getattr(cur, "exceptions", ()) or ())
        if cur.__cause__ is not None:
            stack.append(cur.__cause__)
        if cur.__context__ is not None:
            stack.append(cur.__context__)
    return ""


def _content_text(content) -> str:
    """Собрать текст из content blocks MCP. image_generate может вернуть image + text —
    брать content[0].text нельзя: у image-блока нет поля text."""
    parts: List[str] = []
    for block in (content or []):
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts) if parts else "{}"


class MCPClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self.url = url
        self.token = token
        self.timeout = timeout

    def _session(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        if not self.token:
            raise MemoryUnavailable("не задан PULSENET_CHAT_MCP_TOKEN (см. .env.example)")
        return ClientSession, streamablehttp_client

    def list_tools(self) -> List[dict]:
        """Каталог инструментов MCP-сервера. Раньше чат его не запрашивал — отсюда и
        «я текстовая нейросеть»: модель видела только 4 захардкоженных имени."""
        ClientSession, streamablehttp_client = self._session()

        async def run():
            async with streamablehttp_client(self.url,
                                             headers={"Authorization": f"Bearer {self.token}"}) as (r, w, _):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    res = await sess.list_tools()
                    return [{
                        "name": t.name,
                        "description": getattr(t, "description", "") or "",
                        "inputSchema": getattr(t, "inputSchema", None) or {"type": "object", "properties": {}},
                    } for t in (res.tools or [])]

        try:
            # Новый поток asyncio в каждом вызове: вызовы приходят из рабочих потоков чата.
            return asyncio.run(asyncio.wait_for(run(), self.timeout))
        except Exception as exc:                  # noqa: BLE001
            detail = _http_detail(exc) or str(exc)
            raise MemoryUnavailable(f"MCP-сервер недоступен ({self.url}): {detail}") from exc

    def call(self, tool: str, args: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None) -> dict:
        ClientSession, streamablehttp_client = self._session()

        async def run():
            async with streamablehttp_client(self.url,
                                             headers={"Authorization": f"Bearer {self.token}"}) as (r, w, _):
                async with ClientSession(r, w) as sess:
                    await sess.initialize()
                    return await sess.call_tool(tool, args or {})

        try:
            # Новый поток asyncio в каждом вызове: вызовы приходят из рабочих потоков чата.
            res = asyncio.run(asyncio.wait_for(run(), timeout or self.timeout))
        except Exception as exc:                  # noqa: BLE001
            detail = _http_detail(exc) or str(exc)
            raise MemoryUnavailable(f"MCP-сервер недоступен ({self.url}): {detail}") from exc
        text = _content_text(res.content)
        if getattr(res, "isError", False):
            raise MemoryUnavailable(text[:300])
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise MemoryUnavailable("ответ MCP-сервера не является JSON") from exc
        if isinstance(data, dict) and data.get("status") == "error":
            raise ValueError(str(data.get("message", "ошибка памяти")))
        return data


class RemoteMemory:
    """Тот же интерфейс, что у TriplexMemory, но данные — на MCP-сервере."""

    backend = "mcp"

    def __init__(self, client: MCPClient):
        self.client = client

    def recall(self, query: str, as_of: Optional[str] = None, k_facts: int = 8,
               include_archived: bool = False) -> dict:
        args: Dict[str, Any] = {"query": query, "k": int(k_facts), "include_archived": bool(include_archived)}
        if as_of:
            args["as_of"] = as_of
        return self.client.call("triplex_search", args)

    def ingest(self, text: str, session_id: str = "", role: str = "user",
               recorded_at: Optional[str] = None, model: str = "") -> dict:
        # Обычные сообщения сохраняются с серверным временем. А «чат → память»
        # передаёт стабильное recorded_at: иначе каждый повтор создавал бы новый doc_id.
        # model — модель текущего ответа: факты извлекаются ею же, а не TRIPLEX_LLM_MODEL из .env.
        args = {"text": text, "session_id": session_id, "role": role}
        if recorded_at:
            args["recorded_at"] = recorded_at
        if model:
            args["model"] = model
        return self.client.call("triplex_ingest", args)

    def stats(self) -> dict:
        return self.client.call("triplex_stats").get("memory", {})

    def graph(self, max_edges: int = 200) -> dict:
        return self.client.call("triplex_graph", {"max_edges": int(max_edges)})

    def list_facts(self, limit: int = 50) -> List[dict]:
        return self.client.call("triplex_facts", {"limit": int(limit)}).get("facts", [])

    def confirm(self, fact_id: str, yes: bool) -> dict:
        return self.client.call("triplex_confirm", {"fact_id": fact_id, "yes": bool(yes)})

    def retract(self, fact_id: str) -> dict:
        return self.client.call("triplex_retract", {"fact_id": fact_id})

    def merge_alias(self, entity_id: str, alias: str) -> dict:
        return self.client.call("triplex_link", {"entity_id": entity_id, "alias": alias})

    def supersede(self, old_fact_id: str, s_name: str, r: str, o_name: str, valid_from: str,
                  source: dict, confidence: float = 0.8, functional: bool = False) -> dict:
        return self.client.call("triplex_supersede", {
            "old_fact_id": old_fact_id, "s": s_name, "r": r, "o": o_name, "valid_from": valid_from,
            "doc_id": source["doc_id"], "span": source["span"],
            "confidence": confidence, "functional": functional,
        })
