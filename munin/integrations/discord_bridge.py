"""An allowlisted Discord control plane for Munin.

Discord is an operator interface, never an inter-agent backchannel.  Inbound
messages require both the configured channel and an explicit Discord user-ID
allowlist; no credentials or hidden model reasoning are sent to Discord.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from .discord_config import DiscordConfig, get_discord_config

logger = logging.getLogger("munin.discord")
_BRIDGE: "DiscordBridge | None" = None
_LOCK = threading.Lock()


class DiscordBridge:
    def __init__(self, config: DiscordConfig, handler: Callable[[int, str, str, int], None] | None = None) -> None:
        self.config = config
        self.handler = handler
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> bool:
        if not self.config.outbound_enabled:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="munin-discord", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=8)
        return self._ready.is_set()

    def status(self) -> dict[str, Any]:
        return {
            "outbound_enabled": self.config.outbound_enabled,
            "inbound_enabled": self.config.inbound_enabled,
            "channel_id": self.config.channel_id,
            "guild_id": self.config.guild_id,
            "allowed_user_count": len(self.config.allowed_user_ids),
            "connected": self._ready.is_set() and bool(self._thread and self._thread.is_alive()),
        }

    def send(self, content: str, *, channel_id: int | None = None) -> bool:
        if not self.config.outbound_enabled or not content.strip() or self._loop is None or self._client is None:
            return False
        try:
            asyncio.run_coroutine_threadsafe(self._send(channel_id or self.config.channel_id, content), self._loop)
            return True
        except RuntimeError:
            return False

    def _run(self) -> None:
        try:
            import discord  # type: ignore[import-not-found]
        except ImportError:
            logger.error("Discord bridge requires discord.py; install the optional project dependency")
            return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready() -> None:
            self._ready.set()
            logger.info("Discord bridge connected as %s", client.user)

        @client.event
        async def on_message(message: Any) -> None:
            if getattr(message.author, "bot", True):
                return
            root_channel_id = getattr(message.channel, "parent_id", None) or message.channel.id
            if root_channel_id != self.config.channel_id:
                return
            if self.config.guild_id and getattr(getattr(message, "guild", None), "id", 0) != self.config.guild_id:
                return
            if not self.config.inbound_enabled or message.author.id not in self.config.allowed_user_ids:
                logger.warning("Ignored Discord message from unapproved user id=%s", message.author.id)
                return
            raw = (message.content or "").strip()
            prefix = self.config.prefix
            if not raw.lower().startswith(prefix):
                return
            prompt = raw[len(prefix):].lstrip(" ,:;\t")
            if not prompt or self.handler is None:
                return
            self.send("Munin: tarea recibida; responderé en este canal.", channel_id=message.channel.id)
            threading.Thread(
                target=self.handler,
                args=(message.author.id, str(message.author), prompt, message.channel.id),
                name="munin-discord-task",
                daemon=True,
            ).start()

        try:
            loop.run_until_complete(client.start(self.config.token))
        except Exception:
            logger.exception("Discord bridge stopped unexpectedly")
        finally:
            self._ready.clear()
            try:
                loop.run_until_complete(client.close())
            except Exception:
                pass
            loop.close()

    async def _send(self, channel_id: int, content: str) -> None:
        try:
            channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(channel_id)
            for chunk in _chunks(content, 1900):
                await channel.send(chunk)
        except Exception:
            logger.exception("Discord send failed")


def get_bridge(handler: Callable[[int, str, str, int], None] | None = None) -> DiscordBridge | None:
    global _BRIDGE
    with _LOCK:
        if _BRIDGE is None:
            config = get_discord_config()
            if not config.outbound_enabled:
                return None
            _BRIDGE = DiscordBridge(config, handler)
            _BRIDGE.start()
        elif handler is not None:
            _BRIDGE.handler = handler
        return _BRIDGE


def post_to_discord(content: str, *, channel_id: int | None = None) -> bool:
    bridge = get_bridge()
    return bool(bridge and bridge.send(content, channel_id=channel_id))


def _chunks(text: str, max_len: int) -> list[str]:
    return [text[index:index + max_len] for index in range(0, len(text), max_len)] or ["(empty)"]
