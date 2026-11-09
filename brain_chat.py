import json
import math
import os
import random
import re
import time
from collections import deque

import pygame

from mind import Mind, STOPWORDS, MIN_WORD_LEN, cosine, key
from lm_backend import LMBackend, LMWorker, RUNTIME, set_option
from episodes import EpisodeMemory, RECALL_STRONG, RECALL_WEAK
from reasoning import Answerer, REVISE_ROUNDS
from nets.manager import NetsManager
from nets.llm_tasks import LLMTaskRunner
from nets.teach import apply_plan
from cortex import Cortex
from explain import HELP, answer_label, describe_stream, describe_thought, status_text
import textedit as te
import clipboard
from rl_policy import Bandit, arms_for
from ui_feedback import FeedbackButtons, NeuronMenu
from rich_text import RLine, plain_lines, markdown_lines, x_at, index_at
W, H = 1280, 800
CHAT_W = 420
NET_W = W - CHAT_W
GRID = 50
CELL = min(NET_W, H) / GRID
OX = (NET_W - GRID * CELL) / 2
OY = (H - GRID * CELL) / 2

SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain_state.json")
BRAIN_FORMAT = 6        # версия формата brain_state.json
AUTOSAVE_SEC = 60.0
CORTEX_EVERY = 45.0     # сек между фоновыми проходами слоёв и вопросами сети

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
# Лимита нейронов нет: сеть растёт вместе с обучением (новые слова, связи, мосты).
GROW_P = 0.08
SPARK_P = 0.06
ISO_LIMIT = 60
PRUNE = 0.06
HEBB_RATE = 0.05
ASSOC_Q_MAX = 6          # сколько слов вопроса связывать с ответом
ASSOC_A_MAX = 12         # сколько слов ответа связывать с вопросом
DISTILL_BUDGET = 5      # сколько раз одна пара (a,b) дистиллируется до насыщения
KCONF_MAX = 1.0         # потолок уверенности в токене знания
# step() вызывается в цикле симуляции каждые SIM_DT (10 раз в секунду, не 60 FPS).
# 0.9998^10 ≈ 0.998 в секунду; связь без подкрепления уходит к PRUNE примерно за 23 минуты.
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
    "Обучение с подкреплением: /policy — какие стратегии ответа пользователь оценил лучше.",
    "Цепочка сетей: /chain — трасса последнего ответа; /nets — состояние сетей и адаптаций.",
    "Кнопки под чатом: верно / неверно / переформулировать. Правая кнопка по нейрону: меню; /rename старое новое.",
    "Обучение: /assoc слово слово… — связать слова между собой; /train файл.txt — учить по тексту.",
    "Память: /recall текст — найти похожие прошлые вопросы; /sleep — повторить память.",
    "Сеть: /neurons — число нейронов; /groups — смысловые группы; /cortex — слои и вопросы сети; /facts — токены знаний; /save, /load, /reset.",
    "Модель: /ctx N — символов контекста; /history N — сколько обменов помнит;",
    "        /time N — секунд на ответ (0 — без лимита); /tokens N — длина ответа.",
    "LLM: кнопка «LM: вкл / выкл» в шапке чата — клик переключает режим; "
    "/lm on|off — то же из строки ввода.",
    "Состояние: /mind — что происходит в сети и какие настройки заданы.",
    "Копирование: /copy — мысли и чат в буфер обмена и в файл session_log.txt; /log — только файл.",
    "Значение без числа показывает текущую настройку, например /time.",
    "Как читать: мысли — ассоциации, не ответы; «уверенность» — насколько ответ опирается на граф; "
    "строка вверху справа — чем занята сеть.",
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
HOT_LINK = 8                # кадров подсветки связи (~0.8 с, v16: короче — меньше белой сетки)
HOT_CURVE = 0.05          # связь горячее этого порога рисуется кривой
HALO_MIN = 0.02           # гало рисуется только у нейронов горячее этого
HULL_EVERY = 8            # оболочки групп пересчитываются раз в N кадров
FIRE_CAP = 0.02          # доля нейронов, которые горят за шаг (k-WTA, v16)
FIRE_MIN = 6             # минимум победителей на шаг


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


def write_session(chat, thoughts, path=None):
    path = path or SESSION_PATH
    with open(path, "w", encoding="utf-8") as f:
        f.write(session_text(chat, thoughts))
    return path


def copy_to_clipboard(text):
    """Копирует в буфер: системный, а если его нет — внутренний. Всегда True."""
    clipboard.copy(text)
    return True


def get_clipboard_text():
    """Текст из буфера (системного или внутреннего)."""
    return clipboard.paste()


CAM = {"cell": CELL, "ox": OX, "oy": OY}    # камера: масштаб и сдвиг


def cell_xy(r, c):
    return (CAM["ox"] + (c + 0.5) * CAM["cell"], CAM["oy"] + (r + 0.5) * CAM["cell"])


def update_view(brain, dt, rate=6.0):
    """Камера следует за сетью: масштаб и центр подстраиваются под её границы.

    Сетка неограничена, поэтому фиксированные координаты 0..GRID больше не годятся.
    Сеть всегда помещается в окно; переход сглажен, чтобы не дёргать картинку.
    """
    if not brain.neurons:
        return
    rs = [n.r for n in brain.neurons.values()]
    cs = [n.c for n in brain.neurons.values()]
    r0, r1, c0, c1 = min(rs), max(rs), min(cs), max(cs)
    span = max(r1 - r0 + 1, c1 - c0 + 1, GRID)
    cell_t = min(NET_W, H) / (span + 2)
    cx, cy = (c0 + c1 + 1) / 2, (r0 + r1 + 1) / 2
    VIEW_USER["base"], VIEW_USER["auto"] = cell_t, (cx, cy)
    if VIEW_USER["on"]:
        cell_t *= VIEW_USER["zoom"]
        cx, cy = VIEW_USER["wcx"], VIEW_USER["wcy"]
    ox_t = NET_W / 2 - cx * cell_t
    oy_t = H / 2 - cy * cell_t
    k = min(1.0, rate * max(dt, 0.0))
    CAM["cell"] += (cell_t - CAM["cell"]) * k
    CAM["ox"] += (ox_t - CAM["ox"]) * k
    CAM["oy"] += (oy_t - CAM["oy"]) * k


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
    """Слово из ответа LM (с пунктуацией) -> ключ словаря графа (первое слово)."""
    toks = tokenize(word, limit=None)
    return toks[0] if toks else None


def _norm_all(piece):
    """Кусок токена LM -> все слова из него: 'Нью-Йорк' -> ['нью', 'йорк'].
    Раньше брался только первый, и вторая часть составного слова терялась."""
    return tokenize(piece, limit=None)


