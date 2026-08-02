# tags: [valravn, recon, intel, osint, core, runtime, orchestrator, Candidate, select_candidates, Tier, provider-selection, fan-out-budget, tier-limits, candidate-ranking, economic-tiering]
"""Provider selection for bounded, cost-aware intelligence fan-out."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .config import Depth, ValravnSettings

Tier = Literal["no_key", "free_key", "scarce"]


@dataclass(frozen=True)
class Candidate:
    name: str
    tier: Tier
    priority: int
    configured: Callable[[], bool]


def select_candidates(
    settings: ValravnSettings,
    candidates: list[Candidate],
    *,
    depth: Depth,
) -> list[str]:
    """Select configured providers while respecting each tier's call budget."""
    selected: list[str] = []
    for tier in ("no_key", "free_key", "scarce"):
        budget = settings.policy.budget(tier, depth)
        if budget <= 0:
            continue
        eligible = sorted(
            (item for item in candidates if item.tier == tier and item.configured()),
            key=lambda item: (-item.priority, item.name),
        )
        selected.extend(item.name for item in eligible[:budget])
    return selected
