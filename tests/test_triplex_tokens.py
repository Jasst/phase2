# -*- coding: utf-8 -*-
from pulsenet.triplex.memory import _tokens


def test_russian_inflections_match():
    # «проект» в факте и «проекте» в запросе должны совпасть
    assert set(_tokens("проект")) & set(_tokens("в проекте")) == {"проек"}


def test_short_function_words_dropped():
    assert "в" not in _tokens("какой python в проекте")
    assert "и" not in _tokens("и")


def test_versions_kept():
    assert _tokens("Python 3.12") == ["pytho", "3", "12"]
