"""AsyncSubAgent pressure tests (CI-gated)."""
import os
import pytest

def test_async_subagent_raises_without_url(monkeypatch):
    monkeypatch.delenv("MUNIN_LANGGRAPH_URL", raising=False)
    pytest.importorskip("munin.core.autonomy.subagent_factory")
    from munin.core.autonomy.spec import SubagentSpec
    from munin.core.autonomy.subagent_factory import SubagentFactory
    factory = SubagentFactory(tools=[])
    spec = SubagentSpec(name="async_test", purpose="test", runtime_type="async_langgraph")
    with pytest.raises(NotImplementedError):
        factory.create_subagent(spec)

@pytest.mark.skipif(os.environ.get("MUNIN_LANGGRAPH_TESTS") != "1", reason="Requires LangGraph server")
def test_ten_concurrent_stubs():
    from munin.core.autonomy.spec import SubagentSpec
    from munin.core.autonomy.subagent_factory import SubagentFactory
    factory = SubagentFactory(tools=[])
    specs = [SubagentSpec(name=f"w{i}", purpose="test", runtime_type="async_langgraph") for i in range(10)]
    agents = [factory.create_subagent(s) for s in specs]
    assert len(agents) == 10
