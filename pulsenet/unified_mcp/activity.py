# -*- coding: utf-8 -*-
"""Журнал активности: что и когда делал MCP-сервер для пользователя.

Другие агенты (opencode и т. п.) читают его через инструмент activity_recent и понимают,
что уже искали, что запомнили и что не удалось — без повторной работы.
Файл на пользователя: data/users/<uid>/activity.jsonl. Аргументы сохраняются только кратко
(первые символы), токены и полные тексты документов в журнал не попадают.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import List

from .auth import valid_user_id

MAX_BYTES = 512 * 1024      # больше — оставляем хвост
KEEP_LINES = 1000
SUMMARY_CHARS = 160


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


class ActivityLog:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._lock = threading.Lock()

    def _path(self, uid: str) -> str:
        if not valid_user_id(uid):
            raise ValueError("некорректный user_id")
        return os.path.join(self.data_dir, "users", uid, "activity.jsonl")

    def record(self, uid: str, tool: str, summary: str, ok: bool) -> None:
        path = self._path(uid)
        line = json.dumps({"t": _now(), "tool": tool, "ok": bool(ok),
                           "summary": " ".join(str(summary or "").split())[:SUMMARY_CHARS]},
                          ensure_ascii=False)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if os.path.getsize(path) > MAX_BYTES:
                with open(path, encoding="utf-8") as fh:
                    tail: List[str] = fh.readlines()[-KEEP_LINES:]
                with open(path, "w", encoding="utf-8") as fh:
                    fh.writelines(tail)

    def recent(self, uid: str, limit: int = 50) -> List[dict]:
        path = self._path(uid)
        with self._lock:
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
        out = []
        for raw in lines[-max(1, min(limit, 500)):]:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out[::-1]
