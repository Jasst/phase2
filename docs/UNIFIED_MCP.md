# Единый MCP-сервер: память, личность, интернет, код

Пакет: `pulsenet/unified_mcp/`. Запуск: `python -m pulsenet.unified_mcp serve` (по умолчанию `127.0.0.1:8010/mcp`).
Транспорт: Streamable HTTP, stateless, JSON-ответы. Протокол — официальный MCP SDK (`mcp<2`).

## Что взято откуда

| Возможность | Источник | Что сделано при слиянии |
|---|---|---|
| Журнал фактов, провенанс (I1), bi-temporal, supersede/retract/confirm, конфликты functional | pulsenet / triplex | используется как есть (`TriplexMemory`), отдельная память на каждого пользователя |
| Гибридный поиск и RAG-фолбэк | pulsenet / triplex | как есть; лексика теперь с русскими окончаниями |
| Многопользовательский доступ | blockcoin | вместо одного Bearer-токена — токены на пользователя (хеш в базе), отзыв, выпуск командой CLI |
| Личная идентичность: карантин, два дня, дословная цитата | blockcoin | цитата теперь проверяется по **вашим документам памяти** (ingest), не по чату |
| Общее ядро, меняет только администратор, интервал 24 ч, история | blockcoin | сохранено; добавлено хранение предыдущих версий |
| Поиск: мульти-запрос, BM25, дедупликация | blockcoin `GCN/web_search.py` | подключается через `BLOCKCOIN_PATH`; без него — встроенный DuckDuckGo |
| Чтение страниц | новое | `web_fetch` с защитой от SSRF: только http(s), без приватных адресов, без редиректов |
| Чтение кода | blockcoin `code_analyzer` | упрощённая песочница: корни из `CODE_ROOTS`, realpath, секреты закрыты, только `CODE_ACCESS_USERS` |

Отброшено из blockcoin: вход по криптоподписи кошелька, генерация изображений, цели и эпизоды,
команды чата. Они завязаны на чат-контроллер blockcoin и не переносились.

## Настройка (`.env`)

    PULSENET_DATA_DIR=E:\pulsenet\data_unified
    PULSENET_MCP_TOKEN=<токен владельца>      # владелец, user = MCP_OWNER_ID
    MCP_OWNER_ID=owner
    MCP_ADMIN_USERS=owner                     # кто меняет общее ядро
    CODE_ACCESS_USERS=owner                   # кто читает код (пусто = запрещено всем)
    CODE_ROOTS=pulsenet=E:\pulsenet           # имя=путь; несколько через ;
    MCP_ALLOWED_HOSTS=127.0.0.1:*,localhost:*,ai.example.ru   # домен через nginx добавьте сюда
    IDENTITY_MIN_DAYS=2
    CORE_MIN_INTERVAL_HOURS=24
    SEARXNG_URL=http://127.0.0.1:8080         # свой SearXNG (папка searxng/, запускается run_server.bat)
    SEARCH_BACKENDS_ORDER=searxng,ddg         # порядок поисковых бэкендов: побеждает первый с результатами
    SEARCH_TIME_BUDGET=15                     # секунд на один поиск
    BLOCKCOIN_PATH=                           # путь к проекту blockcoin для deep_search (необязательно)
    PULSENET_UNIFIED_HOST=127.0.0.1
    PULSENET_UNIFIED_PORT=8010

## Токены: как узнать, какой использовать

- **Владелец** — токен из `.env` (`PULSENET_MCP_TOKEN`). Его не нужно искать: он там, где вы его задали.
- **Остальные** — выдаются командами. Значение показывается один раз, сохраните его сразу.
  Потерянный токен восстановить нельзя (в базе только хеш), поэтому выпускайте новый через `rotate`.

    python -m pulsenet.unified_mcp issue-token alice     # первый токен
    python -m pulsenet.unified_mcp rotate alice          # обновить: старый перестаёт работать сразу
    python -m pulsenet.unified_mcp revoke alice          # отозвать без замены
    python -m pulsenet.unified_mcp list-tokens           # кто, активен ли, отпечаток (значения не видны)

