# -*- coding: utf-8 -*-
"""v20: управление сетью мышью.

- колесо над сетью — масштаб, привязанный к курсору (точка под курсором не уезжает);
- тянуть левой кнопкой по сети — сдвиг;
- клик по нейрону — выбор (его соседи подсвечиваются, в чате описание);
- двойной клик по пустому месту — вернуть автоматический вид.

Камера: автоподгонка остаётся по умолчанию; пользовательский вид накладывается
поверх неё, пока пользователь не сбросит его.

Меняет brain_chat.py (LF внутри, CRLF на запись). Каждая замена проверяет число совпадений.
Запуск из корня проекта:  python patch_net_mouse_v20.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

MODULE_BLOCK = '''# ---------- v20: управление сетью мышью ----------

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


'''

EDITS = [
    # 1. камера: пользовательский вид поверх автоподгонки
    ("    cx, cy = (c0 + c1 + 1) / 2, (r0 + r1 + 1) / 2\n"
     "    ox_t = NET_W / 2 - cx * cell_t\n"
     "    oy_t = H / 2 - cy * cell_t\n",
     "    cx, cy = (c0 + c1 + 1) / 2, (r0 + r1 + 1) / 2\n"
     "    VIEW_USER[\"base\"], VIEW_USER[\"auto\"] = cell_t, (cx, cy)\n"
     "    if VIEW_USER[\"on\"]:\n"
     "        cell_t *= VIEW_USER[\"zoom\"]\n"
     "        cx, cy = VIEW_USER[\"wcx\"], VIEW_USER[\"wcy\"]\n"
     "    ox_t = NET_W / 2 - cx * cell_t\n"
     "    oy_t = H / 2 - cy * cell_t\n", 1),
    # 2. колесо над сетью — масштаб
    ("                if THINK_RECT.collidepoint(pos):\n"
     "                    scroll[\"think\"] = max(0, scroll[\"think\"] + e.y * 3)\n",
     "                if pos[0] < NET_W:\n"
     "                    zoom_at(pos, e.y)\n"
     "                elif THINK_RECT.collidepoint(pos):\n"
     "                    scroll[\"think\"] = max(0, scroll[\"think\"] + e.y * 3)\n", 1),
    # 3. нажатие левой кнопки в сети: запоминаем, что под курсором
    ("            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:\n"
     "                # Меню нейрона",
     "            elif (e.type == pygame.MOUSEBUTTONDOWN and e.button == 1\n"
     "                  and e.pos[0] < NET_W and not neuron_menu.active):\n"
     "                nid_p = nearest_neuron(brain, e.pos[0], e.pos[1])\n"
     "                NET_PRESS.update(pos=e.pos, moved=False, nid=nid_p)\n"
     "                if nid_p is None and _is_double(e.pos):\n"
     "                    reset_view()\n"
     "                    sys_msg(\"Вид сброшен: автоматический масштаб и центр.\")\n"
     "            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:\n"
     "                # Меню нейрона", 1),
    # 4. движение с нажатой кнопкой в сети — сдвиг
    ("            elif e.type == pygame.MOUSEMOTION and FIELD_DRAG[\"on\"]:\n",
     "            elif e.type == pygame.MOUSEMOTION and NET_PRESS[\"pos\"] is not None:\n"
     "                if not NET_PRESS[\"moved\"]:\n"
     "                    x0, y0 = NET_PRESS[\"pos\"]\n"
     "                    NET_PRESS[\"moved\"] = math.hypot(e.pos[0] - x0, e.pos[1] - y0) > 4\n"
     "                if NET_PRESS[\"moved\"]:\n"
     "                    pan_by(e.rel[0], e.rel[1])\n"
     "            elif e.type == pygame.MOUSEMOTION and FIELD_DRAG[\"on\"]:\n", 1),
    # 5. отпускание: короткий щелчок по нейрону — выбор; по пустому — снять выбор
    ("            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:\n",
     "            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and NET_PRESS[\"pos\"] is not None:\n"
     "                if not NET_PRESS[\"moved\"]:\n"
     "                    msg_sel = select_neuron(brain, NET_PRESS[\"nid\"])\n"
     "                    if msg_sel:\n"
     "                        sys_msg(msg_sel)\n"
     "                    elif NET_PRESS[\"nid\"] is None:\n"
     "                        NET_SEL[\"nid\"] = None\n"
     "                NET_PRESS.update(pos=None, moved=False, nid=None)\n"
     "                dragging = None\n"
     "                FIELD_DRAG[\"on\"] = False\n"
     "            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:\n", 1),
    # 6. подсветка: выбранный нейрон остаётся фокусом, когда курсор ушёл
    ("        focus = nearest_neuron(brain, mouse[0], mouse[1])\n",
     "        focus = nearest_neuron(brain, mouse[0], mouse[1]) or NET_SEL[\"nid\"]\n", 1),
    # 7. подсказка управления
    ('HINT = "колесо — масштаб · ПКМ — действия · .txt — обучить"\n',
     'HINT = "колесо — масштаб · тянуть — сдвиг · клик — выбрать · ПКМ — меню"\n', 1),
    # 8. блок модуля вставляется перед v18-блоком
    ("# ---------- v18: поле ввода, выделение и буфер ----------\n",
     MODULE_BLOCK + "# ---------- v18: поле ввода, выделение и буфер ----------\n", 1),
]


def build(text):
    for old, new, expected in EDITS:
        n = text.count(old)
        if n != expected:
            return None, f"якорь {old[:60]!r}: {n} раз, ожидалось {expected}"
        text = text.replace(old, new)
    return text, None


def main():
    path = os.path.join(ROOT, "brain_chat.py")
    with open(path, "rb") as f:
        raw = f.read().decode("utf-8")
    lf = raw.replace("\r\n", "\n")
    new, err = build(lf)
    if err:
        print("СТОП:", err)
        return 1
    with open(path, "wb") as f:
        f.write(new.replace("\n", "\r\n").encode("utf-8"))
    print(f"ok: brain_chat.py, замен {len(EDITS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
