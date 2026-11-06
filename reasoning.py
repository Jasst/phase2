# -*- coding: utf-8 -*-
"""Осознанный ответ: мысль из нескольких групп графа, рекуррентное
самосогласование, перепроверка и обучение только по награде пользователя.

Конвейер (всё, что трогает граф, выполняется в главном потоке):
  1. think():   варианты мысли — вся сеть и лучшая группа нейронов. Каждый
                вариант рекуррентно уточняется, пока не стабилизируется.
  2. begin():   стратегия выбирается бандитом rl_policy (режим подсказки и
                переписывание). Без LM стратегия не нужна — берётся лучшая мысль.
  3. request(): LM пишет связный ответ по мысли выбранной стратегии.
  4. feed():    ответ проверяется сетью (support) и при необходимости переписывается.
  5. final():   итог. Граф НЕ обучается здесь: обучение только по /good, /teach.
  6. unknown_reply(): если ответа нет — проактивное уточнение вместо тупика.
"""

import math
import os
import re
from dataclasses import dataclass

from mind import cosine
from rl_policy import arms_for

GROUPS = 3                    # сколько групп нейронов предлагают мысль
GROUP_WORDS = 2               # слов из каждой группы в затравку
RECUR_ROUNDS = 3              # шагов рекуррентного уточнения мысли
STABLE = 0.6                  # доля совпадения, при которой мысль считается устойчивой
LEARN_MIN = 0.12              # ниже этого оценки ответ не считается уверенным
REVISE_ROUNDS = max(0, int(os.environ.get("BRAIN_REVISE", "1")))
W_SUPPORT, W_STAB, W_LEN = 0.5, 0.3, 0.2
# Порог опоры на граф: ниже него мысль не подсказывается LM.
HINT_MIN_SUPPORT = float(os.environ.get("BRAIN_HINT_MIN", "0.22"))
# Приветствия обходят граф и не требуют оценки.
_GREET = re.compile(
    r"^\s*(привет|здравствуй\w*|добрый\s+(день|вечер|утро)|доброе\s+утро|хай|hi|hello)\b",
    re.I)
GREET_REPLY = "Здравствуйте! Чем могу помочь?"
_WORD = re.compile(r"[^\W_]+")

_ANSWER_LABEL = re.compile(r"\bОТВЕТ\s*:\s*", re.I)
_ANSWER_LABEL_EN = re.compile(r"\bANSWER\s*:\s*", re.I)


