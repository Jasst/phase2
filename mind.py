"""Поток мыслей: два полушария обмениваются полями.
Правое — хаотичное блуждание (шум, высокая температура).
Левое — аналитический выбор по полю, связям, биграммам и группам.
Каждое слово одного полушария попадает в поле другого (зеркало).
tick() работает в фоне, deliberate() отвечает на вопрос пользователя."""

import collections
import math
import random

THINK_EVERY = 0.4        # сек между шагами фонового мышления
STREAM_LEN = 160         # длина памяти потока мыслей (дольше помнится ход мысли)
NOISE_JUMP = 0.15        # вероятность хаотичного прыжка правого полушария
SPONTANEOUS = 0.10       # вероятность спонтанного слова в поле левого (шум)
MIRROR = 0.6             # сила зеркала: окрестность слова уходит в поле другого
FIELD_DECAY = 0.85       # затухание полей за шаг
RECENT_DECAY = 0.95      # затухание памяти о недавно сказанных словах (дольше)
LEARN_STEP = 0.02        # Хебб в потоке мыслей
ADAPT_EVERY = 24         # адаптация групп каждые N шагов
EMIT_EVERY = 6           # мысль в чат не чаще, чем раз в N шагов
COHERENCE_EMIT = 0.25    # порог согласованности полей для вывода мысли
DELIB_CYCLES = 24        # шагов мышления на один вопрос
MAX_ANSWER = 8
CANDIDATES = 5           # сколько вариантов ответа сравнивается
CONF_MIN = 0.05          # ниже этого порога ответ помечается как неуверенный
GROUP_W = 1.2
LEFT_SCALE = 0.6         # левое полушарие холоднее (множитель от /temp)
TOPIC_KEEP = 0.8        # доля прежней темы при обновлении (рабочая память)
TOPIC_W = 0.8           # насколько тема разговора смещает выбор слов
TOPIC_FADE = 0.995      # медленное угасание темы в фоновом мышлении
RIGHT_SCALE = 1.7        # правое горячее
MIN_WORD_LEN = 3         # короче — служебный мусор
THOUGHT_MIN_WORDS = 3    # мысль в чат, только если после очистки осталось столько слов
REPEAT_MAX = 2           # одно слово в ответе не чаще двух раз
STOPWORDS = frozenset(
    "черновик черновика черновику рассуждение рассуждений "
    "формат формату формата служебный служебных "
    "подсказка подсказки проверь проверить перепроверь "
    "вопрос вопроса вопросу ответь ответа ответу "
    "правильный правильных одним словом "
    "и в во на с со к по за из у о об от до не ни что как а но или это то же бы ли так "
    "вот был была было были есть быть он она оно они мы вы я ты его её ее их там тут уже "
    "еще ещё при для без над под про все всё себя этот эта эти который которые такое какой какие".split())

def clean_words(words):
    """Убирает служебные слова, короткий мусор, соседние повторы и излишний повтор."""
    out, seen = [], {}
    for w in words:
        if len(w) < MIN_WORD_LEN or w in STOPWORDS:
            continue
        if out and out[-1] == w:
            continue
        if seen.get(w, 0) >= REPEAT_MAX:
            continue
        seen[w] = seen.get(w, 0) + 1
        out.append(w)
    return out


def key(a, b):
    return (a, b) if a < b else (b, a)


def cosine(a, b):
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


