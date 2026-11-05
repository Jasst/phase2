import json
import math
import os
import random
import re
import time
from collections import deque

import pygame

from mind import Mind
from lm_backend import LMBackend, LMWorker, RUNTIME, set_option
from episodes import EpisodeMemory, RECALL_STRONG, RECALL_WEAK
from reasoning import Answerer

W, H = 1280, 800
CHAT_W = 420
NET_W = W - CHAT_W
GRID = 50
CELL = min(NET_W, H) / GRID
OX = (NET_W - GRID * CELL) / 2
OY = (H - GRID * CELL) / 2

SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain_state.json")
AUTOSAVE_SEC = 60.0

# ---- уровень 1: слои нейронов ----
G_IN, G_ASSOC, G_INTEG, G_OUT = 0, 1, 2, 3
LAYER_NAMES = ["вход (слова)", "ассоциации", "интегратор", "речь (выход)"]
LAYER_COLORS = [(170, 184, 204), (130, 146, 168), (154, 144, 176), (196, 176, 150)]
GROUP_GAIN = [1.0, 0.9, 1.1, 1.0]
SUPER_W = [0.3, 0.6, 1.0, 1.4]

# ---- уровень 2: смысловые группы слов ----
GROUP_COLORS = [
    (196, 124, 124), (204, 162, 112), (200, 192, 116), (150, 188, 134),
    (122, 184, 164), (122, 168, 198), (132, 146, 186), (160, 134, 186),
    (186, 134, 172), (168, 170, 176), (206, 152, 152), (152, 198, 168),
    (130, 194, 198), (208, 190, 152), (160, 152, 192), (226, 228, 234),
]
GROUP_MAX = 16
GROUP_NEW_T = 0.2
REGROUP_ITERS = 3
GROUP_W = 1.2

# ---- уровень 3: адаптация всей сети ----
ADAPT_UP = 0.04
ADAPT_DOWN = 0.002
REGROUP_EVERY = 20

THRESH = 1.0
DECAY = 0.72
MAX_NEURONS = max(100, min(2000, int(os.environ.get("BRAIN_MAX_NEURONS", "700"))))
NEURON_LIMITS = (100, 2000)
GROW_P = 0.08
SPARK_P = 0.06
ISO_LIMIT = 60
PRUNE = 0.06
HEBB_RATE = 0.05
# Затухание за один вызов step() (вызывается каждый кадр): ~0.988 в секунду при 60 FPS,
# связь без подкрепления уходит к PRUNE примерно за 3 минуты.
LINK_DECAY = 0.9998
SIM_DT = 0.1
TIMELINE = 10.0
MAX_WORDS = 12
MAX_LINK_DIST = 9
BRIDGE_P = 0.7
TRAIN_WINDOW = 3
TEMP_DEFAULT = 0.7
NOISE_COUNT = 12
NOISE_P = 0.05
NOISE_GAIN = 0.5
GEN_STEPS = 8
GEN_DECAY = 0.6
REPEAT_PEN = 2.5
BIGRAM_W = 1.0
LINK_W = 1.5
MIN_SCORE = 0.05

# Команды, меняющие настройки модели: команда -> (параметр, подпись, единица)
TUNE_CMDS = {
    "/ctx": ("context_chars", "контекст диалога", "симв."),
    "/history": ("history", "помнит прошлых обменов", "шт."),
    "/time": ("time_budget", "время на ответ", "с (0 — без лимита)"),
    "/tokens": ("max_tokens", "длина ответа модели", "токенов"),
}

HELP_LINES = [
    "Напишите вопрос обычными словами — сеть подумает и ответит.",
    "Оценка ответа: /good — верно, /bad — неверно, /teach текст — правильный ответ.",
    "Обучение: /assoc слово слово… — связать слова между собой; /train файл.txt — учить по тексту.",
    "Память: /recall текст — найти похожие прошлые вопросы; /sleep — повторить память.",
    "Сеть: /neurons N — лимит нейронов; /groups — смысловые группы; /save, /load, /reset.",
    "Модель: /ctx N — символов контекста; /history N — сколько обменов помнит;",
    "        /time N — секунд на ответ (0 — без лимита); /tokens N — длина ответа.",
    "LLM: кнопка «LM: вкл / выкл» в шапке чата — клик переключает режим; "
    "/lm on|off — то же из строки ввода.",
    "Состояние: /mind — что происходит в сети и какие настройки заданы.",
    "Копирование: /copy — мысли и чат в буфер обмена и в файл session_log.txt; /log — только файл.",
    "Значение без числа показывает текущую настройку, например /time.",
]

NB8 = [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1) if (a, b) != (0, 0)]

BG = (13, 14, 17)
PANEL_BG = (19, 21, 26)
PANEL = (24, 26, 32)
PANEL_2 = (32, 35, 43)
BORDER = (46, 50, 60)
TRACK = (42, 46, 55)
TEXT = (236, 238, 241)
MUTED = (182, 190, 201)
DIM = (120, 128, 140)
ACCENT = (138, 156, 184)
ACCENT_HI = (214, 222, 235)
ACCENT_DIM = (84, 98, 122)
DANGER = (229, 84, 79)
C_SPEAK = (222, 200, 140)
C_PULSE = (120, 140, 170)
C_YOU = (168, 190, 222)
C_NET = (206, 210, 220)
C_SYS = DIM
C_THOUGHT = (150, 158, 172)
C_FOCUS = (236, 200, 120)     # подсветка нейрона под курсором
LABEL_ALL = 140              # при таком размере сети подписи видны у всех слов
COH_HIST = 120               # точек на графике согласованности
HOT_NODE = 30               # кадров подсветки нейрона после спайка (~3 с)
HOT_LINK = 20               # кадров подсветки связи (~2 с)


SESSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session_log.txt")
WHO_NAMES = {"you": "Вы", "net": "Сеть", "sys": "Система", "thought": "Мысль"}


def session_text(chat, thoughts, n_chat=120, n_think=60):
    """Текст журнала: последние мысли сети и последние реплики чата.

    ИЗМ: поддерживает 3-элементные записи [who, text, score].
    """
    lines = ["=== МЫСЛИ СЕТИ (последние) ==="]
    lines += [str(t) for _, t in thoughts[-n_think:]]
    lines += ["", "=== ЧАТ ==="]
    # ИЗМ: было `for w, t in chat[-n_chat:]`. Записи могут содержать оценку.
    lines += [f"{WHO_NAMES.get(item[0], item[0])}: {item[1]}"
              for item in chat[-n_chat:]]
    return "\n".join(lines) + "\n"


def write_session(chat, thoughts, path=SESSION_PATH):
    with open(path, "w", encoding="utf-8") as f:
        f.write(session_text(chat, thoughts))
    return path


def copy_to_clipboard(text):
    """Кладёт текст в буфер обмена. False, если буфер недоступен в этой системе."""
    try:
        pygame.scrap.init()
        pygame.scrap.put(pygame.SCRAP_TEXT, text.encode("utf-8"))
        return True
    except Exception:
        return False


def key(a, b):
    return (a, b) if a < b else (b, a)


def cell_xy(r, c):
    return (OX + (c + 0.5) * CELL, OY + (r + 0.5) * CELL)


def ctrl_point(p1, p2, a, b):
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy) or 1.0
    off = ((a * 31 + b * 17) % 9 - 4) * 0.06 * L
    return (mx - dy / L * off, my + dx / L * off)


def bez(p1, c, p2, t):
    u = 1 - t
    return (u * u * p1[0] + 2 * u * t * c[0] + t * t * p2[0],
            u * u * p1[1] + 2 * u * t * c[1] + t * t * p2[1])


def cosine(a, b):
    """Косинусное сходство двух разреженных векторов (dict)."""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def tokenize(text, limit=MAX_WORDS):
    words = re.findall(r"[^\W_]+", text.lower())
    return words[:limit] if limit else words


def wrap(text, font, width):
    lines, line = [], ""
    for word in text.split(" "):
        test = (line + " " + word).strip()
        if font.size(test)[0] <= width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    lines.append(line)
    return lines


def _norm(word):
    """Слово из ответа LM (с пунктуацией) -> ключ словаря графа."""
    toks = tokenize(word, limit=None)
    return toks[0] if toks else None


def start_lm():
    """Подключает LM; при ошибке чат продолжает работать на графе."""
    try:
        backend = LMBackend()
    except Exception as e:
        return None, f"LM недоступна ({e}); отвечает только граф."
    return LMWorker(backend), f"LM подключена: {backend.name} [{backend.backend_used}]."


def restart_lm(lm):
    """Переподключает LM (/lm). Возвращает (worker|None, статус, был_сменён_ли_состояние)."""
    old_backend = lm.backend if lm is not None else None
    try:
        backend = LMBackend()
    except Exception as e:                       # старый worker остаётся в работе
        return lm, f"LM недоступна ({e}); отвечает только граф.", False
    worker = LMWorker(backend)
    if old_backend is not None:
        backend.history = list(old_backend.history)   # диалог не теряется
    return worker, f"LM подключена: {backend.name} [{backend.backend_used}].", True


