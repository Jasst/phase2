@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo [setup] .venv not found. Run setup_env.bat first.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"

rem ---- nginx via docker compose (optional) ----
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo [nginx] Docker is not running or not installed - nginx skipped.
    goto run
)
docker compose up -d
if %errorlevel% neq 0 (
    echo [nginx] docker compose failed - see messages above. Continuing without nginx.
)

:run
python -m pulsenet serve
pause
