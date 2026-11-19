# -*- coding: utf-8 -*-
"""Эмбеддеры для ТРИПЛЕКСИИ.

HashEncoder    — детерминированный офлайн-эмбеддер без весов. Не понимает синонимы и склонения.
OpenAIEmbedder — /v1/embeddings (llama-server --embedding, LM Studio). Батчи, повтор при сбое сети.
               Поддерживает префиксы задач: nomic — «search_document: / search_query: »,
               e5 — «passage: / query: ». Без префиксов такие модели работают заметно хуже.
CachedEncoder  — дисковый кэш векторов. Ключ = вид (документ/запрос) + текст, тег модели.

Документы (фрагменты, факты, сущности) кодируются через embed(), запросы поиска — через embed_query().
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zlib
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

_WORD = re.compile(r"\w+", re.UNICODE)


def default_prefixes(model: str) -> Tuple[str, str]:
    """Префиксы задач по имени модели (документ, запрос). Переменные окружения могут переопределить."""
    m = (model or "").lower()
    if "nomic" in m:
        return "search_document: ", "search_query: "
    if "e5" in m:
        return "passage: ", "query: "
    return "", ""


class HashEncoder:
    name = "hash"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _features(self, text: str) -> List[str]:
        feats: List[str] = []
        for w in _WORD.findall(text.lower()):
            feats.append("w:" + w)
            padded = f"<{w}>"
            feats.extend("c:" + padded[i:i + 3] for i in range(max(1, len(padded) - 2)))
        return feats

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        rows = []
        for text in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for f in self._features(text):
                h = zlib.crc32(f.encode("utf-8"))
                sign = 1.0 if (zlib.crc32(b"s" + f.encode("utf-8")) & 1) else -1.0
                v[h % self.dim] += sign
            n = float(np.linalg.norm(v))
            rows.append(v / n if n > 0 else v)
        if not rows:
            return np.zeros((0, self.dim), dtype=np.float32)
        return np.stack(rows)

    def embed_query(self, texts: Iterable[str]) -> np.ndarray:
        return self.embed(texts)


class OpenAIEmbedder:
    """Клиент /v1/embeddings. Тексты уходят батчами, сетевой сбой повторяется."""
    name = "openai"
    BATCH = 32

    def __init__(self, base_url: str, model: str, dim: int, timeout: float = 120.0, retries: int = 2,
                 doc_prefix: str = "", query_prefix: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout = timeout
        self.retries = retries
        self.doc_prefix = doc_prefix
        self.query_prefix = query_prefix

    def _post(self, items: List[str]) -> dict:
        body = json.dumps({"model": self.model, "input": items}).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/embeddings", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        last = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
        raise ConnectionError(f"эмбеддер недоступен ({self.base_url}): {last}")

    def _embed_raw(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        parts = []
        for i in range(0, len(texts), self.BATCH):
            chunk = texts[i:i + self.BATCH]
            data = self._post(chunk)
            vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
            arr = np.asarray(vecs, dtype=np.float32)
            if arr.shape != (len(chunk), self.dim):
                raise ValueError(f"эмбеддинги неожиданной формы {arr.shape}, ожидалось ({len(chunk)}, {self.dim})")
            parts.append(arr)
        arr = np.vstack(parts)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.maximum(norms, 1e-12)

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        return self._embed_raw([self.doc_prefix + t for t in texts])

    def embed_query(self, texts: Iterable[str]) -> np.ndarray:
        return self._embed_raw([self.query_prefix + t for t in texts])


class CachedEncoder:
    """Кэш векторов на диске. Ключ = вид (doc/query) + тег модели + текст.

    Тег включает имя модели, размерность, пулинг и префиксы: смена любого из них даёт новый кэш.
    """
    def __init__(self, inner, path: str, tag: str):
        self.inner = inner
        self.path = path
        self.tag = tag
        self.dim = inner.dim
        self.name = inner.name + "+cache"
        self._mem: Dict[str, np.ndarray] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        vec = np.frombuffer(base64.b64decode(rec["v"]), dtype=np.float32)
                        if vec.shape[0] == self.dim:
                            self._mem[rec["k"]] = vec.copy()
                    except (ValueError, KeyError):
                        continue          # битая строка кэша — просто пересчитаем

    def _key(self, kind: str, text: str) -> str:
        return hashlib.sha1(f"{self.tag}\0{kind}\0{text}".encode("utf-8")).hexdigest()

    def _cached(self, kind: str, texts: Iterable[str], fn) -> np.ndarray:
        items = list(texts)
        if not items:
            return np.zeros((0, self.dim), dtype=np.float32)
        keys = [self._key(kind, t) for t in items]
        with self._lock:
            missing = list(dict.fromkeys(t for t, k in zip(items, keys) if k not in self._mem))
            self.misses += len(missing)
            self.hits += len(items) - len(missing)
        if missing:
            vecs = fn(missing)
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                for text, vec in zip(missing, vecs):
                    k = self._key(kind, text)
                    v32 = vec.astype(np.float32)
                    self._mem[k] = v32
                    f.write(json.dumps({"k": k, "v": base64.b64encode(v32.tobytes()).decode("ascii")}) + "\n")
                f.flush()
                os.fsync(f.fileno())
        with self._lock:
            return np.stack([self._mem[k] for k in keys])

    def embed(self, texts: Iterable[str]) -> np.ndarray:
        return self._cached("doc", texts, self.inner.embed)

    def embed_query(self, texts: Iterable[str]) -> np.ndarray:
        return self._cached("query", texts, self.inner.embed_query)


# ---------- автоопределение: модель, размерность ----------

EMBED_HINTS = ("embed", "bge", "e5", "nomic", "gte", "minilm")


def list_model_ids(base_url: str, timeout: float = 10.0) -> List[str]:
    """Идентификаторы моделей сервера (/v1/models). LM Studio и llama-server отдают одинаково."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]


def pick_embedding_model(ids: Iterable[str]) -> Optional[str]:
    """Первая модель, в имени которой есть признак эмбеддера. Чат-модели пропускаются."""
    for mid in ids:
        low = mid.lower()
        if any(h in low for h in EMBED_HINTS) and "rerank" not in low:
            return mid
    return None


def probe_dim(base_url: str, model: str, timeout: float = 120.0) -> int:
    """Размерность вектора модели: один пробный запрос. Первый вызов может подгрузить модель в LM Studio."""
    data = OpenAIEmbedder(base_url, model, 0, timeout=timeout)._post(["ping"])
    return len(data["data"][0]["embedding"])