`list-tokens` показывает отпечаток (первые 8 символов хеша). По нему можно понять, какой токен у клиента
(сравните с отпечатком, который печатает команда при выпуске — он не выводится, поэтому ориентируйтесь по дате и пользователю).
Токен владельца `rotate` не меняет: поменяйте `PULSENET_MCP_TOKEN` в `.env` и перезапустите сервер.

## Подключение клиентов

**opencode** (`opencode.json` в корне проекта; токен из переменной окружения):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "pulsenet": {
      "type": "remote",
      "url": "http://127.0.0.1:8010/mcp",
      "headers": { "Authorization": "Bearer {env:PULSENET_MCP_TOKEN}" },
      "enabled": true
    }
  }
}
```

**Claude Desktop** и другие клиенты только с stdio — через мост `mcp-remote` (нужен Node.js):

```json
{
  "mcpServers": {
    "pulsenet": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:8010/mcp",
               "--header", "Authorization:${PULSENET_AUTH}"],
      "env": { "PULSENET_AUTH": "Bearer <токен>" }
    }
  }
}
```

Для внешнего доступа через nginx: блок `location ^~ /mcp` должен проксировать на `127.0.0.1:8010`
с `proxy_buffering off` (как в текущем `nginx.conf`) и не сбрасывать заголовок `Authorization`.

## Инструменты (22)

- **Память:** `triplex_ingest`, `triplex_search`, `triplex_query`, `triplex_assert`, `triplex_supersede`,
  `triplex_retract`, `triplex_confirm`, `triplex_define_relation`, `triplex_stats`, `triplex_check`.
  Для `triplex_assert` нужен `doc_id` и `span` из `triplex_ingest`: без источника факт не пишется.
- **Идентичность:** `identity_propose` (цитата обязательна и должна быть в вашей памяти), `identity_commit`,
  `identity_chain`. Ворота отсекают код, факты (даты, контакты), инструкции и формулировки не от первого лица.
- **Общее ядро:** `core_get` (всем); `core_propose`, `core_commit` (только `MCP_ADMIN_USERS`).
- **Интернет:** `web_search` (до 4 запросов параллельно), `web_fetch`.
- **Код:** `code_list`, `code_read`, `code_search` (только `CODE_ACCESS_USERS`).
- **Служебное:** `whoami`.

## Протокол для модели

Файл `AGENTS.md` в проекте (как в `docs/OPENCODE.md`) дополняется правилами:
перед задачей — `triplex_search`; для проверки фактов в интернете — `web_search` и `web_fetch`;
для кода — `code_search` и `code_read` вместо угадывания; личное наблюдение о себе — только через
`identity_propose` с цитатой из реального диалога, которая уже сохранена через `triplex_ingest`.

## Тесты

    python -m pytest tests/test_unified_mcp.py

Покрыто: токены и отзыв, изоляция памяти между пользователями, ворота идентичности и двухдневное правило,
цитата из памяти, общее ядро и интервал, песочница кода (выход за корень, `.env`), SSRF-блокировка,
разбор DuckDuckGo, сквозной тест по HTTP с настоящим MCP-клиентом (401 без токена, изоляция, отказ не-админу).

## Ограничения

- Поиск: SearXNG нужен формат json в `searxng/settings.yml` (уже включён). DuckDuckGo HTML — запасной вариант, его разметка может меняться.
- SearXNG в этой сборке не проверялся вживую (нужен Docker). Проверка: `curl "http://127.0.0.1:8080/search?q=test&format=json"`.
- Защита от SSRF проверяет адрес при запросе; DNS-rebinding между проверкой и загрузкой не закрыт полностью.
- Эмбеддинги по умолчанию хеш-эмбеддер; для смыслового поиска подключайте bge-m3 через `encoder_factory`.
- Выдача токенов — через CLI на сервере; веб-страницы для этого нет.
- Веб-интерфейс «Память» из pulsenet этим сервером не подключён: это следующий шаг.
