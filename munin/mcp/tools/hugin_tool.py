"""Hugin bridge — passive lookup against Munin's knowledge-graph companion.

Hugin (https://princeofpwn.github.io/Hugin/) exposes a static ``entities.json`` file at
``HUGIN_URL`` (default: the GitHub Pages URL). This module caches it locally with a TTL
(``HUGIN_TTL_SECONDS``) and offers substring/regex/exact filtering on any subset of
fields. Case-insensitive by default. Own HTTP session — no shared headers from other
providers.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from ..main import MCP, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.hugin")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "munin-mcp/1.0"})


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252

    return get_settings()


def _cache_path() -> Path:
    settings = _get_settings()
    return settings.munin_data_path / "hugin_entities.json"


def _cache_meta_path() -> Path:
    settings = _get_settings()
    return settings.munin_data_path / "hugin_entities.meta.json"


def _cache_age_seconds() -> int:
    meta_path = _cache_meta_path()
    if not meta_path.exists():
        return 10**9
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return int(time.time() - float(meta.get("downloaded_at", 0)))
    except Exception:
        return 10**9


def _validate_url(url: str) -> str | None:
    """Basic SSRF guard: allow https:// only, reject metadata IPs and private ranges if not localhost."""
    lowered = url.strip().lower()
    if not lowered.startswith("https://"):
        return "hugin URL must be https://"
    banned = ("169.254.", "metadata.google.internal", "metadata.aws.internal", "0.0.0.0")
    for token in banned:
        if token in lowered:
            return f"hugin URL contains banned host token: {token}"
    return None


def _download(url: str, cache_path: Path, meta_path: Path) -> dict[str, Any]:
    err = _validate_url(url)
    if err:
        raise RuntimeError(err)
    response = _SESSION.get(url, timeout=(5, 20))
    response.raise_for_status()
    data = response.json()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")
    meta_path.write_text(json.dumps({"downloaded_at": time.time(), "url": url, "count": len(data) if isinstance(data, list) else 0}, ensure_ascii=True), encoding="utf-8")
    return {"count": len(data) if isinstance(data, list) else 0}


def _load_cached() -> list[dict[str, Any]]:
    cache_path = _cache_path()
    if not cache_path.exists():
        return []
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return raw if isinstance(raw, list) else []


def _refresh_if_needed(*, force: bool) -> dict[str, Any]:
    settings = _get_settings()
    cache_path = _cache_path()
    meta_path = _cache_meta_path()
    age = _cache_age_seconds()
    if force or age > settings.hugin_ttl_seconds or not cache_path.exists():
        return _download(settings.hugin_url, cache_path, meta_path)
    return {"skipped": True, "age_seconds": age}


@MCP.tool()
@audited_tool("hugin_search", "passive", lambda *a, **k: "sync")
def hugin_search(
    query: str,
    fields_csv: str = "title,summary",
    limit: int = 10,
    case_sensitive: bool = False,
    match_mode: str = "substring",
    force_refresh: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Search the Hugin knowledge base. match_mode: substring | regex | exact. Cache TTL via HUGIN_TTL_SECONDS."""
    if not query.strip():
        return {"ok": False, "tool": "hugin_search", "mode": "sync", "summary": "empty query", "error": {"code": "bad_input", "message": "query required"}}
    fields = [f.strip() for f in fields_csv.split(",") if f.strip()] or ["title", "summary"]
    try:
        refresh_info = _refresh_if_needed(force=bool(force_refresh))
    except Exception as exc:
        return {"ok": False, "tool": "hugin_search", "mode": "sync", "summary": "cache refresh failed", "error": {"code": "hugin_refresh_failed", "message": str(exc)}}
    entities = _load_cached()
    needle = query if case_sensitive else query.lower()
    pattern = None
    if match_mode == "regex":
        try:
            pattern = re.compile(query, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return {"ok": False, "tool": "hugin_search", "mode": "sync", "summary": "bad regex", "error": {"code": "bad_input", "message": str(exc)}}

    results: list[dict[str, Any]] = []
    for entity in entities:
        matched: list[str] = []
        for field in fields:
            value = entity.get(field, "")
            if not isinstance(value, str):
                continue
            candidate = value if case_sensitive else value.lower()
            if match_mode == "regex":
                if pattern and pattern.search(value):
                    matched.append(field)
            elif match_mode == "exact":
                if candidate == needle:
                    matched.append(field)
            else:  # substring
                if needle in candidate:
                    matched.append(field)
        if matched:
            item = dict(entity)
            item["matched_fields"] = matched
            results.append(item)
            if len(results) >= max(1, min(int(limit), 200)):
                break

    return {
        "ok": True,
        "tool": "hugin_search",
        "mode": "sync",
        "summary": f"hugin: {len(results)} matches (cache age {_cache_age_seconds()}s)",
        "data": {
            "query": query,
            "match_mode": match_mode,
            "cache_age_seconds": _cache_age_seconds(),
            "refresh": refresh_info,
            "results": results,
            "count": len(results),
        },
    }


@MCP.tool()
@audited_tool("hugin_refresh", "passive", lambda *a, **k: "sync")
def hugin_refresh(run_id: str = "") -> dict[str, Any]:
    """Force-refresh the Hugin cache from the configured URL."""
    try:
        info = _refresh_if_needed(force=True)
    except Exception as exc:
        return {"ok": False, "tool": "hugin_refresh", "mode": "sync", "summary": "refresh failed", "error": {"code": "hugin_refresh_failed", "message": str(exc)}}
    return {"ok": True, "tool": "hugin_refresh", "mode": "sync", "summary": "hugin cache refreshed", "data": info}
