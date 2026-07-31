"""MCP tools for graph-aware retrieval over the persisted Hugin snapshot."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...rag import hugin_rag


def hugin_rag_search(query: str, limit: int = 8, run_id: str = "") -> dict[str, Any]:
    """Retrieve scored Hugin evidence and nearby graph nodes for a question."""
    data = hugin_rag.search(query, limit=limit)
    return {"ok": bool(data.get("ok")), "tool": "hugin_rag_search", "mode": "sync", "summary": f"{len(data.get('matches', []))} Hugin evidence matches", "data": data, "error": None if data.get("ok") else {"code": "hugin_rag_unavailable", "message": str(data.get("error", "unknown"))}}


def hugin_plan_for(goal: str, limit: int = 6, run_id: str = "") -> dict[str, Any]:
    """Build a scoped, evidence-only candidate plan from Hugin; it does not execute tools."""
    data = hugin_rag.plan_for(goal, limit=limit)
    return {"ok": bool(data.get("ok")), "tool": "hugin_plan_for", "mode": "sync", "summary": f"{len(data.get('steps', []))} evidence-backed plan candidates", "data": data, "error": None if data.get("ok") else {"code": "hugin_plan_unavailable", "message": str(data.get("error", "unknown"))}}


def hugin_node_detail(node_id: str, run_id: str = "") -> dict[str, Any]:
    """Inspect one Hugin node and its linked evidence."""
    data = hugin_rag.node_detail(node_id)
    if data is None:
        return {"ok": False, "tool": "hugin_node_detail", "mode": "sync", "summary": "Hugin node not found", "error": {"code": "not_found", "message": node_id}}
    return {"ok": True, "tool": "hugin_node_detail", "mode": "sync", "summary": f"Hugin node {node_id}", "data": data}


def register(mcp: FastMCP) -> None:
    mcp.tool()(hugin_rag_search)
    mcp.tool()(hugin_plan_for)
    mcp.tool()(hugin_node_detail)
