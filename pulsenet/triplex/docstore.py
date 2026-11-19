# -*- coding: utf-8 -*-
"""DocStore — «плоть» памяти: сырые документы и их чанки. Append-only JSONL."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

_SENT_END = re.compile(r"(?<=[.!?])\s+|\n+")


def _pack(text: str, max_chars: int) -> List[Tuple[int, int]]:
    """Разбить текст на окна не длиннее max_chars по границам предложений, координаты — исходные."""
    bounds = []
    pos = 0
    for m in _SENT_END.finditer(text):
        if m.start() > pos:
            bounds.append((pos, m.start()))
        pos = m.end()
    if pos < len(text):
        bounds.append((pos, len(text)))
    # жёсткая нарезка длинных предложений
    pieces: List[Tuple[int, int]] = []
    for a, b in bounds:
        while b - a > max_chars:
            pieces.append((a, a + max_chars))
            a += max_chars
        if b > a:
            pieces.append((a, b))
    # склейка соседних кусков до max_chars
    out: List[Tuple[int, int]] = []
    for a, b in pieces:
        if out and b - out[-1][0] <= max_chars:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


@dataclass
class Chunk:
    chunk_id: str
    span: Tuple[int, int]
    text: str


@dataclass
class Doc:
    doc_id: str
    session_id: str
    role: str
    text: str
    recorded_at: str
    chunks: List[Chunk] = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class DocStore:
    def __init__(self, path: Optional[str] = None, chunk_chars: int = 400):
        self.path = path
        self.chunk_chars = chunk_chars
        self.docs: Dict[str, Doc] = {}
        self._lock = threading.RLock()
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            if os.path.exists(path):
                self._load()

    def _load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    self._insert(rec)

    def _insert(self, rec: dict) -> Doc:
        chunks = [Chunk(c["chunk_id"], tuple(c["span"]), c["text"]) for c in rec["chunks"]]
        doc = Doc(rec["doc_id"], rec["session_id"], rec["role"], rec["text"], rec["recorded_at"], chunks)
        self.docs[doc.doc_id] = doc
        return doc

    def add(self, text: str, session_id: str = "", role: str = "user",
            recorded_at: Optional[str] = None) -> Doc:
        if not text or not text.strip():
            raise ValueError("пустой документ")
        recorded_at = recorded_at or _now()
        key = json.dumps([recorded_at, session_id, role, text], ensure_ascii=False)
        doc_id = "d:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        with self._lock:
            if doc_id in self.docs:                      # повтор того же документа — не дубль
                return self.docs[doc_id]
            chunks = [Chunk(f"c:{doc_id[2:]}:{i}", (a, b), text[a:b])
                      for i, (a, b) in enumerate(_pack(text, self.chunk_chars))]
            rec = {"doc_id": doc_id, "session_id": session_id, "role": role, "text": text,
                   "recorded_at": recorded_at,
                   "chunks": [{"chunk_id": c.chunk_id, "span": list(c.span), "text": c.text} for c in chunks]}
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            return self._insert(rec)

    def chunk_containing(self, doc_id: str, span: Tuple[int, int]) -> Optional[Chunk]:
        doc = self.docs.get(doc_id)
        if doc is None:
            return None
        for c in doc.chunks:
            if c.span[0] <= span[0] and span[1] <= c.span[1]:
                return c
        return None

    def all_chunks(self):
        for doc in self.docs.values():
            for c in doc.chunks:
                yield c
