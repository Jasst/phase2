# MCP-сервер ТРИПЛЕКСИИ

Эндпоинт: `https://<домен>/mcp/` через nginx, или `http://127.0.0.1:8000/mcp` локально.
Транспорт: Streamable HTTP, JSON-RPC 2.0, один JSON-ответ на запрос (без SSE).

Аутентификация: заголовок `Authorization: Bearer <PULSENET_MCP_TOKEN>`. Без токена эндпоинт отвечает 503.
Host и Origin проверяются по `PULSENET_MCP_HOSTS`. Identity из заголовков не берётся.

## Инструменты (13)

| Инструмент | Что делает |
|---|---|
| `triplex_ingest` | сохранить документ и извлечь факты; сбой экстракции не теряет документ |
| `triplex_search` | гибридный поиск: факты с источниками, фрагменты, готовый `context` |
| `triplex_assert` | записать факт вручную с `source={doc_id, span}`; `functional` может вернуть `conflict` |
| `triplex_supersede` | явно вытеснить действующий факт новым (старый закрывается на `valid_from`) |
| `triplex_retract` | отозвать факт; запись остаётся в журнале |
| `triplex_confirm` | `yes`: уверенность +0.15, не выше 0.95; `no`: ×0.5 |
| `triplex_query` | действующие факты по `s`, `r`, `o` на момент `as_of` |
| `triplex_link_entity` | привязать написание (склонение) к сущности |
| `triplex_define_relation` | синонимы отношения, `functional`, τ затухания в днях |
| `triplex_stats` | счётчики журнала и памяти |
| `triplex_check` | проверка инвариантов журнала |
| `triplex_consolidate` | «Сон», сухой прогон: сколько фактов подходит и датасет; обучение не запускается |
| `triplex_whoami` | сервер и владелец (один сервер — один владелец) |

## Пример

    curl -s -X POST https://<домен>/mcp/ \
      -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
