@echo off
cd /d "%~dp0"
python -m venv .venv
if %errorlevel% neq 0 (
    echo [setup] Failed to create venv. Check that Python is installed and on PATH.
    pause
    exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements-dev.txt
pause
