# tags: [coordination, core, discord.py, DiscordPublisher, outbound, send_discord_message, run-channel-map, publish, agent-messaging, adapter-bridge]
"""Outbound Discord publishing bridge for the in-process adapter.

The production Discord adapter runs inside the ASGI server event loop.
The MCP tool surface (``munin.mcp.tools.discord_tool``) is the agent's
own outbound channel: when the agent calls ``send_discord_message`` it
should land in the *same* channel the operator is talking to — not a
separate legacy bridge channel.

This module owns the single in-process mapping from ``run_id`` to the
Discord channel that run is being streamed into.  The adapter registers
the mapping when a run starts and clears it when the run finishes; the
MCP tool resolves the channel through the publisher and schedules the
send on the adapter's event loop.

Fallback: when the adapter is not running (token unset) the publisher is
inert and callers fall back to the legacy ``integrations.discord_bridge``
outbound path (``post_to_discord``), preserving existing behavior.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class DiscordPublisher:
    """Thread-safe registry mapping run ids to Discord channels.

    Both the ASGI adapter task and MCP tool handlers may live on the same
    loop or different threads; every public method is safe to call from
    either.  The adapter owns ``attach``/``detach``/``map_run``/``unmap_run``;
    the tool uses ``channel_id_for_run`` and ``publish``.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._default_channel_id: str | None = None
        self._run_channels: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # -- lifecycle (adapter side) --------------------------------------------

    def attach(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        client: Any,
        default_channel_id: str | None = None,
    ) -> None:
        """Bind the publisher to a live discord client (adapter startup)."""
        self._loop = loop
        self._client = client
        self._default_channel_id = default_channel_id or None
        log.info(
            "discord publisher attached (default_channel_id=%s)",
            self._default_channel_id,
        )

    def detach(self) -> None:
        """Unbind the client (adapter shutdown)."""
        self._loop = None
        self._client = None
        self._run_channels.clear()

    def map_run(self, *, run_id: str, channel_id: str) -> None:
        """Remember which channel a run is being streamed into."""
        self._run_channels[run_id] = channel_id

    def unmap_run(self, *, run_id: str) -> None:
        self._run_channels.pop(run_id, None)

    # -- resolution (tool side) ----------------------------------------------

    @property
    def attached(self) -> bool:
        return self._client is not None and self._loop is not None

    def channel_id_for_run(self, run_id: str) -> str | None:
        """Resolve the channel for a run: explicit mapping, then default."""
        if run_id:
            channel_id = self._run_channels.get(run_id)
            if channel_id:
                return channel_id
        return self._default_channel_id

    # -- publish -------------------------------------------------------------

    async def publish(self, *, run_id: str, content: str) -> bool:
        """Send ``content`` to the run's channel on the adapter loop.

        Returns ``False`` when no client/channel is available (caller falls
        back to the legacy bridge).  Chunks long content at the Discord cap.
        """
        client = self._client
        loop = self._loop
        if client is None or loop is None:
            return False
        channel_id = self.channel_id_for_run(run_id)
        if not channel_id:
            log.warning("discord publish: no channel resolved for run %r", run_id)
            return False

        async def _send() -> bool:
            channel = client.get_channel(int(channel_id))
            if channel is None:
                try:
                    channel = await client.fetch_channel(int(channel_id))
                except Exception as exc:  # noqa: BLE001
                    log.warning("discord publish: fetch_channel failed: %s", exc)
                    return False
            for chunk in _chunk_content(content):
                await channel.send(chunk)
            return True

        try:
            same_loop = loop is asyncio.get_running_loop()
        except RuntimeError:
            same_loop = False
        if loop.is_running() and same_loop:
            return await _send()
        future = asyncio.run_coroutine_threadsafe(_send(), loop)
        try:
            return await asyncio.wrap_future(future)
        except Exception as exc:  # noqa: BLE001
            log.warning("discord publish: send failed: %s", exc)
            return False


def _chunk_content(text: str, *, size: int = 1900) -> list[str]:
    text = text or ""
    if not text:
        return ["(empty)"]
    return [text[i : i + size] for i in range(0, len(text), size)]


# Module-level singleton: the adapter attaches once, the MCP tool reads it.
PUBLISHER = DiscordPublisher()