def _split_rationale(text):
    """Делит текст на рациональ и финальный ответ по метке ОТВЕТ:/ANSWER:."""
    for pat in (_ANSWER_LABEL, _ANSWER_LABEL_EN):
        m = pat.search(text)
        if m:
            return text[:m.start()].strip(), text[m.end():].strip()
    return "", text.strip()


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
        """Уточнение: стабильное ядро сохраняется, хвост заменяется."""
        current, stab = list(words), 0.0
        for _ in range(RECUR_ROUNDS):
            nxt, _ = self.mind.answer(self._ids(seeds + current[:4]), target)
            if not nxt:
                break
            a, c = set(nxt), set(current)
            stab = len(a & c) / max(1, len(a | c))
            stable_core = [w for w in current if w in a]
            new_extra = [w for w in nxt if w not in stable_core]
            current = (stable_core + new_extra)[:target] or nxt
            if stab >= STABLE:
                break
        return current, stab

    def think(self, question, target):
        """Лучшая мысль отдельно для режима «вся сеть» и «группа».

        Возвращает (словарь {режим: Thought}, затравка). Словарь пуст,
        если вопрос незнаком сети.
        """
        seeds = [w for w in tokens(question) if w in self.brain.words]
        if not seeds:
            return {}, seeds
        best = {}
        for gid, extra in self._variants(seeds):
            mode = "whole" if gid is None else "group"
            words, _ = self.mind.answer(self._ids(seeds + extra), target)
            if not words:
                continue
            words, stab = self._recur(seeds, words, target)
            support = self.mind.support(seeds, words)
            length = min(1.0, len(words) / max(1, target))
            score = W_SUPPORT * support + W_STAB * stab + W_LEN * length
            t = Thought(words, gid, stab, support, score)
            if mode not in best or t.score > best[mode].score:
                best[mode] = t
        return best, seeds

    # ---------- 2–3. LM пишет и проверяется ----------

    def judge(self, seeds, text):
        """Насколько текст опирается на граф вопроса (0..1)."""
        return self.mind.support(seeds, tokens(text))

    def begin(self, question, target, policy=None):
        """Начать ответ. policy — бандит стратегий; None означает «без выбора»
        (например, режим без LM), тогда берётся лучшая мысль."""
        q = str(question)
        greet = bool(_GREET.match(q))
        arm, thought, seeds, revise_rounds = None, None, [], REVISE_ROUNDS
        if not greet:
            by_mode, seeds = self.think(q, target)
            if by_mode:
                if policy is None:
                    thought = max(by_mode.values(), key=lambda t: t.score)
                else:
                    arm = policy.choose(arms_for(REVISE_ROUNDS > 0))
                    thought = by_mode.get(arm[0])          # "none" -> None
                    revise_rounds = REVISE_ROUNDS if arm[1] else 0
            if thought is not None and thought.support < HINT_MIN_SUPPORT:
                thought = None
        return {"q": q, "seeds": seeds, "thought": thought, "stage": 0,
                "best": None, "best_score": -1.0, "greet": greet,
                "arm": arm, "revise_rounds": revise_rounds, "rationale": ""}

    def request(self, st):
        th = st["thought"]
        if st["stage"] == 0:
            note = ""
            if th is not None and th.words:
                where = "вся сеть" if th.group is None else f"группа {th.group}"
                note = (f"Мысль сети ({where}): связанные по смыслу слова — "
                        f"{' '.join(th.words)}. Опирайся на них, если они согласуются "
                        "с вопросом. Если нет — ответь по существу и не выдумывай.")
            q = (st["q"] + "\n\n"
                 "Сначала кратко (1–2 предложения) опиши ход мысли, "
                 "затем с новой строки напиши: ОТВЕТ: <твой ответ>")
            return q, note, True
        prev = st["best"] or ""
        return (f"Вопрос: {st['q']}\nЧерновик ответа: {prev}\n"
                "Проверь черновик: верен ли он и отвечает ли на вопрос. Если да — "
                "повтори его. Если нет — дай исправленный ответ на русском, 1–3 предложения."
                ), "", False

    def feed(self, st, text):
        text = (text or "").strip()
        if text:
            rationale, answer = _split_rationale(text)
            st["rationale"] = rationale
            score = self.judge(st["seeds"], answer)
            if st["best"] is None or score >= st["best_score"]:
                st["best"], st["best_score"] = answer, score
        st["stage"] += 1
        return st["best"] is not None and st["stage"] <= st["revise_rounds"]

    # ---------- 4. итог ----------

    def final(self, st):
        """(текст, оценка, from_lm) или None. Граф здесь не обучается."""
        if st["best"] is not None:
            return st["best"], st["best_score"], True
        if st["thought"] is not None:
            return " ".join(st["thought"].words), st["thought"].score, False
        if st.get("greet"):
            return GREET_REPLY, 0.0, False
        return None

    # ---------- 5. проактивность ----------

    def unknown_reply(self, st):
        """Ответ, когда ответа нет: назвать непонятные слова и спросить."""
        unknown = []
        for w in tokens(st["q"]):
            if w not in self.brain.words and w not in unknown and len(w) > 2:
                unknown.append(w)
        if unknown:
            return ("Я не знаю слов: " + ", ".join(unknown[:3]) + ". "
                    "Объясните, о чём речь, или ответьте сами: /teach ответ.")
        return ("Я пока не уверена в ответе. Если знаете ответ, напишите /teach ответ "
                "— я запомню его.")

    def low_confidence_hint(self, score):
        """Подсказка к неуверенному ответу: попросить оценку пользователя."""
        if score >= LEARN_MIN:
            return ""
        return "(Уверенность низкая. Верно — /good, неверно — /teach ответ.)"

    # ---------- 6. обучение (только по награде) ----------

    def learn(self, question, words, confidence=1.0):
        q = tokens(question)
        self.brain.train_words(q + words, weight=confidence)
        self.brain.associate(q, words, weight=confidence)
        self.brain._reinforce_answer(words, weight=confidence)

    def distill_rationale(self, st, confidence=1.0):
        """Дистиллирует ход мысли LM в граф, если он согласуется с вопросом.
        Вызывается только после награды /good."""
        rationale = st.get("rationale", "")
        if not rationale or not st["seeds"]:
            return
        rw = tokens(rationale)
        if not rw:
            return
        r_score = self.mind.support(st["seeds"], rw)
        if r_score < HINT_MIN_SUPPORT:
            return
        qw = tokens(st["q"])
        w = min(1.0, r_score * confidence)
        self.brain.train_words(qw + rw, weight=w)
        self.brain.associate(qw, rw, weight=w)
        self.brain.adapt(qw + rw, confidence=w)
