# -*- coding: utf-8 -*-
"""Пользователи, scrypt-пароли и одноразовые email-коды (SQLite, стандартная библиотека).

Перенесено из MCPIISys: код хранится только как HMAC (защита от повторного использования —
"no replay"), действует 10 минут и сгорает после 5 неверных попыток. В базе нет ни пароля,
ни кода в открытом виде.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Optional

CODE_TTL = 600
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_SEC = 60
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_SEC = 900
MIN_PASSWORD = 8
MAX_PASSWORD = 128
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 1,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    purpose TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_email ON codes(email, purpose);
"""


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400, retry_after: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def normalize_email(raw: str) -> str:
    email = (raw or "").strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise AuthError("Некорректный email", 400)
    return email


def check_password_policy(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD:
        raise AuthError(f"Пароль должен быть не короче {MIN_PASSWORD} символов", 400)
    if len(password) > MAX_PASSWORD:
        raise AuthError("Пароль слишком длинный", 400)


class AuthStore:
    def __init__(self, db_path: str, secret_key: str):
        self.db_path = db_path
        self.secret_key = secret_key.encode("utf-8")
        self._lock = threading.RLock()

    def _run(self, sql, args=(), *, one=False, commit=False):
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.execute(sql, args)
                result = cur.fetchone() if one else cur.fetchall()
                if commit:
                    conn.commit()
                return result
            finally:
                conn.close()

    def init_db(self) -> None:
        parent = os.path.dirname(os.path.abspath(self.db_path))
        os.makedirs(parent, exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
            try:
                conn.executescript(SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ----- пользователи -----

    def get_user(self, email: str):
        return self._run("SELECT * FROM users WHERE email = ?", (email,), one=True)

    def get_user_by_id(self, uid: int):
        return self._run("SELECT * FROM users WHERE id = ?", (int(uid),), one=True)

    def create_pending_user(self, email: str, password: str) -> None:
        self._run("INSERT INTO users (email, password_hash, verified, created_at) VALUES (?, ?, 0, ?)",
                  (email, hash_password(password), time.time()), commit=True)

    def set_pending_password(self, uid: int, password: str) -> None:
        self._run("UPDATE users SET password_hash = ? WHERE id = ? AND verified = 0",
                  (hash_password(password), int(uid)), commit=True)

    def mark_verified(self, uid: int) -> None:
        self._run("UPDATE users SET verified = 1 WHERE id = ?", (int(uid),), commit=True)

    def set_password(self, uid: int, password: str) -> None:
        # session_version + 1: все старые сессии этого пользователя становятся недействительны.
        self._run("UPDATE users SET password_hash = ?, session_version = session_version + 1, "
                  "failed_logins = 0, locked_until = 0 WHERE id = ?",
                  (hash_password(password), int(uid)), commit=True)

    def register_login_failure(self, uid: int) -> None:
        user = self.get_user_by_id(uid)
        fails = (user["failed_logins"] or 0) + 1
        if fails >= LOGIN_MAX_FAILS:
            self._run("UPDATE users SET failed_logins = 0, locked_until = ? WHERE id = ?",
                      (time.time() + LOGIN_LOCK_SEC, int(uid)), commit=True)
        else:
            self._run("UPDATE users SET failed_logins = ? WHERE id = ?", (fails, int(uid)), commit=True)

    def reset_login_failures(self, uid: int) -> None:
        self._run("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?", (int(uid),), commit=True)

    @staticmethod
    def locked_seconds(user) -> int:
        left = (user["locked_until"] or 0) - time.time()
        return int(left) + 1 if left > 0 else 0

    @staticmethod
    def dummy_verify(password: str) -> None:
        verify_password(password or "", _DUMMY_HASH)

    # ----- одноразовые коды -----

    def _code_hash(self, email: str, purpose: str, code: str) -> str:
        msg = f"{purpose}|{email}|{code}".encode("utf-8")
        return hmac.new(self.secret_key, msg, hashlib.sha256).hexdigest()

    def new_code(self, email: str, purpose: str) -> str:
        last = self._run("SELECT created_at FROM codes WHERE email = ? AND purpose = ? "
                         "ORDER BY id DESC LIMIT 1", (email, purpose), one=True)
        if last is not None:
            wait = int(CODE_RESEND_SEC - (time.time() - last["created_at"]))
            if wait > 0:
                raise AuthError(f"Код уже отправлен. Повторно можно запросить через {wait} с",
                                429, retry_after=wait)
        self.drop_codes(email, purpose)
        code = f"{secrets.randbelow(10 ** 6):06d}"
        self._run("INSERT INTO codes (email, purpose, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                  (email, purpose, self._code_hash(email, purpose, code),
                   time.time() + CODE_TTL, time.time()), commit=True)
        return code

    def drop_codes(self, email: str, purpose: str) -> None:
        self._run("DELETE FROM codes WHERE email = ? AND purpose = ?", (email, purpose), commit=True)

    def check_code(self, email: str, purpose: str, code: str) -> bool:
        row = self._run("SELECT * FROM codes WHERE email = ? AND purpose = ? ORDER BY id DESC LIMIT 1",
                        (email, purpose), one=True)
        if row is None:
            return False
        if time.time() > row["expires_at"] or row["attempts"] >= CODE_MAX_ATTEMPTS:
            self.drop_codes(email, purpose)
            return False
        self._run("UPDATE codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],), commit=True)
        if hmac.compare_digest(row["code_hash"], self._code_hash(email, purpose, (code or "").strip())):
            self.drop_codes(email, purpose)   # одноразовый: повтор не пройдёт
            return True
        return False
