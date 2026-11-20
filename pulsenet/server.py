# -*- coding: utf-8 -*-
"""HTTP-сервер (stdlib). Маршруты:

  GET  /                       веб-интерфейс (web/index.html)
  GET  /ai, /ai.html           тот же интерфейс (старый адрес)
  GET  /static/...             статика интерфейса
  GET  /sw.js                  снятие регистрации устаревшего service worker
  GET  /health                 проверка работоспособности
  GET  /api/status             бэкенд, память, журнал ошибок
  GET  /api/models             модели бэкенда
  POST /api/chat               чат, поток SSE: token | reasoning | tool | sources | done | error
                               тело: {message, chat_id, web, model} — web разрешает веб-инструменты,
                               model выбирает модель для ответа (пусто — по умолчанию)
  GET  /api/chat/stream        продолжить поток ответа после перезагрузки
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
"""
from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WEB_DIR_DEFAULT = "web"
MAX_BODY = 20 * 1024 * 1024

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")


class ChatJob:
    """Ответ ассистента, который продолжается даже без подключённого клиента."""

    def __init__(self, chat_id: str, message: str, web: bool = False, model: str = ""):
        self.chat_id = chat_id
        self.message = message
        self.web = web
        self.model = model
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


class ChatJobs:
    """Один фоновый ответ на чат; SSE-клиенты могут подключаться и отключаться."""

    def __init__(self, app):
        self.app = app
        self._lock = threading.Lock()
        self._jobs = {}

    def start(self, chat_id: str, message: str, web: bool = False, model: str = ""):
        with self._lock:
            current = self._jobs.get(chat_id)
            if current is not None and current.snapshot()["status"] == "running":
                return current, False
            job = ChatJob(chat_id, message, web=web, model=model)
            self._jobs[chat_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job, True

    def _run(self, job):
        try:
            answer = self.app.chat(job.message, job.chat_id, job.add_delta,
                                   cancelled=job.cancelled.is_set, web=job.web, model=job.model)
            job.complete(answer, self.app.index_pending(),
                         "stopped" if job.cancelled.is_set() else "done")
        except Exception as exc:                  # noqa: BLE001
            self.app.log_error("чат", exc)
            job.fail(exc, self.app.index_pending())

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
    server_version = "pulsenet-triplex/2.0"

    def log_message(self, fmt, *args):
        pass

    # ----- помощники -----

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
        self.end_headers()
        self.wfile.write(data)

    def sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
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
        if method == "GET" and path in ("/", "/ai", "/ai.html"):
            return self.send_file(os.path.join(self.web_dir, "index.html"))
        if method == "GET" and path == "/sw.js":
            return self.send_file(os.path.join(self.web_dir, "sw.js"))
        if method == "GET" and path.startswith("/static/"):
            return self.send_static(path[len("/static/"):])
        if method == "GET" and path == "/health":
            return self.send_json({"ok": True})
        handler = ROUTES.get((method, path))
        if handler is None:
            return self.send_json({"error": "not_found"}, 404)
        try:
            body = self.read_json() if method == "POST" else {}
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
    chat_id = str(b.get("chat_id") or "default")
    job, created = h.jobs.start(chat_id, message, web=bool(b.get("web")),
                                model=str(b.get("model") or "").strip())
    if not created and job.message != message:
        return h.send_json({"error": "в этом чате ещё генерируется ответ"}, 409)
    h.stream_job(job)


def _chat_stream(h, _b, q):
    chat_id = str((q.get("chat_id") or ["default"])[0])
    job = h.jobs.get(chat_id)
    if job is None:
        return h.send_json({"error": "нет активного ответа"}, 404)
    h.stream_job(job)


def _stop(h, b, _q):
    chat_id = str(b.get("chat_id") or "").strip()
    if chat_id:
        if h.jobs.stop(chat_id):
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
    h.send_json(h.app.ingest(text, session_id=str(b.get("session_id") or "web"),
                             role=str(b.get("role") or "user"), recorded_at=b.get("recorded_at")))


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


ROUTES = {
    ("GET", "/api/status"): _status,
    ("GET", "/api/models"): _models,
    ("GET", "/api/tools"): _tools,
    ("GET", "/api/memory/facts"): _facts,
    ("GET", "/api/memory/graph"): _graph,
    ("POST", "/api/chat"): _chat,
    ("GET", "/api/chat/stream"): _chat_stream,
    ("POST", "/api/chat/stop"): _stop,
    ("POST", "/api/memory/ingest"): _ingest,
    ("POST", "/api/memory/search"): _search,
    ("POST", "/api/memory/confirm"): _confirm,
    ("POST", "/api/memory/retract"): _retract,
    ("POST", "/api/memory/link"): _link,
    ("POST", "/api/memory/supersede"): _supersede,
    ("POST", "/api/memory/consolidate"): _consolidate,
}


def make_server(app, host="127.0.0.1", port=8000, web_dir=WEB_DIR_DEFAULT):
    class _H(Handler):
        pass
    _H.app = app
    _H.jobs = ChatJobs(app)
    _H.web_dir = web_dir
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
        srv.server_close()
