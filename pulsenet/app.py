# -*- coding: utf-8 -*-
"""Приложение: сеть pulsenet + LLM-бэкенд + история обучения + состояние UI.

Ход генерации: сеть показывает путь импульса (trace), затем LLM отвечает потоком
(token), после ответа сеть дистиллирует пару вопрос→ответ (teach) и сохраняет
себя на диск. При старте сеть загружается с диска, поэтому обучение продолжается.
"""
import json
import os
import shutil
import threading
import time

import numpy as np

from . import store
from .config import Config
from .llm import PARAM_DEFAULTS, BackendError, make_backend
from .net import BrainNet

CONTEXT_TURNS = 6
DEV_HISTORY_LIMIT = 2000


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
        self.net = self._load_net()
        self.settings = _read_json(os.path.join(data_dir, "model_settings.json"),
                                   {"model": "", "params": {}})
        self.ui = _read_json(os.path.join(data_dir, "ui_state.json"), {"updated": 0})
        self.history = _read_json(os.path.join(data_dir, "history.json"), {})
        self.dev = _read_json(os.path.join(data_dir, "dev_history.json"), [])
        self.gen_active = False
        self.stop_flag = False
        self.last = None
        self.last_saved = self._mtime()

    # ---------- сеть: загрузка, сохранение ----------

    def _load_net(self):
        if os.path.isfile(os.path.join(self.state_dir, "meta.json")):
            return store.load(self.state_dir)
        return BrainNet(Config())

    def _mtime(self):
        meta = os.path.join(self.state_dir, "meta.json")
        return os.path.getmtime(meta) if os.path.isfile(meta) else None

    def save_net(self):
        with self.lock:
            store.save(self.net, self.state_dir)
            self.last_saved = self._mtime()

    def load_net(self):
        with self.lock:
            if not os.path.isfile(os.path.join(self.state_dir, "meta.json")):
                return {"ok": False, "error": "на диске нет сохранённой сети"}
            self.net = self._load_net()
            self.last_saved = self._mtime()
        return dict(self.net_state(), ok=True)

    def reset_net(self):
        """Сброс: текущая сеть сохраняется в резервную папку, в рабочей — пустая сеть."""
        with self.lock:
            backup = None
            if os.path.isfile(os.path.join(self.state_dir, "meta.json")):
                backup = os.path.join(self.data_dir, "backups", time.strftime("net-%Y%m%d-%H%M%S"))
                shutil.copytree(self.state_dir, backup)
            self.net = BrainNet(Config())
            shutil.rmtree(self.state_dir, ignore_errors=True)
            self.last_saved = None
        state = self.net_state()
        state["backup"] = backup
        return state

    def restore_backup(self):
        """Восстановить самую свежую резервную копию (создаётся при сбросе) как рабочую сеть."""
        root = os.path.join(self.data_dir, "backups")
        copies = sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "meta.json"))) \
            if os.path.isdir(root) else []
        if not copies:
            return {"ok": False, "error": "резервных копий нет"}
        with self.lock:
            shutil.rmtree(self.state_dir, ignore_errors=True)
            shutil.copytree(os.path.join(root, copies[-1]), self.state_dir)
            self.net = self._load_net()
            self.last_saved = self._mtime()
        return dict(self.net_state(), ok=True, restored=copies[-1])

    def _record(self, loss):
        s = self.net.stats()
        self.dev.append({"t": round(time.time(), 1), "lesson": self.net.lessons,
                         "words": s["слов"], "ensembles": s["ансамблей"], "links": s["связей"],
                         "loss": None if loss is None else round(float(loss), 4)})
        del self.dev[:-DEV_HISTORY_LIMIT]
        _write_json(os.path.join(self.data_dir, "dev_history.json"), self.dev)

    def teach(self, question, answer, weight=1.0):
        """Онлайн-урок: сеть учится на паре и сохраняет себя."""
        with self.lock:
            loss = self.net.teach(question, answer, weight)
            if loss is None:
                return {"ok": False, "error": "пустой вопрос или ответ"}
            self._record(loss)
            self.save_net()
            return {"ok": True, "loss": round(float(loss), 4), "stats": self.net.stats()}

    def net_state(self):
        with self.lock:
            meta = os.path.join(self.state_dir, "meta.json")
            return {"stats": self.net.stats(), "lessons": self.net.lessons,
                    "saved_at": self.last_saved,
                    "on_disk": os.path.isfile(meta),
                    "size_bytes": os.path.getsize(meta) if os.path.isfile(meta) else 0,
                    "dim": self.net.cfg.dim, "history": self.dev[-400:]}

    def respond(self, question, temp=None):
        with self.lock:
            reply = self.net.respond(question, temp=temp)
        return {"text": reply.text,
                "path": [{"word": w, "ensemble": int(k), "mass": m} for w, k, m in reply.path]}

    def graph(self, max_edges=600):
        with self.lock:
            net, cx = self.net, self.net.cortex
            words = net.vocab.words
            members = {k: [words[i] for i in ws] for k, ws in cx.members.items() if ws}
            T = net.chain.T
            idx = np.argwhere(T > 1e-3)
            weights = T[idx[:, 0], idx[:, 1]] if len(idx) else np.zeros(0)
            order = np.argsort(-weights)[:max_edges]
            edges, degree = [], {}
            for r in order:
                i, j = int(idx[r][0]), int(idx[r][1])
                if i not in members or j not in members:
                    continue
                degree[i] = degree.get(i, 0) + 1
                degree[j] = degree.get(j, 0) + 1
                edges.append({"source": i, "target": j, "weight": round(float(weights[r]), 4)})
            names = {"<eos>": "конец"}
            nodes = [{"id": k, "size": len(ws), "words": [names.get(w, w) for w in ws[:12]],
                      "degree": degree.get(k, 0)} for k, ws in members.items()]
            return {"nodes": nodes, "edges": edges, "total_ensembles": len(members)}

    def neurons(self, ensemble):
        with self.lock:
            net = self.net
            ws = net.cortex.members.get(int(ensemble), [])
            out = []
            names = {"<eos>": "конец ответа"}
            for i in ws:
                prof = net.cortex.prof.get(i, {})
                top = sorted(prof.items(), key=lambda kv: -kv[1])[:6]
                word = net.vocab.words[i]
                out.append({"word": names.get(word, word),
                            "neighbours": [[names.get(net.vocab.words[n], net.vocab.words[n]), round(float(c), 1)]
                                           for n, c in top]})
            return {"ensemble": int(ensemble), "neurons": out}

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
        editable = bool(getattr(self.backend, "editable", False))
        return {"models": [{"id": m, "params": self.params_for(m)} for m in models],
                "defaults": PARAM_DEFAULTS, "loaded": self.backend.loaded, "editable": editable,
                "note": "" if editable else "LM Studio управляет параметрами загрузки сама",
                "models_dir": getattr(self.backend, "models_dir", ""), "backend": self.backend.name}

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
        reply = self.respond(message)
        emit({"gen_phase": {"phase": "Импульс по ансамблям сети"}})
        for step in reply["path"][:30]:
            emit({"trace": {"net": "pulsenet", "word": step["word"],
                            "cluster": step["ensemble"], "mass": step["mass"]}})
        if reply["text"]:
            emit({"trace": {"net": "pulsenet", "own_answer": reply["text"]}})

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
        emit({"gen_phase": {"phase": "Сеть учится на ответе"}})
        self.teach(message, answer, 1.0)
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

    def save_all(self):
        self.save_net()
        with self.lock:
            _write_json(os.path.join(self.data_dir, "ui_state.json"), self.ui)
            _write_json(os.path.join(self.data_dir, "history.json"), self.history)
