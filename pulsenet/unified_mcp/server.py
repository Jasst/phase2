# -*- coding: utf-8 -*-
"""Единый MCP-сервер (Streamable HTTP, stateless, JSON-ответы).

Инструменты (все, кроме whoami, работают в контексте токена):
  память ТРИПЛЕКСИИ : triplex_ingest, triplex_search, triplex_query, triplex_assert,
                      triplex_supersede, triplex_retract, triplex_confirm,
                      triplex_define_relation, triplex_stats, triplex_check
  память (сервис)   : triplex_graph (карта нейронов), activity_recent (журнал действий)
  личность          : identity_propose, identity_commit, identity_chain
  общее ядро        : core_get; core_propose / core_commit — только администратор
  интернет          : web_search, web_fetch
  код               : code_list, code_read, code_search — только CODE_ACCESS_USERS
  служебное         : whoami, pulsenet_guide (описание для агентов)
"""
from __future__ import annotations

import functools
import inspect
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette

from ..triplex.journal import ConflictError, InvariantError
from ..triplex.mcp import _fact_dict
from . import web
from .activity import ActivityLog
from .auth import BearerAuthMiddleware, TokenStore
from .code import CodeAccess, parse_roots
from .store import IDENTITY_KINDS, IdentityStore, SharedCore, UserMemories

SERVER_NAME = "pulsenet-unified"
SERVER_VERSION = "1.0.0"

# Явно «не извлекать факты»: отличается от None («использовать экстрактор по умолчанию»).
_NO_EXTRACT = lambda _text: {"entities": [], "facts": []}


