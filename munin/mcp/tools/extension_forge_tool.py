"""MCP surface for human-governed self-extension proposals."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...forge.extension_forge import ExtensionForge
from ...mcp.config import get_settings


def _forge() -> ExtensionForge:
    return ExtensionForge(get_settings())


def extension_forge(
    slug: str,
    kind: str,
    rationale: str,
    target_paths: str,
    diff: str,
    tests: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    """Validate and persist a self-extension proposal; it never modifies live code."""
    result = _forge().propose(
        slug=slug,
        kind=kind,
        rationale=rationale,
        target_paths=[part.strip() for part in target_paths.split(",") if part.strip()],
        diff=diff,
        tests=[line.strip() for line in tests.splitlines() if line.strip()],
    )
    return {
        "ok": result.ok,
        "tool": "extension_forge",
        "mode": "sync",
        "summary": result.summary,
        "data": result.to_dict(),
        "error": None if result.ok else {"code": "extension_rejected", "message": result.summary},
    }


def extension_list(run_id: str = "") -> dict[str, Any]:
    """List extension proposals and their review state."""
    manifests = _forge().list()
    return {
        "ok": True,
        "tool": "extension_list",
        "mode": "sync",
        "summary": f"{len(manifests)} self-extension proposals",
        "data": {"count": len(manifests), "extensions": [item.to_dict() for item in manifests]},
    }


def extension_describe(slug: str, run_id: str = "") -> dict[str, Any]:
    """Read one persisted self-extension proposal."""
    manifest = _forge().describe(slug)
    if manifest is None:
        return {"ok": False, "tool": "extension_describe", "mode": "sync", "summary": "proposal not found", "error": {"code": "not_found", "message": slug}}
    return {"ok": True, "tool": "extension_describe", "mode": "sync", "summary": f"{slug}: {manifest.status}", "data": manifest.to_dict()}


def extension_open_pr(slug: str, operator_approved: bool = False, run_id: str = "") -> dict[str, Any]:
    """Open a PR for a validated proposal only after explicit human approval."""
    result = _forge().open_pr(slug, operator_approved=operator_approved)
    return {
        "ok": result.ok,
        "tool": "extension_open_pr",
        "mode": "sync",
        "summary": result.summary,
        "data": result.to_dict(),
        "error": None if result.ok else {"code": "extension_pr_not_opened", "message": result.summary},
    }


def register(mcp: FastMCP) -> None:
    """Attach tools lazily so this module remains usable by the ReAct catalog."""
    mcp.tool()(extension_forge)
    mcp.tool()(extension_list)
    mcp.tool()(extension_describe)
    mcp.tool()(extension_open_pr)
