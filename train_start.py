"""Обучение нейросети на корпусе.

Запуск:  python train_start.py
Корпус:  файлы .txt и папки с ними (папки читаются рекурсивно).
Сохраняет состояние в brain_state.json, которое загружает brain_chat.py.
Для импорта нужен pygame (его импортирует brain_chat.py), окно не открывается.
"""

import glob
import os
import random
import sys
import time

try:  # структура из архива: файлы лежат рядом; старая структура: папка phase2/
    from brain_chat import Brain, save_brain, load_brain, SAVE_PATH, tokenize, MAX_NEURONS
except ImportError:
    from phase2.brain_chat import Brain, save_brain, load_brain, SAVE_PATH, tokenize, MAX_NEURONS

# ---------- настройки ----------
CORPUS = ["corpus.txt", "corpus"]   # файлы и папки
EPOCHS = 3                          # проходов по корпусу
RESET = False                       # True — начать с пустой сети
CHUNK_WORDS = 12                    # длинные строки режутся на куски по N слов
MIN_CHUNK_WORDS = 2                 # короче — пропускаем
SHUFFLE = True                      # перемешивать фрагменты между эпохами
SEED = 42
PROGRESS_EVERY = 5000
SAVE_EVERY_EPOCH = True


def collect_files(items):
    files = []
    for item in items:
        if os.path.isdir(item):
            files += sorted(glob.glob(os.path.join(item, "**", "*.txt"), recursive=True))
        elif os.path.isfile(item):
            files.append(item)
    return files


def load_chunks(files):
    """Читает файлы, убирает пустые строки и точные дубликаты, режет на фрагменты."""
    seen, chunks = set(), []
    for path in files:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line in seen:
                    continue
                seen.add(line)
                words = tokenize(line, limit=None)
                for i in range(0, len(words), CHUNK_WORDS):
                    chunk = words[i:i + CHUNK_WORDS]
                    if len(chunk) >= MIN_CHUNK_WORDS:
                        chunks.append(chunk)
    return chunks


def main():
    random.seed(SEED)

    files = collect_files(CORPUS)
    if not files:
        print("Корпус не найден: положите corpus.txt или папку corpus/ рядом со скриптом.")
        sys.exit(1)

    chunks = load_chunks(files)
    if not chunks:
        print("В корпусе нет подходящих строк.")
        sys.exit(1)

    vocab = {w for ch in chunks for w in ch}
    print(f"Файлов: {len(files)}, фрагментов: {len(chunks)}, уникальных слов: {len(vocab)}")
    if len(vocab) > MAX_NEURONS - 20:
        print(f"Внимание: слов {len(vocab)}, лимит нейронов {MAX_NEURONS}. "
              f"Лишние слова будут пропущены. Увеличьте MAX_NEURONS в brain_chat.py.")

    if not RESET and os.path.exists(SAVE_PATH):
        try:
            brain = load_brain()
            print(f"Дообучаем сохранённую сеть: {len(brain.neurons)} нейронов.")
        except Exception as e:
            print(f"Не удалось загрузить сохранение ({e}), начинаем с нуля.")
            brain = Brain()
    else:
        brain = Brain()
        print("Новая сеть.")

    t_all = time.time()
    for epoch in range(1, EPOCHS + 1):
        if SHUFFLE:
            random.shuffle(chunks)
        t0 = time.time()
        for i, ch in enumerate(chunks, 1):
            brain.train_words(ch)
            if i % PROGRESS_EVERY == 0:
                print(f"  эпоха {epoch}: {i}/{len(chunks)}")
        brain.regroup()
        print(f"Эпоха {epoch}/{EPOCHS}: {time.time() - t0:.1f} с, "
              f"нейронов {len(brain.neurons)}, связей {len(brain.links)}, "
              f"слов {len(brain.words)}, групп {len(set(brain.wgroup.values()))}")
        if SAVE_EVERY_EPOCH:
            save_brain(brain)

    missing = vocab - set(brain.words)
    if missing:
        print(f"Не попало в сеть слов: {len(missing)} (лимит нейронов)")

    save_brain(brain)
    print(f"Готово за {time.time() - t_all:.1f} с. Сохранено в {SAVE_PATH}")
    print("Примеры групп:")
    for line in brain.group_report()[:5]:
        print("  " + line)
    print("Теперь запустите brain_chat.py.")


if __name__ == "__main__":
    main()