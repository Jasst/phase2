# -*- coding: utf-8 -*-
"""v18: поле ввода с курсором и выделением, буфер обмена, копирование из панелей.

Меняет brain_chat.py: файл читается в LF, правки применяются точечно (каждая замена
проверяет число совпадений), запись — обратно в CRLF.
Заодно исправляет подмену строки ввода в цикле LLM-задач (for tag, text in ...).

Запуск из корня проекта:  python patch_textedit_v18.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

IMPORTS_OLD = "from explain import HELP, answer_label, describe_stream, describe_thought, status_text\n"
IMPORTS_NEW = IMPORTS_OLD + (
    "import textedit as te\n"
    "import clipboard\n"
)

CLIPBOARD_NEW = '''def copy_to_clipboard(text):
    """Копирует в буфер: системный, а если его нет — внутренний. Всегда True."""
    clipboard.copy(text)
    return True


def get_clipboard_text():
    """Текст из буфера (системного или внутреннего)."""
    return clipboard.paste()'''

HELPERS_NEW = '''# ---------- v18: поле ввода, выделение и буфер ----------

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
    return "\\n".join(s for s, _ in v["lines"]) if v else ""


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
            field.insert(get_clipboard_text().replace("\\r\\n", "\\n").replace("\\r", "\\n"))
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
        field.insert("\\n")
    else:
        return None
    return ""


def _draw_field(screen, font, rect, field, now):
    """Поле ввода: окно с прокруткой, выделение, курсор."""
    FIELD_VIEW.update(rect=rect, font=font)
    shown = field.text.replace("\\n", "¶")
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


# ---------- главный цикл ----------'''

# (старое, новое, сколько раз ожидаем)
EDITS = [
    (IMPORTS_OLD, IMPORTS_NEW, 1),
    ("def copy_to_clipboard(text):", "def copy_to_clipboard(text):", 1),  # проверка якоря
    ("\n    text = \"\"\n", "\n    field = te.Field()\n", 1),
    ("draw_chat(screen, thoughts, chat, text, fonts,", "draw_chat(screen, thoughts, chat, field, fonts,", 2),
    ("for tag, text in llm_tasks.tick(lm if lm_on else None, pending is not None):\n"
     "                if tag == \"explain\":\n"
     "                    chat.append([\"net\", text])\n"
     "                    scroll[\"chat\"] = 0\n"
     "                elif tag == \"sys\":\n"
     "                    sys_msg(text)\n",
     "for tag, msg_text in llm_tasks.tick(lm if lm_on else None, pending is not None):\n"
     "                if tag == \"explain\":\n"
     "                    chat.append([\"net\", msg_text])\n"
     "                    scroll[\"chat\"] = 0\n"
     "                elif tag == \"sys\":\n"
     "                    sys_msg(msg_text)\n", 1),
    ("                    msg = text.strip()\n                    text = \"\"\n",
     "                    msg = field.text.strip()\n                    field.set_text(\"\")\n", 1),
    # старые ветки правки в KEYDOWN заменяются handle_edit_key (они стали недостижимы)
    ("                elif e.key == pygame.K_c and pygame.key.get_mods() & pygame.KMOD_CTRL \\\n"
     "                        and selected_text():\n"
     "                    body = selected_text()\n"
     "                    if copy_to_clipboard(body):\n"
     "                        sys_msg(f\"Скопировано: {len(body)} симв.\")\n"
     "                    else:\n"
     "                        sys_msg(\"Буфер обмена недоступен. Текст сохранён: /copy\")\n"
     "                elif e.key == pygame.K_v and pygame.key.get_mods() & pygame.KMOD_CTRL:\n"
     "                    pasted = get_clipboard_text().replace(\"\\r\\n\", \"\\n\").replace(\"\\r\", \"\\n\")\n"
     "                    if pasted:\n"
     "                        text += pasted\n"
     "                    else:\n"
     "                        sys_msg(\"Буфер обмена пуст или недоступен.\")\n"
     "                elif e.key == pygame.K_c and pygame.key.get_mods() & pygame.KMOD_CTRL and text:\n"
     "                    if copy_to_clipboard(text):\n"
     "                        sys_msg(f\"Скопирован ввод: {len(text)} симв.\")\n"
     "                    else:\n"
     "                        sys_msg(\"Буфер обмена недоступен.\")\n"
     "                elif e.key == pygame.K_a and pygame.key.get_mods() & pygame.KMOD_CTRL \\\n"
     "                        and panel_at(pygame.mouse.get_pos()):\n"
     "                    pname = panel_at(pygame.mouse.get_pos())\n"
     "                    lines = VIEW[pname][\"lines\"]\n"
     "                    SEL.update(panel=pname, a=(0, 0),\n"
     "                               b=(len(lines) - 1, len(lines[-1][0])), drag=False)\n", "", 1),
    ("                elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and pygame.key.get_mods() & pygame.KMOD_SHIFT:\n"
     "                    text += \"\\n\"                 # Shift+Enter — перенос строки в вводе\n", "", 1),
    ("                elif e.key == pygame.K_BACKSPACE:\n"
     "                    text = text[:-1]\n"
     "                elif e.key == pygame.K_DELETE:\n"
     "                    text = \"\"\n", "", 1),
    ("            elif e.type == pygame.KEYDOWN:\n",
     "            elif e.type == pygame.KEYDOWN and (edit_msg := handle_edit_key(e, field)) is not None:\n"
     "                if edit_msg:\n"
     "                    sys_msg(edit_msg)\n"
     "            elif e.type == pygame.KEYDOWN:\n", 1),
    # клик по полю ввода: курсор, выделение протягиванием, двойной щелчок — слово
    ("                # NEW: клик по кнопке LM",
     "                if INPUT_RECT.collidepoint(e.pos):\n"
     "                    _click_field(field, e.pos, extend=bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))\n"
     "                    continue\n"
     "                # NEW: клик по кнопке LM", 1),
    # двойной щелчок по панели — слово
    ("                            SEL.update(panel=pname, a=p, b=p, drag=True)\n",
     "                            SEL.update(panel=pname, a=p, b=p, drag=True)\n"
     "                            if p is not None and _is_double(e.pos):\n"
     "                                SEL.update(_word_sel(pname, p))\n", 1),
    # правый клик по панели — копировать выделение (или всю панель)
    ("            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:\n",
     "            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:\n"
     "                if panel_at(e.pos):\n"
     "                    pname = panel_at(e.pos)\n"
     "                    body = (selected_text() if SEL[\"panel\"] == pname else \"\") or _panel_text(pname)\n"
     "                    if body:\n"
     "                        copy_to_clipboard(body)\n"
     "                        sys_msg(f\"Скопировано из панели: {len(body)} симв.\")\n"
     "                    continue\n", 1),
    # перетаскивание в поле ввода
    ("            elif e.type == pygame.MOUSEMOTION and dragging is not None:\n",
     "            elif e.type == pygame.MOUSEMOTION and FIELD_DRAG[\"on\"]:\n"
     "                _drag_field(field, e.pos)\n"
     "            elif e.type == pygame.MOUSEMOTION and dragging is not None:\n", 1),
    # дубликат блока (мышь выделения + отпускание + ввод) встречается дважды подряд: оставляем одну копию
    ("            elif e.type == pygame.MOUSEMOTION and SEL[\"drag\"]:\n"
     "                p = point_at(SEL[\"panel\"], e.pos)\n"
     "                if p is not None:\n"
     "                    SEL[\"b\"] = p\n"
     "            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:\n"
     "                dragging = None\n"
     "                SEL[\"drag\"] = False\n"
     "            elif e.type == pygame.TEXTINPUT:\n"
     "                text += e.text\n"
     "            elif e.type == pygame.MOUSEMOTION and SEL[\"drag\"]:\n"
     "                p = point_at(SEL[\"panel\"], e.pos)\n"
     "                if p is not None:\n"
     "                    SEL[\"b\"] = p\n"
     "            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:\n"
     "                dragging = None\n"
     "                SEL[\"drag\"] = False\n"
     "            elif e.type == pygame.TEXTINPUT:\n"
     "                text += e.text\n",
     "            elif e.type == pygame.MOUSEMOTION and SEL[\"drag\"]:\n"
     "                p = point_at(SEL[\"panel\"], e.pos)\n"
     "                if p is not None:\n"
     "                    SEL[\"b\"] = p\n"
     "            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:\n"
     "                dragging = None\n"
     "                SEL[\"drag\"] = False\n"
     "                FIELD_DRAG[\"on\"] = False\n"
     "            elif e.type == pygame.TEXTINPUT:\n"
     "                field.insert(e.text)\n", 1),
    ("            elif e.type == pygame.DROPTEXT:\n                text += e.text\n",
     "            elif e.type == pygame.DROPTEXT:\n                field.insert(e.text)\n", 1),
    ("draw_chat(screen, thoughts, chat, text, fonts,", "draw_chat(screen, thoughts, chat, field, fonts,", 0),
    ("# ---------- главный цикл ----------", HELPERS_NEW, 1),
]


def build(text):
    """Все замены по LF-тексту; возвращает (новый текст, сообщение об ошибке или None)."""
    # 1. буфер обмена: тело двух старых функций целиком
    start = text.index("def copy_to_clipboard(text):")
    end = text.index("\n\n\nCAM = ")          # CAM (камера) остаётся на месте
    text = text[:start] + CLIPBOARD_NEW + text[end:]
    # 2. правка поля в draw_chat: старый блок ввода заменяется вызовом _draw_field
    s2 = text.index("    rounded(screen, INPUT_RECT, PANEL_2, BORDER, 10)\n")
    e2 = text.index("\n\ndef speech(")
    text = (text[:s2] + "    rounded(screen, INPUT_RECT, PANEL_2, BORDER, 10)\n"
            "    _draw_field(screen, fonts[\"chat\"], INPUT_RECT, field, now)\n" + text[e2:])
    for old, new, expected in EDITS:
        if old == new:                      # проверочные якоря
            if text.count(old) != expected:
                return text, f"якорь {old[:50]!r}: {text.count(old)} раз, ожидалось {expected}"
            continue
        n = text.count(old)
        if n != expected:
            return text, f"якорь {old[:60]!r}: {n} раз, ожидалось {expected}"
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
    print("ok: brain_chat.py применён патч v18")
    return 0


if __name__ == "__main__":
    sys.exit(main())
