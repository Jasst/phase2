# -*- coding: utf-8 -*-
"""Чтение кода — только для пользователей из CODE_ACCESS_USERS и только из корней CODE_ROOTS.
Формат CODE_ROOTS: name=/абсолютный/путь;name2=/путь2. Пути режутся по realpath: выход за корень
и симлинки наружу запрещены. Файлы с секретами (.env, ключи, токены) не отдаются."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

MAX_FILE_BYTES = 200_000
TEXT_EXT = {".py", ".js", ".ts", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
            ".html", ".css", ".sh", ".bat", ".ps1", ".sql", ".rs", ".go", ".java", ".c", ".h", ".cpp"}
DENY_NAME = re.compile(r"(^\.|\.env|secret|token|passw|credential|\.pem$|\.key$|id_rsa)", re.I)


def parse_roots(spec: str) -> Dict[str, str]:
    roots: Dict[str, str] = {}
    for part in (spec or "").split(";"):
        if "=" in part:
            name, path = part.split("=", 1)
            if name.strip() and path.strip():
                roots[name.strip()] = os.path.realpath(path.strip())
    return roots


class CodeAccess:
    def __init__(self, roots: Dict[str, str], allowed_users: set):
        self.roots = roots
        self.allowed_users = allowed_users

    def _check(self, uid: str) -> Optional[str]:
        if uid not in self.allowed_users:
            return "доступ к коду не разрешён для этого пользователя"
        return None

    def _resolve(self, root: str, rel: str) -> tuple:
        if root not in self.roots:
            return None, "неизвестный корень"
        base = self.roots[root]
        full = os.path.realpath(os.path.join(base, rel.lstrip("/\\")))
        if os.path.commonpath([base, full]) != base:
            return None, "путь выходит за пределы корня"
        if rel.strip("/\\.") and (DENY_NAME.search(os.path.basename(full)) or DENY_NAME.search(rel)):
            return None, "файл закрыт политикой доступа"
        return full, None

    def list(self, uid: str, root: str, subdir: str = "", limit: int = 200) -> Dict[str, Any]:
        if (err := self._check(uid)):
            return {"status": "error", "message": err}
        full, err = self._resolve(root, subdir or "")
        if err:
            return {"status": "error", "message": err}
        items = []
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            for f in filenames:
                if os.path.splitext(f)[1] in TEXT_EXT and not DENY_NAME.search(f):
                    items.append(os.path.relpath(os.path.join(dirpath, f), self.roots[root]).replace("\\", "/"))
                    if len(items) >= limit:
                        return {"status": "ok", "files": items, "truncated": True}
        return {"status": "ok", "files": sorted(items), "truncated": False}

    def read(self, uid: str, root: str, path: str, start: int = 1, end: Optional[int] = None) -> Dict[str, Any]:
        if (err := self._check(uid)):
            return {"status": "error", "message": err}
        full, err = self._resolve(root, path)
        if err:
            return {"status": "error", "message": err}
        if not os.path.isfile(full) or os.path.splitext(full)[1] not in TEXT_EXT:
            return {"status": "error", "message": "не текстовый файл из разрешённых"}
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return {"status": "error", "message": "файл больше лимита"}
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        s = max(1, int(start))
        e = min(len(lines), int(end) if end else s + 299)
        numbered = "\n".join(f"{i:5d}  {lines[i - 1]}" for i in range(s, e + 1))
        return {"status": "ok", "path": path, "from": s, "to": e, "total_lines": len(lines), "text": numbered}

    def search(self, uid: str, root: str, pattern: str, limit: int = 50) -> Dict[str, Any]:
        if (err := self._check(uid)):
            return {"status": "error", "message": err}
        if not pattern or len(pattern) < 2:
            return {"status": "error", "message": "запрос короче 2 символов"}
        listed = self.list(uid, root, "", limit=5000)
        if listed["status"] != "ok":
            return listed
        hits = []
        needle = pattern.lower()
        for rel in listed["files"]:
            full, err = self._resolve(root, rel)
            if err or os.path.getsize(full) > MAX_FILE_BYTES:
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if needle in line.lower():
                        hits.append({"path": rel, "line": n, "text": line.strip()[:200]})
                        if len(hits) >= limit:
                            return {"status": "ok", "hits": hits, "truncated": True}
        return {"status": "ok", "hits": hits, "truncated": False}
