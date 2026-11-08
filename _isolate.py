# -*- coding: utf-8 -*-
"""Изоляция тестов: все файлы, которые пишет приложение, идут во временную папку.

Без этого тесты перезаписывают brain_state.json, session_log.txt, episodes.json,
dialog_log.jsonl и policy.json рабочего проекта. Вызывать до bc.main().
"""
import os
import tempfile


def isolate():
    import brain_chat as bc
    import episodes
    import lm_backend
    import rl_policy

    d = tempfile.mkdtemp(prefix="brain_test_")
    bc.SAVE_PATH = os.path.join(d, "brain_state.json")      # и загрузка, и сохранение
    bc.SESSION_PATH = os.path.join(d, "session_log.txt")
    episodes.PATH = os.path.join(d, "episodes.json")
    lm_backend.LOG_PATH = os.path.join(d, "dialog_log.jsonl")
    rl_policy.POLICY_PATH = os.path.join(d, "policy.json")
    # Сети (nets/): хранилище, логи и песочница тоже во временной папке.
    import nets.manager as nets_manager
    nets_manager.STORE_DIR = os.path.join(d, "nets_store")
    nets_manager.LOG_DIR = os.path.join(d, "nets_logs")
    nets_manager.SANDBOX_DIR = os.path.join(d, "nets_sandbox")
    # Сохранение на выходе — в пустую папку, без записи в проект.
    bc.save_brain = lambda *a, **k: None
    return d
