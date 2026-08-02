# tags: [mcp, mcp-tool, coordination, presence, hitl-approval, discord_bridge, redact_secrets, send_discord_message, discord_status, get_discord_config, post_to_discord, outbound_notifications, operator_alerts, async_messaging, secret_filtering]
"""MCP surface for safe asynchronous Discord notifications."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ...integrations.discord_bridge import get_bridge, post_to_discord
from ...integrations.discord_config import get_discord_config
from ..audit import redact_secrets


def send_discord_message(content: str, run_id: str = "") -> dict[str, Any]:
    """Send a concise operator notification with enforced secret filtering.

    SECURITY: Content is automatically redacted before publication.
    For sensitive operations, route through HITL approval workflow first.
    """
    if not get_discord_config().outbound_enabled:
        return {"ok": False, "tool": "send_discord_message", "mode": "sync", "summary": "Discord is not configured", "error": {"code": "discord_not_configured", "message": "Set token and channel ID"}}
    redacted_content = redact_secrets(content)
    if redacted_content != content:
        import logging
        logging.getLogger(__name__).warning("send_discord_message: secrets redacted from outbound message")
    accepted = post_to_discord(redacted_content)
    return {"ok": accepted, "tool": "send_discord_message", "mode": "sync", "summary": "Discord message queued" if accepted else "Discord bridge is not connected", "data": (get_bridge() or None).status() if get_bridge() else {}}


def discord_status(run_id: str = "") -> dict[str, Any]:
    """Report Discord configuration without revealing its token or allowed IDs."""
    bridge = get_bridge()
    config = get_discord_config()
    data = bridge.status() if bridge else {"outbound_enabled": config.outbound_enabled, "inbound_enabled": config.inbound_enabled, "connected": False, "allowed_user_count": len(config.allowed_user_ids)}
    return {"ok": True, "tool": "discord_status", "mode": "sync", "summary": "Discord configured" if data["outbound_enabled"] else "Discord disabled", "data": data}


def register(mcp: FastMCP) -> None:
    mcp.tool()(send_discord_message)
    mcp.tool()(discord_status)
