# -*- coding: utf-8 -*-
"""Поиск и чтение страниц.

Порядок бэкендов — SEARCH_BACKENDS_ORDER (по умолчанию searxng,ddg): побеждает первый бэкенд,
который вернул результаты. Общий лимит времени — SEARCH_TIME_BUDGET секунд.
  searxng — свой SearXNG (SEARXNG_URL, например http://127.0.0.1:8080), нужен формат json в settings.yml
  ddg     — DuckDuckGo HTML, без ключей, на случай если SearXNG не поднят
Если задан BLOCKCOIN_PATH и его deep_search импортируется — используется он (полнее, но тяжелее).
web_fetch защищён от SSRF: только http(s), запрет приватных/служебных адресов, без редиректов."""
from __future__ import annotations

import asyncio
import html
import ipaddress
import os
import re
import socket
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx

MAX_FETCH_BYTES = 1_000_000

# Кэш успешных ответов: повторный вопрос в чате или MCP не ходит в поисковик заново.
_CACHE: Dict[str, tuple] = {}
_CACHE_MAX = 256


def _cache_ttl() -> float:
    try:
        return float(os.getenv("WEB_CACHE_TTL", "600"))
    except ValueError:
        return 600.0


def _cache_get(key: str):
    import time
    hit = _CACHE.get(key)
    if hit is None:
        return None
    stored_at, value = hit
    if time.time() - stored_at > _cache_ttl():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value) -> None:
    import time
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)), None)     # самую старую запись
    _CACHE[key] = (time.time(), value)


def clear_cache() -> None:
    _CACHE.clear()
_DDG = "https://html.duckduckgo.com/html/"
_deep_search = None
_deep_search_loaded = False


def _load_blockcoin_search():
    global _deep_search, _deep_search_loaded
    if _deep_search_loaded:
        return _deep_search
    _deep_search_loaded = True
    path = os.getenv("BLOCKCOIN_PATH", "").strip()
    if not path:
        return None
    sys.path.insert(0, path)
    try:
        from GCN.web_search import deep_search  # type: ignore
        _deep_search = deep_search
    except Exception:  # зависимости blockcoin могут быть не установлены — тогда fallback
        _deep_search = None
    return _deep_search


def is_public_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return False
    return True


def check_url(url: str) -> Optional[str]:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return "разрешены только http(s) адреса"
    if not is_public_address(p.hostname):
        return "адрес не публичный или не резолвится"
    return None


def parse_ddg_html(page: str, max_results: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
        if href.startswith("//"):
            href = "https:" + href
        q = parse_qs(urlparse(href).query).get("uddg")
        url = unquote(q[0]) if q else href
        out.append({"title": html.unescape(title).strip(), "url": url})
        if len(out) >= max_results:
            break
    return out


def html_to_text(page: str, limit: int = 20000) -> str:
    page = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def backends_order() -> List[str]:
    raw = os.getenv("SEARCH_BACKENDS_ORDER", "searxng,ddg")
    names = [n.strip().lower() for n in raw.split(",") if n.strip()]
    return [n for n in names if n in ("searxng", "ddg")] or ["searxng", "ddg"]


async def search_searxng(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    base = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
    if not base:
        return []
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{base}/search",
                             params={"q": query, "format": "json", "language": "ru", "safesearch": "1"})
        if r.status_code != 200:
            # 403 обычно значит: в settings.yml SearXNG не включён формат json
            raise RuntimeError(f"SearXNG вернул {r.status_code} (проверьте search.formats: [html, json])")
        data = r.json()
    out = []
    for item in data.get("results", []):
        url = item.get("url")
        if url:
            out.append({"title": item.get("title", ""), "url": url,
                        "snippet": item.get("content", "") or ""})
    return out[:max_results]


async def search_ddg_one(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (pulsenet-unified)"}) as client:
        r = await client.post(_DDG, data={"q": query})
        return parse_ddg_html(r.text, max_results)


_ONE = {"searxng": search_searxng, "ddg": search_ddg_one}


async def search_one_query(query: str, max_results: int) -> Dict[str, Any]:
    """Пробует бэкенды по порядку; первый непустой результат побеждает. Ошибки бэкенда не роняют цепочку."""
    budget = float(os.getenv("SEARCH_TIME_BUDGET", "15"))
    tried, errors = [], []

    async def chain():
        for name in backends_order():
            tried.append(name)
            try:
                res = await _ONE[name](query, max_results)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            if res:
                return name, res
        return None, []

    try:
        name, res = await asyncio.wait_for(chain(), timeout=budget)
    except asyncio.TimeoutError:
        return {"backend": None, "sources": [], "tried": tried, "errors": errors + ["превышен SEARCH_TIME_BUDGET"]}
    return {"backend": name, "sources": res, "tried": tried, "errors": errors}


async def search(queries: List[str], max_results: int = 5) -> Dict[str, Any]:
    queries = [q.strip() for q in queries if q and q.strip()][:4]
    if not queries:
        return {"status": "error", "message": "нужен хотя бы один запрос"}
    key = "search|" + str(max_results) + "|" + "|".join(queries)
    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}
    result = await _search_uncached(queries, max_results)
    if result.get("status") == "ok":
        _cache_put(key, result)
    return result


async def _search_uncached(queries: List[str], max_results: int) -> Dict[str, Any]:
    ds = _load_blockcoin_search()
    if ds is not None:
        results = []
        for q in queries:
            try:
                results.append(await ds(q, max_results=max_results))
            except Exception as exc:  # noqa: BLE001
                results.append({"search_performed": False, "message": str(exc)})
        sources, parts = [], []
        for q, data in zip(queries, results):
            if data.get("search_performed"):
                sources.extend(data.get("sources", []))
                if data.get("context"):
                    parts.append(f"[{q}]\n{data['context']}")
        return {"status": "ok", "backend": "blockcoin", "queries": queries,
                "sources": sources, "context": "\n\n".join(parts)}
    results = await asyncio.gather(*[search_one_query(q, max_results) for q in queries])
    seen, sources, backends, errors = set(), [], [], []
    for q, r in zip(queries, results):
        backends.append({"query": q, "backend": r["backend"], "tried": r["tried"]})
        errors += r["errors"]
        for s_ in r["sources"]:
            if s_["url"] not in seen:
                seen.add(s_["url"])
                sources.append(s_)
    return {"status": "ok" if sources else "no_results", "backends": backends, "errors": errors,
            "queries": queries, "sources": sources[:max_results * len(queries)]}


async def fetch(url: str, max_chars: int = 8000) -> Dict[str, Any]:
    err = check_url(url)
    if err:
        return {"status": "error", "message": err}
    key = "fetch|" + str(max_chars) + "|" + url
    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}
    result = await _fetch_uncached(url, max_chars)
    if result.get("status") == "ok":
        _cache_put(key, result)
    return result


async def _fetch_uncached(url: str, max_chars: int) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": "Mozilla/5.0 (pulsenet-unified)"}) as client:
        try:
            async with client.stream("GET", url) as r:
                if 300 <= r.status_code < 400:
                    return {"status": "error", "message": "редирект не выполняется (защита от SSRF)"}
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_FETCH_BYTES:
                        break
                ctype = r.headers.get("content-type", "")
        except httpx.HTTPError as exc:
            return {"status": "error", "message": str(exc)}
    text = body.decode("utf-8", errors="replace")
    text = html_to_text(text) if "html" in ctype or "<html" in text[:500].lower() else text
    return {"status": "ok", "url": url, "text": text[:max_chars], "truncated": len(text) > max_chars}
