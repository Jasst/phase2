# -*- coding: utf-8 -*-
"""Речевой слой для brain_chat.

Графовая сеть отвечает за ассоциации и контекст.
Локальная GGUF/LM Studio модель формулирует связный ответ.

Что умеет этот слой:
  * потоковая генерация: текст появляется по мере генерации (stream());
  * очистка ответа: служебные подсказки графа, которые модель могла повторить,
    вырезаются (strip_leaks), пользователь видит только сам ответ;
  * раздельные настройки, которые можно менять во время работы (RUNTIME):
      context_chars — сколько символов прошлого диалога и подсказок передаётся модели;
      history       — сколько прошлых обменов помнится;
      time_budget   — сколько секунд отводится на один ответ (0 — без лимита);
      max_tokens    — максимальная длина ответа в токенах;
  * отмена устаревших запросов: новый вопрос прерывает предыдущую генерацию;
  * статистика скорости (токенов/с) доступна в last_stats.

Переменные окружения (значения по умолчанию в скобках):
  BRAIN_LM_BACKEND     llamacpp (по умолчанию) | lmstudio
  BRAIN_LM_PATH        путь к .gguf (для llamacpp)
  BRAIN_LM_CTX         размер окна модели при загрузке (4096); задаётся до запуска
  BRAIN_LM_GPU_LAYERS  слои на GPU, -1 — все (-1)
  BRAIN_LM_THREADS     потоки CPU для llama.cpp (авто)
  BRAIN_LM_CHAT_FORMAT шаблон чата, например chatml (из метаданных GGUF)
  BRAIN_LM_URL, BRAIN_LM_NAME  для LM Studio
  BRAIN_LM_MAX_TOKENS  длина ответа в токенах (220)
  BRAIN_LM_CONTEXT_CHARS  контекст диалога в символах (6000)
  BRAIN_LM_HISTORY     прошлых обменов в контексте (3)
  BRAIN_LM_TIME_BUDGET секунд на ответ, 0 — без лимита (60)
  BRAIN_LM_TEMPERATURE, BRAIN_LM_TOP_P, BRAIN_LM_TIMEOUT
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


def _norm_path(path):
    """Нормализует путь к модели: убирает лишние пробелы/кавычки и двойные backslash'и.

    В Python r"..." уже означает «сырая» строка, поэтому писать в ней `\\\\` не нужно —
    получается буквальный путь с двумя символами `\\`, который Windows не находит.
    """
    path = str(path).strip().strip('"').strip("'")
    while "\\\\" in path:            # E:\\\\models -> E:\models
        path = path.replace("\\\\", "\\")
    return os.path.normpath(path) if path else path


BACKEND = os.environ.get("BRAIN_LM_BACKEND", "llamacpp").strip().lower()
MODEL_PATH = _norm_path(os.environ.get(
    "BRAIN_LM_PATH",
    r"E:\models\lmstudio-community\Qwen3.5-9B-GGUF\Qwen3.5-9B-Q8_0.gguf",
))
# Если путь всё равно не найден (например, диск E: недоступен), пробуем найти модель
# среди подключённых дисков по относительной части пути.
if BACKEND == "llamacpp" and not os.path.isfile(MODEL_PATH):
    for _drive in ("C:", "D:", "E:", "F:"):
        _cand = _norm_path(_drive + "\\" + MODEL_PATH.split(":", 1)[-1].lstrip("\\/"))
        if _cand != MODEL_PATH and os.path.isfile(_cand):
            MODEL_PATH = _cand
            break
N_CTX = _env_int("BRAIN_LM_CTX", 4096)
N_GPU_LAYERS = _env_int("BRAIN_LM_GPU_LAYERS", -1)
N_THREADS = _env_int("BRAIN_LM_THREADS", 0) or None
VERBOSE = os.environ.get("BRAIN_LM_VERBOSE", "0") not in ("0", "", "false", "False")
CHAT_FORMAT = os.environ.get("BRAIN_LM_CHAT_FORMAT", "").strip() or None
# Логиты нужны для дистилляции распределения LM в граф (distill_from_lm),
# но требуют logits_all=True при загрузке — это +~2.5 ГБ памяти для 9B-модели
# с n_ctx=4096. По умолчанию выключено; включается BRAIN_LM_LOGITS_ALL=1.
LOGITS_ALL = os.environ.get("BRAIN_LM_LOGITS_ALL", "0") not in ("0", "", "false", "False")
LMSTUDIO_URL = os.environ.get(
    "BRAIN_LM_URL", "http://localhost:1234/v1/chat/completions"
)
LMSTUDIO_MODEL = os.environ.get("BRAIN_LM_NAME", "qwen3.5-9b")

TEMPERATURE = _env_float("BRAIN_LM_TEMPERATURE", 0.7)
TOP_P = _env_float("BRAIN_LM_TOP_P", 0.9)
REQUEST_TIMEOUT = _env_int("BRAIN_LM_TIMEOUT", 180)

# Настройки, которые пользователь меняет во время работы (/ctx, /history, /time, /tokens).
RUNTIME = {
    "context_chars": _env_int("BRAIN_LM_CONTEXT_CHARS", 6000),
    "history": max(0, _env_int("BRAIN_LM_HISTORY", 3)),
    "time_budget": max(0.0, _env_float("BRAIN_LM_TIME_BUDGET", 60.0)),
    "max_tokens": max(16, _env_int("BRAIN_LM_MAX_TOKENS", 220)),
}
# Допустимые пределы (минимум, максимум).
LIMITS = {
    "context_chars": (500, 30000),
    "history": (0, 20),
    "time_budget": (0.0, 600.0),
    "max_tokens": (16, 2048),
}


def set_option(name, value):
    """Меняет настройку RUNTIME в допустимых пределах. Возвращает новое значение."""
    lo, hi = LIMITS[name]
    value = max(lo, min(hi, value))
    if name in ("context_chars", "history", "max_tokens"):
        value = int(value)
    RUNTIME[name] = value
    return value


def _lmstudio_url():
    """Адрес LM Studio без суффикса /chat/completions (для проверки доступности)."""
    url = LMSTUDIO_URL
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return url.rstrip("/")


def _lmstudio_available(timeout=2.0):
    """True, если сервер LM Studio отвечает (модель при этом может быть не загружена)."""
    base = _lmstudio_url()
    if not base.endswith("/v1"):
        base += "/v1"
    for path in ("/models", "/health"):
        try:
            with urllib.request.urlopen(base + path, timeout=timeout):
                return True
        except Exception:                              # noqa: BLE001
            continue
    return False


SYSTEM_PROMPT = (
    "Ты собеседник в чате. Отвечай по-русски, связно и по делу, "
    "обычно в 1–3 предложения. Служебные подсказки ниже нужны только тебе: "
    "никогда не пересказывай их, не цитируй и не упоминай в ответе. "
    "Ассоциации из памяти — возможные подсказки, а не гарантированно верные факты. "
    "/no_think"
)
MEMORY_NOTE = "Проверенные прошлые ответы (им можно доверять больше ассоциаций): "
LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dialog_log.jsonl"
)

# Фразы, которые появляются только из служебных подсказок. Предложение, где они
# встретились, в ответ не попадает. Общих слов здесь нет, чтобы не резать обычную речь.
LEAK_PHRASES = (
    "ассоциации графовой",
    "ассоциации из памяти",
    "проверенные прошлые",
    "мысль сети",
    "черновик ответа",
    "проверь черновик",
    "опирайся на них",
    "служебн",
    "/no_think",
)

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
_REASONING_START = re.compile(
    r"\s*(thinking process|thought process|analyze the request|reasoning:)", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|\n+")
_PAIR = re.compile(r"«[^»]*»\s*→\s*«[^»]*»")
_LEAK = re.compile("|".join(re.escape(p) for p in LEAK_PHRASES), re.I)
_LABEL = re.compile(r"^\s*(ответ|черновик|вопрос)\s*:\s*", re.I)
_END = re.compile(r"[.!?…]")


_WORD = re.compile(r"[^\W_]+")


def shingles(text, n=4):
    """Множество фрагментов из n слов подряд (нижний регистр)."""
    words = _WORD.findall(str(text).lower())
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def strip_leaks(text, forbidden=None):
    """Убирает предложения, которые повторяют служебные подсказки.

    forbidden — фрагменты из 4 слов подряд, взятые из системного промпта.
    Если предложение содержит такой фрагмент, оно считается пересказом инструкции.
    """
    kept = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        if not part or _LEAK.search(part) or _PAIR.search(part):
            continue
        if forbidden and shingles(part) & forbidden:
            continue
        kept.append(part)
    return " ".join(kept)


def clean_reply(text, forbidden=None):
    """Удаляет think-блоки, обрезает незавершённое рассуждение и служебные фрагменты."""
    if isinstance(text, list):
        # Некоторые OpenAI-совместимые серверы возвращают content-массив.
        text = "".join(
            part.get("text", "") for part in text if isinstance(part, dict)
        )
    text = _THINK_BLOCK.sub("", str(text or ""))
    if re.search(r"<think>", text, re.I):
        text = re.split(r"<think>", text, maxsplit=1, flags=re.I)[0]
    # Qwen-модели часто выводят рассуждение без тегов: «Thinking Process: ...».
    # Если есть закрывающий тег — берём всё после него; иначе ответа нет.
    if re.search(r"</think>", text, re.I):
        text = re.split(r"</think>", text, flags=re.I)[-1]
    elif _REASONING_START.match(text):
        return ""
    text = strip_leaks(text, forbidden)
    return _LABEL.sub("", text, count=1).strip()


RAW_CHATML = os.environ.get("BRAIN_LM_RAW_CHATML", "1") != "0"


def chatml_prompt(messages):
    """Промпт в формате ChatML с пустым блоком рассуждения для ответа."""
    parts = [f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in messages]
    parts.append("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    return "".join(parts)


def _finish(text, truncated):
    """Если ответ оборван лимитом времени или токенов, обрывает его по последней точке."""
    if not truncated:
        return text
    ends = list(_END.finditer(text))
    if ends:
        return text[:ends[-1].end()]
    return text


class LMBackend:
    def __init__(self):
        self.history = []
        self._gen_lock = threading.Lock()
        self.llm = None
        self.active_model = None  # имя модели, которое реально работает в LM Studio
        self.last_stats = None  # (токенов, секунд)
        self.last_truncated = False  # ответ оборван лимитом времени/токенов
        self._collected_logprobs = []  # снимок логитов после последней генерации

        self.backend_used = BACKEND  # реально работающий бэкенд (может смениться при откате)

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
            if LOGITS_ALL:
                print("[LM] logits_all=True (для дистилляции распределения; +память)")
            # -1 = «все слои на GPU»; если CUDA нет (или её не хватило), уменьшаем до 0.
            attempts = []
            if N_GPU_LAYERS and N_GPU_LAYERS > 0:
                attempts.append(N_GPU_LAYERS)
            else:
                attempts.extend([N_GPU_LAYERS, 0])  # -1 (авто), затем чистый CPU
            error = None
            for layers in attempts:
                kwargs = dict(
                    model_path=MODEL_PATH,
                    n_ctx=N_CTX,
                    n_gpu_layers=layers,
                    verbose=VERBOSE,
                )
                # logits_all=True обязателен для create_completion(logprobs=N).
                # Без него llama-cpp-python падает:
                #   "logprobs is not supported for models created with logits_all=False"
                if LOGITS_ALL:
                    kwargs["logits_all"] = True
                if N_THREADS:
                    kwargs["n_threads"] = N_THREADS
                if CHAT_FORMAT:
                    kwargs["chat_format"] = CHAT_FORMAT
                try:
                    self.llm = Llama(**kwargs)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    error = exc
                    print(f"[LM] не удалось загрузить (n_gpu_layers={layers}): {exc}")
            if error is not None:
                # Не роняем весь чат: переходим на LM Studio, если сервер запущен.
                if _lmstudio_available():
                    print("[LM] откат на BRAIN_LM_BACKEND=lmstudio")
                    self.backend_used = "lmstudio"
                    self.name = f"LM Studio ({LMSTUDIO_MODEL})"
                else:
                    raise RuntimeError(
                        f"не удалось загрузить GGUF ({error}); "
                        "запустите сервер в LM Studio или укажите BRAIN_LM_PATH правильно."
                    ) from error
            else:
                self.name = os.path.basename(MODEL_PATH)

        elif BACKEND == "lmstudio":
            self.name = f"LM Studio ({LMSTUDIO_MODEL})"
        else:
            raise ValueError(f"неизвестный BRAIN_LM_BACKEND: {BACKEND}")

    # ---------- промпт ----------

    def _history_messages(self, used):
        """Прошлые обмены, самые новые первыми, пока укладываются в бюджет контекста."""
        n = RUNTIME["history"]
        if n <= 0:
            return []
        budget = RUNTIME["context_chars"]
        picked = []
        for user_text, assistant_text in reversed(self.history[-n:]):
            size = len(user_text) + len(assistant_text)
            if picked and used + size > budget:
                break
            picked.append((user_text, assistant_text))
            used += size
        messages = []
        for user_text, assistant_text in reversed(picked):
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        return messages

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
        used = len(system)
        messages = [{"role": "system", "content": system}]
        messages += self._history_messages(used)
        messages.append({"role": "user", "content": str(question)})
        return messages

    # ---------- потоковая генерация ----------

    def _stream_llamacpp(self, messages, should_stop):
        self._collected_logprobs = []  # сбрасываем на каждый запрос
        call_kwargs = dict(
            prompt=chatml_prompt(messages),
            max_tokens=RUNTIME["max_tokens"],
            temperature=TEMPERATURE,
            top_p=TOP_P,
            stream=True,
            stop=["<|im_end|>", "<|endoftext|>"],
        )
        # logprobs=5 запрашиваем ТОЛЬКО если модель загружена с logits_all=True.
        # Иначе llama-cpp-python кидает:
        #   "logprobs is not supported for models created with logits_all=False"
        if LOGITS_ALL:
            call_kwargs["logprobs"] = 5
        it = self.llm.create_completion(**call_kwargs)
        for chunk in it:
            if should_stop():
                break
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("text") or ""
            if delta:
                if LOGITS_ALL:
                    # Собираем токены и их топ-логиты по всем чанкам.
                    lp = choices[0].get("logprobs") or {}
                    tokens_lp = lp.get("tokens") or []
                    top_lp = lp.get("top_logprobs") or []
                    for tok, tops in zip(tokens_lp, top_lp):
                        self._collected_logprobs.append({"token": tok, "top": tops})
                yield delta

    def _stream_lmstudio(self, messages, should_stop):
        last_error = None
        for model in self._model_candidates():
            if should_stop():
                return
            started = False
            try:
                for delta in self._lmstudio_request(messages, should_stop, model):
                    started = True
                    yield delta
                self.active_model = model or self.active_model
                return
            except RuntimeError as exc:
                # 4xx обычно означает «модель не загружена / неверное имя»: пробуем следующее.
                if started or not any(k in str(exc).lower() for k in
                                      ("http 4", "not loaded", "no model")):
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error

    def _model_candidates(self):
        """Имя модели из окружения + реальные имена моделей, доступные в LM Studio."""
        names = []
        for candidate in (self.active_model, LMSTUDIO_MODEL):
            if candidate and candidate not in names:
                names.append(candidate)
        base = _lmstudio_url()
        if not base.endswith("/v1"):
            base += "/v1"
        try:
            with urllib.request.urlopen(base + "/models", timeout=5) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            for item in data.get("data") or []:
                ident = item.get("id") or item.get("model") or ""
                if ident and ident not in names:
                    names.append(ident)
        except Exception:                              # noqa: BLE001
            pass
        return names or [""]

    def _lmstudio_request(self, messages, should_stop, model):
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_tokens": RUNTIME["max_tokens"],
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
                "stream": True,
                "chat_template_kwargs": {"enable_thinking": False},
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
        """Генератор (текст, готово). Текст — весь ответ, очищенный от служебного.

        should_stop() проверяется на каждом фрагменте: при True генерация
        прерывается. Лимит времени (RUNTIME['time_budget']) действует так же.
        """
        if not str(question).strip():
            self.last_truncated = False
            yield "", True
            return
        with self._gen_lock:
            messages = self._messages(question, list(ctx or ()), list(memory or ()), note)
            budget = RUNTIME["time_budget"]
            t0 = time.time()
            deadline = t0 + budget if budget > 0 else None
            timed_out = {"v": False}

            def stop():
                if should_stop():
                    return True
                if deadline is not None and time.time() > deadline:
                    timed_out["v"] = True
                    return True
                return False

            source = (self._stream_llamacpp if self.backend_used == "llamacpp"
                      else self._stream_lmstudio)
            # Фрагменты системного промпта: ответ, который их повторяет, очищается.
            forbidden = shingles(messages[0]["content"])
            raw, count = "", 0
            for delta in source(messages, stop):
                raw += delta
                count += 1
                yield clean_reply(raw, forbidden), False
            self.last_stats = (count, time.time() - t0)
            self.last_truncated = timed_out["v"] or count >= RUNTIME["max_tokens"]
            yield _finish(clean_reply(raw, forbidden), self.last_truncated), True

    def generate(self, question, ctx=(), memory=(), remember=True):
        """Совместимый вариант без потока: вернуть готовый ответ."""
        reply = ""
        for reply, done in self.stream(question, ctx, memory):
            pass
        if remember and reply and RUNTIME["history"] > 0:
            self.remember_exchange(question, reply)
        return reply

    def remember_exchange(self, question, reply):
        """Сохраняет только актуальный обмен в короткую историю диалога."""
        n = RUNTIME["history"]
        if not reply or n <= 0:
            return
        with self._gen_lock:
            self.history = (self.history + [(str(question), str(reply))])[-n:]

    def speed_text(self):
        if not self.last_stats:
            return ""
        count, seconds = self.last_stats
        if seconds <= 0:
            return ""
        return f"{count / seconds:.1f} ток/с"

    @staticmethod
    def log_feedback(question, answer, score, path=None):
        """Сохраняет оценку ответа (+1 /good, -1 /bad) в JSONL."""
        path = path or LOG_PATH   # читаем во время вызова, чтобы тесты могли подменить путь
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
        self.last_logprobs = []
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

    def is_current(self, request_id):
        """True, если запрос ещё не перебит новым (ответом в чат и т.п.)."""
        with self._lock:
            return request_id == self._last_id

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
                    # Снимок логитов под локом — их заберёт главный поток
                    # через lm.last_logprobs и передаст в distill_from_lm().
                    self.last_logprobs = list(
                        getattr(self.backend, "_collected_logprobs", [])
                    )