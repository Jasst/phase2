# -*- coding: utf-8 -*-
"""TriplexMemory — ядро памяти ТРИПЛЕКСИИ: скелет (журнал фактов) + плоть (документы) + индексы.

Write path (бриф §5):  store → chunks → extraction → entity_link (A2) → relation_normalize (A3)
                       → conflict_check (A1, внутри журнала) → assert с provenance;
                       сбой экстракции → только документ (I8).
Read path (бриф §6):   Stage 1 coarse (LSH ∪ float, RRF) → Stage 2 expand (BFS ≤ depth, пути с decay)
                       → Stage 3 rerank → бюджет контекста; пусто → RAG по чанкам (I8).
Decay/archive (§7):    salience(t), архив при salience < 0.1 для отношений с конечным τ.

Отклонения от брифа (явные):
  * индекс точный (flat), HNSW не реализован — эталон для измерения recall;
  * rerank — лексико-косинусная комбинация, не cross-encoder (нет модели);
  * consolidate (LoRA «Сон») здесь не реализован — нужен стек обучения и GGUF-конвертация.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .docstore import DocStore
from .encoder import HashEncoder
from .index import LSHIndex, VectorIndex, rrf
from .journal import ConflictError, Fact, FactJournal

LINK_HI = 0.85       # A2: cos ≥ 0.85 → link
LINK_LO = 0.70       # A2: 0.70 ≤ cos < 0.85 → link с conf×0.7
REL_COS = 0.80       # A3: cos ≥ 0.80 → map
CONF_PRIOR = 0.8     # калибровка: conf = llm_report × 0.8
ARCHIVE_THETA = 0.1  # decay: архив при salience < 0.1
DAY = 86400.0

_TOKEN = re.compile(r"\w+", re.UNICODE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _tokens(text: str) -> List[str]:
    """Токены для лексического сравнения. Слова обрезаются до 5 символов (грубый стемминг:
    «проект» и «проекте» совпадают, «использует» и «использовать» тоже), короткие служебные
    слова (в, на, и) отбрасываются. Числа и версии (3.12 → 3, 12) не трогаются."""
    out: List[str] = []
    for tok in _TOKEN.findall(text.lower()):
        if tok.isalpha():
            if len(tok) <= 2:
                continue
            out.append(tok[:5])
        else:
            out.append(tok)
    return out


def _entity_id(canonical: str, etype: str) -> str:
    return "e:" + hashlib.sha1(f"{canonical.lower()}|{etype}".encode("utf-8")).hexdigest()[:12]


class TriplexMemory:
    def __init__(self, root: str, encoder=None,
                 extractor: Optional[Callable[[str], Dict[str, List[dict]]]] = None,
                 chunk_chars: int = 400, lsh_bits: int = 512, min_cos: float = 0.35):
        self.root = root
        # Порог релевантности: без него в промпт попадает память на любой запрос («привет», «погода»).
        # Калибруется под эмбеддер: для хеш-эмбеддера ≈0.35, для bge-m3 подберите на своих данных.
        self.min_cos = min_cos
        os.makedirs(root, exist_ok=True)
        self.encoder = encoder or HashEncoder()
        self.extractor = extractor
        self.dim = self.encoder.dim
        self._lock = threading.RLock()

        self.docs = DocStore(os.path.join(root, "docs.jsonl"), chunk_chars=chunk_chars)
        self.journal = FactJournal(os.path.join(root, "journal.jsonl"))
        self._meta_path = os.path.join(root, "meta.jsonl")

        self.entities: Dict[str, dict] = {}       # entity_id -> {canonical, type, aliases}
        self._alias_to_entity: Dict[str, str] = {}
        self.relations: Dict[str, dict] = {}      # canonical -> {aliases, functional, tau_days, proposed}
        self._rel_alias: Dict[str, str] = {}

        self.chunk_index = VectorIndex(self.dim)
        self.chunk_lsh = LSHIndex(self.dim, bits=lsh_bits)
        self.fact_index = VectorIndex(self.dim)
        self.fact_lsh = LSHIndex(self.dim, bits=lsh_bits)
        self.entity_index = VectorIndex(self.dim)
        self.rel_index = VectorIndex(self.dim)

        self._load_meta()
        self._rebuild_indexes()

    # ---------- метаданные: сущности и отношения ----------

    def _load_meta(self) -> None:
        if not os.path.exists(self._meta_path):
            return
        with open(self._meta_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                self._apply_meta(rec)

    def _apply_meta(self, rec: dict) -> None:
        if rec["op"] == "entity":
            self.entities[rec["entity_id"]] = {"canonical": rec["canonical"], "type": rec["type"],
                                               "aliases": list(rec.get("aliases", []))}
            for a in [rec["canonical"]] + rec.get("aliases", []):
                self._alias_to_entity.setdefault(a.lower(), rec["entity_id"])
        elif rec["op"] == "alias":                 # merge_alias: новое написание той же сущности
            ent = self.entities[rec["entity_id"]]
            if rec["alias"] not in ent["aliases"]:
                ent["aliases"].append(rec["alias"])
            self._alias_to_entity.setdefault(rec["alias"].lower(), rec["entity_id"])
        elif rec["op"] == "relation":
            canon = rec["canonical"]
            self.relations[canon] = {"aliases": list(rec.get("aliases", [])),
                                     "functional": bool(rec.get("functional", False)),
                                     "tau_days": rec.get("tau_days"),
                                     "proposed": bool(rec.get("proposed", False))}
            for a in [canon] + self.relations[canon]["aliases"]:
                self._rel_alias[a.lower()] = canon
        else:
            raise ValueError(f"неизвестная meta-операция {rec['op']!r}")

    def _write_meta(self, rec: dict) -> None:
        with open(self._meta_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._apply_meta(rec)

    def _rebuild_indexes(self) -> None:
        chunks = list(self.docs.all_chunks())
        if chunks:
            vecs = self.encoder.embed([c.text for c in chunks])
            for c, v in zip(chunks, vecs):
                self.chunk_index.add(c.chunk_id, v)
                self.chunk_lsh.add(c.chunk_id, v)
        if self.entities:
            names = list(self.entities)
            vecs = self.encoder.embed([self.entities[e]["canonical"] for e in names])
            for e, v in zip(names, vecs):
                self.entity_index.add(e, v)
        if self.relations:
            names = list(self.relations)
            vecs = self.encoder.embed(names)
            for r, v in zip(names, vecs):
                self.rel_index.add(r, v)
        facts = list(self.journal.facts.values())
        if facts:
            vecs = self.encoder.embed([self._fact_text(f) for f in facts])
            for f, v in zip(facts, vecs):
                self.fact_index.add(f.fact_id, v)
                self.fact_lsh.add(f.fact_id, v)

    # ---------- онтология ----------

    def define_relation(self, canonical: str, aliases=(), functional: bool = False,
                        tau_days: Optional[float] = None) -> dict:
        """Явно описать отношение: синонимы, cardinality (functional) и τ decay."""
        if not canonical.strip():
            raise ValueError("пустое отношение")
        if tau_days is not None and tau_days <= 0:
            raise ValueError("tau_days должен быть положительным или None")
        with self._lock:
            rec = {"op": "relation", "canonical": canonical, "aliases": list(aliases),
                   "functional": bool(functional), "tau_days": tau_days, "proposed": False}
            self._write_meta(rec)
            if canonical not in self.rel_index.pos:
                self.rel_index.add(canonical, self.encoder.embed([canonical])[0])
            return dict(self.relations[canonical], canonical=canonical)

    def normalize_relation(self, r: str) -> Tuple[str, str]:
        """A3: alias → онтология; иначе cos ≥ 0.8 → map (max-cos); иначе propose (review)."""
        with self._lock:
            key = r.strip().lower()
            if key in self._rel_alias:
                return self._rel_alias[key], "known"
            if self.rel_index.keys:
                vec = self.encoder.embed([r.strip()])[0]
                top = self.rel_index.topk(vec, 1)
                if top and top[0][1] >= REL_COS:
                    return top[0][0], "mapped"
            canon = r.strip()
            self._write_meta({"op": "relation", "canonical": canon, "aliases": [],
                              "functional": False, "tau_days": None, "proposed": True})
            self.rel_index.add(canon, self.encoder.embed([canon])[0])
            return canon, "proposed"

    # ---------- сущности: A2 ----------

    @staticmethod
    def _type_compat(a: Optional[str], b: Optional[str]) -> bool:
        return a is None or b is None or a == b or "other" in (a, b)

    def link_entity(self, name: str, etype: Optional[str] = None) -> Tuple[str, Optional[str], float, list]:
        """Вернуть (action, entity_id, conf_factor, candidates).

        action: 'link' | 'create' | 'ask'. 'ask' — ≥2 сильных кандидата (tie-break брифа).
        Совместимость типов: сущности разных типов не сливаются (закрывает TBD #2).
        """
        with self._lock:
            key = name.strip().lower()
            eid = self._alias_to_entity.get(key)
            if eid and self._type_compat(etype, self.entities[eid]["type"]):
                return "link", eid, 1.0, []
            if not self.entity_index.keys:
                return "create", None, 1.0, []
            vec = self.encoder.embed([name.strip()])[0]
            cands = []
            for e, cos in self.entity_index.topk(vec, 8):
                if self._type_compat(etype, self.entities[e]["type"]):
                    cands.append((e, cos))
            strong = [c for c in cands if c[1] >= LINK_HI]
            if len(strong) >= 2:
                return "ask", None, 1.0, [{"entity_id": e, "canonical": self.entities[e]["canonical"],
                                           "cos": round(c, 3)} for e, c in strong]
            if len(strong) == 1:
                return "link", strong[0][0], 1.0, []
            mid = [c for c in cands if LINK_LO <= c[1] < LINK_HI]
            if mid:
                return "link", mid[0][0], 0.7, []
            return "create", None, 1.0, []

    def _create_entity(self, name: str, etype: str) -> str:
        eid = _entity_id(name, etype)
        if eid not in self.entities:
            self._write_meta({"op": "entity", "entity_id": eid, "canonical": name,
                              "type": etype, "aliases": []})
            self.entity_index.add(eid, self.encoder.embed([name])[0])
        return eid

    def merge_alias(self, entity_id: str, alias: str) -> dict:
        """Привязать написание к существующей сущности (закрывает склонения вручную: «кота» → «кот»)."""
        with self._lock:
            if entity_id not in self.entities:
                raise KeyError(entity_id)
            key = alias.strip().lower()
            owner = self._alias_to_entity.get(key)
            if owner not in (None, entity_id):
                raise ValueError(f"алиас {alias!r} уже принадлежит другой сущности")
            self._write_meta({"op": "alias", "entity_id": entity_id, "alias": alias.strip()})
            return {"entity_id": entity_id, "canonical": self.entities[entity_id]["canonical"],
                    "aliases": list(self.entities[entity_id]["aliases"])}

    def confirm(self, fact_id: str, yes: bool) -> dict:
        """I9: подтверждение поднимает confidence на 0.15, но не выше 0.95; отказ только снижает (×0.5)."""
        with self._lock:
            f = self.journal.facts[fact_id]
            if yes:
                new = min(0.95, max(f.confidence, min(1.0, f.confidence + 0.15)))
            else:
                new = f.confidence * 0.5
            self.journal.adjust_confidence(fact_id, round(new, 4))
            return {"fact_id": fact_id, "confidence": round(new, 4), "action": "confirm" if yes else "deny"}

    def supersede(self, old_fact_id: str, s_name: str, r: str, o_name: str, valid_from: str,
                  source: dict, confidence: float = 0.8, functional: bool = False) -> dict:
        with self._lock:
            s_id = self._create_entity(s_name, "other")
            o_id = self._create_entity(o_name, "other")
            rel, _ = self.normalize_relation(r)
            fact = self.journal.supersede(old_fact_id, s_id, rel, o_id, source=source, valid_from=valid_from,
                                          confidence=confidence, functional=functional)
            if fact.fact_id not in self.fact_index.pos:
                v = self.encoder.embed([self._fact_text(fact)])[0]
                self.fact_index.add(fact.fact_id, v)
                self.fact_lsh.add(fact.fact_id, v)
            return {"status": "ok", "fact_id": fact.fact_id, "supersedes": old_fact_id,
                    "text": self._fact_text(fact)}

    def _ename(self, eid: str) -> str:
        return self.entities[eid]["canonical"] if eid in self.entities else eid

    def _fact_text(self, f: Fact) -> str:
        return f"{self._ename(f.s)} {f.r} {self._ename(f.o)}"

    # ---------- write path ----------

    def ingest(self, text: str, session_id: str = "", role: str = "user",
               recorded_at: Optional[str] = None, agent: str = "") -> dict:
        with self._lock:
            doc = self.docs.add(text, session_id=session_id, role=role, recorded_at=recorded_at, agent=agent)
            if doc.chunks and not all(c.chunk_id in self.chunk_index.pos for c in doc.chunks):
                vecs = self.encoder.embed([c.text for c in doc.chunks])
                for c, v in zip(doc.chunks, vecs):
                    self.chunk_index.add(c.chunk_id, v)
                    self.chunk_lsh.add(c.chunk_id, v)
            out = {"doc_id": doc.doc_id, "status": "ok", "facts": [],
                   "needs_confirmation": [], "relations_proposed": []}
            if self.extractor is None:
                out["status"] = "no_extractor"
                return out
            try:
                extracted = self.extractor(doc.text)
            except Exception as exc:                          # I8: документ сохранён, фактов нет
                out["status"] = "extraction_failed"
                out["error"] = str(exc)
                return out

            types = {e["name"].lower(): e["type"] for e in extracted.get("entities", [])}
            valid_from = doc.recorded_at
            for f in extracted.get("facts", []):
                a = doc.text.find(f["evidence"])
                if a < 0:                                     # защита повторно: span из текста
                    continue
                span = [a, a + len(f["evidence"])]
                s_act, s_id, s_cf, s_c = self.link_entity(f["s"], types.get(f["s"].lower()))
                o_act, o_id, o_cf, o_c = self.link_entity(f["o"], types.get(f["o"].lower()))
                if "ask" in (s_act, o_act):
                    out["needs_confirmation"].append({"fact": f, "candidates": s_c or o_c})
                    continue
                s_id = s_id or self._create_entity(f["s"], types.get(f["s"].lower(), "other"))
                o_id = o_id or self._create_entity(f["o"], types.get(f["o"].lower(), "other"))
                rel, how = self.normalize_relation(f["r"])
                if how == "proposed":
                    out["relations_proposed"].append(rel)
                functional = self.relations[rel]["functional"]
                conf = f["conf"] * CONF_PRIOR * min(s_cf, o_cf)
                try:
                    fact = self.journal.assert_fact(s_id, rel, o_id, source={"doc_id": doc.doc_id, "span": span},
                                                    valid_from=valid_from, confidence=conf,
                                                    functional=functional)
                except ConflictError as exc:
                    out["facts"].append({"status": "conflict", "message": str(exc),
                                         "old_fact_id": exc.old_fact_id})
                    continue
                if fact.fact_id not in self.fact_index.pos:
                    v = self.encoder.embed([self._fact_text(fact)])[0]
                    self.fact_index.add(fact.fact_id, v)
                    self.fact_lsh.add(fact.fact_id, v)
                out["facts"].append({"status": "ok", "fact_id": fact.fact_id,
                                     "text": self._fact_text(fact), "confidence": round(conf, 4)})
            return out

    # ---------- decay / archive (A5) ----------

    def _tau(self, f: Fact) -> Optional[float]:
        rel = self.relations.get(f.r)
        return rel["tau_days"] if rel else None

    def decay_factor(self, f: Fact, t: datetime) -> float:
        tau = self._tau(f)
        if tau is None:
            return 1.0
        dt = max(0.0, (t - _dt(f.valid_from)).total_seconds() / DAY)
        return math.exp(-dt / tau)

    def salience(self, f: Fact, t: datetime) -> float:
        """salience(t) = s0·e^{−Δt/τ} + 0.1·min(ln(1+access), 10). Δt — от последнего обращения или valid_from."""
        tau = self._tau(f)
        ref = _dt(f.last_accessed or f.valid_from)
        ref = max(ref, _dt(f.valid_from))
        if tau is None:
            base = f.salience
        else:
            base = f.salience * math.exp(-max(0.0, (t - ref).total_seconds() / DAY) / tau)
        return base + 0.1 * min(math.log(1 + f.access_count), 10.0)

    def is_archived(self, f: Fact, t: datetime) -> bool:
        return self._tau(f) is not None and self.salience(f, t) < ARCHIVE_THETA

    # ---------- read path ----------

    def recall(self, query: str, as_of: Optional[str] = None, k_facts: int = 8, k_chunks: int = 4,
               coarse: int = 24, depth: int = 2, include_archived: bool = False,
               budget_facts_chars: int = 3200, budget_chunks_chars: int = 1600) -> dict:
        with self._lock:
            as_of = as_of or _now()
            t = _dt(as_of)
            qv = self.encoder.embed_query([query])[0]     # запрос: у nomic/e5 свой префикс
            live = self.journal.query(as_of=as_of)
            if not include_archived:
                live = [f for f in live if not self.is_archived(f, t)]
            by_id = {f.fact_id: f for f in live}
            allowed = set(by_id)

            # Stage 1: coarse
            seeds: List[str] = []
            if allowed:
                fl = self.fact_lsh.topk(qv, 64, allowed)
                ff = [k for k, _ in self.fact_index.topk(qv, 64, allowed)]
                seeds = [k for k, _ in rrf([fl, ff])][:coarse]
                # порог релевантности для стартовых фактов; граф расширяем только от релевантных
                qtok = set(_tokens(query))
                seeds = [fid for fid in seeds
                         if self._relevant(self._fact_cos(fid, qv),
                                           set(_tokens(self._fact_text(by_id[fid]))), qtok)]
            chunk_rank = [k for k, _ in self.chunk_index.topk(qv, coarse)]

            # Stage 2: expand (BFS по сущностям, путь = Π conf·decay)
            adj: Dict[str, List[Fact]] = {}
            for f in live:
                adj.setdefault(f.s, []).append(f)
                adj.setdefault(f.o, []).append(f)
            path_w: Dict[str, float] = {}
            q: deque = deque()
            seen_ent = set()
            for fid in seeds:
                f = by_id[fid]
                for e in (f.s, f.o):
                    if e not in seen_ent:
                        seen_ent.add(e)
                        q.append((e, 1.0, 0))
            while q:
                ent, w, d = q.popleft()
                if d >= depth:
                    continue
                for f in adj.get(ent, []):
                    other = f.o if f.s == ent else f.s
                    if other == ent:
                        continue
                    pw = w * f.confidence * self.decay_factor(f, t)
                    if pw > path_w.get(f.fact_id, 0.0):
                        path_w[f.fact_id] = pw
                    if other not in seen_ent:
                        seen_ent.add(other)
                        q.append((other, pw, d + 1))
            candidates = list(dict.fromkeys(seeds + [fid for fid in path_w if fid not in seeds]))

            # Stage 3: rerank (косинус + лексическое перекрытие; путь умножает expanded)
            qtok = set(_tokens(query))
            scored = []
            for fid in candidates:
                f = by_id[fid]
                cos = float(self.fact_index.mat[self.fact_index.pos[fid]] @ qv) if fid in self.fact_index.pos else 0.0
                ftok = set(_tokens(self._fact_text(f)))
                lex = len(qtok & ftok) / max(1, len(qtok))
                base = 0.7 * cos + 0.3 * lex
                if fid not in seeds:
                    base *= path_w.get(fid, 0.0)
                scored.append((base, fid))
            scored.sort(key=lambda x: (-x[0], x[1]))
            top_facts = [fid for _, fid in scored[:k_facts]]

            # чанки: общий coarse-список + чанки-источники верхних фактов
            chunk_ids = list(dict.fromkeys(chunk_rank))
            for fid in top_facts:
                f = by_id[fid]
                c = self.docs.chunk_containing(f.source["doc_id"], tuple(f.source["span"]))
                if c is not None and c.chunk_id not in chunk_ids:
                    chunk_ids.append(c.chunk_id)
            chunk_scores = []
            qtok_all = set(_tokens(query))
            chunk_by_id = {c.chunk_id: c for doc in self.docs.docs.values() for c in doc.chunks}
            for cid in chunk_ids:
                if cid in self.chunk_index.pos:
                    cs = float(self.chunk_index.mat[self.chunk_index.pos[cid]] @ qv)
                    ctoks = set(_tokens(chunk_by_id[cid].text)) if cid in chunk_by_id else set()
                    if self._relevant(cs, ctoks, qtok_all):   # фрагменты тоже только по порогу
                        chunk_scores.append((cs, cid))
            chunk_scores.sort(key=lambda x: (-x[0], x[1]))
            top_chunks = [cid for _, cid in chunk_scores[:k_chunks]]

            if not top_facts:                                  # I8: нет фактов → чистый RAG
                status = "rag_fallback"
                top_facts = []
            else:
                status = "ok"
                self.journal.access(top_facts, at=as_of)       # I10: только top-k

            ctx_lines, used = [], 0
            for fid in top_facts:
                f = by_id[fid]
                line = f"- {self._fact_text(f)} (уверенность {f.confidence:.2f}, источник {f.source['doc_id']})"
                if used + len(line) > budget_facts_chars:
                    break
                ctx_lines.append(line)
                used += len(line)
            used = 0
            for cid in top_chunks:
                line = f"> {chunk_by_id[cid].text}"
                if used + len(line) > budget_chunks_chars:
                    break
                ctx_lines.append(line)
                used += len(line)

            return {
                "status": status,
                "as_of": as_of,
                "facts": [{"fact_id": fid, "text": self._fact_text(by_id[fid]),
                           "score": round(next(s for s, x in scored if x == fid), 4),
                           "confidence": by_id[fid].confidence} for fid in top_facts],
                "chunks": [{"chunk_id": cid, "text": chunk_by_id[cid].text} for cid in top_chunks],
                "context": "\n".join(ctx_lines),
            }

    def _relevant(self, cos: float, text_tokens: set, query_tokens: set) -> bool:
        """Релевантность: семантически близко (cos ≥ порога) ИЛИ половина слов запроса есть в тексте."""
        if cos >= self.min_cos:
            return True
        return bool(query_tokens) and len(query_tokens & text_tokens) / len(query_tokens) >= 0.5

    def _fact_cos(self, fid: str, qv) -> float:
        pos = self.fact_index.pos.get(fid)
        return float(self.fact_index.mat[pos] @ qv) if pos is not None else 0.0

    def retract(self, fact_id: str, at: Optional[str] = None) -> Fact:
        with self._lock:
            return self.journal.retract(fact_id, at=at)     # KeyError для неизвестного факта

    def agent_of(self, f: Fact) -> str:
        """Агент-автор факта: из source.agent, иначе из документа-источника."""
        if f.source.get("agent"):
            return str(f.source["agent"])
        doc = self.docs.docs.get(f.source.get("doc_id"))
        return doc.agent if doc is not None else ""

    def list_facts(self, limit: int = 50) -> List[dict]:
        """Действующие факты с провенансом — для списка в интерфейсе и для других клиентов."""
        with self._lock:
            out = []
            for f in self.journal.query()[:max(1, min(int(limit), 500))]:
                out.append({"fact_id": f.fact_id, "text": self._fact_text(f),
                            "confidence": round(f.confidence, 3), "valid_from": f.valid_from,
                            "source": f.source, "access_count": f.access_count, "agent": self.agent_of(f)})
            return out

    def stats(self) -> dict:
        with self._lock:
            return {
                "docs": len(self.docs.docs),
                "chunks": len(self.chunk_index),
                "entities": len(self.entities),
                "relations": len(self.relations),
                "relations_proposed": sum(1 for r in self.relations.values() if r["proposed"]),
                "facts_records": len(self.journal.facts),
                "facts_active": len(self.journal.query()),
                "encoder": self.encoder.name,
                "extractor": self.extractor is not None,
            }

    def graph(self, max_edges: int = 400) -> dict:
        """Срез памяти для визуализации: сущности — узлы («нейроны»), действующие факты — связи.

        Узел появляется, когда на него ссылается хотя бы один действующий факт; created — момент
        первого факта. growth — накопительное число фактов по дням (как память росла).
        Архивные (затухшие) факты в срез не входят. Если фактов больше max_edges — берутся свежие.
        """
        with self._lock:
            now = _now()
            t = _dt(now)
            active = self.journal.query(as_of=now)
            alive = [f for f in active if not self.is_archived(f, t)]
            alive.sort(key=lambda f: f.recorded_at, reverse=True)
            shown = alive[:max_edges]

            nodes: Dict[str, dict] = {}

            def node(key: str) -> dict:
                if key not in nodes:
                    ent = self.entities.get(key)
                    nodes[key] = {"id": key, "label": ent["canonical"] if ent else key,
                                  "type": ent["type"] if ent else None, "degree": 0,
                                  "created": None, "linked": ent is not None}
                return nodes[key]

            edges = []
            for f in shown:
                a, b = node(f.s), node(f.o)
                for n in (a, b):
                    n["degree"] += 1
                    if n["created"] is None or f.recorded_at < n["created"]:
                        n["created"] = f.recorded_at
                edges.append({"id": f.fact_id, "source": f.s, "target": f.o, "label": f.r,
                              "confidence": round(f.confidence, 3), "recorded_at": f.recorded_at,
                              "access_count": f.access_count})

            per_day: Dict[str, int] = {}
            for f in active:
                day = f.recorded_at[:10]
                per_day[day] = per_day.get(day, 0) + 1
            total = 0
            growth = []
            for day in sorted(per_day):
                total += per_day[day]
                growth.append({"day": day, "facts": total})

            return {
                "as_of": now,
                "nodes": list(nodes.values()),
                "edges": edges,
                "truncated": len(alive) > len(shown),
                "totals": {"entities": len(self.entities), "facts": len(active),
                           "alive": len(alive), "relations": len(self.relations)},
                "growth": growth,
            }
