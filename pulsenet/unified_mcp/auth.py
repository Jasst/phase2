# -*- coding: utf-8 -*-
"""Токены доступа: каждый пользователь — свой токен.

В базе лежит HMAC/sha256 для проверки и (если доступна cryptography) зашифрованный
токен для повторного показа в веб-интерфейсе — как в MCPIISys. Секрет шифрования:
MCP_TOKEN_SECRET или файл mcp_token.key рядом с tokens.sqlite.

Владелец по умолчанию получает токен из PULSENET_MCP_TOKEN (user = MCP_OWNER_ID):
он не хранится в базе и не показывается в UI. Остальные токены выпускаются
в Настройках чата или CLI (`issue-token`, `rotate`, `revoke`, `list-tokens`).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Optional

_UID_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")

# Необязательное шифрование токена для показа текущего кода на любом устройстве.
# Как в MCPIISys: в базе лежит HMAC для проверки и шифртекст для отображения.
# Если cryptography недоступна, работаем как раньше — токен показывается один раз.
try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover — зависимость необязательна
    Fernet = None


def valid_user_id(uid: str) -> bool:
    return bool(uid) and bool(_UID_RE.fullmatch(uid)) and uid not in (".", "..")


class TokenStore:
    def __init__(self, path: str, owner_token: str = "", owner_id: str = "owner",
                 secret: str = "", key_path: str = ""):
        self._lock = threading.RLock()
        self._owner_token = owner_token.strip()
        self._owner_id = owner_id
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._db.execute("CREATE TABLE IF NOT EXISTS tokens ("
                         "hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, created REAL, revoked REAL)")
        cols = {row[1] for row in self._db.execute("PRAGMA table_info(tokens)")}
        if "token_enc" not in cols:
            self._db.execute("ALTER TABLE tokens ADD COLUMN token_enc TEXT")
        self._db.commit()

        # Секрет шифрования: env MCP_TOKEN_SECRET либо файл рядом с базой (как mcp_code.key).
        raw_secret = (secret or "").strip()
        self._key_path = key_path or os.path.join(os.path.dirname(os.path.abspath(path)), "mcp_token.key")
        if not raw_secret and os.path.isfile(self._key_path):
            try:
                with open(self._key_path, "r", encoding="utf-8") as f:
                    raw_secret = f.read().strip()
            except OSError:
                raw_secret = ""
        if not raw_secret:
            raw_secret = secrets.token_urlsafe(48)
            try:
                os.makedirs(os.path.dirname(self._key_path), exist_ok=True)
                with open(self._key_path, "w", encoding="utf-8") as f:
                    f.write(raw_secret)
                try:
                    os.chmod(self._key_path, 0o600)
                except OSError:
                    pass
            except OSError:
                pass
        self._fernet = None
        if Fernet is not None and raw_secret:
            key = base64.urlsafe_b64encode(hashlib.sha256(raw_secret.encode("utf-8")).digest())
            self._fernet = Fernet(key)

    @staticmethod
    def _h(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii") if self._fernet else ""

    def _decrypt(self, enc: str) -> str:
        if not enc or not self._fernet:
            return ""
        try:
            return self._fernet.decrypt(enc.encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001 — испорченный шифртекст не должен ломать статус
            return ""

    def issue(self, user_id: str) -> str:
        if not valid_user_id(user_id):
            raise ValueError("user_id: 1–64 символов из A-Z a-z 0-9 _ . @ -")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._db.execute("INSERT INTO tokens(hash, user_id, created, revoked, token_enc) "
                             "VALUES (?,?,?,NULL,?)",
                             (self._h(token), user_id, time.time(), self._encrypt(token)))
            self._db.commit()
        return token  # показывается один раз; при наличии ключа — ещё и в зашифрованном виде

    def active(self, user_id: str) -> Optional[dict]:
        """Текущий валидный токен пользователя (значение — только если включено шифрование)."""
        if user_id == self._owner_id and self._owner_token:
            return {"user_id": user_id, "fingerprint": self._h(self._owner_token)[:8],
                    "created": None, "token": "", "owner": True, "displayable": False}
        with self._lock:
            row = self._db.execute(
                "SELECT hash, created, token_enc FROM tokens WHERE user_id = ? AND revoked IS NULL "
                "ORDER BY created DESC LIMIT 1", (user_id,)).fetchone()
        if row is None:
            return None
        token = self._decrypt(row["token_enc"])
        return {"user_id": user_id, "fingerprint": row["hash"][:8], "created": row["created"],
                "token": token, "owner": False, "displayable": bool(token)}

    def close(self) -> None:
        with self._lock:
            self._db.close()

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
        """Токены без значений: пользователь, дата выпуска/отзыва и отпечаток (8 символов хеша).
        Для показа значения используйте active(user_id) — он расшифровывает токен,
        если задан MCP_TOKEN_SECRET или найден mcp_token.key. Владелец из .env — отдельно."""
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
