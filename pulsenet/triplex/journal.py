# -*- coding: utf-8 -*-
"""M0 · ТРИПЛЕКСИЯ: журнал фактов (скелет памяти).

Источник истины — append-only JSONL. Состояние — функция от журнала:
replay(journal) детерминирован (I6). Факт никогда не удаляется: retract и
supersede лишь выставляют поля завершения, запись остаётся для аудита (I3).

Инварианты брифа, проверяемые здесь:
  I1 каждый факт имеет source.doc_id и валидный span
  I2 valid_from <= valid_to и invalid_at >= valid_from
  I3 retract => invalid_at задан и retracted=True, факт остаётся в журнале
  I4 supersede => old.invalid_at == new.valid_from, цепочка ациклична
  I5 у functional (s,r) нет двух действующих фактов с разными o на перекрывающихся интервалах
  I6 состояние после replay детерминировано (state_hash)
  I7 повторный assert того же факта не создаёт дубликата
  I10 каждое обращение (access) увеличивает access_count и обновляет last_accessed
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple


class InvariantError(ValueError):
    """Нарушение инварианта I1–I4 или повреждённый журнал."""


class ConflictError(RuntimeError):
    """functional-отношение: новый факт конфликтует с действующим и не может его вытеснить."""

    def __init__(self, message: str, old_fact_id: str):
        super().__init__(message)
        self.old_fact_id = old_fact_id


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class Fact:
    fact_id: str
    s: str
    r: str
    o: str
    valid_from: str
    valid_to: Optional[str]
    recorded_at: str
    invalid_at: Optional[str]
    retracted: bool
    confidence: float
    source: dict
    supersedes: Optional[str]
    functional: bool
    access_count: int = 0          # I10: счётчик обращений (только top-8 при retrieve)
    last_accessed: Optional[str] = None
    salience: float = 0.8          # salience_0 для decay (A5), по умолчанию = confidence

    def end(self) -> Optional[datetime]:
        """Момент прекращения действия: min(valid_to, invalid_at); None — открыт."""
        ends = [_dt(x) for x in (self.valid_to, self.invalid_at) if x is not None]
        return min(ends) if ends else None


def _check_source(source) -> None:
    if not isinstance(source, dict) or not source.get("doc_id"):
        raise InvariantError("I1: факт без source.doc_id")
    span = source.get("span")
    if (not isinstance(span, (list, tuple)) or len(span) != 2
            or not all(isinstance(x, int) for x in span) or not 0 <= span[0] <= span[1]):
        raise InvariantError("I1: невалидный span")


def _fact_id(s, r, o, valid_from, source) -> str:
    key = json.dumps([s, r, o, valid_from, source["doc_id"], source["span"]], ensure_ascii=False)
    return "f:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _overlap(a: Fact, b: Fact) -> bool:
    start = max(_dt(a.valid_from), _dt(b.valid_from))
    ends = [e for e in (a.end(), b.end()) if e is not None]
    return not ends or start < min(ends)


class FactJournal:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.facts: Dict[str, Fact] = {}
        self._by_sr: Dict[Tuple[str, str], Set[str]] = {}
        if path and os.path.exists(path):
            self._replay_file()

    def __len__(self) -> int:
        return len(self.facts)

    # ---------- журнал ----------

    def _append(self, rec: dict) -> None:
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
        self._apply(rec)

    def _replay_file(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        lines = text.split("\n")
        tail = lines.pop()  # после последнего \n: пусто, либо обрыв записи при падении
        for n, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError as exc:
                raise InvariantError(f"журнал повреждён в строке {n}") from exc
            self._apply(rec)
        if tail.strip():
            # Обрезаем недописанный хвост, иначе следующая запись склеится с ним.
            good = "".join(line + "\n" for line in lines)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(good)

    def _apply(self, rec: dict) -> None:
        op = rec["op"]
        if op == "assert":
            self._insert(Fact(**rec["fact"]))
        elif op == "retract":
            f = self.facts[rec["fact_id"]]
            f.retracted = True
            if f.invalid_at is None:
                f.invalid_at = rec["at"]
        elif op == "adjust":                      # I9: confirm/deny меняют только confidence
            f = self.facts[rec["fact_id"]]
            f.confidence = rec["confidence"]
        elif op == "access":
            for fid in rec["fact_ids"]:
                f = self.facts.get(fid)
                if f is not None:
                    f.access_count += 1
                    f.last_accessed = rec["at"]
        elif op == "supersede":
            new = Fact(**rec["fact"])
            if new.fact_id in self.facts:  # I7: повтор после replay
                return
            self.facts[rec["old"]].invalid_at = new.valid_from  # I4
            self._insert(new)
        else:
            raise InvariantError(f"неизвестная операция журнала: {op!r}")

    def _insert(self, fact: Fact) -> None:
        self.facts[fact.fact_id] = fact
        self._by_sr.setdefault((fact.s, fact.r), set()).add(fact.fact_id)

    # ---------- операции ----------

    def conflict_check(self, s: str, r: str, o: str, valid_from: str, functional: bool):
        """A1: вернуть ('OK', None) | ('SUPERSEDE', old) | ('CONFLICT', old)."""
        if not functional:
            return "OK", None
        t = _dt(valid_from)
        hits = []
        for fid in self._by_sr.get((s, r), ()):
            old = self.facts[fid]
            if old.retracted or old.o == o:
                continue
            end = old.end()
            if end is not None and end <= t:
                continue
            hits.append(old)
        if not hits:
            return "OK", None
        # Вытеснять можно только ещё не закрытый факт: иначе перезапишем invalid_at,
        # который уже выставил предыдущий supersede (I4). Ретроактивная вставка — конфликт.
        if len(hits) == 1 and _dt(hits[0].valid_from) < t and hits[0].invalid_at is None:
            return "SUPERSEDE", hits[0]
        return "CONFLICT", hits[0]

    def assert_fact(self, s: str, r: str, o: str, *, source: dict,
                    valid_from: Optional[str] = None, valid_to: Optional[str] = None,
                    confidence: float = 0.8, functional: bool = False) -> Fact:
        _check_source(source)
        vf = valid_from or now_iso()
        if valid_to is not None and _dt(valid_to) < _dt(vf):
            raise InvariantError("I2: valid_to раньше valid_from")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence вне [0, 1]")
        fid = _fact_id(s, r, o, vf, source)
        if fid in self.facts:  # I7
            return self.facts[fid]
        status, old = self.conflict_check(s, r, o, vf, functional)
        if status == "CONFLICT":
            raise ConflictError(f"{s} {r}: уже действует {old.o!r}", old.fact_id)
        rec_fact = {
            "fact_id": fid, "s": s, "r": r, "o": o,
            "valid_from": vf, "valid_to": valid_to,
            "recorded_at": now_iso(), "invalid_at": None, "retracted": False,
            "confidence": confidence, "source": source,
            "supersedes": None, "functional": functional,
            "access_count": 0, "last_accessed": None, "salience": confidence,
        }
        if status == "SUPERSEDE":
            rec_fact["supersedes"] = old.fact_id
            self._append({"op": "supersede", "old": old.fact_id, "fact": rec_fact})
        else:
            self._append({"op": "assert", "fact": rec_fact})
        return self.facts[fid]

    def retract(self, fact_id: str, at: Optional[str] = None) -> Fact:
        f = self.facts[fact_id]  # KeyError, если факта нет
        if not f.retracted:
            self._append({"op": "retract", "fact_id": fact_id, "at": at or now_iso()})
        return f

    def adjust_confidence(self, fact_id: str, confidence: float) -> Fact:
        f = self.facts[fact_id]
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence вне [0, 1]")
        self._append({"op": "adjust", "fact_id": fact_id, "confidence": confidence})
        return f

    def supersede(self, old_id: str, s: str, r: str, o: str, *, source: dict, valid_from: str,
                  valid_to: Optional[str] = None, confidence: float = 0.8,
                  functional: bool = False) -> Fact:
        """Явное вытеснение: старый действующий факт закрывается на valid_from нового (I4)."""
        _check_source(source)
        old = self.facts.get(old_id)
        if old is None or old.retracted or (old.invalid_at is not None):
            raise InvariantError(f"I4: факт {old_id!r} не действует и не может быть вытеснен")
        if _dt(valid_from) <= _dt(old.valid_from):
            raise InvariantError("I4: новый факт должен начинаться позже старого")
        fid = _fact_id(s, r, o, valid_from, source)
        if fid in self.facts:
            return self.facts[fid]
        rec_fact = {
            "fact_id": fid, "s": s, "r": r, "o": o, "valid_from": valid_from, "valid_to": valid_to,
            "recorded_at": now_iso(), "invalid_at": None, "retracted": False,
            "confidence": confidence, "source": source, "supersedes": old_id, "functional": functional,
            "access_count": 0, "last_accessed": None, "salience": confidence,
        }
        self._append({"op": "supersede", "old": old_id, "fact": rec_fact})
        return self.facts[fid]

    def access(self, fact_ids, at: Optional[str] = None) -> None:
        """Учесть обращение к фактам (I10). Вызывать только для фактов, попавших в итоговый top-k."""
        ids = [i for i in fact_ids if i in self.facts]
        if ids:
            self._append({"op": "access", "fact_ids": ids, "at": at or now_iso()})

    def query(self, s=None, r=None, o=None, as_of: Optional[str] = None) -> List[Fact]:
        """Действующие факты на момент as_of (ось valid-time). Retract-нутые не возвращаются."""
        t = _dt(as_of or now_iso())
        if s is not None and r is not None:
            cands = [self.facts[i] for i in self._by_sr.get((s, r), ())]
        else:
            cands = list(self.facts.values())
        out = []
        for f in cands:
            if f.retracted or _dt(f.valid_from) > t:
                continue
            end = f.end()
            if end is not None and t >= end:
                continue
            if (s is not None and f.s != s) or (r is not None and f.r != r) or (o is not None and f.o != o):
                continue
            out.append(f)
        return sorted(out, key=lambda f: (f.s, f.r, f.o, f.fact_id))

    # ---------- проверки ----------

    def check_invariants(self) -> List[str]:
        bad: List[str] = []
        for f in self.facts.values():
            try:
                _check_source(f.source)
            except InvariantError as exc:
                bad.append(f"{f.fact_id}: {exc}")
            if f.valid_to is not None and _dt(f.valid_to) < _dt(f.valid_from):
                bad.append(f"{f.fact_id}: I2 valid_to < valid_from")
            if f.invalid_at is not None and _dt(f.invalid_at) < _dt(f.valid_from):
                bad.append(f"{f.fact_id}: I2 invalid_at < valid_from")
            if f.retracted and f.invalid_at is None:
                bad.append(f"{f.fact_id}: I3 retracted без invalid_at")
            if f.access_count > 0 and f.last_accessed is None:
                bad.append(f"{f.fact_id}: I10 access_count>0 без last_accessed")
            if f.supersedes is not None:
                old = self.facts.get(f.supersedes)
                if old is None or old.invalid_at is None or _dt(old.invalid_at) != _dt(f.valid_from):
                    bad.append(f"{f.fact_id}: I4 нарушено правило supersede")
                seen, cur = set(), f.fact_id
                while cur is not None:
                    if cur in seen:
                        bad.append(f"{f.fact_id}: I4 цикл в цепочке supersedes")
                        break
                    seen.add(cur)
                    node = self.facts.get(cur)
                    cur = node.supersedes if node else None
        for ids in self._by_sr.values():
            live = [self.facts[i] for i in ids if self.facts[i].functional and not self.facts[i].retracted]
            for i, a in enumerate(live):
                for b in live[i + 1:]:
                    if a.o != b.o and _overlap(a, b):
                        bad.append(f"I5: {a.fact_id} и {b.fact_id} спорят о ({a.s}, {a.r})")
        return bad

    def state_hash(self) -> str:
        """I6: хеш состояния. Одинаковый журнал → одинаковый хеш."""
        payload = json.dumps([asdict(self.facts[k]) for k in sorted(self.facts)],
                             ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
