"""Characterization tests for OperatorGuidanceMiddleware."""
import pytest
from unittest.mock import AsyncMock, MagicMock

pytest.importorskip("munin.core.middleware")

from munin.core.middleware.operator_guidance import OperatorGuidanceMiddleware


@pytest.mark.asyncio
async def test_no_guidance_passthrough():
    """With empty guidance queue, state passes through unchanged."""
    store = MagicMock()
    store.drain_guidance = AsyncMock(return_value=[])
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=store)

    state = {"messages": []}
    next_fn = AsyncMock(return_value={"messages": [], "done": True})

    result = await middleware(state, next_fn)
    next_fn.assert_called_once_with(state)


@pytest.mark.asyncio
async def test_guidance_injected_as_human_message():
    """Pending guidance is injected as HumanMessage before next_fn."""
    store = MagicMock()
    store.drain_guidance = AsyncMock(return_value=[{"text": "check port 80"}])
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=store)

    captured_state = {}

    async def capture_next(s):
        captured_state.update(s)
        return s

    state = {"messages": []}
    await middleware(state, capture_next)

    messages = captured_state.get("messages", [])
    assert len(messages) == 1
    assert "check port 80" in messages[0].content


@pytest.mark.asyncio
async def test_multiple_guidance_injected_in_order():
    """Multiple guidance items injected in FIFO order."""
    store = MagicMock()
    store.drain_guidance = AsyncMock(return_value=[
        {"text": "first guidance"},
        {"text": "second guidance"},
    ])
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=store)

    captured = {}

    async def capture(s):
        captured.update(s)
        return s

    await middleware({"messages": []}, capture)

    messages = captured["messages"]
    assert len(messages) == 2
    assert "first guidance" in messages[0].content
    assert "second guidance" in messages[1].content
