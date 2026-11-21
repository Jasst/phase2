# -*- coding: utf-8 -*-
"""HTTP-сервер (stdlib). Маршруты:

  GET  /                       веб-интерфейс (web/index.html)
  GET  /ai, /ai.html           тот же интерфейс (старый адрес)
  GET  /static/...             статика интерфейса
  GET  /sw.js                  снятие регистрации устаревшего service worker
  GET  /health                 проверка работоспособности (без входа)
  GET  /login, /register,
       /forgot                  страница входа по email и паролю (без входа)
  GET  /auth/status            состояние сессии (без входа)
  POST /auth/register          регистрация: email + пароль → письмо с кодом
  POST /auth/register/confirm  подтверждение кода → вход
  POST /auth/code/resend       повторная отправка кода (verify/reset)
  POST /auth/login             вход по email и паролю
  POST /auth/logout            выход
  POST /auth/reset/request,
       /auth/reset/confirm     восстановление пароля по коду из письма
  GET  /api/status             бэкенд, память, журнал ошибок (требует входа)
  GET  /api/models             модели бэкенда
  POST /api/chat               чат, поток SSE: token | reasoning | tool | sources | done | error
                               тело: {message, chat_id, web, model} — web разрешает веб-инструменты,
                               model выбирает модель для ответа (пусто — по умолчанию)
  GET  /api/chat/stream        продолжить поток после перезагрузки / с другого устройства
  GET  /api/chat/active        проверка: идёт ли в чате генерация
  GET  /api/chats              серверные чаты пользователя (rev, сессии, сообщения)
  PUT  /api/chats              слить состояние чатов из браузера
  DELETE /api/chats            удалить чат (?chat_id=…) или все чаты пользователя
  GET  /api/chats/events       SSE state-changed: чаты изменились на другом устройстве
  GET  /api/mcp/token          текущий MCP-токен пользователя
  POST /api/mcp/token          выпустить новый токен: старые подключения отключаются
  DELETE /api/mcp/token        отозвать все токены пользователя
  POST /api/chat/stop          остановить текущую генерацию
  GET  /api/tools              инструменты чата и команды (каталог)
  GET  /api/memory/facts       действующие факты
  GET  /api/memory/graph       карта нейронов: узлы, связи, рост по дням
  POST /api/memory/ingest      сохранить текст (синхронно, с экстракцией)
  POST /api/memory/search      поиск (recall)
  POST /api/memory/confirm     подтвердить / опровергнуть факт
  POST /api/memory/retract     отозвать факт
  POST /api/memory/link        привязать написание к сущности
  POST /api/memory/supersede   явно вытеснить факт
  POST /api/memory/consolidate «Сон», сухой прогон: датасет без обучения
  POST /api/memory/distill     темпоральная дистилляция факта через LLM (пересборка по свежим данным)
  POST /api/chat/save-memory   загрузить чат документом в память с извлечением фактов (идемпотентно)
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .auth_config import AuthConfig
from .auth_routes import AuthAPI
from .auth_session import clear_cookie_header, make_session, session_cookie_header
from .auth_store import AuthStore
from .chat_store import ChatStore
from .mailer import Mailer
from .unified_mcp.auth import TokenStore

WEB_DIR_DEFAULT = "web"
MAX_BODY = 20 * 1024 * 1024

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")


class ChatJob:
    """Ответ ассистента, который продолжается даже без подключённого клиента."""

    def __init__(self, chat_id: str, message: str, web: bool = False, model: str = "",
                 uid: str = "", raw_chat_id: str = "", user_msg_id: str = "",
                 assistant_msg_id: str = ""):
        self.chat_id = chat_id
        self.message = message
        self.web = web
        self.model = model
        # Для синхронизации чатов между устройствами: сервер сам дописывает
        # вопрос и готовый ответ, даже если браузер, который их отправил, закрылся.
        self.uid = uid
        self.raw_chat_id = raw_chat_id
        self.user_msg_id = user_msg_id
        self.assistant_msg_id = assistant_msg_id
        self.text = ""
        self.reasoning = ""
        self.answer = ""
        self.error = ""
        self.status = "running"          # running | done | stopped | error
        self.index_pending = 0
        self.events = []                 # tool / sources, с позицией в тексте (at)
        self.cancelled = threading.Event()
        self._lock = threading.Lock()

    def add_delta(self, kind, payload):
        with self._lock:
            if kind == "reasoning":
                self.reasoning += payload
            elif kind in ("tool", "sources"):
                self.events.append({"kind": kind, "data": payload, "at": len(self.text)})
            else:
                self.text += payload

    def complete(self, answer, index_pending, status):
        with self._lock:
            self.answer = answer
            self.index_pending = index_pending
            self.status = status

    def fail(self, error, index_pending):
        with self._lock:
            self.error = str(error)
            self.index_pending = index_pending
            self.status = "error"

    def snapshot(self):
        with self._lock:
            return {
                "text": self.text,
                "reasoning": self.reasoning,
                "events": list(self.events),
                "answer": self.answer,
                "error": self.error,
                "status": self.status,
                "index_pending": self.index_pending,
            }


class ChatNotifier:
    """Оповещение браузеров об изменении серверного состояния чатов (SSE)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs = {}

    def subscribe(self, uid: str) -> queue.Queue:
        q = queue.Queue()
        with self._lock:
            self._subs.setdefault(uid, []).append(q)
        return q

    def unsubscribe(self, uid: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(uid, [])
            if q in subs:
                subs.remove(q)

    def notify(self, uid: str) -> None:
        with self._lock:
            for q in list(self._subs.get(uid, [])):
                q.put(time.time())


class ChatJobs:
    """Один фоновый ответ на чат; SSE-клиенты могут подключаться и отключаться."""

    def __init__(self, app, store: ChatStore = None, notifier: ChatNotifier = None):
        self.app = app
        self.store = store
        self.notifier = notifier
        self._lock = threading.Lock()
        self._jobs = {}

    def start(self, chat_id: str, message: str, web: bool = False, model: str = "",
              uid: str = "", raw_chat_id: str = "", user_msg_id: str = "",
              assistant_msg_id: str = ""):
        with self._lock:
            current = self._jobs.get(chat_id)
            if current is not None and current.snapshot()["status"] == "running":
                return current, False
            job = ChatJob(chat_id, message, web=web, model=model, uid=uid,
                          raw_chat_id=raw_chat_id, user_msg_id=user_msg_id,
                          assistant_msg_id=assistant_msg_id)
            self._jobs[chat_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job, True

    def _persist(self, job: ChatJob, messages: list, title: str = "") -> None:
        if self.store is None or not job.uid or not job.raw_chat_id:
            return
        try:
            self.store.append(job.uid, job.raw_chat_id, messages, title=title)
            if self.notifier is not None:
                self.notifier.notify(job.uid)
        except Exception as exc:                  # noqa: BLE001 — чат не должен падать из-за синхронизации
            self.app.log_error("синхронизация чата", exc)

    def _run(self, job):
        if job.user_msg_id:
            self._persist(job, [{"id": job.user_msg_id, "role": "user", "text": job.message,
                                 "time": time.time()}], title=job.message[:48])
        try:
            answer = self.app.chat(job.message, job.chat_id, job.add_delta,
                                   cancelled=job.cancelled.is_set, web=job.web, model=job.model)
            job.complete(answer, self.app.index_pending(),
                         "stopped" if job.cancelled.is_set() else "done")
        except Exception as exc:                  # noqa: BLE001
            self.app.log_error("чат", exc)
            job.fail(exc, self.app.index_pending())
        if job.assistant_msg_id:
            snap = job.snapshot()
            tools = [ev["data"] for ev in snap["events"] if ev["kind"] == "tool"]
            sources = []
            for ev in snap["events"]:
                if ev["kind"] == "sources" and isinstance(ev["data"], list):
                    sources.extend(ev["data"])
            msg = {"id": job.assistant_msg_id, "role": "assistant",
                   "text": snap["answer"] or snap["text"], "reasoning": snap["reasoning"],
                   "time": time.time(), "tools": tools, "sources": sources,
                   "indexPending": snap["index_pending"]}
            if snap["error"]:
                msg["error"] = snap["error"]
            if snap["status"] == "stopped":
                msg["note"] = "Остановлено"
            self._persist(job, [msg])

    def get(self, chat_id: str):
        with self._lock:
            return self._jobs.get(chat_id)

    def stop(self, chat_id: str) -> bool:
        job = self.get(chat_id)
        if job is not None and job.snapshot()["status"] == "running":
            job.cancelled.set()
            return True
        return False


class Handler(BaseHTTPRequestHandler):
    app = None
    jobs = None
    web_dir = WEB_DIR_DEFAULT
    auth_api = None
    auth_cfg = None
    token_store = None          # TokenStore для UI-ротации токена пользователя
    chat_store = None           # серверные чаты (источник истины для всех устройств)
    chat_notifier = None
    user = None                # строка users или None (проверено в dispatch)
    uid = None                 # 'u<N>' для изоляции чатов; None в локальном режиме
    server_version = "pulsenet-triplex/2.2"

    def log_message(self, fmt, *args):
        pass

    # ----- помощники -----

    def send_json(self, obj, status=200, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in {**getattr(self, "_extra_headers", {}), **(headers or {})}.items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for key, value in getattr(self, "_extra_headers", {}).items():
            self.send_header(key, str(value))
        self.end_headers()

    def storage_uid(self) -> str:
        """Ключ пользователя для серверных чатов и MCP-токена.

        В локальном режиме (AUTH_DISABLED=1) сессии нет, поэтому используем
        стабильный LOCAL_USER_ID — чтобы чаты не «терялись» между перезапусками.
        """
        if getattr(self, "uid", None):
            return self.uid
        cfg = getattr(self, "auth_cfg", None)
        return (cfg.local_user_id if cfg else "local_user") or "local_user"

    def notify_chats(self, uid: str = None) -> None:
        if self.chat_notifier is not None:
            self.chat_notifier.notify(uid or self.storage_uid())

    def client_ip(self) -> str:
        real = self.headers.get("X-Real-IP", "").strip()
        if real:
            return real
        forwarded = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        return forwarded or (self.client_address[0] if self.client_address else "unknown")

    def issue_session(self, uid, version):
        cfg = self.auth_cfg
        cookie = make_session(int(uid), int(version), cfg.secret_key, cfg.session_lifetime)
        self._extra_headers["Set-Cookie"] = session_cookie_header(cookie, cfg)

    def clear_session(self):
        self._extra_headers["Set-Cookie"] = clear_cookie_header(self.auth_cfg)

    def read_raw(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > MAX_BODY:
            raise ValueError("тело запроса слишком большое")
        return self.rfile.read(n)

    def read_json(self) -> dict:
        raw = self.read_raw()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ValueError("тело запроса — некорректный JSON")
        if not isinstance(data, dict):
            raise ValueError("тело запроса — объект JSON")
        return data

    def send_file(self, path):
        if not os.path.isfile(path):
            return self.send_json({"error": "not_found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        for key, value in getattr(self, "_extra_headers", {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(data)

    def sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def sse_event(self, name: str, obj) -> None:
        self.wfile.write(("event: " + name + "\ndata: "
                          + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def send_static(self, rel):
        root = os.path.abspath(os.path.join(self.web_dir, "static"))
        parts = [p for p in rel.replace("\\", "/").split("/") if p]
        path = os.path.abspath(os.path.join(root, *parts))
        try:
            inside = os.path.commonpath([root, path]) == root
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(path):
            return self.send_json({"error": "not_found"}, 404)
        return self.send_file(path)

    def stream_job(self, job):
        """Отдаёт накопленный ответ и следит за ним, пока он не завершится.

        Токены и события (tool, sources) идут в порядке появления: событие показывается там,
        где в тексте оно произошло (поле at)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        text_pos = 0
        reasoning_pos = 0
        event_pos = 0
        try:
            while True:
                snap = job.snapshot()
                text = snap["text"]
                reasoning = snap["reasoning"]
                for ev in snap["events"][event_pos:]:
                    if ev["at"] > text_pos:
                        self.sse({"token": text[text_pos:ev["at"]]})
                        text_pos = ev["at"]
                    self.sse({ev["kind"]: ev["data"]})
                    event_pos += 1
                if len(text) > text_pos:
                    self.sse({"token": text[text_pos:]})
                    text_pos = len(text)
                if len(reasoning) > reasoning_pos:
                    self.sse({"reasoning": reasoning[reasoning_pos:]})
                    reasoning_pos = len(reasoning)
                if snap["status"] != "running":
                    if snap["error"]:
                        self.sse({"error": snap["error"]})
                    self.sse({"done": {
                        "answer": snap["answer"] or text.strip(),
                        "index_pending": snap["index_pending"],
                        "status": snap["status"],
                    }})
                    break
                time.sleep(0.05)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except ConnectionError:
            # Клиент ушёл (перезагрузка/закрытие вкладки). Ответ продолжается в фоне.
            return

    # ----- маршрутизация -----

    def dispatch(self, method):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        self._extra_headers = {}
        self.user = None
        self.uid = None
        cfg = self.auth_cfg
        auth_on = bool(cfg and cfg.enabled and self.auth_api is not None)
        if auth_on:
            self.user = self.auth_api.current_user(self)
            if self.user is not None:
                self.uid = f"u{self.user['id']}"   # изоляция чатов/сессий памяти по пользователю

        try:
            # Публичное: проверка работоспособности и статика (нужна странице входа).
            if method == "GET" and path == "/health":
                return self.send_json({"ok": True})
            if method == "GET" and path == "/sw.js":
                return self.send_file(os.path.join(self.web_dir, "sw.js"))
            if method == "GET" and path.startswith("/static/"):
                return self.send_static(path[len("/static/"):])

            # Публичный API авторизации.
            if path.startswith("/auth/"):
                body = self.read_json() if method == "POST" else {}
                return self.auth_api.dispatch(self, path, body)

            # Страница входа.
            if method == "GET" and path in ("/login", "/register", "/forgot"):
                if auth_on and self.user is not None:
                    return self.send_redirect("/")
                return self.send_file(os.path.join(self.web_dir, "auth.html"))

            # Всё остальное требует активной сессии.
            if auth_on and self.user is None:
                if method == "GET" and path in ("/", "/ai", "/ai.html"):
                    return self.send_redirect("/login")
                if path.startswith("/api/") or method != "GET":
                    return self.send_json({"error": "unauthorized"}, 401)
                return self.send_redirect("/login")

            if method == "GET" and path in ("/", "/ai", "/ai.html"):
                return self.send_file(os.path.join(self.web_dir, "index.html"))
            handler = ROUTES.get((method, path))
            if handler is None:
                return self.send_json({"error": "not_found"}, 404)
            body = self.read_json() if method in ("POST", "PUT", "DELETE") else {}
            handler(self, body, query)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
        except ConnectionError:
            return
        except KeyError as exc:
            self.send_json({"error": f"не найдено: {exc}"}, 404)
        except Exception as exc:                  # noqa: BLE001
            self.app.log_error(f"{method} {path}", exc)
            try:
                self.send_json({"error": f"внутренняя ошибка: {exc}"}, 500)
            except ConnectionError:
                pass

    def do_GET(self):
        self._safe("GET")

    def do_POST(self):
        self._safe("POST")

    def do_DELETE(self):
        self._safe("DELETE")

    def do_PUT(self):
        self._safe("PUT")

    def _safe(self, method):
        try:
            self.dispatch(method)
        except ConnectionError:
            pass

# ----- обработчики API -----

def _status(h, _b, _q):
    h.send_json(h.app.status())


def _health(h, _b, _q):
    h.send_json({"ok": True, "backend": h.app.backend.name})


def _scope_id(h, value: str) -> str:
    """Ключ чата/сессии памяти с префиксом пользователя (при включённом входе)."""
    uid = getattr(h, "uid", None)
    return f"{uid}:{value}" if uid else value


def _models(h, _b, _q):
    try:
        models = h.app.backend.list_models()
    except Exception as exc:                  # noqa: BLE001
        h.app.log_error("список моделей", exc)
        models = []
    h.send_json({"models": models, "default": h.app.default_rel()})


def _chat(h, b, _q):
    message = (b.get("message") or "").strip()
    if not message:
        raise ValueError("пустое сообщение")
    raw_chat_id = str(b.get("chat_id") or "default")
    chat_id = _scope_id(h, raw_chat_id)
    uid = h.storage_uid() if hasattr(h, "storage_uid") else ""
    job, created = h.jobs.start(
        chat_id, message, web=bool(b.get("web")), model=str(b.get("model") or "").strip(),
        uid=uid, raw_chat_id=raw_chat_id,
        user_msg_id=str(b.get("user_msg_id") or ""),
        assistant_msg_id=str(b.get("assistant_msg_id") or ""))
    if not created and job.message != message:
        return h.send_json({"error": "в этом чате ещё генерируется ответ"}, 409)
    h.stream_job(job)


def _chat_stream(h, _b, q):
    chat_id = _scope_id(h, str((q.get("chat_id") or ["default"])[0]))
    job = h.jobs.get(chat_id)
    if job is None:
        return h.send_json({"error": "нет активного ответа"}, 404)
    h.stream_job(job)


def _chat_active(h, _b, q):
    """Есть ли в чате активная генерация — другое устройство может к ней подключиться.

    Вместе с флагом отдаём id исходных сообщений: подключающийся клиент должен
    дописывать ответ в уже существующий объект, а не создавать новую копию с новым id.
    """
    raw = str((q.get("chat_id") or ["default"])[0])
    job = h.jobs.get(_scope_id(h, raw))
    snap = job.snapshot() if job is not None else None
    active = snap is not None and snap["status"] == "running"
    info = {"active": active, "chat_id": raw}
    if active:
        info["user_msg_id"] = job.user_msg_id
        info["assistant_msg_id"] = job.assistant_msg_id
    h.send_json(info)


def _chats_get(h, _b, q):
    if h.chat_store is None:
        return h.send_json({"error": "серверное хранилище чатов недоступно"}, 503)
    rev = int((q.get("rev") or ["0"])[0] or 0)
    snap = h.chat_store.snapshot(h.storage_uid())
    if rev and snap["rev"] == rev:
        return h.send_json({"unchanged": True, "rev": snap["rev"]})
    h.send_json(snap)


def _chats_put(h, b, _q):
    if h.chat_store is None:
        return h.send_json({"error": "серверное хранилище чатов недоступно"}, 503)
    rev = h.chat_store.merge(h.storage_uid(), b.get("sessions"), b.get("messages"), b.get("deleted"))
    h.notify_chats()
    h.send_json({"rev": rev})


def _chats_delete(h, b, q):
    if h.chat_store is None:
        return h.send_json({"error": "серверное хранилище чатов недоступно"}, 503)
    uid = h.storage_uid()
    chat_id = str((q.get("chat_id") or [str(b.get("chat_id") or "")])[0])
    rev = h.chat_store.delete(uid, chat_id) if chat_id else h.chat_store.clear(uid)
    h.notify_chats(uid)
    h.send_json({"rev": rev})


def _chats_events(h, _b, _q):
    """SSE: уведомление других устройств об изменении серверных чатов."""
    uid = h.storage_uid()
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("X-Accel-Buffering", "no")
    h.end_headers()
    h.wfile.write(b"retry: 5000\n\n")
    h.wfile.flush()
    q = h.chat_notifier.subscribe(uid) if h.chat_notifier is not None else None
    try:
        while True:
            if q is None:
                h.wfile.write(b": hb\n\n")
                h.wfile.flush()
                time.sleep(20)
                continue
            try:
                q.get(timeout=20)
                rev = h.chat_store.revision(uid) if h.chat_store else 1
                h.sse_event("state-changed", {"rev": rev})
            except queue.Empty:
                h.wfile.write(b": hb\n\n")
                h.wfile.flush()
    except (ConnectionError, BrokenPipeError, OSError):
        return
    finally:
        if h.chat_notifier is not None and q is not None:
            h.chat_notifier.unsubscribe(uid, q)


def _mcp_token_status(h, _b, _q):
    store = h.token_store
    if store is None:
        return h.send_json({"error": "MCP-сервер недоступен"}, 503)
    uid = h.storage_uid()
    info = store.active(uid) or {"user_id": uid, "fingerprint": "", "created": None,
                                 "token": "", "owner": False, "displayable": False}
    info["active"] = bool(info.get("fingerprint"))
    info["can_rotate"] = not info.get("owner", False)
    if info.get("owner"):
        info["hint"] = "Токен владельца задаётся в .env (PULSENET_MCP_TOKEN) и меняется перезапуском."
    else:
        info["hint"] = ("Токен внешнего ИИ под вашим аккаунтом. Сервисный токен чата для памяти "
                        "задаётся в .env (PULSENET_CHAT_MCP_TOKEN) и здесь не меняется.")
    h.send_json(info)


def _mcp_token_rotate(h, _b, _q):
    store = h.token_store
    if store is None:
        return h.send_json({"error": "MCP-сервер недоступен"}, 503)
    uid = h.storage_uid()
    try:
        token = store.rotate(uid)
    except ValueError as exc:
        return h.send_json({"error": str(exc)}, 400)
    info = store.active(uid) or {}
    h.send_json({"ok": True, "token": token, "user_id": uid,
                 "fingerprint": info.get("fingerprint", ""),
                 "hint": "Старый токен отозван: подключённые внешние ИИ нужно обновить."})


def _mcp_token_revoke(h, _b, _q):
    store = h.token_store
    if store is None:
        return h.send_json({"error": "MCP-сервер недоступен"}, 503)
    uid = h.storage_uid()
    try:
        revoked = store.revoke_user(uid)
    except ValueError as exc:
        return h.send_json({"error": str(exc)}, 400)
    h.send_json({"ok": True, "revoked": revoked})


def _stop(h, b, _q):
    chat_id = str(b.get("chat_id") or "").strip()
    if chat_id:
        if h.jobs.stop(_scope_id(h, chat_id)):
            return h.send_json({"status": "stopping"})
        return h.send_json({"status": "idle"})
    h.app.stop()
    h.send_json({"status": "stopping"})


def _facts(h, _b, q):
    limit = int((q.get("limit") or ["50"])[0])
    h.send_json({"facts": h.app.memory.list_facts(max(1, min(limit, 500)))})


def _ingest(h, b, _q):
    text = (b.get("text") or "").strip()
    if not text:
        raise ValueError("пустой текст")
    h.send_json(h.app.ingest(text, session_id=_scope_id(h, str(b.get("session_id") or "web")),
                             role=str(b.get("role") or "user"), recorded_at=b.get("recorded_at"),
                             model=str(b.get("model") or "")))


def _graph(h, _b, q):
    limit = int((q.get("limit") or ["200"])[0])
    h.send_json(h.app.graph(max_edges=max(10, min(limit, 1000))))


def _tools(h, _b, _q):
    h.send_json(h.app.describe_tools())


def _search(h, b, _q):
    query = (b.get("query") or "").strip()
    if not query:
        raise ValueError("пустой запрос")
    h.send_json(h.app.memory.recall(query, as_of=b.get("as_of"), k_facts=int(b.get("k", 8))))


def _confirm(h, b, _q):
    h.send_json(h.app.memory.confirm(str(b.get("fact_id", "")), bool(b.get("yes"))))


def _retract(h, b, _q):
    fact_id = str(b.get("fact_id", ""))
    h.app.memory.retract(fact_id)                 # локально возвращает Fact, по MCP — dict: нужен только факт
    h.send_json({"status": "ok", "retracted": fact_id})


def _link(h, b, _q):
    h.send_json(h.app.memory.merge_alias(str(b.get("entity_id", "")), str(b.get("alias", ""))))


def _supersede(h, b, _q):
    h.send_json(h.app.memory.supersede(
        str(b.get("old_fact_id", "")), str(b.get("s", "")), str(b.get("r", "")), str(b.get("o", "")),
        valid_from=str(b.get("valid_from", "")),
        source=b.get("source") or {"doc_id": str(b.get("doc_id", "manual")), "span": [0, 0]},
        confidence=float(b.get("confidence", 0.8)), functional=bool(b.get("functional", False))))


def _consolidate(h, _b, _q):
    h.send_json(h.app.consolidate_dry_run())


def _distill(h, b, _q):
    h.send_json(h.app.distill_fact(str(b.get("fact_id", "")), model=str(b.get("model") or "")))


def _chat_save_memory(h, b, _q):
    """Сохранить весь чат одним документом: из него извлекаются факты с дедупликацией.

    Повторный вызов того же снимка не создаёт копию: recorded_at выводится из
    времени последнего сообщения, а DocStore хеширует [recorded_at, session, role, text].
    """
    raw_chat_id = str(b.get("chat_id") or "").strip()
    uid = h.storage_uid() if hasattr(h, "storage_uid") else ""
    # Браузер присылает то, что видит пользователь; серверный снимок — запасной
    # вариант (например, при вызове команды с другого устройства).
    items = b.get("messages") or []
    if not items and h.chat_store is not None and raw_chat_id:
        items = h.chat_store.chat_messages(uid, raw_chat_id)
    lines, latest = [], 0.0
    for m in items:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        text = str(m.get("text") or "").strip()
        if not text or role == "system":
            continue
        try:
            t = float(m.get("time") or 0)
        except (TypeError, ValueError):
            t = 0.0
        if t > 1e12:                       # браузер присылает миллисекунды
            t /= 1000.0
        latest = max(latest, t)
        who = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"{who}: {text}")
    if not lines:
        raise ValueError("в чате нет сообщений для сохранения")
    text = "\n\n".join(lines)
    recorded = (datetime.fromtimestamp(latest, timezone.utc).isoformat(timespec="microseconds")
                if latest else None)
    out = h.app.ingest(text, session_id=_scope_id(h, raw_chat_id or "chat"),
                       role="chat", recorded_at=recorded,
                       model=str(b.get("model") or ""))
    out["messages"] = len(lines)
    out["chars"] = len(text)
    h.send_json(out)


ROUTES = {
    ("GET", "/api/status"): _status,
    ("GET", "/api/models"): _models,
    ("GET", "/api/tools"): _tools,
    ("GET", "/api/memory/facts"): _facts,
    ("GET", "/api/memory/graph"): _graph,
    ("POST", "/api/chat"): _chat,
    ("GET", "/api/chat/stream"): _chat_stream,
    ("GET", "/api/chat/active"): _chat_active,
    ("POST", "/api/chat/stop"): _stop,
    ("POST", "/api/chat/save-memory"): _chat_save_memory,
    ("GET", "/api/chats"): _chats_get,
    ("PUT", "/api/chats"): _chats_put,
    ("DELETE", "/api/chats"): _chats_delete,
    ("GET", "/api/chats/events"): _chats_events,
    ("GET", "/api/mcp/token"): _mcp_token_status,
    ("POST", "/api/mcp/token"): _mcp_token_rotate,
    ("DELETE", "/api/mcp/token"): _mcp_token_revoke,
    ("POST", "/api/memory/ingest"): _ingest,
    ("POST", "/api/memory/search"): _search,
    ("POST", "/api/memory/confirm"): _confirm,
    ("POST", "/api/memory/retract"): _retract,
    ("POST", "/api/memory/link"): _link,
    ("POST", "/api/memory/supersede"): _supersede,
    ("POST", "/api/memory/consolidate"): _consolidate,
    ("POST", "/api/memory/distill"): _distill,
}


def make_server(app, host="127.0.0.1", port=8000, web_dir=WEB_DIR_DEFAULT, auth=None):
    class _H(Handler):
        pass
    env = getattr(app, "env", None) or os.environ
    data_dir = getattr(app, "data_dir", "data")
    _H.app = app
    _H.web_dir = web_dir

    # Серверные чаты и токены: каталог данных MCP берём из той же переменной, что
    # и unified_mcp — иначе веб будет читать не ту базу токенов.
    chat_store = ChatStore(os.path.join(data_dir, "chats.sqlite"))
    mcp_dir = (env.get("PULSENET_DATA_DIR")
               or os.path.join(os.path.dirname(os.path.abspath(data_dir)), "data_unified"))
    os.makedirs(mcp_dir, exist_ok=True)
    token_store = TokenStore(
        os.path.join(mcp_dir, "tokens.sqlite"),
        env.get("PULSENET_MCP_TOKEN", ""), env.get("MCP_OWNER_ID", "owner"),
        secret=env.get("MCP_TOKEN_SECRET", ""),
        key_path=env.get("MCP_TOKEN_KEY_PATH") or os.path.join(mcp_dir, "mcp_token.key"))
    notifier = ChatNotifier()
    _H.chat_store = chat_store
    _H.token_store = token_store
    _H.chat_notifier = notifier
    _H.jobs = ChatJobs(app, store=chat_store, notifier=notifier)

    if auth is None:
        cfg = AuthConfig.from_env(env, data_dir=data_dir)
        store = AuthStore(cfg.db_path, cfg.secret_key)
        if cfg.enabled:
            store.init_db()
        auth = AuthAPI(cfg, store, Mailer(cfg))
    _H.auth_api = auth
    _H.auth_cfg = auth.cfg
    srv = ThreadingHTTPServer((host, port), _H)
    srv.daemon_threads = True
    return srv


def serve_forever(app, host, port, web_dir=WEB_DIR_DEFAULT):
    srv = make_server(app, host, port, web_dir)
    print(f"pulsenet: http://{host}:{srv.server_address[1]}/  (бэкенд: {app.backend.name}, память: "
          f"{'вкл' if app.memory_enabled else 'выкл'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Ctrl+C должен гасить не только HTTP-сокет: генерация, локальный llama-server
        # и отдельный процесс эмбеддингов иначе остаются живыми после выхода.
        app.stop()
        stop_backend = getattr(app.backend, "stop", None)
        if callable(stop_backend):
            try:
                stop_backend()
            except Exception:                      # noqa: BLE001 — уборка не должна ломать выход
                pass
        if getattr(app, "embed_server", None):
            try:
                app.embed_server.stop()
            except Exception:                      # noqa: BLE001
                pass
        srv.server_close()
