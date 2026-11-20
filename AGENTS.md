# AGENTS.md — Инструкции для агента (opencode и другие MCP-клиенты)

## 1. Единый MCP-сервер (память + интернет)

- Единственный MCP-сервер проекта: `pulsenet-unified` → `http://127.0.0.1:8010/mcp`
  (tools: `triplex_*` — память ТРИПЛЕКСИИ; `web_*` — интернет).
- Запуск: `cd C:\Users\PC\Documents\GitHub\phase2\pulsenet && python -m pulsenet.unified_mcp serve`.
- Auth: Bearer `PULSENET_MCP_TOKEN`; без токена — 503, неверный — 401.
- nginx (docker) проксирует `/mcp` → backend `192.168.65.254:8010` (unified).

- Память общая для всех ваших агентов: факт записывает тот, чей токен; автор — поле `agent` (см. `triplex_facts`). Связать написание с сущностью: `triplex_link`.
- Дополнительно: `triplex_graph` (карта нейронов памяти), `activity_recent` (журнал действий: что уже искали и что не вышло), `pulsenet_guide` (описание сервера и рабочий процесс — вызвать первым, если не уверены).

- **Перед нетривиальной задачей**: `triplex_search` с формулировкой задачи. Если есть факты или
  готовый `context` — учитывай их и явно говори, что опираешьшься на память.
- **После решения** (архитектура, библиотека, баг, правило): `triplex_ingest` → `doc_id` →
  `triplex_assert` с `source={doc_id, span}`. Факт без источника не записывается (I1).
- Конфликт: `triplex_supersede` / `triplex_retract`. Неподтверждённые уверенности: `triplex_confirm`.
- Не сохранять секреты, токены и пароли. Не сохранять догадки — только подтверждённые факты.

## 2. Веб-поиск и проверка фактов в интернете (MCP: pulsenet-unified)

- Инструменты: `web_search`, `web_fetch` — сервер `pulsenet-unified` (`http://127.0.0.1:8010/mcp`).
- **Перед ответами о свежих фактах, сравнениях, документации, чужих решениях — через `web_search`.**

### Полноценный поиск через SearXNG (deep) — возможности как в проекте MCPIISys

- Бэкенды: `SEARCH_BACKENDS_ORDER=searxng,ddg`; `SEARXNG_URL=http://127.0.0.1:8080` (Docker,
  `searxng/settings.yml` с форматом `json`), запасной — DuckDuckGo.
- BM25-скоринг (k1=1.5, b=0.75) по корпусу вместо попарного overlap.
- Cross-encoder rerank (`BAAI/bge-reranker-v2-m3`), fallback на BM25.
- Fallback на сниппет поисковика, если страница не прочиталась.
- Query expansion: базовый запрос + расширения параллельно (синонимы, англ. вариант, декомпозиция).
- Iterative refinement: LLM-уточнение с sanitize, порог релевантности, откат, если не улучшило.
- Time budget / дедлайн, частичные результаты при нехватке времени.
- Dedup (simhash), recency boost, domain trust, coverage gate.
- Context собирается из прочитанных страниц (chunk-выбор по BM25).

### Проверка работоспособности

- MCP: `GET /health` + `tools/list` через Bearer.
- Unified: `http://127.0.0.1:8010/mcp` — инструменты triplex_* + web_*.
- SearXNG: `curl "http://127.0.0.1:8080/search?q=test&format=json"`.

## 3. Ограничения

- SearXNG в этой сборке не проверялся вживую (нужен Docker); без него работает DuckDuckGo.
- Безопасность: web_fetch защищён от SSRF (только http(s), без приватных адресов, без редиректов).
- Модель помнит: память повышает качество ответов, но сама по себе не делает локальную модель умнее.
