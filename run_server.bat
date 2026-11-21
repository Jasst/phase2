@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ---- Usage: run_server.bat [start|stop] ----
rem start (default): stop old pulsenet processes, start docker services (nginx, searxng), MCP, web/API
rem stop: stop pulsenet processes and listeners on its ports, stop docker services

set "MODE=start"
if /i "%~1"=="stop" set "MODE=stop"
if /i "%~1"=="start" set "MODE=start"
if not "%~1"=="" if /i not "%~1"=="stop" if /i not "%~1"=="start" (
    echo [error] Unknown argument: %~1
    echo Usage: run_server.bat start ^| run_server.bat stop
    pause
    exit /b 1
)

if "%MODE%"=="stop" (
    call :stop_python
    call :stop_docker
    echo [ok] Services stopped.
    pause
    exit /b 0
)

rem ---- start: сначала полная остановка старых процессов, потом запуск ----
call :stop_python
call :start_docker
goto :run_python

:stop_python
echo [app] Stopping pulsenet processes and listeners on its ports...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_server.ps1"
exit /b 0

:stop_docker
echo [docker] Stopping services...
docker compose down
if errorlevel 1 (
    echo [docker] Warning: docker compose down failed or docker unavailable.
) else (
    echo [docker] Services stopped.
)
exit /b 0

:start_docker
rem ---- nginx + searxng via docker compose (optional) ----
docker info >nul 2>&1
if errorlevel 1 (
    echo [docker] Docker is not running or not installed - nginx/searxng skipped.
    exit /b 0
)

rem SearXNG wants a secret in searxng\.env; generate it on first run.
if not exist "searxng\.env" (
    echo [searxng] Creating searxng\.env with a random secret...
    python -c "import secrets; print('SEARXNG_SECRET=' + secrets.token_hex(32))" > "searxng\.env"
    if errorlevel 1 (
        echo [searxng] Failed to create searxng\.env - check that Python is available.
        del "searxng\.env" >nul 2>&1
    )
)

echo [docker] Starting services...
docker compose up -d
if errorlevel 1 (
    echo [docker] docker compose failed - see messages above. Continuing without nginx/searxng.
) else (
    echo [docker] nginx + searxng started. SearXNG: http://127.0.0.1:8080
)
exit /b 0

:run_python
if not exist ".venv\Scripts\activate.bat" (
    echo [setup] .venv not found. Run setup_env.bat first.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

rem ---- MCP-сервер pulsenet-unified (память + интернет) в фоне ----
echo [mcp] Starting pulsenet-unified MCP server (http://127.0.0.1:8010/mcp)...
start /b "" python -m pulsenet.unified_mcp serve

rem ---- чат / веб-API на переднем плане ----
echo [app] Starting web/API server (http://127.0.0.1:8000/)...
echo [app] Ctrl+C: если cmd спросит "Terminate batch job (Y/N)?" - ответьте N,
echo [app] иначе уборка не выполнится; для полной остановки: run_server.bat stop
python -m pulsenet serve

rem ---- веб вышел (в т.ч. по Ctrl+C): гасим MCP и docker ----
echo [app] Web/API stopped, cleaning up MCP, ports and docker...
call :stop_python
call :stop_docker
echo [ok] Services stopped.
pause
exit /b 0