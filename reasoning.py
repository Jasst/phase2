# -*- coding: utf-8 -*-
"""Осознанный ответ: мысль из нескольких групп графа, рекуррентное
самосогласование, перепроверка и обучение на подтверждённом ответе.

Конвейер (всё, что трогает граф, выполняется в главном потоке):
  1. think():   несколько вариантов мысли — вся сеть и каждая релевантная
                группа нейронов. Каждый вариант рекуррентно уточняется:
                черновик снова подаётся в сеть, пока не стабилизируется.
  2. request(): LM пишет связный ответ по лучшей мысли.
  3. feed():    ответ проверяется сетью (support) и переписывается LM,
                лучшая версия сохраняется.
  4. final():   ответ, прошедший порог, обучает граф (train_words).
"""

import math
import os
import re
from dataclasses import dataclass

from mind import cosine

GROUPS = 3                    # сколько групп нейронов предлагают мысль
GROUP_WORDS = 2               # слов из каждой группы в затравку
RECUR_ROUNDS = 3              # шагов рекуррентного уточнения мысли
STABLE = 0.6                  # доля совпадения, при которой мысль считается устойчивой
LEARN_MIN = 0.12              # ниже этого оценки ответ не обучает граф
REVISE_ROUNDS = max(0, int(os.environ.get("BRAIN_REVISE", "1")))
W_SUPPORT, W_STAB, W_LEN = 0.5, 0.3, 0.2

_WORD = re.compile(r"[^\W_]+")


def tokens(text):
    return _WORD.findall(str(text).lower())


@dataclass
class Thought:
    words: list
    group: object          # id группы нейронов или None (вся сеть)
    stability: float
    support: float
    score: float

    def describe(self):
        where = "вся сеть" if self.group is None else f"группа {self.group}"
        return (f"мысль ({where}, оценка {self.score:.2f}): "
                + " ".join(self.words))


class Answerer:
    def __init__(self, brain, mind):
        self.brain = brain
        self.mind = mind

    def _ids(self, words):
        return [self.brain.words[w] for w in words if w in self.brain.words]

    # ---------- 1. мысль из нескольких групп ----------

    def _variants(self, seeds):
        """Варианты затравки: без группы и с словами каждой релевантной группы."""
        b = self.brain
        ctx = self.mind._context(seeds, b.adjacency())
        ranked = sorted(((cosine(ctx, cent), gid) for gid, cent in b.gcent.items() if cent),
                        reverse=True)
        out = [(None, [])]
        for sim, gid in ranked[:GROUPS]:
            if sim <= 0:
                break
            members = [w for w, g in b.wgroup.items() if g == gid and w not in seeds]
            members.sort(key=lambda w: -ctx.get(w, 0.0))
            extra = [w for w in members[:GROUP_WORDS] if ctx.get(w, 0.0) > 0]
            if extra:
                out.append((gid, extra))
        return out

    def _recur(self, seeds, words, target):
        """Черновик снова подаётся в сеть как контекст, пока не стабилизируется."""
        current, stab = list(words), 0.0
        for _ in range(RECUR_ROUNDS):
            nxt, _ = self.mind.answer(self._ids(seeds + current[:4]), target)
            if not nxt:
                break
            a, c = set(nxt), set(current)
            stab = len(a & c) / max(1, len(a | c))
            current = nxt
            if stab >= STABLE:
                break
        return current, stab

    def think(self, question, target):
        """Лучшая мысль среди вариантов или None, если вопрос незнаком сети."""
        seeds = [w for w in tokens(question) if w in self.brain.words]
        if not seeds:
            return None, seeds
        best = None
        for gid, extra in self._variants(seeds):
            words, _ = self.mind.answer(self._ids(seeds + extra), target)
            if not words:
                continue
            words, stab = self._recur(seeds, words, target)
            support = self.mind.support(seeds, words)
            length = min(1.0, len(words) / max(1, target))
            score = W_SUPPORT * support + W_STAB * stab + W_LEN * length
            t = Thought(words, gid, stab, support, score)
            if best is None or t.score > best.score:
                best = t
        return best, seeds

    # ---------- 2–3. LM пишет и проверяется ----------

    def judge(self, seeds, text):
        """Насколько текст опирается на граф вопроса (0..1)."""
        return self.mind.support(seeds, tokens(text))

    def begin(self, question, target):
        thought, seeds = self.think(question, target)
        return {"q": question, "seeds": seeds, "thought": thought, "stage": 0,
                "best": None, "best_score": -1.0}

    def request(self, st):
        """Запрос к LM: (вопрос, добавка к системному промпту, запоминать?)."""
        th = st["thought"]
        if st["stage"] == 0:
            note = ""
            if th is not None and th.words:
                where = "вся сеть" if th.group is None else f"группа {th.group}"
                note = (f"Мысль сети ({where}): связанные по смыслу слова — "
                        f"{' '.join(th.words)}. Опирайся на них, если они согласуются "
                        "с вопросом. Если нет — ответь по существу и не выдумывай.")
            return st["q"], note, True
        prev = st["best"] or ""
        return (f"Вопрос: {st['q']}\nЧерновик ответа: {prev}\n"
                "Проверь черновик: верен ли он и отвечает ли на вопрос. Если да — "
                "повтори его. Если нет — дай исправленный ответ на русском, 1–3 предложения."
                ), "", False

    def feed(self, st, text):
        """Принимает ответ LM. True — нужен следующий запрос (переписывание)."""
        text = (text or "").strip()
        if text:
            score = self.judge(st["seeds"], text)
            if st["best"] is None or score >= st["best_score"]:
                st["best"], st["best_score"] = text, score
        st["stage"] += 1
        return st["best"] is not None and st["stage"] <= REVISE_ROUNDS

    # ---------- 4. итог и обучение ----------

    def final(self, st):
        """(текст, оценка, from_lm) или None. Обучает граф, если оценка достаточна."""
        if st["best"] is not None:
            text, score, from_lm = st["best"], st["best_score"], True
            words = tokens(text)
        elif st["thought"] is not None:
            text = " ".join(st["thought"].words)
            score, from_lm = st["thought"].score, False
            words = st["thought"].words
        else:
            return None
        if words and score >= LEARN_MIN:
            self.learn(st["q"], words)
        return text, score, from_lm

    def learn(self, question, words):
        """Подтверждённый ответ становится частью сети: вопрос и ответ связываются."""
        q = tokens(question)
        self.brain.train_words(q + words)        # новые слова становятся нейронами
        self.brain._reinforce_answer(words)