class Slider:
    """Ползунок 0..100 в панели чата (тёмная тема)."""

    def __init__(self, label, value, to_text):
        self.label = label
        self.value = float(value)
        self.to_text = to_text
        self.rect = None

    def hit(self, pos):
        return self.rect is not None and self.rect.inflate(0, 14).collidepoint(pos)

    def set_from_x(self, x):
        t = (x - self.rect.x) / self.rect.w
        self.value = max(0.0, min(100.0, t * 100.0))

    def draw(self, screen, fonts, x, y, w):
        self.rect = pygame.Rect(x, y + 18, w, 6)
        screen.blit(fonts["word"].render(self.label, True, MUTED), (x, y))
        txt = fonts["word"].render(self.to_text(self.value), True, TEXT)
        screen.blit(txt, (x + w - txt.get_width(), y))
        pygame.draw.rect(screen, TRACK, self.rect, border_radius=3)
        fw = int(self.rect.w * self.value / 100)
        if fw > 0:
            pygame.draw.rect(screen, ACCENT, (self.rect.x, self.rect.y, fw, self.rect.h),
                             border_radius=3)
        k = (self.rect.x + fw, self.rect.centery)
        pygame.draw.circle(screen, PANEL_2, k, 8)
        pygame.draw.circle(screen, ACCENT_HI, k, 5)


def think_sec(v):
    return 2.0 - v / 100.0 * 1.85     # 0 — медленно, 100 — почти непрерывно


def gap_sec(v):
    return 0.05 + v / 100.0 * 2.95    # зазор между словами речи


class Neuron:
    __slots__ = ("id", "r", "c", "word", "layer", "pot", "thr", "fired_at", "iso")

    def __init__(self, nid, r, c, word, layer):
        self.id, self.r, self.c, self.word, self.layer = nid, r, c, word, layer
        self.pot = 0.0
        self.thr = THRESH
        self.fired_at = -999
        self.iso = 0


