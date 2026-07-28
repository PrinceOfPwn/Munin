"""Graph-aware, local retrieval over Munin's persisted Hugin cache.

The module deliberately builds on ``hugin_tool``'s Turso-backed cache instead
of downloading a second copy.  Retrieval is deterministic and dependency-free,
so a live agent has a useful evidence layer even on lean runner images.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from ..mcp.tools import hugin_tool

_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.:-]{1,}", re.IGNORECASE)


def _tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return {token.lower() for token in _TOKEN.findall(value)}


def _brief(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(node.get("id", "")),
        "label": str(node.get("label") or node.get("name") or node.get("id") or ""),
        "category": str(node.get("category") or node.get("kind") or node.get("type") or "unknown"),
        "tags": node.get("tags", [])[:12] if isinstance(node.get("tags"), list) else node.get("tags", []),
        "source_url": node.get("url") or node.get("source_url") or "",
    }


def _cached_bundle() -> tuple[dict[str, Any] | None, int, bool]:
    return hugin_tool._load_cached(allow_stale=True)


def search(query: str, *, limit: int = 8) -> dict[str, Any]:
    """Rank Hugin entities using label/tag/content overlap with score evidence."""
    bundle, age, stale = _cached_bundle()
    if not bundle:
        return {"ok": False, "error": "Hugin cache is empty; call hugin_refresh first"}
    terms = _tokens(query)
    if not terms:
        return {"ok": False, "error": "query must contain searchable terms"}
    ranked: list[tuple[float, dict[str, Any], list[str]]] = []
    for raw in bundle.get("entities", []):
        if not isinstance(raw, dict):
            continue
        label_terms = _tokens(raw.get("label") or raw.get("name") or raw.get("id"))
        tag_terms = _tokens(raw.get("tags")) | _tokens(raw.get("mitre"))
        body_terms = _tokens(raw.get("content")) | _tokens(raw.get("description")) | _tokens(raw.get("category"))
        label_hit, tag_hit, body_hit = terms & label_terms, terms & tag_terms, terms & body_terms
        score = (4.0 * len(label_hit)) + (2.5 * len(tag_hit)) + len(body_hit)
        if score:
            matched = sorted(label_hit | tag_hit | body_hit)
            ranked.append((score, raw, matched))
    ranked.sort(key=lambda item: (-item[0], str(item[1].get("label", ""))))
    try:
        cap = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        cap = 8
    nodes = {str(item.get("id")): item for item in bundle.get("entities", []) if isinstance(item, dict) and item.get("id")}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in bundle.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        if source and target:
            adjacency[source].append(target)
            adjacency[target].append(source)
    matches: list[dict[str, Any]] = []
    for score, node, matched in ranked[:cap]:
        node_id = str(node.get("id", ""))
        matches.append({
            **_brief(node),
            "score": round(score, 3),
            "matched_terms": matched,
            "neighbors": [_brief(nodes[nid]) for nid in adjacency.get(node_id, [])[:5] if nid in nodes],
        })
    return {"ok": True, "query": query, "matches": matches, "graph_size": len(nodes), "cache_age_seconds": age, "is_stale": stale}


def node_detail(node_id: str) -> dict[str, Any] | None:
    bundle, age, stale = _cached_bundle()
    if not bundle:
        return None
    nodes = {str(item.get("id")): item for item in bundle.get("entities", []) if isinstance(item, dict) and item.get("id")}
    node = nodes.get(node_id)
    if node is None:
        return None
    related: list[dict[str, Any]] = []
    for edge in bundle.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source, target = str(edge.get("source", "")), str(edge.get("target", ""))
        if source == node_id and target in nodes:
            related.append({"relation": edge.get("type", "related"), **_brief(nodes[target])})
        elif target == node_id and source in nodes:
            related.append({"relation": edge.get("type", "related"), **_brief(nodes[source])})
    return {"node": node, "neighbors": related[:25], "cache_age_seconds": age, "is_stale": stale}


def plan_for(goal: str, *, limit: int = 6) -> dict[str, Any]:
    """Return evidence candidates, never executable attack instructions."""
    retrieved = search(goal, limit=max(limit * 2, 8))
    if not retrieved.get("ok"):
        return retrieved
    priority = {"technique": 0, "tactic": 0, "tool": 1, "cve": 2, "mitigation": 3}
    candidates = list(retrieved["matches"])
    candidates.sort(key=lambda item: (priority.get(str(item["category"]).lower(), 4), -float(item["score"])))
    steps = [
        {
            "step": index + 1,
            "evidence_node": item["id"],
            "label": item["label"],
            "category": item["category"],
            "why": f"Hugin lexical evidence score {item['score']}",
            "requires_operator_scope": True,
        }
        for index, item in enumerate(candidates[: max(1, min(limit, 12))])
    ]
    return {"ok": True, "goal": goal, "steps": steps, "retrieval": retrieved}
