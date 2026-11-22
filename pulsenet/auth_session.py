# -*- coding: utf-8 -*-
"""Подписанная cookie-сессия и заголовки Set-Cookie (аналог Starlette SessionMiddleware).

Полayload: {uid, v, exp}, подпись HMAC-SHA256 на SECRET_KEY из env. Поле v —
session_version пользователя: после смены пароля все старые сессии перестают работать.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from .auth_config import AuthConfig

COOKIE = "session"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(uid: int, version: int, secret_key: str, lifetime: int) -> str:
    payload = json.dumps({"uid": int(uid), "v": int(version),
                          "exp": int(time.time()) + int(lifetime)},
                         separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64e(payload) + "." + _b64e(sig)


def read_session(cookie: str, secret_key: str) -> Optional[dict]:
    try:
        payload_part, sig_part = cookie.split(".", 1)
        payload, sig = _b64d(payload_part), _b64d(sig_part)
        expected = hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(payload.decode("utf-8"))
        if int(data.get("exp", 0)) < time.time():
            return None
        if not isinstance(data.get("uid"), int) or not isinstance(data.get("v"), int):
            return None
        return data
    except (ValueError, TypeError, KeyError, UnicodeDecodeError):
        return None


def get_cookie(header: str, name: str = COOKIE) -> str:
    for part in (header or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value.strip()
    return ""


def session_cookie_header(cookie: str, cfg: AuthConfig) -> str:
    parts = [f"{COOKIE}={cookie}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={int(cfg.session_lifetime)}"]
    if cfg.secure_cookies:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header(cfg: AuthConfig) -> str:
    parts = [f"{COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if cfg.secure_cookies:
        parts.append("Secure")
    return "; ".join(parts)
