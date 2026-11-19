# -*- coding: utf-8 -*-
"""Ядро ТРИПЛЕКСИИ (M1–M4): документы, индексы, экстракция, A1–A5, retrieval, decay, перезапуск."""
import json

import pytest

from pulsenet.triplex import (ExtractionError, HashEncoder, TriplexMCP, TriplexMemory,
                              make_extractor, parse_extraction)
from pulsenet.triplex.index import LSHIndex, VectorIndex, rrf

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-02-01T00:00:00+00:00"
T2 = "2026-03-01T00:00:00+00:00"


def scripted(payload_by_text):
    """Экстрактор, который «отвечает» заранее заданным JSON для каждого текста (без модели)."""
    def llm(prompt):
        text = prompt.split("Текст:\n", 1)[1]
        return json.dumps(payload_by_text[text], ensure_ascii=False)
    return make_extractor(llm)


@pytest.fixture()
def mem(tmp_path):
    return TriplexMemory(str(tmp_path / "triplex"), encoder=HashEncoder(256))


# ---------- экстракция: валидация (M2) ----------

def test_evidence_must_be_literal_substring():
    text = "Кот лежит на коврике."
    good = json.dumps({"facts": [{"s": "кот", "r": "лежит_на", "o": "коврик",
                                  "evidence": "лежит на коврике", "conf": 0.9}]})
    bad = json.dumps({"facts": [{"s": "кот", "r": "любит", "o": "рыба",
                                 "evidence": "кот любит рыбу", "conf": 0.9}]})
    assert len(parse_extraction(good, text)["facts"]) == 1
    assert parse_extraction(bad, text)["facts"] == []        # выдумка отброшена (I1)


def test_fenced_json_and_clamped_confidence_are_accepted():
    text = "Маша любит кота."
    raw = '```json\n{"facts":[{"s":"Маша","r":"любит","o":"кота","evidence":"Маша любит кота","conf":7}]}\n```'
    f = parse_extraction(raw, text)["facts"][0]
    assert f["conf"] == 1.0


def test_garbage_raises_extraction_error_and_extractor_retries_once():
    calls = []

    def llm(prompt):
        calls.append(1)
        return "не json"
    with pytest.raises(ExtractionError):
        make_extractor(llm)("текст")
    assert len(calls) == 2


# ---------- write path ----------

def test_ingest_stores_fact_with_valid_provenance(mem):
    text = "Кот лежит на коврике. Погода хорошая."
    mem.extractor = scripted({text: {
        "entities": [{"name": "кот", "type": "animal"}, {"name": "коврик", "type": "object"}],
        "facts": [{"s": "кот", "r": "лежит_на", "o": "коврик",
                   "evidence": "Кот лежит на коврике", "conf": 0.9}]}})
    out = mem.ingest(text, recorded_at=T0)
    assert out["status"] == "ok"
    f = out["facts"][0]
    assert f["status"] == "ok" and f["confidence"] == pytest.approx(0.9 * 0.8)
    fact = mem.journal.facts[f["fact_id"]]
    assert text[fact.source["span"][0]:fact.source["span"][1]] == "Кот лежит на коврике"   # I1
    assert mem.journal.check_invariants() == []


def test_extraction_failure_keeps_document_and_falls_back_to_rag(mem):
    def boom(text):
        raise ExtractionError("модель вернула мусор")
    mem.extractor = boom
    out = mem.ingest("Кот лежит на коврике.", recorded_at=T0)
    assert out["status"] == "extraction_failed" and out["facts"] == []   # I8
    assert len(mem.docs.docs) == 1
    r = mem.recall("где кот")
    assert r["status"] == "rag_fallback" and r["facts"] == []
    assert r["chunks"] and "коврик" in r["chunks"][0]["text"]            # RAG отвечает непустым


def test_no_extractor_still_keeps_document(mem):
    out = mem.ingest("Просто текст.", recorded_at=T0)
    assert out["status"] == "no_extractor" and len(mem.docs.docs) == 1


def test_functional_relation_supersedes_across_documents(mem):
    mem.define_relation("лежит_на", functional=True)
    d1, d2 = "Кот лежит на коврике.", "Кот лежит на диване."
    mem.extractor = scripted({
        d1: {"facts": [{"s": "кот", "r": "лежит_на", "o": "коврик", "evidence": "лежит на коврике", "conf": 1}]},
        d2: {"facts": [{"s": "кот", "r": "лежит_на", "o": "диван", "evidence": "лежит на диване", "conf": 1}]},
    })
    mem.ingest(d1, recorded_at=T0)
    mem.ingest(d2, recorded_at=T1)
    now_objs = [f.o for f in mem.journal.query(r="лежит_на", as_of=T2)]
    assert len(now_objs) == 1
    assert mem.entities[now_objs[0]]["canonical"] == "диван"
    assert mem.journal.check_invariants() == []


