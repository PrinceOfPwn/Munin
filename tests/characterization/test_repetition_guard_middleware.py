"""Characterization tests for RepetitionGuardMiddleware (LangChain hook API)."""
import pytest

pytest.importorskip("munin.core.middleware")

from langchain_core.messages import AIMessage

from munin.core.middleware.repetition_guard import (
    RepetitionGuardMiddleware,
    RepetitionGuardTripped,
)


def ai_msg(content: str) -> AIMessage:
    return AIMessage(content=content)


@pytest.mark.asyncio
async def test_no_repetition_returns_none():
    guard = RepetitionGuardMiddleware(window_size=4, min_unique=2)
    state = {"messages": [ai_msg("step 1")]}
    assert await guard.aafter_model(state, runtime=None) is None
    assert not guard._nudge_issued


@pytest.mark.asyncio
async def test_nudge_issued_once_on_repetition():
    guard = RepetitionGuardMiddleware(window_size=4, min_unique=3)
    guard._recent = ["same"] * 3  # one short of window; next push fills it

    update = await guard.aafter_model({"messages": [ai_msg("same")]}, runtime=None)
    assert guard._nudge_issued
    assert update is not None
    assert "repeating yourself" in update["messages"][0].content


@pytest.mark.asyncio
async def test_repetition_guard_trips_after_nudge():
    guard = RepetitionGuardMiddleware(window_size=4, min_unique=3)
    guard._nudge_issued = True
    guard._recent = ["same"] * 3

    with pytest.raises(RepetitionGuardTripped):
        await guard.aafter_model({"messages": [ai_msg("same")]}, runtime=None)


@pytest.mark.asyncio
async def test_window_not_full_no_trip():
    guard = RepetitionGuardMiddleware(window_size=6, min_unique=3)
    for _ in range(4):  # below window size
        assert await guard.aafter_model({"messages": [ai_msg("repeat")]}, runtime=None) is None
    assert not guard._nudge_issued
