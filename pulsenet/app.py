# -*- coding: utf-8 -*-
"""Приложение: сеть + LLM + режимы + журнал уроков + проверка + состояние UI.

Режимы ответа (settings.mode):
  llm — отвечает LLM; сеть учится на ответе, если включено auto_learn;
  net — отвечает сама сеть (мысль, импульс); учится только по действиям пользователя.

Обратная связь (feedback): good — закрепить пару; bad — ослабить; fix — пользователь
даёт правильный ответ: правильный закрепляется, неверный ослабляется.
Хорошие ответы попадают в проверочный набор (eval.json), по нему измеряется
припоминание: сеть должна снова выдать слова из подтверждённых ответов.
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
from .vocab import tokenize

CONTEXT_TURNS = 6
DEV_HISTORY_LIMIT = 2000
EPISODES_LIMIT = 5000
EVAL_LIMIT = 200
FIX_WEIGHT = 2.0               # вес исправления по умолчанию (меняется в настройках)
STOP_WORDS = {"это", "как", "что", "для", "или", "все", "его", "она", "они", "при", "так", "там", "тут", "был", "быть"}


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
        self.settings.setdefault("mode", "llm")
        self.settings.setdefault("auto_learn", True)
        self.ui = _read_json(os.path.join(data_dir, "ui_state.json"), {"updated": 0})
        self.history = _read_json(os.path.join(data_dir, "history.json"), {})
        self.dev = _read_json(os.path.join(data_dir, "dev_history.json"), [])
        self.episodes = _read_json(os.path.join(data_dir, "episodes.json"), [])
        self.eval_set = _read_json(os.path.join(data_dir, "eval.json"), [])
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
        """Сброс: текущая сеть копируется в резервную папку, рабочая становится пустой."""
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

    # ---------- журналы ----------

    def _record(self, loss, kind="lesson"):
        s = self.net.stats()
        entry = {"t": round(time.time(), 1), "kind": kind, "lesson": self.net.lessons,
                 "words": s["слов"], "ensembles": s["ансамблей"], "links": s["связей"],
                 "loss": None if loss is None else round(float(loss), 4)}
        self.dev.append(entry)
        del self.dev[:-DEV_HISTORY_LIMIT]
        _write_json(os.path.join(self.data_dir, "dev_history.json"), self.dev)

    def _log_episode(self, question, answer, weight, source):
        self.episodes.append({"id": len(self.episodes) + 1, "t": round(time.time(), 1),
                              "question": question, "answer": answer,
                              "weight": weight, "source": source, "lesson": self.net.lessons})
        del self.episodes[:-EPISODES_LIMIT]
        _write_json(os.path.join(self.data_dir, "episodes.json"), self.episodes)

    def _add_eval(self, question, answer):
        words = [w for w in tokenize(answer) if w.isalnum() and len(w) >= 3 and w not in STOP_WORDS]
        expected = list(dict.fromkeys(words))[:4]
        if not expected:
            return
        self.eval_set = [e for e in self.eval_set if e["question"] != question]
        self.eval_set.append({"question": question, "expected": expected})
        del self.eval_set[:-EVAL_LIMIT]
        _write_json(os.path.join(self.data_dir, "eval.json"), self.eval_set)

    # ---------- обучение и обратная связь ----------

    def teach(self, question, answer, weight=1.0, source="manual"):
        """Онлайн-урок: сеть учится на паре и сохраняет себя."""
        with self.lock:
            loss = self.net.teach(question, answer, weight)
            if loss is None:
                return {"ok": False, "error": "пустой вопрос или ответ"}
            self._record(loss, source)
            self._log_episode(question, answer, weight, source)
            self.save_net()
            return {"ok": True, "loss": round(float(loss), 4), "stats": self.net.stats()}

    def feedback(self, question, answer, verdict, correct=None):
        """good — закрепить; bad — ослабить; fix — правильный ответ вместо неверного."""
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not question:
            return {"ok": False, "error": "нет вопроса"}
        with self.lock:
            if verdict == "good":
                r = self.teach(question, answer, 1.0, "good")
                if r["ok"]:
                    self._add_eval(question, answer)
                return r
            if verdict == "bad":
                return self.teach(question, answer, -1.0, "bad")
            if verdict == "fix":
                correct = (correct or "").strip()
                if not correct:
                    return {"ok": False, "error": "нет правильного ответа"}
                w = self.fix_weight()
                if answer:   # исправление сильнее обычного урока: старое знание переписывается быстрее
                    self.teach(question, answer, -w, "fix-wrong")
                r = self.teach(question, correct, w, "fix")
                if r["ok"]:
                    self._add_eval(question, correct)
                return r
        return {"ok": False, "error": f"неизвестный verdict: {verdict}"}

    def run_eval(self):
        """Проверка припоминания: доля ожидаемых слов, которые сеть выдаёт на вопрос."""
        with self.lock:
            details = []
            for item in self.eval_set:
                reply = self.net.respond(item["question"], temp=0)
                got = set(tokenize(reply.text))
                exp = item["expected"]
                hit = len([w for w in exp if w in got]) / len(exp)
                details.append({"question": item["question"], "score": round(hit, 3),
                                "answer": reply.text, "confidence": reply.confidence})
            score = round(float(np.mean([d["score"] for d in details])), 3) if details else None
            self.dev.append({"t": round(time.time(), 1), "kind": "eval", "lesson": self.net.lessons,
                             "eval": score, "words": self.net.stats()["слов"],
                             "ensembles": self.net.stats()["ансамблей"],
                             "links": self.net.stats()["связей"], "loss": None})
            del self.dev[:-DEV_HISTORY_LIMIT]
            _write_json(os.path.join(self.data_dir, "dev_history.json"), self.dev)
        return {"score": score, "count": len(details), "details": details}

    def set_mode(self, mode=None, auto_learn=None):
        if mode is not None:
            if mode not in ("llm", "net"):
                return {"ok": False, "error": "mode: llm или net"}
            self.settings["mode"] = mode
        if auto_learn is not None:
            self.settings["auto_learn"] = bool(auto_learn)
        self._save_settings()
        return {"ok": True, "mode": self.settings["mode"], "auto_learn": self.settings["auto_learn"]}


    # ---------- параметры сети (страница настроек) ----------

    # имя: (тип, минимум, максимум, где хранится)
    TUNING = {
        "temperature": ("float", 0.0, 1.5, "cfg"),
        "min_confidence": ("float", 0.0, 0.3, "cfg"),
        "settle_steps": ("int", 0, 6, "cfg"),
        "focus_gain": ("float", 0.0, 1.0, "cfg"),
        "replay_n": ("int", 0, 5, "cfg"),
        "fix_weight": ("float", 1.0, 4.0, "app"),
    }

    def fix_weight(self):
        return float(self.settings.get("fix_weight", FIX_WEIGHT))

    def get_tuning(self):
        cfg = self.net.cfg
        out = {}
        for name, (_t, lo, hi, where) in self.TUNING.items():
            value = self.fix_weight() if where == "app" else getattr(cfg, name)
            out[name] = {"value": value, "min": lo, "max": hi}
        return out

    def set_tuning(self, values):
        """Применить параметры. Неизвестные имена игнорируются, значения проверяются по границам."""
        errors, applied = [], {}
        for name, raw in (values or {}).items():
            if name not in self.TUNING:
                continue
            kind, lo, hi, _where = self.TUNING[name]
            try:
                v = int(raw) if kind == "int" else float(raw)
            except (TypeError, ValueError):
                errors.append(f"{name}: нужно число")
                continue
            if not lo <= v <= hi:
                errors.append(f"{name}: допустимо от {lo} до {hi}")
                continue
            applied[name] = v
        with self.lock:
            for name, v in applied.items():
                if self.TUNING[name][3] == "app":
                    self.settings["fix_weight"] = v
                else:
                    setattr(self.net.cfg, name, v)
            if applied:
                self._save_settings()
                self.save_net()
        return {"ok": not errors, "applied": applied, "errors": errors, "tuning": self.get_tuning()}

    # ---------- состояние сети для страницы ----------

    def net_state(self):
        with self.lock:
            meta = os.path.join(self.state_dir, "meta.json")
            ev = [h for h in self.dev if h.get("kind") == "eval"]
            return {"stats": self.net.stats(), "lessons": self.net.lessons,
                    "saved_at": self.last_saved, "on_disk": os.path.isfile(meta),
                    "size_bytes": os.path.getsize(meta) if os.path.isfile(meta) else 0,
                    "dim": self.net.cfg.dim, "history": self.dev[-400:],
                    "mode": self.settings["mode"], "auto_learn": self.settings["auto_learn"],
                    "focus": self.focus_words(), "self_error": self.net.self_error_avg(),
                    "episodes": len(self.episodes), "recent": self.episodes[-8:][::-1],
                    "eval_items": len(self.eval_set),
                    "eval_last": ev[-1]["eval"] if ev else None}

    def focus_words(self, k=5):
        out = []
        for ens, mass in self.net.top_focus(k):
            words = [self.net.vocab.words[i] for i in self.net.cortex.members.get(ens, [])][:3]
            out.append({"ensemble": ens, "mass": mass, "words": words})
        return out

    def respond(self, question, temp=None):
        with self.lock:
            reply = self.net.respond(question, temp=temp)
        names = {"<eos>": "конец"}
        return {"text": reply.text, "confidence": reply.confidence, "self_error": reply.self_error,
                "known": reply.known, "focus": self.focus_words(),
                "path": [{"word": names.get(w, w), "ensemble": int(k), "mass": m}
                         for w, k, m in reply.path]}

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
            names = {"<eos>": "конец ответа"}
            out = []
            for i in ws:
                prof = net.cortex.prof.get(i, {})
                top = sorted(prof.items(), key=lambda kv: -kv[1])[:6]
                word = net.vocab.words[i]
                out.append({"word": names.get(word, word),
                            "neighbours": [[names.get(net.vocab.words[n], net.vocab.words[n]), round(float(c), 1)]
                                           for n, c in top]})
            return {"ensemble": int(ensemble), "neurons": out}

    # ---------- модели LLM ----------

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
        emit({"gen_phase": {"phase": "Мысль и импульс сети"}})
        for step in reply["path"][:30]:
            emit({"trace": {"net": "pulsenet", "word": step["word"],
                            "cluster": step["ensemble"], "mass": step["mass"]}})
        emit({"trace": {"net": "pulsenet", "confidence": reply["confidence"],
                        "self_error": reply["self_error"], "focus": reply["focus"]}})

        if self.settings["mode"] == "net":
            emit({"gen_phase": {"phase": "Отвечает сеть"}})
            text = reply["text"]
            if not text:
                text = "Пока не уверена в ответе. Дайте мне урок: введите правильный ответ кнопкой «Переучить»."
            for piece in text.split(" "):
                if self.stop_flag:
                    break
                emit({"token": piece + " "})
            answer = reply["text"]
            source = "net"
        else:
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
            source = "llm"
            if answer and not self.stop_flag and self.settings.get("auto_learn", True):
                emit({"gen_phase": {"phase": "Сеть учится на ответе LLM"}})
                self.teach(message, answer, 1.0, "llm")

        if answer and not self.stop_flag:
            self._remember(chat_id or "default", message, answer, source)
        emit({"turn": {"question": message, "answer": answer, "source": source,
                       "confidence": reply["confidence"], "self_error": reply["self_error"],
                       "uncertain": not answer}})
        emit({"gen_phase": {"phase": "Готово"}})

    def _remember(self, chat_id, user, assistant, source="llm"):
        with self.lock:
            turns = self.history.setdefault(chat_id, [])
            turns.append({"role": "user", "content": user})
            turns.append({"role": "assistant", "content": assistant})
            del turns[:-CONTEXT_TURNS * 4]
            self.last = {"user": user, "assistant": assistant, "source": source, "images": []}
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
