# -*- coding: utf-8 -*-
"""Настройки веб-авторизации. Все секреты и почтовые реквизиты берутся из env/.env.

Имена переменных совпадают с соседним проектом MCPIISys, чтобы конфигурацию можно
было перенести без переименования. AUTH_DISABLED=1 отключает вход (локальный режим).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool = True
    local_user_id: str = "local_user"
    db_path: str = "data/auth.db"
    secret_key: str = ""
    session_lifetime: int = 60 * 60 * 24 * 14
    app_name: str = "ТРИПЛЕКСИЯ"
    dev_mode: bool = False
    secure_cookies: bool = False
    # Почта
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    mail_provider: str = "smtp"
    resend_api_key: str = ""
    brevo_api_key: str = ""
    unisender_go_api_key: str = ""
    unisender_go_api_url: str = "https://go1.unisender.ru/ru/transactional/api/v1"

    @classmethod
    def from_env(cls, env=None, data_dir: str = "data") -> "AuthConfig":
        env = dict(os.environ if env is None else env)
        enabled = (env.get("AUTH_DISABLED", "0").strip() != "1")
        secret = (env.get("SECRET_KEY") or "").strip()
        if not secret:
            # Как в MCPIISys: без постоянного ключа сессии сбрасываются при рестарте.
            secret = secrets.token_hex(32)
        if enabled and len(secret) < 32:
            raise ValueError("SECRET_KEY должен быть не короче 32 символов")

        secure = any(env.get(k, "") == "1" for k in
                     ("SECURE_COOKIES", "PULSENET_SECURE_COOKIES")) or env.get("FLASK_ENV") == "production"
        return cls(
            enabled=enabled,
            local_user_id=(env.get("LOCAL_USER_ID") or "local_user").strip(),
            db_path=(env.get("AUTH_DB_PATH") or os.path.join(data_dir, "auth.db")).strip(),
            secret_key=secret,
            session_lifetime=int(env.get("SESSION_LIFETIME", 60 * 60 * 24 * 14)),
            app_name=(env.get("APP_NAME") or "ТРИПЛЕКСИЯ").strip(),
            dev_mode=(env.get("AUTH_DEV_MODE", "0").strip() == "1"),
            secure_cookies=secure,
            smtp_host=(env.get("SMTP_HOST") or "").strip(),
            smtp_port=int(env.get("SMTP_PORT", 587)),
            smtp_user=(env.get("SMTP_USER") or "").strip(),
            smtp_password=env.get("SMTP_PASSWORD") or "",
            # Отправитель/«no-reply» адрес — только из env (.env), не из кода.
            smtp_from=(env.get("SMTP_FROM") or env.get("EMAIL_FROM")
                       or env.get("NO_REPLY") or env.get("NO_REPLY_EMAIL") or "").strip(),
            smtp_starttls=(env.get("SMTP_STARTTLS", "1").strip() == "1"),
            mail_provider=(env.get("MAIL_PROVIDER") or "smtp").strip().lower(),
            resend_api_key=(env.get("RESEND_API_KEY") or "").strip(),
            brevo_api_key=(env.get("BREVO_API_KEY") or "").strip(),
            unisender_go_api_key=(env.get("UNISENDER_GO_API_KEY") or "").strip(),
            unisender_go_api_url=(env.get("UNISENDER_GO_API_URL")
                                  or "https://go1.unisender.ru/ru/transactional/api/v1").strip().rstrip("/"),
        )
