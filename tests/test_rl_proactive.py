# -*- coding: utf-8 -*-
"""Проверки обучения с подкреплением и проактивности (без окна pygame).
Запуск из корня проекта: python test_rl_proactive.py
"""
import os
import random
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase3.rl_policy import Bandit, arms_for, arm_key  # noqa: E402
from phase3 import reasoning
from phase3.reasoning import Answerer, GREET_REPLY  # noqa: E402
from phase3.mind import Mind  # noqa: E402
from phase3.brain_chat import Brain  # noqa: E402


def test_bandit_learns_rewarded_arm():
    """Стратегия, которую пользователь регулярно оценивает /good, должна выигрывать."""
    with tempfile.TemporaryDirectory() as d:
        pol = Bandit(os.path.join(d, "policy.json"))
        arms = arms_for(True)
        good = ("group", True)
        rng = random.Random(1)
        picks = {arm_key(a): 0 for a in arms}
        for step in range(400):
            arm = pol.choose(arms, rng=rng)
            picks[arm_key(arm)] += 1
            # Группа с переписыванием «нравится» пользователю в 90% случаев, остальные в 30%.
            r = 1.0 if (arm == good and rng.random() < 0.9) or (arm != good and rng.random() < 0.3) else 0.0
            pol.reward(arm, r)
        assert pol.mean(good) > max(pol.mean(a) for a in arms if a != good), pol.summary(arms)
        assert picks[arm_key(good)] > 0.4 * 400, picks


def test_bandit_persists_and_survives_corruption():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "policy.json")
        pol = Bandit(path)
        pol.reward(("whole", False), 1.0)
        again = Bandit(path)
        assert again.mean(("whole", False)) > 0.5
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        broken = Bandit(path)          # не должно падать
        assert broken.stats == {}


def test_no_auto_learning_from_unverified_answer():
    """final() не должен менять граф: закрепление только по /good и /teach."""
    brain = Brain()
    brain.train_words("кошка пьёт молоко из миски".split())
    mind = Mind(brain)
    ans = Answerer(brain, mind)
    before = (len(brain.links), len(brain.neurons))
    st = ans.begin("кошка пьёт молоко", 4)
    st["best"], st["best_score"] = "молоко из миски", 0.9
    ans.final(st)
    assert (len(brain.links), len(brain.neurons)) == before


def test_greeting_fallback_without_lm():
    brain = Brain()
    brain.train_words("кошка пьёт молоко".split())
    ans = Answerer(brain, Mind(brain))
    st = ans.begin("привет", 4)
    out = ans.final(st)
    assert out is not None and out[0] == GREET_REPLY, out


def test_unknown_question_gets_proactive_reply():
    brain = Brain()
    brain.train_words("кошка пьёт молоко".split())
    ans = Answerer(brain, Mind(brain))
    st = ans.begin("квантовый гравитон запутанность", 4)
    assert ans.final(st) is None
    text = ans.unknown_reply(st)
    assert "/teach" in text and "гравитон" in text, text


def test_policy_arm_stored_and_none_mode_has_no_hint():
    brain = Brain()
    brain.train_words("кошка пьёт молоко из миски каждый день".split())
    mind = Mind(brain)
    ans = Answerer(brain, mind)
    pol = Bandit(os.path.join(tempfile.mkdtemp(), "p.json"))
    st = ans.begin("кошка пьёт молоко", 4, pol)
    assert st["arm"] is not None
    mode, revise = st["arm"]
    if mode == "none":
        assert st["thought"] is None
    expected = reasoning.REVISE_ROUNDS if st["arm"][1] else 0
    assert st["revise_rounds"] == expected


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
