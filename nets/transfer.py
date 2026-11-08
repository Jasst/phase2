# -*- coding: utf-8 -*-
"""Матрица перерасчёта между двумя сетями.

Сети связаны через общие узлы (слова, которые есть в обеих). Если масса
стоит в узле i сети A, то её путь в сеть B идёт так: i → k (переход в A),
k — общий узел (мост), k → j (переход в B). Итоговая матрица:

    M_AB[i][j] = Σ_k P_A(k | i) · P_B(j | k),   где k ∈ nodes(A) ∩ nodes(B)

Это и есть «матрица перерасчёта»: по ней видно, какая мысль из сети A
во что превращается в сети B. Матрица — диагностика; при работе цепочки
сигнал идёт теми же переходами, но по шагам.
"""
import json
import os

from .core import top

MATRIX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_matrices")


def transfer_matrix(a, b, temp=1.0):
    shared = a.nodes() & b.nodes()
    M = {}
    for i in a.W:
        row = {}
        for k, p in a.transitions(i, temp).items():
            if k not in shared:
                continue
            for j, q in b.transitions(k, temp).items():
                row[j] = row.get(j, 0.0) + p * q
        if row:
            M[i] = row
    return M


def dump_matrix(M, a_name, b_name, temp, out_dir=MATRIX_DIR, top_k=10):
    """Пишет матрицу в JSON (полностью) и в читаемый txt (топ связей)."""
    os.makedirs(out_dir, exist_ok=True)
    stem = f"{a_name}__{b_name}_T{temp:.2f}"
    json_path = os.path.join(out_dir, stem + ".json")
    txt_path = os.path.join(out_dir, stem + ".txt")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"from": a_name, "to": b_name, "temp": temp,
                   "M": {i: {j: round(v, 5) for j, v in r.items()}
                         for i, r in M.items()}},
                  f, ensure_ascii=False)
    lines = [f"Матрица перерасчёта {a_name} -> {b_name} (T={temp:.2f})",
             f"строк: {len(M)}", ""]
    for i in sorted(M):
        best = top(M[i], top_k)
        lines.append(f"{i} -> " + ", ".join(f"{j}:{M[i][j]:.3f}" for j in best))
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return json_path, txt_path
