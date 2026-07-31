"""Strict, environment-only configuration for the optional Discord bridge."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(raw: str) -> int:
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    channel_id: int
    guild_id: int
    allowed_user_ids: frozenset[int]
    prefix: str
    max_iterations: int

    @property
    def outbound_enabled(self) -> bool:
        return bool(self.token and self.channel_id)

    @property
    def inbound_enabled(self) -> bool:
        return self.outbound_enabled and bool(self.allowed_user_ids)


def get_discord_config() -> DiscordConfig:
    raw_users = os.environ.get("MUNIN_DISCORD_ALLOWED_USER_IDS", "")
    allowed = frozenset(user for user in (_positive_int(item) for item in raw_users.split(",")) if user)
    return DiscordConfig(
        token=os.environ.get("MUNIN_DISCORD_TOKEN", "").strip(),
        channel_id=_positive_int(os.environ.get("MUNIN_DISCORD_CHANNEL_ID", "")),
        guild_id=_positive_int(os.environ.get("MUNIN_DISCORD_GUILD_ID", "")),
        allowed_user_ids=allowed,
        prefix=os.environ.get("MUNIN_DISCORD_PREFIX", "munin").strip().lower() or "munin",
        max_iterations=max(1, min(_positive_int(os.environ.get("MUNIN_DISCORD_MAX_ITERATIONS", "60")) or 60, 60)),
    )
