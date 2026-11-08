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
    which = shutil.which
    if sys.platform == "darwin" and which("pbcopy") and which("pbpaste"):
        return ["pbcopy"], ["pbpaste"]
    if sys.platform.startswith("win") and which("powershell"):
        # base64 — обходит любую кодировку консоли Windows
        write = ["powershell", "-NoProfile", "-Command",
                 "$b=[Convert]::FromBase64String($env:CLIP_B64);"
                 "Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString($b))"]
        read = ["powershell", "-NoProfile", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                "$s=Get-Clipboard -Raw; "
                "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s))"]
        return write, read
    if os.environ.get("WAYLAND_DISPLAY") and which("wl-copy") and which("wl-paste"):
        return ["wl-copy"], ["wl-paste", "-n"]
    if which("xclip"):
        return (["xclip", "-selection", "clipboard"],
                ["xclip", "-selection", "clipboard", "-o"])
    if which("xsel"):
        return ["xsel", "-bi"], ["xsel", "-bo"]
    return None, None


def copy(text):
    _INTERNAL["text"] = text
    write, _ = _commands()
    if not write:
        return "internal"
    try:
        if sys.platform.startswith("win") and shutil.which("powershell"):
            import base64, os as _os
            env = dict(_os.environ)
            env["CLIP_B64"] = base64.b64encode(text.encode("utf-8")).decode("ascii")
            subprocess.run(write, timeout=TIMEOUT, check=True,
                           capture_output=True, env=env)
        else:
            subprocess.run(write, input=text.encode("utf-8"), timeout=TIMEOUT,
                           check=True, capture_output=True)
        return "system"
    except (OSError, subprocess.SubprocessError):
        return "internal"


def paste():
    _, read = _commands()
    if not read:
        return _INTERNAL["text"]
    try:
        out = subprocess.run(read, timeout=TIMEOUT, capture_output=True)
        if out.returncode != 0:
            return _INTERNAL["text"]
        if sys.platform.startswith("win") and shutil.which("powershell"):
            import base64
            return base64.b64decode(out.stdout.strip()).decode("utf-8", "ignore")
        return out.stdout.decode("utf-8", "ignore").replace("\x00", "")
    except (OSError, subprocess.SubprocessError, ValueError):
        return _INTERNAL["text"]
