@echo off
cd /d "%~dp0"

rem ---- Usage: run_server.bat [start|stop] ----
rem start (default): restart docker services + unified MCP + web/API
rem stop: stop docker services + pulsenet python processes
setlocal EnableDelayedExpansion

set MODE=start
if /i "%~1"=="stop" set MODE=stop
if /i "%~1"=="start" set MODE=start
if not "%~1"=="" if /i not "%~1"=="stop" if /i not "%~1"=="start" (
    echo [error] Unknown argument: %~1
    echo Usage: run_server.bat start ^| run_server.bat stop
    pause
    exit /b 1
)

if "!MODE!"=="stop" (
    call :stop_python
    call :stop_docker
    echo [ok] Services stopped.
    pause
    exit /b 0
)

rem ---- start: ensure clean state, then run everything ----
call :stop_python
call :start_docker
goto :run_python

:stop_python
echo [app] Stopping pulsenet python processes...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'pulsenet(\.unified_mcp)?\s+serve' -or $_.CommandLine -match '-m\s+pulsenet' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Host ('[app] Stopped PID ' + $_.ProcessId) } catch {} }"
exit /b 0

:stop_docker
echo [docker] Stopping services...
docker compose down
if !errorlevel! neq 0 (
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

rem SearXNG wants a secret in searxng/.env; generate it on first run.
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

rem ---- Единый MCP-сервер: pulsenet-unified (память + интернет, веб-поиск) ----
echo [mcp] Starting pulsenet-unified MCP server (http://127.0.0.1:8010/mcp)...
start /b "" python -m pulsenet.unified_mcp serve

echo [app] Starting web/API server (http://127.0.0.1:8000/)...
python -m pulsenet serve
pause