def _feedback_weight(support, good):
    """Сила оценки пользователя. /good на неуверенном ответе — сильный сигнал,
    на уверенном — почти повтор известного. /bad на пограничном — слабый сигнал."""
    s = 1.0 if support is None else support
    if good:
        return 1.5 if s < 0.3 else (0.5 if s > 0.6 else 1.0)
    return 0.4 if s < 0.3 else 1.0


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
        self.word_seen = {}       # слово -> сколько раз участвовало в обучении
        self.kconf = {}           # слово -> уверенность (токен знания), 0..KCONF_MAX
        self.distill_count = {}   # пара (a,b) -> сколько раз дистиллирована
        self.support_hist = deque(maxlen=20)   # опора последних ответов (для A5)

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

    def feedback_neuron(self, nid, good):
        """Оценка нейрона из меню: верно — усилить его связи и уверенность в слове,
        неверно — ослабить. Это ручная награда, как /good и /bad, а не автообучение."""
        n = self.neurons.get(nid)
        if n is None:
            return
        for k in [k for k in self.links if nid in k]:
            w = self.links[k]
            if good:
                self.links[k] = min(2.0, w + HEBB_RATE * 2)
            else:
                w *= 0.5
                if w < PRUNE:
                    del self.links[k]
                else:
                    self.links[k] = w
        if n.word:
            self.bump_kconf([n.word], 0.2 if good else -0.2)

    def rename_word(self, old, new):
        """Переформулировать слово: нейрон сохраняет связи, меняется только подпись.
        Слово bigram-статистики не переписывается; её пары остаются под старым словом."""
        nid = self.words.get(old)
        if nid is None or not new or new in self.words:
            return False
        n = self.neurons[nid]
        del self.words[old]
        self.words[new] = nid
        n.word = new
        for table in (self.wgroup, self.kconf, self.word_seen):
            if old in table:
                table[new] = table.pop(old)
        return True

    def add_free(self, r, c, word=None, layer=None):
        """Нейрон в ближайшей свободной клетке. Всегда находит место: сетка бесконечна."""
        if (r, c) not in self.grid:
            return self.add(r, c, word, layer)
        rad = 1
        while True:   # занятых клеток конечное число, поэтому цикл завершится
            for dr in range(-rad, rad + 1):
                for dc in range(-rad, rad + 1):
                    if max(abs(dr), abs(dc)) != rad:
                        continue
                    rr, cc = r + dr, c + dc
                    if (rr, cc) not in self.grid:
                        return self.add(rr, cc, word, layer)
            rad += 1

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
        if random.random() < BRIDGE_P:
            m = self.add_free((a.r + b.r) // 2, (a.c + b.c) // 2, None, G_ASSOC)
            if m is not None:
                self.connect(a, m, 0.9)
                self.connect(m, b, 0.9)
                return
        self.connect(a, b, 0.45)

    def spawn(self, src, layer=None):
        if src is None or src.id not in self.neurons:
            src = random.choice(tuple(self.neurons.values()))
        target = layer if layer is not None else min(src.layer + 1, G_OUT)
        best, best_score = None, -1e9
        for _ in range(28):
            dr, dc = random.choice(NB8)
            r, c = src.r + dr, src.c + dc
            if (r, c) in self.grid:
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
                r, c = self.ring_cell(w)
                n = self.add_free(r, c, w, G_IN)
                if n is None:
                    continue
                nid = n.id
                if random.random() < 0.3:
                    self.spawn(n, G_ASSOC)
            ids.append(nid)
        return ids

    def train_words(self, words, weight=1.0):
        ids = self._ids_for(words)
        for w in words:
            self.word_seen[w] = self.word_seen.get(w, 0) + 1
        for i in range(len(ids)):
            for j in range(i + 1, min(len(ids), i + 1 + TRAIN_WINDOW)):
                a, b = ids[i], ids[j]
                if a == b:
                    continue
                k = key(a, b)
                gain = HEBB_RATE * weight / (j - i)  # ← умножено на weight
                if k in self.links:
                    self.links[k] = min(2.0, self.links[k] + gain)
                elif j == i + 1:
                    self.connect_words(self.neurons[a], self.neurons[b])
                if j == i + 1 and weight > 0.1:  # ← bigram только при весе
                    bk = (self.neurons[a].word, self.neurons[b].word)
                    self.bigram[bk] = self.bigram.get(bk, 0) + 1
        return ids

    def associate(self, q_words, a_words, weight=1.0):
        """Связь вопрос→ответ: слова вопроса соединяются с каждым словом ответа.

        train_words связывает только соседние слова (и усиливает дальние пары,
        только если связь уже есть). Поэтому ответ, отделённый от вопроса,
        не попадал в окрестность вопроса, и мысль по нему не находилась.
        Здесь пары создаются явно, с ограничением их числа.
        """
        if weight <= 0:
            return
        q = [w for w in dict.fromkeys(q_words) if w not in STOPWORDS] or list(dict.fromkeys(q_words))
        a = [w for w in dict.fromkeys(a_words) if w not in STOPWORDS] or list(dict.fromkeys(a_words))
        q, a = q[:ASSOC_Q_MAX], a[:ASSOC_A_MAX]
        if not q or not a:
            return
        qids = self._ids_for(q)
        aids = self._ids_for(a)
        for x in qids:
            for y in aids:
                if x == y:
                    continue
                k = key(x, y)
                if k in self.links:
                    self.links[k] = min(2.0, self.links[k] + HEBB_RATE * weight)
                else:
                    self.connect_words(self.neurons[x], self.neurons[y])
                    if key(x, y) in self.links:
                        self.links[key(x, y)] = max(self.links[key(x, y)], weight)

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
                # Частое слово (много обучения) весит больше редкого: центр группы
                # не сдвигается от одного случайного упоминания.
                wt = 1.0 + math.log1p(self.word_seen.get(w, 0))
                cnt[g] = cnt.get(g, 0.0) + wt
                a = acc.setdefault(g, {})
                for k, v in prof[w].items():
                    a[k] = a.get(k, 0.0) + wt * v
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
        up = ADAPT_UP * confidence * self.adapt_rate()
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
            if ga == gb:
                # Rescorla-Wagner: шаг пропорционален новизне (2 − w). Сильные связи
                # почти не двигаются, слабые быстро подтягиваются. При w = 1 шаг
                # совпадает с прежним, поэтому общая скорость обучения не меняется.
                w = min(2.0, w + up * (2.0 - w))
            else:
                w = w * (1 - down)
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
        cand = []
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
                cand.append(n)
            else:
                n.thr += (THRESH - n.thr) * 0.02
        # k-WTA: за шаг горит не больше FIRE_CAP доли нейронов. Без этого возбуждение
        # лавиной захватывает сеть (около трети нейронов за шаг) и перегружает процессор.
        # Принудительные нейроны (ответ, анимация слов) горят всегда. Проигравшие
        # сохраняют потенциал и могут выстрелить позже.
        cap = max(FIRE_MIN, int(FIRE_CAP * len(self.neurons)))
        forced_c = [n for n in cand if n.id in self.forced]
        free = sorted((n for n in cand if n.id not in self.forced), key=lambda n: -n.pot)
        room = max(0, cap - len(forced_c))
        for n in forced_c + free[:room]:
            n.pot = 0.0
            n.fired_at = self.tick
            n.thr += 0.03
            new.add(n.id)
        for n in free[room:]:
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
        for a, b in zip(answer, answer[1:]):
            na, nb = self.words.get(a), self.words.get(b)
            if na is None or nb is None:
                continue
            k = key(na, nb)
            gain = HEBB_RATE * 2 * weight
            if k in self.links:
                self.links[k] = min(2.0, self.links[k] + gain)
            if weight > 0.1:  # ← защита bigram
                self.bigram[(a, b)] = self.bigram.get((a, b), 0) + 1

    # ---------- токены знаний (B4) ----------

    def bump_kconf(self, words, step):
        """Уверенность в слове растёт от подтверждений и падает от /bad."""
        for w in words:
            if w in self.words:
                v = self.kconf.get(w, 0.0) + step
                self.kconf[w] = max(0.0, min(KCONF_MAX, v))

    def top_facts(self, n=20):
        return sorted(((w, v) for w, v in self.kconf.items() if v > 0),
                      key=lambda kv: -kv[1])[:n]

    # ---------- мета-обучение скорости (A5) ----------

    def note_support(self, s):
        self.support_hist.append(max(0.0, min(1.0, float(s))))

    def adapt_rate(self):
        """Мало опоры на граф в последних ответах — учим быстрее, и наоборот."""
        if len(self.support_hist) < 5:
            return 1.0
        avg = sum(self.support_hist) / len(self.support_hist)
        return 1.5 - avg          # avg 0 → ×1.5, avg 1 → ×0.5

    # ---------- дистилляция LM в граф (B2, B3, B5, B8) ----------

    def _ensure_word(self, w):
        """Id нейрона слова; нейрон создаётся, если его ещё нет."""
        nid = self.words.get(w)
        if nid is not None and nid in self.neurons:
            return nid
        r, c = self.ring_cell(w)
        n = self.add_free(r, c, w, G_IN)
        return n.id if n is not None else None

    def _bigram_dists(self, prevs):
        """p(b | a) по bigram-счётчикам, нормированное, для каждого a из prevs."""
        raw = {p: {} for p in prevs}
        for (a, b), c in self.bigram.items():
            d = raw.get(a)
            if d is not None:
                d[b] = d.get(b, 0) + c
        out = {}
        for p, d in raw.items():
            tot = sum(d.values())
            out[p] = {b: c / tot for b, c in d.items()} if tot else {}
        return out

    def distill_from_lm(self, answer_words, logprobs, weight=0.5):
        """Дистилляция распределения LM в граф.

        Учитель p_t(b): насколько LM держала слово b в топ-5 при генерации.
        Ученик p_s(b|a): нормированные bigram-счётчики графа после слова a.
        Связь (a→b) сдвигается на lr·(p_t − p_s). Слова, которые LM не выбрала,
        а граф считал вероятными, ослабляются: это уже не просто подкрепление.
        Нейроны для новых слов создаются, стоп-слова пропускаются,
        одна пара дистиллируется не больше DISTILL_BUDGET раз.
        """
        if not logprobs or len(answer_words) < 2:
            return
        p_teacher = {}
        for item in logprobs:
            for tok, lp in (item.get("top") or {}).items():
                prob = math.exp(lp) if lp < 0 else 1.0
                for w in _norm_all(tok):
                    if prob > p_teacher.get(w, 0.0):
                        p_teacher[w] = prob

        def usable(w):
            return len(w) >= MIN_WORD_LEN and w not in STOPWORDS

        pairs = [(a, b) for a, b in zip(answer_words, answer_words[1:])
                 if usable(a) and usable(b)
                 and self.distill_count.get((a, b), 0) < DISTILL_BUDGET]
        if not pairs:
            return
        lr = HEBB_RATE * 2 * weight
        prevs = {a for a, _ in pairs}
        p_student = self._bigram_dists(prevs)

        for a, b in pairs:
            p_t = p_teacher.get(b, 0.0)
            p_s = p_student.get(a, {}).get(b, 0.0)
            delta = lr * (p_t - p_s)
            if abs(delta) < 1e-4:
                continue
            na, nb = self._ensure_word(a), self._ensure_word(b)
            if na is None or nb is None:
                continue
            # бюджет считает каждое применение, даже если связь не изменилась
            self.distill_count[(a, b)] = self.distill_count.get((a, b), 0) + 1
            k = key(na, nb)
            if k not in self.links:
                if delta <= 0:
                    continue
                # прямая слабая связь: connect_words может поставить мост вместо неё
                self.connect(self.neurons[na], self.neurons[nb], 0.45)
            new = self.links.get(k, 0.0) + delta
            if new < PRUNE:
                self.links.pop(k, None)
            else:
                self.links[k] = min(2.0, new)
            if delta > 0:
                self.bigram[(a, b)] = self.bigram.get((a, b), 0) + 1
            self.bump_kconf([b], 0.05 * p_t)

        # Отрицательный сигнал: граф уверен в продолжении, а LM его не выбрала.
        for a in prevs:
            for b, p_s in p_student.get(a, {}).items():
                if p_s < 0.2 or b in p_teacher or not usable(b):
                    continue
                na, nb = self.words.get(a), self.words.get(b)
                if na is None or nb is None:
                    continue
                k = key(na, nb)
                if k in self.links:
                    v = self.links[k] * (1 - lr * p_s)
                    if v < PRUNE:
                        del self.links[k]
                    else:
                        self.links[k] = v

    def punish(self, answer, strength=1.0):
        """/bad: цепочка ответа ослабляется пропорционально силе сигнала (A4)."""
        s = max(0.0, min(1.0, strength))
        for a, b in zip(answer, answer[1:]):
            na, nb = self.words.get(a), self.words.get(b)
            if na is not None and nb is not None:
                k = key(na, nb)
                if k in self.links:
                    v = self.links[k] * (1 - 0.3 * s)
                    if v < PRUNE:
                        del self.links[k]
                    else:
                        self.links[k] = v
            if s >= 0.5 and (a, b) in self.bigram:
                self.bigram[(a, b)] = max(0, self.bigram[(a, b)] - 1)
        self.bump_kconf(answer, -0.1 * s)

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
            "version": BRAIN_FORMAT,
            "center": self.center.id if self.center else None,
            "nid": self.nid,
            "tick": self.tick,
            "temp": self.temp,
            "noisy": list(self.noisy),
            "neurons": [[n.id, n.r, n.c, n.word, n.layer, round(n.thr, 4)]
                        for n in self.neurons.values()],
            "links": [[a, b, round(w, 4)] for (a, b), w in self.links.items()],
            "bigram": [[a, b, c] for (a, b), c in self.bigram.items()],
            "kconf": {w: round(v, 4) for w, v in self.kconf.items()},
            "word_seen": self.word_seen,
            "distill_count": [[a, b, c] for (a, b), c in self.distill_count.items()],
        }

    def load_dict(self, d):
        self._clear()
        for row in d.get("neurons", []):
            nid, r, c, word, layer, thr = row
            n = Neuron(nid, r, c, word, layer)
            n.thr = thr
            self._register(n)
        self.nid = d.get("nid", max(self.neurons, default=-1) + 1)
        self.tick = d.get("tick", 0)
        self.temp = d.get("temp", TEMP_DEFAULT)
        self.center = self.neurons.get(d.get("center"))
        if self.center is None:
            self.center = self.add_free(GRID // 2, GRID // 2, None, G_OUT)
        for a, b, w in d.get("links", []):
            if a in self.neurons and b in self.neurons:
                self.links[key(a, b)] = w
        for a, b, c in d.get("bigram", []):
            self.bigram[(a, b)] = c
        self.kconf = {w: float(v) for w, v in d.get("kconf", {}).items()}
        self.word_seen = {w: int(v) for w, v in d.get("word_seen", {}).items()}
        self.distill_count = {(a, b): c for a, b, c in d.get("distill_count", [])}
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


def save_brain(brain, path=None):
    path = path or SAVE_PATH
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(brain.to_dict(), f, ensure_ascii=False)
    os.replace(tmp, path)


def load_brain(path=SAVE_PATH):
    """Читает сохранение. Бросает ValueError с понятным текстом, если файл не подходит."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or "neurons" not in d:
        raise ValueError("файл не похож на сохранение сети")
    version = d.get("version", 0)
    if version > BRAIN_FORMAT:
        raise ValueError(f"формат v{version} новее поддерживаемого v{BRAIN_FORMAT}")
    b = Brain()
    b.load_dict(d)
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
def draw_group_hulls(screen, brain, surface, fonts=None):
    """Показывает границы смысловых групп и подписывает каждую самым частым словом."""
    surface.fill((0, 0, 0, 0))
    by_group = {}
    top_word = {}
    for w, g in brain.wgroup.items():
        nid = brain.words.get(w)
        if nid is None or nid not in brain.neurons:
            continue
        n = brain.neurons[nid]
        by_group.setdefault(g, []).append((n.r, n.c))
        if brain.word_seen.get(w, 0) > brain.word_seen.get(top_word.get(g), 0):
            top_word[g] = w

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
    if fonts is None:
        return
    for g, cells in by_group.items():
        word = top_word.get(g)
        if len(cells) < 2 or word is None:
            continue
        pts = [cell_xy(r, c) for r, c in cells]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = min(p[1] for p in pts) - 14        # подпись над группой
        col = GROUP_COLORS[g % len(GROUP_COLORS)]
        txt = fonts["word"].render(word, True, col)
        surface.blit(txt, txt.get_rect(center=(int(cx), int(cy))))


LEGEND_MAX = 16          # строк в легенде групп (остальные — сводкой)


def group_rows(brain):
    """Все группы нейронов, включая одиночные: (id, число слов, слова по частоте)."""
    members = {}
    for w, g in brain.wgroup.items():
        members.setdefault(g, []).append(w)
    rows = []
    for g, ws in members.items():
        ws = sorted(ws, key=lambda w: -brain.word_seen.get(w, 0))
        rows.append((g, len(ws), ws))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows


def draw_group_legend(screen, brain, fonts, origin=(12, 44)):
    """Легенда ВСЕХ групп нейронов (клавиша F3): цвет, номер, размер, слова группы.
    В отличие от оболочек на сцене, показывает и одиночные группы."""
    rows = group_rows(brain)
    font = fonts["word"]
    x, y = origin
    line_h = font.get_linesize() + 2
    shown = rows[:LEGEND_MAX]
    head = f"Группы нейронов: {len(rows)}   (F3 — скрыть)"
    texts = [(g, font.render(f"группа {g} ({n}): " + " ".join(ws[:5]), True,
                             GROUP_COLORS[g % len(GROUP_COLORS)]))
             for g, n, ws in shown]
    width = max([font.size(head)[0]] + [t.get_width() for _, t in texts]) + 34
    extra = line_h if len(rows) > LEGEND_MAX else 0
    height = 10 + line_h * (len(shown) + 1) + extra
    rounded(screen, pygame.Rect(x - 6, y - 6, width, height), (13, 14, 17), (88, 94, 110), 8)
    screen.blit(font.render(head, True, (226, 230, 236)), (x, y))
    for i, (g, txt) in enumerate(texts):
        cy = y + line_h * (i + 1) + line_h // 2
        pygame.draw.circle(screen, GROUP_COLORS[g % len(GROUP_COLORS)], (x + 5, cy), 5)
        screen.blit(txt, (x + 16, cy - line_h // 2))
    if extra:
        more = font.render(f"… и ещё {len(rows) - LEGEND_MAX} групп", True, (150, 158, 172))
        screen.blit(more, (x + 16, y + line_h * (len(shown) + 1)))


TRAIL_LEN = 10           # сколько последних активных нейронов показывает след мысли
LABEL_HOT_MAX = 45       # сколько подписей слов показывать одновременно
TRAIL_WINDOW = 40        # не старше стольких тиков


def draw_halo(surf, pos, base, hot, R):
    """Мягкое гало из колец: ярче у центра, плавно гаснет к краю."""
    steps = 6
    px, py = int(pos[0]), int(pos[1])
    for i in range(steps, 0, -1):
        f = i / steps                              # 1 — внешнее кольцо, 1/steps — внутреннее
        rad = int(R * (1.0 + 4.0 * f))
        k = (0.04 + 0.45 * hot) * (1.0 - f) + 0.06 * hot * f
        pygame.draw.circle(surf, tuple(min(255, int(v * k)) for v in base), (px, py), rad)


def draw_trail(surf, brain, tick):
    """След мысли: линия через последние активные нейроны, тускнеющая к старым."""
    recent = [n for n in brain.neurons.values() if tick - n.fired_at <= TRAIL_WINDOW]
    recent.sort(key=lambda n: n.fired_at)
    recent = recent[-TRAIL_LEN:]
    if len(recent) < 2:
        return
    for i in range(1, len(recent)):
        a, b = recent[i - 1], recent[i]
        age = 1.0 - (i - 1) / max(1, len(recent) - 1)     # 1 — новый участок
        alpha = int(40 + 150 * age)
        col = (C_FOCUS[0], C_FOCUS[1], C_FOCUS[2], alpha)
        pygame.draw.line(surf, col, cell_xy(a.r, a.c), cell_xy(b.r, b.c), 2 if age > 0.6 else 1)


COLD_EVERY = 5      # статичный слой связей обновляется раз в N тактов (~0.5 с)
_COLD = {"surf": None, "tick": -999, "cam": None, "n": -1, "brain": None}


def _link_hot(tick, na, nb):
    return max(0.0, 1 - (tick - max(na.fired_at, nb.fired_at)) / HOT_LINK)


def _link_style(w, hot, focused):
    """Цвет и толщина связи: фокус, горячая, холодная."""
    if focused:
        return (C_FOCUS[0], C_FOCUS[1], C_FOCUS[2], 230), 2
    alpha = int(min(255, 28 + 80 * min(w, 2.0) / 2 + 140 * hot))
    if hot < 0.3:
        return (118, 130, 150, alpha), 1 + (w > 1.2)
    # горячие связи: спокойный голубой, непрозрачность не выше 170 (v16)
    return (130, 170, 215, min(alpha, 170)), 1 + (w > 1.2) + (hot > 0.5)


def draw_scene(screen, brain, layers, fonts, cache, stars, now, focus=None, info_extra=""):
    """Сеть: связи, свечение, частицы, подписи. focus — нейрон под курсором.

    v16: нагрузка снижена. Гало рисуется только у горящих и выбранных нейронов,
    кривые Безье — только у активных связей, холодные связи — прямыми отрезками,
    всё за пределами окна пропускается. Активные элементы выглядят как раньше."""
    links_layer, glow = layers
    tick = brain.tick
    pos = {nid: cell_xy(n.r, n.c) for nid, n in brain.neurons.items()}   # позиции один раз на кадр
    nbrs = set()
    if focus is not None:
        for a, b in brain.links:
            if a == focus:
                nbrs.add(b)
            elif b == focus:
                nbrs.add(a)
    view = pygame.Rect(-40, -40, NET_W + 80, H + 80)

    for x, y, ph in stars:
        b = int(34 + 24 * math.sin(now * 1.1 + ph))
        screen.set_at((int(x), int(y)), (b, b, b + 4))

    links_layer.fill((0, 0, 0, 0))
    cam = (CAM["cell"], CAM["ox"], CAM["oy"])
    cold = _COLD
    if (cold["surf"] is None or cold["brain"] is not brain or tick - cold["tick"] >= COLD_EVERY
            or cold["n"] != len(brain.links) or cold["cam"] is None
            or any(abs(p - q) > 0.5 for p, q in zip(cold["cam"], cam))):
        # статичный слой: все связи прямыми линиями; обновляется раз в COLD_EVERY тактов
        if cold["surf"] is None:
            cold["surf"] = pygame.Surface(links_layer.get_size(), pygame.SRCALPHA)
        cold["surf"].fill((0, 0, 0, 0))
        for (a, b), w in brain.links.items():
            p1, p2 = pos.get(a), pos.get(b)
            if p1 is None or p2 is None or focus in (a, b):
                continue
            if not (view.collidepoint(p1) or view.collidepoint(p2)):
                continue
            hot = _link_hot(tick, brain.neurons[a], brain.neurons[b])
            col, width = _link_style(w, hot, False)
            pygame.draw.line(cold["surf"], col, p1, p2, width)
        cold.update(brain=brain, tick=tick, cam=cam, n=len(brain.links))
    links_layer.blit(cold["surf"], (0, 0))
    # живой слой: только горящие и выбранные связи, с кривой Безье
    for (a, b), w in brain.links.items():
        p1, p2 = pos.get(a), pos.get(b)
        if p1 is None or p2 is None:
            continue
        hot = _link_hot(tick, brain.neurons[a], brain.neurons[b])
        focused = focus is not None and focus in (a, b)
        if not focused and hot < HOT_CURVE:
            continue
        if not (view.collidepoint(p1) or view.collidepoint(p2)):
            continue
        col, width = _link_style(w, hot, focused)
        c = ctrl_point(p1, p2, a, b)
        pts = [bez(p1, c, p2, i / 10) for i in range(11)]
        pygame.draw.lines(links_layer, col, False, pts, width)
    draw_trail(links_layer, brain, tick)
    screen.blit(links_layer, (0, 0))

    glow.fill((0, 0, 0))
    for nid, n in brain.neurons.items():
        x, y = pos[nid]
        if not view.collidepoint(x, y):
            continue
        hot = max(0.0, 1 - (tick - n.fired_at) / HOT_NODE)
        if n.id == focus or n.id in nbrs:
            hot = max(hot, 0.6)                  # соседи выбранного нейрона светятся
        if hot < HALO_MIN:
            continue
        base = node_base(brain, n)
        R = (6 if n.word else 4) + 3 * hot
        draw_halo(glow, (x, y), base, hot, R)

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
    # подписи только у самых активных слов: иначе сцена превращается в кашу
    hot_words = sorted((n for n in brain.neurons.values() if n.word),
                       key=lambda n: tick - n.fired_at)[:LABEL_HOT_MAX]
    hot_ids = {n.id for n in hot_words}
    for n in brain.neurons.values():
        x, y = cell_xy(n.r, n.c)
        hot = max(0.0, 1 - (tick - n.fired_at) / HOT_NODE)
        base = node_base(brain, n)
        core = base if hot <= 0 else tuple(min(255, int(v + (255 - v) * hot)) for v in base)
        R = (5 if n.word else 3) + 2 * hot
        pygame.draw.circle(screen, core, (int(x), int(y)), int(R))
        if n.word:                                   # слово отличается от скрытого нейрона кольцом
            pygame.draw.circle(screen, base, (int(x), int(y)), int(R) + 2, 1)
        is_focus = n.id == focus
        if is_focus:
            pygame.draw.circle(screen, C_FOCUS, (int(x), int(y)), int(R) + 4, 2)
        if n.word and (is_focus or n.id in hot_ids and hot > 0.15 or n.id in nbrs
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
            pill = rect.inflate(8, 4)
            rounded(screen, pill, (13, 14, 17), None, 5)
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


HINT = "колесо — масштаб · тянуть — сдвиг · клик — выбрать · ПКМ — меню"


def draw_hint(screen, fonts):
    """Подсказка управления в левом нижнем углу сетевой области (v16)."""
    surf = fonts["ui"].render(HINT, True, DIM)
    rect = pygame.Rect(NET_W - surf.get_width() - 32, H - 36, surf.get_width() + 20, 26)
    rounded(screen, rect, PANEL, BORDER, 8)
    screen.blit(surf, (rect.x + 10, rect.y + 5))


def draw_status(screen, fonts, text):
    """Строка «что делает сеть» под информационной панелью (v17)."""
    surf = fonts["ui"].render(text, True, ACCENT_HI)
    rect = pygame.Rect(NET_W - surf.get_width() - 32, 44, surf.get_width() + 20, 26)
    rounded(screen, rect, PANEL, BORDER, 8)
    screen.blit(surf, (rect.x + 10, rect.y + 5))


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
        ("ЛЕВОЕ", "связные слова", mind.fieldL, mind.last_side == "L"),
        ("ПРАВОЕ", "ассоциации", mind.fieldR, mind.last_side == "R"),
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

    screen.blit(fonts["word"].render("согласованность полушарий", True, DIM),
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
def wrap_lines(entries, fonts, width):
    """Строки панели. Ответы сети и ввод пользователя размечаются markdown;
    системные сообщения и мысли выводятся как текст."""
    lines = []
    for item in entries:
        who, msg = item[0], item[1]
        score = item[2] if len(item) > 2 else None
        if who != "thought":
            col = {"you": C_YOU, "net": C_NET, "sys": C_SYS}[who]
            if who == "net" and score is not None:
                label = answer_label(score)
            else:
                label = {"you": "Вы", "net": "Сеть", "sys": "•"}[who]
            lines.append(RLine([(label, col, "title")], head=True))
            body_col = DIM if who == "sys" else TEXT
            if who in ("net", "you"):
                lines += markdown_lines(msg, fonts, width, body_col, full=(who == "net"))
            else:
                lines += plain_lines(msg, fonts, width, body_col)
            lines.append(RLine([("", DIM, "chat")]))
        else:
            lines += plain_lines(msg, fonts, width, C_THOUGHT)
    return lines

VIEW = {}                       # имя панели -> размеры, строки и шрифты последней отрисовки
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
    s, ln = v["lines"][idx]
    x = pos[0] - (rect.x + pad)
    return (idx, index_at(ln.segs, v["fonts"], x) if s else 0)


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
    lines = wrap_lines(entries, fonts, width)
    max_rows = max(1, (rect.h - 2 * pad) // 20)
    maxs = max(0, len(lines) - max_rows)
    scroll = max(0, min(scroll, maxs))
    end = len(lines) - scroll
    start = max(0, end - max_rows)

    VIEW[name] = {
        "rect": rect, "pad": pad, "start": start, "rows": end - start,
        "lines": [(ln.plain, ln) for ln in lines],
        "fonts": fonts,
    }

    screen.set_clip(rect.inflate(-4, -4))
    visible = lines[start:end]
    for i, ln in enumerate(visible):          # фон блоков кода
        if ln.bg:
            y0 = rect.y + pad + i * 20
            pygame.draw.rect(screen, ln.bg, (rect.x + pad - 4, y0, rect.w - 2 * pad - 2, 20),
                             border_radius=3)
    sel = SEL["panel"] == name and SEL["a"] is not None and SEL["b"] is not None \
        and SEL["a"] != SEL["b"]
    if sel:
        lo, hi = sorted([SEL["a"], SEL["b"]])
        for li in range(max(start, lo[0]), min(end, hi[0] + 1)):
            ln = lines[li]
            sc = lo[1] if li == lo[0] else 0
            ec = hi[1] if li == hi[0] else len(ln.plain)
            x1 = rect.x + pad + x_at(ln.segs, fonts, sc)
            x2 = rect.x + pad + x_at(ln.segs, fonts, ec)
            if li < hi[0]:
                x2 += 6
            y0 = rect.y + pad + (li - start) * 20
            pygame.draw.rect(screen, SEL_BG, (x1, y0, max(4, x2 - x1), 20))
    y = rect.y + pad
    for ln in visible:
        x = rect.x + pad
        for text, col, font_name in ln.segs:
            if text:
                surf = fonts[font_name].render(text, True, col)
                screen.blit(surf, (x, y))
                x += surf.get_width()
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
def draw_chat(screen, thoughts, chat, field, fonts, now, progress, sliders, scroll,
              lm_on, lm_available=True):
    x0 = NET_W
    pygame.draw.rect(screen, PANEL_BG, (x0, 0, CHAT_W, H))
    pygame.draw.line(screen, BORDER, (x0, 0), (x0, H), 1)

    header(screen, fonts, "МЫСЛИ", x0 + 18, 12, C_THOUGHT)
    screen.blit(fonts["word"].render(HELP["thoughts"], True, DIM),
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
    _draw_field(screen, fonts["chat"], INPUT_RECT, field, now)


def speech(words, entry, now, gap, live=False):
    return {"words": list(words), "start": now, "shown": 0, "prev": None,
            "entry": entry, "gap": gap, "live": live}


def _set_score(entry, score):
    """ИЗМ: записывает оценку в 2-элементную запись, расширяя её до 3-элементной."""
    if len(entry) >= 3:
        entry[2] = score
    else:
        entry.append(score)


# ---------- v20: управление сетью мышью ----------

VIEW_USER = {"on": False, "zoom": 1.0, "wcx": 0.0, "wcy": 0.0, "base": CELL, "auto": (0.0, 0.0)}
NET_PRESS = {"pos": None, "moved": False, "nid": None}
NET_SEL = {"nid": None}
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.25, 10.0, 1.15


def _ensure_user_view():
    """Перейти от автоподгонки к пользовательскому виду, сохранив текущий центр."""
    if not VIEW_USER["on"]:
        VIEW_USER.update(on=True, zoom=1.0, wcx=VIEW_USER["auto"][0], wcy=VIEW_USER["auto"][1])


def reset_view():
    VIEW_USER["on"] = False


def zoom_at(pos, steps):
    """Масштаб колесом, относительно курсора: мировая точка под курсором остаётся на месте."""
    if not steps:
        return
    _ensure_user_view()
    mx, my = pos
    base = VIEW_USER["base"]
    z0 = VIEW_USER["zoom"]
    z1 = max(ZOOM_MIN, min(ZOOM_MAX, z0 * ZOOM_STEP ** steps))
    if z1 == z0:
        return
    wx = VIEW_USER["wcx"] + (mx - NET_W / 2) / (base * z0)
    wy = VIEW_USER["wcy"] + (my - H / 2) / (base * z0)
    VIEW_USER["zoom"] = z1
    VIEW_USER["wcx"] = wx - (mx - NET_W / 2) / (base * z1)
    VIEW_USER["wcy"] = wy - (my - H / 2) / (base * z1)


def pan_by(dx, dy):
    """Сдвиг сети мышью: dx, dy — смещение курсора в пикселях."""
    _ensure_user_view()
    cell = VIEW_USER["base"] * VIEW_USER["zoom"]
    VIEW_USER["wcx"] -= dx / cell
    VIEW_USER["wcy"] -= dy / cell


def select_neuron(brain, nid):
    """Выбор нейрона кликом: описание в чате, соседи подсвечиваются (см. focus)."""
    NET_SEL["nid"] = nid
    if nid is None or nid not in brain.neurons:
        NET_SEL["nid"] = None
        return None
    word = brain.neurons[nid].word
    deg = sum(1 for a, b in brain.links if a == nid or b == nid)
    label = f"«{word}»" if word else f"нейрон {nid}"
    return f"Выбран {label}: связей {deg}. ПКМ — действия, двойной клик по пустому — вид сброшен."


# ---------- v18: поле ввода, выделение и буфер ----------

FIELD_PAD = 12
DOUBLE_SEC = 0.4
FIELD_VIEW = {"rect": None, "font": None, "start": 0, "end": 0, "shown": ""}
FIELD_DRAG = {"on": False}
CLICK = {"t": 0.0, "pos": None}


def _is_double(pos):
    """Двойной щелчок: второй в пределах DOUBLE_SEC и рядом с первым."""
    now = time.time()
    last = CLICK["pos"]
    dbl = (last is not None and now - CLICK["t"] <= DOUBLE_SEC
           and abs(pos[0] - last[0]) <= 6 and abs(pos[1] - last[1]) <= 6)
    CLICK["t"] = now
    CLICK["pos"] = None if dbl else pos
    return dbl


def _field_width(s):
    font = FIELD_VIEW["font"]
    return font.size(s)[0] if font else 0


def _field_index(pos):
    rect = FIELD_VIEW["rect"]
    x = pos[0] - (rect.x + FIELD_PAD)
    return te.index_at(_field_width, FIELD_VIEW["shown"], FIELD_VIEW["start"],
                       FIELD_VIEW["end"], x)


def _click_field(field, pos, extend):
    if FIELD_VIEW["rect"] is None:
        return
    SEL.update(panel=None, a=None, b=None, drag=False)
    i = _field_index(pos)
    if not extend and _is_double(pos):
        field.select_word_at(i)
        FIELD_DRAG["on"] = False
    else:
        field.move(i, extend=extend)
        FIELD_DRAG["on"] = True


def _drag_field(field, pos):
    if FIELD_VIEW["rect"] is not None:
        field.move(_field_index(pos), extend=True)


def _panel_text(name):
    v = VIEW.get(name)
    return "\n".join(s for s, _ in v["lines"]) if v else ""


def _word_sel(name, p):
    """Выделение слова в панели по двойному щелчку."""
    line_idx, ch = p
    s = VIEW[name]["lines"][line_idx][0]
    lo, hi = te.word_span(s, ch)
    return {"a": (line_idx, lo), "b": (line_idx, hi), "drag": False}


def _focus_field():
    """Фокус на поле: выделение в панелях сбрасывается."""
    SEL.update(panel=None, a=None, b=None, drag=False)


def handle_edit_key(e, field):
    """Правка поля и буфер: Ctrl/Cmd+A, C, X, V; стрелки, Shift-выделение, Home/End,
    Backspace, Delete, Shift+Enter. None — клавиша не относится к правке.

    Действие с полем сбрасывает выделение в панелях: активна одна область, и Ctrl+C
    копирует именно её (последнюю, с которой работал пользователь)."""
    mods = pygame.key.get_mods()
    ctrl = mods & (pygame.KMOD_CTRL | pygame.KMOD_META)
    shift = bool(mods & pygame.KMOD_SHIFT)
    if ctrl:
        if e.key == pygame.K_a:
            pname = panel_at(pygame.mouse.get_pos())
            if pname:
                lines = VIEW[pname]["lines"]
                SEL.update(panel=pname, a=(0, 0), b=(len(lines) - 1, len(lines[-1][0])), drag=False)
            else:
                _focus_field()
                field.select_all()
            return ""
        if e.key == pygame.K_c:
            body = selected_text() if SEL["panel"] else ""
            if not body:
                body = field.selected()
            if not body:
                return ""
            copy_to_clipboard(body)
            return f"Скопировано: {len(body)} симв."
        if e.key == pygame.K_x:
            _focus_field()
            body = field.cut()
            if not body:
                return ""
            copy_to_clipboard(body)
            return f"Вырезано: {len(body)} симв."
        if e.key == pygame.K_v:
            _focus_field()
            field.insert(get_clipboard_text().replace("\r\n", "\n").replace("\r", "\n"))
            return ""
        return None
    if e.key == pygame.K_LEFT:
        _focus_field()
        field.left(extend=shift)
    elif e.key == pygame.K_RIGHT:
        _focus_field()
        field.right(extend=shift)
    elif e.key == pygame.K_HOME:
        _focus_field()
        field.home(extend=shift)
    elif e.key == pygame.K_END:
        _focus_field()
        field.end(extend=shift)
    elif e.key == pygame.K_BACKSPACE:
        _focus_field()
        field.backspace()
    elif e.key == pygame.K_DELETE:
        _focus_field()
        field.delete()
    elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and shift:
        _focus_field()
        field.insert("\n")
    else:
        return None
    return ""


def _draw_field(screen, font, rect, field, now):
    """Поле ввода: окно с прокруткой, выделение, курсор."""
    FIELD_VIEW.update(rect=rect, font=font)
    shown = field.text.replace("\n", "¶")
    avail = rect.w - 2 * FIELD_PAD
    base_x, y = rect.x + FIELD_PAD, rect.y + 10
    if not field.text:
        FIELD_VIEW.update(start=0, end=0, shown="")
        screen.blit(font.render("напишите сообщение…", True, DIM), (base_x, y))
    else:
        start, end = te.window(_field_width, shown, field.caret, avail, FIELD_VIEW["start"])
        FIELD_VIEW.update(start=start, end=end, shown=shown)
        r = field.sel_range()
        if r:
            lo, hi = max(r[0], start), min(r[1], end)
            if lo < hi:
                x1 = base_x + _field_width(shown[start:lo])
                x2 = base_x + _field_width(shown[start:hi])
                pygame.draw.rect(screen, SEL_BG, (x1, rect.y + 8, x2 - x1, rect.h - 16))
        screen.blit(font.render(shown[start:end], True, TEXT), (base_x, y))
    if int(now * 2) % 2 == 0:            # мигающий курсор
        cx = base_x + _field_width(shown[FIELD_VIEW["start"]:field.caret]) if field.text else base_x
        pygame.draw.line(screen, TEXT, (cx, rect.y + 8), (cx, rect.y + rect.h - 8), 2)


# ---------- главный цикл ----------

def main():
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
        "chatb": pygame.font.SysFont(fam, 16, bold=True),
        "mono": pygame.font.SysFont("consolas,menlo,dejavusansmono,couriernew", 15),
        "title": pygame.font.SysFont(fam, 13, bold=True),
    }

    bg = make_background()
    stars = [(random.uniform(0, NET_W), random.uniform(0, H), random.uniform(0, 6.28))
             for _ in range(160)]
    layers = (pygame.Surface((NET_W, H), pygame.SRCALPHA), pygame.Surface((NET_W, H)))
    # NEW: слой для оболочек смысловых групп
    group_layer = pygame.Surface((NET_W, H), pygame.SRCALPHA)
    hull_tick = 0                 # счётчик кадров для оболочек групп
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
    nets_mgr = NetsManager.open()          # цепочка сетей (папка nets/)
    llm_tasks = LLMTaskRunner()            # обучение через LLM и адаптация, по одному запросу
    nets_state = {"adapt_queued": False}
    show_legend = False                    # F3: легенда всех групп нейронов

    def teach_done_factory(word):
        def done(res):
            if not res.get("ok"):
                return [("sys", f"Обучение «{word}» не удалось: {res.get('reason', 'нет ответа')}.")]
            plan = res["plan"]
            added = apply_plan(brain, plan)
            brain.regroup()
            verdict = "принята" if res["committed"] else "отклонена"
            return [("sys", f"«{word}»: связей в граф добавлено {added}, слоёв {len(plan['layers'])}. "
                            f"Дистилляция в сети {verdict}.")]
        return done

    def adapt_done(rep):
        nets_state["adapt_queued"] = False
        if not rep:
            return []
        verdict = "принята" if rep["commit"] else "отклонена"
        return [("sys", f"Адаптация сетей: качество {rep['before']} → {rep['after']} ({verdict}).")]
    answerer = Answerer(brain, mind, nets_mgr)
    sliders = [
        Slider("частота мыслей", 50, lambda v: f"каждые {think_sec(v):.2f} с"),
        Slider("длина ответа", 8, lambda v: f"{int(round(v))} слов"),
        Slider("зазор между словами", 30, lambda v: f"{gap_sec(v):.2f} с"),
    ]
    lm, lm_status = start_lm()
    lm_on = True                          # NEW: включена ли LLM в текущем режиме
    episodes = EpisodeMemory()
    policy = Bandit()                     # RL: выбор стратегии по оценкам пользователя
    last_arm = None                       # стратегия последнего ответа (для награды)
    last_st = None                        # состояние последнего ответа (для дистилляции)
    last_lp = None                        # логиты LM последнего ответа (для дистилляции)
    fb = FeedbackButtons()                # кнопки под чатом
    neuron_menu = NeuronMenu()            # меню нейрона по правой кнопке
    pending = None
    last_user_msg = ""
    last_support = None
    coh_hist = deque(maxlen=COH_HIST)
    coh_acc = 0.0
    cortex = Cortex()
    cortex_acc = 0.0
    last_lm = None
    dragging = None
    thoughts = []
    scroll = {"think": 0, "chat": 0}

    chat = [["sys", status + " Напишите /help — список команд."]]
    field = te.Field()
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
                nets_mgr.save()
            except OSError as e:
                sys_msg(f"Автосохранение не удалось: {e}")

        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN and (edit_msg := handle_edit_key(e, field)) is not None:
                if edit_msg:
                    sys_msg(edit_msg)
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    msg = field.text.strip()
                    field.set_text("")
                    if msg == "/reset":
                        brain = Brain()
                        mind = Mind(brain)
                        answerer = Answerer(brain, mind, nets_mgr)
                        cache.clear()
                        resp = None
                        last_answer = None
                        sys_msg("Сеть сброшена (файл сохранения не тронут).")
                    elif msg == "/save":
                        save_brain(brain)
                        nets_mgr.save()
                        sys_msg(f"Сохранено в {SAVE_PATH}")
                    elif msg == "/load":
                        if os.path.exists(SAVE_PATH):
                            try:
                                new_brain = load_brain()
                            except (OSError, ValueError, KeyError, TypeError) as e:
                                # текущая сеть не трогается
                                sys_msg(f"Не удалось загрузить сохранение: {e}")
                            else:
                                brain = new_brain
                                mind = Mind(brain)
                                answerer = Answerer(brain, mind, nets_mgr)
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
                    elif msg == "/cortex":
                        for line in cortex.report(brain):
                            sys_msg(line)
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
                        sys_msg(f"нейроны: {len(brain.neurons)} (без лимита)")
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
                    elif msg == "/facts":
                        top = brain.top_facts(20)
                        if not top:
                            sys_msg("Токенов знаний пока нет: появляются после /teach, /good и дистилляции.")
                        else:
                            sys_msg("Токены знаний (уверенность): "
                                    + ", ".join(f"{w} {v:.2f}" for w, v in top))
                    elif msg.split()[:1] == ["/rename"]:
                        parts = msg.split()
                        if len(parts) == 3 and brain.rename_word(parts[1], parts[2]):
                            sys_msg(f"Переименовано: {parts[1]} → {parts[2]}.")
                        else:
                            sys_msg("Формат: /rename старое новое (новое слово должно быть свободно).")
                    elif msg.split()[:1] == ["/neurons"]:
                        sys_msg(f"Нейронов: {len(brain.neurons)}. Лимита нет: сеть растёт при обучении.")
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
                            s_good = _feedback_weight(last_support, good=True)
                            brain._reinforce_answer(last_answer, weight=s_good)
                            brain.bump_kconf(last_answer, 0.15 * s_good)
                            policy.reward(last_arm, 1.0)
                            if last_lp and last_answer:
                                brain.distill_from_lm(last_answer, last_lp, weight=s_good)
                            if last_st is not None:
                                answerer.distill_rationale(last_st, confidence=s_good)
                            if last_user_msg:
                                nets_mgr.feedback(last_user_msg, " ".join(last_answer), weight=s_good, auto=False)
                            last_arm, last_st, last_lp = None, None, None
                            if last_user_msg:
                                brain.train_words(tokenize(last_user_msg, limit=None) + last_answer,
                                                  weight=s_good)
                                brain.associate(tokenize(last_user_msg, limit=None), last_answer,
                                                weight=s_good)
                            if last_lm is not None:
                                LMBackend.log_feedback(last_lm[0], last_lm[1], +1)
                                episodes.add(last_lm[0], last_lm[1])
                            elif last_user_msg:
                                episodes.add(last_user_msg, " ".join(last_answer))
                            sys_msg("Ответ закреплён и сохранён в эпизодах.")
                        else:
                            brain.punish(last_answer, strength=_feedback_weight(last_support, good=False))
                            policy.reward(last_arm, 0.0)
                            last_arm, last_st, last_lp = None, None, None
                            if last_lm is not None:
                                LMBackend.log_feedback(last_lm[0], last_lm[1], -1)
                            if last_user_msg:
                                episodes.drop(last_user_msg)
                            sys_msg("Ответ ослаблен, эпизод удалён. Как ответить правильно? Напишите: /teach ответ.")
                    elif msg.startswith("/teach"):
                        body = msg[len("/teach"):].strip()
                        if not body or not last_q:
                            sys_msg("Формат: /teach правильный ответ (после вопроса).")
                        else:
                            ans_words = tokenize(body, limit=None)
                            brain.train_words(last_q + ans_words)
                            brain.associate(last_q, ans_words)
                            brain._reinforce_answer(ans_words)
                            brain.bump_kconf(ans_words, 0.1)
                            brain.regroup()
                            policy.reward(last_arm, 0.0)   # пользователь сам дал ответ — прошлая стратегия не сработала
                            last_arm, last_st, last_lp = None, None, None
                            if last_user_msg:
                                episodes.add(last_user_msg, body)
                                nets_mgr.feedback(last_user_msg, body, weight=1.0, auto=False)
                            sys_msg(f"Запомнила: {len(ans_words)} слов связано с вопросом.")
                    elif msg == "/nets":
                        for line in nets_mgr.status():
                            sys_msg(line)
                    elif msg == "/chain":
                        for line in (nets_mgr.last_text or "Цепочки пока не было.").splitlines()[:20]:
                            sys_msg(line)
                    elif msg == "/policy":
                        for line in policy.summary(arms_for(REVISE_ROUNDS > 0)):
                            sys_msg(line)
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
                        last_arm, last_st = None, None
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
                            st = answerer.begin(msg, target, policy if (lm is not None and lm_on) else None)
                            if st["thought"] is not None:
                                thoughts.append(["thought", describe_thought(st["thought"], brain)])
                                thoughts = thoughts[-300:]
                            answerer.learn(msg, tokenize(ep["a"], limit=None))
                            last_answer = tokenize(ep["a"], limit=None) or None
                            resp = speech(ep["a"].split(), entry, now, gap)
                        else:
                            st = answerer.begin(msg, target, policy if (lm is not None and lm_on) else None)
                            if st["thought"] is not None:
                                thoughts.append(["thought", describe_thought(st["thought"], brain)])
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
                                    brain.note_support(score)
                                    # Автообучения нет: граф меняется только по награде (/good, /teach).
                                    last_arm, last_st = st.get("arm"), st
                                else:
                                    entry[1] = answerer.unknown_reply(st)
                    chat = chat[-300:]
                elif e.key == pygame.K_F3:
                    show_legend = not show_legend
                elif e.key in (pygame.K_PAGEUP, pygame.K_PAGEDOWN):
                    k = "think" if pygame.key.get_mods() & pygame.KMOD_SHIFT else "chat"
                    scroll[k] = max(0, scroll[k] + (5 if e.key == pygame.K_PAGEUP else -5))
                elif e.key == pygame.K_END:
                    scroll["chat"] = 0
                    scroll["think"] = 0
            elif e.type == pygame.DROPFILE:
                path = e.file
                if path.lower().endswith(".txt"):
                    try:
                        n_lines, n_words = brain.train_file(path)
                        sys_msg(f"Файл «{os.path.basename(path)}»: строк {n_lines}, "
                                f"слов {n_words}. Сеть обучена на тексте.")
                    except Exception as ex:
                        sys_msg(f"Не удалось прочитать файл: {ex}")
                else:
                    sys_msg("Перетащите текстовый файл (.txt), чтобы обучить сеть.")
            elif e.type == pygame.DROPTEXT:
                field.insert(e.text)
            elif e.type == pygame.MOUSEWHEEL:
                pos = pygame.mouse.get_pos()
                if pos[0] < NET_W:
                    zoom_at(pos, e.y)
                elif THINK_RECT.collidepoint(pos):
                    scroll["think"] = max(0, scroll["think"] + e.y * 3)
                elif CHAT_RECT.collidepoint(pos):
                    scroll["chat"] = max(0, scroll["chat"] + e.y * 3)
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
                if panel_at(e.pos):
                    pname = panel_at(e.pos)
                    body = (selected_text() if SEL["panel"] == pname else "") or _panel_text(pname)
                    if body:
                        copy_to_clipboard(body)
                        sys_msg(f"Скопировано из панели: {len(body)} симв.")
                    continue
                nid_r = nearest_neuron(brain, e.pos[0], e.pos[1])
                if nid_r is not None:
                    neuron_menu.open(brain.neurons[nid_r], e.pos)
                else:
                    neuron_menu.close()
            elif (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1
                  and e.pos[0] < NET_W and not neuron_menu.active):
                nid_p = nearest_neuron(brain, e.pos[0], e.pos[1])
                NET_PRESS.update(pos=e.pos, moved=False, nid=nid_p)
                if nid_p is None and _is_double(e.pos):
                    reset_view()
                    sys_msg("Вид сброшен: автоматический масштаб и центр.")
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                # Меню нейрона и кнопки оценки имеют приоритет над остальными элементами.
                if neuron_menu.active:
                    act = neuron_menu.hit(e.pos)
                    nid_m, word_m = neuron_menu.nid, neuron_menu.word
                    if act in ("good", "bad") and nid_m in brain.neurons:
                        brain.feedback_neuron(nid_m, act == "good")
                        sys_msg(f"Нейрон «{word_m or nid_m}»: "
                                f"{'усилен' if act == 'good' else 'ослаблен'}.")
                    elif act == "activate" and nid_m in brain.neurons:
                        brain.forced.add(nid_m)          # нейрон сработает на ближайшем шаге
                        if word_m:
                            mind.fieldL[word_m] = mind.fieldL.get(word_m, 0.0) + 1.0
                        sys_msg(f"Нейрон «{word_m or nid_m}» активирован: "
                                f"левое полушарие думает о нём.")
                    elif act == "llm_teach" and word_m:
                        if lm is None:
                            sys_msg("LLM не подключена: обучение через LLM недоступно.")
                        elif not lm_on:
                            sys_msg("LLM выключена: включите её кнопкой и повторите.")
                        else:
                            llm_tasks.add(f"обучение «{word_m}»",
                                          lambda w=word_m: nets_mgr.teach_gen(w),
                                          teach_done_factory(word_m))
                            sys_msg(f"LLM объясняет «{word_m}»…")
                    elif act == "rename" and word_m:
                        text = f"/rename {word_m} "
                    elif act == "delete" and nid_m in brain.neurons \
                            and brain.neurons[nid_m] is not brain.center:
                        brain.remove(brain.neurons[nid_m])
                        sys_msg(f"Нейрон «{word_m or nid_m}» удалён.")
                    continue
                fbk = fb.hit(e.pos)
                if fbk and last_answer is not None:
                    if fbk == "teach":
                        text = "/teach "          # пользователь введёт правильную формулировку
                    else:
                        text = "/good" if fbk == "good" else "/bad"
                        # Отправляем команду тем же путём, что и Enter.
                        pygame.event.post(pygame.event.Event(
                            pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))
                    continue
                if INPUT_RECT.collidepoint(e.pos):
                    _click_field(field, e.pos, extend=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
                    continue
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
                            if p is not None and _is_double(e.pos):
                                SEL.update(_word_sel(pname, p))
                        else:
                            SEL.update(panel=None, a=None, b=None, drag=False)
            elif e.type == pygame.MOUSEMOTION and NET_PRESS["pos"] is not None:
                if not NET_PRESS["moved"]:
                    x0, y0 = NET_PRESS["pos"]
                    NET_PRESS["moved"] = math.hypot(e.pos[0] - x0, e.pos[1] - y0) > 4
                if NET_PRESS["moved"]:
                    pan_by(e.rel[0], e.rel[1])
            elif e.type == pygame.MOUSEMOTION and FIELD_DRAG["on"]:
                _drag_field(field, e.pos)
            elif e.type == pygame.MOUSEMOTION and dragging is not None:
                sliders[dragging].set_from_x(e.pos[0])
            elif e.type == pygame.MOUSEMOTION and SEL["drag"]:
                p = point_at(SEL["panel"], e.pos)
                if p is not None:
                    SEL["b"] = p
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and NET_PRESS["pos"] is not None:
                if not NET_PRESS["moved"]:
                    msg_sel = select_neuron(brain, NET_PRESS["nid"])
                    if msg_sel:
                        sys_msg(msg_sel)
                    elif NET_PRESS["nid"] is None:
                        NET_SEL["nid"] = None
                NET_PRESS.update(pos=None, moved=False, nid=None)
                dragging = None
                FIELD_DRAG["on"] = False
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                dragging = None
                SEL["drag"] = False
                FIELD_DRAG["on"] = False
            elif e.type == pygame.TEXTINPUT:
                field.insert(e.text)

        mind.think_every = think_sec(sliders[0].value)
        thought = mind.tick(dt)
        if thought:
            thoughts.append(["thought", describe_stream(thought)])
            thoughts = thoughts[-300:]

        # ИЗМ: обрабатываем pending только при включённой LLM
        if lm_on and pending is not None:
            partial_text = lm.partial(pending["req"])
            if partial_text:
                pending["entry"][1] = partial_text
            res = lm.poll(pending["req"])
            if res is not None:
                lp = lm.last_logprobs if lm is not None else []  # ← захватываем до нового submit
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
                        brain.note_support(score)
                        last_answer = tokenize(reply_text, limit=None) or None
                        if from_lm:
                            last_lm = (st["q"], reply_text)
                        entry_ref[1] = reply_text
                        _set_score(entry_ref, score)
                        resp = speech(reply_text.split(), entry_ref, now,
                                      gap_sec(sliders[2].value), live=True)
                        # Автообучения нет. Запоминаем стратегию и логиты: они понадобятся,
                        # если пользователь подтвердит ответ через /good (награда и дистилляция).
                        if from_lm:
                            last_arm, last_st, last_lp = st.get("arm"), st, lp
                            hint = answerer.low_confidence_hint(score)
                            if hint:
                                sys_msg(hint)
                    else:
                        entry_ref[1] = answerer.unknown_reply(st)

        # Фон сетей: обучение через LLM и адаптация. По одному запросу за раз; ждём,
        # пока LLM не занята ответом в чат. Без LLM адаптация идёт сразу (по словам).
        if nets_mgr.adapt_due and not nets_state["adapt_queued"]:
            if lm is None or not lm_on:
                nets_mgr.adapt()
            else:
                nets_state["adapt_queued"] = True
                llm_tasks.add("адаптация сетей", lambda: nets_mgr.adapt_gen(), adapt_done)
        if llm_tasks.active():
            for tag, msg_text in llm_tasks.tick(lm if lm_on else None, pending is not None):
                if tag == "explain":
                    chat.append(["net", msg_text])
                    scroll["chat"] = 0
                elif tag == "sys":
                    sys_msg(msg_text)

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

        cortex_acc += dt
        if cortex_acc >= CORTEX_EVERY and resp is None and pending is None:
            cortex_acc = 0.0
            for q in cortex.idle(brain, list(mind.stream)):
                chat.append(["net", q])
                scroll["chat"] = 0
        coh_acc += dt
        if coh_acc >= 0.1:
            coh_acc = 0.0
            coh_hist.append(max(0.0, min(1.0, mind.coherence())))
        mouse = pygame.mouse.get_pos()
        update_view(brain, dt)
        focus = nearest_neuron(brain, mouse[0], mouse[1]) or NET_SEL["nid"]
        screen.blit(bg, (0, 0))
        # NEW: оболочки групп — позади связей и узлов
        hull_tick += 1
        if hull_tick % HULL_EVERY == 1:
            draw_group_hulls(screen, brain, group_layer, fonts)
        else:
            screen.blit(group_layer, (0, 0))
        draw_scene(screen, brain, layers, fonts, cache, stars, now, focus=focus,
                   info_extra=f"   эпизодов {len(episodes.items)}")
        if show_legend:
            draw_group_legend(screen, brain, fonts)
        # ИЗМ: draw_overlay теперь принимает mind вместо side
        draw_overlay(screen, fonts, brain, mind, coh_hist, focus, mouse, now)
        draw_hint(screen, fonts)
        draw_status(screen, fonts, status_text(answering=resp is not None,
                                               waiting=pending is not None,
                                               learning=llm_tasks.active()))
        # ИЗМ: передаём lm_on для бейджа
        draw_chat(screen, thoughts, chat, field, fonts, now, progress, sliders, scroll,
                  lm_on, lm_available=(lm is not None))
        fb.draw(screen, fonts["word"], NET_W + 18, H - 80, CHAT_W - 36,
                enabled=last_answer is not None, mouse=pygame.mouse.get_pos())
        neuron_menu.draw(screen, fonts["word"], (W, H))
        pygame.display.flip()

    try:
        save_brain(brain)
    except OSError:
        pass
    try:
        nets_mgr.save()
    except OSError:
        pass
    try:
        write_session(chat, thoughts)
    except OSError:
        pass
    pygame.quit()


if __name__ == "__main__":
    main()