class Brain:
    def __init__(self):
        self.temp = TEMP_DEFAULT
        self._clear()
        self.center = self.add_free(GRID // 2, GRID // 2, None, G_OUT)
        self.add_noise()

    def _clear(self):
        self.neurons = {}
        self.grid = {}
        self.words = {}
        self.links = {}
        self.bigram = {}
        self.inhib = {}
        self.noisy = set()
        self.wgroup = {}        # слово -> id группы
        self.gcent = {}         # id группы -> центр (вектор связей слов)
        self.fired = set()
        self.forced = set()
        self.particles = []
        self.nid = 0
        self.tick = 0
        self.center = None

    # ---------- структура ----------

    def _register(self, n):
        self.neurons[n.id] = n
        self.grid[(n.r, n.c)] = n.id
        if n.word:
            self.words[n.word] = n.id

    def add(self, r, c, word=None, layer=None):
        if layer is None:
            layer = G_IN if word else G_ASSOC
        n = Neuron(self.nid, r, c, word, layer)
        self.nid += 1
        self._register(n)
        return n

    def remove(self, n):
        if n is self.center:
            return
        del self.neurons[n.id]
        del self.grid[(n.r, n.c)]
        self.noisy.discard(n.id)
        if n.word and self.words.get(n.word) == n.id:
            del self.words[n.word]
            self.wgroup.pop(n.word, None)
        for k in [k for k in self.links if n.id in k]:
            del self.links[k]
        self.fired.discard(n.id)

    def add_free(self, r, c, word=None, layer=None, max_r=8):
        if 0 <= r < GRID and 0 <= c < GRID and (r, c) not in self.grid:
            return self.add(r, c, word, layer)
        for rad in range(1, max_r + 1):
            for dr in range(-rad, rad + 1):
                for dc in range(-rad, rad + 1):
                    if max(abs(dr), abs(dc)) != rad:
                        continue
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < GRID and 0 <= cc < GRID and (rr, cc) not in self.grid:
                        return self.add(rr, cc, word, layer)
        return None

    def add_noise(self, count=NOISE_COUNT):
        for _ in range(count):
            r, c = random.randrange(GRID), random.randrange(GRID)
            n = self.add_free(r, c, None, G_ASSOC)
            if n is None:
                break
            self.noisy.add(n.id)
            others = sorted(self.neurons.values(),
                            key=lambda m: abs(m.r - n.r) + abs(m.c - n.c))
            for m in others[1:3]:
                self.connect(n, m, 0.5)

    def ring_cell(self, word):
        a = (sum((i + 7) * ord(ch) for i, ch in enumerate(word)) % 628) / 100.0
        R = GRID * 0.30
        return (int(GRID / 2 + R * math.sin(a)), int(GRID / 2 + R * math.cos(a)))

    def connect(self, a, b, w=1.0):
        if a.id != b.id:
            k = key(a.id, b.id)
            self.links[k] = max(self.links.get(k, 0.0), w)

    def connect_words(self, a, b):
        d = max(abs(a.r - b.r), abs(a.c - b.c))
        if d <= MAX_LINK_DIST:
            self.connect(a, b, 1.0)
            return
        if random.random() < BRIDGE_P and len(self.neurons) < MAX_NEURONS - 2:
            m = self.add_free((a.r + b.r) // 2, (a.c + b.c) // 2, None, G_ASSOC)
            if m is not None:
                self.connect(a, m, 0.9)
                self.connect(m, b, 0.9)
                return
        self.connect(a, b, 0.45)

    def spawn(self, src, layer=None):
        if len(self.neurons) >= MAX_NEURONS:
            return None
        if src is None or src.id not in self.neurons:
            src = random.choice(tuple(self.neurons.values()))
        target = layer if layer is not None else min(src.layer + 1, G_OUT)
        best, best_score = None, -1e9
        for _ in range(28):
            dr, dc = random.choice(NB8)
            r, c = src.r + dr, src.c + dc
            if not (0 <= r < GRID and 0 <= c < GRID) or (r, c) in self.grid:
                continue
            occ = sum(1 for ar, ac in NB8 if (r + ar, c + ac) in self.grid)
            score = -occ * 3 + random.random()
            if target == G_OUT:
                score -= 0.3 * (abs(r - GRID / 2) + abs(c - GRID / 2))
            if score > best_score:
                best_score, best = score, (r, c)
        if best is None:
            return None
        n = self.add(best[0], best[1], None, target)
        self.links[key(src.id, n.id)] = 0.6
        return n

    def adjacency(self):
        adj = {}
        for (a, b), w in self.links.items():
            adj.setdefault(a, []).append((b, w))
            adj.setdefault(b, []).append((a, w))
        return adj

    # ---------- обучение (без пошаговой симуляции) ----------

    def _ids_for(self, words):
        ids = []
        for w in words:
            nid = self.words.get(w)
            if nid is None:
                if len(self.neurons) >= MAX_NEURONS:
                    continue
                r, c = self.ring_cell(w)
                n = self.add_free(r, c, w, G_IN)
                if n is None:
                    continue
                nid = n.id
                if random.random() < 0.3:
                    self.spawn(n, G_ASSOC)
            ids.append(nid)
        return ids

    def train_words(self, words):
        ids = self._ids_for(words)
        for i in range(len(ids)):
            for j in range(i + 1, min(len(ids), i + 1 + TRAIN_WINDOW)):
                a, b = ids[i], ids[j]
                if a == b:
                    continue
                k = key(a, b)
                gain = HEBB_RATE / (j - i)
                if k in self.links:
                    self.links[k] = min(2.0, self.links[k] + gain)
                elif j == i + 1:
                    self.connect_words(self.neurons[a], self.neurons[b])
                if j == i + 1:
                    bk = (self.neurons[a].word, self.neurons[b].word)
                    self.bigram[bk] = self.bigram.get(bk, 0) + 1
        return ids

    def train_file(self, path):
        lines_done, words_done = 0, 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                words = tokenize(line, limit=None)
                if len(words) < 2:
                    continue
                self.train_words(words)
                lines_done += 1
                words_done += len(words)
        self.regroup()
        return lines_done, words_done

    # ---------- уровень 2: группировка слов по всей сети ----------

    def _profiles(self):
        """Профиль слова: связи со всеми другими словами плюс биграммы."""
        prof = {w: {} for w in self.words}
        id2w = {nid: n.word for nid, n in self.neurons.items() if n.word}
        for (a, b), w in self.links.items():
            wa, wb = id2w.get(a), id2w.get(b)
            if wa and wb:
                prof[wa][wb] = prof[wa].get(wb, 0.0) + w
                prof[wb][wa] = prof[wb].get(wa, 0.0) + w
        for (a, b), c in self.bigram.items():
            if a in prof and b in prof:
                prof[a][b] = prof[a].get(b, 0.0) + 0.5 * c
                prof[b][a] = prof[b].get(a, 0.0) + 0.5 * c
        return {w: p for w, p in prof.items() if p}

    def regroup(self):
        """Каждое слово сравнивается с центрами ВСЕХ групп по косинусу.
        Переходит в лучшую группу; если похожих нет, открывается новая."""
        prof = self._profiles()
        if not prof:
            self.wgroup, self.gcent = {}, {}
            return
        cent = {g: c for g, c in self.gcent.items() if c}
        if not cent:
            seed = max(prof, key=lambda w: sum(prof[w].values()))
            cent = {0: dict(prof[seed])}

        assign = {}
        for _ in range(REGROUP_ITERS):
            assign = {}
            for w, p in prof.items():
                best_g, best_s = None, -1.0
                for g, c in cent.items():
                    s = cosine(p, c)
                    if s > best_s:
                        best_g, best_s = g, s
                if best_s < GROUP_NEW_T and len(cent) < GROUP_MAX:
                    best_g = max(cent) + 1
                    cent[best_g] = dict(p)
                assign[w] = best_g

            acc, cnt = {}, {}
            for w, g in assign.items():
                cnt[g] = cnt.get(g, 0) + 1
                a = acc.setdefault(g, {})
                for k, v in prof[w].items():
                    a[k] = a.get(k, 0.0) + v
            cent = {g: {k: v / cnt[g] for k, v in a.items()} for g, a in acc.items()}

        self.wgroup = assign
        self.gcent = cent

    def update_clusters(self):
        """Совместимость со старыми скриптами (train_start.py)."""
        self.regroup()

    def _group_activity(self, state):
        """Сравнение состояния сети со всеми группами: косинус с каждым центром."""
        return {g: cosine(state, c) for g, c in self.gcent.items()}

    # ---------- уровень 3: адаптация всей сети к фразе ----------

    def adapt(self, seq, confidence=1.0):
        if not seq:
            return
        up = ADAPT_UP * confidence
        down = ADAPT_DOWN * confidence
        id2w = {n.id: n.word for n in self.neurons.values() if n.word}
        for k in list(self.links):
            a, b = k
            wa, wb = id2w.get(a), id2w.get(b)
            if wa is None or wb is None:
                continue
            ga, gb = self.wgroup.get(wa), self.wgroup.get(wb)
            if ga is None or gb is None:
                continue
            w = self.links[k]
            w = min(2.0, w + up) if ga == gb else w * (1 - down)
            if w < PRUNE:
                del self.links[k]
            else:
                self.links[k] = w
        for w in seq:
            g = self.wgroup.get(w)
            if g is None:
                continue
            cen = self.gcent.setdefault(g, {})
            for other in seq:
                if other != w:
                    cen[other] = cen.get(other, 0.0) + up

    # ---------- динамика (фон) ----------

    def step(self, grow=True, plastic=True):
        self.tick += 1
        if grow and self.neurons and random.random() < SPARK_P:
            random.choice(tuple(self.neurons.values())).pot += THRESH * 0.8

        for nid in self.noisy:
            m = self.neurons.get(nid)
            if m is not None and random.random() < NOISE_P:
                m.pot += NOISE_GAIN * THRESH

        adj = self.adjacency()
        new = set()
        for n in self.neurons.values():
            inp = random.random() * 0.03
            for j, w in adj.get(n.id, ()):
                if j in self.fired:
                    inp += w * 0.9
            inp *= GROUP_GAIN[n.layer]
            if n.word:
                inp -= self.inhib.get(n.word, 0.0)
            n.pot = n.pot * DECAY + inp
            if n.pot >= n.thr or n.id in self.forced:
                n.pot = 0.0
                n.fired_at = self.tick
                n.thr += 0.03
                new.add(n.id)
            else:
                n.thr += (THRESH - n.thr) * 0.02
        self.forced = set()

        for nid in new:
            for j, w in adj.get(nid, ()):
                if random.random() < 0.5 * min(1.0, w):
                    self.particles.append([nid, j, 0.0, random.uniform(0.04, 0.08), False])

        if plastic:
            prev = self.fired
            for k in list(self.links):
                a, b = k
                co = ((a in new and b in new) or (a in new and b in prev)
                      or (b in new and a in prev))
                w = self.links[k]
                w = min(2.0, w + HEBB_RATE) if co else w * LINK_DECAY
                if w < PRUNE:
                    del self.links[k]
                else:
                    self.links[k] = w

        if grow:
            for nid in list(new):
                n = self.neurons.get(nid)
                if n is not None and random.random() < GROW_P:
                    self.spawn(n)
            deg = {}
            for a, b in self.links:
                deg[a] = deg.get(a, 0) + 1
                deg[b] = deg.get(b, 0) + 1
            dead = []
            for n in self.neurons.values():
                n.iso = 0 if n.id in deg else n.iso + 1
                if n.word or n is self.center or n.id in self.noisy:
                    continue
                occ = sum(1 for dr, dc in NB8 if (n.r + dr, n.c + dc) in self.grid)
                if n.iso > ISO_LIMIT or (occ >= 6 and random.random() < 0.03):
                    dead.append(n)
            for n in dead:
                self.remove(n)

        self.fired = {i for i in new if i in self.neurons}
        return set(self.fired)

    # ---------- генерация: рекуррентный проход по группам ----------

    def _spread(self, state, adj):
        """Один проход: активность слов раздаётся по связям, а через скрытые
        нейроны идёт второй хоп с весом слоя (SUPER_W)."""
        out = {}
        for wd, a in state.items():
            nid = self.words.get(wd)
            if nid is None:
                continue
            for j, w in adj.get(nid, ()):
                m = self.neurons.get(j)
                if m is None:
                    continue
                if m.word:
                    out[m.word] = out.get(m.word, 0.0) + a * w
                else:
                    gain = SUPER_W[m.layer]
                    for k2, w2 in adj.get(j, ()):
                        m2 = self.neurons.get(k2)
                        if m2 is not None and m2.word and k2 != nid:
                            out[m2.word] = out.get(m2.word, 0.0) + a * w * w2 * gain * 0.5
        return out

    def _pick(self, cands):
        """Температурное сэмплирование: p(w) ∝ exp(score / T)."""
        if not cands:
            return None
        if self.temp < 0.05:
            return max(cands, key=cands.get)
        mx = max(cands.values())
        words = list(cands)
        weights = [math.exp((cands[w] - mx) / self.temp) for w in words]
        return random.choices(words, weights=weights, k=1)[0]

    def respond(self, input_ids):
        """Однопроходная пошаговая генерация (запасной режим, без полушарий)."""
        prompt = [self.neurons[i].word for i in input_ids
                  if i in self.neurons and self.neurons[i].word]
        if not prompt:
            return []
        if any(w not in self.wgroup for w in prompt):
            self.regroup()
        adj = self.adjacency()

        state = {w: 1.0 for w in prompt}
        for w, v in self._spread(state, adj).items():
            state[w] = state.get(w, 0.0) + 0.5 * v

        answer, used, cur = [], {}, prompt[-1]
        for _ in range(GEN_STEPS):
            act = self._group_activity(state)
            nc = self.words.get(cur)
            cands = {}
            for wd, a in state.items():
                if wd == cur:
                    continue
                s = a + BIGRAM_W * self.bigram.get((cur, wd), 0)
                nw = self.words.get(wd)
                if nc is not None and nw is not None:
                    s += LINK_W * self.links.get(key(nc, nw), 0.0)
                g = self.wgroup.get(wd)
                if g is not None:
                    s += GROUP_W * act.get(g, 0.0)
                s -= REPEAT_PEN * used.get(wd, 0)
                cands[wd] = s
            nxt = self._pick(cands)
            if nxt is None or cands[nxt] < MIN_SCORE:
                break
            answer.append(nxt)
            used[nxt] = used.get(nxt, 0) + 1

            state = {w: a * GEN_DECAY for w, a in state.items()}
            for w, v in self._spread({nxt: 1.0}, adj).items():
                state[w] = state.get(w, 0.0) + 0.5 * v
            state[nxt] = state.get(nxt, 0.0) + 1.0
            cur = nxt

        if answer:
            self.adapt(prompt + answer)
        return answer

    def _reinforce_answer(self, answer, weight=1.0):
        """weight 0..1 — уверенность в ответе. 1.0 = старый режим."""
        for a, b in zip(answer, answer[1:]):
            na, nb = self.words.get(a), self.words.get(b)
            if na is None or nb is None:
                continue
            k = key(na, nb)
            gain = HEBB_RATE * 2 * weight
            if k in self.links:
                self.links[k] = min(2.0, self.links[k] + gain)
            self.bigram[(a, b)] = self.bigram.get((a, b), 0) + 1

    def punish(self, answer):
        """/bad: цепочка ответа ослабляется."""
        for a, b in zip(answer, answer[1:]):
            na, nb = self.words.get(a), self.words.get(b)
            if na is not None and nb is not None:
                k = key(na, nb)
                if k in self.links:
                    self.links[k] *= 0.7
            if (a, b) in self.bigram:
                self.bigram[(a, b)] = max(0, self.bigram[(a, b)] - 1)

    # ---------- консолидация ----------

    def consolidate(self):
        pruned = 0
        for k in list(self.links):
            w = self.links[k] * 0.98
            if w < PRUNE:
                del self.links[k]
                pruned += 1
            else:
                self.links[k] = w
        self.regroup()
        return pruned, len(set(self.wgroup.values()))

    def group_report(self):
        members = {}
        for w, g in self.wgroup.items():
            members.setdefault(g, []).append(w)
        out = []
        for g in sorted(members):
            ws = sorted(members[g], key=lambda w: -sum(
                self.links.get(key(self.words[w], self.words[x]), 0.0)
                for x in members[g] if x != w and x in self.words))
            out.append(f"группа {g} ({len(ws)}): " + " ".join(ws[:6]))
        return out

    # ---------- сохранение ----------

    def to_dict(self):
        return {
            "version": 5,
            "center": self.center.id if self.center else None,
            "nid": self.nid,
            "tick": self.tick,
            "temp": self.temp,
            "noisy": list(self.noisy),
            "neurons": [[n.id, n.r, n.c, n.word, n.layer, round(n.thr, 4)]
                        for n in self.neurons.values()],
            "links": [[a, b, round(w, 4)] for (a, b), w in self.links.items()],
            "bigram": [[a, b, c] for (a, b), c in self.bigram.items()],
        }

    def load_dict(self, d):
        self._clear()
        for nid, r, c, word, layer, thr in d["neurons"]:
            n = Neuron(nid, r, c, word, layer)
            n.thr = thr
            self._register(n)
        self.nid = d.get("nid", max(self.neurons, default=-1) + 1)
        self.tick = d.get("tick", 0)
        self.temp = d.get("temp", TEMP_DEFAULT)
        self.center = self.neurons.get(d.get("center"))
        if self.center is None:
            self.center = self.add_free(GRID // 2, GRID // 2, None, G_OUT)
        for a, b, w in d["links"]:
            if a in self.neurons and b in self.neurons:
                self.links[key(a, b)] = w
        for a, b, c in d["bigram"]:
            self.bigram[(a, b)] = c
        self.noisy = {i for i in d.get("noisy", []) if i in self.neurons}
        if not self.noisy:
            self.add_noise()
        self.regroup()

    def trim_particles(self):
        if len(self.particles) > 1500:
            speak_p = [p for p in self.particles if p[4]]
            other_p = [p for p in self.particles if not p[4]][-1000:]
            self.particles = speak_p + other_p

    def move_particles(self):
        alive = []
        for p in self.particles:
            p[2] += p[3]
            if p[2] < 1.0:
                alive.append(p)
        self.particles = alive


def save_brain(brain, path=SAVE_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(brain.to_dict(), f, ensure_ascii=False)
    os.replace(tmp, path)


def load_brain(path=SAVE_PATH):
    b = Brain()
    with open(path, encoding="utf-8") as f:
        b.load_dict(json.load(f))
    return b


# ---------- отрисовка ----------

THINK_RECT = pygame.Rect(NET_W + 12, 36, CHAT_W - 24, 280)
CHAT_RECT = pygame.Rect(NET_W + 12, 346, CHAT_W - 24, 228)
INPUT_RECT = pygame.Rect(NET_W + 12, H - 54, CHAT_W - 24, 40)


def rounded(screen, rect, fill, border=None, r=10):
    pygame.draw.rect(screen, fill, rect, border_radius=r)
    if border is not None:
        pygame.draw.rect(screen, border, rect, 1, border_radius=r)


def header(screen, fonts, text, x, y, col):
    pygame.draw.circle(screen, col, (x + 3, y + 9), 3)
    screen.blit(fonts["title"].render(text, True, col), (x + 12, y))


def make_background():
    surf = pygame.Surface((W, H))
    surf.fill(BG)
    cx, cy = NET_W / 2, H / 2
    for r in range(520, 0, -26):
        k = 1 - r / 520
        col = (BG[0] + int(8 * k), BG[1] + int(8 * k), BG[2] + int(10 * k))
        pygame.draw.circle(surf, col, (int(cx), int(cy)), r)
    pygame.draw.rect(surf, PANEL_BG, (NET_W, 0, CHAT_W, H))
    pygame.draw.line(surf, BORDER, (NET_W, 0), (NET_W, H), 1)
    return surf


def node_base(brain, n):
    if n.id in brain.noisy:
        return (176, 178, 186)
    if n.word and n.word in brain.wgroup:
        return GROUP_COLORS[brain.wgroup[n.word] % len(GROUP_COLORS)]
    return LAYER_COLORS[n.layer]


# NEW: оболочка выпуклой оболочки группы (monotone chain)
def convex_hull(points):
    """Оболочка группы. Точки CCW. Возвращает список вершин."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# NEW: рисует полупрозрачные оболочки смысловых групп
def draw_group_hulls(screen, brain, surface):
    """Показывает границы смысловых групп — где какая тема «живёт» в сети."""
    surface.fill((0, 0, 0, 0))
    by_group = {}
    for w, g in brain.wgroup.items():
        nid = brain.words.get(w)
        if nid is None or nid not in brain.neurons:
            continue
        n = brain.neurons[nid]
        by_group.setdefault(g, []).append((n.r, n.c))

    for g, cells in by_group.items():
        if len(cells) < 2:
            continue
        col = GROUP_COLORS[g % len(GROUP_COLORS)]
        pts = [cell_xy(r, c) for r, c in cells]
        hull = convex_hull(pts)
        if len(hull) < 2:
            continue
        if len(hull) == 2:
            pygame.draw.line(surface, (*col, 40), *hull, 22)
            pygame.draw.line(surface, (*col, 75), *hull, 1)
            continue
        cx = sum(p[0] for p in hull) / len(hull)
        cy = sum(p[1] for p in hull) / len(hull)
        expanded = []
        for x, y in hull:
            dx, dy = x - cx, y - cy
            L = math.hypot(dx, dy) or 1.0
            expanded.append((x + dx / L * 12, y + dy / L * 12))
        pygame.draw.polygon(surface, (*col, 22), expanded)
        pygame.draw.polygon(surface, (*col, 60), expanded, 1)
    screen.blit(surface, (0, 0))


def draw_scene(screen, brain, layers, fonts, cache, stars, now, focus=None, info_extra=""):
    """Сеть: связи, свечение, частицы, подписи. focus — нейрон под курсором."""
    links_layer, glow = layers
    tick = brain.tick
    adj = brain.adjacency()
    nbrs = {j for j, _ in adj.get(focus, ())} if focus is not None else set()

    for x, y, ph in stars:
        b = int(34 + 24 * math.sin(now * 1.1 + ph))
        screen.set_at((int(x), int(y)), (b, b, b + 4))

    links_layer.fill((0, 0, 0, 0))
    for (a, b), w in brain.links.items():
        na, nb = brain.neurons.get(a), brain.neurons.get(b)
        if na is None or nb is None:
            continue
        p1, p2 = cell_xy(na.r, na.c), cell_xy(nb.r, nb.c)
        c = ctrl_point(p1, p2, a, b)
        hot = max(0.0, 1 - (tick - max(na.fired_at, nb.fired_at)) / HOT_LINK)
        if focus is not None and focus in (a, b):
            col = (C_FOCUS[0], C_FOCUS[1], C_FOCUS[2], 230)
            width = 2
        else:
            alpha = int(min(255, 28 + 80 * min(w, 2.0) / 2 + 140 * hot))
            col = (118, 130, 150, alpha) if hot < 0.3 else (214, 222, 235, alpha)
            width = 1 + (w > 1.2) + (hot > 0.5)
        pts = [bez(p1, c, p2, i / 10) for i in range(11)]
        pygame.draw.lines(links_layer, col, False, pts, width)
    screen.blit(links_layer, (0, 0))

    glow.fill((0, 0, 0))
    for n in brain.neurons.values():
        x, y = cell_xy(n.r, n.c)
        hot = max(0.0, 1 - (tick - n.fired_at) / HOT_NODE)
        if n.id == focus or n.id in nbrs:
            hot = max(hot, 0.6)                  # соседи выбранного нейрона светятся
        base = node_base(brain, n)
        R = (6 if n.word else 4) + 3 * hot
        g1 = [int(v * (0.10 + 0.4 * hot)) for v in base]
        g2 = [int(v * (0.20 + 0.5 * hot)) for v in base]
        pygame.draw.circle(glow, g1, (int(x), int(y)), int(R * 4))
        pygame.draw.circle(glow, g2, (int(x), int(y)), int(R * 2))

    for a, b, t, _speed, speak in brain.particles:
        na, nb = brain.neurons.get(a), brain.neurons.get(b)
        if na is None or nb is None:
            continue
        lo, hi = key(a, b)
        p_src, p_dst = cell_xy(na.r, na.c), cell_xy(nb.r, nb.c)
        plo, phi = (p_src, p_dst) if a == lo else (p_dst, p_src)
        c = ctrl_point(plo, phi, lo, hi)
        pos = bez(plo, c, phi, min(1.0, t) if a == lo else 1 - min(1.0, t))
        col = C_SPEAK if speak else C_PULSE
        pygame.draw.circle(glow, col, (int(pos[0]), int(pos[1])), 4 if speak else 2)
    screen.blit(glow, (0, 0), special_flags=pygame.BLEND_ADD)

    # NEW: подписи с защитой от наложений. Подпись под фокусом рисуется всегда.
    drawn_label_rects = []
    for n in brain.neurons.values():
        x, y = cell_xy(n.r, n.c)
        hot = max(0.0, 1 - (tick - n.fired_at) / HOT_NODE)
        base = node_base(brain, n)
        core = tuple(min(255, int(v + (255 - v) * hot)) for v in base)
        R = (5 if n.word else 3) + 2 * hot
        pygame.draw.circle(screen, core, (int(x), int(y)), int(R))
        is_focus = n.id == focus
        if is_focus:
            pygame.draw.circle(screen, C_FOCUS, (int(x), int(y)), int(R) + 4, 2)
        if n.word and (is_focus or hot > 0.15 or n.id in nbrs
                       or len(brain.neurons) <= LABEL_ALL):
            surf = cache.get(n.word)
            if surf is None:
                surf = fonts["word"].render(n.word, True, (206, 212, 222))
                cache[n.word] = surf
            rect = surf.get_rect(topleft=(int(x) + 8, int(y) - 8))
            # Наложения: подпись фокуса важнее, её рисуем всегда.
            if not is_focus and any(rect.colliderect(r) for r in drawn_label_rects):
                continue
            drawn_label_rects.append(rect)
            screen.blit(surf, rect)

    info_rect = pygame.Rect(12, 10, 560, 26)
    rounded(screen, info_rect, PANEL, BORDER, 8)
    n_groups = len(set(brain.wgroup.values()))
    info = (f"нейронов {len(brain.neurons)}   связей {len(brain.links)}"
            f"   слов {len(brain.words)}   групп {n_groups}   T={brain.temp:.2f}"
            f"{info_extra}")
    screen.blit(fonts["ui"].render(info, True, MUTED), (info_rect.x + 10, info_rect.y + 4))

    legend = pygame.Rect(12, 44, 220, 6 * 18 + 12)
    rounded(screen, legend, PANEL, BORDER, 8)
    ly = legend.y + 8
    for g in range(4):
        pygame.draw.circle(screen, LAYER_COLORS[g], (legend.x + 14, ly + 8), 4)
        screen.blit(fonts["ui"].render(LAYER_NAMES[g], True, MUTED), (legend.x + 26, ly))
        ly += 18
    pygame.draw.circle(screen, (176, 178, 186), (legend.x + 14, ly + 8), 4)
    screen.blit(fonts["ui"].render("шумовые", True, MUTED), (legend.x + 26, ly))


def nearest_neuron(brain, mx, my, radius=12.0):
    """Id нейрона ближе radius пикселей к курсору (только в зоне сети)."""
    if mx >= NET_W or my < 0 or my > H:
        return None
    best, best_d = None, radius
    for n in brain.neurons.values():
        x, y = cell_xy(n.r, n.c)
        d = math.hypot(x - mx, y - my)
        if d <= best_d:
            best, best_d = n.id, d
    return best


# ИЗМ: новая версия принимает mind вместо side. Верхняя часть панели
# показывает топ-слова из fieldL/fieldR — видно, что каждое полушарие
# реально «думает» прямо сейчас.
def draw_overlay(screen, fonts, brain, mind, coh_hist, focus, mouse, now):
    """Панель полушарий, sparkline согласованности, подсказка под курсором."""
    box = pygame.Rect(12, H - 186, 340, 174)
    rounded(screen, box, PANEL, BORDER, 8)
    screen.blit(fonts["title"].render("ПОЛУШАРИЯ", True, MUTED), (box.x + 12, box.y + 6))

    col_w = (box.w - 36) // 2
    sides = [
        ("ЛЕВОЕ", "анализ", mind.fieldL, mind.last_side == "L"),
        ("ПРАВОЕ", "блуждание", mind.fieldR, mind.last_side == "R"),
    ]
    for i, (name, sub, field, active) in enumerate(sides):
        cx = box.x + 12 + i * (col_w + 12)
        head_col = ACCENT_HI if active else MUTED
        bg = PANEL_2 if active else PANEL
        pygame.draw.rect(screen, bg, (cx, box.y + 26, col_w, 108), border_radius=6)
        screen.blit(fonts["word"].render(name, True, head_col), (cx + 6, box.y + 28))
        screen.blit(fonts["word"].render(sub, True, DIM), (cx + 6, box.y + 42))
        top = sorted(field.items(), key=lambda x: -x[1])[:5]
        if top:
            mx = top[0][1] or 1.0
            y = box.y + 60
            for w, v in top:
                if v < 0.05:
                    continue
                bw = int((col_w - 16) * min(1.0, v / mx))
                bar = ACCENT if active else ACCENT_DIM
                pygame.draw.rect(screen, bar, (cx + 8, y, max(2, bw), 12),
                                 border_radius=2)
                screen.blit(fonts["word"].render(w, True, TEXT), (cx + 12, y))
                y += 16

    screen.blit(fonts["word"].render("согласованность полей", True, DIM),
                (box.x + 12, box.y + 140))
    spark = pygame.Rect(box.x + 12, box.y + 152, box.w - 24, 18)
    pygame.draw.rect(screen, TRACK, spark, border_radius=3)
    if len(coh_hist) >= 2:
        step = spark.w / (max(len(coh_hist), 2) - 1)
        pts = []
        for i, v in enumerate(coh_hist):
            x = spark.right - (len(coh_hist) - 1 - i) * step
            y = spark.bottom - 2 - max(0.0, min(1.0, v)) * (spark.h - 4)
            pts.append((x, y))
        pygame.draw.lines(screen, ACCENT, False, pts, 2)

    if focus is not None and focus in brain.neurons:
        n = brain.neurons[focus]
        deg = sum(1 for a, b in brain.links if focus in (a, b))
        lines = [n.word or "скрытый нейрон",
                 f"слой: {LAYER_NAMES[n.layer]}",
                 f"связей: {deg}"]
        if n.word and n.word in brain.wgroup:
            lines.append(f"группа: {brain.wgroup[n.word]}")
        if n.id in brain.noisy:
            lines.append("шумовой нейрон")
        width = max(fonts["ui"].size(t)[0] for t in lines) + 20
        tip = pygame.Rect(mouse[0] + 16, mouse[1] + 14, width, 16 + len(lines) * 18)
        tip.right = min(tip.right, NET_W - 8)
        tip.bottom = min(tip.bottom, H - 8)
        rounded(screen, tip, PANEL_2, BORDER, 8)
        for i, t in enumerate(lines):
            col = TEXT if i == 0 else MUTED
            screen.blit(fonts["ui"].render(t, True, col), (tip.x + 10, tip.y + 8 + i * 18))


# ИЗМ: поддерживает как 2-элементные [who, text], так и 3-элементные
# [who, text, score]. Для ответа сети рисуется «Сеть · опора X.XX».
def wrap_lines(entries, font, width):
    lines = []
    for item in entries:
        who, msg = item[0], item[1]
        score = item[2] if len(item) > 2 else None
        if who != "thought":
            col = {"you": C_YOU, "net": C_NET, "sys": C_SYS}[who]
            if who == "net" and score is not None:
                label = f"Сеть · опора {score:.2f}"
            else:
                label = {"you": "Вы", "net": "Сеть", "sys": "•"}[who]
            lines.append((label, col, True))
            body_col = DIM if who == "sys" else TEXT
        else:
            body_col = C_THOUGHT
        for ln in wrap(msg, font, width):
            lines.append((ln, body_col, False))
        if who != "thought":
            lines.append(("", DIM, False))
    return lines


VIEW = {}                       # имя панели -> размеры и строки последней отрисовки
SEL = {"panel": None, "a": None, "b": None, "drag": False}
SEL_BG = (72, 98, 142)
LM_BTN_RECT = pygame.Rect(0, 0, 0, 0)   # NEW: кликабельная кнопка LM


def _char_at(font, s, x):
    best, best_d = 0, None
    for i in range(len(s) + 1):
        d = abs(font.size(s[:i])[0] - x)
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def point_at(name, pos):
    v = VIEW.get(name)
    if not v or not v["lines"] or v["rows"] <= 0:
        return None
    rect, pad = v["rect"], v["pad"]
    row = max(0, min(v["rows"] - 1, (pos[1] - (rect.y + pad)) // 20))
    idx = v["start"] + row
    s, font = v["lines"][idx]
    x = pos[0] - (rect.x + pad)
    return (idx, _char_at(font, s, x) if s else 0)


def panel_at(pos):
    for name, rect in (("think", THINK_RECT), ("chat", CHAT_RECT)):
        if rect.collidepoint(pos):
            return name
    return None


def selected_text():
    name, a, b = SEL["panel"], SEL["a"], SEL["b"]
    if not name or a is None or b is None or a == b or name not in VIEW:
        return ""
    lo, hi = sorted([a, b])
    lines = VIEW[name]["lines"]
    out = []
    for li in range(lo[0], hi[0] + 1):
        s = lines[li][0]
        sc = lo[1] if li == lo[0] else 0
        ec = hi[1] if li == hi[0] else len(s)
        out.append(s[sc:ec])
    return "\n".join(out)


def draw_panel(screen, fonts, rect, entries, scroll, name=""):
    rounded(screen, rect, PANEL, BORDER, 10)
    pad = 10
    width = rect.w - 2 * pad - 10
    lines = wrap_lines(entries, fonts["chat"], width)
    max_rows = max(1, (rect.h - 2 * pad) // 20)
    maxs = max(0, len(lines) - max_rows)
    scroll = max(0, min(scroll, maxs))
    end = len(lines) - scroll
    start = max(0, end - max_rows)

    VIEW[name] = {
        "rect": rect, "pad": pad, "start": start, "rows": end - start,
        "lines": [(s, fonts["title"] if head else fonts["chat"]) for s, col, head in lines],
    }

    screen.set_clip(rect.inflate(-4, -4))
    sel = SEL["panel"] == name and SEL["a"] is not None and SEL["b"] is not None \
        and SEL["a"] != SEL["b"]
    if sel:
        lo, hi = sorted([SEL["a"], SEL["b"]])
        for li in range(max(start, lo[0]), min(end, hi[0] + 1)):
            s, f = VIEW[name]["lines"][li]
            sc = lo[1] if li == lo[0] else 0
            ec = hi[1] if li == hi[0] else len(s)
            x1 = rect.x + pad + f.size(s[:sc])[0]
            x2 = rect.x + pad + f.size(s[:ec])[0]
            if li < hi[0]:
                x2 += 6
            y0 = rect.y + pad + (li - start) * 20
            pygame.draw.rect(screen, SEL_BG, (x1, y0, max(4, x2 - x1), 20))
    y = rect.y + pad
    for s, col, head in lines[start:end]:
        if s:
            f = fonts["title"] if head else fonts["chat"]
            screen.blit(f.render(s, True, col), (rect.x + pad, y))
        y += 20
    screen.set_clip(None)

    if maxs:
        track = pygame.Rect(rect.right - 7, rect.y + pad, 3, rect.h - 2 * pad)
        th = max(18, int(track.h * max_rows / len(lines)))
        ty = track.y + int((track.h - th) * (1 - scroll / maxs))
        pygame.draw.rect(screen, ACCENT_DIM, (track.x, ty, 3, th), border_radius=2)
    if scroll > 0:
        hint = fonts["word"].render("↓ к последнему", True, MUTED)
        pill = pygame.Rect(0, 0, hint.get_width() + 16, 18)
        pill.bottomright = (rect.right - 12, rect.bottom - 6)
        rounded(screen, pill, PANEL_2, BORDER, 9)
        screen.blit(hint, (pill.x + 8, pill.y + 2))
    return scroll


# ИЗМ: бейдж LM превращён в кликабельную кнопку. Рамка кнопки сохраняется
# в глобальном LM_BTN_RECT, чтобы обработчик кликов мог её поймать.
def draw_chat(screen, thoughts, chat, text, fonts, now, progress, sliders, scroll,
              lm_on, lm_available=True):
    x0 = NET_W
    pygame.draw.rect(screen, PANEL_BG, (x0, 0, CHAT_W, H))
    pygame.draw.line(screen, BORDER, (x0, 0), (x0, H), 1)

    header(screen, fonts, "МЫСЛИ", x0 + 18, 12, C_THOUGHT)
    screen.blit(fonts["word"].render("выделите мышью · Ctrl+C копирует", True, DIM),
                (x0 + 100, 16))
    scroll["think"] = draw_panel(screen, fonts, THINK_RECT, thoughts, scroll["think"], "think")

    header(screen, fonts, "ЧАТ", x0 + 18, 326, ACCENT_HI)
    screen.blit(fonts["word"].render("Ctrl+C — копировать", True, DIM), (x0 + 100, 330))

    # NEW: кнопка-тумблер LM
    if not lm_available:
        label, base_col = "LM: нет", DIM
    elif lm_on:
        label, base_col = "LM: вкл", (130, 190, 140)
    else:
        label, base_col = "LM: выкл", (210, 170, 110)

    badge = fonts["title"].render(label, True, base_col)
    pad_x, pill_h = 10, 22
    pill_w = badge.get_width() + pad_x * 2
    LM_BTN_RECT.x = x0 + CHAT_W - 18 - pill_w
    LM_BTN_RECT.y = 323
    LM_BTN_RECT.w = pill_w
    LM_BTN_RECT.h = pill_h

    hovered = lm_available and LM_BTN_RECT.collidepoint(pygame.mouse.get_pos())
    bg_col = (46, 50, 60) if hovered else PANEL_2
    border_col = (235, 238, 242) if hovered else base_col
    pygame.draw.rect(screen, bg_col, LM_BTN_RECT, border_radius=10)
    pygame.draw.rect(screen, border_col, LM_BTN_RECT, 1, border_radius=10)
    ty = LM_BTN_RECT.y + (pill_h - badge.get_height()) // 2
    screen.blit(badge, (LM_BTN_RECT.x + pad_x, ty))

    scroll["chat"] = draw_panel(screen, fonts, CHAT_RECT, chat, scroll["chat"], "chat")

    for i, s in enumerate(sliders):
        s.draw(screen, fonts, x0 + 18, H - 204 + i * 32, CHAT_W - 36)

    if progress is not None:
        frac, label_p = progress
        screen.blit(fonts["word"].render(label_p, True, MUTED), (x0 + 18, H - 110))
        bar = pygame.Rect(x0 + 18, H - 90, CHAT_W - 36, 5)
        pygame.draw.rect(screen, TRACK, bar, border_radius=3)
        if frac > 0:
            pygame.draw.rect(screen, C_SPEAK,
                             (bar.x, bar.y, max(4, int(bar.w * frac)), bar.h),
                             border_radius=3)

    rounded(screen, INPUT_RECT, PANEL_2, BORDER, 10)
    caret = "|" if int(now * 2) % 2 == 0 else " "
    if not text:
        screen.blit(fonts["chat"].render("напишите сообщение…", True, DIM),
                    (INPUT_RECT.x + 12, INPUT_RECT.y + 10))
    shown = text
    while shown and fonts["chat"].size(shown + "|")[0] > INPUT_RECT.w - 24:
        shown = shown[1:]
    screen.blit(fonts["chat"].render(shown + caret, True, TEXT),
                (INPUT_RECT.x + 12, INPUT_RECT.y + 10))

def speech(words, entry, now, gap, live=False):
    return {"words": list(words), "start": now, "shown": 0, "prev": None,
            "entry": entry, "gap": gap, "live": live}


def _set_score(entry, score):
    """ИЗМ: записывает оценку в 2-элементную запись, расширяя её до 3-элементной."""
    if len(entry) >= 3:
        entry[2] = score
    else:
        entry.append(score)


# ---------- главный цикл ----------

def main():
    global MAX_NEURONS
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Brain Chat")
    pygame.key.start_text_input()
    clock = pygame.time.Clock()

    fam = "dejavusans,arial,liberationsans,notosans,freesans"
    fonts = {
        "ui": pygame.font.SysFont(fam, 15),
        "word": pygame.font.SysFont(fam, 12),
        "chat": pygame.font.SysFont(fam, 16),
        "title": pygame.font.SysFont(fam, 13, bold=True),
    }

    bg = make_background()
    stars = [(random.uniform(0, NET_W), random.uniform(0, H), random.uniform(0, 6.28))
             for _ in range(160)]
    layers = (pygame.Surface((NET_W, H), pygame.SRCALPHA), pygame.Surface((NET_W, H)))
    # NEW: слой для оболочек смысловых групп
    group_layer = pygame.Surface((NET_W, H), pygame.SRCALPHA)
    cache = {}

    if os.path.exists(SAVE_PATH):
        try:
            brain = load_brain()
            status = f"Загружена сеть: {len(brain.neurons)} нейронов, {len(brain.links)} связей."
        except Exception as e:
            brain = Brain()
            status = f"Не удалось загрузить сохранение ({e}), начата новая сеть."
    else:
        brain = Brain()
        status = "Новая сеть."
    mind = Mind(brain)
    answerer = Answerer(brain, mind)
    sliders = [
        Slider("частота мыслей", 50, lambda v: f"каждые {think_sec(v):.2f} с"),
        Slider("длина ответа", 8, lambda v: f"{int(round(v))} слов"),
        Slider("зазор между словами", 30, lambda v: f"{gap_sec(v):.2f} с"),
    ]
    lm, lm_status = start_lm()
    lm_on = True                          # NEW: включена ли LLM в текущем режиме
    episodes = EpisodeMemory()
    pending = None
    last_user_msg = ""
    last_support = None
    coh_hist = deque(maxlen=COH_HIST)
    coh_acc = 0.0
    last_lm = None
    dragging = None
    thoughts = []
    scroll = {"think": 0, "chat": 0}

    chat = [["sys", status + " Напишите /help — список команд."]]
    text = ""
    acc = 0.0
    now = 0.0
    save_timer = 0.0
    running = True
    resp = None
    last_answer = None
    msg_count = 0
    prev_q, last_q = [], []

    def sys_msg(msg):
        chat.append(["sys", msg])

    sys_msg(lm_status)

    while running:
        dt = clock.tick(60) / 1000.0
        now += dt

        save_timer += dt
        if save_timer >= AUTOSAVE_SEC:
            save_timer = 0.0
            try:
                save_brain(brain)
            except OSError as e:
                sys_msg(f"Автосохранение не удалось: {e}")

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key == pygame.K_c and pygame.key.get_mods() & pygame.KMOD_CTRL \
                        and selected_text():
                    body = selected_text()
                    if copy_to_clipboard(body):
                        sys_msg(f"Скопировано: {len(body)} симв.")
                    else:
                        sys_msg("Буфер обмена недоступен. Текст сохранён: /copy")
                elif e.key == pygame.K_a and pygame.key.get_mods() & pygame.KMOD_CTRL \
                        and panel_at(pygame.mouse.get_pos()):
                    pname = panel_at(pygame.mouse.get_pos())
                    lines = VIEW[pname]["lines"]
                    SEL.update(panel=pname, a=(0, 0),
                               b=(len(lines) - 1, len(lines[-1][0])), drag=False)
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    msg = text.strip()
                    text = ""
                    if msg == "/reset":
                        brain = Brain()
                        mind = Mind(brain)
                        answerer = Answerer(brain, mind)
                        cache.clear()
                        resp = None
                        last_answer = None
                        sys_msg("Сеть сброшена (файл сохранения не тронут).")
                    elif msg == "/save":
                        save_brain(brain)
                        sys_msg(f"Сохранено в {SAVE_PATH}")
                    elif msg == "/load":
                        if os.path.exists(SAVE_PATH):
                            brain = load_brain()
                            mind = Mind(brain)
                            answerer = Answerer(brain, mind)
                            cache.clear()
                            resp = None
                            last_answer = None
                            sys_msg(f"Загружено: {len(brain.neurons)} нейронов, "
                                    f"{len(brain.links)} связей.")
                        else:
                            sys_msg("Файл сохранения не найден.")
                    elif msg == "/sleep":
                        pruned, ngroups = brain.consolidate()
                        for ep in episodes.items:
                            brain.train_words(tokenize(ep["q"], limit=None) + tokenize(ep["a"], limit=None))
                        sys_msg(f"Консолидация: удалено {pruned} связей, групп {ngroups}, "
                                f"повторено эпизодов: {len(episodes.items)}.")
                    elif msg == "/groups":
                        for line in brain.group_report() or ["Групп пока нет."]:
                            sys_msg(line)
                    elif msg == "/mind":
                        for line in mind.report():
                            sys_msg(line)
                        sys_msg(f"эпизодов в памяти: {len(episodes.items)}")
                        sys_msg(f"модель: контекст {RUNTIME['context_chars']} симв., "
                                f"помнит {RUNTIME['history']} обмена, "
                                f"время {RUNTIME['time_budget']:g} с, "
                                f"длина {RUNTIME['max_tokens']} токенов")
                        sys_msg(f"нейроны: {len(brain.neurons)} из {MAX_NEURONS}")
                        sys_msg(f"LLM: {'включена' if lm_on else 'выключена (только граф)'}")
                        if last_support is not None:
                            sys_msg(f"опора последнего ответа на граф: {last_support:.2f}")
                        if lm is not None and lm.backend.speed_text():
                            sys_msg(f"скорость LM: {lm.backend.speed_text()}")
                    # NEW: /lm — включение/выключение LLM
                    elif msg == "/lm" or msg.startswith("/lm "):
                        parts = msg.split(None, 1)
                        arg = parts[1].strip().lower() if len(parts) > 1 else ""
                        valid = True
                        if arg in ("", "toggle", "t"):
                            lm_on = not lm_on
                        elif arg in ("on", "вкл", "1", "true"):
                            lm_on = True
                        elif arg in ("off", "выкл", "0", "false"):
                            lm_on = False
                        else:
                            valid = False
                            sys_msg(f"Формат: /lm [on|off]. Сейчас LLM "
                                    f"{'вкл' if lm_on else 'выкл'}")
                        if valid:
                            if lm is None and lm_on:
                                lm_on = False
                                sys_msg("LM не подключена, переключение невозможно. "
                                        "Отвечает граф.")
                            else:
                                if not lm_on and pending is not None:
                                    pending["entry"][1] += " (отменено: LLM выкл)"
                                    pending = None
                                sys_msg("LLM: включена" if lm_on
                                        else "LLM: выключена — отвечает только граф")
                    elif msg.startswith("/recall"):
                        query = msg[len("/recall"):].strip()
                        found = episodes.recall(query, k=3) if query else []
                        if not found:
                            sys_msg("Ничего похожего в памяти нет.")
                        for sim, ep in found:
                            sys_msg(f"[{sim:.2f}] {ep['q']} → {ep['a']}")
                    elif msg == "/copy":
                        body = session_text(chat, thoughts)
                        path = write_session(chat, thoughts)
                        if copy_to_clipboard(body):
                            sys_msg(f"Скопировано в буфер обмена. Файл: {path}")
                        else:
                            sys_msg(f"Буфер обмена недоступен. Текст сохранён в файл: {path}")
                    elif msg == "/log":
                        path = write_session(chat, thoughts)
                        sys_msg(f"Журнал сохранён: {path}")
                    elif msg == "/help":
                        for line in HELP_LINES:
                            sys_msg(line)
                    elif msg.split()[:1] == ["/neurons"]:
                        parts = msg.split()
                        if len(parts) == 2 and parts[1].isdigit():
                            MAX_NEURONS = max(NEURON_LIMITS[0], min(NEURON_LIMITS[1], int(parts[1])))
                            sys_msg(f"Лимит нейронов: {MAX_NEURONS}. "
                                    f"Сейчас: {len(brain.neurons)}.")
                        else:
                            sys_msg(f"Лимит нейронов: {MAX_NEURONS} (сейчас {len(brain.neurons)}). "
                                    f"Формат: /neurons 700")
                    elif msg.split()[:1] == ["/assoc"]:
                        ids = tokenize(msg[len("/assoc"):], limit=None)
                        if len(ids) < 2:
                            sys_msg("Формат: /assoc слово слово слово — эти слова свяжутся между собой.")
                        else:
                            brain.train_words(ids)
                            brain.train_words(ids)
                            brain.regroup()
                            sys_msg(f"Связано слов: {len(ids)} — {' '.join(ids)}.")
                    elif msg.split()[:1] and msg.split()[0] in TUNE_CMDS:
                        cmd = msg.split()[0]
                        name, label, unit = TUNE_CMDS[cmd]
                        arg = msg[len(cmd):].strip().replace(",", ".")
                        if arg:
                            try:
                                new = set_option(name, float(arg))
                                sys_msg(f"{label}: {new:g} {unit}")
                            except ValueError:
                                sys_msg(f"Формат: {cmd} число (например {cmd} 30)")
                        else:
                            sys_msg(f"{label}: {RUNTIME[name]:g} {unit}")
                    elif msg.startswith("/temp"):
                        parts = msg.split()
                        if len(parts) == 2:
                            try:
                                brain.temp = max(0.0, min(3.0, float(parts[1])))
                                sys_msg(f"Температура: {brain.temp:.2f}")
                            except ValueError:
                                sys_msg("Формат: /temp 0.7")
                        else:
                            sys_msg(f"Температура: {brain.temp:.2f}")
                    elif msg in ("/good", "/bad"):
                        if last_answer is None:
                            sys_msg("Нет последнего ответа.")
                        elif msg == "/good":
                            brain._reinforce_answer(last_answer)
                            if last_user_msg:
                                brain.train_words(tokenize(last_user_msg, limit=None) + last_answer)
                            if last_lm is not None:
                                LMBackend.log_feedback(last_lm[0], last_lm[1], +1)
                                episodes.add(last_lm[0], last_lm[1])
                            elif last_user_msg:
                                episodes.add(last_user_msg, " ".join(last_answer))
                            sys_msg("Ответ закреплён и сохранён в эпизодах.")
                        else:
                            brain.punish(last_answer)
                            if last_lm is not None:
                                LMBackend.log_feedback(last_lm[0], last_lm[1], -1)
                            if last_user_msg:
                                episodes.drop(last_user_msg)
                            sys_msg("Ответ ослаблен, эпизод удалён из памяти.")
                    elif msg.startswith("/teach"):
                        body = msg[len("/teach"):].strip()
                        if not body or not last_q:
                            sys_msg("Формат: /teach правильный ответ (после вопроса).")
                        else:
                            ans_words = tokenize(body, limit=None)
                            brain.train_words(last_q + ans_words)
                            brain._reinforce_answer(ans_words)
                            brain.regroup()
                            if last_user_msg:
                                episodes.add(last_user_msg, body)
                            sys_msg(f"Запомнила: {len(ans_words)} слов связано с вопросом.")
                    elif msg.startswith("/conf"):
                        parts = msg.split()
                        if len(parts) == 2:
                            try:
                                mind.conf_min = max(0.0, min(1.0, float(parts[1])))
                            except ValueError:
                                pass
                        sys_msg(f"Порог уверенности: {mind.conf_min:.2f}")
                    elif msg.startswith("/train"):
                        parts = msg.split(maxsplit=1)
                        if len(parts) < 2:
                            sys_msg("Формат: /train путь_к_файлу.txt")
                        else:
                            t0 = time.time()
                            try:
                                lines_n, words_n = brain.train_file(parts[1].strip('"'))
                                sys_msg(f"Обучено на {lines_n} строках, {words_n} словах "
                                        f"за {time.time() - t0:.1f} с.")
                            except OSError as ex:
                                sys_msg(f"Не удалось открыть файл: {ex}")
                    elif msg:
                        if resp is not None:
                            if not resp.get("live"):
                                resp["entry"][1] = " ".join(resp["words"]) + "."
                            resp = None
                        if pending is not None:
                            pending["entry"][1] = "(заменено новым вопросом)"
                            pending = None
                        chat.append(["you", msg])
                        scroll["chat"] = 0
                        cur = tokenize(msg, limit=None)
                        brain.train_words(prev_q[-3:] + cur)
                        prev_q = last_q = cur
                        last_user_msg = msg
                        target = max(1, int(round(sliders[1].value)))
                        msg_count += 1
                        if msg_count % REGROUP_EVERY == 0:
                            brain.regroup()
                        recalled = episodes.recall(msg, k=2)
                        entry = ["net", "думаю…"]
                        chat.append(entry)
                        gap = gap_sec(sliders[2].value)
                        # ИЗМ: (lm is None or not lm_on) вместо (lm is None)
                        if (lm is None or not lm_on) and recalled and recalled[0][0] >= RECALL_STRONG:
                            ep = recalled[0][1]
                            st = answerer.begin(msg, target)
                            if st["thought"] is not None:
                                thoughts.append(["thought", st["thought"].describe()])
                                thoughts = thoughts[-300:]
                            answerer.learn(msg, tokenize(ep["a"], limit=None))
                            last_answer = tokenize(ep["a"], limit=None) or None
                            resp = speech(ep["a"].split(), entry, now, gap)
                        else:
                            st = answerer.begin(msg, target)
                            if st["thought"] is not None:
                                thoughts.append(["thought", st["thought"].describe()])
                                thoughts = thoughts[-300:]
                            # ИЗМ: (lm is not None and lm_on)
                            if lm is not None and lm_on:
                                memory = [f"«{ep['q']}» → «{ep['a']}»"
                                          for sim, ep in recalled if sim >= RECALL_WEAK]
                                q_req, note, remember = answerer.request(st)
                                req = lm.submit(q_req, (), memory, note, remember)
                                pending = {"req": req, "entry": entry, "state": st}
                            else:
                                final = answerer.final(st)
                                if final:
                                    reply_text, score, from_lm = final
                                    last_answer = tokenize(reply_text, limit=None) or None
                                    last_support = score
                                    # ↓ новое: адаптация и обучение взвешены по уверенности
                                    if score > LEARN_MIN:  # из reasoning.py
                                        brain.adapt(tokenize(st["q"]) + last_answer, confidence=min(1.0, score))
                                        if last_user_msg:
                                            answerer.learn(last_user_msg, last_answer, confidence=min(1.0, score))
                                else:
                                    entry[1] = "пока не знаю этого. Научите: /teach ответ"
                    chat = chat[-300:]
                elif e.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                    k = "think" if pygame.key.get_mods() & pygame.KMOD_SHIFT else "chat"
                    scroll[k] = max(0, scroll[k] + (5 if e.key == pygame.K_PAGEUP else -5))
                elif e.key == pygame.K_END:
                    scroll["chat"] = 0
                    scroll["think"] = 0
                elif e.key == pygame.K_BACKSPACE:
                    text = text[:-1]
                elif e.key == pygame.K_DELETE:
                    text = ""
            elif e.type == pygame.MOUSEWHEEL:
                pos = pygame.mouse.get_pos()
                if THINK_RECT.collidepoint(pos):
                    scroll["think"] = max(0, scroll["think"] + e.y * 3)
                elif CHAT_RECT.collidepoint(pos):
                    scroll["chat"] = max(0, scroll["chat"] + e.y * 3)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                # NEW: клик по кнопке LM переключает режим (не мешает слайдерам/выделению)
                if LM_BTN_RECT.collidepoint(e.pos):
                    if lm is None:
                        sys_msg("LM не подключена — переключение невозможно. "
                                "Отвечает граф.")
                    else:
                        lm_on = not lm_on
                        if not lm_on and pending is not None:
                            pending["entry"][1] += " (отменено: LLM выкл)"
                            pending = None
                        sys_msg("LLM: включена" if lm_on
                                else "LLM: выключена — отвечает только граф")
                else:
                    for i, s in enumerate(sliders):
                        if s.hit(e.pos):
                            dragging = i
                            s.set_from_x(e.pos[0])
                    if dragging is None:
                        pname = panel_at(e.pos)
                        if pname:
                            p = point_at(pname, e.pos)
                            SEL.update(panel=pname, a=p, b=p, drag=True)
                        else:
                            SEL.update(panel=None, a=None, b=None, drag=False)
            elif e.type == pygame.MOUSEMOTION and dragging is not None:
                sliders[dragging].set_from_x(e.pos[0])
            elif e.type == pygame.MOUSEMOTION and SEL["drag"]:
                p = point_at(SEL["panel"], e.pos)
                if p is not None:
                    SEL["b"] = p
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                dragging = None
                SEL["drag"] = False
            elif e.type == pygame.TEXTINPUT:
                text += e.text
            elif e.type == pygame.MOUSEMOTION and SEL["drag"]:
                p = point_at(SEL["panel"], e.pos)
                if p is not None:
                    SEL["b"] = p
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                dragging = None
                SEL["drag"] = False
            elif e.type == pygame.TEXTINPUT:
                text += e.text

        mind.think_every = think_sec(sliders[0].value)
        thought = mind.tick(dt)
        if thought:
            thoughts.append(["thought", thought])
            thoughts = thoughts[-300:]

        # ИЗМ: обрабатываем pending только при включённой LLM
        if lm_on and pending is not None:
            partial_text = lm.partial(pending["req"])
            if partial_text:
                pending["entry"][1] = partial_text
            res = lm.poll(pending["req"])
            if res is not None:
                st = pending["state"]
                if answerer.feed(st, res):
                    q_req, note, remember = answerer.request(st)
                    pending["req"] = lm.submit(q_req, (), (), note, remember)
                    pending["entry"][1] = "перепроверяю…"
                else:
                    entry_ref = pending["entry"]
                    pending = None
                    final = answerer.final(st)
                    if final:
                        reply_text, score, from_lm = final
                        last_support = score
                        last_answer = tokenize(reply_text, limit=None) or None
                        if from_lm:
                            last_lm = (st["q"], reply_text)
                        entry_ref[1] = reply_text
                        _set_score(entry_ref, score)
                        resp = speech(reply_text.split(), entry_ref, now,
                                      gap_sec(sliders[2].value), live=True)
                    else:
                        entry_ref[1] = "пока не знаю этого. Научите: /teach ответ"

        progress = None
        if resp is not None:
            elapsed = now - resp["start"]
            words = resp["words"]
            gap = resp["gap"]
            total = len(words) * gap
            want = min(len(words), int(elapsed / gap) + 1)
            while resp["shown"] < want:
                w = words[resp["shown"]]
                nid = brain.words.get(_norm(w))
                if nid is not None:
                    brain.forced.add(nid)
                    if resp["prev"] is not None and resp["prev"] in brain.neurons:
                        brain.particles.append([resp["prev"], nid, 0.0, 0.05, True])
                    resp["prev"] = nid
                resp["shown"] += 1
            done = resp["shown"] == len(words)
            if not resp.get("live"):
                resp["entry"][1] = " ".join(words[:resp["shown"]]) + ("" if done else " …")
            progress = None
            if done and elapsed >= total:
                resp = None

        acc += dt
        while acc >= SIM_DT:
            brain.step(grow=True, plastic=True)
            acc -= SIM_DT
        brain.trim_particles()
        brain.move_particles()

        coh_acc += dt
        if coh_acc >= 0.1:
            coh_acc = 0.0
            coh_hist.append(max(0.0, min(1.0, mind.coherence())))
        mouse = pygame.mouse.get_pos()
        focus = nearest_neuron(brain, mouse[0], mouse[1])
        screen.blit(bg, (0, 0))
        # NEW: оболочки групп — позади связей и узлов
        draw_group_hulls(screen, brain, group_layer)
        draw_scene(screen, brain, layers, fonts, cache, stars, now, focus=focus,
                   info_extra=f"   эпизодов {len(episodes.items)}")
        # ИЗМ: draw_overlay теперь принимает mind вместо side
        draw_overlay(screen, fonts, brain, mind, coh_hist, focus, mouse, now)
        # ИЗМ: передаём lm_on для бейджа
        draw_chat(screen, thoughts, chat, text, fonts, now, progress, sliders, scroll,
                  lm_on, lm_available=(lm is not None))
        pygame.display.flip()

    try:
        save_brain(brain)
    except OSError:
        pass
    try:
        write_session(chat, thoughts)
    except OSError:
        pass
    pygame.quit()


if __name__ == "__main__":
    main()