# -*- coding: utf-8 -*-
"""v16: быстрее отрисовка, перетаскивание файлов и текста, кнопка «активировать»
у нейрона, подсказка управления.

Меняет brain_chat.py и ui_feedback.py точечными заменами. Файлы в CRLF;
замены сохраняют окончания строк. Каждый якорь должен встречаться ровно один раз.

Запуск из корня проекта:  python patch_ui_v16.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def crlf(s):
    return s.replace("\r\n", "\n").replace("\n", "\r\n")


# ---------- новая голова draw_scene: нагрузка снижена, внешний вид активного сохранён ----------

KWTA_BLOCK = '''        cand = []
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
'''

NEW_SCENE_HEAD = '''COLD_EVERY = 5      # статичный слой связей обновляется раз в N тактов (~0.5 с)
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

'''

NEW_HINT_FUNC = '''HINT = "колесо — масштаб · ПКМ — действия · .txt — обучить"


def draw_hint(screen, fonts):
    """Подсказка управления в левом нижнем углу сетевой области (v16)."""
    surf = fonts["ui"].render(HINT, True, DIM)
    rect = pygame.Rect(NET_W - surf.get_width() - 32, H - 36, surf.get_width() + 20, 26)
    rounded(screen, rect, PANEL, BORDER, 8)
    screen.blit(surf, (rect.x + 10, rect.y + 5))


def nearest_neuron(brain, mx, my, radius=12.0):'''

DROP_HANDLERS = '''            elif e.type == pygame.DROPFILE:
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
                text += e.text
'''

ACTIVATE_BRANCH = '''                    elif act == "activate" and nid_m in brain.neurons:
                        brain.forced.add(nid_m)          # нейрон сработает на ближайшем шаге
                        if word_m:
                            mind.fieldL[word_m] = mind.fieldL.get(word_m, 0.0) + 1.0
                        sys_msg(f"Нейрон «{word_m or nid_m}» активирован: "
                                f"левое полушарие думает о нём.")
'''

HINT_CALL = '''        draw_hint(screen, fonts)
'''

# (файл, старое, новое) — старое и новое записываются с \n и переводятся в CRLF
BRAIN_EDITS = []


def build_edits(text):
    """Готовит замены по тексту brain_chat.py; возвращает список (старое, новое)."""
    edits = []
    start = text.index(crlf("def draw_scene("))
    end = text.index(crlf("    # NEW: подписи с защитой"))
    edits.append((text[start:end], crlf(NEW_SCENE_HEAD)))
    edits.append((crlf("def nearest_neuron(brain, mx, my, radius=12.0):"), crlf(NEW_HINT_FUNC)))
    edits.append((crlf("        core = tuple(min(255, int(v + (255 - v) * hot)) for v in base)\n"),
                  crlf("        core = base if hot <= 0 else tuple(min(255, int(v + (255 - v) * hot)) for v in base)\n")))
    edits.append((crlf("            elif e.type == pygame.MOUSEWHEEL:\n"),
                  crlf(DROP_HANDLERS + "            elif e.type == pygame.MOUSEWHEEL:\n")))
    edits.append((crlf('                    elif act == "llm_teach" and word_m:\n'),
                  crlf(ACTIVATE_BRANCH + '                    elif act == "llm_teach" and word_m:\n')))
    edits.append((crlf("        draw_overlay(screen, fonts, brain, mind, coh_hist, focus, mouse, now)\n"),
                  crlf("        draw_overlay(screen, fonts, brain, mind, coh_hist, focus, mouse, now)\n"
                       + HINT_CALL)))
    # HOT_CURVE и HALO_MIN — константы рядом с HOT_LINK
    edits.append((crlf("HOT_LINK = 20               # кадров подсветки связи (~2 с)\n"),
                  crlf("HOT_LINK = 8                # кадров подсветки связи (~0.8 с, v16: короче — меньше белой сетки)\n"
                       "HOT_CURVE = 0.05          # связь горячее этого порога рисуется кривой\n"
                       "HALO_MIN = 0.02           # гало рисуется только у нейронов горячее этого\n")))
    # оболочки групп: подписи рисуются на слой, слой пересчитывается раз в HULL_EVERY кадров
    edits.append((crlf("        screen.blit(txt, txt.get_rect(center=(int(cx), int(cy))))\n"),
                  crlf("        surface.blit(txt, txt.get_rect(center=(int(cx), int(cy))))\n")))
    edits.append((crlf("    group_layer = pygame.Surface((NET_W, H), pygame.SRCALPHA)\n"),
                  crlf("    group_layer = pygame.Surface((NET_W, H), pygame.SRCALPHA)\n"
                       "    hull_tick = 0                 # счётчик кадров для оболочек групп\n")))
    edits.append((crlf("        draw_group_hulls(screen, brain, group_layer, fonts)\n"),
                  crlf("        hull_tick += 1\n"
                       "        if hull_tick % HULL_EVERY == 1:\n"
                       "            draw_group_hulls(screen, brain, group_layer, fonts)\n"
                       "        else:\n"
                       "            screen.blit(group_layer, (0, 0))\n")))
    edits.append((crlf("HALO_MIN = 0.02           # гало рисуется только у нейронов горячее этого\n"),
                  crlf("HALO_MIN = 0.02           # гало рисуется только у нейронов горячее этого\n"
                       "HULL_EVERY = 8            # оболочки групп пересчитываются раз в N кадров\n")))
    # k-WTA: за шаг горит не больше FIRE_CAP доли нейронов; принудительные горят всегда.
    start_marker = crlf("        for n in self.neurons.values():\n            inp = random.random() * 0.03\n")
    end_marker = crlf("        self.forced = set()\n")
    start = text.index(start_marker)
    end = text.index(end_marker, start) + len(end_marker)
    edits.append((text[start:end], crlf(KWTA_BLOCK)))
    edits.append((crlf("HULL_EVERY = 8            # оболочки групп пересчитываются раз в N кадров\n"),
                  crlf("HULL_EVERY = 8            # оболочки групп пересчитываются раз в N кадров\n"
                       "FIRE_CAP = 0.02          # доля нейронов, которые горят за шаг (k-WTA, v16)\n"
                       "FIRE_MIN = 6             # минимум победителей на шаг\n")))
    return edits


def apply(path, edits, label):
    with open(path, "rb") as f:
        text = f.read().decode("utf-8")
    for old, new in edits:
        n = text.count(old)
        if n != 1:
            print(f"СТОП ({label}): якорь встречается {n} раз(а): {old[:70]!r}")
            return False
        text = text.replace(old, new)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))
    print(f"ok ({label}): {len(edits)} замен")
    return True


def main():
    brain_path = os.path.join(ROOT, "brain_chat.py")
    with open(brain_path, "rb") as f:
        brain_text = f.read().decode("utf-8")
    if not apply(brain_path, build_edits(brain_text), "brain_chat.py"):
        return 1

    menu_path = os.path.join(ROOT, "ui_feedback.py")
    with open(menu_path, "rb") as f:
        raw = f.read().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    old = '    ITEMS = (("верно", "good"),' + eol
    new = ('    ITEMS = (("активировать", "activate"),' + eol +
           '             ("верно", "good"),' + eol)
    if not apply(menu_path, [(old, new)], "ui_feedback.py"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
