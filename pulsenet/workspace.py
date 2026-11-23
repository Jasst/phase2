# -*- coding: utf-8 -*-
"""Рабочее пространство агента: навыки, заметки и правила поверх каталога data_unified.

Структура (всё — обычные файлы, их можно открыть и поправить руками):

    data_unified/
      AGENTS.md                 — правила пользователя для агента (как у opencode); необязательно
      skills/<slug>/SKILL.md    — навыки: frontmatter (name, description, triggers) + инструкция
      skills/<slug>/.history/   — прежние версии SKILL.md (откат, ничего не теряется)
      notes/<slug>.md           — заметки агента; kind=note или kind=self (его «личность»/самонаблюдения)
      notes/<slug>.md           — картинки внутри заметки: ![подпись](url) из image_generate

Агент работает с этим через инструменты (см. tools.py): skill_list/skill_read/skill_write/
skill_patch, note_write/note_append/note_read/note_list, todo_write. Здесь — вся логика и проверки;
инструменты только переводят результаты в текст для модели.

Ворота записи: имя — slug; тело ограничено по размеру; секреты (ключи, пароли, Bearer) не
сохраняются; ссылки на картинки — только http(s) или абсолютный путь.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import shutil
import threading
import uuid
from typing import Dict, List, Optional, Sequence

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|(?i:api[_-]?key|password|passwd|пароль|secret)\s*[:=]\s*\S{4,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{16,}"
)
IMG_URL_RE = re.compile(r"^(https?://[^\s()<>]+|/[^\s()<>]*)$")
WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}", re.IGNORECASE)
STOP = frozenset({"что", "как", "это", "для", "the", "and", "with", "или", "про", "мне",
                  "нужно", "надо", "если", "когда", "чтобы", "есть", "при", "from", "your"})
HISTORY_KEEP = 10
NOTE_KINDS = ("note", "self")


class WorkspaceError(ValueError):
    """Ошибка проверки ворот: текст сообщения уходит модели как результат инструмента."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    """Slug из заголовка. Нелатинский текст без ASCII-остатка → случайный «n-…»."""
    base = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:47].strip("-")
    if len(base) < 2:
        base = "n-" + uuid.uuid4().hex[:8]
    return base


def note_slug(title: str) -> str:
    """Имя файла заметки. В отличие от навыков допускает кириллицу (\\w), а текст без букв
    превращает в стабильный хеш: тот же заголовок всегда даёт тот же файл."""
    base = re.sub(r"[^\w\- ]+", "", str(title or "").lower(), flags=re.UNICODE)
    base = re.sub(r"[\s_]+", "-", base).strip("-")[:60].strip("-")
    if not base:
        base = "n-" + hashlib.sha1(str(title or "").encode("utf-8")).hexdigest()[:8]
    return base


def _clean_line(value: str, limit: int) -> str:
    """Однострочное значение для frontmatter: без переводов строк и кавычек-разделителей."""
    return re.sub(r"\s+", " ", str(value or "")).replace("---", "–").strip()[:limit]


def parse_front(raw: str) -> tuple:
    """Разбор `---\\nkey: value\\n---\\nbody`. Возвращает (dict, body). Без YAML: достаточно строк."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("\n---", 1)
    if len(parts) != 2:
        return {}, raw
    meta: Dict[str, str] = {}
    for line in parts[0].splitlines()[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, parts[1].lstrip("\n")


def render_front(meta: Dict[str, str], body: str) -> str:
    head = "\n".join(f"{k}: {v}" for k, v in meta.items() if v not in (None, ""))
    return f"---\n{head}\n---\n\n{body.strip()}\n"


def _check_body(text: str, limit: int, what: str) -> str:
    text = str(text or "").strip()
    if not text:
        raise WorkspaceError(f"{what}: пустое содержимое.")
    if len(text) > limit:
        raise WorkspaceError(f"{what}: длиннее {limit} символов ({len(text)}). Сократи или раздели.")
    if SECRET_RE.search(text):
        raise WorkspaceError(f"{what}: похоже на секрет (ключ, пароль или токен) — не сохраняю.")
    return text


def _tokens(text: str) -> List[str]:
    out = []
    for w in WORD_RE.findall(str(text or "").lower()):
        if w in STOP:
            continue
        # префикс 5 символов: «навыки»/«навыка»/«навык» совпадают (грубый стемминг для рус./англ.)
        out.append(w[:5])
    return out


class Workspace:
    """Файловое хранилище агента. Потокобезопасно внутри процесса (RLock на запись)."""

    def __init__(self, root: str, *, skills_write: bool = True, max_skill: int = 12000,
                 max_note: int = 8000, max_instructions: int = 6000):
        self.root = os.path.abspath(root)
        self.skills_dir = os.path.join(self.root, "skills")
        self.notes_dir = os.path.join(self.root, "notes")
        self.user_agents = os.path.join(self.root, "AGENTS.md")
        self.skills_write = skills_write
        self.max_skill = max_skill
        self.max_note = max_note
        self.max_instructions = max_instructions
        self._lock = threading.RLock()

    # ---------- навыки ----------

    def _skill_file(self, name: str) -> str:
        if not SLUG_RE.match(name or ""):
            raise WorkspaceError("имя навыка: строчные латинские буквы, цифры и дефис (2–48 символов).")
        return os.path.join(self.skills_dir, name, "SKILL.md")

    def _ensure_writable(self) -> None:
        if not self.skills_write:
            raise WorkspaceError("запись навыков отключена (AGENT_SKILLS_WRITE=0).")

    def skill_list(self) -> List[dict]:
        out = []
        if not os.path.isdir(self.skills_dir):
            return out
        for name in sorted(os.listdir(self.skills_dir)):
            path = os.path.join(self.skills_dir, name, "SKILL.md")
            if not (SLUG_RE.match(name) and os.path.isfile(path)):
                continue
            meta, _ = parse_front(_read(path))
            out.append({"name": name, "description": meta.get("description", ""),
                        "updated": meta.get("updated", "")})
        return out

    def skill_read(self, name: str) -> dict:
        path = self._skill_file(name)
        if not os.path.isfile(path):
            raise WorkspaceError(f"навык «{name}» не найден. Сначала skill_list.")
        meta, body = parse_front(_read(path))
        return {"name": name, "description": meta.get("description", ""), "body": body,
                "updated": meta.get("updated", "")}

    def skill_write(self, name: str, description: str, body: str) -> dict:
        """Создать или заменить навык целиком. Прежняя версия уходит в .history/."""
        self._ensure_writable()
        path = self._skill_file(name)
        desc = _clean_line(description, 300)
        if len(desc) < 10:
            raise WorkspaceError("description: нужно хотя бы 10 символов — когда применять навык.")
        text = _check_body(body, self.max_skill, "тело навыка")
        with self._lock:
            created = not os.path.isfile(path)
            if not created:
                self._snapshot(name, path)
            _write(path, render_front({"name": name, "description": desc, "updated": _now()}, text))
        return {"status": "created" if created else "updated", "name": name}

    def skill_patch(self, name: str, old: str, new: str) -> dict:
        """Точечная правка: old должен встречаться в теле ровно один раз."""
        self._ensure_writable()
        path = self._skill_file(name)
        if not os.path.isfile(path):
            raise WorkspaceError(f"навык «{name}» не найден.")
        if not old:
            raise WorkspaceError("old пустой — укажи фрагмент для замены.")
        with self._lock:
            meta, body = parse_front(_read(path))
            count = body.count(old)
            if count != 1:
                raise WorkspaceError(f"фрагмент встречается {count} раз(а); нужен ровно один.")
            new_body = _check_body(body.replace(old, new, 1), self.max_skill, "тело навыка")
            self._snapshot(name, path)
            meta["updated"] = _now()
            _write(path, render_front(meta, new_body))
        return {"status": "patched", "name": name}

    def _snapshot(self, name: str, path: str) -> None:
        hist = os.path.join(self.skills_dir, name, ".history")
        os.makedirs(hist, exist_ok=True)
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        shutil.copyfile(path, os.path.join(hist, f"{stamp}.md"))
        versions = sorted(os.listdir(hist))
        for old in versions[:-HISTORY_KEEP]:
            os.remove(os.path.join(hist, old))

    def skill_match(self, text: str, k: int = 2, min_score: int = 2) -> List[dict]:
        """Навыки, релевантные запросу: совпадения токенов в имени, описании и triggers.
        Пустой список, если совпадений мало — чтобы не засорять контекст."""
        q = set(_tokens(text))
        if not q:
            return []
        scored = []
        for item in self.skill_list():
            hay = set(_tokens(item["name"].replace("-", " ") + " " + item["description"]))
            score = len(q & hay)
            if score >= min_score:
                scored.append((score, item["name"]))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.skill_read(n) for _, n in scored[:k]]

    def skill_catalog(self, limit: int = 30) -> str:
        """Короткий список навыков для системного промпта: полный текст — через skill_read."""
        items = self.skill_list()[:limit]
        if not items:
            return ""
        lines = [f"- {i['name']}: {i['description']}" for i in items]
        return ("Твои навыки (готовые инструкции; перед похожей задачей загрузи через skill_read, "
                "новые создавай через skill_write):\n" + "\n".join(lines))

    # ---------- заметки ----------

    def _note_file(self, title_or_slug: str) -> str:
        return os.path.join(self.notes_dir, note_slug(title_or_slug) + ".md")

    def note_write(self, title: str, body: str, *, tags: Sequence[str] = (), kind: str = "note",
                   image_url: Optional[str] = None) -> dict:
        """Создать или перезаписать заметку. image_url — картинка из image_generate, вставляется в тело."""
        if kind not in NOTE_KINDS:
            raise WorkspaceError(f"kind: одно из {', '.join(NOTE_KINDS)}.")
        slug = note_slug(title)
        path = os.path.join(self.notes_dir, slug + ".md")
        text = _check_body(body, self.max_note, "заметка")
        text = _with_image(text, image_url, title)
        with self._lock:
            os.makedirs(self.notes_dir, exist_ok=True)
            prev_meta = parse_front(_read(path))[0] if os.path.isfile(path) else {}
            meta = {"title": _clean_line(title, 120), "kind": kind,
                    "tags": ", ".join(_clean_line(t, 30) for t in tags if str(t).strip())[:200],
                    "created": prev_meta.get("created") or _now(), "updated": _now()}
            _write(path, render_front(meta, text))
        return {"status": "saved", "slug": slug, "kind": kind}

    def note_append(self, title: str, text: str, image_url: Optional[str] = None) -> dict:
        path = self._note_file(title)
        if not os.path.isfile(path):
            raise WorkspaceError(f"заметка «{title}» не найдена. Создай её через note_write.")
        with self._lock:
            meta, body = parse_front(_read(path))
            extra = _check_body(text, self.max_note, "дополнение") if text else ""
            merged = body.rstrip() + ("\n\n" + extra if extra else "")
            merged = _with_image(merged, image_url, meta.get("title", title))
            if len(merged) > self.max_note:
                raise WorkspaceError(f"заметка станет длиннее {self.max_note} символов. Создай новую.")
            meta["updated"] = _now()
            _write(path, render_front(meta, merged))
        return {"status": "appended", "slug": note_slug(title)}

    def note_read(self, title: str) -> dict:
        path = self._note_file(title)
        if not os.path.isfile(path):
            raise WorkspaceError(f"заметка «{title}» не найдена.")
        meta, body = parse_front(_read(path))
        return {"title": meta.get("title", title), "kind": meta.get("kind", "note"),
                "tags": meta.get("tags", ""), "updated": meta.get("updated", ""), "body": body}

    def note_list(self, kind: Optional[str] = None, limit: int = 50) -> List[dict]:
        out = []
        if not os.path.isdir(self.notes_dir):
            return out
        for fn in sorted(os.listdir(self.notes_dir)):
            if not fn.endswith(".md"):
                continue
            meta, _ = parse_front(_read(os.path.join(self.notes_dir, fn)))
            if kind and meta.get("kind", "note") != kind:
                continue
            out.append({"slug": fn[:-3], "title": meta.get("title", fn[:-3]),
                        "kind": meta.get("kind", "note"), "tags": meta.get("tags", ""),
                        "updated": meta.get("updated", "")})
        out.sort(key=lambda x: x["updated"], reverse=True)
        return out[:limit]

    def self_notes(self, limit: int = 5, max_chars: int = 1500) -> str:
        """Последние самонаблюдения агента (kind=self) — кладутся в системный промпт.
        Это и есть «личность» между сессиями: что агент заметил о себе и о работе с пользователем."""
        items = self.note_list(kind="self", limit=limit)
        if not items:
            return ""
        parts, used = [], 0
        for item in items:
            body = self.note_read(item["slug"])["body"]
            chunk = f"- {item['title']}: {body.strip()}"
            if used + len(chunk) > max_chars:
                chunk = chunk[: max(0, max_chars - used)]
            parts.append(chunk)
            used += len(chunk)
            if used >= max_chars:
                break
        return ("Твои самонаблюдения (kind=self), учитывай их, но не пересказывай вслух:\n"
                + "\n".join(parts))

    # ---------- правила (AGENTS.md) ----------

    def instructions(self, project_agents: Optional[str] = None) -> str:
        """Правила для агента: AGENTS.md проекта + AGENTS.md пользователя из data_unified."""
        chunks = []
        for label, path in (("проект", project_agents), ("пользователь", self.user_agents)):
            if path and os.path.isfile(path):
                chunks.append(f"[правила: {label}]\n" + _read(path).strip())
        text = "\n\n".join(chunks)
        return text[: self.max_instructions] if text else ""


def _with_image(text: str, image_url: Optional[str], title: str) -> str:
    if not image_url:
        return text
    url = str(image_url).strip()
    if not IMG_URL_RE.match(url):
        raise WorkspaceError("image_url: нужен http(s)-адрес или путь, начинающийся с «/».")
    alt = _clean_line(title, 80).replace("[", "(").replace("]", ")") or "image"
    return text.rstrip() + f"\n\n![{alt}]({url})\n"


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
