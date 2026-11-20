# -*- coding: utf-8 -*-
"""Единый MCP-сервер (Streamable HTTP, stateless, JSON-ответы).

Инструменты (все, кроме whoami, работают в контексте токена):
  память ТРИПЛЕКСИИ : triplex_ingest, triplex_search, triplex_query, triplex_assert,
                      triplex_supersede, triplex_retract, triplex_confirm,
                      triplex_define_relation, triplex_stats, triplex_check
  личность          : identity_propose, identity_commit, identity_chain
  общее ядро        : core_get; core_propose / core_commit — только администратор
  интернет          : web_search, web_fetch
  код               : code_list, code_read, code_search — только CODE_ACCESS_USERS
  служебное         : whoami
"""
from __future__ import annotations

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
from .auth import BearerAuthMiddleware, TokenStore
from .code import CodeAccess, parse_roots
from .store import IDENTITY_KINDS, IdentityStore, SharedCore, UserMemories

SERVER_NAME = "pulsenet-unified"
SERVER_VERSION = "1.0.0"


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

    def mem_of(ctx):
        uid, err = _uid(ctx)
        if err:
            return None, None, _err(err)
        return uid, memories.for_user(uid), None

    def is_admin(uid: str) -> bool:
        return uid in cfg.admins

    # ---------- память ----------

    @mcp.tool()
    def triplex_ingest(text: str = Field(..., description="Текст для сохранения (сообщение, решение, заметка)"),
                       session_id: str = Field("", description="Необязательный id сессии"),
                       role: str = Field("user", description="user | assistant | system"),
                       ctx: Context = None) -> Dict[str, Any]:
        """Сохранить документ в память и извлечь из него факты. Документ сохраняется даже при сбое
        извлечения. Возвращает doc_id: его нужно указать как source при triplex_assert."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        try:
            return mem.ingest(text, session_id=session_id, role=role)
        except ValueError as exc:
            return _err(str(exc))

    @mcp.tool()
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
            f = mem.journal.assert_fact(s, r, o, source={"doc_id": doc_id, "span": span},
                                        confidence=confidence, functional=functional)
        except ConflictError as exc:
            return {"status": "conflict", "message": str(exc), "old_fact_id": exc.old_fact_id}
        except (InvariantError, ValueError) as exc:
            return _err(str(exc))
        return {"status": "ok", "fact": _fact_dict(f)}

    @mcp.tool()
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
                                 source={"doc_id": doc_id, "span": span},
                                 confidence=confidence, functional=functional)
        except (InvariantError, ValueError, KeyError) as exc:
            return _err(str(exc))

    @mcp.tool()
    def triplex_retract(fact_id: str = Field(...), ctx: Context = None) -> Dict[str, Any]:
        """Отозвать факт. Запись остаётся в журнале для аудита."""
        uid, mem, e = mem_of(ctx)
        if e:
            return e
        if fact_id not in mem.journal.facts:
            return _err(f"факт {fact_id!r} не найден")
        return {"status": "ok", "fact": _fact_dict(mem.retract(fact_id))}

    @mcp.tool()
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
    memories = UserMemories(cfg.data_dir)
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
