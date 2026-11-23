# -*- coding: utf-8 -*-
"""Серверное хранилище настроек интерфейса: один uid — один набор настроек.

localStorage остаётся быстрым кешем, а источник истины — этот SQLite: настройки
не теряются при перезапуске сервера и подтягиваются на других устройствах.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Dict, Tuple


class SettingsStore:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA busy_timeout=10000")
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    uid TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated REAL NOT NULL
                )
            """)
            self._db.commit()

    def get(self, uid: str) -> Dict:
        with self._lock:
            row = self._db.execute(
                "SELECT data, updated FROM user_settings WHERE uid = ?", (uid,)).fetchone()
        if not row:
            return {"settings": {}, "updated": 0}
        try:
            data = json.loads(row[0])
        except (TypeError, ValueError):
            data = {}
        return {"settings": data if isinstance(data, dict) else {}, "updated": float(row[1] or 0)}

    def put(self, uid: str, data: dict) -> Tuple[dict, float]:
        updated = time.time()
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._db.execute("""
                INSERT INTO user_settings(uid, data, updated) VALUES (?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET data = excluded.data, updated = excluded.updated
            """, (uid, raw, updated))
            self._db.commit()
        return data, updated
