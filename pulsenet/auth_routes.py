# -*- coding: utf-8 -*-
"""Роуты /auth/*: регистрация, подтверждение email, вход, выход, сброс пароля.

Логика и коды ответов повторяют MCPIISys (routes/auth.py), но адаптированы к
stdlib-серверу phase2: ошибки приходят полем error, сессия — подписанной cookie.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from .auth_config import AuthConfig
from .auth_session import get_cookie, read_session
from .auth_store import AuthError, AuthStore, check_password_policy, normalize_email
from .mailer import MailError, Mailer

logger = logging.getLogger(__name__)
SENT_MESSAGE = "Если адрес можно использовать, мы отправили на него код."


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clients: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, client_id: str, limit: int = None) -> Tuple[bool, int]:
        max_req = limit or self.max_requests
        now = time.time()
        with self._lock:
            hits = [t for t in self._clients[client_id] if now - t < self.window_seconds]
            if len(hits) >= max_req:
                self._clients[client_id] = hits
                return False, 0
            hits.append(now)
            self._clients[client_id] = hits
            return True, max_req - len(hits)


class AuthAPI:
    def __init__(self, cfg: AuthConfig, store: AuthStore, mailer: Mailer):
        self.cfg = cfg
        self.store = store
        self.mailer = mailer
        self._code_limiter = RateLimiter(max_requests=10, window_seconds=600)
        self._login_limiter = RateLimiter(max_requests=30, window_seconds=600)

    # ----- сессия -----

    def current_user(self, h):
        """Вернуть строку users или None. AUTH_DISABLED=1 → None (особый режим в server.py)."""
        if not self.cfg.enabled:
            return None
        cookie = get_cookie(h.headers.get("Cookie", ""))
        data = read_session(cookie, self.cfg.secret_key)
        if data is None:
            return None
        user = self.store.get_user_by_id(data["uid"])
        if user is None or not user["verified"] or user["session_version"] != data["v"]:
            return None
        return user

    def status(self, user) -> dict:
        if not self.cfg.enabled:
            return {"required": False, "authenticated": True, "email": None, "uid": self.cfg.local_user_id}
        return {
            "required": True,
            "authenticated": user is not None,
            "email": user["email"] if user is not None else None,
            "uid": f"u{user['id']}" if user is not None else None,
        }

    # ----- помощники -----

    @staticmethod
    def _error(h, status: int, message: str, retry_after: int = 0):
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return h.send_json({"error": message}, status, headers=headers)

    def _fail(self, h, exc: AuthError):
        return self._error(h, exc.status, exc.message, exc.retry_after)

    def _limit(self, h, limiter: RateLimiter) -> bool:
        allowed, _ = limiter.is_allowed(h.client_ip())
        if not allowed:
            self._error(h, 429, "Слишком много запросов. Попробуйте позже.", retry_after=600)
            return False
        return True

    def _send(self, h, email: str, code: str, purpose: str) -> bool:
        try:
            self.mailer.send_code(email, code, purpose)
            return True
        except MailError as exc:
            self.store.drop_codes(email, purpose)
            self._error(h, 503, f"{exc}. Попробуйте позже.")
            return False

    @staticmethod
    def _same_password(h, b, password) -> bool:
        """Второе поле подтверждения пароля обязано совпадать с первым."""
        confirm = b.get("password_confirm")
        if not confirm:
            h.send_json({"error": "Подтвердите пароль"}, 400)
            return False
        if confirm != password:
            h.send_json({"error": "Пароли не совпадают"}, 400)
            return False
        return True

    # ----- диспетчер -----

    def dispatch(self, h, path: str, body: dict) -> bool:
        method = getattr(h, "command", "GET")
        routes = {
            ("GET", "/auth/status"): self._status,
            ("POST", "/auth/register"): self._register,
            ("POST", "/auth/register/confirm"): self._register_confirm,
            ("POST", "/auth/code/resend"): self._resend,
            ("POST", "/auth/login"): self._login,
            ("POST", "/auth/logout"): self._logout,
            ("POST", "/auth/reset/request"): self._reset_request,
            ("POST", "/auth/reset/confirm"): self._reset_confirm,
        }
        handler = routes.get((method, path))
        if handler is None:
            self._error(h, 404, "not_found")
            return True
        handler(h, body or {})
        return True

    def _status(self, h, _b):
        h.send_json(self.status(getattr(h, "user", None)))

    # ----- регистрация -----

    def _register(self, h, b):
        if not self._limit(h, self._code_limiter):
            return
        try:
            email = normalize_email(b.get("email") or "")
            password = b.get("password") or ""
            check_password_policy(password)
            if not self._same_password(h, b, password):
                return
            user = self.store.get_user(email)
            if user is not None and user["verified"]:
                return h.send_json({"ok": True, "message": SENT_MESSAGE})
            if user is not None:
                self.store.set_pending_password(user["id"], password)
            else:
                self.store.create_pending_user(email, password)
            code = self.store.new_code(email, "verify")
        except AuthError as exc:
            return self._fail(h, exc)
        if self._send(h, email, code, "verify"):
            h.send_json({"ok": True, "message": SENT_MESSAGE})

    def _register_confirm(self, h, b):
        try:
            email = normalize_email(b.get("email") or "")
        except AuthError as exc:
            return self._fail(h, exc)
        user = self.store.get_user(email)
        if user is None or not self.store.check_code(email, "verify", b.get("code") or ""):
            return self._error(h, 400, "Неверный или просроченный код")
        self.store.mark_verified(user["id"])
        user = self.store.get_user_by_id(user["id"])
        h.issue_session(user["id"], user["session_version"])
        h.send_json({"ok": True})

    def _resend(self, h, b):
        if not self._limit(h, self._code_limiter):
            return
        purpose = b.get("purpose")
        if purpose not in ("verify", "reset"):
            return self._error(h, 400, "Неизвестный тип кода")
        try:
            email = normalize_email(b.get("email") or "")
        except AuthError:
            return h.send_json({"ok": True, "message": SENT_MESSAGE})
        user = self.store.get_user(email)
        relevant = user is not None and (
            (purpose == "verify" and not user["verified"]) or
            (purpose == "reset" and user["verified"])
        )
        if relevant:
            try:
                code = self.store.new_code(email, purpose)
            except AuthError as exc:
                return self._fail(h, exc)
            if not self._send(h, email, code, purpose):
                return
        h.send_json({"ok": True, "message": SENT_MESSAGE})

    # ----- вход / выход -----

    def _login(self, h, b):
        if not self._limit(h, self._login_limiter):
            return
        try:
            email = normalize_email(b.get("email") or "")
        except AuthError:
            self.store.dummy_verify(b.get("password") or "")
            return self._error(h, 401, "Неверный email или пароль")
        user = self.store.get_user(email)
        if user is None:
            self.store.dummy_verify(b.get("password") or "")
            return self._error(h, 401, "Неверный email или пароль")
        left = self.store.locked_seconds(user)
        if left:
            return self._error(h, 429, f"Слишком много неудачных попыток. Подождите {left // 60 + 1} мин.",
                               retry_after=left)
        from .auth_store import verify_password
        if not verify_password(b.get("password") or "", user["password_hash"]):
            self.store.register_login_failure(user["id"])
            return self._error(h, 401, "Неверный email или пароль")
        if not user["verified"]:
            return self._error(h, 403, "Email не подтверждён. Введите код из письма.")
        self.store.reset_login_failures(user["id"])
        h.issue_session(user["id"], user["session_version"])
        h.send_json({"ok": True})

    def _logout(self, h, _b):
        h.clear_session()
        h.send_json({"ok": True})

    # ----- сброс пароля -----

    def _reset_request(self, h, b):
        if not self._limit(h, self._code_limiter):
            return
        try:
            email = normalize_email(b.get("email") or "")
        except AuthError:
            return h.send_json({"ok": True, "message": SENT_MESSAGE})
        user = self.store.get_user(email)
        if user is not None and user["verified"]:
            try:
                code = self.store.new_code(email, "reset")
            except AuthError as exc:
                return self._fail(h, exc)
            try:
                self.mailer.send_code(email, code, "reset")
            except MailError:
                self.store.drop_codes(email, "reset")
                logger.error("Не удалось отправить код восстановления")
        h.send_json({"ok": True, "message": SENT_MESSAGE})

    def _reset_confirm(self, h, b):
        if not self._limit(h, self._login_limiter):
            return
        try:
            email = normalize_email(b.get("email") or "")
            password = b.get("password") or ""
            check_password_policy(password)
        except AuthError as exc:
            return self._fail(h, exc)
        if not self._same_password(h, b, password):
            return
        user = self.store.get_user(email)
        if user is None or not user["verified"] or not self.store.check_code(
                email, "reset", b.get("code") or ""):
            return self._error(h, 400, "Неверный или просроченный код")
        # session_version увеличивается: все старые сессии становятся недействительны.
        self.store.set_password(user["id"], password)
        h.send_json({"ok": True})
