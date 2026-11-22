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
import re
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from . import llm
from .mcp_client import MCPClient, MemoryUnavailable, RemoteMemory
from .memory_service import build_encoder
from .triplex import TriplexMemory, make_extractor, openai_chat_caller
from .triplex import sleep
from .toolcalls import (COMMANDS, MAX_CALLS_PER_STEP, ToolCallFilter, agent_limits,
                         parse_calls, parse_command, tool_result_block)
from .tools import SEQUENTIAL_TOOLS, SUBAGENT_DENY, ChatTools, Outcome, catalog, instructions
from .unified_mcp.store import IdentityStore
from .tts import SileroTTS
from .workspace import Workspace

CONTEXT_TURNS = 8
ERROR_LOG_SIZE = 50
LLM_RETRIES = 2                 # повторы при сбое соединения с LLM, если пользователь ещё ничего не увидел
LLM_RETRY_DELAY = 1.5           # сек, умножается на номер попытки
MEMORY_RETRIES = 12          # индексация ждёт недоступный MCP: 12 попыток
MEMORY_RETRY_SECONDS = 5.0

# Персона явно сообщает модели про инструменты. Без неё локальная модель отвечает
# «я текстовая нейросеть и не могу отправить картинку», хотя image_generate подключён.
ASSISTANT_PERSONA = (
    "Ты — ассистент ТРИПЛЕКСИИ. У тебя есть инструменты MCP-сервера pulsenet-unified, "
    "включая image_generate. Если пользователь просит изображение, картинку, рисунок или иллюстрацию — "
    "вызови image_generate и отправь результат. Не говори, что ты «текстовая нейросеть», не проси "
    "вставить промпт в Midjourney/DALL·E и не советуй другие сервисы: ты можешь создать изображение сам(а). "
    "Для поиска фактов и свежих данных используй доступные инструменты, а не догадки."
)


WORKSPACE_TOOLS_NAMES = ("skill_list", "skill_read", "skill_write", "skill_patch",
                         "note_write", "note_append", "note_read", "note_list", "todo_write")
PLAN_TOOL_NAME = "todo_write"
WORKFLOW_HINT = (
    "Как работать с навыками и заметками: "
    "1) перед задачей, похожей на навык из списка ниже, загрузи его через skill_read и следуй шагам; "
    "2) если нашёл последовательность, которая сработала и повторяется, сохрани её навыком через "
    "skill_write (короткие шаги, без личных данных и секретов); если навык устарел — skill_patch; "
    "3) заметки для себя о работе и о пользователе, а также выводы из ошибок — note_write с kind=self; "
    "обычные заметки — kind=note; "
    "4) картинку из image_generate можно вставить в заметку: image_url=\"last\"; "
    "5) субагент (subagent) — для самостоятельной подзадачи, которую можно описать одной фразой "
    "(поиск в памяти, сбор фактов, картинка). В task — что вернуть, в context — нужный кусок вопроса. "
    "Не делегируй то, что сделаешь одним вызовом сам. Несколько subagent в одном шаге идут параллельно."
)


TRIVIAL_RE = re.compile(r"^(привет\w*|здравствуй\w*|спасибо|благодарю|пока|ок|окей|ага|да|нет|хорошо|"
                        r"понятно|hi|hello|thanks|thank you|ok|okay|bye)[\s!.?,)]*$", re.IGNORECASE)

SUBAGENT_PERSONA = (
    "Ты — субагент. Выполни одну подзадачу коротко и точно. Инструменты вызывай, только если без них "
    "нельзя; результаты не выдумывай. В конце верни итог одним-двумя абзацами, без вступлений."
)


def looks_multistep(text: str) -> bool:
    """Грубая эвристика: длинный запрос или несколько связок/пунктов — похоже на план из шагов."""
    t = str(text or "").lower()
    markers = sum(t.count(m) for m in ("\n- ", "\n* ", "\n1.", "\n2.", " затем ", " потом ",
                                       " после этого ", " сначала ", " then ", " and then "))
    return markers >= 2 or (len(t) > 400 and markers >= 1)


