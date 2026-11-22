# -*- coding: utf-8 -*-
"""Тесты веб-авторизации: страница входа, email-коды, сессия, выход, сброс пароля."""
import http.client
import json
import os
import threading

import pytest

from pulsenet.app import App
from pulsenet.auth_config import AuthConfig
from pulsenet.auth_routes import AuthAPI
from pulsenet.auth_store import AuthStore
from pulsenet.server import make_server

SECRET = "test-secret-key-for-auth-tests-0123456789abcdef"
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class FakeMailer:
    def __init__(self):
        self.sent = []

    def send_code(self, to, code, purpose):
        self.sent.append((to, code, purpose))


def _req(port, method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    result = (resp.status, dict(resp.getheaders()), raw)
    conn.close()
    return result


def _json(port, method, path, body=None, cookie=None):
    status, headers, raw = _req(port, method, path, body, cookie)
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        data = {}
    return status, headers, data


@pytest.fixture()
def auth_stack(tmp_path):
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "", "TRIPLEX_EXTRACT": "0"}
    app = App(env, data_dir=str(tmp_path / "data"))
    cfg = AuthConfig(enabled=True, db_path=str(tmp_path / "auth.db"), secret_key=SECRET,
                     session_lifetime=3600, app_name="Тест")
    store = AuthStore(cfg.db_path, cfg.secret_key)
    store.init_db()
    mailer = FakeMailer()
    api = AuthAPI(cfg, store, mailer)
    srv = make_server(app, "127.0.0.1", 0, web_dir=WEB_DIR, auth=api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], store, mailer, cfg
    srv.shutdown()
    srv.server_close()


def _cookie(headers):
    raw = headers.get("Set-Cookie") or ""
    return raw.split(";", 1)[0]


def test_login_page_and_guards(auth_stack):
    port, _store, _mailer, _cfg = auth_stack
    status, headers, raw = _req(port, "GET", "/")
    assert status == 302 and headers["Location"] == "/login"

    status, _h, raw = _req(port, "GET", "/login")
    assert status == 200 and b"loginForm" in raw and b"registerForm" in raw

    status, _h, data = _json(port, "GET", "/auth/status")
    assert status == 200 and data == {"required": True, "authenticated": False,
                                      "email": None, "uid": None}

    assert _json(port, "GET", "/api/status")[0] == 401
    assert _json(port, "GET", "/api/memory/facts")[0] == 401
    assert _json(port, "GET", "/health")[0] == 200


def test_register_confirm_session_and_no_replay(auth_stack):
    port, store, mailer, cfg = auth_stack
    email, password = "user@example.com", "strong-password-123"

    assert _json(port, "POST", "/auth/register",
                 {"email": email, "password": password, "password_confirm": "другой-пароль"})[0] == 400
    assert _json(port, "POST", "/auth/register", {"email": email, "password": password})[2]["error"] == \
        "Подтвердите пароль"

    status, _h, data = _json(port, "POST", "/auth/register",
                             {"email": email, "password": password, "password_confirm": password})
    assert status == 200 and data["ok"] is True
    assert len(mailer.sent) == 1 and mailer.sent[0][0] == email and mailer.sent[0][2] == "verify"
    code = mailer.sent[0][1]

    assert _json(port, "POST", "/auth/register/confirm", {"email": email, "code": "000000"})[0] == 400

    status, headers, data = _json(port, "POST", "/auth/register/confirm", {"email": email, "code": code})
    assert status == 200 and data == {"ok": True}
    cookie = _cookie(headers)
    assert cookie.startswith("session=")

    # Одноразовый код: повторное подтверждение не проходит.
    assert _json(port, "POST", "/auth/register/confirm", {"email": email, "code": code})[0] == 400

    status, _h, raw = _req(port, "GET", "/", cookie=cookie)
    assert status == 200 and "ТРИПЛЕКСИЯ".encode("utf-8") in raw
    status, _h, data = _json(port, "GET", "/auth/status", cookie=cookie)
    assert status == 200 and data["authenticated"] is True and data["email"] == email
    assert data["uid"] == f"u{store.get_user(email)['id']}"
    assert _json(port, "GET", "/api/status", cookie=cookie)[0] == 200

    # Чужой/испорченный куки-файл не авторизует.
    assert _json(port, "GET", "/auth/status", cookie="session=garbage")[2]["authenticated"] is False


def test_login_logout_and_password_reset(auth_stack):
    port, store, mailer, cfg = auth_stack
    email, password = "owner@example.com", "initial-password-1"
    store.create_pending_user(email, password)
    store.mark_verified(store.get_user(email)["id"])

    assert _json(port, "POST", "/auth/login", {"email": email, "password": "wrong"})[0] == 401
    status, headers, _data = _json(port, "POST", "/auth/login", {"email": email, "password": password})
    assert status == 200
    cookie = _cookie(headers)
    assert _json(port, "GET", "/api/status", cookie=cookie)[0] == 200

    status, headers, _data = _json(port, "POST", "/auth/logout", cookie=cookie)
    assert status == 200 and "Max-Age=0" in headers.get("Set-Cookie", "")

    # Сброс пароля увеличивает session_version → старая сессия мертва.
    status, _h, _d = _json(port, "POST", "/auth/reset/request", {"email": email})
    assert status == 200
    code = [m for m in mailer.sent if m[2] == "reset"][-1][1]
    new_password = "new-strong-password-2"
    assert _json(port, "POST", "/auth/reset/confirm",
                 {"email": email, "code": code, "password": new_password,
                  "password_confirm": "не-совпадает"})[0] == 400
    assert _json(port, "POST", "/auth/reset/confirm",
                 {"email": email, "code": code, "password": new_password,
                  "password_confirm": new_password})[0] == 200
    assert _json(port, "GET", "/api/status", cookie=cookie)[0] == 401

    status, headers, _d = _json(port, "POST", "/auth/login", {"email": email, "password": new_password})
    assert status == 200
    assert _json(port, "GET", "/api/status", cookie=_cookie(headers))[0] == 200


def test_auth_disabled_mode(tmp_path):
    env = {"LLM_BACKEND": "lmstudio", "LMSTUDIO_URL": "", "TRIPLEX_EXTRACT": "0"}
    app = App(env, data_dir=str(tmp_path / "data"))
    cfg = AuthConfig(enabled=False, secret_key=SECRET, db_path=str(tmp_path / "auth.db"))
    api = AuthAPI(cfg, AuthStore(cfg.db_path, cfg.secret_key), FakeMailer())
    srv = make_server(app, "127.0.0.1", 0, web_dir=WEB_DIR, auth=api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert _req(port, "GET", "/")[0] == 200
        assert _json(port, "GET", "/auth/status")[2] == {
            "required": False, "authenticated": True, "email": None, "uid": "local_user"}
        assert _json(port, "GET", "/api/status")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()
