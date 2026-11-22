# -*- coding: utf-8 -*-
"""Генерация изображений для MCP-инструмента image_generate.

Бэкенды перебираются по IMAGE_BACKENDS_ORDER, побеждает первый успешный:
  easydiffusion — локальный EasyDiffusion API (POST /render, затем /image/stream/<task>, base64 PNG);
  pollinations — публичный keyless endpoint (только если локальный не поднят).

Файлы кладутся в IMAGE_OUTPUT_DIR/<user>/; рядом сохраняется .json с prompt, seed и
параметрами, чтобы генерацию можно было повторить. Один вызов за раз (semaphore):
EasyDiffusion работает с одной моделью и одним VAE на процессе.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx

MAX_DIMENSION = 1536
MAX_STEPS = 60
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ImageError(RuntimeError):
    """Ошибка одного бэкенда: не прерывает перебор остальных."""


def _env_int(env: dict, key: str, default: int) -> int:
    try:
        return int(str(env.get(key, "")).strip() or default)
    except ValueError:
        return default


def _env_float(env: dict, key: str, default: float) -> float:
    try:
        return float(str(env.get(key, "")).strip() or default)
    except ValueError:
        return default


def _new_client(timeout: float) -> httpx.AsyncClient:
    """Отдельный клиент на вызов: генерация редкая и долгая, пул тут не нужен."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


@dataclass
class ImageConfig:
    backends: List[str] = field(default_factory=lambda: ["easydiffusion", "pollinations"])
    ed_url: str = "http://127.0.0.1:9000"
    ed_endpoint: str = "/render"
    ed_model: str = ""
    ed_vae: str = ""
    ed_negative: str = ""
    ed_sampler: str = ""
    ed_lora: str = ""
    ed_lora_weight: float = 0.8
    steps: int = 30
    width: int = 512
    height: int = 768
    cfg_scale: float = 7.0
    timeout: float = 180.0
    output_dir: str = "data_unified/images"
    pollinations_url: str = "https://image.pollinations.ai/prompt/"
    pollinations_model: str = "flux"
    max_prompt_chars: int = 500
    inline_max_bytes: int = 1_500_000

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "ImageConfig":
        env = os.environ if env is None else env
        order = [x.strip().lower() for x in
                 str(env.get("IMAGE_BACKENDS_ORDER", "easydiffusion,pollinations")).split(",")
                 if x.strip()]
        lora = (str(env.get("EASYDIFFUSION_LORA", "")).strip()
                if str(env.get("EASYDIFFUSION_LORA_USE", "0")).strip() == "1" else "")
        return cls(
            backends=order or ["easydiffusion", "pollinations"],
            ed_url=str(env.get("EASYDIFFUSION_URL") or "http://127.0.0.1:9000").strip().rstrip("/"),
            ed_model=str(env.get("EASYDIFFUSION_MODEL", "")).strip(),
            ed_vae=str(env.get("EASYDIFFUSION_VAE", "")).strip(),
            ed_negative=str(env.get("EASYDIFFUSION_NEGATIVE_PROMPT", "")).strip(),
            ed_sampler=str(env.get("EASYDIFFUSION_SAMPLER", "")).strip(),
            ed_lora=lora,
            ed_lora_weight=_env_float(env, "EASYDIFFUSION_LORA_WEIGHT", 0.8),
            steps=_env_int(env, "EASYDIFFUSION_STEPS", 30),
            width=_env_int(env, "EASYDIFFUSION_WIDTH", 512),
            height=_env_int(env, "EASYDIFFUSION_HEIGHT", 768),
            cfg_scale=_env_float(env, "EASYDIFFUSION_CFG", 7.0),
            timeout=_env_float(env, "EASYDIFFUSION_TIMEOUT", 180.0),
            output_dir=str(env.get("IMAGE_OUTPUT_DIR") or "data_unified/images").strip(),
            pollinations_model=str(env.get("IMAGE_POLLINATIONS_MODEL") or "flux").strip(),
            max_prompt_chars=_env_int(env, "IMAGE_MAX_PROMPT_CHARS", 500),
            inline_max_bytes=_env_int(env, "IMAGE_INLINE_MAX_BYTES", 1_500_000),
        )

    @property
    def lora_tag(self) -> str:
        if not self.ed_lora:
            return ""
        name = Path(self.ed_lora).stem
        return f"<lora:{name}:{self.ed_lora_weight:g}>"


def _round8(value: int) -> int:
    return max(64, min(MAX_DIMENSION, round(value / 8) * 8))


