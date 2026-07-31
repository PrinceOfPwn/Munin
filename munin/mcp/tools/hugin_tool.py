"""Passive bridge to the PrinceOfPwn/Hugin knowledge graph.

Hugin publishes ``hugin/graph.json`` with ``nodes``, ``edges`` and a ``contents``
mapping. Munin normalises that graph, stores it in the shared persistence backend
(SQLite or Turso), and keeps a file fallback for offline recovery.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from ..main import MCP, STATE, audited_tool  # noqa: TID252
from ..shared_state import _coerce_int  # noqa: TID252,PLC2701

logger = logging.getLogger("munin-mcp.hugin")

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "munin-mcp/1.0"})
_CACHE_NAMESPACE = "hugin"
_CACHE_KEY = "graph"
_FALLBACK_URLS = (
    "https://raw.githubusercontent.com/PrinceOfPwn/Hugin/main/hugin/graph.json",
    "https://princeofpwn.github.io/Hugin/data/public-graph.json",
    "https://princeofpwn.github.io/Hugin/data/entities.json",
)
_WINDOWS_PATH_WITH_LONE_SEPARATORS = re.compile(r'(?<!\\)\b[A-Za-z]:(?:\\[^"\\\r\n]+)+')


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Handle MCP clients that serialize booleans as strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default if value is None else bool(value)


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252

    return get_settings()


def _cache_path() -> Path:
    return _get_settings().munin_data_path / "hugin_graph.json"


def _validate_url(url: str) -> str | None:
    """Basic SSRF guard for a configurable passive data source."""
    lowered = url.strip().lower()
    if not lowered.startswith("https://"):
        return "hugin URL must be https://"
    banned = (
        "169.254.",
        "metadata.google.internal",
        "metadata.aws.internal",
        "0.0.0.0",  # noqa: S104 - blocked input, not a bind address
        "localhost",
    )
    for token in banned:
        if token in lowered:
            return f"hugin URL contains banned host token: {token}"
    return None


def _candidate_urls(primary: str) -> list[str]:
    urls = [primary.strip()] if primary and primary.strip() else []
    for url in _FALLBACK_URLS:
        if url not in urls:
            urls.append(url)
    return urls


def _decode_payload(text: str, *, source_url: str) -> Any:
    """Decode an upstream Hugin payload, repairing only invalid JSON escapes.

    Hugin's graph includes source snippets.  A malformed upstream snippet such
    as ``C:\\Temp`` can leave a lone ``\\T`` in an otherwise valid graph.  The
    graph is data only (never executed); preserving that literal backslash is
    safer and more useful than dropping the complete passive-intel cache.
    """
    def escape_windows_path(match: re.Match[str]) -> str:
        return match.group(0).replace("\\", "\\\\")

    # Repair a complete Windows-style path first.  Some valid JSON escapes
    # such as ``\\n`` otherwise decode as a newline even though the upstream
    # source intended ``\\notes`` as part of a path.
    repaired_paths, path_count = _WINDOWS_PATH_WITH_LONE_SEPARATORS.subn(escape_windows_path, text)
    repaired_count = 0
    try:
        parsed = json.loads(repaired_paths)
    except json.JSONDecodeError as original_error:
        # JSON permits only " \\ / b f n r t u after a backslash.  Repair a
        # *single* malformed escape by making the backslash literal.  Existing
        # escaped backslashes stay untouched, and a second decode is still the
        # authority for all remaining JSON structure.
        repaired, repaired_count = re.subn(r'(?<!\\)\\(?!["\\\\/bfnrtu])', r"\\\\", repaired_paths)
        if not repaired_count:
            raise original_error
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError as repaired_error:
            raise original_error from repaired_error
    if path_count or repaired_count:
        logger.warning(
            "Recovered Hugin graph from %s by escaping %d Windows path(s) and %d malformed JSON sequence(s)",
            source_url,
            path_count,
            repaired_count,
        )
    return parsed


def _download_from(url: str) -> Any:
    error = _validate_url(url)
    if error:
        raise RuntimeError(error)
    response = _SESSION.get(url, timeout=(5, 20))
    response.raise_for_status()
    return _decode_payload(response.text, source_url=url)


def _normalise_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        entities = [dict(item) for item in raw if isinstance(item, dict)]
        return {"entities": entities, "edges": [], "source_format": "entity-list"}

    if not isinstance(raw, dict):
        raise ValueError("Hugin payload must be an object or entity list")

    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("Hugin graph payload is missing a nodes list")
    contents = raw.get("contents") if isinstance(raw.get("contents"), dict) else {}
    entities: list[dict[str, Any]] = []
    for item in nodes:
        if not isinstance(item, dict):
            continue
        node = dict(item)
        node_id = str(node.get("id", ""))
        if node_id and node_id in contents:
            node["content"] = contents[node_id]
        entities.append(node)
    edges = [dict(item) for item in raw.get("edges", []) if isinstance(item, dict)]
    return {
        "entities": entities,
        "edges": edges,
        "source_format": "hugin-graph",
        "category_colors": raw.get("category_colors", {}),
        "edge_types": raw.get("edge_types", {}),
    }


def _persist(url: str, raw: Any, ttl: int) -> dict[str, Any]:
    bundle = _normalise_payload(raw)
    bundle["source_url"] = url
    bundle["downloaded_at_epoch"] = time.time()
    STATE.cache_put(_CACHE_NAMESPACE, _CACHE_KEY, bundle, ttl_seconds=ttl)

    # The file is deliberately a fallback, not the authority. It also makes the
    # cached graph inspectable and useful when moving a local Munin workspace.
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=True), encoding="utf-8")
    return {
        "count": len(bundle["entities"]),
        "edges": len(bundle["edges"]),
        "url": url,
    }


def _load_cached(*, allow_stale: bool = True) -> tuple[dict[str, Any] | None, int, bool]:
    try:
        cached = STATE.cache_get(_CACHE_NAMESPACE, _CACHE_KEY, allow_stale=allow_stale)
    except Exception as exc:  # pragma: no cover - storage outage fallback
        logger.warning("Hugin persistent cache read failed: %s", exc)
        cached = None
    if cached:
        value = cached.get("value")
        if isinstance(value, dict):
            return value, int(cached.get("age_seconds", 0)), bool(cached.get("is_stale"))

    path = _cache_path()
    if not path.exists():
        return None, 10**9, True
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        downloaded = float(bundle.get("downloaded_at_epoch", path.stat().st_mtime))
        return bundle, max(0, int(time.time() - downloaded)), True
    except (OSError, ValueError, TypeError):
        logger.warning("Hugin file cache is corrupt; ignoring it")
        return None, 10**9, True


def _refresh(*, force: bool, primary_url: str, ttl: int) -> dict[str, Any]:
    bundle, age, _ = _load_cached(allow_stale=True)
    if not force and bundle is not None and age <= ttl:
        return {"status": "skipped", "age_seconds": age}

    attempts: list[dict[str, Any]] = []
    for url in _candidate_urls(primary_url):
        try:
            raw = _download_from(url)
            info = _persist(url, raw, ttl)
        except requests.Timeout as exc:
            attempts.append({"url": url, "error": "timeout", "message": str(exc)})
        except requests.HTTPError as exc:
            attempts.append({
                "url": url,
                "error": "http",
                "status": exc.response.status_code if exc.response is not None else None,
                "message": str(exc),
            })
        except (requests.RequestException, RuntimeError, ValueError, OSError) as exc:
            attempts.append({"url": url, "error": exc.__class__.__name__, "message": str(exc)})
        else:
            return {"status": "ok", "attempts": attempts, **info}
    return {"status": "failed", "attempts": attempts, "last_error": attempts[-1] if attempts else None}


def _field_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _bundle_or_error(force_refresh: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any], int, bool]:
    settings = _get_settings()
    refresh = _refresh(
        force=force_refresh,
        primary_url=settings.hugin_url,
        ttl=settings.hugin_ttl_seconds,
    )
    bundle, age, cached_stale = _load_cached(allow_stale=True)
    is_stale = refresh["status"] == "failed" or cached_stale
    return bundle, refresh, age, is_stale


@MCP.tool()
@audited_tool("hugin_search", "passive", lambda *a, **k: "sync")
def hugin_search(
    query: str,
    fields_csv: str = "label,tags,category,mitre,content",
    limit: int = 10,
    case_sensitive: bool = False,
    match_mode: str = "substring",
    force_refresh: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Search Hugin nodes. ``match_mode`` is substring, regex, or exact."""
    if not query.strip():
        return {
            "ok": False,
            "tool": "hugin_search",
            "mode": "sync",
            "summary": "empty query",
            "error": {"code": "bad_input", "message": "query required"},
        }
    if match_mode not in {"substring", "regex", "exact"}:
        return {
            "ok": False,
            "tool": "hugin_search",
            "mode": "sync",
            "summary": "invalid match mode",
            "error": {"code": "bad_input", "message": "match_mode must be substring, regex, or exact"},
        }

    bundle, refresh, age, is_stale = _bundle_or_error(_coerce_bool(force_refresh))
    if not bundle:
        return {
            "ok": False,
            "tool": "hugin_search",
            "mode": "sync",
            "summary": "hugin unavailable; no persistent cache",
            "error": {
                "code": "hugin_unavailable",
                "message": (refresh.get("last_error") or {}).get("message", "no candidate URL succeeded"),
                "attempts": refresh.get("attempts", []),
            },
        }

    aliases = {"title": "label", "summary": "content"}
    fields = [aliases.get(field.strip(), field.strip()) for field in fields_csv.split(",") if field.strip()]
    fields = fields or ["label", "tags", "category", "mitre", "content"]
    sensitive = _coerce_bool(case_sensitive)
    needle = query if sensitive else query.lower()
    pattern = None
    if match_mode == "regex":
        try:
            pattern = re.compile(query, 0 if sensitive else re.IGNORECASE)
        except re.error as exc:
            return {
                "ok": False,
                "tool": "hugin_search",
                "mode": "sync",
                "summary": "bad regex",
                "error": {"code": "bad_input", "message": str(exc)},
            }

    cap = max(1, min(_coerce_int(limit, 10), 200))
    results: list[dict[str, Any]] = []
    for entity in bundle.get("entities", []):
        matched: list[str] = []
        for field in fields:
            value = _field_text(entity.get(field))
            candidate = value if sensitive else value.lower()
            if match_mode == "regex" and pattern and pattern.search(value):
                matched.append(field)
            elif match_mode == "exact" and candidate == needle:
                matched.append(field)
            elif match_mode == "substring" and needle in candidate:
                matched.append(field)
        if matched:
            results.append({**entity, "matched_fields": matched})
            if len(results) >= cap:
                break

    return {
        "ok": True,
        "tool": "hugin_search",
        "mode": "sync",
        "summary": f"hugin: {len(results)} matches from {len(bundle.get('entities', []))} nodes",
        "data": {
            "query": query,
            "match_mode": match_mode,
            "cache_age_seconds": age,
            "refresh": refresh,
            "is_stale": is_stale,
            "results": results,
            "count": len(results),
        },
    }


