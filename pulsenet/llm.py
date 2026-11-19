# -*- coding: utf-8 -*-
"""Бэкенды LLM. Выбор задаётся переменной LLM_BACKEND:

  llamacpp  — llama-server (llama.cpp). Может сам запускать сервер с нужной моделью
              (LLAMACPP_AUTOSTART=1) и перезапускать его при смене модели.
  lmstudio  — уже запущенный сервер LM Studio (LMSTUDIO_URL).

Оба говорят по OpenAI-совместимому API: /v1/models и /v1/chat/completions со стримингом.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

LLAMACPP_PORT_DEFAULT = 8090
LMSTUDIO_URL_DEFAULT = "http://localhost:1234/v1"

PARAM_DEFAULTS = {
    "ctx": 4096,
    "gpu_layers": -1,
    "flash_attn": False,
    "kv_cache_type": "f16",
    "threads": 0,
    "main_gpu": "0",
    "tensor_split": "",
    "parallel": 1,
}


class BackendError(RuntimeError):
    pass


def _flag(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() not in ("0", "false", "no", "off")


def http_json(url, body=None, timeout=10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _error_detail(exc):
    """Текст ошибки HTTP из ответа сервера: LM Studio и llama-server пишут причину в теле."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except (OSError, AttributeError):
        body = ""
    try:
        msg = json.loads(body).get("error")
        if isinstance(msg, dict):
            msg = msg.get("message")
        if msg:
            return str(msg)
    except (ValueError, AttributeError):
        pass
    return body.strip()[:300] or str(exc.reason)


class ThinkSplitter:
    """Отделяет <think>…</think> от ответа в потоке (теги могут приходить по частям)."""

    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, text):
        self.buf += text
        out = []
        while True:
            tag = "</think>" if self.inside else "<think>"
            i = self.buf.find(tag)
            if i >= 0:
                if i > 0:
                    out.append(("reasoning" if self.inside else "token", self.buf[:i]))
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            keep = 0
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-k:]):
                    keep = k
                    break
            emit = self.buf[:len(self.buf) - keep]
            if emit:
                out.append(("reasoning" if self.inside else "token", emit))
            self.buf = self.buf[len(self.buf) - keep:]
            return out

    def flush(self):
        rest, self.buf = self.buf, ""
        return [("reasoning" if self.inside else "token", rest)] if rest else []


