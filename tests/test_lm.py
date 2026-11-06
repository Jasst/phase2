import os, sys, time, threading, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from phase3 import lm_backend as L

# 1) очистка think и журнал
assert L.clean_reply("<think>рассуждаю</think>Ответ") == "Ответ"
assert L.clean_reply("Начало <think>обрыв") == "Начало"
tmp = tempfile.mktemp(suffix=".jsonl")
L.LMBackend.log_feedback("q", "a", +1, path=tmp); L.LMBackend.log_feedback("q2", "a2", -1, path=tmp)
lines = open(tmp, encoding="utf-8").read().splitlines()
assert len(lines) == 2 and json.loads(lines[1])["score"] == -1, lines
print("журнал: 2 строки JSONL, корректно")

# 2) потоковая генерация + отмена устаревшего запроса (фейковый llama-источник)
b = L.LMBackend.__new__(L.LMBackend)
b.history = []; b._gen_lock = threading.Lock(); b.llm = None; b.last_stats = None; b.name = "fake"; b.backend_used = "llamacpp"
def fake_stream(messages, should_stop):
    for i in range(40):
        if should_stop():
            return
        time.sleep(0.02)
        yield f"слово{i} "
b._stream_llamacpp = fake_stream
w = L.LMWorker(b)
r1 = w.submit("первый вопрос")
time.sleep(0.25)
p1 = w.partial(r1)
assert p1.startswith("слово") and len(p1) > 10, p1
r2 = w.submit("второй вопрос")          # отменяет первый
assert w.partial(r1) == ""              # старый текст больше не виден
res2 = None
for _ in range(400):
    res2 = w.poll(r2)
    if res2 is not None: break
    time.sleep(0.02)
assert res2 and res2.startswith("слово0")
time.sleep(0.2)
assert w.poll(r1) is None               # результат отменённого запроса не публикуется
print("стриминг: частичный текст есть; отмена работает; результат r2 =", len(res2.split()), "слов")
print("скорость:", b.speed_text())
