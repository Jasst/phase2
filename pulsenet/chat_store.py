# -*- coding: utf-8 -*-
"""Серверное хранилище чатов: один аккаунт — одни и те же чаты на всех устройствах.

Браузер по-прежнему хранит копию в localStorage, но источник истины — этот SQLite:
сообщения и заголовки пишутся сервером (в том числе финальный ответ, когда все
устройства отключились), а клиенты подтягивают изменения по `/api/chats`.

Ключ сообщения — его `id`, который выдаёт браузер при отправке. Благодаря этому
повторная синхронизация не создаёт дубликатов, а сервер и клиент сходятся к одному
и тому же набору сообщений даже после нескольких переподключений.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Optional


class ChatStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=10000")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS chats (
                    uid TEXT NOT NULL,
                    id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    updated REAL NOT NULL,
                    PRIMARY KEY (uid, id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    uid TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    time REAL NOT NULL,
                    PRIMARY KEY (uid, chat_id, id)
                );
                CREATE TABLE IF NOT EXISTS revs (
                    uid TEXT PRIMARY KEY,
                    rev INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS deleted (
                    uid TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    at REAL NOT NULL,
                    PRIMARY KEY (uid, chat_id)
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages
                    ON messages(uid, chat_id, time);
            """)
            self._db.commit()

    # ----- служебное -----

    def _bump(self, uid: str) -> int:
        self._db.execute("INSERT INTO revs(uid, rev) VALUES (?, 1) "
                         "ON CONFLICT(uid) DO UPDATE SET rev = rev + 1", (uid,))
        self._db.commit()
        row = self._db.execute("SELECT rev FROM revs WHERE uid = ?", (uid,)).fetchone()
        return int(row[0]) if row else 0

    def revision(self, uid: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT rev FROM revs WHERE uid = ?", (uid,)).fetchone()
            return int(row[0]) if row else 0

    @staticmethod
    def _dump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load(raw: str, fallback):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _merge_images(a, b) -> List[dict]:
        """Соединяет картинки двух копий ответа без дублей по url."""
        out: List[dict] = []
        seen = set()
        for img in (a if isinstance(a, list) else []) + (b if isinstance(b, list) else []):
            if not isinstance(img, dict):
                continue
            url = img.get("url")
            if not isinstance(url, str) or not url or url in seen:
                continue
            seen.add(url)
            out.append(img)
        return out

    @staticmethod
    def _dedupe_messages(items: Iterable[dict]) -> List[dict]:
        """Схлопывает копии одного ответа, оставшиеся от старого реаттача.

        Копии имеют разные id, но одинаковый текст и время в пределах 10 минут.
        Сервер не удаляет старые записи, поэтому чистим именно на выдаче — так
        все клиенты получают уже выверенную историю.
        """
        out: List[dict] = []
        for m in items:
            if not isinstance(m, dict):
                continue
            role = m.get("role") or ""
            text = str(m.get("text") or "")
            if role == "assistant" and not m.get("pending") and not text \
                    and not m.get("reasoning") and not m.get("tools") and not m.get("sources") \
                    and not m.get("images") \
                    and (m.get("indexPending") or
                         (not m.get("error") and not m.get("note"))):
                continue
            raw_time = float(m.get("time") or 0)
            ms = raw_time * 1000 if 0 < raw_time < 1e12 else raw_time
            twin = None
            if role == "assistant":
                for prev in out:
                    pt = float(prev.get("time") or 0)
                    pms = pt * 1000 if 0 < pt < 1e12 else pt
                    if prev.get("role") == "assistant" and str(prev.get("text") or "") == text \
                            and abs(pms - ms) < 600000:
                        twin = prev
                        break
            if twin is None:
                out.append(m)
                continue
            idx = out.index(twin)
            imgs = ChatStore._merge_images(twin.get("images"), m.get("images"))
            longer = len(text) > len(str(twin.get("text") or "")) or (not twin.get("text") and text)
            if imgs:
                base = m if longer else twin
                merged = dict(base)
                merged["images"] = imgs
                out[idx] = merged
            elif longer:
                out[idx] = m
        return out

    # ----- чтение -----

    def snapshot(self, uid: str) -> dict:
        """Полное состояние чатов пользователя: ревизия, сессии и сообщения по чатам."""
        with self._lock:
            sessions = []
            for row in self._db.execute(
                    "SELECT data FROM chats WHERE uid = ? ORDER BY updated DESC", (uid,)):
                data = self._load(row["data"], None)
                if isinstance(data, dict):
                    sessions.append(data)
            messages: Dict[str, list] = {}
            for row in self._db.execute(
                    "SELECT chat_id, data FROM messages WHERE uid = ? ORDER BY time", (uid,)):
                data = self._load(row["data"], None)
                if isinstance(data, dict):
                    messages.setdefault(row["chat_id"], []).append(data)
            rev = self.revision(uid)
        messages = {cid: self._dedupe_messages(items) for cid, items in messages.items()}
        return {"rev": rev, "sessions": sessions, "messages": messages}

    def chat_messages(self, uid: str, chat_id: str) -> List[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM messages WHERE uid = ? AND chat_id = ? ORDER BY time",
                (uid, chat_id)).fetchall()
        out = []
        for row in rows:
            data = self._load(row["data"], None)
            if isinstance(data, dict):
                out.append(data)
        return self._dedupe_messages(out)

    # ----- запись -----

    def merge(self, uid: str, sessions: Optional[Iterable[dict]] = None,
              messages: Optional[Dict[str, list]] = None,
              deleted: Optional[Iterable[str]] = None) -> int:
        """Слить состояние, присланное браузером. Старые сообщения не удаляются.

        Сессии обновляются только если пришедшее время новее; сообщения — по id,
        последняя запись побеждает. Удаления сохраняются как «могилки», чтобы
        офлайн-устройство не воскресило чат при следующей синхронизации.
        """
        changed = False
        with self._lock:
            now = time.time()
            for chat_id in deleted or []:
                chat_id = str(chat_id)
                row = self._db.execute(
                    "SELECT at FROM deleted WHERE uid = ? AND chat_id = ?", (uid, chat_id)).fetchone()
                if row is not None and float(row[0]) >= now:
                    continue
                self._db.execute("DELETE FROM chats WHERE uid = ? AND id = ?", (uid, chat_id))
                self._db.execute("DELETE FROM messages WHERE uid = ? AND chat_id = ?", (uid, chat_id))
                self._db.execute("INSERT OR REPLACE INTO deleted(uid, chat_id, at) VALUES (?,?,?)",
                                 (uid, chat_id, now))
                changed = True

            tombstones = {row[0]: row[1] for row in self._db.execute(
                "SELECT chat_id, at FROM deleted WHERE uid = ?", (uid,))}
            for sess in sessions or []:
                if not isinstance(sess, dict) or not sess.get("id"):
                    continue
                sid = str(sess["id"])
                updated = float(sess.get("updated") or 0)
                if sid in tombstones and updated <= tombstones[sid]:
                    continue
                row = self._db.execute(
                    "SELECT data, updated FROM chats WHERE uid = ? AND id = ?", (uid, sid)).fetchone()
                if row is not None and float(row[1]) > updated:
                    continue
                dump = self._dump(sess)
                if row is not None and row[0] == dump:
                    continue
                self._db.execute("INSERT OR REPLACE INTO chats(uid, id, data, updated) VALUES (?,?,?,?)",
                                 (uid, sid, dump, updated))
                changed = True

            for chat_id, items in (messages or {}).items():
                chat_id = str(chat_id)
                if not isinstance(items, list):
                    continue
                for msg in items:
                    if not isinstance(msg, dict) or not msg.get("id"):
                        continue
                    dump = self._dump(msg)
                    row = self._db.execute(
                        "SELECT data FROM messages WHERE uid = ? AND chat_id = ? AND id = ?",
                        (uid, chat_id, str(msg["id"]))).fetchone()
                    if row is not None and row[0] == dump:
                        continue
                    self._db.execute(
                        "INSERT OR REPLACE INTO messages(uid, chat_id, id, data, time) VALUES (?,?,?,?,?)",
                        (uid, chat_id, str(msg["id"]), dump, float(msg.get("time") or 0)))
                    changed = True
            if changed:
                return self._bump(uid)
            return self.revision(uid)

    def append(self, uid: str, chat_id: str, messages: Iterable[dict],
               title: str = "") -> int:
        """Дописать сообщения сервером (пользовательский вопрос и/или готовый ответ)."""
        changed = False
        with self._lock:
            now = time.time()
            self._db.execute("DELETE FROM deleted WHERE uid = ? AND chat_id = ?", (uid, chat_id))
            for msg in messages:
                if not isinstance(msg, dict) or not msg.get("id"):
                    continue
                self._db.execute(
                    "INSERT OR REPLACE INTO messages(uid, chat_id, id, data, time) VALUES (?,?,?,?,?)",
                    (uid, chat_id, str(msg["id"]), self._dump(msg), float(msg.get("time") or now)))
                changed = True
            if changed:
                row = self._db.execute(
                    "SELECT data, updated FROM chats WHERE uid = ? AND id = ?", (uid, chat_id)).fetchone()
                sess = self._load(row["data"], {}) if row else {}
                sess.update({"id": sess.get("id") or chat_id,
                             "chatId": sess.get("chatId") or chat_id,
                             "title": sess.get("title") or title or "Новый чат",
                             "created": sess.get("created") or now,
                             "updated": max(float(sess.get("updated") or 0), now)})
                self._db.execute("INSERT OR REPLACE INTO chats(uid, id, data, updated) VALUES (?,?,?,?)",
                                 (uid, chat_id, self._dump(sess), sess["updated"]))
                return self._bump(uid)
        return self.revision(uid)

    def delete(self, uid: str, chat_id: str) -> int:
        with self._lock:
            self._db.execute("DELETE FROM chats WHERE uid = ? AND id = ?", (uid, chat_id))
            self._db.execute("DELETE FROM messages WHERE uid = ? AND chat_id = ?", (uid, chat_id))
            self._db.execute("INSERT OR REPLACE INTO deleted(uid, chat_id, at) VALUES (?,?,?)",
                             (uid, chat_id, time.time()))
            return self._bump(uid)

    def clear(self, uid: str) -> int:
        with self._lock:
            self._db.execute("DELETE FROM chats WHERE uid = ?", (uid,))
            self._db.execute("DELETE FROM messages WHERE uid = ?", (uid,))
            self._db.execute("DELETE FROM deleted WHERE uid = ?", (uid,))
            return self._bump(uid)

    def close(self) -> None:
        with self._lock:
            self._db.close()