class OpenAICompat:
    base_url = ""
    name = ""

    def stream(self, model, messages, on_delta, cancelled, temperature=0.7, max_tokens=1024):
        """on_delta(kind, text): kind — 'token' (ответ) или 'reasoning' (рассуждения)."""
        body = {"model": model, "messages": messages, "stream": True,
                "temperature": temperature, "max_tokens": max_tokens}
        req = urllib.request.Request(self.base_url + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as exc:        # сервер ответил, но с ошибкой
            raise BackendError(f"{self.name} ответил ошибкой {exc.code}: {_error_detail(exc)}") from exc
        except OSError as exc:                       # сервер не отвечает вовсе
            raise BackendError(f"нет связи с LLM ({self.name}): {exc}") from exc
        splitter = ThinkSplitter()
        with resp:
            for raw in resp:
                if cancelled():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                choice = (ev.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    on_delta("reasoning", delta["reasoning_content"])
                if delta.get("content"):
                    for kind, text in splitter.feed(delta["content"]):
                        on_delta(kind, text)
        for kind, text in splitter.flush():
            on_delta(kind, text)


class LMStudioBackend(OpenAICompat):
    name = "lmstudio"
    editable = False

    def __init__(self, env):
        url = env.get("LMSTUDIO_URL", LMSTUDIO_URL_DEFAULT).strip().rstrip("/")
        for tail in ("/chat/completions", "/models"):
            if url.endswith(tail):
                url = url[:-len(tail)]
        self.base_url = url if url.endswith("/v1") else url + "/v1"
        self.default_model = env.get("LMSTUDIO_MODEL", "").strip()
        self.loaded = None

    def _root_url(self):
        return self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url

    def _loaded_ids(self):
        """id моделей, реально находящихся в памяти (REST API LM Studio)."""
        try:
            data = http_json(self._root_url() + "/api/v0/models", timeout=5)
        except (OSError, ValueError):
            return None
        ids = []
        for m in data.get("data", []):
            if not isinstance(m, dict) or not m.get("id"):
                continue
            if m.get("state") not in ("loaded", "loading"):
                continue
            if m.get("type") == "embeddings":
                continue
            ids.append(m["id"])
        return ids

    def refresh_loaded(self):
        """Обновить `loaded` по фактическому состоянию LM Studio.

        Не полагаемся на сохранённый выбор модели: он мог остаться от другого
        бэкенда (например, путь .gguf), и запрос к LM Studio загрузил бы
        совсем не ту модель, что уже находится в памяти.
        """
        ids = self._loaded_ids()
        if ids is None:      # старые версии: state есть только в /v1/models
            try:
                data = http_json(self.base_url + "/models", timeout=5)
            except (OSError, ValueError):
                return self.loaded
            known = [m for m in data.get("data", [])
                     if isinstance(m, dict) and m.get("id") and "state" in m]
            if not known:
                return self.loaded
            ids = [m["id"] for m in known if m.get("state") == "loaded"]
        self.loaded = ids[0] if ids else None
        return self.loaded

    def list_models(self):
        try:
            data = http_json(self.base_url + "/models", timeout=5)
        except (OSError, ValueError) as exc:
            raise BackendError(f"LM Studio недоступен по адресу {self.base_url}: {exc}") from exc
        self.refresh_loaded()
        return [m["id"] for m in data.get("data", []) if "id" in m]

    def _known_ids(self):
        """Все id, которые LM Studio принимает в запросе (включая ещё не загруженные)."""
        try:
            data = http_json(self.base_url + "/models", timeout=5)
        except (OSError, ValueError) as exc:
            raise BackendError(f"LM Studio недоступен по адресу {self.base_url}: {exc}") from exc
        return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]

    def ensure(self, model, params=None):
        target = (model or "").strip()
        if target:
            # Модель из настроек другого бэкенда (например, путь .gguf из llama.cpp)
            # LM Studio не знает: без проверки запрос упал бы с непонятной ошибкой.
            if target not in self._known_ids():
                raise BackendError(f"модель {target!r} не найдена в LM Studio: выберите модель "
                                   f"из списка или загрузите её в LM Studio")
            self.loaded = target
            return target
        loaded = self.refresh_loaded()
        target = loaded or self.default_model
        self.loaded = target or None
        return target

    def stop(self):
        self.loaded = None

    def status(self):
        return {"backend": self.name, "url": self.base_url, "loaded": self.loaded}


class LlamaCppBackend(OpenAICompat):
    name = "llamacpp"
    editable = True

    def __init__(self, env, models_dir_default="models", log_dir="data"):
        self.bin = env.get("LLAMACPP_SERVER_BIN", "llama-server").strip()
        self.models_dir = env.get("MODELS_DIR", models_dir_default).strip()
        self.port = int(env.get("LLAMACPP_PORT", LLAMACPP_PORT_DEFAULT))
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self.default_rel = env.get("LLAMACPP_DEFAULT_MODEL", "").strip()
        self.autostart = _flag(env.get("LLAMACPP_AUTOSTART"), default=True)
        self.cuda_order = env.get("LLAMACPP_CUDA_DEVICE_ORDER", "PCI_BUS_ID").strip()
        self.extra_args = env.get("LLAMACPP_EXTRA_ARGS", "").split()
        self.log_path = os.path.join(log_dir, "llama-server.log")
        self.proc = None
        self.loaded = None          # относительный путь модели, которая сейчас запущена
        self.external = False       # сервер запущен не нами (его не перезапускаем)

    # ----- модели на диске -----

    def model_path(self, rel):
        return os.path.join(self.models_dir, *rel.replace("\\", "/").split("/"))

    def list_models(self):
        found = []
        if not os.path.isdir(self.models_dir):
            return found
        for root, _dirs, files in os.walk(self.models_dir):
            for f in files:
                if f.lower().endswith(".gguf") and "mmproj" not in f.lower():
                    rel = os.path.relpath(os.path.join(root, f), self.models_dir)
                    found.append(rel.replace("\\", "/"))
        return sorted(found)[:300]

    # ----- процесс llama-server -----

    def healthy(self):
        try:
            http_json(f"http://127.0.0.1:{self.port}/health", timeout=2)
            return True
        except (OSError, ValueError):
            return False

    def _args(self, rel, params):
        p = {**PARAM_DEFAULTS, **(params or {})}
        kv = str(p["kv_cache_type"])
        gl = int(p["gpu_layers"])
        args = [self.bin, "-m", self.model_path(rel), "--host", "127.0.0.1",
                "--port", str(self.port), "-c", str(int(p["ctx"])),
                "-ngl", "999" if gl < 0 else str(gl), "-np", str(int(p["parallel"])),
                "--cache-type-k", kv, "--cache-type-v", kv, "-mg", str(p["main_gpu"])]
        if int(p["threads"]) > 0:
            args += ["-t", str(int(p["threads"]))]
        if p["flash_attn"]:
            args += ["-fa", "on"]       # новые сборки llama-server требуют значение: on|off|auto
        if str(p["tensor_split"]).strip():
            args += ["-ts", str(p["tensor_split"]).strip()]
        return args + self.extra_args

    def start(self, rel, params=None):
        self.stop()
        if not os.path.isfile(self.model_path(rel)):
            raise BackendError(f"модель не найдена: {self.model_path(rel)}")
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        env = dict(os.environ)
        if self.cuda_order:
            env["CUDA_DEVICE_ORDER"] = self.cuda_order
        log = open(self.log_path, "a", encoding="utf-8", errors="replace")
        try:
            self.proc = subprocess.Popen(self._args(rel, params), env=env,
                                         stdout=log, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise BackendError(f"не удалось запустить {self.bin}: {exc}") from exc
        finally:
            log.close()
        deadline = time.time() + 240
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise BackendError(f"llama-server завершился с кодом {self.proc.returncode}; см. {self.log_path}")
            if self.healthy():
                self.loaded = rel
                self.external = False
                return rel
            time.sleep(1.0)
        self.stop()
        raise BackendError(f"llama-server не ответил за 240 с; см. {self.log_path}")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.loaded = None
        self.external = False

    def ensure(self, rel, params=None):
        """Гарантирует, что нужная модель отвечает. Перезапускает сервер при смене модели."""
        rel = (rel or self.default_rel).replace("\\", "/")
        if self.healthy():
            if self.proc is None and self.loaded is None:
                self.external = True     # сервер поднят вручную (например, вашим приложением)
                self.loaded = rel if rel else "(внешний сервер)"
                return self.loaded
            if self.loaded == rel:
                return rel
            if self.external:
                raise BackendError("llama-server уже запущен вручную с другой моделью; "
                                   "остановите его или включите LLAMACPP_AUTOSTART=1")
        if not self.autostart:
            raise BackendError("llama-server не запущен и LLAMACPP_AUTOSTART=0")
        if not rel:
            raise BackendError("не задан LLAMACPP_DEFAULT_MODEL")
        return self.start(rel, params)

    def status(self):
        return {"backend": self.name, "url": self.base_url, "loaded": self.loaded,
                "external": self.external, "models_dir": self.models_dir}


def make_backend(env=None, data_dir="data"):
    env = os.environ if env is None else env
    name = env.get("LLM_BACKEND", "llamacpp").strip().lower()
    if name == "llamacpp":
        return LlamaCppBackend(env, log_dir=data_dir)
    if name == "lmstudio":
        return LMStudioBackend(env)
    raise BackendError(f"неизвестный LLM_BACKEND={name!r}: допустимо llamacpp или lmstudio")
