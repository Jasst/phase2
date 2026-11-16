# -*- coding: utf-8 -*-
"""BrainNet — сеть импульсов: нейроны, ансамбли, цепи и онлайн-дистилляция.

learn()   — один урок: вопрос → ответ учителя. Обновляет эмбеддинги, ансамбли,
            направленные связи и выходную голову (дистилляция).
respond() — ответ: импульс стартует с ансамблей вопроса и идёт по направленным
            связям; на каждом шаге слово выбирается из выходной головы.
teach()   — урок и повторение старого опыта (replay) против забывания.
"""
import numpy as np

from .chain import Chain
from .config import Config
from .cortex import Cortex
from .vocab import EOS_ID, Vocab, detokenize, split_sentences, tokenize


class Reply:
    __slots__ = ("text", "path")

    def __init__(self, text, path):
        self.text = text
        self.path = path        # [(слово, номер ансамбля, масса импульса), ...]


class BrainNet:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.rng = np.random.default_rng(self.cfg.seed)
        self.vocab = Vocab()
        self.cortex = Cortex(self.cfg, self.rng)
        self.chain = Chain(self.cfg.max_clusters)
        self.buffer = []        # опыт: [(вопрос, ответ, вес)] для повторения
        self.updates = 0
        self.last_loss = None

    # ---------- вспомогательное ----------

    def _qvec(self, ids):
        """Вектор вопроса: нормированное среднее входных векторов его слов."""
        v = self.cortex.E[ids].mean(axis=0)
        n = float(np.linalg.norm(v))
        return (v / n if n > 1e-9 else v).astype(np.float32)

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

    def _context(self, s, qvec, last):
        """Контекст шага: импульс (ансамбли) + вопрос + предыдущее слово."""
        cfg, cx = self.cfg, self.cortex
        w_last = 1.0 - cfg.alpha - cfg.beta
        return (cfg.alpha * (cx.M.T @ s) + cfg.beta * qvec
                + w_last * cx.E[last]).astype(np.float32)

    def _advance(self, s, k_e, anchor, visited):
        """Один шаг импульса: из ансамбля k_e (с подмесью вопроса) по цепи."""
        cfg = self.cfg
        visited *= cfg.visited_decay
        if k_e >= 0:
            cur = np.zeros(cfg.max_clusters)
            cur[k_e] = 1.0
            mixed = (1 - cfg.anchor_mix) * cur + cfg.anchor_mix * anchor
        else:
            mixed = anchor
        nxt = self.chain.hop(mixed, visited, cfg.chain_topk)
        if nxt is None:                       # цепь оборвалась — импульс возвращается к вопросу
            nxt = anchor.copy()
        visited += (nxt > 0)
        return nxt

    # ---------- обучение ----------

    def learn(self, question, answer, weight=1.0):
        """Один онлайн-урок. weight < 0 — отрицательная обратная связь (/bad).
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

        # 1. нейроны, встречающиеся рядом, сближаются и сливаются в ансамбли
        cx.cooccur(q, weight, V)
        cx.cooccur(a, weight, V)
        cx.update_profiles(q, weight)
        cx.update_profiles(a, weight)
        for keep, drop in cx.merge_step(q + a, weight):
            self.chain.merge(keep, drop)
        cx.refresh()
        q_cl = [cx.cluster_of(i) for i in q]
        a_cl = [cx.cluster_of(i) for i in a]
        K = cfg.max_clusters

        # 2. направленные связи: вопрос → начало ответа, затем по порядку внутри ответа
        for k in set(q_cl):
            for m in set(a_cl[:8]):
                self.chain.add(k, m, cfg.chain_link * weight)
        for i, k in enumerate(a_cl):
            for d in range(1, cfg.chain_window + 1):
                if i + d < len(a_cl):
                    self.chain.add(k, a_cl[i + d], weight / d)
        self.chain.decay(cfg.chain_forget)

        # 3. дистилляция с принуждением учителя: импульс идёт так же, как при
        #    генерации, но по настоящим словам учителя
        anchor = self._dist(q)
        qvec = self._qvec(q)
        s, visited, last = anchor.copy(), np.zeros(K), q[-1]
        total = 0.0
        for tgt in a:
            ctx = self._context(s, qvec, last)
            loss, grad = cx.distill(ctx, tgt, V, cfg.lr_out * weight, cfg.smooth)
            cx.E[last] -= (cfg.lr_emb * weight * (1 - cfg.alpha - cfg.beta)) * grad
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
        self.buffer.append((question, answer, weight))
        del self.buffer[:-self.cfg.replay_size]
        if replay and len(self.buffer) > 1:
            for idx in self.rng.integers(0, len(self.buffer) - 1, size=self.cfg.replay_n):
                q, a, w = self.buffer[int(idx)]
                self.learn(q, a, w * self.cfg.replay_weight)
        return loss

    def pretrain(self, text, weight=0.5, epochs=None):
        """Предобучение на тексте: предыдущее предложение — вопрос, следующее — ответ.
        Несколько проходов нужны выходной голове, чтобы она успела сойтись."""
        sents = split_sentences(text)
        done = 0
        for _ in range(epochs or self.cfg.pretrain_epochs):
            for prev, cur in zip(sents, sents[1:]):
                if self.learn(prev, cur, weight) is not None:
                    done += 1
        return done

    # ---------- ответ ----------

    def respond(self, question, max_tokens=None, temp=None):
        """Генерация: импульс идёт по направленным цепям, слово выбирается на каждом шаге."""
        cfg, cx = self.cfg, self.cortex
        max_tokens = max_tokens or cfg.max_answer
        temp = cfg.temperature if temp is None else temp
        q = self.vocab.encode(tokenize(question), grow=False)
        if not q:
            return Reply("", [])
        cx.refresh()
        anchor = self._dist(q)
        if anchor is None:
            return Reply("", [])
        K, V = cfg.max_clusters, len(self.vocab)
        qvec = self._qvec(q)
        s, visited, last = anchor.copy(), np.zeros(K), q[-1]
        out, path, recent = [], [], []
        for t in range(max_tokens):
            ctx = self._context(s, qvec, last)
            logits = (cx.O[:V] @ ctx).astype(np.float64)
            freq = np.log1p(np.asarray(self.vocab.counts[:V], dtype=np.float64))
            logits -= cfg.freq_penalty * freq
            # импульс → слово: выбираются слова из тех ансамблей, через которые сейчас идёт импульс
            lab = cx.label[:V]
            mass = np.where(lab >= 0, s[np.maximum(lab, 0)], 0.0)
            logits += cfg.ensemble_gain * np.log(mass + 1e-3)
            for r in set(recent[-cfg.no_repeat:]):
                logits[r] = -np.inf if r != EOS_ID else logits[r]
            if t == 0:                       # ответ не начинается с EOS или знака препинания
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
            s = self._advance(s, cx.cluster_of(tok), anchor, visited)
            path.append((self.vocab.words[tok], int(np.argmax(s)), round(float(s.max()), 3)))
            last = tok
        return Reply(detokenize([self.vocab.words[i] for i in out]), path)

    # ---------- диагностика ----------

    def stats(self):
        return {"слов": len(self.vocab) - 1, "ансамблей": len(self.cortex.members),
                "связей": self.chain.edges(), "уроков": self.updates,
                "в_памяти_опыта": len(self.buffer)}

    def clusters(self):
        """Ансамбли: [(номер, [слова...])], от больших к малым."""
        groups = [(k, [self.vocab.words[i] for i in ws])
                  for k, ws in self.cortex.members.items() if ws]
        return sorted(groups, key=lambda kv: (-len(kv[1]), kv[0]))
