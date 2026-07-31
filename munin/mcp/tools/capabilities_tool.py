"""MCP discovery endpoint for Munin's cross-domain capability catalog."""

from __future__ import annotations

from typing import Any

from ..capabilities import capabilities_catalog
from ..main import MCP, audited_tool  # noqa: TID252


def _get_settings() -> Any:
    from ..config import get_settings  # noqa: TID252

    return get_settings()


@MCP.tool()
@audited_tool("munin_capabilities", "passive", lambda *a, **k: "sync")
def munin_capabilities(include_generated_tool_context: bool = False, run_id: str = "") -> dict[str, Any]:
    """Discover native capability profiles and safe non-secret defaults for forged tools."""
    data = capabilities_catalog(_get_settings(), include_context=bool(include_generated_tool_context))
    return {
        "ok": True,
        "tool": "munin_capabilities",
        "mode": "sync",
        "summary": f"{data['count']} cross-domain capability profiles",
        "data": data,
    }
