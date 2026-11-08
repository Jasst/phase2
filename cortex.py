# -*- coding: utf-8 -*-
"""Слоистая обработка: три слоя с разведёнными ролями.

Прямой слой (FeedForward) только читает граф: переносит активность слов вверх
по слоям нейронов (вход -> ассоциации -> интегратор -> речь), гасит хабы и
усиливает группы, которым принадлежат сильные слова. Связей не пишет.

Рекуррентный слой (Recurrent) гоняет активность туда-обратно, пока она не
стабилизируется. Это единственный слой, который создаёт новые связи: между
словами, которые возбуждаются вместе и ещё не связаны. Бюджет ограничен.

Внутренний слой (Internal) строит картину мира: группы слов, их сплочённость,
мосты между группами. По пробелам формулирует вопросы к пользователю. Он
читает граф и пишет только в свою картину, связей не меняет.

Контракт слоёв проверяют тесты (tests/test_cortex.py): прямой и внутренний
слои не меняют brain.links.
"""
import math
import random

from mind import MIN_WORD_LEN, STOPWORDS, cosine, key

DEPTH_FF = 2            # шагов прямого прохода
DEPTH_REC = 2           # шагов одного цикла рекуррентной циркуляции
CYCLES_REC = 4          # максимум циклов рекуррентного слоя
EPS_REC = 1e-3          # стабилизация: косинус между циклами >= 1 - EPS_REC
TOP_REC = 8             # сколько самых активных слов участвуют в создании связей
NEW_MAX = 6             # бюджет новых связей за один проход
HEBB_REC = 0.1          # укрепление уже существующей связи за совместную активность
CREATE_T = 1.0          # P(создать) = 1 - exp(-T * s), как в core.learn_sequence
NEW_W = 0.4             # вес новой связи (слабее обученной, чтобы не доминировать)
HUB_POW = 0.5           # гашение хабов: вклад связи делится на (1 + степень)^HUB_POW
GROUP_GAIN = 0.8        # усиление членов доминирующей группы прямым слоем
COHESION_LOW = 0.15     # группа слабее этого порога — неясная тема
MIN_DEG = 2             # слово с меньшим числом связей — пробел знаний
SEED_MAX = 6            # сколько последних слов потока берёт фоновый проход


def content(words):
    """Содержательные слова: без служебных и коротких (как в mind.clean_words)."""
    return [w for w in words if w not in STOPWORDS and len(w) >= MIN_WORD_LEN]


def propagate(brain, state, depth, directed, adj=None):
    """Перенос активности по связям.

    directed=True  — только вверх по слоям нейронов (dst.layer >= src.layer);
    directed=False — в обе стороны.
    Масса узла делится между исходящими связями пропорционально весу, поэтому
    суммарная масса не растёт. Хабы (много связей) гасятся. Возвращает
    активность только словесных нейронов.
    """
    adj = adj if adj is not None else brain.adjacency()
    mass = {}
    for w, a in state.items():
        nid = brain.words.get(w)
        if nid is not None and a > 0:
            mass[nid] = mass.get(nid, 0.0) + a
    out = {}
    for _ in range(depth):
        nxt = {}
        for nid, a in mass.items():
            src = brain.neurons.get(nid)
            if src is None:
                continue
            outs = []
            for j, lw in adj.get(nid, ()):
                m = brain.neurons.get(j)
                if m is None or j == nid:
                    continue
                if directed and m.layer < src.layer:
                    continue
                hub = (1.0 + len(adj.get(j, ()))) ** -HUB_POW
                outs.append((j, lw * hub))
            z = sum(v for _, v in outs)
            if z <= 0:
                continue
            for j, v in outs:
                nxt[j] = nxt.get(j, 0.0) + a * v / z
        mass = nxt
        for j, a in mass.items():
            w = brain.neurons[j].word
            if w:
                out[w] = out.get(w, 0.0) + a
    return out


# ---------- слой 1: прямой ----------

