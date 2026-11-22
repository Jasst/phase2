# -*- coding: utf-8 -*-
"""MCP image_generate: нормализация параметров, локальный бэкенд и fallback."""
import asyncio
import base64
import json
import os
import tempfile

import httpx
import pytest

from pulsenet.unified_mcp import image as image_mod
from pulsenet.unified_mcp.image import ImageConfig, ImageGenerator

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20 + b"IEND\xaeB`\x82")
B64 = base64.b64encode(PNG).decode("ascii")


def _cfg(tmp, **overrides):
    env = {
        "IMAGE_BACKENDS_ORDER": "easydiffusion,pollinations",
        "EASYDIFFUSION_URL": "http://127.0.0.1:9000",
        "EASYDIFFUSION_MODEL": "DreamShaper8_pruned.safetensors",
        "EASYDIFFUSION_STEPS": "40",
        "EASYDIFFUSION_WIDTH": "513",
        "EASYDIFFUSION_HEIGHT": "767",
        "IMAGE_OUTPUT_DIR": tmp,
    }
    env.update(overrides)
    return ImageConfig.from_env(env)


def _patch_client(monkeypatch, handler):
    monkeypatch.setattr(image_mod, "_new_client",
                        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                          timeout=timeout, follow_redirects=True))


def test_config_from_env_and_normalization():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg(tmp)
        assert cfg.backends == ["easydiffusion", "pollinations"]
        assert cfg.steps == 40
        assert "DreamShaper8_pruned" in cfg.ed_model
        gen = ImageGenerator(cfg)
        p = gen.normalize(steps=None, width=None, height=None, cfg_scale=None,
                          seed=-1, sampler=None, negative_prompt=None)
        assert p["width"] == 512 and p["height"] == 768      # кратные 8
        assert p["steps"] == 40
        assert p["seed"] >= 0
        p2 = gen.normalize(steps=999, width=9999, height=8, cfg_scale=99,
                           seed=7, sampler="Euler", negative_prompt="blur")
        assert p2["steps"] == 60 and p2["width"] == 1536 and p2["height"] == 64
        assert p2["cfg_scale"] == 30.0 and p2["seed"] == 7
        assert gen.clean_prompt("  кот\tв\x00шляпе  ") == "кот в шляпе"


def test_easydiffusion_success_saves_png_and_metadata(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        seen = []

        def handler(request):
            if request.method == "POST" and request.url.path.endswith("/render"):
                seen.append(json.loads(request.content))
                return httpx.Response(200, json={"task": 1, "stream": "/image/stream/1"})
            if request.url.path.startswith("/image/stream/"):
                return httpx.Response(200, json={
                    "status": "succeeded",
                    "output": [{"data": "data:image/png;base64," + B64, "seed": 123}]})
            return httpx.Response(404, text="unknown")

        _patch_client(monkeypatch, handler)
        gen = ImageGenerator(_cfg(tmp))
        res = asyncio.run(gen.generate("кот в шляпе", uid="u1", seed=123))
        assert res["status"] == "ok"
        assert res["backend"] == "easydiffusion"
        assert res["seed"] == 123
        assert os.path.isfile(res["file_path"])
        assert res["url"].startswith("/images/u1/image_")
        sidecar = res["file_path"].replace(".png", ".json")
        with open(sidecar, encoding="utf-8") as f:
            meta = json.load(f)
        assert meta["prompt"] == "кот в шляпе" and meta["seed"] == 123
        assert meta["backend"] == "easydiffusion"
        assert seen[-1]["seed"] == 123 and seen[-1]["num_inference_steps"] == 40
        assert seen[-1]["guidance_scale"] == 7.0
        assert seen[-1]["width"] == 512 and seen[-1]["height"] == 768
        assert seen[-1]["use_stable_diffusion_model"] == "DreamShaper8_pruned.safetensors"


def test_fallback_to_pollinations(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        def handler(request):
            if request.url.host == "127.0.0.1":
                return httpx.Response(500, text="EasyDiffusion down")
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

        _patch_client(monkeypatch, handler)
        gen = ImageGenerator(_cfg(tmp))
        res = asyncio.run(gen.generate("лисёнок", uid="owner"))
        assert res["status"] == "ok"
        assert res["backend"] == "pollinations"
        assert [a["backend"] for a in res["attempts"]] == ["easydiffusion"]
        assert os.path.isfile(res["file_path"])


def test_all_backends_fail(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        def handler(request):
            return httpx.Response(503, text="down")

        _patch_client(monkeypatch, handler)
        gen = ImageGenerator(_cfg(tmp))
        res = asyncio.run(gen.generate("кот"))
        assert res["status"] == "error"
        assert [a["backend"] for a in res["attempts"]] == ["easydiffusion", "pollinations"]


def test_unknown_backend_and_empty_prompt():
    gen = ImageGenerator(ImageConfig.from_env({"IMAGE_BACKENDS_ORDER": "bogus"}))
    assert asyncio.run(gen.generate("кот"))["status"] == "error"
    assert asyncio.run(gen.generate("   "))["status"] == "error"


def test_lora_tag_requires_flag():
    cfg = ImageConfig.from_env({"EASYDIFFUSION_LORA": "realism_lora_v3_lite.safetensors",
                                "EASYDIFFUSION_LORA_USE": "0"})
    assert cfg.lora_tag == ""
    cfg2 = ImageConfig.from_env({"EASYDIFFUSION_LORA": "realism_lora_v3_lite.safetensors",
                                 "EASYDIFFUSION_LORA_USE": "1", "EASYDIFFUSION_LORA_WEIGHT": "0.7"})
    assert cfg2.lora_tag == "<lora:realism_lora_v3_lite:0.7>"
