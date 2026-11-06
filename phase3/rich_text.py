# -*- coding: utf-8 -*-
"""Markdown и подсветка кода для панели чата.

Строка панели — RLine: набор фрагментов (текст, цвет, ключ шрифта). Склеенный
текст фрагментов (plain) совпадает с тем, что копируется и выделяется мышью,
поэтому выделение и Ctrl+C работают одинаково для обычного и размеченного текста.

Ключи шрифтов: "chat", "chatb" (жирный), "title", "word", "mono" (моноширинный).
"""

import re

# Палитра для размеченного текста (фон панели тёмный).
TXT = (206, 212, 222)
BOLD = (240, 242, 246)
HEAD = (229, 192, 123)
INLINE = (224, 150, 120)
MUTE = (120, 126, 138)
QUOTE = (150, 158, 172)
CODE_BG = (20, 23, 29)
CODE_LABEL = (120, 126, 138)
KW = (198, 120, 221)
STR = (152, 195, 121)
NUM = (209, 154, 102)
COM = (98, 106, 120)
FUNC = (97, 175, 239)

# Ключевые слова нескольких языков: достаточно для понятной подсветки.
KEYWORDS = frozenset("""
def class return if elif else for while in is not and or import from as with try
except finally raise lambda yield pass break continue None True False self async await
global nonlocal del assert
function const let var new this typeof instanceof switch case default throw catch
extends export require void null undefined true false
int long float double char bool string struct enum fn use mut impl pub match
public private protected static final package interface implements
SELECT FROM WHERE INSERT UPDATE DELETE JOIN ON GROUP BY ORDER AND OR NOT NULL
""".split())

_CODE_TOKEN = re.compile(
    r"(#.*$|//.*$)"                                   # комментарии
    r"|(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)"  # строки
    r"|(\b\d+(?:\.\d+)?\b)"                           # числа
    r"|([A-Za-z_][A-Za-z_0-9]*)"                      # идентификаторы
)
_INLINE = re.compile(r"(\*\*[^*]+?\*\*|`[^`\n]+`|\*[^*\s][^*\n]*?\*)")
_FENCE = re.compile(r"^\s*```")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")


class RLine:
    __slots__ = ("segs", "head", "bg")

    def __init__(self, segs, head=False, bg=None):
        self.segs = segs          # [(текст, цвет, ключ_шрифта)]
        self.head = head          # заголовок-подпись (крупный шрифт)
        self.bg = bg              # фон строки (для блоков кода)

    @property
    def plain(self):
        return "".join(s[0] for s in self.segs)


def _line(segs, head=False, bg=None):
    return RLine(segs or [("", TXT, "chat")], head, bg)


# ---------- измерение и позиции (для выделения мышью) ----------

def x_at(segs, fonts, idx):
    """Горизонтальная позиция (px) перед символом idx строки."""
    x, pos = 0, 0
    for text, _, key in segs:
        n = len(text)
        if idx <= pos + n:
            return x + fonts[key].size(text[:idx - pos])[0]
        x += fonts[key].size(text)[0]
        pos += n
    return x


def index_at(segs, fonts, px):
    """Индекс символа, ближайшего к горизонтальной позиции px."""
    total = sum(len(s[0]) for s in segs)
    best, best_d = 0, None
    for i in range(total + 1):
        d = abs(x_at(segs, fonts, i) - px)
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


# ---------- подсветка кода ----------

def highlight(code_line):
    """Фрагменты одной строки кода с подсветкой."""
    segs, pos = [], 0
    for m in _CODE_TOKEN.finditer(code_line):
        if m.start() > pos:
            segs.append((code_line[pos:m.start()], TXT, "mono"))
        text = m.group(0)
        if m.group(1):
            col = COM
        elif m.group(2):
            col = STR
        elif m.group(3):
            col = NUM
        elif text in KEYWORDS:
            col = KW
        elif code_line[m.end():m.end() + 1] == "(":
            col = FUNC
        else:
            col = TXT
        segs.append((text, col, "mono"))
        pos = m.end()
    if pos < len(code_line):
        segs.append((code_line[pos:], TXT, "mono"))
    return segs or [("", TXT, "mono")]


