# -*- coding: utf-8 -*-
"""Буфер обмена: системный, если доступен, и внутренний запасной.

Копирование внутри приложения работает всегда: даже если системного буфера нет
(headless, нет xclip/wl-copy), текст остаётся во внутреннем буфере и вставляется.
pygame.scrap не используется: он помечен как устаревший.
"""
import os
import shutil
import subprocess
import sys

_INTERNAL = {"text": ""}
TIMEOUT = 2.0


def _commands():
    """(команда записи, команда чтения) для текущей системы или (None, None)."""
    which = shutil.which
    if sys.platform == "darwin" and which("pbcopy") and which("pbpaste"):
        return ["pbcopy"], ["pbpaste"]
    if sys.platform.startswith("win") and which("clip") and which("powershell"):
        return ["clip"], ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]
    if os.environ.get("WAYLAND_DISPLAY") and which("wl-copy") and which("wl-paste"):
        return ["wl-copy"], ["wl-paste", "-n"]
    if which("xclip"):
        return (["xclip", "-selection", "clipboard"],
                ["xclip", "-selection", "clipboard", "-o"])
    if which("xsel"):
        return ["xsel", "-bi"], ["xsel", "-bo"]
    return None, None


def copy(text):
    """Кладёт текст в буфер. Возвращает 'system' или 'internal'."""
    _INTERNAL["text"] = text
    write, _ = _commands()
    if write:
        try:
            subprocess.run(write, input=text.encode("utf-8"), timeout=TIMEOUT,
                           check=True, capture_output=True)
            return "system"
        except (OSError, subprocess.SubprocessError):
            pass
    return "internal"


def paste():
    """Текст из буфера. Если системный недоступен — внутренний."""
    _, read = _commands()
    if read:
        try:
            out = subprocess.run(read, timeout=TIMEOUT, capture_output=True)
            if out.returncode == 0:
                return out.stdout.decode("utf-8", "ignore").replace("\x00", "")
        except (OSError, subprocess.SubprocessError):
            pass
    return _INTERNAL["text"]
