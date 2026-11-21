# SearXNG для поиска (бесплатно, без ключей)

Запускается вместе с приложением:

1. `run_server.bat` при первом запуске сам создаёт `searxng/.env` со случайным
   `SEARXNG_SECRET`. Вручную это можно сделать так (в папке searxng):

       python -c "import secrets; print('SEARXNG_SECRET=' + secrets.token_hex(32))" > .env

2. Затем `run_server.bat` поднимает контейнер:
   `docker compose -f searxng/docker-compose.yml up -d`.
   SearXNG слушает только `127.0.0.1:8080`, остановка — `stop_server.bat`.

3. В корневом `.env` проекта (см. `.env.example`):

       SEARXNG_URL=http://127.0.0.1:8080
       SEARCH_BACKENDS_ORDER=searxng,ddg
       SEARCH_TIME_BUDGET=15

4. Проверка: `curl "http://127.0.0.1:8080/search?q=test&format=json"` должен вернуть JSON, а не 403.
   403 означает, что в `settings.yml` не включён формат json.

Если запускаете без `run_server.bat`: `docker compose up -d` из папки `searxng`.