class Mind:
    def __init__(self, brain):
        self.b = brain
        self.fieldL = {}                       # вход левого полушария
        self.fieldR = {}                       # вход правого полушария
        self.stream = collections.deque(maxlen=STREAM_LEN)
        self.recent = {}                       # слово -> недавность
        self.acc = 0.0
        self.steps = 0
        self.turn = 0                          # 0 — правое, 1 — левое
        self.since_emit = 0
        self.think_every = THINK_EVERY
        self.conf_min = CONF_MIN               # порог уверенности (меняется /conf)
        self.topic = {}                        # рабочая память: тема разговора
        self.last_side = "R"                  # какое полушарие думало последним
        self.workspace = {}

    # ---------- утилиты ----------

    def _nbrs(self, word, adj):
        """Слова в окрестности: прямые связи и второй хоп через скрытые нейроны."""
        nid = self.b.words.get(word)
        out = {}
        if nid is None:
            return out
        for j, w in adj.get(nid, ()):
            m = self.b.neurons.get(j)
            if m is None:
                continue
            if m.word:
                out[m.word] = out.get(m.word, 0.0) + w
            else:
                for k2, w2 in adj.get(j, ()):
                    m2 = self.b.neurons.get(k2)
                    if m2 is not None and m2.word and k2 != nid:
                        out[m2.word] = out.get(m2.word, 0.0) + w * w2 * 0.5
        return out

    @staticmethod
    def _sample(cands, temp):
        """Температурное сэмплирование: p(w) ∝ exp(score / T)."""
        if not cands:
            return None
        if temp < 0.05:
            return max(cands, key=cands.get)
        mx = max(cands.values())
        words = list(cands)
        weights = [math.exp((cands[w] - mx) / temp) for w in words]
        return random.choices(words, weights=weights, k=1)[0]

    def _jump(self):
        """Хаотичный прыжок: любое слово словаря, редкие предпочтительнее."""
        if not self.b.words:
            return None
        pool = {w: 1.0 / (1.0 + self.recent.get(w, 0.0)) for w in self.b.words}
        return self._sample(pool, 1.0)

    def _mirror(self, field, word, adj):
        """Зеркало: слово и его окрестность попадают в поле другого полушария."""
        if word is None:
            return
        field[word] = field.get(word, 0.0) + 1.0
        for w, v in self._nbrs(word, adj).items():
            field[w] = field.get(w, 0.0) + MIRROR * v

    def _commit(self, word, learn=False):
        """Добавляет слово в текущий поток; обучение включается явно.

        Слова, сгенерированные самой сетью, не должны автоматически
        становиться «истиной» и усиливать собственные переходы.
        """
        prev = self.stream[-1] if self.stream else None
        self.stream.append(word)
        self.recent[word] = self.recent.get(word, 0.0) + 1.0
        pid = self.b.words.get(prev) if prev else None
        nid = self.b.words.get(word)
        if learn and pid is not None and nid is not None and pid != nid:
            k = key(pid, nid)
            if k in self.b.links:
                self.b.links[k] = min(2.0, self.b.links[k] + LEARN_STEP)
            self.b.bigram[(prev, word)] = self.b.bigram.get((prev, word), 0) + 1
        if nid is not None:
            self.b.forced.add(nid)

    def _decay(self):
        for field in (self.fieldL, self.fieldR):
            for w in list(field):
                field[w] *= FIELD_DECAY
                if field[w] < 0.02:
                    del field[w]
        for w in list(self.topic):
            self.topic[w] *= TOPIC_FADE
            if self.topic[w] < 0.01:
                del self.topic[w]
        for w in list(self.recent):
            self.recent[w] *= RECENT_DECAY
            if self.recent[w] < 0.05:
                del self.recent[w]

    def _integrate(self):
        """GWT: объединяем поля и активные группы в единое рабочее пространство."""
        merged = {}
        for w, v in self.fieldL.items():
            merged[w] = merged.get(w, 0.0) + v
        for w, v in self.fieldR.items():
            merged[w] = merged.get(w, 0.0) + v
        # вклад активных групп (метод _group_activity определён в Brain, не в Mind)
        active = {g: act for g, act in self.b._group_activity(merged).items()
                  if act >= 0.1}
        if active:
            # один проход по словам вместо обхода всего словаря на каждую группу
            for w, gg in self.b.wgroup.items():
                act = active.get(gg)
                if act is not None:
                    merged[w] = merged.get(w, 0.0) + GROUP_W * act
        for w in set(self.workspace) | set(merged):
            self.workspace[w] = 0.5 * self.workspace.get(w, 0.0) + 0.5 * merged.get(w, 0.0)
        self.workspace = {w: v for w, v in self.workspace.items() if v >= 0.02}

    def integration(self):
        """Грубый аналог Φ: насколько распределения полей пересекаются (0..1)."""
        if not self.fieldL or not self.fieldR:
            return 0.0
        def dist(f):
            s = sum(f.values()) or 1.0
            return {w: v / s for w, v in f.items()}
        p, q = dist(self.fieldL), dist(self.fieldR)
        keys = set(p) | set(q)
        return sum(math.sqrt(p.get(w, 0.0) * q.get(w, 0.0)) for w in keys)

    # ---------- полушария ----------

    def _right(self, adj):
        """Правое: хаотичное блуждание по соседям и полю левого."""
        last = self.stream[-1] if self.stream else None
        if last is None or last not in self.b.words or random.random() < NOISE_JUMP:
            return self._jump()
        cands = self._nbrs(last, adj)
        for w, v in self.fieldR.items():
            cands[w] = cands.get(w, 0.0) + v
        for w, v in self.topic.items():
            cands[w] = cands.get(w, 0.0) + 0.5 * TOPIC_W * v
        if not cands:
            return self._jump()
        cands = {w: v / (1.0 + self.recent.get(w, 0.0)) for w, v in cands.items()}
        return self._sample(cands, max(0.05, self.b.temp * RIGHT_SCALE))

    def _left(self, adj):
        """Левое: аналитический выбор по полю, связям, биграммам и группам."""
        if not self.fieldL:
            return None
        last = self.stream[-1] if self.stream else None
        nc = self.b.words.get(last) if last else None
        act = {g: cosine(self.fieldL, c) for g, c in self.b.gcent.items()}
        cands = {}
        for w, a in self.fieldL.items():
            if w in STOPWORDS or len(w) < MIN_WORD_LEN:
                continue
            s = a
            if last:
                s += 0.5 * math.log1p(self.b.bigram.get((last, w), 0))
                nw = self.b.words.get(w)
                if nc is not None and nw is not None:
                    s += 1.5 * self.b.links.get(key(nc, nw), 0.0)
            g = self.b.wgroup.get(w)
            if g is not None:
                s += GROUP_W * act.get(g, 0.0)
            s += 0.7 * self.workspace.get(w, 0.0)  # ← GWT
            s -= 2.0 * self.recent.get(w, 0.0)
            s += TOPIC_W * self.topic.get(w, 0.0)
            cands[w] = s
        return self._sample(cands, max(0.05, self.b.temp * LEFT_SCALE))

    # ---------- один шаг мышления ----------

    def step(self, adapt=True):
        adj = self.b.adjacency()
        self.steps += 1
        if random.random() < SPONTANEOUS:          # спонтанная мысль-шум
            self._mirror(self.fieldL, self._jump(), adj)

        if self.turn == 0:
            side, word = "R", self._right(adj)
        else:
            side, word = "L", self._left(adj)
        self.turn ^= 1
        self.last_side = side

        if word is not None:
            self._commit(word)
            # зеркало: выход одного полушария становится входом другого
            self._mirror(self.fieldL if side == "R" else self.fieldR, word, adj)

        self._decay()
        self._integrate()  # ← новое
        if (adapt and self.steps % ADAPT_EVERY == 0
                and len(self.stream) >= 4 and self.b.wgroup):
            self.b.adapt(list(self.stream)[-ADAPT_EVERY:])
        return side, word

    def coherence(self):
        return cosine(self.fieldL, self.fieldR)

    # ---------- фоновый поток ----------

    def tick(self, dt):
        """Вызывается каждый кадр. Возвращает строку мысли для чата или None."""
        self.acc += dt
        thought = None
        while self.acc >= self.think_every:
            self.acc -= self.think_every
            side, word = self.step()
            self.since_emit += 1
            coherent = self.coherence() >= COHERENCE_EMIT
            if (side == "L" and word and self.since_emit >= EMIT_EVERY
                    and (coherent or self.since_emit >= EMIT_EVERY * 4)):
                words = clean_words(list(self.stream)[-EMIT_EVERY:])
                if len(words) >= THOUGHT_MIN_WORDS:
                    thought = " ".join(words)
                self.since_emit = 0
        return thought

    # ---------- ответ на вопрос через зеркало ----------

    def deliberate(self, seed_ids, target=8, learn=True):
        if target <= 0:
            return []
        seeds = [self.b.neurons[i].word for i in seed_ids
                 if i in self.b.neurons and self.b.neurons[i].word]
        if not seeds:
            return []
        adj = self.b.adjacency()
        for s in seeds:
            self._commit(s)
            self._mirror(self.fieldL, s, adj)
            self._mirror(self.fieldR, s, adj)

        spoken = []
        cycles = max(DELIB_CYCLES, target * 3)
        for _ in range(cycles):
            if len(spoken) >= target:
                break
            side, word = self.step(adapt=False)
            if side == "L" and word and (not spoken or spoken[-1] != word):
                spoken.append(word)

        answer = [w for w in spoken if w not in seeds] or spoken
        answer = clean_words(answer)[:target]
        if learn and answer:
            self.b._reinforce_answer(answer)     # закрепляем только выбранный ответ
            self.b.adapt(seeds + answer)
        return answer

    # ---------- метакогниция: выбор лучшего варианта ----------

    def _context(self, seeds, adj):
        """Контекст вопроса: сами слова + окрестность с затуханием.

        ИЗМ: 1-хоп идёт с коэффициентом 0.7 (было 1.0). Это отделяет прямой
        контекст от ассоциативного шума, из-за которого косинус «размазывался»
        по всему словарю и support() плохо различал хорошие и слабые ответы.
        """
        ctx = {}
        for s in seeds:
            ctx[s] = ctx.get(s, 0.0) + 1.0
            for w, v in self._nbrs(s, adj).items():
                ctx[w] = ctx.get(w, 0.0) + 0.7 * v
        return ctx

    def _update_topic(self, seeds, adj):
        """Тема разговора: сглаженный контекст последних вопросов."""
        ctx = {w: v for w, v in self._context(seeds, adj).items()
               if w not in STOPWORDS and len(w) >= MIN_WORD_LEN}
        if not ctx:
            return
        mx = max(ctx.values())
        for w in set(self.topic) | set(ctx):
            target = ctx.get(w, 0.0) / mx
            self.topic[w] = TOPIC_KEEP * self.topic.get(w, 0.0) + (1 - TOPIC_KEEP) * target
        self.topic = {w: v for w, v in self.topic.items() if v >= 0.01}

    def support(self, question_words, reply_words):
        """Самопроверка: косинус темы + покрытие окрестности (0..1).

        ИЗМ: старая версия была чистым косинусом. Одно попадание в тему
        могло дать высокий support при куче мусора вокруг. Теперь косинус
        смешан с покрытием: какая доля слов ответа вообще встречается
        в окрестности вопроса. Ответ, где осмысленно связано каждое слово,
        получает высокий балл; ответ из одного тематического + мусора — нет.
        """
        seeds = [w for w in question_words if w in self.b.words]
        reply_known = [w for w in reply_words if w in self.b.words]
        if not seeds or not reply_known:
            return 0.0
        adj = self.b.adjacency()
        ctx = self._context(seeds, adj)
        if not ctx:
            return 0.0
        cos = cosine({w: 1.0 for w in reply_known}, ctx)
        nearby = set(ctx.keys())
        coverage = sum(1 for w in reply_known if w in nearby) / len(reply_known)
        return 0.55 * cos + 0.45 * coverage

    def answer(self, seed_ids, target=8):
        """Возвращает (слова, оценка ассоциативного сходства).

        Это не вероятность истинности. Непроверенный сгенерированный ответ
        не закрепляется автоматически; для обучения используются /good и /teach.
        """
        seeds = [self.b.neurons[i].word for i in seed_ids
                 if i in self.b.neurons and self.b.neurons[i].word]
        if not seeds or target <= 0:
            return [], 0.0
        adj = self.b.adjacency()
        ctx = self._context(seeds, adj)
        self._update_topic(seeds, adj)
        cands = []
        # Каждый вариант должен начинаться с одного и того же состояния.
        # Иначе предыдущая попытка незаметно меняет следующую.
        for _ in range(CANDIDATES):
            snapshot = (
                collections.deque(self.stream, maxlen=STREAM_LEN),
                dict(self.fieldL), dict(self.fieldR), dict(self.recent),
                dict(self.topic), dict(self.workspace), self.steps,
                self.turn, self.since_emit,
                set(self.b.forced), self.last_side,
            )
            try:
                # Повторы убираем: мысль из повторяющихся слов не несёт смысла
                # и раздувала оценку cosine (см. session_log: «обсуждают share…»).
                cand = list(dict.fromkeys(
                    w for w in self.deliberate(seed_ids, target, learn=False)
                    if w not in seeds))
            finally:
                (self.stream, self.fieldL, self.fieldR, self.recent, self.topic,
                 self.workspace, self.steps, self.turn, self.since_emit,
                 self.b.forced, self.last_side) = snapshot
            if cand:
                cands.append((cosine({w: 1.0 for w in cand}, ctx), cand))
        if not cands:
            self.b.adapt(seeds)  # незнакомый вопрос всё равно учит сеть
            return [], 0.0
        # ИЗМ: калибровка уверенности через margin. Если топ-1 и топ-2
        # почти равны, ответ выбирается «на грани» — уверенность завышена.
        # Штрафуем такую ситуацию: при margin >= 0.25 — полный вес, иначе 0.55..1.0.
        cands.sort(key=lambda x: -x[0])
        conf, best = cands[0]
        if len(cands) >= 2:
            margin = conf - cands[1][0]
            conf *= 0.55 + 0.45 * min(1.0, margin * 4.0)
        return best, conf

    def report(self):
        tail = " ".join(list(self.stream)[-12:]) or "пусто"
        top = sorted(self.topic.items(), key=lambda x: -x[1])[:5]
        topic = " ".join(w for w, _ in top) or "нет"
        side = "левое" if self.last_side == "L" else "правое"
        return [f"поток: {tail}",
                f"тема: {topic}",
                f"согласованность полей: {self.coherence():.2f}",
                f"интеграция (Φ≈): {self.integration():.2f}",  # ← новое
                f"последнее полушарие: {side}",
                f"шагов мышления: {self.steps}"]