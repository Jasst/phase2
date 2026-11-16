# -*- coding: utf-8 -*-
"""Загрузка .env без внешних зависимостей. Уже заданные переменные окружения не перезаписываются."""
import os


def load_dotenv(path=".env"):
    if not os.path.isfile(path):
        return 0
    loaded = 0
    with open(path, encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    return loaded
