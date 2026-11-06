# -*- coding: utf-8 -*-
"""Пишет весь вывод программы в файл brain_log.txt рядом с ней.

Перенаправляет стандартные потоки на уровне системы, поэтому попадают:
обычный вывод Python, ошибки и предупреждения, необработанные исключения,
и сообщения нативных библиотек (llama.cpp, CUDA), которые пишут мимо Python.

Использование: в самом начале brain_chat.py, до других импортов:
    import debug_log; debug_log.setup()
"""
import faulthandler
import os
import platform
import sys
import traceback
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain_log.txt")
_file = None


def _log(text):
    print(text, flush=True)


def _excepthook(exc_type, exc, tb):
    _log("\n=== НЕОБРАБОТАННОЕ ИСКЛЮЧЕНИЕ ===")
    _log("".join(traceback.format_exception(exc_type, exc, tb)))


def _unraisable(unraisable):
    _log("\n=== ИСКЛЮЧЕНИЕ, ПРОИГНОРИРОВАННОЕ PYTHON ===")
    _log(f"{unraisable.object!r}")
    _log("".join(traceback.format_exception(
        unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback)))


def setup():
    """Открывает лог и перенаправляет вывод. Вызывать один раз в самом начале."""
    global _file
    if _file is not None:
        return
    _file = open(LOG_PATH, "w", encoding="utf-8")
    fd = _file.fileno()
    # Всё, что пишется в stdout/stderr (включая C-библиотеки), уходит в файл.
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")
    _log(f"Журнал запуска: {datetime.now():%Y-%m-%d %H:%M:%S}")
    _log(f"Python: {sys.version}")
    _log(f"Платформа: {platform.platform()}")
    _log(f"Папка: {os.path.dirname(os.path.abspath(__file__))}")
    _log("-" * 60)
    sys.excepthook = _excepthook
    if hasattr(sys, "unraisablehook"):
        default = sys.unraisablehook

        def hook(unraisable):
            _unraisable(unraisable)
            default(unraisable)
        sys.unraisablehook = hook
    # Аварийные падения (segfault в нативной библиотеке) тоже пишутся в файл
    faulthandler.enable(file=sys.stderr, all_threads=True)
