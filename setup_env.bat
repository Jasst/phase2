@echo off
REM Пересборка окружения проекта phase2 (запускать из папки проекта)
setlocal
cd /d "%~dp0"

set PY=C:\Users\PC\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PY%" set PY=py -3.13

echo [1/4] Удаляю старое виртуальное окружение...
if exist .venv_rmdir /s /q .venv

echo [2/4] Создаю новое .venv...
%PY% -m venv .venv || goto :err

echo [3/4] Обновляю pip и ставлю зависимости...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel || goto :err
pip install -r requirements.txt || goto :err

echo [4/4] Пересобираю llama-cpp-python с нуля (CPU, без кэша)...
pip install --force-reinstall --no-cache-dir llama-cpp-python || goto :err

echo.
echo Готово. Запуск:  .venv\Scripts\python brain_chat.py
echo Для GPU-варианта (если есть CUDA):
echo   pip install --force-reinstall --no-binary=:all: ^
echo     --config-setting cmake_args="-DGGML_CUDA=ON" llama-cpp-python
goto :eof

:err
echo ОШИБКА: прервано на шаге выше.
exit /b 1