@dataclass
class Settings:
    data_dir: str = "data_unified"
    owner_token: str = ""
    owner_id: str = "owner"
    admins: set = field(default_factory=set)
    code_users: set = field(default_factory=set)
    code_roots: Dict[str, str] = field(default_factory=dict)
    allowed_hosts: List[str] = field(default_factory=lambda: ["127.0.0.1:*", "localhost:*"])
    allowed_origins: List[str] = field(default_factory=lambda: ["http://127.0.0.1:*", "http://localhost:*"])
    identity_min_days: int = 2
    core_min_interval_hours: float = 24.0
    memory_scope: str = "shared"            # shared — одна память для ваших агентов; per_user — изоляция
    env: Dict[str, str] = field(default_factory=dict)   # переменные для эмбеддера и экстрактора

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        csv = lambda k: {x.strip() for x in env.get(k, "").split(",") if x.strip()}  # noqa: E731
        hosts = [h.strip() for h in env.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
        origins = [o.strip() for o in env.get("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
        allowed_hosts = hosts or ["127.0.0.1:*", "localhost:*", "127.0.0.1", "localhost"]
        allowed_origins = origins or [
            "http://127.0.0.1:*", "http://localhost:*",
            "http://127.0.0.1", "http://localhost",
        ]
        return cls(
            data_dir=env.get("PULSENET_DATA_DIR", "data_unified"),
            owner_token=env.get("PULSENET_MCP_TOKEN", ""),
            owner_id=env.get("MCP_OWNER_ID", "owner"),
            admins=csv("MCP_ADMIN_USERS"),
            code_users=csv("CODE_ACCESS_USERS"),
            code_roots=parse_roots(env.get("CODE_ROOTS", "")),
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
            identity_min_days=int(env.get("IDENTITY_MIN_DAYS", "2")),
            core_min_interval_hours=float(env.get("CORE_MIN_INTERVAL_HOURS", "24")),
            memory_scope=(env.get("MCP_MEMORY_SCOPE") or "shared").strip().lower(),
            env=dict(env),
        )


def _uid(ctx) -> tuple:
    """user_id из токена (ставит BearerAuthMiddleware). Возвращает (uid, error)."""
    try:
        req = ctx.request_context.request
        uid = req.state.user_id
    except Exception:  # noqa: BLE001
        uid = None
    if not uid:
        return None, "нет идентификации: нужен Authorization: Bearer <токен>"
    return uid, None


def _err(msg: str, **extra) -> Dict[str, Any]:
    return {"status": "error", "message": msg, **extra}


def build_mcp(cfg: Settings, memories: UserMemories, identity: IdentityStore,
              core: SharedCore, code: CodeAccess, transport_security=None) -> FastMCP:
    mcp = FastMCP(SERVER_NAME, stateless_http=True, json_response=True,
                  transport_security=transport_security)
    activity = ActivityLog(cfg.data_dir)

    def audit(tool: str, summarize=None):
        """Пишет вызов в журнал активности пользователя. Ошибка журнала инструмент не ломает."""
        def record(ctx, kwargs, result):
            try:
                uid = _uid(ctx)[0] if ctx is not None else None
                if not uid:
                    return
                text = summarize(kwargs) if summarize else ""
                ok = not (isinstance(result, dict) and result.get("status") == "error")
                activity.record(uid, tool, text, ok)
            except Exception:  # noqa: BLE001
                pass

        def deco(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def wrapper(*args, **kwargs):
                    result = await fn(*args, **kwargs)
                    record(kwargs.get("ctx"), kwargs, result)
                    return result
            else:
                @functools.wraps(fn)
                def wrapper(*args, **kwargs):
                    result = fn(*args, **kwargs)
                    record(kwargs.get("ctx"), kwargs, result)
                    return result
            return wrapper
        return deco

    def mem_of(ctx):
        uid, err = _uid(ctx)
        if err:
            return None, None, _err(err)
        return uid, memories.for_user(uid), None

    def is_admin(uid: str) -> bool:
        return uid in cfg.admins

    # ---------- память ----------

    @mcp.tool()
    @audit("triplex_ingest", lambda k: k.get("text", ""))
    def triplex_ingest(text: str = Field(..., description="Текст для сохранения (сообщение, решение, заметка)"),
                       session_id: str = Field("", description="Необязательный id сессии"),
                       role: str = Field("user", description="user | assistant | system"),
                       model: str = Field("", description="Модель экстракции; пусто — TRIPLEX_LLM_MODEL. "
                                                          "Веб-чат передаёт модель текущего ответа"),
                       ctx: Context = None) -> Dict[str, Any]:
        """Сохранить документ в память и извлечь из него факты. Документ сохраняется даже при сбое
        извлечения. Возвращает doc_id: его нужно указать как source при triplex_assert.

        model — модель, которой извлекаются факты: чат передаёт свою текущую модель, чтобы факты
        записывались той же моделью, а не фиксированной из .env."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        try:
            extractor = None
            if (model or "").strip():
                from ..memory_service import build_extractor
                extractor = build_extractor(cfg.env, model=model)
                if extractor is None:              # TRIPLEX_EXTRACT=0 — экстракция отключена
                    extractor = _NO_EXTRACT
            return mem.ingest(text, session_id=session_id, role=role, agent=uid,
                              extractor=extractor)
        except ValueError as exc:
            return _err(str(exc))

    @mcp.tool()
    @audit("triplex_search", lambda k: k.get("query", ""))
    def triplex_search(query: str = Field(..., description="Что вспомнить"),
                       k: int = Field(8, ge=1, le=50),
                       as_of: Optional[str] = Field(None, description="ISO 8601, по умолчанию сейчас"),
                       include_archived: bool = Field(False),
                       ctx: Context = None) -> Dict[str, Any]:
        """Гибридный поиск по памяти: факты с источниками и фрагменты документов, готовый context."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        return mem.recall(query, as_of=as_of, k_facts=k, include_archived=include_archived)

    @mcp.tool()
    @audit("triplex_query", lambda k: f"{k.get('s')} {k.get('r')} {k.get('o')}")
    def triplex_query(s: Optional[str] = Field(None), r: Optional[str] = Field(None),
                      o: Optional[str] = Field(None), as_of: Optional[str] = Field(None),
                      ctx: Context = None) -> Dict[str, Any]:
        """Действующие факты по любой комбинации субъекта, отношения и объекта."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        facts = mem.journal.query(s=s, r=r, o=o, as_of=as_of)
        return {"count": len(facts), "facts": [_fact_dict(f) for f in facts]}

    @mcp.tool()
    @audit("triplex_assert", lambda k: f"{k.get('s')} {k.get('r')} {k.get('o')}")
    def triplex_assert(s: str = Field(...), r: str = Field(...), o: str = Field(...),
                       doc_id: str = Field(..., description="doc_id из triplex_ingest"),
                       span: List[int] = Field(..., min_length=2, max_length=2,
                                               description="[start, end] символов в документе"),
                       confidence: float = Field(0.8, ge=0, le=1),
                       functional: bool = Field(False, description="не более одного объекта на (s, r)"),
                       ctx: Context = None) -> Dict[str, Any]:
        """Записать факт с обязательным источником (без источника факт не записывается)."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        try:
            f = mem.journal.assert_fact(s, r, o, source={"doc_id": doc_id, "span": span, "agent": uid},
                                        confidence=confidence, functional=functional)
        except ConflictError as exc:
            return {"status": "conflict", "message": str(exc), "old_fact_id": exc.old_fact_id}
        except (InvariantError, ValueError) as exc:
            return _err(str(exc))
        return {"status": "ok", "fact": _fact_dict(f)}

    @mcp.tool()
    @audit("triplex_supersede", lambda k: f"{k.get('s')} {k.get('r')} {k.get('o')}")
    def triplex_supersede(old_fact_id: str = Field(...), s: str = Field(...), r: str = Field(...),
                          o: str = Field(...), valid_from: str = Field(..., description="ISO 8601"),
                          doc_id: str = Field(...), span: List[int] = Field(..., min_length=2, max_length=2),
                          confidence: float = Field(0.8, ge=0, le=1),
                          functional: bool = Field(False), ctx: Context = None) -> Dict[str, Any]:
        """Вытеснить действующий факт новым: старый закрывается на valid_from нового."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        try:
            return mem.supersede(old_fact_id, s, r, o, valid_from=valid_from,
                                 source={"doc_id": doc_id, "span": span, "agent": uid},
                                 confidence=confidence, functional=functional)
        except (InvariantError, ValueError, KeyError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @audit("triplex_facts", lambda k: "")
    def triplex_facts(limit: int = Field(50, ge=1, le=500), ctx: Context = None) -> Dict[str, Any]:
        """Действующие факты с провенансом и автором (поле agent)."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        facts = mem.list_facts(limit)
        return {"count": len(facts), "facts": facts}

    @mcp.tool()
    @audit("triplex_link", lambda k: f"{k.get('alias')} -> {k.get('entity_id')}")
    def triplex_link(entity_id: str = Field(..., description="id сущности из triplex_graph"),
                     alias: str = Field(..., description="другое написание того же объекта"),
                     ctx: Context = None) -> Dict[str, Any]:
        """Привязать написание к существующей сущности (например «Nel» → «Нел»)."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        try:
            return mem.merge_alias(entity_id, alias)
        except (KeyError, ValueError) as exc:
            return _err(str(exc))

    @mcp.tool()
    @audit("triplex_retract", lambda k: k.get("fact_id", ""))
    def triplex_retract(fact_id: str = Field(...), ctx: Context = None) -> Dict[str, Any]:
        """Отозвать факт. Запись остаётся в журнале для аудита."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        if fact_id not in mem.journal.facts:
            return _err(f"факт {fact_id!r} не найден")
        return {"status": "ok", "fact": _fact_dict(mem.retract(fact_id))}

    @mcp.tool()
    @audit("triplex_confirm", lambda k: f"{k.get('fact_id')} {k.get('yes')}")
    def triplex_confirm(fact_id: str = Field(...), yes: bool = Field(...),
                        ctx: Context = None) -> Dict[str, Any]:
        """Подтвердить факт (+0.15, не выше 0.95) или опровергнуть (×0.5)."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        if fact_id not in mem.journal.facts:
            return _err(f"факт {fact_id!r} не найден")
        return mem.confirm(fact_id, yes)

    @mcp.tool()
    def triplex_define_relation(canonical: str = Field(...),
                                aliases: List[str] = Field(default_factory=list),
                                functional: bool = Field(False),
                                tau_days: Optional[float] = Field(None, gt=0,
                                                                  description="затухание, дней; null — не затухает"),
                                ctx: Context = None) -> Dict[str, Any]:
        """Описать отношение: синонимы, functional, τ затухания."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        try:
            return {"status": "ok", "relation": mem.define_relation(
                canonical, aliases=aliases, functional=functional, tau_days=tau_days)}
        except ValueError as exc:
            return _err(str(exc))

    @mcp.tool()
    def triplex_stats(ctx: Context = None) -> Dict[str, Any]:
        """Счётчики памяти текущего пользователя."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        return {"total": len(mem.journal.facts), "memory": mem.stats()}

    @mcp.tool()
    def triplex_check(ctx: Context = None) -> Dict[str, Any]:
        """Проверка инвариантов журнала. Пустой список violations — журнал корректен."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        v = mem.journal.check_invariants()
        return {"ok": not v, "violations": v}

    # ---------- личность ----------

    @mcp.tool()
    @audit("identity_propose", lambda k: k.get("content", ""))
    def identity_propose(content: str = Field(..., description="Самоформулировка от первого лица, 30–600 символов"),
                         kind: str = Field(..., description=" | ".join(IDENTITY_KINDS)),
                         evidence_quote: str = Field(..., description="дословная цитата из вашего документа памяти"),
                         ctx: Context = None) -> Dict[str, Any]:
        """Предложить звено личной идентичности. Попадает в цепочку, только после подтверждения
        в разные дни (identity_commit). Факты о мире сюда не пишутся — это память."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        return identity.propose(uid, content, kind, evidence_quote, mem)

    @mcp.tool()
    @audit("identity_commit", lambda k: k.get("candidate_id", ""))
    def identity_commit(candidate_id: str = Field(...), ctx: Context = None) -> Dict[str, Any]:
        """Записать подтверждённого кандидата в личную цепочку (ворота проверяются повторно)."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        return identity.commit(uid, candidate_id, mem)

    @mcp.tool()
    def identity_chain(ctx: Context = None) -> Dict[str, Any]:
        """Личная цепочка идентичности и ожидающие кандидаты. Видна только вам."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        return {"user": uid, **identity.chain(uid)}

    @mcp.tool()
    def core_get(ctx: Context = None) -> Dict[str, Any]:
        """Общее ядро — одинаковое для всех, только чтение."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        return core.get()

    @mcp.tool()
    def core_propose(content: str = Field(..., description="Формулировка общего ядра, 30–400 символов"),
                     ctx: Context = None) -> Dict[str, Any]:
        """(администратор) Предложить изменение общего ядра. Вступит в силу после core_commit."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        if not is_admin(uid):
            return _err("только администратор меняет общее ядро")
        return core.propose(content, by=uid)

    @mcp.tool()
    def core_commit(candidate_id: str = Field(...), ctx: Context = None) -> Dict[str, Any]:
        """(администратор) Применить предложение общего ядра. Не чаще CORE_MIN_INTERVAL_HOURS."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        if not is_admin(uid):
            return _err("только администратор меняет общее ядро")
        return core.commit(candidate_id, by=uid)

    # ---------- интернет ----------

    @mcp.tool()
    @audit("web_search", lambda k: "; ".join((k.get("queries") or [k.get("query") or ""])))
    async def web_search(query: Optional[str] = Field(None, description="Один запрос"),
                         queries: Optional[List[str]] = Field(None, description="До 4 запросов параллельно"),
                         max_results: int = Field(5, ge=1, le=10),
                         ctx: Context = None) -> Dict[str, Any]:
        """Поиск в интернете. Возвращает источники и выдержки для ответа."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        qs = queries or ([query] if query else [])
        return await web.search(qs, max_results=max_results)

    @mcp.tool()
    @audit("web_fetch", lambda k: k.get("url", ""))
    async def web_fetch(url: str = Field(...), ctx: Context = None) -> Dict[str, Any]:
        """Прочитать публичную страницу (только http/https, без приватных адресов)."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        return await web.fetch(url)

    # ---------- код ----------

    def _code_gate(ctx):
        uid, err = _uid(ctx)
        if err:
            return None, _err(err)
        if uid not in cfg.code_users:
            return None, _err("доступ к коду не разрешён для этого пользователя")
        return uid, None

    @mcp.tool()
    def code_list(root: str = Field(...), subdir: str = Field(""), ctx: Context = None) -> Dict[str, Any]:
        """Список текстовых файлов в корне кода (CODE_ROOTS)."""
        uid, e = _code_gate(ctx)
        return e or code.list(uid, root, subdir)

    @mcp.tool()
    def code_read(root: str = Field(...), path: str = Field(...), start: int = Field(1, ge=1),
                  end: Optional[int] = Field(None, ge=1), ctx: Context = None) -> Dict[str, Any]:
        """Прочитать фрагмент файла с номерами строк (до 300 строк за вызов)."""
        uid, e = _code_gate(ctx)
        return e or code.read(uid, root, path, start, end)

    @mcp.tool()
    def code_search(root: str = Field(...), pattern: str = Field(...),
                    ctx: Context = None) -> Dict[str, Any]:
        """Поиск подстроки по коду без учёта регистра."""
        uid, e = _code_gate(ctx)
        return e or code.search(uid, root, pattern)

    @mcp.tool()
    @audit("triplex_graph")
    def triplex_graph(max_edges: int = Field(200, ge=1, le=1000,
                                             description="сколько самых свежих связей вернуть"),
                      ctx: Context = None) -> Dict[str, Any]:
        """Карта «нейронов» памяти: сущности (узлы), действующие факты (связи), рост по дням.
        Нужна, чтобы понять, что уже накоплено, и для визуализации."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        return mem.graph(max_edges=max_edges)

    @mcp.tool()
    def activity_recent(limit: int = Field(30, ge=1, le=200), ctx: Context = None) -> Dict[str, Any]:
        """Последние действия сервера для вас: поиски, сохранения, ошибки (новые сверху).
        Агенту — чтобы не повторять уже сделанную работу и видеть, что не удалось."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        return {"user": uid, "items": activity.recent(uid, limit)}

    @mcp.tool()
    def pulsenet_guide(ctx: Context = None) -> Dict[str, Any]:
        """Описание сервера для агентов: назначение, инструменты, когда их звать, текущее состояние."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        return {
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "user": uid,
            "purpose": ("Личная память и интернет-поиск. Память хранит факты с источниками и фрагменты "
                        "документов; ответы без источника не записываются."),
            "workflow": [
                "Перед нетривиальной задачей: triplex_search с формулировкой задачи.",
                "После решения: triplex_ingest (получить doc_id), затем triplex_assert с источником.",
                "Свежие факты, документация, сравнения: web_search, затем web_fetch для полного текста.",
                "Что уже делалось и что сорвалось: activity_recent.",
                "Что накоплено: triplex_graph (узлы и связи) или triplex_stats.",
            ],
            "tools": {
                "triplex_ingest": "сохранить документ, извлечь факты (doc_id нужен для triplex_assert)",
                "triplex_search": "гибридный поиск: факты с источниками и фрагменты",
                "triplex_query": "действующие факты по субъекту, отношению или объекту",
                "triplex_assert / triplex_supersede / triplex_retract / triplex_confirm": "записать, "
                    "вытеснить, отозвать, подтвердить или опровергнуть факт",
                "triplex_graph": "карта нейронов памяти",
                "triplex_check": "проверка инвариантов журнала",
                "web_search / web_fetch": "поиск в интернете и чтение публичной страницы",
                "activity_recent": "журнал действий пользователя",
                "triplex_facts": "действующие факты с автором (agent)",
                "triplex_link": "привязать написание к сущности",
                "identity_*, core_*": "личная идентичность и общее ядро (с карантином)",
            },
            "memory_scope": cfg.memory_scope,
            "memory_note": ("общая память: все ваши агенты пишут в одно хранилище; автор каждого факта — "
                            "поле agent" if cfg.memory_scope == "shared" else "память у каждого пользователя своя"),
            "state": mem.stats(),
            "recent_activity": activity.recent(uid, 5),
        }

    @mcp.tool()
    @audit("whoami")
    def whoami(ctx: Context = None) -> Dict[str, Any]:
        """Кто вы для сервера и какие возможности доступны."""
        uid, err = _uid(ctx)
        if err:
            return _err(err)
        return {"server": SERVER_NAME, "version": SERVER_VERSION, "user": uid,
                "admin": is_admin(uid), "code_access": uid in cfg.code_users,
                "web_backends": web.backends_order(), "searxng_configured": bool(os.getenv("SEARXNG_URL"))}

    return mcp


def create_app(cfg: Settings) -> Starlette:
    os.makedirs(cfg.data_dir, exist_ok=True)
    tokens = TokenStore(os.path.join(cfg.data_dir, "tokens.sqlite"), cfg.owner_token, cfg.owner_id)
    from ..memory_service import build_encoder, build_extractor

    def _log(where, exc):
        logging.getLogger("pulsenet.unified").warning("%s: %s", where, exc)

    encoder_factory = lambda: build_encoder(cfg.env, cfg.data_dir, _log)[0]     # noqa: E731
    extractor_factory = lambda: build_extractor(cfg.env)                       # noqa: E731
    memories = UserMemories(cfg.data_dir, encoder_factory=encoder_factory,
                            extractor_factory=extractor_factory, scope=cfg.memory_scope)
    identity = IdentityStore(cfg.data_dir, cfg.identity_min_days)
    core = SharedCore(os.path.join(cfg.data_dir, "core.json"), cfg.core_min_interval_hours)
    code = CodeAccess(cfg.code_roots, cfg.code_users)
    from mcp.server.transport_security import TransportSecuritySettings as TSS
    sec = TSS(enable_dns_rebinding_protection=True, allowed_hosts=cfg.allowed_hosts,
              allowed_origins=cfg.allowed_origins)
    mcp = build_mcp(cfg, memories, identity, core, code, transport_security=sec)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, tokens=tokens)
    app.state.tokens = tokens
    return app
