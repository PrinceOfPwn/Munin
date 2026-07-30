"""Characterization tests for RepetitionGuardMiddleware."""
import pytest
from unittest.mock import AsyncMock

pytest.importorskip("munin.core.middleware")

from munin.core.middleware.repetition_guard import (
    RepetitionGuardMiddleware, RepetitionGuardTripped
)
from langchain_core.messages import AIMessage


def make_state(messages):
    return {"messages": messages}


def ai_msg(content):
    return AIMessage(content=content)


@pytest.mark.asyncio
async def test_no_repetition_passes_through():
    """Non-repeating messages pass through without intervention."""
    guard = RepetitionGuardMiddleware(window_size=4, min_unique=2)
    next_fn = AsyncMock(return_value={"messages": []})

    state = make_state([ai_msg("step 1"), ai_msg("step 2"), ai_msg("step 3")])
    for _ in range(3):
        await guard(state, next_fn)

    assert not guard._nudge_issued


@pytest.mark.asyncio
async def test_nudge_issued_once_on_repetition():
    """A single nudge is injected on first repetition detection."""
    guard = RepetitionGuardMiddleware(window_size=4, min_unique=3)

    captured_states = []

    async def capture(s):
        captured_states.append(s)
        return s

    repeated_msg = ai_msg("I will scan the network")
    state = make_state([repeated_msg] * 6)

    # Force repetition detection
    guard._recent_messages = ["same"] * 6
    guard._iteration_count = 7

    await guard(state, capture)
    assert guard._nudge_issued


@pytest.mark.asyncio
async def test_repetition_guard_trips_after_nudge():
    """RepetitionGuardTripped raised if repetition continues after nudge."""
    guard = RepetitionGuardMiddleware(window_size=4, min_unique=3)
    guard._nudge_issued = True  # Already nudged
    guard._recent_messages = ["same"] * 6
    guard._iteration_count = 10

    next_fn = AsyncMock(return_value={})

    with pytest.raises(RepetitionGuardTripped):
        await guard(make_state([ai_msg("same")] * 6), next_fn)


@pytest.mark.asyncio
async def test_window_size_boundary():
    """Guard does not trip before window_size iterations complete."""
    guard = RepetitionGuardMiddleware(window_size=6, min_unique=3)
    next_fn = AsyncMock(return_value={})

    state = make_state([ai_msg("repeat")])
    # Only 5 iterations — should NOT trip
    for _ in range(5):
        await guard(state, next_fn)

    assert not guard._nudge_issued