def test_retroactive_functional_fact_is_conflict_not_silent_overwrite(mem):
    mem.define_relation("лежит_на", functional=True)
    d_late, d_early = "Кот лежит на диване.", "Кот лежит на коврике."
    mem.extractor = scripted({
        d_late: {"facts": [{"s": "кот", "r": "лежит_на", "o": "диван", "evidence": "лежит на диване", "conf": 1}]},
        d_early: {"facts": [{"s": "кот", "r": "лежит_на", "o": "коврик", "evidence": "лежит на коврике", "conf": 1}]},
    })
    mem.ingest(d_late, recorded_at=T1)
    out = mem.ingest(d_early, recorded_at=T0)
    assert out["facts"][0]["status"] == "conflict"
    assert mem.journal.check_invariants() == []


# ---------- A2: entity linking ----------

def test_entities_of_different_types_are_not_merged(mem):
    mem._create_entity("кот", "animal")
    action, eid, _, _ = mem.link_entity("кот", "object")
    assert action == "create"                      # TBD #2 из брифа: animal ≠ object


def test_exact_alias_links_same_type(mem):
    eid = mem._create_entity("кот", "animal")
    action, got, cf, _ = mem.link_entity("Кот", "animal")
    assert action == "link" and got == eid and cf == 1.0


def test_two_strong_candidates_ask_user(mem):
    a = mem._create_entity("кот рыжий", "animal")
    b = mem._create_entity("кот рыжий ", "animal")
    # подмена векторов: оба кандидата сильно близки к запросу
    v = HashEncoder(256).embed(["кот рыжий"])[0]
    mem.entity_index.add(a, v)
    mem.entity_index.add(b, v)
    action, _, _, cands = mem.link_entity("рыжий кот", "animal")
    assert action == "ask" and len(cands) == 2


# ---------- A3: relations ----------

def test_relation_alias_and_proposal(mem):
    mem.define_relation("лежит_на", aliases=["лежит на"], functional=True)
    assert mem.normalize_relation("лежит на") == ("лежит_на", "known")
    canon, how = mem.normalize_relation("ужасно любит")
    assert how == "proposed" and canon == "ужасно любит"
    assert mem.relations[canon]["proposed"] is True


# ---------- A5: decay / archive ----------

def test_old_fact_with_finite_tau_is_archived_and_hidden(mem):
    mem.define_relation("видит", tau_days=30)
    text = "Кот видит мышь."
    mem.extractor = scripted({text: {"facts": [{"s": "кот", "r": "видит", "o": "мышь",
                                                "evidence": "видит мышь", "conf": 1}]}})
    mem.ingest(text, recorded_at=T0)
    far = "2026-12-31T00:00:00+00:00"                   # ~360 дней спустя
    assert mem.recall("кот видит мышь", as_of=far)["facts"] == []
    assert len(mem.recall("кот видит мышь", as_of=far, include_archived=True)["facts"]) == 1
    # архив не удаляет запись (≠ delete)
    assert len(mem.journal.facts) == 1


def test_semantic_relation_never_decays(mem):
    mem.define_relation("родился_в", tau_days=None)
    text = "Анна родилась в Казани."
    mem.extractor = scripted({text: {"facts": [{"s": "Анна", "r": "родился_в", "o": "Казани",
                                                "evidence": "родилась в Казани", "conf": 1}]}})
    mem.ingest(text, recorded_at=T0)
    assert mem.recall("Анна", as_of="2030-01-01T00:00:00+00:00")["facts"]


# ---------- read path ----------

def test_multi_hop_reaches_fact_through_shared_entity(mem):
    doc_a = "Маша любит кота."
    doc_b = "Кот лежит на коврике."
    mem.extractor = scripted({
        doc_a: {"facts": [{"s": "Маша", "r": "любит", "o": "кот", "evidence": "Маша любит кота", "conf": 1}]},
        doc_b: {"facts": [{"s": "кот", "r": "лежит_на", "o": "коврик", "evidence": "Кот лежит на коврике", "conf": 1}]},
    })
    mem.ingest(doc_a, recorded_at=T0)
    mem.ingest(doc_b, recorded_at=T0)
    r = mem.recall("Маша", as_of=T1, depth=2)
    texts = [f["text"] for f in r["facts"]]
    assert any("Маша любит" in t for t in texts)
    assert any("лежит_на" in t for t in texts)      # факт про кота достигнут через сущность «кот»


def test_access_counter_updates_only_returned_facts(mem):
    text = "Кот лежит на коврике."
    mem.extractor = scripted({text: {"facts": [{"s": "кот", "r": "лежит_на", "o": "коврик",
                                                "evidence": "лежит на коврике", "conf": 1}]}})
    mem.ingest(text, recorded_at=T0)
    mem.recall("кот коврик", as_of=T1)
    fid = next(iter(mem.journal.facts))
    assert mem.journal.facts[fid].access_count == 1
    assert mem.journal.facts[fid].last_accessed is not None
    assert mem.journal.check_invariants() == []


