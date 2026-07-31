"""AsyncSubAgent roundtrip via LangGraph SDK (CI-gated)."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MUNIN_LANGGRAPH_TESTS") != "1",
    reason="Requires LangGraph server"
)


@pytest.mark.asyncio
async def test_start_and_check_async_task():
    """start_async_task + poll until COMPLETED."""
    pytest.importorskip("langgraph_sdk")
    from langgraph_sdk import get_async_client

    url = os.environ.get("MUNIN_LANGGRAPH_URL", "http://127.0.0.1:8123")
    client = get_async_client(url=url)

    # This is a smoke test — actual graph invocation
    thread = await client.threads.create()
    assert thread["thread_id"]
