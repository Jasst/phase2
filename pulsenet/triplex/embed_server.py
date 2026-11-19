# -*- coding: utf-8 -*-
"""Отдельный долгоживущий процесс эмбеддингов (llama-server --embedding с bge-m3).

Зачем отдельный процесс:
  * модель грузится один раз при старте приложения и остаётся в памяти;
  * смена модели чата (LlamaCppBackend.ensure перезапускает свой llama-server) её не выгружает;
  * эмбеддинги и чат не конкурируют за один слот `-np`.

start() идемпотентен: повторный вызов не запускает второй процесс.
"""
from __future__ import annotations

import atexit
import os
import subprocess
import time
import urllib.request
from typing import List, Optional


class EmbeddingServerError(RuntimeError):
    pass


class EmbeddingServer:
    def __init__(self, bin_path: str, model_path: str, port: int, host: str = "127.0.0.1",
                 pooling: str = "cls", extra_args: Optional[List[str]] = None,
                 prefix: Optional[List[str]] = None,
                 log_path: Optional[str] = None, startup_timeout: float = 180.0):
        self.bin_path = bin_path
        self.model_path = model_path
        self.port = port
        self.host = host
        self.pooling = pooling
        self.extra_args = list(extra_args or [])
        self.prefix = list(prefix or [])      # аргументы перед -m (например путь к скрипту при запуске через python)
        self.log_path = log_path
        self.startup_timeout = startup_timeout
        self.proc: Optional[subprocess.Popen] = None
        self.starts = 0                       # сколько раз процесс запускали (для проверки «один раз»)
        atexit.register(self.stop)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def _args(self) -> List[str]:
        args = [self.bin_path] + self.prefix + ["-m", self.model_path, "--host", self.host, "--port", str(self.port),
                "--embedding"]
        if self.pooling:
            args += ["--pooling", self.pooling]
        return args + self.extra_args

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> str:
        if self.is_running():
            return self.base_url
        if not os.path.isfile(self.model_path):
            raise EmbeddingServerError(f"модель эмбеддингов не найдена: {self.model_path}")
        log = open(self.log_path, "a", encoding="utf-8") if self.log_path else subprocess.DEVNULL
        self.proc = subprocess.Popen(self._args(), stdout=log, stderr=subprocess.STDOUT)
        self.starts += 1
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise EmbeddingServerError(f"процесс эмбеддингов завершился с кодом {self.proc.returncode}"
                                           + (f", см. {self.log_path}" if self.log_path else ""))
            try:
                with urllib.request.urlopen(f"http://{self.host}:{self.port}/health", timeout=2) as r:
                    if r.status == 200:
                        return self.base_url
            except OSError:
                pass
            time.sleep(0.5)
        self.stop()
        raise EmbeddingServerError(f"эмбеддер не ответил за {int(self.startup_timeout)} с")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
