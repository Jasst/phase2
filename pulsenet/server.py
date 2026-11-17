# -*- coding: utf-8 -*-
"""HTTP-сервер (stdlib): отдаёт веб-интерфейс из web/ и API, которое он вызывает.

Страницы: /  — чат (ai.html), /network — сеть импульсов (network.html).
Чат: POST /ai/chat — поток Server-Sent Events (token, reasoning_token, trace,
gen_phase, error, затем [DONE]).
"""
import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .llm import BackendError

WEB_DIR_DEFAULT = "web"
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")


def _not_supported(h, _body, _q):
    h.send_json({"error": "не поддерживается в pulsenet"}, 501)


class Handler(BaseHTTPRequestHandler):
    app = None
    web_dir = WEB_DIR_DEFAULT
    server_version = "pulsenet/1.0"

    def log_message(self, fmt, *args):
        pass

    # ----- низкоуровневые помощники -----

    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return {}

    def send_file(self, path):
        if not os.path.isfile(path):
            self.send_json({"error": "not_found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def sse(self, obj):
        self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def sse_done(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # ----- маршрутизация -----

    def dispatch(self, method):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if method == "GET" and path == "/":
            return self.send_file(os.path.join(self.web_dir, "ai.html"))
        if method == "GET" and path == "/network":
            return self.send_file(os.path.join(self.web_dir, "network.html"))
        if method == "GET" and path in ("/memory", "/ai/memory/page"):
            self.send_response(302)
            self.send_header("Location", "/network")
            self.end_headers()
            return
        if method == "GET" and path == "/login":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if method == "GET" and path.startswith("/static/"):
            root = os.path.realpath(os.path.join(self.web_dir, "static"))
            target = os.path.realpath(os.path.join(root, path[len("/static/"):]))
            if not target.startswith(root + os.sep):
                return self.send_json({"error": "forbidden"}, 403)
            return self.send_file(target)

        handler = ROUTES.get((method, path))
        if handler is None:
            if path.startswith("/ai/memory"):
                return _not_supported(self, None, None)
            return self.send_json({"error": "not_found"}, 404)
        body = self.read_json() if method in ("POST", "PUT") else None
        try:
            handler(self, body, query)
        except BackendError as exc:
            self.send_json({"error": str(exc)}, 503)
        except BrokenPipeError:
            self.app.stop_flag = True
        except Exception as exc:               # не даём серверу упасть из-за одного запроса
            self.send_json({"error": f"внутренняя ошибка: {exc}"}, 500)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


# ----- обработчики API -----

def _health(h, _b, _q):
    h.send_json({"ok": True, "backend": h.app.backend.name})


def _status(h, _b, _q):
    h.send_json({"ok": True, "readonly": False})


def _auth_status(h, _b, _q):
    h.send_json({"required": False, "authenticated": True, "email": "локальный доступ", "uid": "local"})


def _ok(h, _b, _q):
    h.send_json({"ok": True})


def _models(h, _b, _q):
    h.send_json(h.app.models_payload())


def _models_select(h, b, _q):
    try:
        h.send_json(h.app.select_model(b.get("model"), bool(b.get("check_only"))))
    except BackendError as exc:
        h.send_json({"error": str(exc)}, 400)


def _settings_get(h, _b, _q):
    h.send_json(h.app.settings_payload())


def _settings_post(h, b, _q):
    h.send_json(h.app.save_model_params(b.get("model", ""), b.get("params") or {}))


def _unload(h, _b, _q):
    h.send_json(h.app.unload())


def _gen_status(h, _b, _q):
    h.send_json({"active": h.app.gen_active, "may_watch": False})


def _chat(h, b, _q):
    app = h.app
    message = str(b.get("message", "")).strip()
    if not message:
        return h.send_json({"error": "empty_message"}, 400)
    if not app.begin_generation():
        return h.send_json({"error": "generation_busy"}, 409)
    h.start_sse()
    try:
        app.run_generation(message, b.get("model"), b.get("chat_id"), h.sse)
    except BackendError as exc:
        h.sse({"error": str(exc)})
    except BrokenPipeError:
        app.stop_flag = True
    except Exception as exc:
        h.sse({"error": f"внутренняя ошибка: {exc}"})
    finally:
        app.end_generation()
        try:
            h.sse_done()
        except OSError:
            pass


def _attach(h, _b, _q):
    h.start_sse()
    h.sse({"no_active_generation": True})
    h.sse_done()


def _last(h, _b, q):
    user_text = (q.get("user_text") or [None])[0]
    h.send_json(h.app.last_response(user_text))


def _stop(h, _b, _q):
    h.app.stop_flag = True
    h.send_json({"ok": True})


def _notifications(h, _b, _q):
    h.send_json({"notifications": []})


def _ui_get(h, _b, _q):
    h.send_json(h.app.ui_get())


def _ui_put(h, b, _q):
    h.send_json(h.app.ui_put(b or {}))


def _ui_version(h, _b, _q):
    h.send_json({"updated": h.app.ui_get().get("updated", 0)})


def _ui_delta(h, _b, _q):
    ui = h.app.ui_get()
    h.send_json({"updated": ui.get("updated", 0), "changed": ui})


def _ui_events(h, _b, _q):
    # 204 — EventSource не переподключается; потоковой синхронизации здесь нет
    h.send_response(204)
    h.send_header("Content-Length", "0")
    h.end_headers()


def _mcp_code(h, _b, _q):
    h.send_json({"code": None})


def _net_state(h, _b, _q):
    h.send_json(h.app.net_state())


def _net_graph(h, _b, _q):
    h.send_json(h.app.graph())


def _net_neurons(h, _b, q):
    try:
        k = int((q.get("ensemble") or ["-1"])[0])
    except ValueError:
        return h.send_json({"error": "ensemble должен быть числом"}, 400)
    h.send_json(h.app.neurons(k))


def _net_respond(h, b, _q):
    question = str(b.get("question", "")).strip()
    if not question:
        return h.send_json({"error": "пустой вопрос"}, 400)
    h.send_json(h.app.respond(question))


def _net_teach(h, b, _q):
    result = h.app.teach(str(b.get("question", "")), str(b.get("answer", "")),
                         float(b.get("weight", 1.0)), source="manual")
    h.send_json(result, 200 if result.get("ok") else 400)


def _net_save(h, _b, _q):
    h.app.save_net()
    h.send_json(h.app.net_state())


def _net_load(h, _b, _q):
    result = h.app.load_net()
    h.send_json(result, 200 if result.get("ok") else 400)


def _net_reset(h, _b, _q):
    h.send_json(h.app.reset_net())


def _net_restore(h, _b, _q):
    result = h.app.restore_backup()
    h.send_json(result, 200 if result.get("ok") else 400)


def _net_mode(h, b, _q):
    result = h.app.set_mode(b.get("mode"), b.get("auto_learn"))
    h.send_json(result, 200 if result.get("ok") else 400)


def _net_feedback(h, b, _q):
    result = h.app.feedback(b.get("question"), b.get("answer"), b.get("verdict"), b.get("correct"))
    h.send_json(result, 200 if result.get("ok") else 400)


def _net_tuning_get(h, _b, _q):
    h.send_json(h.app.get_tuning())


def _net_tuning_set(h, b, _q):
    result = h.app.set_tuning(b)
    h.send_json(result, 200 if result["ok"] else 400)


def _net_eval(h, _b, _q):
    h.send_json(h.app.run_eval())


def _net_episodes(h, _b, q):
    try:
        limit = max(1, min(500, int((q.get("limit") or ["50"])[0])))
    except ValueError:
        limit = 50
    h.send_json({"episodes": h.app.episodes[-limit:][::-1], "total": len(h.app.episodes)})


ROUTES = {
    ("GET", "/health"): _health,
    ("GET", "/status"): _status,
    ("GET", "/auth/status"): _auth_status,
    ("POST", "/auth/logout"): _ok,
    ("GET", "/ai/models"): _models,
    ("POST", "/ai/models/select"): _models_select,
    ("GET", "/ai/model-settings"): _settings_get,
    ("POST", "/ai/model-settings"): _settings_post,
    ("POST", "/ai/model-settings/unload"): _unload,
    ("GET", "/ai/gen-status"): _gen_status,
    ("POST", "/ai/gen-lock"): _ok,
    ("DELETE", "/ai/gen-lock"): _ok,
    ("POST", "/ai/chat"): _chat,
    ("GET", "/ai/chat/attach"): _attach,
    ("GET", "/ai/chat/last_response"): _last,
    ("POST", "/ai/chat/stop"): _stop,
    ("POST", "/ai/chat/skip_tools"): _ok,
    ("GET", "/ai/notifications/poll"): _notifications,
    ("GET", "/ai/ui-state"): _ui_get,
    ("PUT", "/ai/ui-state"): _ui_put,
    ("POST", "/ai/ui-state"): _ui_put,
    ("GET", "/ai/ui-state/version"): _ui_version,
    ("GET", "/ai/ui-state/delta"): _ui_delta,
    ("GET", "/ai/ui-state/events"): _ui_events,
    ("GET", "/ai/mcp_code"): _mcp_code,
    ("POST", "/ai/mcp_code"): _mcp_code,
    ("GET", "/net/state"): _net_state,
    ("GET", "/net/graph"): _net_graph,
    ("GET", "/net/neurons"): _net_neurons,
    ("POST", "/net/respond"): _net_respond,
    ("POST", "/net/teach"): _net_teach,
    ("POST", "/net/save"): _net_save,
    ("POST", "/net/load"): _net_load,
    ("POST", "/net/reset"): _net_reset,
    ("POST", "/net/restore"): _net_restore,
    ("POST", "/net/mode"): _net_mode,
    ("POST", "/net/feedback"): _net_feedback,
    ("POST", "/net/eval"): _net_eval,
    ("GET", "/net/tuning"): _net_tuning_get,
    ("POST", "/net/tuning"): _net_tuning_set,
    ("GET", "/net/episodes"): _net_episodes,
}


def make_server(app, host="127.0.0.1", port=8000, web_dir=WEB_DIR_DEFAULT):
    class _H(Handler):
        pass
    _H.app = app
    _H.web_dir = web_dir
    srv = ThreadingHTTPServer((host, port), _H)
    srv.daemon_threads = True
    return srv


def serve_forever(app, host, port, web_dir=WEB_DIR_DEFAULT):
    srv = make_server(app, host, port, web_dir)
    print(f"pulsenet: http://{host}:{srv.server_address[1]}/  (память: /memory, бэкенд: {app.backend.name})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        app.save_all()
        if hasattr(app.backend, "proc") and app.backend.proc is not None and not app.backend.external:
            app.backend.stop()
