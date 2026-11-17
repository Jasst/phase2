# -*- coding: utf-8 -*-
"""BrainNet — сеть импульсов.

Мысль (thought) — распределение массы по ансамблям. Она переживает ходы:
вопрос не стартует с нуля, а смешивается с предыдущей мыслью (фокус). Перед ответом
импульс несколько шагов устаканивается внутри сети (settle). Во время ответа
сеть сравнивает собственное предсказание следующего состояния (шаг цепи) с тем,
что реально произошло (самопредсказание), и отдаёт уверенность.

learn()   — онлайн-урок: эмбеддинги, ансамбли, связи, дистилляция в выходную голову.
respond() — ответ сети с учётом мысли; тексты не учат сеть (обучение — отдельным уроком).
teach()   — урок и повтор старого опыта против забывания.
"""
import numpy as np

from .chain import Chain
from .config import Config
from .cortex import Cortex
from .vocab import EOS_ID, Vocab, detokenize, split_sentences, tokenize


class Reply:
    __slots__ = ("text", "path", "confidence", "self_error", "focus", "known")

    def __init__(self, text, path, confidence=0.0, self_error=None, focus=None, known=True):
        self.text = text
        self.path = path            # [(слово, ансамбль, масса импульса), ...]
        self.confidence = confidence
        self.self_error = self_error
        self.focus = focus or []    # топ ансамблей мысли: [(ансамбль, масса)]
        self.known = known          # знает ли сеть хоть одно слово вопроса


