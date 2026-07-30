"""Tavily search — fixes the PR #1 bug: use ``Authorization: Bearer`` header and an isolated session.

The PR #1 proposal put the API key in the JSON body (`api_key`) and reused the shared
`VulnIntelService.session` which had the GitHub PAT baked into its headers, causing that
PAT to leak to Tavily. This module uses its own `requests.Session` (no shared state) and
places the key in ``Authorization: Bearer …`` per current Tavily docs.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from ..main import MCP, audited_tool  # noqa: TID252

logger = logging.getLogger("munin-mcp.tavily")

_ENDPOINT = "https://api.tavily.com/search"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "munin-mcp/1.0"})


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252

    return get_settings()


@MCP.tool()
@audited_tool("tavily_search", "passive", lambda *a, **k: "sync")
def tavily_search(
    query: str,
    max_results: int = 5,
    include_domains: str = "",
    exclude_domains: str = "",
    topic: str = "general",
    search_depth: str = "basic",
    run_id: str = "",
) -> dict[str, Any]:
    """Passive web search via Tavily. Requires TAVILY_API_KEY env. Returns list of {title, url, content, score}.
    Auth header is set per-request; no shared session with other providers."""
    settings = _get_settings()
    if not settings.tavily_api_key:
        return {"ok": False, "tool": "tavily_search", "mode": "sync", "summary": "TAVILY_API_KEY not configured", "error": {"code": "config_missing", "message": "TAVILY_API_KEY empty"}}
    if not query.strip():
        return {"ok": False, "tool": "tavily_search", "mode": "sync", "summary": "empty query", "error": {"code": "bad_input", "message": "query is empty"}}
    payload: dict[str, Any] = {
        "query": query.strip(),
        "max_results": max(1, min(int(max_results), 20)),
        "topic": topic.strip() or "general",
        "search_depth": search_depth.strip() or "basic",
    }
    if include_domains.strip():
        payload["include_domains"] = [d.strip() for d in include_domains.split(",") if d.strip()]
    if exclude_domains.strip():
        payload["exclude_domains"] = [d.strip() for d in exclude_domains.split(",") if d.strip()]
    headers = {"Authorization": f"Bearer {settings.tavily_api_key}"}
    try:
        response = _SESSION.post(_ENDPOINT, json=payload, headers=headers, timeout=30)
    except requests.RequestException as exc:
        return {"ok": False, "tool": "tavily_search", "mode": "sync", "summary": "network error", "error": {"code": "network", "message": str(exc)}}
    if response.status_code != 200:
        return {"ok": False, "tool": "tavily_search", "mode": "sync", "summary": f"HTTP {response.status_code}", "error": {"code": "http_error", "message": response.text[:400]}}
    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "tool": "tavily_search", "mode": "sync", "summary": "bad JSON response", "error": {"code": "bad_response", "message": response.text[:400]}}
    results = data.get("results", [])
    return {
        "ok": True,
        "tool": "tavily_search",
        "mode": "sync",
        "summary": f"tavily: {len(results)} results",
        "data": {
            "query": payload["query"],
            "results": [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": item.get("content", ""),
                    "score": item.get("score"),
                }
                for item in results
            ],
            "answer": data.get("answer", ""),
        },
    }
