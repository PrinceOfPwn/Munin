"""Characterization tests for OperatorGuidanceMiddleware (LangChain hook API)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

pytest.importorskip("munin.core.middleware")

from munin.core.middleware.operator_guidance import OperatorGuidanceMiddleware


@pytest.mark.asyncio
async def test_no_guidance_returns_none():
    """Empty guidance queue → no state update (framework contract: None)."""
    store = MagicMock()
    store.drain_guidance = AsyncMock(return_value=[])
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=store)

    assert await middleware.abefore_model({"messages": []}, runtime=None) is None


@pytest.mark.asyncio
async def test_guidance_injected_as_human_message():
    store = MagicMock()
    store.drain_guidance = AsyncMock(return_value=[{"text": "check port 80"}])
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=store)

    update = await middleware.abefore_model({"messages": []}, runtime=None)
    assert update is not None
    messages = update["messages"]
    assert len(messages) == 1
    assert "check port 80" in messages[0].content
    assert messages[0].name == "operator"


@pytest.mark.asyncio
async def test_multiple_guidance_injected_in_order():
    store = MagicMock()
    store.drain_guidance = AsyncMock(return_value=[
        {"text": "first guidance"},
        {"text": "second guidance"},
    ])
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=store)

    update = await middleware.abefore_model({"messages": []}, runtime=None)
    messages = update["messages"]
    assert len(messages) == 2
    assert "first guidance" in messages[0].content
    assert "second guidance" in messages[1].content


def test_store_without_drain_method_is_noop():
    """SharedStateStore (no drain_guidance) → middleware silently skips."""
    middleware = OperatorGuidanceMiddleware(run_id="run-1", store=object())
    assert middleware.before_model({"messages": []}, runtime=None) is None
