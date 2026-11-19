# -*- coding: utf-8 -*-
"""Автогенерация пар «вопрос → ответ» из корпуса через учителя (LLM).

Для каждого предложения корпуса учитель придумывает вопрос, на который оно отвечает.
Эталонный ответ — само предложение из корпуса, а не выдумка LLM: так проверяется
обобщение, а не качество учителя.

Результат — experiments/out/pairs.jsonl, по строке на пару. Повторный запуск
дописывает только новые предложения, уже обработанные пропускаются.

  python experiments/scaling/generate_pairs.py --corpus corpus --url http://localhost:1234/v1/chat/completions --model имя-модели
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from pulsenet.teacher import clean_answer  # noqa: E402  (рассуждения учителя отбрасываются)
from pulsenet.vocab import split_sentences  # noqa: E402

OUT_DEFAULT = os.path.join(HERE, "..", "out", "pairs.jsonl")
PROMPT = ("Придумай один короткий вопрос на русском языке, на который следующее предложение "
          "является прямым ответом. Верни только вопрос, без пояснений.\n\nПредложение: {sentence}")


def sentence_id(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def corpus_sentences(paths):
    """Предложения из файлов в исходном порядке, без дублей."""
    seen, out = set(), []
    for path in paths:
        with open(path, encoding="utf-8-sig") as f:
            for s in split_sentences(f.read()):
                if s not in seen and len(s) >= 15:
                    seen.add(s)
                    out.append(s)
    return out


def clean_question(raw):
    """Первая непустая строка без префикса «Вопрос:». Слишком короткое или длинное отбрасывается."""
    raw = clean_answer(raw or "")          # рассуждения учителя отбрасываются и здесь тоже
    for line in raw.splitlines():
        line = re.sub(r"^\s*(вопрос\s*[:：]\s*)", "", line.strip(), flags=re.I).strip()
        if line:
            if len(line) < 5 or len(line) > 200:
                return None
            return line if line.endswith("?") else line.rstrip(".!") + "?"
    return None


def llm_ask(url, model, prompt, timeout=120):
    """Один запрос к учителю (OpenAI-совместимый /chat/completions, без рассуждений в ответе)."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3, "max_tokens": 800}
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return clean_answer(data["choices"][0]["message"]["content"] or "")


def load_done(path):
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def generate(sentences, ask, out_path):
    """Дописывает пары в out_path. ask(prompt) -> текст. Возвращает счётчики."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    done = load_done(out_path)
    made = skipped = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for s in sentences:
            sid = sentence_id(s)
            if sid in done:
                continue
            try:
                q = clean_question(ask(PROMPT.format(sentence=s)))
            except OSError as exc:                 # учитель недоступен — останавливаемся, не портим файл
                raise SystemExit(f"учитель недоступен: {exc}. Уже сохранено пар: {made + len(done)}")
            if q is None:
                skipped += 1
                continue
            f.write(json.dumps({"id": sid, "question": q, "answer": s}, ensure_ascii=False) + "\n")
            f.flush()
            made += 1
    return {"новых": made, "пропущено": skipped, "всего": len(done) + made}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", default=os.path.join(ROOT, "corpus"), help="папка с .txt")
    p.add_argument("--url", default="http://localhost:1234/v1/chat/completions")
    p.add_argument("--model", default="local")
    p.add_argument("--out", default=OUT_DEFAULT)
    p.add_argument("--limit", type=int, default=0, help="для пробного запуска: сколько предложений")
    a = p.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.corpus, "*.txt")))
    sentences = corpus_sentences(files)
    if a.limit:
        sentences = sentences[:a.limit]
    print(f"предложений в корпусе: {len(sentences)}")
    stats = generate(sentences, lambda prompt: llm_ask(a.url, a.model, prompt), a.out)
    print(stats, "->", os.path.relpath(a.out, ROOT))


if __name__ == "__main__":
    main()
