@echo off
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -r requirements-dev.txt
pause
