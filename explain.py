# -*- coding: utf-8 -*-
"""Как пользователь читает интерфейс: подписи по-человечески вместо служебных чисел.

Раньше: «мысль (группа 5, оценка 0.21): ...», «Сеть · опора 0.21».
Теперь: «Мысль · тема «слово» · уверенность низкая», «Сеть · уверенность средняя (0.21)».

Модуль чистый (без pygame), его легко проверять тестами.
"""

LOW_MAX = 0.15      # уверенность ниже — низкая
MID_MAX = 0.40      # ниже — средняя, выше — высокая

HELP = {
    "thoughts": "ассоциации сети, не ответы",
    "chat": "вопросы и ответы · оценивайте кнопками под чатом",
    "hemis": "левое выбирает связные слова, правое блуждает по ассоциациям",
    "coherence": "полушария думают об одном: выше — сеть сосредоточена",
}


def confidence_word(score):
    """Уверенность словами. None — когда оценки нет."""
    if score is None:
        return "неизвестна"
    if score < LOW_MAX:
        return "низкая"
    if score < MID_MAX:
        return "средняя"
    return "высокая"


def topic_name(brain, group):
    """Название группы — её самое частое слово, а не номер."""
    if group is None:
        return "вся сеть"
    if group == "chain":
        return "цепочка сетей"
    members = [w for w, g in brain.wgroup.items() if g == group]
    if not members:
        return "неизвестная тема"
    top = max(members, key=lambda w: (brain.word_seen.get(w, 0), w))
    return f"тема «{top}»"


def describe_thought(thought, brain):
    """Мысль из ответа: тема, уверенность и путь слов."""
    head = (f"Мысль · {topic_name(brain, thought.group)} · "
            f"уверенность {confidence_word(thought.score)}")
    return head + ": " + " → ".join(thought.words)


def describe_stream(text):
    """Фоновая мысль (поток ассоциаций). Это не ответ, поэтому подпись явная."""
    return "Ход мыслей: " + text


def answer_label(score):
    """Подпись ответа в чате: уверенность словами и число для тех, кому нужно."""
    if score is None:
        return "Сеть"
    return f"Сеть · уверенность {confidence_word(score)} ({score:.2f})"


def status_text(answering, waiting, learning):
    """Короткая строка «что сейчас делает сеть» (приоритет: ответ, ожидание, обучение)."""
    if answering:
        return "отвечаю…"
    if waiting:
        return "думаю над ответом…"
    if learning:
        return "учусь…"
    return "жду вопрос · мысли идут в фоне"
