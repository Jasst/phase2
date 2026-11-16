# -*- coding: utf-8 -*-
"""Приложение: сеть pulsenet + LLM-бэкенд + состояние UI и история.

Ход генерации: сеть показывает путь импульса (события trace), затем LLM отвечает
потоком (token), после ответа сеть дистиллирует пару вопрос→ответ (teach).
"""
import json
import os
import threading

import numpy as np

from . import store
from .config import Config
from .llm import PARAM_DEFAULTS, BackendError, make_backend
from .net import BrainNet
from .vocab import tokenize

CONTEXT_TURNS = 6


def _read_json(path, default):
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default
    return default


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


class App:
    def __init__(self, env, data_dir="data"):
        self.env = env
        self.data_dir = data_dir
        self.state_dir = os.path.join(data_dir, "net")
        os.makedirs(data_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.backend = make_backend(env)
        self.net = store.load(self.state_dir) if os.path.isfile(
            os.path.join(self.state_dir, "meta.json")) else BrainNet(Config())
        self.settings = _read_json(os.path.join(data_dir, "model_settings.json"),
                                   {"model": "", "params": {}})
        self.ui = _read_json(os.path.join(data_dir, "ui_state.json"), {"updated": 0})
        self.history = _read_json(os.path.join(data_dir, "history.json"), {})
        self.gen_active = False
        self.stop_flag = False
        self.last = None

    # ---------- модели ----------

    def default_model(self):
        if self.backend.name == "llamacpp":
            return self.settings.get("model") or self.backend.default_rel
        return self.settings.get("model") or self.backend.default_model

    def models(self):
        return self.backend.list_models()

    def params_for(self, model):
        return {**PARAM_DEFAULTS, **self.settings.get("params", {}).get(model, {})}

    def models_payload(self):
        try:
            models, error = self.models(), None
        except BackendError as exc:
            models, error = [], str(exc)
        return {"models": models, "loaded": self.backend.loaded,
                "default": self.default_model(), "desired": self.settings.get("model") or None,
                "backend": self.backend.name, "error": error}

    def select_model(self, model, check_only=False):
        if check_only:
            return {"ok": True, "loaded": self.backend.loaded}
        if not model or model not in self.models():
            raise BackendError(f"модель не найдена в списке: {model!r}")
        self.settings["model"] = model
        self._save_settings()
        with self.lock:
            self.backend.ensure(model, self.params_for(model))
        return {"ok": True, "loaded": self.backend.loaded}

    def settings_payload(self):
        try:
            models = self.models()
        except BackendError:
            models = []
        return {"models": [{"id": m, "params": self.params_for(m)} for m in models],
                "defaults": PARAM_DEFAULTS, "loaded": self.backend.loaded,
                "editable": bool(getattr(self.backend, "editable", False)),
                "note": "" if getattr(self.backend, "editable", False)
                        else "LM Studio управляет параметрами загрузки сама",
                "models_dir": getattr(self.backend, "models_dir", ""),
                "backend": self.backend.name}

    def save_model_params(self, model, params):
        clean = {k: params[k] for k in PARAM_DEFAULTS if k in params}
        self.settings.setdefault("params", {})[model] = clean
        self._save_settings()
        return {"ok": True, "restart_needed": self.backend.loaded == model}

    def unload(self):
        with self.lock:
            self.backend.stop()
        return {"ok": True}

    def _save_settings(self):
        _write_json(os.path.join(self.data_dir, "model_settings.json"), self.settings)

    # ---------- генерация ----------

    def begin_generation(self):
        with self.lock:
            if self.gen_active:
                return False
            self.gen_active = True
            self.stop_flag = False
            return True

    def end_generation(self):
        with self.lock:
            self.gen_active = False

    def context(self, chat_id):
        turns = self.history.get(chat_id or "default", [])[-CONTEXT_TURNS * 2:]
        return [{"role": t["role"], "content": t["content"]} for t in turns]

    def run_generation(self, message, model, chat_id, emit):
        with self.lock:
            reply = self.net.respond(message)
        emit({"gen_phase": {"phase": "Импульс по ансамблям сети"}})
        for word, cluster, mass in reply.path[:30]:
            emit({"trace": {"net": "pulsenet", "word": word, "cluster": cluster, "mass": mass}})
        if reply.text:
            emit({"trace": {"net": "pulsenet", "own_answer": reply.text}})

        rel = model or self.default_model()
        emit({"gen_phase": {"phase": f"Ответ модели ({self.backend.name})"}})
        with self.lock:
            self.backend.ensure(rel, self.params_for(rel))
        parts = []

        def on_delta(kind, text):
            if self.stop_flag:
                return
            if kind == "token":
                parts.append(text)
                emit({"token": text})
            else:
                emit({"reasoning_token": text})

        messages = self.context(chat_id) + [{"role": "user", "content": message}]
        self.backend.stream(rel, messages, on_delta, cancelled=lambda: self.stop_flag)
        answer = "".join(parts).strip()
        if not answer or self.stop_flag:
            return
        emit({"gen_phase": {"phase": "Сеть дистиллирует ответ"}})
        with self.lock:
            self.net.teach(message, answer, 1.0)
            store.save(self.net, self.state_dir)
        self._remember(chat_id or "default", message, answer)
        emit({"gen_phase": {"phase": "Готово"}})

    def _remember(self, chat_id, user, assistant):
        with self.lock:
            turns = self.history.setdefault(chat_id, [])
            turns.append({"role": "user", "content": user})
            turns.append({"role": "assistant", "content": assistant})
            del turns[:-CONTEXT_TURNS * 4]
            self.last = {"user": user, "assistant": assistant, "images": []}
            _write_json(os.path.join(self.data_dir, "history.json"), self.history)

    def last_response(self, user_text=None):
        if self.last and (not user_text or self.last["user"] == user_text):
            return self.last
        return {}

    # ---------- состояние UI ----------

    def ui_put(self, body):
        with self.lock:
            for k, v in body.items():
                if k not in ("updated", "version"):
                    self.ui[k] = v
            self.ui["updated"] = int(self.ui.get("updated", 0)) + 1
            _write_json(os.path.join(self.data_dir, "ui_state.json"), self.ui)
            return {"ok": True, "updated": self.ui["updated"]}

    def ui_get(self):
        with self.lock:
            return dict(self.ui)

    # ---------- память (граф ансамблей и цепей) ----------

    def memory_snapshot(self, max_edges=400):
        with self.lock:
            net, cx = self.net, self.net.cortex
            words = net.vocab.words
            members = {k: [words[i] for i in ws] for k, ws in cx.members.items() if ws}
            T = net.chain.T
            idx = np.argwhere(T > 1e-3)
            weights = T[idx[:, 0], idx[:, 1]] if len(idx) else np.zeros(0)
            order = np.argsort(-weights)[:max_edges]
            degree = {}
            edges = []
            for r in order:
                i, j = int(idx[r][0]), int(idx[r][1])
                if i not in members or j not in members:
                    continue
                degree[i] = degree.get(i, 0) + 1
                degree[j] = degree.get(j, 0) + 1
                edges.append({"key": f"{i}>{j}", "source": f"c{i}", "target": f"c{j}",
                              "relation": "импульс →", "weight": round(float(weights[r]), 4)})
            nodes = []
            for k, ws in members.items():
                label = [w for w in ws if w.isalnum()][:4] or ws[:1]
                nodes.append({"id": f"c{k}", "subject": " ".join(label),
                              "predicate": f"ансамбль из {len(ws)}", "type": "ensemble",
                              "salience": round(len(ws) / net.cfg.max_cluster_size, 3),
                              "confidence": 1.0, "degree": degree.get(k, 0), "version": net.updates})
            nodes.sort(key=lambda n: -n["degree"])
            return {"version": net.updates, "scope": "me", "readonly": False,
                    "nodes": nodes[:300], "edges": edges, "stats": net.stats()}

    def save_all(self):
        with self.lock:
            store.save(self.net, self.state_dir)
            _write_json(os.path.join(self.data_dir, "ui_state.json"), self.ui)
            _write_json(os.path.join(self.data_dir, "history.json"), self.history)
