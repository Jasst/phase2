# -*- coding: utf-8 -*-
"""Речевой слой для brain_chat.

Графовая сеть отвечает за ассоциации и контекст.
Локальная GGUF/LM Studio модель формулирует связный ответ.

Что умеет этот слой:
  * потоковая генерация: текст появляется по мере генерации (stream());
  * отмена устаревших запросов: если пользователь задал новый вопрос,
    старая генерация прерывается на ближайшем токене;
  * эпизоды памяти передаются в промпт как доверенные прошлые ответы;
  * статистика скорости (токенов/с) доступна в last_stats.

Переменные окружения:
  BRAIN_LM_BACKEND     llamacpp (по умолчанию) | lmstudio
  BRAIN_LM_PATH        путь к .gguf (для llamacpp)
  BRAIN_LM_CTX         размер контекста (по умолчанию 4096)
  BRAIN_LM_GPU_LAYERS  слои на GPU, -1 — все (по умолчанию)
  BRAIN_LM_THREADS     потоки CPU для llama.cpp (по умолчанию авто)
  BRAIN_LM_CHAT_FORMAT шаблон чата (например chatml); по умолчанию из метаданных GGUF
  BRAIN_LM_URL, BRAIN_LM_NAME  для LM Studio
  BRAIN_LM_MAX_TOKENS, BRAIN_LM_TEMPERATURE, BRAIN_LM_TOP_P, BRAIN_LM_HISTORY,
  BRAIN_LM_TIMEOUT
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


BACKEND = os.environ.get("BRAIN_LM_BACKEND", "llamacpp").strip().lower()
MODEL_PATH = os.environ.get(
    "BRAIN_LM_PATH",
    r"E:\\models\\lmstudio-community\\Qwen3.5-9B-GGUF\\Qwen3.5-9B-Q8_0.gguf",
)
N_CTX = _env_int("BRAIN_LM_CTX", 4096)
N_GPU_LAYERS = _env_int("BRAIN_LM_GPU_LAYERS", -1)
N_THREADS = _env_int("BRAIN_LM_THREADS", 0) or None
CHAT_FORMAT = os.environ.get("BRAIN_LM_CHAT_FORMAT", "").strip() or None
LMSTUDIO_URL = os.environ.get(
    "BRAIN_LM_URL", "http://localhost:1234/v1/chat/completions"
)
LMSTUDIO_MODEL = os.environ.get("BRAIN_LM_NAME", "qwen3.5-9b")

MAX_NEW_TOKENS = _env_int("BRAIN_LM_MAX_TOKENS", 220)
TEMPERATURE = _env_float("BRAIN_LM_TEMPERATURE", 0.7)
TOP_P = _env_float("BRAIN_LM_TOP_P", 0.9)
HISTORY_TURNS = max(0, _env_int("BRAIN_LM_HISTORY", 3))
REQUEST_TIMEOUT = _env_int("BRAIN_LM_TIMEOUT", 180)

SYSTEM_PROMPT = (
    "Ты собеседник в чате. Отвечай по-русски, связно и по делу, "
    "обычно в 1–3 предложения. Ассоциации из памяти рассматривай "
    "как подсказки, а не как гарантированно верные факты. /no_think"
)
MEMORY_NOTE = (
    "Проверенные прошлые ответы (им можно доверять больше ассоциаций): "
)
LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dialog_log.jsonl"
)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean_reply(text):
    """Удаляет think-блоки и обрезает незавершённый блок рассуждений."""
    if isinstance(text, list):
        # Некоторые OpenAI-совместимые серверы возвращают content-массив.
        text = "".join(
            part.get("text", "") for part in text if isinstance(part, dict)
        )
    text = _THINK_BLOCK.sub("", str(text or ""))
    if re.search(r"<think>", text, re.I):
        text = re.split(r"<think>", text, maxsplit=1, flags=re.I)[0]
    return text.strip()


class LMBackend:
    def __init__(self):
        self.history = []
        self._gen_lock = threading.Lock()
        self.llm = None
        self.last_stats = None          # (токенов, секунд)

        if BACKEND == "llamacpp":
            if not os.path.isfile(MODEL_PATH):
                raise FileNotFoundError(f"нет файла модели: {MODEL_PATH}")
            try:
                from llama_cpp import Llama
            except ImportError as exc:
                raise RuntimeError(
                    "Не установлен llama-cpp-python. Установите его или "
                    "выберите BRAIN_LM_BACKEND=lmstudio."
                ) from exc

            print(f"[LM] загрузка модели: {MODEL_PATH}")
            kwargs = dict(
                model_path=MODEL_PATH,
                n_ctx=N_CTX,
                n_gpu_layers=N_GPU_LAYERS,
                verbose=False,
            )
            if N_THREADS:
                kwargs["n_threads"] = N_THREADS
            if CHAT_FORMAT:
                kwargs["chat_format"] = CHAT_FORMAT
            self.llm = Llama(**kwargs)
            self.name = os.path.basename(MODEL_PATH)

        elif BACKEND == "lmstudio":
            self.name = f"LM Studio ({LMSTUDIO_MODEL})"
        else:
            raise ValueError(f"неизвестный BRAIN_LM_BACKEND: {BACKEND}")

    # ---------- промпт ----------

    def _messages(self, question, ctx, memory=(), note=""):
        system = SYSTEM_PROMPT
        clean_ctx = [str(x).strip() for x in ctx if str(x).strip()]
        if clean_ctx:
            system += " Ассоциации графовой памяти: " + ", ".join(clean_ctx[:12]) + "."
        clean_mem = [str(m).strip() for m in memory if str(m).strip()]
        if clean_mem:
            system += " " + MEMORY_NOTE + " ".join(clean_mem[:3])
        if note:
            system += " " + str(note).strip()
        messages = [{"role": "system", "content": system}]
        for user_text, assistant_text in self.history[-HISTORY_TURNS:]:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        messages.append({"role": "user", "content": str(question)})
        return messages

    # ---------- потоковая генерация ----------

    def _stream_llamacpp(self, messages, should_stop):
        extra = {"repeat_penalty": 1.1}
        it = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=MAX_NEW_TOKENS,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            stream=True,
            **extra,
        )
        for chunk in it:
            if should_stop():
                break
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = (choices[0].get("delta") or {}).get("content") or ""
            if delta:
                yield delta

    def _stream_lmstudio(self, messages, should_stop):
        payload = json.dumps(
            {
                "model": LMSTUDIO_MODEL,
                "messages": messages,
                "max_tokens": MAX_NEW_TOKENS,
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
                "stream": True,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            LMSTUDIO_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"LM Studio HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"не удалось подключиться к LM Studio: {exc}") from exc

        with response:
            for raw_line in response:
                if should_stop():
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    data = json.loads(body)
                except ValueError:
                    continue
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content") or ""
                if delta:
                    yield delta

    def stream(self, question, ctx=(), memory=(), should_stop=lambda: False, note=""):
        """Генератор (текст, готово). Текст — весь ответ, очищенный от think.

        should_stop() проверяется на каждом фрагменте: при True генерация
        прерывается, а накопленный текст отдаётся с готово=True.
        """
        if not str(question).strip():
            yield "", True
            return
        with self._gen_lock:
            messages = self._messages(question, list(ctx or ()), list(memory or ()), note)
            source = (self._stream_llamacpp if BACKEND == "llamacpp"
                      else self._stream_lmstudio)
            raw, count, t0 = "", 0, time.time()
            for delta in source(messages, should_stop):
                raw += delta
                count += 1
                yield clean_reply(raw), False
            self.last_stats = (count, time.time() - t0)
            yield clean_reply(raw), True

    def generate(self, question, ctx=(), memory=(), remember=True):
        """Совместимый вариант без потока: вернуть готовый ответ."""
        reply = ""
        for reply, done in self.stream(question, ctx, memory):
            pass
        if remember and reply and HISTORY_TURNS > 0:
            self.remember_exchange(question, reply)
        return reply

    def remember_exchange(self, question, reply):
        """Сохраняет только актуальный обмен в короткую историю диалога."""
        if not reply or HISTORY_TURNS <= 0:
            return
        with self._gen_lock:
            self.history = (
                self.history + [(str(question), str(reply))]
            )[-HISTORY_TURNS:]

    def speed_text(self):
        if not self.last_stats:
            return ""
        count, seconds = self.last_stats
        if seconds <= 0:
            return ""
        return f"{count / seconds:.1f} ток/с"

    @staticmethod
    def log_feedback(question, answer, score, path=LOG_PATH):
        """Сохраняет оценку ответа (+1 /good, -1 /bad) в JSONL."""
        record = {
            "t": time.time(),
            "q": str(question),
            "a": str(answer),
            "score": int(score),
        }
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


class LMWorker:
    """Один фоновый исполнитель. Новый запрос отменяет предыдущий."""

    def __init__(self, backend):
        self.backend = backend
        self.name = backend.name
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._job = None
        self._result = None             # (id, итоговый текст)
        self._partial = (0, "")         # (id, текст по мере генерации)
        self._last_id = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, question, ctx=(), memory=(), note="", remember=True):
        with self._lock:
            self._last_id += 1
            request_id = self._last_id
            self._job = (request_id, str(question), tuple(ctx or ()), tuple(memory or ()),
                         str(note or ""), bool(remember))
            self._result = None
            self._partial = (request_id, "")
        self._wake.set()
        return request_id

    def partial(self, request_id):
        """Текст ответа, который уже сгенерирован (пусто, если ещё нет)."""
        with self._lock:
            if self._partial[0] == request_id:
                return self._partial[1]
        return ""

    def poll(self, request_id):
        """None — запрос ещё выполняется; '' — ошибка/пустой ответ."""
        with self._lock:
            if self._result is not None and self._result[0] == request_id:
                result = self._result[1]
                self._result = None
                return result
        return None

    def _cancelled(self, request_id):
        with self._lock:
            return request_id != self._last_id

    def _run(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                job, self._job = self._job, None
            if job is None:
                continue

            request_id, question, context, memory, note, remember = job
            answer = ""
            try:
                for text, done in self.backend.stream(
                    question, context, memory,
                    should_stop=lambda: self._cancelled(request_id), note=note,
                ):
                    with self._lock:
                        if request_id == self._last_id:
                            self._partial = (request_id, text)
                    if done:
                        answer = text
            except Exception as exc:
                print(f"[LM] ошибка генерации: {exc}")
                answer = ""

            with self._lock:
                if request_id == self._last_id:
                    if answer and remember:
                        self.backend.remember_exchange(question, answer)
                    self._result = (request_id, answer)
