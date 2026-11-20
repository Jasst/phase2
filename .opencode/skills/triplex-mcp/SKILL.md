---
name: Triplex MCP
description: Work with the Triplex memory MCP server (facts, hybrid search, assert/supersede/retract/confirm, sleep consolidation)
---

# Skill: MCP ТРИПЛЕКСИИ (память проекта)

Use this skill when the task involves the Triplex memory MCP server: saving facts,
searching memory, asserting / superseding / retracting / confirming facts, ontology,
statistics or the sleep/consolidation rehearsal.

## Overview

- Server: `pulsenet-triplex` (implementation: `pulsenet/triplex/mcp.py`)
- Transport: Streamable HTTP, JSON-RPC 2.0, single JSON reply per POST (no SSE)
- Endpoint (local): `http://127.0.0.1:8010/mcp` (унифицированный сервер: память + интернет, веб-поиска)
- Auth: `Authorization: Bearer <PULSENET_MCP_TOKEN>` (from `.env`, without token → 503,
  wrong token → 401); Host / Origin checked against `PULSENET_MCP_HOSTS` (see `mcp_settings`
  in `pulsenet/server.py` / `pulsenet/unified_mcp/server.py`)
- Client (opencode): MCP server name `pulsenet-unified` → tools are called as
  `tools["pulsenet-unified"].<tool_name>({ ... })`

## Tools

| Tool | Purpose | Required args |
|---|---|---|
| `triplex_ingest` | Save a document (flesh) and extract facts from it | `text` |
| `triplex_search` | Hybrid recall: facts + fragments + ready `context` | `query` |
| `triplex_assert` | Write a fact (subject, relation, object) with source | `s`, `r`, `o`, `source: {doc_id, span}` |
| `triplex_supersede` | Explicitly supersede an active fact with a new one | `old_fact_id`, `s`, `r`, `o`, `valid_from`, `source` |
| `triplex_retract` | Retract a fact (kept in journal for audit) | `fact_id`, `at?` |
| `triplex_confirm` | Confirm (`yes`: +0.15, cap 0.95) or refute (`no`: ×0.5) | `fact_id`, `yes` |
| `triplex_query` | Active facts by SRO triple at point in time `as_of` | — |
| `triplex_link_entity` | Bind a writing variant (e.g. declension) to an entity | `entity_id`, `alias` |
| `triplex_define_relation` | Ontology of relations: canonical, aliases, functional, tau | `canonical`, `aliases?`, `functional?`, `tau_days?` |
| `triplex_stats` | Journal + memory counters | — |
| `triplex_check` | Check invariants I1–I5 | — |
| `triplex_consolidate` | "Sleep": dry-run consolidation plan (no training) | — |
| `triplex_whoami` | Server and owner info | — |
| `web_search` | Web search (backends: SearXNG / DuckDuckGo, multi-query, deep ranking) | `queries` or `query` |
| `web_fetch` | Fetch a public page (SSRF-safe) | `url` |

## Workflow

1. Before a non-trivial task: call `triplex_search` with the task formulation. If facts or a
   ready `context` come back, use them and say explicitly that the answer is based on memory.
2. If a fact from memory contradicts the code: trust the code, then `triplex_supersede` or
   `triplex_retract` the old fact.
3. After making a decision (architecture, library choice, bug fix, project rule):
   `triplex_ingest` the source text (role=`assistant`), take `doc_id` from the response, then
   `triplex_assert` with `source={doc_id, span}`. A fact without a source is not recorded (I1).
4. For uncertain facts: `triplex_confirm` (`yes` / `no`). For functional relations (at most one
   active object per subject): expect `status=conflict` from `triplex_assert`.

## Invariants & rules

- I1: a fact requires a source (`doc_id` + character `span`); without source it is not written.
- Retracted facts stay in the journal (audit), but stop being active.
- Supersede closes the old fact at the new `valid_from`.
- Do not save secrets, tokens or passwords. Do not save guesses: only confirmed facts.

## Internet tools

Web tools (`web_search`, `web_fetch`) are NOT part of the `triplex-mcp` server (port 8000).
They live in the unified MCP server `pulsenet-unified` (`python -m pulsenet.unified_mcp serve`,
`http://127.0.0.1:8010/mcp`), which combines memory, identity, core, internet and code tools.

## Connection & troubleshooting

- Server must be running; verify with `GET /health` and `tools/list`.
- Start: `cd C:\Users\PC\Documents\GitHub\phase2\pulsenet && python -m pulsenet serve --port 8000`
- HTTP statuses: `503` — MCP disabled (no token), `401` — bad token, `403` — host/origin not allowed.
