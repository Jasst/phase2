# -*- coding: utf-8 -*-
"""Приложение: чат с LLM, память ТРИПЛЕКСИИ и фоновая индексация.

Чат: память (поиск) → промпт → LLM (llama-server или LM Studio) → ответ.
После ответа реплика пользователя уходит в очередь индексации: документ, экстракция, факты.
Ошибки любого этапа пишутся в журнал ошибок и видны в интерфейсе (/api/status).
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional

from . import llm
from .triplex import HashEncoder, OpenAIEmbedder, TriplexMemory, make_extractor, openai_chat_caller
from .triplex.embed_server import EmbeddingServer
from .triplex.encoder import CachedEncoder, default_prefixes, list_model_ids, pick_embedding_model, probe_dim
from .triplex import sleep

CONTEXT_TURNS = 8
ERROR_LOG_SIZE = 50


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
        self.memory = self._make_memory(data_dir)
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
        return self.env.get("LMSTUDIO_MODEL", "").strip()

    def model_id(self) -> str:
        """Идентификатор модели для запроса. Запускает бэкенд при необходимости."""
        with self.backend_lock:
            return self.backend.ensure(self.default_rel()) or self.default_rel() or "local"

    def _make_encoder(self, data_dir: str):
        """Эмбеддер. Модель bge-m3 грузится один раз: отдельным процессом (TRIPLEX_EMBED_MODEL_PATH)
        или уже запущенным сервером (TRIPLEX_EMBED_URL). Иначе — офлайн-хеш. Сбой запуска не роняет чат."""
        env = self.env
        url = (env.get("TRIPLEX_EMBED_URL") or "").strip()
        model_path = (env.get("TRIPLEX_EMBED_MODEL_PATH") or "").strip()
        model = env.get("TRIPLEX_EMBED_MODEL", "").strip()          # пусто — определить автоматически
        dim_env = env.get("TRIPLEX_EMBED_DIM", "").strip()
        pooling = env.get("TRIPLEX_EMBED_POOLING", "cls").strip()
        if model_path and env.get("TRIPLEX_EMBED_AUTOSTART", "1") != "0":
            self.embed_server = EmbeddingServer(
                env.get("TRIPLEX_EMBED_BIN", "").strip() or env.get("LLAMACPP_SERVER_BIN", "llama-server").strip(),
                model_path, port=int(env.get("TRIPLEX_EMBED_PORT", "8091")), pooling=pooling,
                log_path=os.path.join(data_dir, "embed-server.log"))
            try:
                url = self.embed_server.start()
            except Exception as exc:              # noqa: BLE001
                self.log_error("эмбеддер (запуск)", exc)
                url = ""
        if url:
            try:
                if not model:
                    model = pick_embedding_model(list_model_ids(url)) or ""
                    if not model:
                        raise EmbeddingServerError(f"на {url} нет модели эмбеддингов: задайте TRIPLEX_EMBED_MODEL")
                dim = probe_dim(url, model)                      # размерность — из ответа модели
                if dim_env and int(dim_env) != dim:
                    self.log_error("эмбеддер (размерность)",
                                   ValueError(f"TRIPLEX_EMBED_DIM={dim_env}, а модель {model!r} даёт {dim}: беру {dim}"))
                auto_doc, auto_query = default_prefixes(model)
                doc_prefix = env.get("TRIPLEX_EMBED_DOC_PREFIX", auto_doc)
                query_prefix = env.get("TRIPLEX_EMBED_QUERY_PREFIX", auto_query)
                inner = OpenAIEmbedder(url, model, dim, doc_prefix=doc_prefix, query_prefix=query_prefix)
                self.embed_model = model
                return CachedEncoder(inner, os.path.join(data_dir, "triplex", "embed_cache.jsonl"),
                                     tag=f"{model}|{dim}|{pooling}|{doc_prefix}|{query_prefix}")
            except Exception as exc:              # noqa: BLE001
                self.log_error("эмбеддер (подключение)", exc)
        return HashEncoder(512)

    def _make_memory(self, data_dir: str) -> TriplexMemory:
        encoder = self._make_encoder(data_dir)
        extractor = None
        if self.env.get("TRIPLEX_EXTRACT", "1") != "0":
            speaker = self.env.get("TRIPLEX_USER_NAME", "").strip() or None

            def llm_call(prompt: str) -> str:
                base = self.backend.base_url
                return openai_chat_caller(base, self.model_id())(prompt)
            extractor = make_extractor(llm_call, speaker=speaker)
        min_cos = float(self.env.get("TRIPLEX_MIN_COS", "0.35"))
        return TriplexMemory(os.path.join(data_dir, "triplex"), encoder=encoder, extractor=extractor,
                             min_cos=min_cos)

    def _index_loop(self) -> None:
        """Одна фоновая очередь: экстракция идёт через ту же модель, что и чат, поэтому по одной."""
        while True:
            text, chat_id, role = self._index_q.get()
            try:
                self.ingest(text, session_id=chat_id or "default", role=role)
            except Exception as exc:              # noqa: BLE001
                self.log_error("индексация", exc)
            finally:
                self._index_q.task_done()

    def ingest(self, text: str, session_id: str = "", role: str = "user", recorded_at=None) -> dict:
        """Единая точка записи: сбой экстракции не исключение, а статус — и он попадает в журнал ошибок."""
        out = self.memory.ingest(text, session_id=session_id, role=role, recorded_at=recorded_at)
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

    # ---------- чат ----------

    def _history(self, chat_id: str) -> List[dict]:
        with self.history_lock:
            turns = self.history.get(chat_id or "default", [])[-CONTEXT_TURNS * 2:]
            return [{"role": t["role"], "content": t["content"]} for t in turns]

    def _append_history(self, chat_id: str, role: str, content: str) -> None:
        with self.history_lock:
            self.history.setdefault(chat_id or "default", []).append({"role": role, "content": content})

    def chat(self, message: str, chat_id: str, on_delta: Callable[[str, str], None],
             cancelled: Optional[Callable[[], bool]] = None) -> str:
        """on_delta(kind, text): kind — 'token' или 'reasoning'. Возвращает итоговый ответ."""
        self.stop_flag = False
        mem = self.memory_context(message)
        messages = self._history(chat_id)
        if mem:
            messages = [{"role": "system", "content": mem}] + messages
        messages = messages + [{"role": "user", "content": message}]
        with self.backend_lock:
            rel = self.backend.ensure(self.default_rel()) or self.default_rel() or "local"
        parts: List[str] = []

        def delta(kind, text):
            if kind == "token":
                parts.append(text)
            on_delta(kind, text)

        def is_cancelled():
            return bool(cancelled and cancelled()) or self.stop_flag

        self.backend.stream(rel, messages, delta, cancelled=is_cancelled)
        answer = "".join(parts).strip()
        if answer:
            self._append_history(chat_id, "user", message)
            self._append_history(chat_id, "assistant", answer)
        self.index_async(message, chat_id, "user")
        return answer

    def stop(self) -> None:
        self.stop_flag = True

    def status(self) -> dict:
        return {
            "backend": self.backend.name,
            "model": getattr(self.backend, "loaded", None),
            "memory_enabled": self.memory_enabled,
            "index_pending": self.index_pending(),
            "memory": self.memory.stats(),
            "embedder": {"name": self.memory.encoder.name,
                         "server_running": bool(self.embed_server and self.embed_server.is_running()),
                         "server_starts": self.embed_server.starts if self.embed_server else 0,
                "model": self.embed_model or None},
            "errors": self.recent_errors(),
        }

    # ---------- обслуживание ----------

    def consolidate_dry_run(self) -> dict:
        """«Сон» без обучения: отбор фактов и запись датасета. Обучение — отдельная команда."""
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
