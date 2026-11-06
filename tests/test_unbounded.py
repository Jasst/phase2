# -*- coding: utf-8 -*-
"""Сеть без лимита нейронов: рост, размещение, камера, сохранение.
Запуск из корня проекта: python test_unbounded.py
"""
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _isolate  # noqa: E402
_isolate.isolate()

import brain_chat as bc  # noqa: E402
from brain_chat import Brain, GRID  # noqa: E402

N_WORDS = 3000          # больше, чем было лимитом (700) и клеток старой сетки (2500)


def _words(n, seed=7):
    rng = random.Random(seed)
    syl = ["ка", "то", "ми", "ра", "не", "зу", "ло", "ви", "ос", "ту", "ге", "пи"]
    out, seen = [], set()
    while len(out) < n:
        w = "".join(rng.choice(syl) for _ in range(rng.randint(2, 4)))
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def test_grows_past_old_limits():
    """Все слова получают нейроны, даже если их больше прежнего лимита."""
    b = Brain()
    words = _words(N_WORDS)
    t0 = time.time()
    for i in range(0, len(words), 8):
        b.train_words(words[i:i + 8])
    elapsed = time.time() - t0
    missing = [w for w in words if w not in b.words]
    assert not missing, f"без нейрона осталось {len(missing)} слов"
    assert len(b.neurons) >= N_WORDS, len(b.neurons)
    print(f"  рост: {len(b.neurons)} нейронов, {len(b.links)} связей, {elapsed:.1f} с")


def test_placement_never_fails_and_leaves_grid():
    """add_free всегда находит клетку, а координаты выходят за прежнюю сетку."""
    b = Brain()
    for _ in range(GRID * GRID + 500):          # заполняем больше клеток, чем было
        assert b.add_free(GRID // 2, GRID // 2) is not None
    assert len(b.neurons) > GRID * GRID
    assert any(not (0 <= n.r < GRID and 0 <= n.c < GRID) for n in b.neurons.values()), \
        "сеть не вышла за прежние границы сетки"
    assert len({(n.r, n.c) for n in b.neurons.values()}) == len(b.neurons)


def test_camera_keeps_whole_network_on_screen():
    """После роста вся сеть помещается в окно сети (камера подстраивается)."""
    b = Brain()
    for w in _words(800, seed=3):
        b.train_words([w])
    for _ in range(5):
        bc.update_view(b, dt=1.0)        # dt=1 — камера доходит до цели сразу
    W, H = bc.NET_W, bc.H
    for n in b.neurons.values():
        x, y = bc.cell_xy(n.r, n.c)
        assert 0 <= x <= W and 0 <= y <= H, (n.id, x, y)


def test_save_load_keeps_every_neuron():
    b = Brain()
    words = _words(1200, seed=11)
    for i in range(0, len(words), 8):
        b.train_words(words[i:i + 8])
    before = (len(b.neurons), len(b.links), len(b.words))
    restored = Brain()
    restored.load_dict(b.to_dict())
    after = (len(restored.neurons), len(restored.links), len(restored.words))
    assert after == before, (before, after)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("OK  ", t.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, "->", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