def _wrap_chars(segs, fonts, width, bg):
    """Перенос строки кода по символам (моноширинный шрифт)."""
    out, cur, cur_w = [], [], 0
    f = fonts["mono"]
    for text, col, key in segs:
        for ch in text:
            cw = f.size(ch)[0]
            if cur and cur_w + cw > width:
                out.append(_line(cur, bg=bg))
                cur, cur_w = [], 0
            cur.append((ch, col, key))
            cur_w += cw
    out.append(_line(cur, bg=bg))
    return out


def _code_block(lang, code, fonts, width):
    out = [_line([(lang or "код", CODE_LABEL, "word")], bg=CODE_BG)]
    for raw in code or [""]:
        out += _wrap_chars(highlight(raw), fonts, width, CODE_BG)
    return out


# ---------- markdown ----------

def parse_blocks(text, full=True):
    """Блоки: ('code', язык, строки), ('h', t), ('li', метка, t), ('quote', t),
    ('blank',), ('p', t). Если full=False, разбираются только блоки кода.
    Незакрытый блок кода (потоковая генерация) считается кодом до конца текста."""
    blocks, in_code, lang, code = [], False, "", []
    for raw in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE.match(raw):
            if not in_code:
                in_code, lang, code = True, raw.strip()[3:].strip().lower(), []
            else:
                blocks.append(("code", lang, code))
                in_code = False
            continue
        if in_code:
            code.append(raw)
            continue
        if not raw.strip():
            blocks.append(("blank",))
            continue
        if not full:
            blocks.append(("p", raw))
            continue
        m = _HEAD.match(raw)
        if m:
            blocks.append(("h", m.group(2)))
            continue
        m = _NUMBERED.match(raw)
        if m:
            blocks.append(("li", f"{m.group(1)}. ", m.group(2)))
            continue
        m = _BULLET.match(raw)
        if m:
            blocks.append(("li", "• ", m.group(1)))
            continue
        m = _QUOTE.match(raw)
        if m:
            blocks.append(("quote", m.group(1)))
            continue
        blocks.append(("p", raw))
    if in_code:
        blocks.append(("code", lang, code))
    return blocks


def inline_segs(text, col, key):
    """**жирный», `код`, *курсив* (курсив — без отдельного шрифта, знаки убираются)."""
    out = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append((part[2:-2], BOLD, "chatb"))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append((part[1:-1], INLINE, "mono"))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append((part[1:-1], col, key))
        else:
            out.append((part, col, key))
    return out or [("", col, key)]


def wrap_segs(segs, fonts, width, bg=None):
    """Перенос фрагментов по словам с учётом шрифта каждого слова."""
    out, cur, cur_w = [], [], 0
    for text, col, key in segs:
        f = fonts[key]
        for tok in re.findall(r"\S+|\s+", text):
            if tok.isspace():
                cur.append((tok, col, key))
                cur_w += f.size(tok)[0]
                continue
            ww = f.size(tok)[0]
            if cur and cur_w + ww > width:
                # убираем пробелы в конце строки
                while cur and cur[-1][0].isspace():
                    cur.pop()
                out.append(_line(cur, bg=bg))
                cur, cur_w = [], 0
            cur.append((tok, col, key))
            cur_w += ww
    out.append(_line(cur, bg=bg))
    return out


def plain_lines(text, fonts, width, col, key="chat"):
    """Обычный текст (без разметки), перенос по словам."""
    out = []
    for raw in str(text).split("\n"):
        out += wrap_segs([(raw, col, key)], fonts, width) if raw else [_line([("", col, key)])]
    return out


def markdown_lines(text, fonts, width, col, key="chat", full=True):
    """Строки панели для сообщения. full=False — только блоки кода (для ввода пользователя),
    чтобы текст, который он вставил, не менялся, кроме оформления кода."""
    out = []
    for b in parse_blocks(text, full=full):
        kind = b[0]
        if kind == "blank":
            out.append(_line([("", col, key)]))
        elif kind == "code":
            out += _code_block(b[1], b[2], fonts, width)
        elif kind == "h":
            out += wrap_segs(inline_segs(b[1], HEAD, "chatb"), fonts, width)
        elif kind == "li":
            out += wrap_segs([(b[1], MUTE, key)] + inline_segs(b[2], col, key), fonts, width)
        elif kind == "quote":
            out += wrap_segs([("│ ", MUTE, key)] + inline_segs(b[1], QUOTE, key), fonts, width)
        else:
            segs = inline_segs(b[1], col, key) if full else [(b[1], col, key)]
            out += wrap_segs(segs, fonts, width)
    return out or [_line([("", col, key)])]
