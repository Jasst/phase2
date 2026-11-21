# -*- coding: utf-8 -*-
"""Токены доступа: каждый пользователь — свой токен, хранится только хеш (sha256).

Владелец по умолчанию получает токен из PULSENET_MCP_TOKEN (user = MCP_OWNER_ID).
Остальные токены выпускает администратор командой `issue-token`; отзыв — `revoke`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from typing import Optional

_UID_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")


def valid_user_id(uid: str) -> bool:
    return bool(uid) and bool(_UID_RE.fullmatch(uid)) and uid not in (".", "..")


class TokenStore:
    def __init__(self, path: str, owner_token: str = "", owner_id: str = "owner"):
        self._lock = threading.RLock()
        self._owner_token = owner_token.strip()
        self._owner_id = owner_id
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS tokens ("
                         "hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL, revoked REAL)")
        self._db.commit()

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, user_id: str) -> str:
        if not valid_user_id(user_id):
            raise ValueError("user_id: 1–64 символов из A-Z a-z 0-9 _ . @ -")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("INSERT INTO tokens(hash, user_id, created, revoked) VALUES (?,?,?,NULL)",
                             (self._h(token), user_id, time.time()))
            self._db.commit()
        return token  # показывается один раз, в базе только хеш

    def verify(self, token: str) -> Optional[str]:
        if not token:
            return None
        if self._owner_token and hmac.compare_digest(token.encode("utf-8"), self._owner_token.encode("utf-8")):
            return self._owner_id
        with self._lock:
            row = self._db.execute("SELECT user_id FROM tokens WHERE hash=? AND revoked IS NULL",
                                   (self._h(token),)).fetchone()
        return row[0] if row else None

    def list_tokens(self) -> list:
        """Токены без значений: только пользователь, дата выпуска, дата отзыва и отпечаток (первые 8 символов хеша).
        Сам токен восстановить нельзя — он хранится только как хеш. Владелец из .env показывается отдельно."""
        with self._lock:
            rows = self._db.execute("SELECT hash, user_id, created, revoked FROM tokens "
                                    "ORDER BY created DESC").fetchall()
        out = [{"user_id": uid, "fingerprint": h[:8], "created": c, "revoked": r,
                "active": r is None} for h, uid, c, r in rows]
        if self._owner_token:
            out.insert(0, {"user_id": self._owner_id, "fingerprint": self._h(self._owner_token)[:8],
                           "created": None, "revoked": None, "active": True, "source": ".env PULSENET_MCP_TOKEN"})
        return out

    def rotate(self, user_id: str) -> str:
        """Отозвать все активные токены пользователя и выпустить новый. Старый перестаёт работать сразу."""
        if user_id == self._owner_id and self._owner_token:
            raise ValueError("токен владельца задаётся в .env (PULSENET_MCP_TOKEN): поменяйте его там и перезапустите")
        with self._lock:
            self.revoke_user(user_id)
        return self.issue(user_id)

    def revoke_user(self, user_id: str) -> int:
        with self._lock:
            cur = self._db.execute("UPDATE tokens SET revoked=? WHERE user_id=? AND revoked IS NULL",
                                   (time.time(), user_id))
            self._db.commit()
            return cur.rowcount


class BearerAuthMiddleware:
    """ASGI-обёртка над /mcp: без валидного токена — 401, до MCP-кода запрос не доходит.
    Найденный user_id кладётся в scope['state'] и читается инструментами."""

    def __init__(self, app, tokens: TokenStore):
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").rstrip("/").startswith("/mcp"):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            uid = self.tokens.verify(auth[7:] if auth.startswith("Bearer ") else "")
            if not uid:
                body = json.dumps({"error": "unauthorized"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="pulsenet-unified"'),
                    (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
            scope.setdefault("state", {})["user_id"] = uid
        await self.app(scope, receive, send)
