# tags: [mcp, mcp-tool, coordination, presence, hitl-approval, DiscordPublisher, send_discord_message, discord_status, get_discord_config, post_to_discord, outbound_notifications, operator_alerts, async_messaging]
"""MCP surface for asynchronous Discord notifications.

The agent's own outbound channel.  ``send_discord_message`` resolves the
target channel through the in-process :class:`DiscordPublisher` (the
channel the operator is currently talking to for that run) and falls
back to the legacy bridge channel when the production adapter is not
running.

Per operator decision, Discord output is deliberately **not** redacted:
this surface is the operator's own window into the operation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ...integrations.discord_bridge import get_bridge, post_to_discord
from ...integrations.discord_config import get_discord_config
from ...production.discord_publisher import PUBLISHER

log = logging.getLogger(__name__)


def send_discord_message(content: str, run_id: str = "") -> dict[str, Any]:
    """Send a message to the operator's Discord channel.

    When the production Discord adapter is attached, the message is sent
    to the channel that is streaming ``run_id`` (or the adapter's default
    channel).  Otherwise it falls back to the legacy outbound bridge
    channel configured via ``MUNIN_DISCORD_CHANNEL_ID``.
    """
    if not content or not str(content).strip():
        return {"ok": False, "tool": "send_discord_message", "mode": "sync", "summary": "empty message", "error": {"code": "empty_content", "message": "content is required"}}

    if PUBLISHER.attached:
        try:
            loop = PUBLISHER._loop  # noqa: SLF001 - publisher owns the loop binding
            if loop is not None and loop.is_running() and loop is asyncio.get_running_loop():
                # Same loop (agent calling inside the supervisor): schedule
                # the send, report queued.
                asyncio.ensure_future(PUBLISHER.publish(run_id=run_id, content=content))
                return {"ok": True, "tool": "send_discord_message", "mode": "sync", "summary": "Discord message queued", "data": {"channel_id": PUBLISHER.channel_id_for_run(run_id), "via": "adapter"}}
            if loop is not None:
                future = asyncio.run_coroutine_threadsafe(
                    PUBLISHER.publish(run_id=run_id, content=content), loop
                )
                sent = future.result(timeout=10)
                if not sent:
                    return {"ok": False, "tool": "send_discord_message", "mode": "sync", "summary": "Discord message not delivered", "error": {"code": "publish_failed", "message": "adapter could not resolve or send to the channel"}}
                return {"ok": True, "tool": "send_discord_message", "mode": "sync", "summary": "Discord message queued", "data": {"channel_id": PUBLISHER.channel_id_for_run(run_id), "via": "adapter"}}
        except Exception as exc:  # noqa: BLE001 - fall through to legacy bridge
            log.warning("send_discord_message: adapter publish failed, falling back to bridge: %s", exc)

    if not get_discord_config().outbound_enabled:
        return {"ok": False, "tool": "send_discord_message", "mode": "sync", "summary": "Discord is not configured", "error": {"code": "discord_not_configured", "message": "Set token and channel ID"}}
    accepted = post_to_discord(content)
    return {"ok": accepted, "tool": "send_discord_message", "mode": "sync", "summary": "Discord message queued" if accepted else "Discord bridge is not connected", "data": (get_bridge() or None).status() if get_bridge() else {}}


def discord_status(run_id: str = "") -> dict[str, Any]:
    """Report Discord configuration without revealing its token or allowed IDs."""
    bridge = get_bridge()
    config = get_discord_config()
    publisher_data = {
        "attached": PUBLISHER.attached,
        "channel_id": PUBLISHER.channel_id_for_run(run_id),
        "run_mapped": bool(run_id and PUBLISHER.channel_id_for_run(run_id)),
    }
    data = bridge.status() if bridge else {"outbound_enabled": config.outbound_enabled, "inbound_enabled": config.inbound_enabled, "connected": False, "allowed_user_count": len(config.allowed_user_ids)}
    data.update(publisher_data)
    return {"ok": True, "tool": "discord_status", "mode": "sync", "summary": "Discord attached" if publisher_data["attached"] else ("Discord configured" if data.get("outbound_enabled") else "Discord disabled"), "data": data}


def register(mcp: FastMCP) -> None:
    mcp.tool()(send_discord_message)
    mcp.tool()(discord_status)