class BrainNet:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.rng = np.random.default_rng(self.cfg.seed)
        self.vocab = Vocab()
        self.chain = Chain(self.cfg.max_clusters)
        self.cortex = Cortex(self.cfg, self.rng, chain=self.chain)
        self.thought = None         # текущая мысль (распределение по ансамблям), переживает ходы
        self.self_errors = []       # история ошибок самопредсказания
        self.buffer = []            # опыт: [(вопрос, ответ, вес)] для повторения
        self.updates = 0            # все внутренние обновления (включая повтор опыта)
        self.lessons = 0            # реальные уроки: teach() и предобучение, без повтора
        self.last_loss = None

    # ---------- вспомогательное ----------

    def _pad(self, v):
        cap = self.cortex.cap
        if v is None:
            return np.zeros(cap)
        if v.shape[0] == cap:
            return v
        out = np.zeros(cap)
        out[:min(cap, v.shape[0])] = v[:cap]
        return out

    def _dist(self, ids):
        """Распределение массы по ансамблям для набора нейронов (якорь вопроса)."""
        cx = self.cortex
        v = np.zeros(cx.cap)
        for i in ids:
            k = cx.cluster_of(i)
            if k >= 0:
                v[k] += 1.0
        tot = v.sum()
        return v / tot if tot > 0 else None

    @staticmethod
    def _norm(v):
        t = v.sum()
        return v / t if t > 1e-12 else v

    def _advance(self, s, k_e, anchor, visited):
        """Один шаг импульса: из ансамбля k_e (с подмесью вопроса) по цепи."""
        cfg = self.cfg
        visited *= cfg.visited_decay
        if k_e >= 0:
            cur = np.zeros(self.cortex.cap)
            cur[k_e] = 1.0
            mixed = (1 - cfg.anchor_mix) * cur + cfg.anchor_mix * anchor
        else:
            mixed = anchor
        nxt = self.chain.hop(mixed, visited, cfg.chain_topk)
        if nxt is None:                       # цепь оборвалась — импульс возвращается к вопросу
            nxt = anchor.copy()
        visited += (nxt > 0)
        return nxt

    def _focus(self, anchor):
        """Новая мысль = фокус на вопросе, смешанный с прошлой мыслью."""
        g = self.cfg.focus_gain
        if self.thought is None:
            return anchor.copy()
        prev = self._pad(self.thought)
        return self._norm((1 - g) * prev + g * anchor)

    def _settle(self, s, anchor):
        """Внутренние шаги перед ответом: мысль устаканивается, без вывода слов."""
        visited = np.zeros(self.cortex.cap)
        for _ in range(self.cfg.settle_steps):
            nxt = self.chain.hop(s, visited, self.cfg.chain_topk)
            if nxt is None:
                break
            visited = visited * self.cfg.visited_decay + (nxt > 0)
            s = self._norm(0.8 * nxt + 0.2 * anchor)
        return s

    def top_focus(self, k=5):
        if self.thought is None:
            return []
        t = self._pad(self.thought)
        idx = np.argsort(-t)[:k]
        return [(int(i), round(float(t[i]), 3)) for i in idx if t[i] > 1e-4]

    # ---------- обучение ----------

    def learn(self, question, answer, weight=1.0):
        """Один онлайн-урок. weight < 0 — отрицательная обратная связь.
        Возвращает средний loss дистилляции или None, если урок пуст."""
        cfg, cx = self.cfg, self.cortex
        q_tok, a_tok = tokenize(question), tokenize(answer)
        if not q_tok or not a_tok:
            return None
        q = self.vocab.encode(q_tok, grow=True)
        a = self.vocab.encode(a_tok, grow=True) + [EOS_ID]
        V = len(self.vocab)
        cx.ensure_words(V)
        self.vocab.bump(q + a)
        cx.attach_seq(q)
        cx.attach_seq(a)

        cx.cooccur(q, weight, V)
        cx.cooccur(a, weight, V)
        cx.update_profiles(q, weight)
        cx.update_profiles(a, weight)
        for keep, drop in cx.merge_step(q + a, weight):
            self.chain.merge(keep, drop)
            if self.thought is not None and drop < self.thought.shape[0]:
                # мысль переезжает вместе с ансамблем: масса drop уходит в keep
                self.thought[keep] += self.thought[drop]
                self.thought[drop] = 0.0
        cx.refresh()
        q_cl = [cx.cluster_of(i) for i in q]
        a_cl = [cx.cluster_of(i) for i in a]
        K = cx.cap

        for k in set(q_cl):
            for m in set(a_cl[:8]):
                self.chain.add(k, m, cfg.chain_link * weight)
        for i, k in enumerate(a_cl):
            for d in range(1, cfg.chain_window + 1):
                if i + d < len(a_cl):
                    self.chain.add(k, a_cl[i + d], weight / d)
        self.chain.decay(cfg.chain_forget)

        anchor = self._dist(q)
        s, visited, last = anchor.copy(), np.zeros(K), q[-1]
        total = 0.0
        for tgt in a:
            ctx = (cfg.alpha * (cx.M.T @ s) + (1 - cfg.alpha) * cx.E[last]).astype(np.float32)
            loss, grad = cx.distill(ctx, tgt, V, cfg.lr_out * weight, cfg.smooth)
            cx.E[last] -= (cfg.lr_emb * weight * (1 - cfg.alpha)) * grad
            total += loss
            s = self._advance(s, cx.cluster_of(tgt), anchor, visited)
            last = tgt

        self.updates += 1
        self.last_loss = total / len(a)
        return self.last_loss

    def teach(self, question, answer, weight=1.0, replay=True):
        """Урок и повторение старого опыта, чтобы сеть не забывала."""
        loss = self.learn(question, answer, weight)
        if loss is None:
            return None
        self.lessons += 1
        self.buffer.append((question, answer, weight))
        del self.buffer[:-self.cfg.replay_size]
        if replay and len(self.buffer) > 1:
            for idx in self.rng.integers(0, len(self.buffer) - 1, size=self.cfg.replay_n):
                q, a, w = self.buffer[int(idx)]
                self.learn(q, a, w * self.cfg.replay_weight)
        return loss

    def pretrain(self, text, weight=0.5, epochs=None):
        """Предобучение на тексте: предыдущее предложение — вопрос, следующее — ответ."""
        sents = split_sentences(text)
        done = 0
        for _ in range(epochs or self.cfg.pretrain_epochs):
            for prev, cur in zip(sents, sents[1:]):
                if self.learn(prev, cur, weight) is not None:
                    done += 1
        self.lessons += done
        return done

    # ---------- ответ ----------

    def respond(self, question, max_tokens=None, temp=None, context=None):
        """Ответ сети с учётом мысли. Обновляет мысль и журнал самопредсказания.

        context — текст последних мыслей (внутренний нарратив): он подмешивается
        в якорь вопроса, так что накопленный нарратив участвует в ответе.
        """
        cfg, cx = self.cfg, self.cortex
        max_tokens = max_tokens or cfg.max_answer
        temp = cfg.temperature if temp is None else temp
        q = self.vocab.encode(tokenize(question), grow=False)
        if not q:
            return Reply("", [], 0.0, None, self.top_focus(), known=False)
        cx.refresh()
        anchor = self._dist(q)
        if context and cfg.thought_mix > 0:
            c = self._dist(self.vocab.encode(tokenize(context), grow=False))
            if c is not None:
                anchor = self._norm(anchor + cfg.thought_mix * c)

        focus = self._focus(anchor)
        settled = self._settle(focus, anchor)
        prediction = self.chain.hop(settled, np.zeros(cx.cap), cfg.chain_topk)   # что сеть ждёт дальше
        # говорим из фокуса (сохраняет первое слово вопроса), уверенность берём из устаканенной мысли
        s = focus

        K, V = cx.cap, len(self.vocab)
        visited, last = np.zeros(K), q[-1]
        out, path, recent = [], [], []
        act = np.zeros(K)
        for t in range(max_tokens):
            ctx = (cfg.alpha * (cx.M.T @ s) + (1 - cfg.alpha) * cx.E[last]).astype(np.float32)
            logits = (cx.O[:V] @ ctx).astype(np.float64)
            freq = np.log1p(np.asarray(self.vocab.counts[:V], dtype=np.float64))
            logits -= cfg.freq_penalty * freq
            mass = np.where(cx.label[:V] >= 0, s[np.maximum(cx.label[:V], 0)], 0.0)
            logits += cfg.ensemble_gain * np.log(mass + 1e-3)
            for r in set(recent[-cfg.no_repeat:]):
                logits[r] = -np.inf if r != EOS_ID else logits[r]
            if t == 0:
                logits[EOS_ID] = -np.inf
                for i in range(1, V):
                    if not self.vocab.words[i][:1].isalnum():
                        logits[i] = -np.inf
            if temp <= 0:
                tok = int(np.argmax(logits))
            else:
                z = (logits - logits.max()) / temp
                p = np.exp(z)
                p /= p.sum()
                tok = int(self.rng.choice(V, p=p))
            if tok == EOS_ID:
                break
            out.append(tok)
            recent.append(tok)
            k_e = cx.cluster_of(tok)
            if k_e >= 0:
                act[k_e] += 1.0
            s = self._advance(s, k_e, anchor, visited)
            path.append((self.vocab.words[tok], int(np.argmax(s)), round(float(s.max()), 3)))
            last = tok

        # самопредсказание: предсказанный шаг цепи против реально пройденных ансамблей
        self_error = None
        if prediction is not None and act.sum() > 0:
            a_n = act / np.linalg.norm(act)
            p_n = prediction / (np.linalg.norm(prediction) + 1e-12)
            self_error = round(float(1.0 - a_n @ p_n), 4)
            self.self_errors.append(self_error)
            del self.self_errors[:-200]
        # мысль после ответа: её переписывает то, что сеть сказала
        if act.sum() > 0:
            told = self._norm(act)
            self.thought = self._norm((1 - cfg.thought_decay) * settled + cfg.thought_decay * told)
        else:
            self.thought = settled
        confidence = round(float(settled.max()), 4)
        text = detokenize([self.vocab.words[i] for i in out]) if confidence >= cfg.min_confidence else ""
        return Reply(text, path, confidence, self_error, self.top_focus(), known=True)

    # ---------- спонтанная мысль (внутренний нарратив) ----------

    def _start_thought(self):
        """Отправная точка нарратива, если мысли ещё нет: последний опыт, иначе частоты словаря."""
        if self.buffer:
            q, a, _w = self.buffer[-1]
            d = self._dist(self.vocab.encode(tokenize(q + " " + a), grow=False))
            if d is not None:
                return d
        V = len(self.vocab)
        d = np.zeros(self.cortex.cap)
        for i in range(1, V):
            k = self.cortex.cluster_of(i)
            if k >= 0:
                d[k] += float(self.vocab.counts[i])
        tot = d.sum()
        return d / tot if tot > 0 else None

    def think(self, max_words=8):
        """Спонтанная мысль: сеть сама продолжает свой нарратив.

        Импульс блуждает по цепи от текущей мысли (без вопроса), на каждом шаге
        из активных ансамблей выбирается слово. Если мысли ещё нет — отправной
        точкой становится последний опыт из буфера. Тупик цепи не останавливает
        нарратив: слова выбираются из самой мысли. Полученная короткая фраза
        переписывает мысль (thought_decay) — нарратив продолжается.
        Возвращает текст мысли или None, если думать пока не о чем.
        """
        cfg, cx = self.cfg, self.cortex
        V = len(self.vocab)
        if V < 3:
            return None
        if self.thought is None:
            self.thought = self._start_thought()
            if self.thought is None:
                return None
        anchor = self._norm(self._pad(self.thought))
        s, visited, out_ids = anchor.copy(), np.zeros(cx.cap), []
        for t in range(max(1, int(max_words))):
            nxt = self.chain.hop(s, visited, cfg.chain_topk)
            if nxt is None:
                nxt = anchor.copy()        # тупик: думаем от самой мысли
            visited = visited * cfg.visited_decay + (nxt > 0)
            pick = self._norm(0.6 * nxt + 0.4 * self._pad(s))
            mass = np.where(cx.label[:V] >= 0, pick[np.maximum(cx.label[:V], 0)], 0.0)
            ctx = (cfg.alpha * (cx.M.T @ s)).astype(np.float32)
            logits = (cx.O[:V] @ ctx).astype(np.float64)
            logits += cfg.ensemble_gain * np.log(mass + 1e-3)
            freq = np.log1p(np.asarray(self.vocab.counts[:V], dtype=np.float64))
            logits -= cfg.freq_penalty * freq
            for r in set(out_ids[-cfg.no_repeat:]):
                logits[r] = -np.inf
            if t == 0:
                logits[EOS_ID] = -np.inf
                for i in range(1, V):
                    if not self.vocab.words[i][:1].isalnum():
                        logits[i] = -np.inf
            if not np.isfinite(logits).any():
                break
            if cfg.temperature <= 0:
                tok = int(np.argmax(logits))
            else:
                z = (logits - logits.max()) / cfg.temperature
                p = np.exp(z)
                p /= p.sum()
                tok = int(self.rng.choice(V, p=p))
            if tok == EOS_ID:
                break
            out_ids.append(tok)
            s = self._advance(s, cx.cluster_of(tok), anchor, visited)
        if not out_ids:
            return None
        text = detokenize([self.vocab.words[i] for i in out_ids])
        # мысль переписывается тем, о чём сеть «подумала» — нарратив движется сам
        act = np.zeros(cx.cap)
        for tok in out_ids:
            k = cx.cluster_of(tok)
            if k >= 0:
                act[k] += 1.0
        if act.sum() > 0:
            told = self._norm(act)
            self.thought = self._norm((1 - cfg.thought_decay) * self._pad(self.thought) + cfg.thought_decay * told)
        self.updates += 1
        return text

    # ---------- диагностика ----------

    def stats(self):
        return {"слов": len(self.vocab) - 1, "ансамблей": len(self.cortex.members),
                "связей": self.chain.edges(), "уроков": self.lessons,
                "в_памяти_опыта": len(self.buffer), "ёмкость": self.cortex.cap}

    def clusters(self):
        groups = [(k, [self.vocab.words[i] for i in ws])
                  for k, ws in self.cortex.members.items() if ws]
        return sorted(groups, key=lambda kv: (-len(kv[1]), kv[0]))

    def self_error_avg(self, last=20):
        xs = self.self_errors[-last:]
        return round(float(np.mean(xs)), 4) if xs else None
