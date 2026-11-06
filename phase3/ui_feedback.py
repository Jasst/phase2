# -*- coding: utf-8 -*-
"""Интерфейс обратной связи: кнопки оценки ответа и меню нейрона (правая кнопка).

Модуль ничего не меняет сам: клики возвращают действие, а главный цикл
выполняет его теми же путями, что и команды /good, /bad, /teach, /rename.
"""

import pygame

BTN_BG = (46, 50, 60)
BTN_HOVER = (62, 68, 82)
BTN_TEXT = (220, 225, 232)
BTN_OFF_TEXT = (110, 116, 128)
BTN_OFF_BORDER = (60, 64, 74)
MENU_BG = (34, 37, 46)
MENU_BORDER = (88, 94, 110)
MENU_HOVER = (58, 64, 80)
MENU_TEXT = (226, 230, 236)
MENU_HEAD = (150, 158, 172)
GOOD = (130, 190, 140)
BAD = (229, 84, 79)
EDIT = (210, 170, 110)


class FeedbackButtons:
    """Три кнопки под чатом: верно / неверно / переформулировать."""

    ITEMS = (("good", "верно", GOOD),
             ("bad", "неверно", BAD),
             ("teach", "переформулировать", EDIT))

    def __init__(self):
        self.rects = {}

    def _layout(self, font, x, y, w, h=22, gap=8):
        # Ширина каждой кнопки по тексту; остаток делим поровну.
        need = [font.size(label)[0] + 18 for _, label, _ in self.ITEMS]
        spare = max(0, w - sum(need) - gap * (len(need) - 1)) // len(need)
        self.rects = {}
        cx = x
        for (key, _, _), nw in zip(self.ITEMS, need):
            bw = nw + spare
            self.rects[key] = pygame.Rect(cx, y, bw, h)
            cx += bw + gap

    def draw(self, screen, font, x, y, w, enabled, mouse):
        self._layout(font, x, y, w)
        for key, label, col in self.ITEMS:
            r = self.rects[key]
            hover = enabled and r.collidepoint(mouse)
            pygame.draw.rect(screen, BTN_HOVER if hover else BTN_BG, r, border_radius=8)
            pygame.draw.rect(screen, col if enabled else BTN_OFF_BORDER, r, 1, border_radius=8)
            txt = font.render(label, True, BTN_TEXT if enabled else BTN_OFF_TEXT)
            screen.blit(txt, txt.get_rect(center=r.center))

    def hit(self, pos):
        for key, r in self.rects.items():
            if r.collidepoint(pos):
                return key
        return None


class NeuronMenu:
    """Меню нейрона по правой кнопке мыши. Действие возвращается из hit()."""

    ITEMS = (("верно", "good"),
             ("неверно", "bad"),
             ("переформулировать", "rename"),
             ("удалить нейрон", "delete"))

    def __init__(self):
        self.active = False
        self.nid = None
        self.word = None
        self.pos = (0, 0)
        self.rects = []        # [(rect, action)]
        self.box = None

    def open(self, neuron, pos):
        self.active = True
        self.nid = neuron.id
        self.word = neuron.word
        self.pos = pos
        self.rects = []
        self.box = None

    def close(self):
        self.active = False
        self.rects = []
        self.box = None

    def _items(self):
        # Переименовать можно только нейрон со словом.
        return [(label, act) for label, act in self.ITEMS
                if not (act == "rename" and not self.word)]

    def draw(self, screen, font, bounds):
        if not self.active:
            return
        head = self.word if self.word else f"нейрон #{self.nid} (без слова)"
        items = self._items()
        row_h, pad = 24, 8
        head_s = font.render(head, True, MENU_HEAD)
        widths = [font.render(lbl, True, MENU_TEXT).get_width() for lbl, _ in items]
        w = max([head_s.get_width()] + widths) + pad * 2 + 6
        h = pad * 2 + 18 + row_h * len(items)
        x, y = self.pos
        bw, bh = bounds
        x = min(x, bw - w - 2)
        y = min(y, bh - h - 2)
        self.box = pygame.Rect(max(2, x), max(2, y), w, h)
        pygame.draw.rect(screen, MENU_BG, self.box, border_radius=8)
        pygame.draw.rect(screen, MENU_BORDER, self.box, 1, border_radius=8)
        screen.blit(head_s, (self.box.x + pad, self.box.y + pad))
        mouse = pygame.mouse.get_pos()
        self.rects = []
        for i, (label, act) in enumerate(items):
            r = pygame.Rect(self.box.x + 4, self.box.y + pad + 18 + i * row_h,
                            w - 8, row_h - 2)
            if r.collidepoint(mouse):
                pygame.draw.rect(screen, MENU_HOVER, r, border_radius=5)
            screen.blit(font.render(label, True, MENU_TEXT), (r.x + pad, r.y + 4))
            self.rects.append((r, act))

    def hit(self, pos):
        """Действие по клику или None. Любой клик закрывает меню."""
        action = None
        for r, act in self.rects:
            if r.collidepoint(pos):
                action = act
                break
        self.close()
        return action
