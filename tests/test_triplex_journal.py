# -*- coding: utf-8 -*-
"""M0 / гейт G1 брифа ТРИПЛЕКСИЯ: инварианты I1–I7 журнала фактов."""
import random
from datetime import datetime, timedelta, timezone

import pytest

from pulsenet.triplex.journal import ConflictError, FactJournal, InvariantError

SRC = {"doc_id": "d:1", "span": [0, 10]}
T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-02-01T00:00:00+00:00"
T2 = "2026-03-01T00:00:00+00:00"
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def day(d):
    return (BASE + timedelta(days=d)).isoformat()


def test_i1_fact_requires_provenance():
    j = FactJournal()
    with pytest.raises(InvariantError):
        j.assert_fact("кот", "лежит_на", "коврик", source=None, valid_from=T0)
    with pytest.raises(InvariantError):
        j.assert_fact("кот", "лежит_на", "коврик", source={"doc_id": "d:1", "span": [5, 2]}, valid_from=T0)
    with pytest.raises(InvariantError):
        j.assert_fact("кот", "лежит_на", "коврик", source={"span": [0, 1]}, valid_from=T0)
    assert len(j) == 0


def test_i2_valid_interval_order():
    j = FactJournal()
    with pytest.raises(InvariantError):
        j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T1, valid_to=T0)


def test_i3_retract_keeps_record_but_hides_it():
    j = FactJournal()
    f = j.assert_fact("кот", "любит", "рыба", source=SRC, valid_from=T0)
    j.retract(f.fact_id, at=T1)
    assert len(j) == 1                                   # запись в журнале осталась
    assert j.facts[f.fact_id].retracted is True
    assert j.facts[f.fact_id].invalid_at == T1
    assert j.query(s="кот", as_of=T2) == []              # но не активна
    assert j.check_invariants() == []


def test_i4_supersede_closes_old_fact_and_chains():
    j = FactJournal()
    old = j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T0, functional=True)
    new = j.assert_fact("кот", "лежит_на", "диван", source=SRC, valid_from=T1, functional=True)
    assert new.supersedes == old.fact_id
    assert j.facts[old.fact_id].invalid_at == T1
    assert [f.o for f in j.query(s="кот", r="лежит_на", as_of=T2)] == ["диван"]
    assert [f.o for f in j.query(s="кот", r="лежит_на", as_of=T0)] == ["коврик"]
    assert j.check_invariants() == []


def test_i4_retroactive_insert_after_closed_chain_is_conflict_not_overwrite():
    """Регресс: ретроактивная вставка не должна перезаписывать invalid_at уже закрытого факта."""
    j = FactJournal()
    old = j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T0, functional=True)
    new1 = j.assert_fact("кот", "лежит_на", "диван", source=SRC, valid_from=T2, functional=True)
    assert j.facts[old.fact_id].invalid_at == T2
    j.retract(new1.fact_id, at=T2)
    with pytest.raises(ConflictError):
        j.assert_fact("кот", "лежит_на", "стол", source={"doc_id": "d:3", "span": [0, 2]},
                      valid_from=T1, functional=True)
    assert j.facts[old.fact_id].invalid_at == T2          # не перезаписан
    assert j.check_invariants() == []


def test_i5_functional_conflict_when_new_is_not_later():
    j = FactJournal()
    j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T1, functional=True)
    with pytest.raises(ConflictError):
        j.assert_fact("кот", "лежит_на", "диван", source={"doc_id": "d:2", "span": [0, 3]},
                      valid_from=T0, functional=True)
    assert j.check_invariants() == []


def test_nonfunctional_relation_allows_several_objects():
    j = FactJournal()
    j.assert_fact("кот", "видит", "мышь", source=SRC, valid_from=T0)
    j.assert_fact("кот", "видит", "птицу", source={"doc_id": "d:2", "span": [0, 4]}, valid_from=T0)
    assert len(j.query(s="кот", r="видит", as_of=T1)) == 2


def test_i7_repeated_assert_creates_no_duplicate():
    j = FactJournal()
    f1 = j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T0)
    f2 = j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T0)
    assert f1.fact_id == f2.fact_id
    assert len(j) == 1


def test_i6_replay_is_deterministic(tmp_path):
    p = tmp_path / "j.jsonl"
    j = FactJournal(str(p))
    for i in range(50):
        j.assert_fact(f"e{i}", "rel", f"o{i}", source={"doc_id": "d:x", "span": [i, i + 1]}, valid_from=T0)
    j.assert_fact("кот", "лежит_на", "коврик", source=SRC, valid_from=T0, functional=True)
    j.assert_fact("кот", "лежит_на", "диван", source=SRC, valid_from=T1, functional=True)
    h = j.state_hash()
    assert FactJournal(str(p)).state_hash() == h


def test_crash_torn_tail_is_dropped_and_file_repaired(tmp_path):
    p = tmp_path / "j.jsonl"
    j = FactJournal(str(p))
    j.assert_fact("кот", "любит", "рыба", source=SRC, valid_from=T0)
    with open(p, "a", encoding="utf-8") as f:           # обрыв записи при падении
        f.write('{"op":"assert","fa')
    j2 = FactJournal(str(p))
    assert len(j2) == 1
    j2.assert_fact("кот", "любит", "молоко", source={"doc_id": "d:2", "span": [0, 4]}, valid_from=T0)
    j3 = FactJournal(str(p))                             # хвост починен, новая запись не склеилась
    assert len(j3) == 2
    assert j3.check_invariants() == []


def test_corruption_in_middle_of_journal_raises(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text('{"op":"assert"BROKEN}\n{"op":"retract","fact_id":"x","at":"' + T0 + '"}\n', encoding="utf-8")
    with pytest.raises(InvariantError):
        FactJournal(str(p))


def test_invariants_hold_on_1000_random_facts(tmp_path):
    rng = random.Random(7)
    p = tmp_path / "big.jsonl"
    j = FactJournal(str(p))
    conflicts = 0
    for i in range(1000):
        s = f"кот{rng.randrange(40)}"
        o = f"место{rng.randrange(6)}"
        try:
            j.assert_fact(s, "лежит_на", o, source={"doc_id": f"d:{i}", "span": [0, 5]},
                          valid_from=day(rng.randrange(1000)), functional=True)
        except ConflictError:
            conflicts += 1
        if rng.random() < 0.05:
            live = [f for f in j.facts.values() if not f.retracted]
            if live:
                j.retract(rng.choice(live).fact_id, at=day(1000 + i))
    assert conflicts > 0                                 # конфликты действительно возникали
    assert len(j) + conflicts == 1000                    # каждая попытка либо записана, либо явно отклонена (G1)
    assert j.check_invariants() == []
    h = j.state_hash()
    reloaded = FactJournal(str(p))
    assert len(reloaded) == len(j)
    assert reloaded.state_hash() == h
    assert reloaded.check_invariants() == []


def test_journal_creates_missing_directory(tmp_path):
    """Регресс: журнал в ещё не созданной папке data/triplex/ должен создаваться сам."""
    p = tmp_path / "нет" / "папки" / "journal.jsonl"
    j = FactJournal(str(p))
    j.assert_fact("кот", "любит", "рыба", source=SRC, valid_from=T0)
    assert p.exists() and len(FactJournal(str(p))) == 1