@MCP.tool()
@audited_tool("hugin_neighbors", "passive", lambda *a, **k: "sync")
def hugin_neighbors(
    node_id: str,
    depth: int = 1,
    relation_type: str = "",
    limit: int = 50,
    force_refresh: bool = False,
    run_id: str = "",
) -> dict[str, Any]:
    """Traverse Hugin relationships around a node (one to three hops)."""
    bundle, refresh, age, is_stale = _bundle_or_error(_coerce_bool(force_refresh))
    if not bundle:
        return {
            "ok": False,
            "tool": "hugin_neighbors",
            "mode": "sync",
            "summary": "hugin unavailable; no persistent cache",
            "error": {"code": "hugin_unavailable", "message": "no graph cache available"},
        }
    nodes = {str(node.get("id")): node for node in bundle.get("entities", []) if node.get("id")}
    start = node_id.strip()
    if start not in nodes:
        return {
            "ok": False,
            "tool": "hugin_neighbors",
            "mode": "sync",
            "summary": f"Hugin node not found: {start}",
            "error": {"code": "not_found", "message": start},
        }

    max_depth = max(1, min(_coerce_int(depth, 1), 3))
    cap = max(1, min(_coerce_int(limit, 50), 500))
    relation = relation_type.strip().lower()
    frontier = {start}
    visited = {start}
    matched_edges: list[dict[str, Any]] = []
    for _ in range(max_depth):
        next_frontier: set[str] = set()
        for edge in bundle.get("edges", []):
            if relation and str(edge.get("type", "")).lower() != relation:
                continue
            source, target = str(edge.get("source", "")), str(edge.get("target", ""))
            if source in frontier and target not in visited:
                next_frontier.add(target)
                matched_edges.append(edge)
            elif target in frontier and source not in visited:
                next_frontier.add(source)
                matched_edges.append(edge)
            if len(matched_edges) >= cap:
                break
        visited.update(next_frontier)
        frontier = next_frontier
        if not frontier or len(matched_edges) >= cap:
            break

    neighbor_ids = [item for item in visited if item != start]
    return {
        "ok": True,
        "tool": "hugin_neighbors",
        "mode": "sync",
        "summary": f"hugin: {len(neighbor_ids)} related nodes around {start}",
        "data": {
            "root": nodes[start],
            "nodes": [nodes[item] for item in neighbor_ids if item in nodes][:cap],
            "edges": matched_edges[:cap],
            "depth": max_depth,
            "relation_type": relation_type,
            "cache_age_seconds": age,
            "refresh": refresh,
            "is_stale": is_stale,
        },
    }


@MCP.tool()
@audited_tool("hugin_refresh", "passive", lambda *a, **k: "sync")
def hugin_refresh(run_id: str = "") -> dict[str, Any]:
    """Refresh Hugin's graph and persist it in SQLite/Turso."""
    settings = _get_settings()
    info = _refresh(force=True, primary_url=settings.hugin_url, ttl=settings.hugin_ttl_seconds)
    if info["status"] == "ok":
        return {
            "ok": True,
            "tool": "hugin_refresh",
            "mode": "sync",
            "summary": f"hugin cache refreshed from {info['url']}",
            "data": info,
        }
    return {
        "ok": False,
        "tool": "hugin_refresh",
        "mode": "sync",
        "summary": "hugin refresh failed on all candidate URLs",
        "error": {
            "code": "hugin_refresh_failed",
            "message": (info.get("last_error") or {}).get("message", "no candidate URL succeeded"),
            "attempts": info.get("attempts", []),
        },
    }
