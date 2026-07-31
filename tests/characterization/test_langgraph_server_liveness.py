"""LangGraph server liveness — CI-gated on MUNIN_LANGGRAPH_TESTS=1."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MUNIN_LANGGRAPH_TESTS") != "1",
    reason="Set MUNIN_LANGGRAPH_TESTS=1 to run LangGraph server tests"
)


def test_langgraph_health_endpoint():
    """Ping /health after server start."""
    import urllib.request
    url = os.environ.get("MUNIN_LANGGRAPH_URL", "http://127.0.0.1:8123")
    with urllib.request.urlopen(f"{url}/health", timeout=5) as resp:
        assert resp.status == 200
