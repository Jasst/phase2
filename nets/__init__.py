# -*- coding: utf-8 -*-
"""Пакет взаимодействующих нейросетей (графовые сети с температурой).

Все сети проекта живут в этой папке:
  core.py      — одна сеть: узлы-слова, веса, температурные переходы, асинхронный проход
  transfer.py  — матрица перерасчёта между двумя сетями (мост через общие узлы)
  chain.py     — цепочка сетей: выход одной становится входом следующей, до устойчивой мысли
  cluster.py   — мутации сетей, кластеризация мутантов, эволюционный отбор
  sandbox.py   — изолированная среда адаптации: живые сети не меняются без проверки
  build.py     — построение сетей из текстовых файлов (одна сеть на файл)
  store.py     — сохранение и загрузка ансамбля в _store/
  manager.py   — менеджер: мысль цепочкой, обучение по наградам через песочницу
  _matrices/   — дампы матриц перерасчёта и трасс цепочки (для понимания работы)
"""
from .core import Net
from .chain import Chain
from .transfer import transfer_matrix, dump_matrix
from .cluster import evolve, score_ensemble, cluster_nets
from .sandbox import Sandbox
from .build import build_net, tokenize
from .store import save_ensemble, load_ensemble
from .manager import NetsManager

__all__ = [
    "Net", "Chain", "transfer_matrix", "dump_matrix", "evolve",
    "score_ensemble", "cluster_nets", "Sandbox", "build_net", "tokenize",
    "save_ensemble", "load_ensemble", "NetsManager",
]
