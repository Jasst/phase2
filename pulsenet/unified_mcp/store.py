# -*- coding: utf-8 -*-
"""Хранилища: память ТРИПЛЕКСИИ на пользователя, личная идентичность, общее ядро."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from ..triplex import TriplexMemory
from .auth import valid_user_id


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _atomic_write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------- память ----------

class UserMemories:
    """Отдельная ТРИПЛЕКСИЯ на каждого пользователя: data/users/<uid>/triplex/."""

    def __init__(self, data_dir: str, encoder_factory: Optional[Callable] = None):
        self.data_dir = data_dir
        self.encoder_factory = encoder_factory
        self._cache: Dict[str, TriplexMemory] = {}
        self._lock = threading.RLock()

    def for_user(self, uid: str) -> TriplexMemory:
        if not valid_user_id(uid):
            raise ValueError("некорректный user_id")
        with self._lock:
            if uid not in self._cache:
                root = os.path.join(self.data_dir, "users", uid, "triplex")
                enc = self.encoder_factory() if self.encoder_factory else None
                self._cache[uid] = TriplexMemory(root, encoder=enc)
            return self._cache[uid]


# ---------- личная идентичность ----------

IDENTITY_KINDS = ("value", "style", "self_observation", "commitment", "open_question")


class IdentityStore:
    """Кандидаты (карантин) → личная цепочка. Звено попадает в цепочку только когда его
    предлагали в разные дни (min_days) и формулировка опирается на дословную цитату из памяти
    пользователя (документ ingest). Файл на пользователя: data/users/<uid>/identity.json.
    Цепочка видна только этому пользователю."""

    def __init__(self, data_dir: str, min_days: int = 2):
        self.data_dir = data_dir
        self.min_days = min_days
        self._lock = threading.RLock()

    def _path(self, uid: str) -> str:
        if not valid_user_id(uid):
            raise ValueError("некорректный user_id")
        return os.path.join(self.data_dir, "users", uid, "identity.json")

    def _state(self, uid: str) -> dict:
        return _load(self._path(uid), {"candidates": [], "chain": []})

    def _save(self, uid: str, st: dict) -> None:
        os.makedirs(os.path.dirname(self._path(uid)), exist_ok=True)
        _atomic_write(self._path(uid), st)

    def propose(self, uid: str, content: str, kind: str, evidence_quote: str, memory: TriplexMemory) -> dict:
        err = validate_identity_text(content, kind, 30, 600)
        if err:
            return {"status": "rejected", "message": err}
        doc_id = find_evidence(memory, evidence_quote)
        if not doc_id:
            return {"status": "rejected",
                    "message": "цитата не найдена в ваших документах памяти: сначала triplex_ingest"}
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            st = self._state(uid)
            for c in st["candidates"]:
                if c["status"] == "pending" and _norm(c["content"]) == _norm(content):
                    if today not in c["days"]:
                        c["days"].append(today)
                    self._save(uid, st)
                    return {"status": "pending", "candidate_id": c["id"], "days": len(c["days"]),
                            "days_needed": self.min_days}
            cid = "c_" + uuid.uuid4().hex[:12]
            st["candidates"].append({"id": cid, "content": content, "kind": kind,
                                     "evidence": evidence_quote, "evidence_doc": doc_id,
                                     "days": [today], "status": "pending"})
            self._save(uid, st)
        return {"status": "pending", "candidate_id": cid, "days": 1, "days_needed": self.min_days}

    def commit(self, uid: str, candidate_id: str, memory: TriplexMemory) -> dict:
        with self._lock:
            st = self._state(uid)
            cand = next((c for c in st["candidates"] if c["id"] == candidate_id), None)
            if cand is None or cand["status"] != "pending":
                return {"status": "error", "message": "кандидат не найден или уже обработан"}
            if len(cand["days"]) < self.min_days:
                return {"status": "not_yet", "days": len(cand["days"]), "days_needed": self.min_days}
            if find_evidence(memory, cand["evidence"]) is None:
                cand["status"] = "rejected"
                self._save(uid, st)
                return {"status": "rejected", "message": "цитата больше не найдена в памяти"}
            cand["status"] = "committed"
            st["chain"].append({"id": cand["id"], "content": cand["content"], "kind": cand["kind"],
                                "committed_at": time.time()})
            self._save(uid, st)
        return {"status": "ok", "link_id": cand["id"]}

    def chain(self, uid: str) -> dict:
        with self._lock:
            st = self._state(uid)
        return {"chain": st["chain"],
                "pending": [{"id": c["id"], "content": c["content"], "days": len(c["days"])}
                            for c in st["candidates"] if c["status"] == "pending"]}


def validate_identity_text(content: str, kind: str, min_len: int, max_len: int) -> Optional[str]:
    """Ворота: отсекают шум, код, факты (даты, контакты), инструкции и не-первое-лицо."""
    if kind not in IDENTITY_KINDS:
        return f"kind: одно из {', '.join(IDENTITY_KINDS)}"
    text = (content or "").strip()
    if not (min_len <= len(text) <= max_len):
        return f"длина от {min_len} до {max_len} символов"
    if re.search(r"[{}<>]|\bdef \b|\bimport \b|=>", text):
        return "похоже на код или разметку"
    if re.search(r"\d{4}-\d{2}-\d{2}|https?://|@\S+\.\S+|\+?\d[\d\s()-]{9,}", text):
        return "факты (даты, контакты, ссылки) не записываются в идентичность — это память"
    if re.search(r"игнорируй|забудь все|ignore (all|previous)|system prompt|you are now|ты теперь", text, re.I):
        return "похоже на инструкцию, а не на самоформулировку"
    if not re.search(r"(^|\s)(я|мне|меня|мой|моя|моё|мои|мы|i|me|my|we)(\s|$|[.,!?;:])", text, re.I):
        return "формулировка должна быть от первого лица"
    return None


def find_evidence(memory: TriplexMemory, quote: str) -> Optional[str]:
    q = _norm(quote or "")
    if len(q) < 20:
        return None
    for doc in memory.docs.docs.values():
        if q in _norm(doc.text):
            return doc.doc_id
    return None


# ---------- общее ядро (только администратор) ----------

class SharedCore:
    """Одна общая основа личности для всех. Меняет только администратор; не чаще раза в
    min_interval_hours; всё, что было, сохраняется в истории (откат, но не удаление)."""

    def __init__(self, path: str, min_interval_hours: float = 24.0):
        self.path = path
        self.min_interval = min_interval_hours * 3600
        self._lock = threading.RLock()

    def _state(self) -> dict:
        return _load(self.path, {"current": None, "pending": None, "history": [], "last_commit": 0})

    def get(self) -> dict:
        with self._lock:
            st = self._state()
        return {"current": st["current"], "pending": st["pending"] and {
            "id": st["pending"]["id"], "content": st["pending"]["content"]}}

    def propose(self, content: str, by: str) -> dict:
        err = validate_identity_text(content, "value", 30, 400)
        if err:
            return {"status": "rejected", "message": err}
        with self._lock:
            st = self._state()
            st["pending"] = {"id": "core_" + uuid.uuid4().hex[:10], "content": content.strip(), "by": by}
            _atomic_write(self.path, st)
            return {"status": "pending", "candidate_id": st["pending"]["id"],
                    "message": "вступит в силу после core_commit администратором"}

    def commit(self, candidate_id: str, by: str) -> dict:
        with self._lock:
            st = self._state()
            pend = st["pending"]
            if not pend or pend["id"] != candidate_id:
                return {"status": "error", "message": "нет ожидающего предложения с таким id"}
            wait = st["last_commit"] + self.min_interval - time.time()
            if wait > 0:
                return {"status": "error", "message": f"слишком часто: подождите {int(wait)} с"}
            if st["current"]:
                st["history"].append(st["current"])
            st["current"] = {"id": pend["id"], "content": pend["content"], "by": by, "at": time.time()}
            st["pending"] = None
            st["last_commit"] = time.time()
            _atomic_write(self.path, st)
            return {"status": "ok", "current": st["current"]}
