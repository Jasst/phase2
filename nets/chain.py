# -*- coding: utf-8 -*-
"""Цепочка сетей с динамическим временем ответа.

Один раунд: вход → сеть 1 → сеть 2 → ... → сеть N. Выход каждой сети
становится входом следующей. Каждая сеть получает также накопленную
сумму выходов предыдущих (общее число), поэтому информация копится по цепочке.

Мысль — то новое, что цепочка добавила к вопросу (эхо входа исключается).
Раунды повторяются, пока мысль не стабилизируется (косинус между раундами
> 1 - eps). Число раундов динамическое: чем размазаннее (энтропийнее) мысль,
тем больше раундов разрешено, но не больше max_rounds и не дольше time_budget.
"""
import math
import random
import time

from .core import top


def _normalize(v):
    s = sum(v.values())
    if s <= 0:
        return {}
    return {k: x / s for k, x in v.items()}


def _cos(a, b):
    keys = set(a) | set(b)
    num = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    na = math.sqrt(sum(x * x for x in a.values()))
    nb = math.sqrt(sum(x * x for x in b.values()))
    return num / (na * nb) if na and nb else 0.0


def _entropy_norm(v):
    """Нормированная энтропия 0..1: 0 — одна уверенная мысль, 1 — всё размазано."""
    if len(v) <= 1:
        return 0.0
    h = -sum(p * math.log(p) for p in v.values() if p > 0)
    return h / math.log(len(v))


class Chain:
    def __init__(self, nets, temp=1.0, sweeps=2, feedback=0.5, seed=0, log=False):
        self.nets = list(nets)           # порядок цепочки фиксирован
        self.temp = temp
        self.sweeps = sweeps
        self.feedback = feedback
        self.seed = seed
        self.rng = random.Random(seed)   # случайный порядок нейронов; сбрасывается в run()
        self.log = log

    def _through(self, net, sig):
        act = dict(sig)
        for _ in range(self.sweeps):
            net.sweep(act, temp=self.temp, rng=self.rng)
        return _normalize(act)

    def run(self, tokens, max_rounds=8, min_rounds=2, eps=0.02,
            time_budget=None):
        """Возвращает dict: thought, vector, rounds, trace, reason, entropy, seconds."""
        t0 = time.monotonic()
        self.rng.seed(self.seed)                 # один и тот же вход — один и тот же путь
        inp = {}
        for w in tokens:
            inp[w] = inp.get(w, 0.0) + 1.0
        inp = _normalize(inp)
        if not inp or not self.nets:
            return {"thought": [], "vector": {}, "rounds": 0,
                    "trace": [], "reason": "нет входа", "entropy": 0.0, "seconds": 0.0}

        final, prev_final = {}, None
        trace, reason = [], "max_rounds"
        seen_sum = {}                            # накопленная сумма выходов в раунде
        r = -1
        for r in range(max_rounds):
            sig = dict(inp)
            if r > 0:                            # обратная связь мысли на вход
                for k, x in final.items():
                    sig[k] = sig.get(k, 0.0) + self.feedback * x
                sig = _normalize(sig)
            for net in self.nets:
                # общее число: вход = сигнал от предыдущей сети + сумма всех предыдущих
                feed = dict(sig)
                for k, x in seen_sum.items():
                    feed[k] = feed.get(k, 0.0) + x
                out = self._through(net, _normalize(feed))
                for k, x in out.items():
                    seen_sum[k] = seen_sum.get(k, 0.0) + x
                sig = out
                novel = {k: v for k, v in out.items() if k not in inp}
                trace.append({"round": r, "net": net.name, "top": top(novel, 5)})
            # мысль — то новое, что сеть добавила к вопросу: эхо входа исключается
            final = _normalize({k: v for k, v in sig.items() if k not in inp})
            seen_sum = {}                        # следующий раунд копит заново
            conv = _cos(final, prev_final) if prev_final else 0.0
            if self.log:
                print(f"[chain] раунд {r}: {top(final, 5)}  cos={conv:.3f}")
            if prev_final is not None and r + 1 >= min_rounds and conv >= 1 - eps:
                reason = "стабилизация"
                break
            # динамическое время: размазанная мысль получает больше раундов
            budget = min(max_rounds, min_rounds + round(_entropy_norm(final) * (max_rounds - min_rounds)))
            if r + 1 >= budget:
                reason = "бюджет раундов"
                break
            if time_budget is not None and time.monotonic() - t0 > time_budget:
                reason = "лимит времени"
                break
            prev_final = final
        return {"thought": top(final, 6), "vector": final, "rounds": r + 1,
                "trace": trace, "reason": reason,
                "entropy": round(_entropy_norm(final), 3),
                "seconds": round(time.monotonic() - t0, 4)}

    def format_trace(self, result):
        lines = [f"Цепочка из {len(self.nets)} сетей, раундов: {result['rounds']}, "
                 f"остановка: {result['reason']}, энтропия: {result.get('entropy')}"]
        for step in result["trace"]:
            lines.append(f"  раунд {step['round']}  {step['net']:<12} -> "
                         + " ".join(step["top"]))
        lines.append("мысль: " + " ".join(result["thought"]))
        return "\n".join(lines)