async def _report(progress, value: int, total: int, message: str) -> None:
    if progress is None:
        return
    try:
        await progress(value, total, message)
    except Exception:  # noqa: BLE001 — прогресс не должен ломать генерацию
        pass


class ImageGenerator:
    def __init__(self, cfg: Optional[ImageConfig] = None):
        self.cfg = cfg or ImageConfig.from_env()
        self._sem = asyncio.Semaphore(1)

    # ---------- подготовка ----------

    def clean_prompt(self, prompt: str) -> str:
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(prompt or ""))
        text = re.sub(r"\s+", " ", text).strip()
        return text[:self.cfg.max_prompt_chars]

    def normalize(self, *, steps, width, height, cfg_scale, seed, sampler, negative_prompt) -> Dict[str, Any]:
        if seed is None or seed < 0:
            seed = secrets.randbelow(2 ** 31 - 1)
        return {
            "steps": max(1, min(MAX_STEPS, int(steps if steps is not None else self.cfg.steps))),
            "width": _round8(int(width if width is not None else self.cfg.width)),
            "height": _round8(int(height if height is not None else self.cfg.height)),
            "cfg_scale": max(1.0, min(30.0, float(cfg_scale if cfg_scale is not None else self.cfg.cfg_scale))),
            "seed": int(seed),
            "sampler": str(sampler).strip() if sampler else self.cfg.ed_sampler,
            "negative_prompt": (str(negative_prompt) if negative_prompt is not None else self.cfg.ed_negative),
        }

    # ---------- бэкенды ----------

    def _ed_payload(self, prompt: str, p: dict) -> dict:
        cfg = self.cfg
        payload = {
            "prompt": prompt,
            "negative_prompt": p["negative_prompt"],
            "seed": p["seed"],
            "width": p["width"],
            "height": p["height"],
            "num_inference_steps": p["steps"],
            "guidance_scale": p["cfg_scale"],
            "sampler_name": p["sampler"] or None,
            "output_format": "png",
            "stream_image_progress": False,
        }
        if cfg.ed_model:
            payload["use_stable_diffusion_model"] = cfg.ed_model
        if cfg.ed_vae:
            payload["use_vae_model"] = cfg.ed_vae
        if cfg.ed_lora:
            # EasyDiffusion не понимает A1111-тег <lora:...> в промпте — модель задаётся полем.
            payload["use_lora_model"] = cfg.ed_lora
            payload["lora_alpha"] = cfg.ed_lora_weight
        return payload

    @staticmethod
    def _stream_objects(response) -> List[dict]:
        """Stream-ответ EasyDiffusion: пустые чанки, JSON-прогресс и финальный JSON."""
        text = response.text.strip()
        if not text:
            return []
        try:
            data = response.json()
            return [data] if isinstance(data, dict) else []
        except ValueError:
            pass
        out = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out

    async def _call_easydiffusion(self, client, prompt: str, p: dict, progress=None) -> bytes:
        resp = await client.post(self.cfg.ed_url + self.cfg.ed_endpoint,
                                 json=self._ed_payload(prompt, p))
        if resp.status_code != 200:
            raise ImageError(f"EasyDiffusion HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            task_id = (resp.json() or {}).get("task")
        except ValueError as exc:
            raise ImageError(f"EasyDiffusion вернул не JSON: {exc}") from exc
        if not task_id:
            raise ImageError("EasyDiffusion не вернул task id")
        stream = f"{self.cfg.ed_url}/image/stream/{task_id}"
        deadline = time.monotonic() + self.cfg.timeout
        while time.monotonic() < deadline:
            r = await client.get(stream)
            if r.status_code == 404:
                raise ImageError(f"задача EasyDiffusion {task_id} не найдена")
            if r.status_code not in (200, 425):
                raise ImageError(f"EasyDiffusion stream HTTP {r.status_code}: {r.text[:200]}")
            for obj in self._stream_objects(r):
                if obj.get("status") == "succeeded":
                    outputs = obj.get("output") or []
                    if not outputs:
                        raise ImageError("EasyDiffusion вернул пустой output")
                    return self._decode(outputs[0].get("data"))
                if obj.get("status") == "failed" or obj.get("error"):
                    raise ImageError(f"EasyDiffusion: {str(obj.get('error'))[:200]}")
                if progress is not None and "step" in obj:
                    await _report(progress, obj["step"],
                                  obj.get("total_steps") or p["steps"], "рендер EasyDiffusion")
            await asyncio.sleep(1)
        raise ImageError(f"таймаут {self.cfg.timeout:g}с ожидания EasyDiffusion")

    async def _call_pollinations(self, client, prompt: str, p: dict, progress=None) -> bytes:
        query = urlencode({
            "width": p["width"], "height": p["height"], "seed": p["seed"],
            "nologo": "true", "model": self.cfg.pollinations_model,
        })
        url = self.cfg.pollinations_url.rstrip("/") + "/" + quote(prompt, safe="") + "?" + query
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ImageError(f"Pollinations HTTP {resp.status_code}: {resp.text[:200]}")
        content = resp.content
        if not content.startswith(_PNG_MAGIC) and "image" not in resp.headers.get("content-type", ""):
            raise ImageError("Pollinations вернул не изображение")
        return content

    @staticmethod
    def _decode(value: Any) -> bytes:
        if isinstance(value, bytes):
            raw = value
        else:
            text = str(value).strip()
            if text.startswith("data:"):        # EasyDiffusion отдаёт data:image/png;base64,...
                text = text.split(",", 1)[-1]
            try:
                raw = base64.b64decode(text, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ImageError(f"не удалось декодировать изображение: {exc}") from exc
        if not raw.startswith(_PNG_MAGIC) and not raw.startswith(b"\xff\xd8\xff"):
            raise ImageError("ответ не похож на PNG/JPEG")
        return raw

    # ---------- сохранение ----------

    def _save(self, raw: bytes, uid: str, prompt: str, p: dict, backend: str) -> Dict[str, Any]:
        safe_uid = re.sub(r"[^A-Za-z0-9_.@-]", "_", str(uid))[:64] or "unknown"
        folder = Path(self.cfg.output_dir).expanduser() / safe_uid
        folder.mkdir(parents=True, exist_ok=True)
        name = f"image_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}.png"
        path = folder / name
        path.write_bytes(raw)
        meta = {
            "created": datetime.now(timezone.utc).isoformat(),
            "user": safe_uid,
            "backend": backend,
            "prompt": prompt,
            "seed": p["seed"],
            "steps": p["steps"],
            "width": p["width"],
            "height": p["height"],
            "cfg_scale": p["cfg_scale"],
            "sampler": p["sampler"],
            "negative_prompt": p["negative_prompt"],
            "model": self.cfg.ed_model if backend == "easydiffusion" else self.cfg.pollinations_model,
            "file": name,
        }
        path.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "status": "ok",
            "backend": backend,
            "file_path": str(path.resolve()),
            "url": f"/images/{safe_uid}/{name}",
            "prompt": prompt,
            "seed": p["seed"],
            "width": p["width"],
            "height": p["height"],
            "steps": p["steps"],
            "bytes": len(raw),
            "image_data": raw if len(raw) <= self.cfg.inline_max_bytes else None,
            "note": ("" if len(raw) <= self.cfg.inline_max_bytes
                     else "файл слишком большой для встроенной отправки — откройте file_path"),
        }

    # ---------- публичный вызов ----------

    async def generate(self, prompt: str, *, uid: str = "owner", steps=None, width=None,
                       height=None, cfg_scale=None, seed=None, sampler=None,
                       negative_prompt=None, backends: Optional[List[str]] = None,
                       progress=None) -> Dict[str, Any]:
        text = self.clean_prompt(prompt)
        if not text:
            return {"status": "error", "message": "пустой prompt"}
        p = self.normalize(steps=steps, width=width, height=height, cfg_scale=cfg_scale,
                           seed=seed, sampler=sampler, negative_prompt=negative_prompt)
        attempts: List[Dict[str, str]] = []
        names = [x.strip().lower() for x in (backends or self.cfg.backends) if x.strip()]
        total = max(1, len(names))
        # EasyDiffusion меняет модель/VAE через общий /options — вызовы не параллелим.
        async with self._sem:
            for i, name in enumerate(names):
                await _report(progress, i, total, f"генерация через {name}")
                try:
                    if name == "easydiffusion":
                        call = self._call_easydiffusion
                    elif name == "pollinations":
                        call = self._call_pollinations
                    else:
                        raise ImageError(f"неизвестный бэкенд {name!r}")
                    async with _new_client(self.cfg.timeout) as client:
                        raw = await call(client, text, p, progress)
                    result = self._save(raw, uid, text, p, name)
                    result["attempts"] = attempts          # какие бэкенды упали до успеха
                    await _report(progress, total, total, "готово")
                    return result
                except Exception as exc:  # noqa: BLE001 — пробуем следующий бэкенд
                    attempts.append({"backend": name, "error": str(exc)[:300]})
        return {"status": "error", "message": "не удалось сгенерировать изображение ни одним бэкендом",
                "attempts": attempts, "prompt": text, "seed": p["seed"]}
