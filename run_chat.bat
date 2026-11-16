@echo off
call .venv\Scripts\activate.bat
python -m pulsenet chat --state state
pause