def forward(brain, state, depth=DEPTH_FF):
    """Прямой слой: обработка вверх по слоям плюс усиление доминирующей группы.
    Граф не меняется."""
    out = propagate(brain, state, depth, directed=True)
    if not out:
        return {}
    res = dict(out)
    gsum = {}
    for w, a in out.items():
        g = brain.wgroup.get(w)
        if g is not None:
            gsum[g] = gsum.get(g, 0.0) + a
    if gsum:
        best = max(gsum, key=lambda g: (gsum[g], -g))
        share = gsum[best] / sum(out.values())
        top_val = max(out.values())
        for w, g in brain.wgroup.items():
            if g == best:
                res[w] = res.get(w, 0.0) + GROUP_GAIN * share * top_val
    return res


# ---------- слой 2: рекуррентный ----------

def recurrent(brain, state, rng, cycles=CYCLES_REC):
    """Рекуррентный слой: активность циркулирует туда-обратно до стабилизации.
    Пары слов, которые возбуждаются вместе, укрепляются (если связь уже есть)
    или создаются заново с вероятностью 1 - exp(-T * s). Возвращает
    (созданные пары, финальная активность)."""
    cur = {w: a for w, a in state.items() if w in brain.words and a > 0}
    if not cur:
        return [], {}
    adj = brain.adjacency()
    pairs = {}
    for _ in range(cycles):
        back = propagate(brain, cur, DEPTH_REC, directed=False, adj=adj)
        mix = {}
        for w, a in cur.items():
            mix[w] = mix.get(w, 0.0) + 0.5 * a
        for w, a in back.items():
            mix[w] = mix.get(w, 0.0) + 0.5 * a
        mx = max(mix.values()) if mix else 0.0
        if mx <= 0:
            break
        mix = {w: v / mx for w, v in mix.items() if v / mx > 1e-3}
        # служебные слова не создают связей: «с», «от», «и» есть почти везде
        top = content(w for w, _ in sorted(mix.items(), key=lambda kv: (-kv[1], kv[0])))[:TOP_REC]
        for i, x in enumerate(top):
            for y in top[i + 1:]:
                k = key(x, y)
                pairs[k] = pairs.get(k, 0.0) + min(mix[x], mix[y])
        stable = cosine(mix, cur) >= 1.0 - EPS_REC
        cur = mix
        if stable:
            break

    created = []
    for (x, y), s in sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0])):
        if len(created) >= NEW_MAX:
            break
        nx, ny = brain.words.get(x), brain.words.get(y)
        if nx is None or ny is None or nx == ny:
            continue
        k = key(nx, ny)
        if k in brain.links:
            brain.links[k] = min(2.0, brain.links[k] + HEBB_REC * s)
        elif rng.random() < 1.0 - math.exp(-CREATE_T * s):
            brain.connect_words(brain.neurons[nx], brain.neurons[ny])
            if k in brain.links:
                brain.links[k] = NEW_W
            created.append((x, y))
    return created, cur


# ---------- слой 3: внутренний (картина мира) ----------

