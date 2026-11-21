# -*- coding: utf-8 -*-
"""Приложение: чат с LLM, память ТРИПЛЕКСИИ и фоновая индексация.

Чат: память (поиск) → промпт → LLM (llama-server или LM Studio) → ответ.
После ответа реплика пользователя уходит в очередь индексации: документ, экстракция, факты.
Ошибки любого этапа пишутся в журнал ошибок и видны в интерфейсе (/api/status).
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from . import llm
from .mcp_client import MCPClient, MemoryUnavailable, RemoteMemory
from .memory_service import build_encoder
from .triplex import TriplexMemory, make_extractor, openai_chat_caller
from .triplex import sleep
from .toolcalls import (COMMANDS, MAX_CALLS_PER_STEP, MAX_STEPS, ToolCallFilter, parse_calls,
                         parse_command, tool_result_block)
from .tools import ChatTools, Outcome, catalog, instructions

CONTEXT_TURNS = 8
ERROR_LOG_SIZE = 50
MEMORY_RETRIES = 12          # индексация ждёт недоступный MCP: 12 попыток
MEMORY_RETRY_SECONDS = 5.0


def _parse_json_loose(raw: str) -> dict:
    """Достать JSON-объект из ответа модели: модели любят оборачивать его в ```json."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {}


class App:
    def __init__(self, env, data_dir: str = "data"):
        self.env = dict(env)
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.backend = llm.make_backend(self.env, data_dir=data_dir)
        self.backend_lock = threading.Lock()
        self.history: Dict[str, List[dict]] = {}
        self.history_lock = threading.Lock()
        self.errors: deque = deque(maxlen=ERROR_LOG_SIZE)
        self.stop_flag = False
        self.memory_enabled = self.env.get("TRIPLEX_MEMORY", "1") != "0"
        self.embed_server = None
        self.embed_model = ""
        self.active_model = ""       # модель последнего ответа: экстракция идёт ею же, без второй загрузки
        # local — свой журнал в data_dir (как раньше); mcp — память у MCP-сервера pulsenet-unified,
        # общая для всех ваших агентов. Значение по умолчанию local: без .env поведение прежнее.
        self.memory_backend = (self.env.get("PULSENET_MEMORY") or "local").strip().lower()
        self.memory = self._make_remote_memory() if self.memory_backend == "mcp" else self._make_memory(data_dir)
        self._index_q: "queue.Queue[tuple]" = queue.Queue()
        self._worker = threading.Thread(target=self._index_loop, daemon=True, name="triplex-index")
        self._worker.start()

    # ---------- ошибки ----------

    def log_error(self, where: str, exc: BaseException) -> None:
        self.errors.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "where": where,
                            "message": str(exc)[:500]})

    def recent_errors(self) -> List[dict]:
        return list(self.errors)[::-1]

    # ---------- память ----------

    def default_rel(self) -> str:
        if self.backend.name == "llamacpp":
            return self.env.get("LLAMACPP_DEFAULT_MODEL", "").strip()
        # LM Studio: если пользователь выбрал модель в интерфейсе LM Studio, не дёргаем
        # модель из .env — лишняя загрузка вытесняет из памяти эмбеддинги.
        refresh = getattr(self.backend, "refresh_loaded", None)
        if callable(refresh):
            try:
                loaded = refresh()
                if loaded:
                    return loaded
            except Exception:                # noqa: BLE001 — LM Studio может быть недоступен
                pass
        return self.env.get("LMSTUDIO_MODEL", "").strip()

    def model_id(self) -> str:
        """Идентификатор модели для запроса. Запускает бэкенд при необходимости."""
        with self.backend_lock:
            target = (self.active_model or "").strip() or self.default_rel()
            return self.backend.ensure(target) or target or "local"

    def extraction_model(self) -> str:
        """Модель для записи фактов/дистилляции: без ensure — память не должна падать,
        если модель временно недоступна (документ всё равно сохраняется)."""
        return (self.active_model or "").strip() or self.default_rel() or "local"

    def _make_encoder(self, data_dir: str):
        """Эмбеддер — см. memory_service.build_encoder (общий с MCP-сервером)."""
        encoder, self.embed_server, self.embed_model = build_encoder(self.env, data_dir, self.log_error)
        return encoder

    def _make_remote_memory(self) -> RemoteMemory:
        url = (self.env.get("PULSENET_MCP_URL") or "http://127.0.0.1:8010/mcp").strip()
        token = (self.env.get("PULSENET_CHAT_MCP_TOKEN") or "").strip()
        return RemoteMemory(MCPClient(url, token))

    def _make_memory(self, data_dir: str) -> TriplexMemory:
        encoder = self._make_encoder(data_dir)
        extractor = None
        if self.env.get("TRIPLEX_EXTRACT", "1") != "0":
            speaker = self.env.get("TRIPLEX_USER_NAME", "").strip() or None

            def llm_call(prompt: str) -> str:
                base = self.backend.base_url
                return openai_chat_caller(base, self.extraction_model())(prompt)
            extractor = make_extractor(llm_call, speaker=speaker)
        min_cos = float(self.env.get("TRIPLEX_MIN_COS", "0.35"))
        return TriplexMemory(os.path.join(data_dir, "triplex"), encoder=encoder, extractor=extractor,
                             min_cos=min_cos)

    def _index_loop(self) -> None:
        """Одна фоновая очередь: экстракция идёт через ту же модель, что и чат, поэтому по одной.
        Если MCP-сервер памяти недоступен, сообщение ждёт его (MEMORY_RETRIES попыток), а не теряется."""
        while True:
            text, chat_id, role = self._index_q.get()
            try:
                for attempt in range(MEMORY_RETRIES):
                    try:
                        self.ingest(text, session_id=chat_id or "default", role=role)
                        break
                    except MemoryUnavailable as exc:
                        if attempt == MEMORY_RETRIES - 1:
                            self.log_error("индексация: память недоступна, сообщение не сохранено", exc)
                        else:
                            time.sleep(MEMORY_RETRY_SECONDS)
            except Exception as exc:              # noqa: BLE001
                self.log_error("индексация", exc)
            finally:
                self._index_q.task_done()

    def ingest(self, text: str, session_id: str = "", role: str = "user", recorded_at=None,
               model: str = "") -> dict:
        """Единая точка записи: сбой экстракции не исключение, а статус — и он попадает в журнал ошибок.

        model — модель чата, которой извлекаются факты. Явно указанная (например, из веба)
        становится active_model; иначе берётся последняя использованная / загруженная в LM Studio.
        """
        target = (model or "").strip()
        if target:
            with self.backend_lock:
                self.active_model = target
        resolved = self.extraction_model()
        # Локальная память уже вызывает model_id() в своём экстракторе; MCP — отдельный процесс,
        # ему модель надо передать явно.
        extra = {"model": resolved} if self.memory_backend == "mcp" else {}
        out = self.memory.ingest(text, session_id=session_id, role=role,
                                 recorded_at=recorded_at, **extra)
        if out["status"] == "extraction_failed":
            self.log_error("экстракция фактов", RuntimeError(out.get("error", "неизвестная ошибка")))
        return out

    def index_async(self, text: str, chat_id: str, role: str = "user") -> None:
        if self.memory_enabled:
            self._index_q.put((text, chat_id, role))

    def index_pending(self) -> int:
        return self._index_q.qsize()

    def memory_context(self, message: str) -> str:
        if not self.memory_enabled:
            return ""
        try:
            ctx = self.memory.recall(message)["context"]
        except Exception as exc:                  # noqa: BLE001
            self.log_error("поиск в памяти", exc)
            return ""
        if not ctx:
            return ""
        return ("Факты и фрагменты из памяти пользователя (используй, только если относятся к вопросу; "
                "источники указаны):\n" + ctx)

    def _tool_recall(self, query: str, k: int) -> dict:
        return self.memory.recall(query, k_facts=k)

    def _tool_remember(self, text: str, chat_id: str) -> dict:
        if not self.memory_enabled:
            return {"status": "disabled"}
        self.index_async(text, chat_id, "user")
        return {"status": "queued"}

    def graph(self, max_edges: int = 200) -> dict:
        """Карта «нейронов» памяти для интерфейса и других клиентов."""
        return self.memory.graph(max_edges=max_edges)

    def describe_tools(self) -> dict:
        return catalog()

    # ---------- чат ----------

    def _history(self, chat_id: str) -> List[dict]:
        with self.history_lock:
            turns = self.history.get(chat_id or "default", [])[-CONTEXT_TURNS * 2:]
            return [{"role": t["role"], "content": t["content"]} for t in turns]

    def _append_history(self, chat_id: str, role: str, content: str) -> None:
        with self.history_lock:
            self.history.setdefault(chat_id or "default", []).append({"role": role, "content": content})

    def _call_tool(self, tools: ChatTools, name: str, args: dict, on_delta) -> Outcome:
        """Исполняет инструмент и сообщает интерфейсу о ходе: running → done/error, источники."""
        cid = "t" + uuid.uuid4().hex[:8]
        on_delta("tool", {"id": cid, "name": name, "status": "running", "label": tools.describe(name, args)})
        out = tools.run(name, args)
        on_delta("tool", {"id": cid, "name": name, "status": "done" if out.ok else "error",
                          "label": out.label, "detail": out.detail})
        if out.sources:
            on_delta("sources", out.sources)
        return out

    def _run_command(self, name: str, arg: str, tools: ChatTools, on_delta, chat_id: str) -> dict:
        """Команды выполняются приложением: модели передаётся готовый результат."""
        if not arg:
            return {"reply": COMMANDS[name]["usage"]}
        if name == "search":
            out = self._call_tool(tools, "web_search", {"queries": [arg]}, on_delta)
            return {"user": f"Найди в интернете и ответь по источникам: {arg}",
                    "note": tool_result_block("web_search", out.text)}
        if name == "fetch":
            out = self._call_tool(tools, "web_fetch", {"url": arg}, on_delta)
            return {"user": f"Что на странице {arg}? Ответь по её содержимому.",
                    "note": tool_result_block("web_fetch", out.text)}
        if name == "memory":
            out = self._call_tool(tools, "memory_search", {"query": arg}, on_delta)
            return {"user": f"Что известно из памяти по теме: {arg}",
                    "note": tool_result_block("memory_search", out.text)}
        if name == "remember":
            out = self._call_tool(tools, "memory_remember", {"text": arg}, on_delta)
            return {"user": f"Запомни: {arg}", "note": tool_result_block("memory_remember", out.text)}
        return {"reply": COMMANDS[name]["usage"]}

    def chat(self, message: str, chat_id: str, on_delta: Callable[[str, object], None],
             cancelled: Optional[Callable[[], bool]] = None, web: bool = False, model: str = "") -> str:
        """Ход чата с инструментами.

        on_delta(kind, payload):
          'token' / 'reasoning' — текст ответа и рассуждений (str);
          'tool'    — ход инструмента (dict: id, name, status, label, detail);
          'sources' — найденные источники (list: n, title, url, snippet).
        web — разрешить веб-инструменты в этом ходе; model — модель для этого ответа
        (пусто — модель по умолчанию из настроек). Возвращает итоговый текст ответа.
        """
        self.stop_flag = False

        def is_cancelled() -> bool:
            return bool(cancelled and cancelled()) or self.stop_flag

        tools = ChatTools(recall=self._tool_recall,
                          remember=lambda text: self._tool_remember(text, chat_id))
        names = tools.available(web=web, memory=self.memory_enabled)
        parts: List[str] = []

        def emit_text(text: str) -> None:
            parts.append(text)
            on_delta("token", text)

        # 1) команды: /search, /fetch, /memory, /remember
        user_text, note = message, ""
        cmd = parse_command(message)
        if cmd:
            result = self._run_command(cmd[0], cmd[1], tools, on_delta, chat_id)
            if result.get("reply"):
                emit_text(result["reply"])
                return result["reply"]
            user_text, note = result["user"], result["note"]

        # 2) контекст: память по вопросу + инструкция по инструментам
        system = []
        mem = self.memory_context(user_text)
        if mem:
            system.append(mem)
        if names:
            system.append(instructions(names))
        messages: List[dict] = [{"role": "system", "content": "\n\n".join(system)}] if system else []
        messages += self._history(chat_id)
        content = user_text + ("\n\n" + note if note else "")
        messages.append({"role": "user", "content": content})

        target = (model or "").strip() or self.default_rel()
        with self.backend_lock:
            rel = self.backend.ensure(target) or target or "local"
            self.active_model = rel      # экстрактор/индексация используют ту же модель, не грузят вторую

        # 3) агентный цикл: модель → (вызовы инструментов → результаты) → модель ...
        steps = 0
        while True:
            flt = ToolCallFilter()
            step: List[str] = []

            def delta(kind, text, flt=flt, step=step):
                if kind == "token":
                    visible = flt.feed(text)
                    if visible:
                        step.append(visible)
                        emit_text(visible)
                else:
                    on_delta(kind, text)

            self.backend.stream(rel, messages, delta, cancelled=is_cancelled)
            tail = flt.finish()
            if tail:
                step.append(tail)
                emit_text(tail)
            raw_calls = flt.calls_raw[:MAX_CALLS_PER_STEP]
            if not raw_calls or steps >= MAX_STEPS or is_cancelled():
                break
            steps += 1
            messages.append({"role": "assistant",
                             "content": "".join(step) + "".join(f"<tool_call>{r}</tool_call>" for r in raw_calls)})
            blocks = []
            for call in parse_calls(raw_calls):
                name = call.get("name") or "?"
                if call.get("error"):
                    out = Outcome(False, call["error"], label="Инструмент", detail=call["error"])
                    on_delta("tool", {"id": "t" + uuid.uuid4().hex[:8], "name": name, "status": "error",
                                      "label": "Ошибка вызова", "detail": call["error"]})
                elif name not in names:
                    out = Outcome(False, f"Инструмент {name} недоступен. Доступны: {', '.join(names) or 'нет'}.",
                                  label="Инструмент", detail=name)
                    on_delta("tool", {"id": "t" + uuid.uuid4().hex[:8], "name": name, "status": "error",
                                      "label": f"Нет инструмента: {name}", "detail": ""})
                else:
                    out = self._call_tool(tools, name, call.get("arguments") or {}, on_delta)
                blocks.append(tool_result_block(name, out.text))
            messages.append({"role": "user", "content": "\n".join(blocks)})

        answer = "".join(parts).strip()
        if answer:
            self._append_history(chat_id, "user", message)
            self._append_history(chat_id, "assistant", answer)
        # Команда /remember уже поставила текст в очередь индексации; обычное сообщение — тоже в очередь.
        if cmd is None:
            self.index_async(message, chat_id, "user")
        return answer

    def stop(self) -> None:
        self.stop_flag = True

    def _embedder_status(self, mstats: dict) -> dict:
        """В MCP-режиме эмбеддер живёт в сервере памяти — врать про «не загружен» не нужно."""
        if self.memory_backend == "mcp":
            return {"name": mstats.get("encoder"),
                    "server_running": None,          # отдельный процесс не ours, управляется MCP
                    "server_starts": 0,
                    "model": mstats.get("embed_model"),
                    "dim": mstats.get("embed_dim"),
                    "location": "mcp"}
        return {"name": mstats.get("encoder"),
                "server_running": bool(self.embed_server and self.embed_server.is_running()),
                "server_starts": self.embed_server.starts if self.embed_server else 0,
                "model": self.embed_model or None,
                "dim": getattr(self.memory, "dim", None),
                "location": "local"}

    def status(self) -> dict:
        mstats, online = {}, True
        if self.memory_enabled:
            try:
                mstats = self.memory.stats()
            except Exception:                     # noqa: BLE001 — MCP недоступен: чат работает без памяти
                online = False
        return {
            "backend": self.backend.name,
            "model": getattr(self.backend, "loaded", None),
            "fact_model": self.active_model or self.default_rel(),   # модель экстракции фактов
            "memory_enabled": self.memory_enabled,
            "memory_backend": self.memory_backend,
            "memory_online": online,
            "index_pending": self.index_pending(),
            "memory": mstats,
            "embedder": self._embedder_status(mstats),
            "errors": self.recent_errors(),
        }

    def distill_fact(self, fact_id: str, model: str = "") -> dict:
        """Темпоральная дистилляция: LLM пересобирает факт по актуальным данным журнала.

        verdict=update создаёт новый факт через supersede: старый остаётся в аудите,
        а обоснование модели становится документом-источником (I1).
        Модель — та же, что в чате (явная из веба или последняя использованная).
        """
        fid = str(fact_id or "").strip()
        if not fid:
            raise ValueError("не указан fact_id")
        target_model = (model or "").strip()
        if target_model:
            with self.backend_lock:
                self.active_model = target_model
        facts = self.memory.list_facts(500)
        target = next((f for f in facts if f.get("fact_id") == fid), None)
        if target is None:
            raise ValueError("факт не найден или уже не действует")
        query = " ".join(str(target.get(k, "")) for k in ("s", "r", "o", "text"))
        try:
            recall = self.memory.recall(query, k_facts=6)
        except Exception:                          # noqa: BLE001 — память может быть недоступна
            recall = {}
        nearby = [f for f in facts if f.get("fact_id") != fid and
                  (f.get("s") == target.get("s") or f.get("o") == target.get("o"))][:8]
        evidence = {
            "fact": target,
            "related_facts": [{"text": f.get("text"), "valid_from": f.get("valid_from"),
                               "confidence": f.get("confidence")} for f in nearby],
            "chunks": [{"text": c.get("text")} for c in (recall.get("chunks") or [])[:6]],
        }
        prompt = (
            "Ты — редактор базы знаний. Пересобери один факт по актуальным данным.\n"
            "Верни ТОЛЬКО JSON: {\"verdict\":\"keep|update|retract\",\"s\":\"...\",\"r\":\"...\","
            "\"o\":\"...\",\"confidence\":0.0,\"reason\":\"кратко почему\"}.\n"
            "update — если формулировка или объект устарели; s/r/o — новая триада.\n"
            "retract — если данные опровергают факт; keep — если всё верно.\n"
            "Не выдумывай данные: опирайся только на факты и фрагменты ниже.\n\n"
            + json.dumps(evidence, ensure_ascii=False)[:6000]
        )
        llm_call = openai_chat_caller(self.backend.base_url, self.extraction_model(), max_tokens=512)
        payload = _parse_json_loose(llm_call(prompt))
        verdict = str(payload.get("verdict") or "keep").strip().lower()
        if verdict not in ("keep", "update", "retract"):
            verdict = "keep"
        reason = str(payload.get("reason") or "").strip()[:1000]
        try:
            conf = float(payload.get("confidence", target.get("confidence", 0.8)))
        except (TypeError, ValueError):
            conf = float(target.get("confidence") or 0.8)
        conf = max(0.0, min(1.0, conf))

        if verdict == "retract":
            self.memory.retract(fid)
            return {"status": "ok", "verdict": "retract", "fact_id": fid, "reason": reason}
        if verdict == "update":
            s = str(payload.get("s") or "").strip()
            r = str(payload.get("r") or "").strip()
            o = str(payload.get("o") or "").strip()
            if s and r and o:
                doc = self.ingest(reason or f"Пересборка факта {fid}",
                                  session_id="temporal-distill", role="assistant",
                                  model=self.extraction_model())
                doc_id = doc.get("doc_id") or (doc.get("result") or {}).get("doc_id") or "manual-distill"
                source = {"doc_id": doc_id, "span": [0, max(1, len(reason))]}
                out = self.memory.supersede(
                    fid, s, r, o,
                    valid_from=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    source=source, confidence=conf)
                return {"status": "ok", "verdict": "update", "fact_id": fid,
                        "old_fact_id": fid, "reason": reason, "result": out}
        self.memory.confirm(fid, True)
        return {"status": "ok", "verdict": "keep", "fact_id": fid, "reason": reason}

    # ---------- управление нейронами (карта памяти) ----------

    def _llm_json(self, prompt: str, model: str = "", max_tokens: int = 768) -> dict:
        """Один LLM-вызов с моделью чата и «рыхлым» JSON (модели любят обёртки ```json)."""
        target = (model or "").strip()
        if target:
            with self.backend_lock:
                self.active_model = target
        call = openai_chat_caller(self.backend.base_url, self.extraction_model(), max_tokens=max_tokens)
        return _parse_json_loose(call(prompt))

    @staticmethod
    def _source_of(fact: dict) -> dict:
        """И1: переиспользуем провенант редактируемого факта — новый doc не нужен."""
        src = fact.get("source") or {}
        doc_id = src.get("doc_id")
        span = src.get("span")
        if doc_id and isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) for x in span):
            return {"doc_id": str(doc_id), "span": span}
        return {"doc_id": "manual-edit", "span": [0, 0]}

    @staticmethod
    def _valid_after(fact: dict) -> str:
        now = datetime.now(timezone.utc)
        raw = str(fact.get("valid_from") or "").replace("Z", "+00:00")
        try:
            old = datetime.fromisoformat(raw)
            if old.tzinfo is None:
                old = old.replace(tzinfo=timezone.utc)
        except ValueError:
            old = now - timedelta(seconds=1)
        if now <= old:
            now = old + timedelta(microseconds=1)
        return now.isoformat(timespec="microseconds")

    def dedupe_facts(self, model: str = "", fact_ids: Optional[List[str]] = None,
                     limit: int = 150) -> dict:
        """LLM ищет смысловые дубли среди фактов. Только предпросмотр, без записи."""
        facts = self.memory.list_facts(500)
        wanted = {str(i) for i in (fact_ids or []) if str(i)}
        if wanted:
            facts = [f for f in facts if f.get("fact_id") in wanted]
        items = [{"id": f.get("fact_id"), "text": f.get("text"),
                  "s": f.get("s"), "r": f.get("r"), "o": f.get("o")}
                 for f in facts[:max(2, min(int(limit), 500))]]
        if len(items) < 2:
            return {"status": "ok", "scanned": len(items), "groups": []}
        prompt = (
            "Ты — редактор базы знаний. Найди смысловые дубликаты среди фактов: "
            "один и тот же смысл, переформулированный или повторённый.\n"
            "Не объединяй факты просто потому, что у них общий субъект: объект и отношение должны "
            "значить то же самое. Не выдумывай id.\n"
            "Верни ТОЛЬКО JSON: {\"groups\":[{\"ids\":[\"f:...\",\"f:...\"],"
            "\"reason\":\"почему это дубли\",\"canonical\":\"итоговая формулировка\"}]}.\n"
            "Если дублей нет — {\"groups\":[]}.\n\n" + json.dumps(items, ensure_ascii=False)[:12000]
        )
        data = self._llm_json(prompt, model, max_tokens=1200)
        known = {i["id"] for i in items}
        groups, used = [], set()
        for g in data.get("groups") or []:
            if not isinstance(g, dict):
                continue
            ids = [str(i) for i in (g.get("ids") or []) if str(i) in known and str(i) not in used]
            ids = list(dict.fromkeys(ids))
            if len(ids) < 2:
                continue
            used.update(ids)
            groups.append({"ids": ids, "reason": str(g.get("reason") or "")[:600],
                           "canonical": str(g.get("canonical") or "")[:600]})
        return {"status": "ok", "scanned": len(items), "groups": groups}

    def edit_fact(self, fact_id: str, s: str = "", r: str = "", o: str = "",
                  confidence: float = 0.8, reason: str = "", model: str = "") -> dict:
        """Ручная правка: старый факт вытесняется новым с тем же документом-источником (I1, I4)."""
        fid = str(fact_id or "").strip()
        target = next((f for f in self.memory.list_facts(500) if f.get("fact_id") == fid), None)
        if target is None:
            raise ValueError("факт не найден или уже не действует")
        new_s = str(s or target.get("s") or "").strip()
        new_r = str(r or target.get("r") or "").strip()
        new_o = str(o or target.get("o") or "").strip()
        if not (new_s and new_r and new_o):
            raise ValueError("нужны субъект, отношение и объект")
        try:
            conf = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            conf = float(target.get("confidence") or 0.8)
        out = self.memory.supersede(
            fid, new_s, new_r, new_o, valid_from=self._valid_after(target),
            source=self._source_of(target), confidence=conf)
        new_id = out.get("fact_id") if isinstance(out, dict) else getattr(out, "fact_id", "")
        return {"status": "ok", "fact_id": fid, "new_fact_id": new_id,
                "s": new_s, "r": new_r, "o": new_o, "confidence": conf,
                "reason": str(reason or "")[:600], "result": out}

    def recombine_facts(self, fact_ids: List[str], model: str = "", apply: bool = False) -> dict:
        """Рекомбинация: LLM предлагает единую триаду, затем один факт вытесняет остальные."""
        ids = list(dict.fromkeys(str(i) for i in (fact_ids or []) if str(i)))
        if len(ids) < 2:
            raise ValueError("для слияния нужны хотя бы два факта")
        facts = [f for f in self.memory.list_facts(500) if f.get("fact_id") in set(ids)]
        if len(facts) < 2:
            raise ValueError("часть фактов не найдена или уже не действует")
        prompt = (
            "Ты — редактор базы знаний. Объедини несколько faktов в один, не теряя смысла.\n"
            "Верни ТОЛЬКО JSON: {\"s\":\"...\",\"r\":\"...\",\"o\":\"...\","
            "\"confidence\":0.0,\"reason\":\"кратко объясни слияние\"}.\n"
            "Не выдумывай детали: опирайся только на перечисленные факты.\n\n"
            + json.dumps([{"id": f.get("fact_id"), "text": f.get("text"),
                           "s": f.get("s"), "r": f.get("r"), "o": f.get("o"),
                           "confidence": f.get("confidence")} for f in facts],
                          ensure_ascii=False)[:8000]
        )
        data = self._llm_json(prompt, model, max_tokens=768)
        s = str(data.get("s") or "").strip()
        r = str(data.get("r") or "").strip()
        o = str(data.get("o") or "").strip()
        if not (s and r and o):
            raise ValueError("модель не вернула корректную триаду для слияния")
        try:
            conf = max(0.0, min(1.0, float(data.get("confidence", 0.85))))
        except (TypeError, ValueError):
            conf = 0.85
        reason = str(data.get("reason") or "")[:600]
        preview = {"s": s, "r": r, "o": o, "confidence": conf, "reason": reason,
                   "fact_ids": [f.get("fact_id") for f in facts]}
        if not apply:
            return {"status": "ok", "applied": False, "preview": preview}
        canonical = facts[0]
        out = self.memory.supersede(
            canonical["fact_id"], s, r, o, valid_from=self._valid_after(canonical),
            source=self._source_of(canonical), confidence=conf)
        new_id = out.get("fact_id") if isinstance(out, dict) else getattr(out, "fact_id", "")
        retracted, failed = [], []
        for f in facts[1:]:
            try:
                self.memory.retract(f["fact_id"])
                retracted.append(f["fact_id"])
            except Exception as exc:                 # noqa: BLE001 — один сбой не отменяет слияние
                failed.append({"fact_id": f["fact_id"], "error": str(exc)})
        return {"status": "ok", "applied": True, "preview": preview, "fact_id": new_id,
                "superseded": canonical["fact_id"], "retracted": retracted, "failed": failed}

    def forget_facts(self, fact_ids: Optional[List[str]] = None,
                     node_id: str = "") -> dict:
        """Удалить нейрон из активной памяти: отозвать все его Fact, аудит сохранить в журнале."""
        ids = {str(i) for i in (fact_ids or []) if str(i)}
        node = str(node_id or "").strip()
        if node and not ids:
            graph = self.memory.graph(1000)
            ids = {str(e.get("id")) for e in graph.get("edges", [])
                   if e.get("source") == node or e.get("target") == node}
        if not ids:
            raise ValueError("не указаны fact_id или node_id")
        retracted, failed = [], []
        for fid in sorted(ids):
            try:
                self.memory.retract(fid)
                retracted.append(fid)
            except Exception as exc:                 # noqa: BLE001 — неизвестный id не ломает остальные
                failed.append({"fact_id": fid, "error": str(exc)})
        return {"status": "ok", "retracted": retracted, "failed": failed, "node_id": node}

    # ---------- обслуживание ----------

    def consolidate_dry_run(self) -> dict:
        """«Сон» без обучения: отбор фактов и запись датасета. Обучение — отдельная команда.
        Сейчас работает только с локальной памятью: журнал MCP-режима читает сам сервер."""
        if self.memory_backend != "local":
            return {"status": "unavailable", "eligible": 0, "rehearsal": 0, "dataset": None,
                    "training": "не запущено",
                    "message": "«Сон» работает только с локальной памятью (PULSENET_MEMORY=local)"}
        plan = sleep.plan_consolidation(self.memory)
        path = None
        if plan["rows"]:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = sleep.write_dataset(plan["rows"], os.path.join(self.data_dir, "triplex", "sleep",
                                                                  f"dataset-{stamp}.jsonl"))
        return {"status": plan["status"], "eligible": plan["eligible"], "rehearsal": plan["rehearsal"],
                "dataset": path, "training": "не запущено: выполните `python -m pulsenet train-sleep`"}

    def wait_index(self, timeout: float = 60.0) -> bool:
        """Для тестов: дождаться опустошения очереди индексации."""
        deadline = time.time() + timeout
        while self._index_q.unfinished_tasks and time.time() < deadline:
            time.sleep(0.01)
        return self._index_q.unfinished_tasks == 0
