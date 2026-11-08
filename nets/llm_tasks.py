# -*- coding: utf-8 -*-
"""Драйвер LLM-задач без потоков и без блокировок.

Задача — генератор. Он отдаёт (тег, промпт) и получает ответ LLM через send().
Драйвер вызывается из главного цикла каждый кадр: отправляет промпт LM, когда она
свободна от ответа в чат, забирает результат и передаёт его генератору. Так UI не
ждёт модель, а изменения графа происходят в главном потоке.

Теги:
  "explain" — ответ показывается пользователю в чате;
  "judge"   — служебный запрос (оценка, дистилляция), в чат не выводится.
Задача может вернуть результат; on_finish(result) вызывается в главном потоке и
возвращает список событий для показа.
"""
from collections import deque


class LLMTaskRunner:
    def __init__(self):
        self.queue = deque()        # (label, factory, on_finish)
        self.gen = None
        self.label = None
        self.on_finish = None
        self.tag = None
        self.prompt = None
        self.rid = None             # id запроса в LMWorker, если отправлен
        self.resubmits = 0

    def add(self, label, factory, on_finish=None):
        self.queue.append((label, factory, on_finish))

    def active(self):
        return self.gen is not None or bool(self.queue)

    def describe(self):
        if self.gen is not None:
            return self.label
        return self.queue[0][0] if self.queue else None

    def _start_next(self, events):
        if self.gen is None and self.queue:
            self.label, factory, self.on_finish = self.queue.popleft()
            self.gen = factory()
            self._advance(None, events, first=True)

    def _advance(self, reply, events, first=False):
        self.rid = None
        try:
            item = next(self.gen) if first else self.gen.send(reply)
        except StopIteration as stop:
            self._finish(stop.value, events)
            return
        self.tag, self.prompt = item

    def _finish(self, result, events):
        cb, self.on_finish = self.on_finish, None
        self.gen, self.tag, self.prompt = None, None, None
        if cb is not None:
            events.extend(cb(result) or [])

    def tick(self, lm, busy):
        """Вызывать каждый кадр. busy — LM занята ответом в чат.
        Возвращает список событий (тег, текст) для показа в чате."""
        events = []
        self._start_next(events)
        if self.gen is None or lm is None:
            return events
        if self.rid is None:
            if not busy:
                self.rid = lm.submit(self.prompt, (), (), "", False)
            return events
        if not lm.is_current(self.rid):      # перебита ответом в чат: отправим снова
            self.rid = None
            self.resubmits += 1
            return events
        reply = lm.poll(self.rid)
        if reply is None:
            return events
        if self.tag == "explain":
            events.append(("explain", reply))
        self._advance(reply or None, events)
        self._start_next(events)
        return events


def run_sync(gen, handler=None):
    """Выполнить генератор без LLM-драйвера. handler(текст_промпта) -> ответ или None.
    Без handler оценки откатываются на совпадение слов."""
    try:
        item = next(gen)
        while True:
            reply = handler(item[1]) if handler else None
            item = gen.send(reply)
    except StopIteration as stop:
        return stop.value