def _env_num(env: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(env.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _preview(text: str, limit: int = 600) -> str:
    """Короткий кусок результата инструмента для раскрытия карточки в интерфейсе."""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


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
        self.limits = agent_limits(self.env)
        # Субагенты делят одну модель: их запросы к LLM идут по очереди (или по AGENT_LLM_PARALLEL).
        self._llm_slot = threading.BoundedSemaphore(self.limits["llm_parallel"])
        # Навыки, заметки и правила агента — в data_unified (та же папка, что у unified MCP).
        ws_root = (self.env.get("AGENT_WORKSPACE_DIR") or self.env.get("PULSENET_DATA_DIR")
                   or os.path.join(os.path.dirname(os.path.abspath(data_dir)), "data_unified"))
        self.workspace = Workspace(ws_root,
                                   skills_write=self.env.get("AGENT_SKILLS_WRITE", "1") == "1")
        # Корневой AGENTS.md описывает инструменты для внешних клиентов (opencode). В чат его
        # подключают только по AGENT_PROJECT_RULES=1; по умолчанию чат читает data_unified/AGENTS.md.
        self.project_agents = None
        if self.env.get("AGENT_PROJECT_RULES", "0") == "1":
            self.project_agents = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "AGENTS.md")
        # Один клиент на все MCP-вызовы: память, каталог инструментов и генерация картинок.
        # Объект не подключается до первого вызова, поэтому его можно создавать всегда.
        self._mcp = MCPClient(
            (self.env.get("PULSENET_MCP_URL") or "http://127.0.0.1:8010/mcp").strip(),
            (self.env.get("PULSENET_CHAT_MCP_TOKEN") or "").strip())
        self.mcp_tools_enabled = (bool(self._mcp.token) and
                                  self.env.get("PULSENET_CHAT_TOOLS", "1") != "0")
        self._mcp_specs_cache: Optional[Dict[str, dict]] = None
        # local — свой журнал в data_dir (как раньше); mcp — память у MCP-сервера pulsenet-unified,
        # общая для всех ваших агентов. Значение по умолчанию local: без .env поведение прежнее.
        self.memory_backend = (self.env.get("PULSENET_MEMORY") or "local").strip().lower()
        self.memory = self._make_remote_memory() if self.memory_backend == "mcp" else self._make_memory(data_dir)
        # Индексация не должна конкурировать с ответом чата: пока идёт ход, фоновые записи ждут.
        self._busy_cv = threading.Condition()
        self._chat_busy = 0
        self.index_cfg = {
            "mode": (self.env.get("INDEX_MODE", "batch") or "batch").strip().lower(),
            "min_chars": _env_num(self.env, "INDEX_MIN_CHARS", 15, 0, 5000),
            "debounce": _env_num(self.env, "INDEX_DEBOUNCE_SECONDS", 8, 0.1, 3600),
            "batch_max": int(_env_num(self.env, "INDEX_BATCH_MAX", 8, 1, 100)),
        }
        self._index_recent: "deque[int]" = deque(maxlen=200)
        # Озвучка ответов (Silero): модель грузится лениво, при первом запросе озвучки
        # Модель лежит рядом с данными агента: data_unified/tts/v4_ru.pt (не в data/)
        self.tts = SileroTTS(self.env, self.workspace.root)
        self._mcp_cool_until = 0.0
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
        return RemoteMemory(self._mcp)

    # ---------- MCP-инструменты (динамический каталог) ----------

    def _mcp_tool_call(self, name: str, args: dict, timeout=None):
        """Вызов MCP-инструмента. image_generate ждёт дольше обычного (рендер на GPU)."""
        if timeout is None and name == "image_generate":
            timeout = self.limits["image_timeout"]
        return self._mcp.call(name, args, timeout=timeout)

    def _mcp_tool_specs(self) -> Dict[str, dict]:
        """Каталог MCP-инструментов для модели: image_generate + discovery через list_tools().

        Ошибка discovery не отключает инструменты: базовая схема image_generate всё равно
        остаётся в промпте. Кэш живёт до перезапуска процесса — list_tools не дёргаем каждый ход.
        """
        if not self.mcp_tools_enabled:
            return {}
        if self._mcp_specs_cache is not None:
            return self._mcp_specs_cache
        specs: Dict[str, dict] = {}
        try:
            discovered = self._mcp.list_tools()
        except Exception as exc:                  # noqa: BLE001 — сервер может быть выключен
            self.log_error("обнаружение MCP-инструментов", exc)
            discovered = []
        # image_generate — ядро: доступен даже если list_tools не удался.
        from .tools import SPECS  # локальный импорт, чтобы не было цикла
        specs["image_generate"] = {"mcp_name": "image_generate",
                                   "description": SPECS["image_generate"]["description"],
                                   "parameters": SPECS["image_generate"]["parameters"],
                                   "timeout": self.limits["image_timeout"]}
        priority = ("triplex_search", "triplex_facts", "triplex_graph", "activity_recent",
                    "web_search", "web_fetch", "pulsenet_guide", "whoami")
        local = {"memory_search", "memory_remember", "web_search", "web_fetch"}
        ordered = []
        by_name = {t["name"]: t for t in discovered}
        for n in priority:
            if n in by_name and n not in local and n not in ordered:
                ordered.append(n)
        for t in discovered:
            n = t["name"]
            if n not in local and n not in ordered and n != "image_generate":
                ordered.append(n)
        # Не вываливаем все 27 схем в локальную модель: ограничиваем топ лимитом.
        room = max(0, self.limits["native_max"] - len(specs))
        for n in ordered[:room]:
            if n.startswith("code_"):       # доступ к коду — только по явному разрешению сервера
                continue
            t = by_name[n]
            shown = "mcp__" + n if n in local else n
            specs[shown] = {"mcp_name": n, "description": (t.get("description") or "")[:300],
                            "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}
        self._mcp_specs_cache = specs
        return specs

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
        """Индексация пакетами. Сообщения копятся, пока пользователь пишет (debounce), и записываются
        только когда модель свободна — то есть нет активного хода чата. Один ingest на чат вместо
        одного LLM-вызова экстракции на каждое сообщение. Недоступный MCP — повторы, не потеря."""
        cfg = self.index_cfg
        while True:
            first = self._index_q.get()
            batch = [first]
            while len(batch) < cfg["batch_max"]:
                try:
                    batch.append(self._index_q.get(timeout=cfg["debounce"]))
                except queue.Empty:
                    break
            self._wait_model_idle()
            try:
                self._flush_batch(batch)
            except Exception as exc:              # noqa: BLE001
                self.log_error("индексация", exc)
            finally:
                for _ in batch:
                    self._index_q.task_done()

    def _wait_model_idle(self) -> None:
        with self._busy_cv:
            while self._chat_busy > 0:
                self._busy_cv.wait(timeout=1.0)

    def _flush_batch(self, batch: list) -> None:
        per_chat: Dict[str, List[str]] = {}
        roles: Dict[str, str] = {}
        for text, chat_id, role in batch:
            key = chat_id or "default"
            per_chat.setdefault(key, []).append(text)
            roles.setdefault(key, role)
        for chat_id, texts in per_chat.items():
            joined = "\n".join(texts)
            for attempt in range(MEMORY_RETRIES):
                try:
                    self.ingest(joined, session_id=chat_id, role=roles[chat_id])
                    break
                except MemoryUnavailable as exc:
                    if attempt == MEMORY_RETRIES - 1:
                        self.log_error("индексация: память недоступна, сообщение не сохранено", exc)
                    else:
                        time.sleep(MEMORY_RETRY_SECONDS)

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

    def index_async(self, text: str, chat_id: str, role: str = "user", force: bool = False) -> None:
        """Поставить текст в индексацию. Обычные сообщения: короткие, команды и повторы пропускаются;
        force=True — явный /remember и инструмент memory_remember."""
        if not self.memory_enabled:
            return
        clean = " ".join(str(text or "").split())
        if not force:
            if self.index_cfg["mode"] == "off" or len(clean) < self.index_cfg["min_chars"]:
                return
            if TRIVIAL_RE.match(clean):           # приветствия и служебные реплики фактов не содержат
                return
            key = hash((chat_id or "default", clean))
            if key in self._index_recent:
                return
            self._index_recent.append(key)
        self._index_q.put((clean, chat_id, role))

    def index_pending(self) -> int:
        return self._index_q.qsize()

    def _identity_context(self, uid: Optional[str]) -> str:
        """Подтверждённая цепочка личной идентичности пользователя (только committed-звенья)."""
        if not uid:
            return ""
        try:
            chain = IdentityStore(self.workspace.root).chain(uid)["chain"]
        except (ValueError, OSError) as exc:      # noqa: BLE001
            self.log_error("цепочка идентичности", exc)
            return ""
        lines = [f"- {c.get('content', '')}" for c in chain[-12:] if c.get("content")]
        if not lines:
            return ""
        return ("Твоя личная цепочка идентичности (подтверждённые самоформулировки; держись их и не "
                "пересказывай вслух):\n" + "\n".join(lines)[:1200])

    def _run_subagent(self, task: str, tools: ChatTools, names: List[str], requested: List[str],
                      on_delta, cancelled, origin: str = "") -> Outcome:
        """Субагент: отдельный короткий цикл с тем же LLM. Его вызовы видны в трассе (поле sub).
        Ошибка модели или инструмента не роняет родительский ход: возвращается как результат."""
        try:
            return self._run_subagent_impl(task, tools, names, requested, on_delta, cancelled, origin)
        except Exception as exc:                  # noqa: BLE001
            self.log_error("субагент", exc)
            return Outcome(False, f"Субагент не выполнил задачу: {exc}. Сделай это сам или ответь по имеющемуся.",
                           label=f"Субагент: «{_preview(task, 60)}»", detail="ошибка")

    def _run_subagent_impl(self, task: str, tools: ChatTools, names: List[str], requested: List[str],
                           on_delta, cancelled, origin: str = "") -> Outcome:
        allowed = [n for n in names if n not in SUBAGENT_DENY]
        if requested:
            allowed = [n for n in allowed if n in requested] or allowed
        sid = "s" + uuid.uuid4().hex[:6]
        lim = self.limits
        system = SUBAGENT_PERSONA
        if origin:
            system += f"\n\nОбщий вопрос пользователя (для контекста, не отвечай на него целиком): {_preview(origin, 600)}"
        ins = instructions(allowed, tools.specs(allowed))
        if ins:
            system += "\n\n" + ins
        messages: List[dict] = [{"role": "system", "content": system}, {"role": "user", "content": task}]
        target = self.active_model or self.default_rel()

        def sub_delta(kind, payload):
            if kind == "tool" and isinstance(payload, dict):
                payload = {**payload, "sub": sid}
            on_delta(kind, payload)

        calls = 0
        final = ""
        for rnd in range(lim["sub_rounds"] + 1):
            flt = ToolCallFilter()
            visible: List[str] = []

            def tok(kind, text, flt=flt, visible=visible):
                if kind == "token":
                    visible.append(flt.feed(text))

            with self._llm_slot:
                self.backend.stream(target, messages, tok, cancelled=cancelled)
            visible.append(flt.finish())
            text = "".join(visible).strip()
            raw = flt.calls_raw[: lim["calls_per_step"]]
            if not raw or rnd == lim["sub_rounds"] or cancelled():
                final = text
                break
            messages.append({"role": "assistant",
                             "content": text + "".join(f"<tool_call>{r}</tool_call>" for r in raw)})
            blocks: List[str] = []
            for call in parse_calls(raw):
                name = call.get("name") or "?"
                args = call.get("arguments") or {}
                calls += 1
                if call.get("error"):
                    out = Outcome(False, call["error"], label="Инструмент", detail=call["error"])
                elif name not in allowed:
                    out = Outcome(False, f"Субагенту недоступен инструмент {name}. "
                                         f"Доступны: {', '.join(allowed) or 'нет'}.",
                                  label="Инструмент", detail=name)
                    sub_delta("tool", {"id": "t" + uuid.uuid4().hex[:8], "name": name, "status": "error",
                                       "step": getattr(self, "_agent_step", 0),
                                       "label": f"Нет доступа у субагента: {name}",
                                       "detail": "субагенту не разрешено", "preview": ""})
                else:
                    out = self._call_tool(tools, name, args, sub_delta)
                blocks.append(tool_result_block(name, out.text, lim["result_chars"]))
            messages.append({"role": "user", "content": "\n".join(blocks)})
        return Outcome(True, final or "Субагент не дал ответа.", label=f"Субагент: «{_preview(task, 60)}»",
                       detail=f"вызовов: {calls}")

    def _agent_context(self, message: str, names: List[str]) -> str:
        """Правила (AGENTS.md), каталог навыков, самонаблюдения и навыки, подходящие к запросу.
        Всё необязательно: при ошибке чтения агент работает без этого блока."""
        parts: List[str] = []
        ws_on = any(n in names for n in WORKSPACE_TOOLS_NAMES)
        if ws_on:
            parts.append(WORKFLOW_HINT)
        if PLAN_TOOL_NAME in names and looks_multistep(message):
            parts.append("Задача похоже из нескольких шагов: сначала вызови todo_write с планом, "
                         "потом выполняй шаги и отмечай прогресс.")
        try:
            rules = self.workspace.instructions(self.project_agents)
            if rules:
                parts.append(rules)
            catalog_text = self.workspace.skill_catalog()
            if catalog_text:
                parts.append(catalog_text)
            notes = self.workspace.self_notes()
            if notes:
                parts.append(notes)
            for skill in self.workspace.skill_match(message, k=1):
                body = skill["body"].strip()[:4000]
                parts.append(f"Подходящий навык «{skill['name']}» (применяй его шаги):\n{body}")
        except Exception as exc:                  # noqa: BLE001
            self.log_error("рабочее пространство агента", exc)
            return ""
        return "\n\n".join(parts)

    def memory_context(self, message: str) -> str:
        if not self.memory_enabled:
            return ""
        if time.monotonic() < self._mcp_cool_until:
            return ""                             # сервер памяти недавно не отвечал: не ждём его каждый ход
        try:
            ctx = self.memory.recall(message)["context"]
        except Exception as exc:                  # noqa: BLE001
            if isinstance(exc, MemoryUnavailable):
                self._mcp_cool_until = time.monotonic() + self.limits["mcp_cooldown"]
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
        self.index_async(text, chat_id, "user", force=True)
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
        """Исполняет инструмент и сообщает интерфейсу о ходе: running → done/error, источники.
        step — номер раунда агента; ms — длительность; preview — что вернулось (для раскрытия в UI)."""
        cid = "t" + uuid.uuid4().hex[:8]
        step = getattr(self, "_agent_step", 0)     # номер раунда ставит цикл chat()
        started = time.monotonic()
        on_delta("tool", {"id": cid, "name": name, "status": "running", "step": step,
                          "label": tools.describe(name, args)})
        out = tools.run(name, args)
        on_delta("tool", {"id": cid, "name": name, "status": "done" if out.ok else "error",
                          "step": step, "label": out.label, "detail": out.detail,
                          "ms": int((time.monotonic() - started) * 1000),
                          "preview": _preview(out.text)})
        if out.sources:
            on_delta("sources", out.sources)
        if out.image:
            # Картинка уходит отдельным SSE-событием; в.tool_trace остаётся только подпись.
            on_delta("image", out.image)
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
             cancelled: Optional[Callable[[], bool]] = None, web: bool = False, model: str = "",
             uid: Optional[str] = None) -> str:
        """Ход чата. Пока он идёт, индексация ждёт: модель занята ответом."""
        with self._busy_cv:
            self._chat_busy += 1
        try:
            return self._chat_impl(message, chat_id, on_delta, cancelled=cancelled, web=web,
                                   model=model, uid=uid)
        finally:
            with self._busy_cv:
                self._chat_busy -= 1
                self._busy_cv.notify_all()

    def _chat_impl(self, message: str, chat_id: str, on_delta: Callable[[str, object], None],
                   cancelled: Optional[Callable[[], bool]] = None, web: bool = False, model: str = "",
                   uid: Optional[str] = None) -> str:
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

        # Инструменты: локальные + динамический каталог MCP (image_generate, triplex_*, ...).
        mcp_specs = self._mcp_tool_specs()
        tools = ChatTools(recall=self._tool_recall,
                          remember=lambda text: self._tool_remember(text, chat_id),
                          mcp_call=self._mcp_tool_call if self.mcp_tools_enabled else None,
                          mcp_specs=mcp_specs,
                          workspace=self.workspace,
                          on_event=lambda kind, payload: on_delta(kind, payload))
        # Субагент получает инструменты текущего хода (без записи и без рекурсии).
        # Подключаем до available(): иначе инструмент subagent не попадёт в каталог.
        tools.set_subagent(lambda task, requested: self._run_subagent(
            task, tools, names, requested, on_delta, is_cancelled, origin=message))
        names = tools.available(web=web, memory=self.memory_enabled)
        specs = tools.specs(names)
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

        # 2) контекст: персона + память по вопросу + инструкция по инструментам
        system = [ASSISTANT_PERSONA]
        ident = self._identity_context(uid)
        if ident:
            system.append(ident)
        rules = self._agent_context(user_text, names)
        if rules:
            system.append(rules)
        mem = self.memory_context(user_text)
        if mem:
            system.append(mem)
        if names:
            system.append(instructions(names, specs))
        messages: List[dict] = [{"role": "system", "content": "\n\n".join(system)}] if system else []
        messages += self._history(chat_id)
        content = user_text + ("\n\n" + note if note else "")
        messages.append({"role": "user", "content": content})

        target = (model or "").strip() or self.default_rel()
        with self.backend_lock:
            rel = self.backend.ensure(target) or target or "local"
            self.active_model = rel      # экстрактор/индексация используют ту же модель, не грузят вторую

        # 3) агентный цикл: запрос → инструменты → реакт → инструменты → ... → ответ.
        # Лимиты из env; один вызов инструмента не занимает больше result_chars контекста.
        lim = self.limits
        per_step = lim["calls_per_step"]
        max_rounds = lim["max_rounds"]
        hard_cap = lim["hard_cap"]
        max_calls = lim["max_calls"]
        reflection = lim["reflection_budget"]
        rounds = calls_used = failures = stuck = 0
        seen_calls: Dict[str, str] = {}
        trace: List[str] = []
        stop_reason = "model_done"
        # Нативный function calling — опция (TOOL_NATIVE=1). По умолчанию текстовый протокол:
        # локальные 26B-модели часто не умеют tool_calls, а llm.py при HTTP 400 откатывается.
        native_tools = None
        if self.env.get("TOOL_NATIVE", "0") == "1" and names:
            native_tools = [{"type": "function", "function": {
                "name": n,
                "description": (specs.get(n) or {}).get("description", ""),
                "parameters": (specs.get(n) or {}).get("parameters") or {"type": "object", "properties": {}},
            }} for n in names[:lim["native_max"]]]

        while True:
            flt = ToolCallFilter()
            step: List[str] = []
            native: List[dict] = []

            def delta(kind, text, flt=flt, step=step, native=native):
                if kind == "token":
                    visible = flt.feed(text)
                    if visible:
                        step.append(visible)
                        emit_text(visible)
                elif kind == "native_tool_calls":
                    native.extend(text)
                else:
                    on_delta(kind, text)

            stream_err = None
            for attempt in range(LLM_RETRIES + 1):
                try:
                    if native_tools:
                        self.backend.stream(rel, messages, delta, cancelled=is_cancelled, tools=native_tools)
                    else:
                        self.backend.stream(rel, messages, delta, cancelled=is_cancelled)
                    stream_err = None
                    break
                except llm.BackendError as exc:
                    stream_err = exc
                    # повторяем, только если пользователь ещё ничего не увидел и ход не отменён
                    if step or native or flt.calls_raw or is_cancelled() or attempt == LLM_RETRIES:
                        break
                    time.sleep(LLM_RETRY_DELAY * (attempt + 1))
            if stream_err is not None:
                self.log_error("модель", stream_err)
                stop_reason = "llm_error"
                break
            tail = flt.finish()
            if tail:
                step.append(tail)
                emit_text(tail)
            native_mode = bool(native and not flt.calls_raw)
            raw_calls = flt.calls_raw[:per_step]
            if native_mode:
                raw_calls = [json.dumps({"name": c["name"], "arguments": c["arguments"]},
                                        ensure_ascii=False) for c in native[:per_step]]

            if is_cancelled():
                stop_reason = "cancelled"
                break
            if not raw_calls:
                stop_reason = "model_done"
                break
            # Проверяем лимит ПОСЛЕ стриминга: модель уже успела дать видимый текст,
            # поэтому на последнем раунде пользователь всё равно получит ответ.
            if rounds >= min(max_rounds, hard_cap) or calls_used >= max_calls:
                if stuck >= 2:
                    stop_reason = "no_progress"
                else:
                    stop_reason = "round_limit" if rounds >= min(max_rounds, hard_cap) else "call_limit"
                break

            rounds += 1
            assistant = {"role": "assistant",
                         "content": "".join(step) + "".join(f"<tool_call>{r}</tool_call>" for r in raw_calls)}
            if native_mode:
                assistant["tool_calls"] = []
                for i, c in enumerate(native[:per_step]):
                    tid = c.get("id") or ("call_" + uuid.uuid4().hex[:8])
                    c["id"] = tid
                    assistant["tool_calls"].append({"id": tid, "type": "function", "function": {
                        "name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}})
            messages.append(assistant)
            blocks: List[str] = []
            native_msgs: List[dict] = []
            had_error = False
            # 1) разбор по порядку: повторы, ошибки, недоступные инструменты — без исполнения;
            # 2) исполнение: независимые вызовы — параллельно, инструменты записи — по очереди;
            # 3) результаты возвращаются модели в исходном порядке.
            items: List[dict] = []
            batch_sigs: Dict[str, int] = {}
            for idx, call in enumerate(parse_calls(raw_calls)):
                if calls_used >= max_calls:
                    break
                name = call.get("name") or "?"
                args = call.get("arguments") or {}
                signature = name + ":" + json.dumps(args, sort_keys=True, ensure_ascii=False)
                calls_used += 1
                if signature in seen_calls:
                    # Повтор того же вывода: не перевызываем дорогой инструмент (особенно генерацию).
                    stuck += 1
                    out = Outcome(True, seen_calls[signature], label=tools.describe(name, args),
                                  detail="результат из кэша")
                    on_delta("tool", {"id": "t" + uuid.uuid4().hex[:8], "name": name, "status": "done",
                                      "label": out.label, "detail": out.detail})
                    items.append({"idx": idx, "name": name, "out": out})
                elif signature in batch_sigs:
                    stuck += 1
                    items.append({"idx": idx, "name": name, "dup": batch_sigs[signature]})
                elif call.get("error"):
                    out = Outcome(False, call["error"], label="Инструмент", detail=call["error"])
                    on_delta("tool", {"id": "t" + uuid.uuid4().hex[:8], "name": name, "status": "error",
                                      "label": "Ошибка вызова", "detail": call["error"]})
                    items.append({"idx": idx, "name": name, "out": out})
                elif name not in names:
                    out = Outcome(False, f"Инструмент {name} недоступен. Доступны: {', '.join(names) or 'нет'}.",
                                  label="Инструмент", detail=name)
                    on_delta("tool", {"id": "t" + uuid.uuid4().hex[:8], "name": name, "status": "error",
                                      "label": f"Нет инструмента: {name}", "detail": ""})
                    items.append({"idx": idx, "name": name, "out": out})
                else:
                    batch_sigs[signature] = len(items)
                    items.append({"idx": idx, "name": name, "args": args, "sig": signature, "run": True})

            to_run = [it for it in items if it.get("run")]
            self._agent_step = rounds
            if len(to_run) > 1 and not any(it["name"] in SEQUENTIAL_TOOLS for it in to_run):
                with ThreadPoolExecutor(max_workers=min(len(to_run), lim["parallel"])) as pool:
                    futures = [pool.submit(self._call_tool, tools, it["name"], it["args"], on_delta)
                               for it in to_run]
                    for it, fut in zip(to_run, futures):
                        try:
                            it["out"] = fut.result()
                        except Exception as exc:          # noqa: BLE001 — инструмент не роняет ход
                            it["out"] = Outcome(False, f"Ошибка инструмента {it['name']}: {exc}",
                                                label="Инструмент", detail=_preview(str(exc), 120))
            else:
                for it in to_run:
                    it["out"] = self._call_tool(tools, it["name"], it["args"], on_delta)
            for it in items:
                if "dup" in it:
                    it["out"] = items[it["dup"]]["out"]
                if it.get("run"):
                    seen_calls[it["sig"]] = it["out"].text

            blocks: List[str] = []
            native_msgs: List[dict] = []
            had_error = False
            for it in items:
                out = it["out"]
                name = it["name"]
                idx = it["idx"]
                if not out.ok:
                    failures += 1
                    had_error = True
                trace.append(f"{name}{'✓' if out.ok else '✕'}")
                blocks.append(tool_result_block(name, out.text, lim["result_chars"]))
                if native_mode and idx < len(native):
                    native_msgs.append({"role": "tool", "tool_call_id": native[idx].get("id") or "",
                                        "content": out.text[:lim["result_chars"]]})

            if had_error and reflection > 0:
                reflection -= 1
                messages.append({"role": "system", "content": (
                    "Инструмент вернул ошибку. Измени аргументы, выбери другой инструмент "
                    "или ответь по уже собранным данным — не выдумывай результат.")})
            final_hint = "Это последний раунд вызовов: дай итоговый ответ по собранным результатам."
            if native_mode:
                messages.extend(native_msgs)
                if rounds >= max_rounds or calls_used >= max_calls:
                    messages.append({"role": "user", "content": final_hint})
            else:
                last = "\n".join(blocks)
                if rounds >= max_rounds or calls_used >= max_calls:
                    last += "\n\n" + final_hint
                messages.append({"role": "user", "content": last})

            on_delta("trace", {"round": rounds, "calls": len(blocks), "failures": failures,
                               "state": "running"})
            if failures >= 4:
                stop_reason = "failures"
                break

        on_delta("trace", {"round": rounds, "calls": calls_used, "failures": failures,
                           "state": "done", "stop": stop_reason})
        answer = "".join(parts).strip()
        if not answer:
            fallback = {"round_limit": "Достигнут лимит шагов — отвечаю по собранным данным.",
                        "call_limit": "Достигнут лимит вызовов инструментов — отвечаю по собранным данным.",
                        "failures": "Инструменты не справились — отвечаю по собранным данным.",
                        "no_progress": "Отвечаю по собранным данным.",
                        "cancelled": "Ответ остановлен.",
                        "llm_error": "Модель не ответила: проверь, что LM Studio или llama.cpp запущены и модель загружена."}.get(stop_reason)
            if fallback:
                emit_text(fallback)
                answer = "".join(parts).strip()
        if answer:
            # Компактный след кладём в реплику пользователя: следующий ход знает,
            # что уже сделано, и в истории не появляется два ассистента подряд.
            saved_user = message
            if trace:
                saved_user += (f"\n[инструменты: {', '.join(trace[:12])}] ({stop_reason})")
            self._append_history(chat_id, "user", saved_user)
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