class WorldModel:
    """Внутренний слой: картина мира из групп слов и связей между ними.
    Читает граф и группы, пишет только в собственное состояние. По пробелам
    формулирует вопросы; каждый вопрос задаётся один раз."""

    def __init__(self):
        self.picture = {}      # группа -> {words, size, cohesion}
        self.bridges = set()   # пары групп, между которыми есть связи
        self.asked = set()     # уже заданные вопросы

    def update(self, brain):
        id2w = {n.id: n.word for n in brain.neurons.values() if n.word}
        members = {}
        for w, g in brain.wgroup.items():
            members.setdefault(g, []).append(w)
        inside = {}
        bridges = set()
        for (a, b), lw in brain.links.items():
            wa, wb = id2w.get(a), id2w.get(b)
            if wa is None or wb is None:
                continue
            ga, gb = brain.wgroup.get(wa), brain.wgroup.get(wb)
            if ga is None or gb is None:
                continue
            if ga == gb:
                inside[ga] = inside.get(ga, 0.0) + lw
            else:
                bridges.add(frozenset((ga, gb)))
        pic = {}
        for g, ws in members.items():
            n = len(ws)
            pairs = n * (n - 1) / 2.0 or 1.0
            pic[g] = {
                "words": sorted(ws, key=lambda w: (-brain.word_seen.get(w, 0), w)),
                "size": n,
                "cohesion": min(1.0, inside.get(g, 0.0) / pairs),
            }
        self.picture = pic
        self.bridges = bridges
        return pic

    def questions(self, brain, limit=1):
        """Вопросы о пробелах: слово, которое часто встречается, но почти без связей;
        группа с неясной сплочённостью; две крупные группы без моста."""
        self.update(brain)
        deg = {}
        for a, b in brain.links:
            deg[a] = deg.get(a, 0) + 1
            deg[b] = deg.get(b, 0) + 1
        cands = []
        for w, nid in brain.words.items():
            seen = brain.word_seen.get(w, 0)
            if w in STOPWORDS or len(w) < MIN_WORD_LEN:
                continue
            if seen >= 1 and deg.get(nid, 0) < MIN_DEG:
                cands.append((2.0 * seen + 1.0, f"Что означает «{w}» и с чем его обычно связывают?"))
        for info in self.picture.values():
            words = content(info["words"])
            if info["size"] >= 3 and info["cohesion"] < COHESION_LOW and len(words) >= 3:
                a, b, c = words[:3]
                cands.append((float(info["size"]), f"Как связаны «{a}», «{b}» и «{c}»?"))
        named = [g for g in self.picture if content(self.picture[g]["words"])]
        big = sorted(named, key=lambda g: (-self.picture[g]["size"], g))[:4]
        for i, ga in enumerate(big):
            for gb in big[i + 1:]:
                if frozenset((ga, gb)) not in self.bridges:
                    wa = content(self.picture[ga]["words"])[0]
                    wb = content(self.picture[gb]["words"])[0]
                    cands.append((1.0, f"Есть ли связь между «{wa}» и «{wb}»?"))
        cands.sort(key=lambda c: (-c[0], c[1]))
        out = []
        for _, text in cands:
            if text in self.asked:
                continue
            self.asked.add(text)
            out.append(text)
            if len(out) >= limit:
                break
        return out


# ---------- оркестратор ----------

class Cortex:
    """Связывает слои: прямой -> рекуррентный -> внутренний.
    Единственный слой, создающий связи, — рекуррентный."""

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.world = WorldModel()
        self.last = {"ff": {}, "rec": {}, "created": []}

    def think(self, brain, seeds):
        state = {w: 1.0 for w in seeds if w in brain.words}
        if not state:
            return self.last
        ff = forward(brain, state)
        start = dict(state)
        for w, a in ff.items():
            start[w] = start.get(w, 0.0) + a
        created, rec = recurrent(brain, start, self.rng)
        self.world.update(brain)
        self.last = {"ff": ff, "rec": rec, "created": created}
        return self.last

    def idle(self, brain, stream_words, limit=1):
        """Фоновый шаг: прогоняет последние слова потока и возвращает вопросы."""
        seeds = [w for w in stream_words if w in brain.words][-SEED_MAX:]
        if len(seeds) >= 2:
            self.think(brain, seeds)
        return self.world.questions(brain, limit=limit)

    def report(self, brain):
        self.world.update(brain)
        pic = self.world.picture
        weak = sum(1 for i in pic.values()
                   if i["size"] >= 3 and i["cohesion"] < COHESION_LOW and content(i["words"]))
        return [
            f"внутренний слой: групп {len(pic)}, слабых {weak}, мостов {len(self.world.bridges)}",
            f"рекуррентный слой в последнем проходе: новых связей {len(self.last['created'])}",
            f"прямой слой: активных слов {len(self.last['ff'])}",
            f"вопросов задано: {len(self.world.asked)}",
        ]
