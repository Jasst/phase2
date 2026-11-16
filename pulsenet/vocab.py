# -*- coding: utf-8 -*-
"""Токенизация и словарь. Каждое слово — нейрон; словарь растёт при обучении."""
import re

EOS = "<eos>"
EOS_ID = 0
_TOKEN = re.compile(r"[^\W\d_]+|\d+|[.,!?;:]")
_SENT = re.compile(r"(?<=[.!?…])\s+|\n+")


def tokenize(text):
    return [m.group(0).lower() for m in _TOKEN.finditer(text or "")]


def split_sentences(text):
    return [s.strip() for s in _SENT.split(text or "") if s.strip()]


def detokenize(words):
    text = re.sub(r"\s+([.,!?;:])", r"\1", " ".join(words))
    return text[:1].upper() + text[1:]


class Vocab:
    def __init__(self, words=None, counts=None):
        self.words = [EOS]
        self.index = {EOS: EOS_ID}
        self.counts = [0]
        for w in (words or [])[1:]:
            self._add(w)
        if counts is not None:
            self.counts = list(counts)

    def __len__(self):
        return len(self.words)

    def _add(self, word):
        i = len(self.words)
        self.words.append(word)
        self.index[word] = i
        self.counts.append(0)
        return i

    def bump(self, ids):
        """Учёт частот: частые слова (точка, «и») не должны выигрывать по инерции."""
        for i in ids:
            self.counts[i] += 1

    def encode(self, tokens, grow):
        """Слова → id. Неизвестные слова добавляются (grow=True) или пропускаются."""
        ids = []
        for t in tokens:
            i = self.index.get(t)
            if i is None and grow:
                i = self._add(t)
            if i is not None:
                ids.append(i)
        return ids