def test_context_respects_character_budget(mem):
    body = "Кот лежит на коврике. " * 40
    mem.extractor = scripted({body: {"facts": []}})
    mem.ingest(body, recorded_at=T0)
    r = mem.recall("кот", budget_chunks_chars=120)
    assert len(r["context"]) <= 120


def test_restart_rebuilds_indexes_and_gives_same_result(tmp_path):
    root = str(tmp_path / "t")
    text = "Кот лежит на коврике."
    m1 = TriplexMemory(root, encoder=HashEncoder(256), extractor=scripted({text: {"facts": [
        {"s": "кот", "r": "лежит_на", "o": "коврик", "evidence": "лежит на коврике", "conf": 1}]}}))
    m1.ingest(text, recorded_at=T0)
    m2 = TriplexMemory(root, encoder=HashEncoder(256))
    # replay даёт тот же состояние-хеш (I6); чтение меняет состояние по дизайну (I10), поэтому сравниваем до него
    assert m2.journal.state_hash() == m1.journal.state_hash()
    before = [f["text"] for f in m1.recall("кот", as_of=T1)["facts"]]
    after = [f["text"] for f in m2.recall("кот", as_of=T1)["facts"]]
    assert before == after and before


def test_mcp_ingest_and_search_end_to_end(tmp_path):
    mem = TriplexMemory(str(tmp_path / "t"), encoder=HashEncoder(256))
    text = "Кот лежит на коврике."
    mem.extractor = scripted({text: {"facts": [{"s": "кот", "r": "лежит_на", "o": "коврик",
                                                "evidence": "лежит на коврике", "conf": 1}]}})
    mcp = TriplexMCP(mem)

    def call(name, **args):
        return mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": name, "arguments": args}})["result"]

    ing = call("triplex_ingest", text=text, recorded_at=T0)["structuredContent"]
    assert ing["status"] == "ok"
    s = call("triplex_search", query="кот", as_of=T1)["structuredContent"]
    assert s["status"] == "ok" and s["facts"]
    assert call("triplex_stats")["structuredContent"]["memory"]["facts_active"] == 1


def test_mcp_memory_tools_without_memory_are_tool_errors(tmp_path):
    from pulsenet.triplex import FactJournal
    mcp = TriplexMCP(FactJournal(str(tmp_path / "j.jsonl")))
    r = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "triplex_search", "arguments": {"query": "x"}}})["result"]
    assert r["isError"] is True


# ---------- индексы ----------

def test_lsh_codes_are_deterministic_and_near_vectors_are_close():
    enc = HashEncoder(128)
    a, b, c = enc.embed(["кот лежит на коврике", "кот лежит на коврике.", "квантовая хромодинамика"])
    l1, l2 = LSHIndex(128, bits=512, seed=7), LSHIndex(128, bits=512, seed=7)
    l1.add("a", a)
    l2.add("a", a)
    assert (l1.codes["a"] == l2.codes["a"]).all()              # фикс. seed → воспроизводимо (I6)
    l1.add("b", b)
    l1.add("c", c)
    assert l1.topk(a, 2)[0] == "a" and l1.topk(a, 2)[1] == "b"


def test_vector_index_exact_topk_and_rrf_order():
    vi = VectorIndex(2)
    vi.add("x", __import__("numpy").array([1.0, 0.0]))
    vi.add("y", __import__("numpy").array([0.0, 1.0]))
    assert vi.topk(__import__("numpy").array([1.0, 0.1]), 1)[0][0] == "x"
    assert [k for k, _ in rrf([["a", "b"], ["b", "a"]])] == ["a", "b"]   # равный score → порядок первого появления
    assert rrf([["c"], ["d"]], k=60)[0][0] == "c"


def test_unrelated_query_gets_no_memory(mem):
    """Регресс: «привет как дела» и «погода» не должны подмешивать факт про адрес."""
    text = "Я живу в Амстердаме."
    mem.extractor = scripted({text: {"facts": [{"s": "Нел", "r": "живёт_в", "o": "Амстердам",
                                                "evidence": "живу в Амстердаме", "conf": 0.9}]}})
    mem.ingest(text, recorded_at=T0)
    for q in ["привет как дела", "погода сегодня"]:
        r = mem.recall(q, as_of=T1)
        assert r["facts"] == [] and r["context"] == ""


def test_speaker_name_is_passed_into_prompt():
    seen = []

    def llm(prompt):
        seen.append(prompt)
        return '{"facts": []}'
    make_extractor(llm, speaker="Нел")("Я устал.")
    assert "Говорящий в тексте — Нел" in seen[0] and "Я устал." in seen[0]
