# -*- coding: utf-8 -*-
"""Markdown, подсветка кода, перенос и выделение в панели чата.
Запуск из корня проекта: python test_rich_text.py
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase3 import _isolate, rich_text as rt, brain_chat as bc

_isolate.isolate()

import pygame  # noqa: E402


def _fonts():
    pygame.init()
    f = pygame.font.Font(None, 18)
    return {"chat": f, "chatb": f, "title": f, "word": f, "mono": f}


def test_keywords_strings_comments_functions_colored():
    segs = rt.highlight('def f(x): return "hi"  # done')
    colors = {text: col for text, col, _ in segs if text.strip()}
    assert colors["def"] == rt.KW and colors["return"] == rt.KW
    assert colors["f"] == rt.FUNC
    assert colors['"hi"'] == rt.STR
    assert colors["# done"] == rt.COM
    assert "".join(t for t, _, _ in segs) == 'def f(x): return "hi"  # done'


def test_numbers_colored():
    segs = rt.highlight("x = 42.5")
    assert any(t == "42.5" and c == rt.NUM for t, c, _ in segs)


def test_fenced_code_block_parsed():
    blocks = rt.parse_blocks("текст\n```python\nprint(1)\n```\nещё")
    kinds = [b[0] for b in blocks]
    assert kinds == ["p", "code", "p"], kinds
    assert blocks[1][1] == "python" and blocks[1][2] == ["print(1)"]


def test_unclosed_fence_is_code_for_streaming():
    blocks = rt.parse_blocks("```js\nconst a = 1;")
    assert blocks == [("code", "js", ["const a = 1;"])], blocks


def test_inline_bold_and_code_strip_markers():
    fonts = _fonts()
    lines = rt.markdown_lines("это **важно** и `x()`", fonts, 400, rt.TXT)
    plain = lines[0].plain
    assert plain == "это важно и x()", plain
    styles = {t: k for t, _, k in lines[0].segs}
    assert styles["важно"] == "chatb" and styles["x()"] == "mono"


def test_user_text_keeps_asterisks_outside_code():
    """Вопрос пользователя показывается как введён: звёздочки не убираются."""
    fonts = _fonts()
    lines = rt.markdown_lines("2*3*4 и **не жирный**", fonts, 400, rt.TXT, full=False)
    assert lines[0].plain == "2*3*4 и **не жирный**", lines[0].plain


def test_wrap_respects_width():
    fonts = _fonts()
    text = " ".join(["слово"] * 60)
    lines = rt.markdown_lines(text, fonts, 200, rt.TXT)
    assert len(lines) > 1
    for ln in lines:
        assert sum(fonts[k].size(t)[0] for t, _, k in ln.segs) <= 200 + 40  # допуск на одно слово
    assert " ".join(ln.plain for ln in lines).split() == text.split()


def test_index_roundtrip_for_selection():
    fonts = _fonts()
    segs = [("ab", rt.TXT, "chat"), ("CDE", rt.KW, "mono"), ("fg", rt.TXT, "chatb")]
    for i in range(0, 8):
        assert rt.index_at(segs, fonts, rt.x_at(segs, fonts, i)) == i


def test_code_block_lines_have_background_and_plain_text():
    fonts = _fonts()
    lines = rt.markdown_lines("```py\nx = 1\n```", fonts, 400, rt.TXT)
    code = [ln for ln in lines if ln.bg is not None]
    assert code and all(ln.bg == rt.CODE_BG for ln in code)
    assert any(ln.plain == "x = 1" for ln in code)


def test_panel_draws_and_selection_copies_plain_text():
    """Полный путь: панель рисует размеченный ответ, выделение даёт обычный текст."""
    pygame.init()
    fonts = {"chat": pygame.font.Font(None, 18), "chatb": pygame.font.Font(None, 18),
             "title": pygame.font.Font(None, 16), "word": pygame.font.Font(None, 14),
             "mono": pygame.font.Font(None, 16)}
    screen = pygame.Surface((420, 300))
    rect = pygame.Rect(0, 0, 400, 260)
    entries = [["you", "Покажи\n```python\nprint('hi')\n```"],
               ["net", "Ответ: **жирный** и `код`\n- пункт"]]
    bc.SEL.update(panel="chat", a=(0, 0), b=(2, 3), drag=False)
    bc.draw_panel(screen, fonts, rect, entries, 0, name="chat")
    v = bc.VIEW["chat"]
    assert v["lines"] and all(isinstance(p, str) for p, _ in v["lines"])
    # выделение дает обычный текст строк, без служебной разметки
    copied = bc.selected_text()
    assert "**" not in copied and "`" not in copied
    # клик мышью попадает в строку панели
    pos = (rect.x + v["pad"] + 5, rect.y + v["pad"] + 2)
    idx_line, idx_char = bc.point_at("chat", pos)
    assert idx_line == v["start"] and idx_char >= 0


def test_clipboard_read_is_safe():
    assert isinstance(bc.get_clipboard_text(), str)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print("OK  ", t.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", t.__name__, "->", repr(e))
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
