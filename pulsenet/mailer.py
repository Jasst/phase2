# -*- coding: utf-8 -*-
"""Отправка email-кодов. Реквизиты берутся из AuthConfig (env/.env).

MAIL_PROVIDER: smtp (465/587), resend, brevo, unisender_go — HTTPS-провайдеры,
если SMTP-порты заблокированы. В AUTH_DEV_MODE код пишется в лог вместо письма.
"""
from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

from .auth_config import AuthConfig

logger = logging.getLogger(__name__)

HTTP_TIMEOUT = 15
USER_AGENT = "pulsenet-auth/1.0"   # без него Resend отклоняет запросы из Python
SUBJECTS = {"verify": "Подтверждение регистрации", "reset": "Восстановление пароля"}


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, cfg: AuthConfig):
        self.cfg = cfg

    def send_code(self, to: str, code: str, purpose: str) -> None:
        cfg = self.cfg
        subject = f"{cfg.app_name}: {SUBJECTS.get(purpose, 'Код подтверждения')}"
        text = (
            f"Ваш код: {code}\n\n"
            "Код действует 10 минут. Никому его не сообщайте.\n"
            "Если вы не запрашивали код, просто проигнорируйте письмо."
        )
        sender = cfg.smtp_from or cfg.smtp_user

        if cfg.mail_provider == "resend":
            self._resend(sender, to, subject, text)
        elif cfg.mail_provider == "brevo":
            self._brevo(sender, to, subject, text)
        elif cfg.mail_provider == "unisender_go":
            self._unisender_go(sender, to, subject, text)
        elif cfg.smtp_host:
            self._smtp(sender, to, subject, text)
        elif cfg.dev_mode:
            logger.warning("[DEV] код (%s) для %s: %s", purpose, to, code)
        else:
            raise MailError("Почта не настроена на сервере")

    @staticmethod
    def _post_json(url: str, headers: dict, payload: dict, provider: str) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()[:300]
            logger.error("%s: ошибка %s: %r", provider, exc.code, body)
            raise MailError("Сервис почты отклонил письмо. Проверьте ключ и отправителя") from exc
        except (urllib.error.URLError, OSError) as exc:
            logger.error("%s: нет соединения: %s", provider, exc)
            raise MailError("Не удалось отправить письмо") from exc

    def _resend(self, sender: str, to: str, subject: str, text: str) -> None:
        if not self.cfg.resend_api_key:
            raise MailError("Не задан RESEND_API_KEY")
        self._post_json("https://api.resend.com/emails",
                        {"Authorization": f"Bearer {self.cfg.resend_api_key}"},
                        {"from": sender, "to": [to], "subject": subject, "text": text}, "Resend")

    def _brevo(self, sender: str, to: str, subject: str, text: str) -> None:
        if not self.cfg.brevo_api_key:
            raise MailError("Не задан BREVO_API_KEY")
        self._post_json("https://api.brevo.com/v3/smtp/email",
                        {"api-key": self.cfg.brevo_api_key},
                        {"sender": {"email": sender}, "to": [{"email": to}],
                         "subject": subject, "textContent": text}, "Brevo")

    def _unisender_go(self, sender: str, to: str, subject: str, text: str) -> None:
        if not self.cfg.unisender_go_api_key:
            raise MailError("Не задан UNISENDER_GO_API_KEY")
        self._post_json(f"{self.cfg.unisender_go_api_url}/email/send.json",
                        {"X-API-KEY": self.cfg.unisender_go_api_key},
                        {"message": {"recipients": [{"email": to}],
                                     "body": {"plaintext": text},
                                     "subject": subject, "from_email": sender}}, "Unisender Go")

    def _smtp(self, sender: str, to: str, subject: str, text: str) -> None:
        cfg = self.cfg
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        msg.set_content(text)
        try:
            if cfg.smtp_port == 465:
                server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=HTTP_TIMEOUT,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=HTTP_TIMEOUT)
            with server:
                if cfg.smtp_port != 465 and cfg.smtp_starttls:
                    server.starttls(context=ssl.create_default_context())
                if cfg.smtp_user:
                    server.login(cfg.smtp_user, cfg.smtp_password)
                server.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("Ошибка отправки письма: %s", exc)
            raise MailError("Не удалось отправить письмо") from exc
