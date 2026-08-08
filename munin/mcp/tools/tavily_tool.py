# tags: [mcp, mcp-tool, osint, recon, intel, scanning, tavily_search, api_tavily_com, bearer_auth, config_missing, isolated_requests_session, passive_web_search, domain_filtering, tavily_api_key, search_depth]
"""Tavily search — fixes the PR #1 bug: use ``Authorization: Bearer`` header and an isolated session.

The PR #1 proposal put the API key in the JSON body (`api_key`) and reused the shared
`VulnIntelService.session` which had the GitHub PAT baked into its headers, causing that
PAT to leak to Tavily. This module uses its own `requests.Session` (no shared state) and
places the key in ``Authorization: Bearer …`` per current Tavily docs.

Graceful degradation
--------------------
When TAVILY_API_KEY is missing we now return a clean structured ``config_missing``
error (with a hint) so callers (LLM in the ReAct loop, frontend Tool Explorer) can
handle it without crashing. All numeric params are coerced via ``_coerce_int`` so
MCP clients that ship ints-as-strings don't trigger TypeErrors.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from ..main import MCP, audited_tool  # noqa: TID252
from ..shared_state import _coerce_int  # noqa: TID252,PLC2701

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
    Auth header is set per-request; no shared session with other providers.
    Returns a clean structured error if TAVILY_API_KEY is unset — never crashes the ReAct loop."""
    settings = _get_settings()
    if not settings.tavily_api_key:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "TAVILY_API_KEY not configured — set env to enable",
            "error": {
                "code": "config_missing",
                "message": "TAVILY_API_KEY empty",
                "hint": "Set TAVILY_API_KEY in .env or the workflow secrets. Free tier at tavily.com.",
            },
            "data": {"query": query, "results": [], "count": 0},
        }
    if not query.strip():
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "empty query",
            "error": {"code": "bad_input", "message": "query is empty"},
        }

    payload: dict[str, Any] = {
        "query": query.strip(),
        "max_results": max(1, min(_coerce_int(max_results, 5), 20)),
        "topic": (topic or "general").strip() or "general",
        "search_depth": (search_depth or "basic").strip() or "basic",
    }
    if include_domains.strip():
        payload["include_domains"] = [d.strip() for d in include_domains.split(",") if d.strip()]
    if exclude_domains.strip():
        payload["exclude_domains"] = [d.strip() for d in exclude_domains.split(",") if d.strip()]
    headers = {"Authorization": f"Bearer {settings.tavily_api_key}"}

    try:
        response = _SESSION.post(_ENDPOINT, json=payload, headers=headers, timeout=(5, 30))
    except requests.Timeout as exc:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "tavily timeout",
            "error": {"code": "timeout", "message": str(exc)},
            "data": {"query": payload["query"], "results": [], "count": 0},
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "network error",
            "error": {"code": "network", "message": str(exc)},
            "data": {"query": payload["query"], "results": [], "count": 0},
        }

    if response.status_code == 401:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "tavily rejected key",
            "error": {
                "code": "auth_failed",
                "message": "TAVILY_API_KEY was rejected. Regenerate at tavily.com.",
            },
            "data": {"query": payload["query"], "results": [], "count": 0},
        }
    if response.status_code == 429:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "tavily rate-limited",
            "error": {"code": "rate_limited", "message": "Free tier exhausted or too many requests."},
            "data": {"query": payload["query"], "results": [], "count": 0},
        }
    if response.status_code != 200:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": f"HTTP {response.status_code}",
            "error": {"code": "http_error", "message": response.text[:400]},
            "data": {"query": payload["query"], "results": [], "count": 0},
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "ok": False,
            "tool": "tavily_search",
            "mode": "sync",
            "summary": "bad JSON response",
            "error": {"code": "bad_response", "message": response.text[:400]},
            "data": {"query": payload["query"], "results": [], "count": 0},
        }

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
            "count": len(results),
        },
    }


# Register Valravn after the FastMCP singleton and audit wrapper are ready.
# The Deep Agents Tool Gateway discovers these registrations dynamically.
from . import valravn_tool as _valravn_tool  # noqa: E402,F401

# Register the Valravn Burp extension wrapper. Same resilience pattern as the
# passive Valravn tools above — every burp_* tool returns a structured error
# envelope when the Burp extension is unreachable, so Munin keeps running in
# CI / dev environments without Burp.
try:
    from . import burp_tool as _burp_tool  # noqa: E402,F401
except Exception as _burp_register_exc:  # pragma: no cover - import-time only
    import logging

    logging.getLogger("munin-mcp").warning(
        "burp_tool register failed (Burp DAST wrapper unavailable): %s",
        _burp_register_exc,
    )
