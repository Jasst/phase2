# -*- coding: utf-8 -*-
"""pulsenet — онлайн-обучаемая сеть импульсов.

Нейроны-слова собираются в кластеры (ансамбли). Направленные связи между
кластерами образуют цепь импульса, которая ведёт от вопроса к ответу.
Сеть учится на лету: ответы учителя (LLM) дистиллируются в её веса.
"""
from .config import Config
from .net import BrainNet, Reply
from .teacher import HTTPTeacher, Teacher

__all__ = ["Config", "BrainNet", "Reply", "HTTPTeacher", "Teacher"]
__version__ = "1.1.0"
