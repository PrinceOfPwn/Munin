"""Each of 5 runtime types produces the right runnable."""
import pytest
pytest.importorskip("munin.core.autonomy.subagent_factory")

from munin.core.autonomy.spec import SubagentSpec
from munin.core.autonomy.subagent_factory import SubagentFactory


def make_spec(**kwargs) -> SubagentSpec:
    defaults = {"name": "test_agent", "purpose": "testing"}
    defaults.update(kwargs)
    return SubagentSpec(**defaults)


def test_persisted_subagent_dict_route():
    factory = SubagentFactory(tools=[])
    result = factory.create_subagent(make_spec(runtime_type="persisted_subagent_dict"))
    assert isinstance(result, dict)
    assert result["name"] == "test_agent"


def test_deep_agent_route_requires_deepagents():
    pytest.importorskip("deepagents")
    factory = SubagentFactory(tools=[])
    result = factory.create_subagent(make_spec(runtime_type="deep_agent"))
    assert result is not None


def test_compiled_langgraph_route():
    factory = SubagentFactory(tools=[])
    result = factory.create_subagent(make_spec(runtime_type="compiled_langgraph"))
    assert hasattr(result, "invoke") or hasattr(result, "astream")


def test_async_langgraph_raises_without_url(monkeypatch):
    monkeypatch.delenv("MUNIN_LANGGRAPH_URL", raising=False)
    factory = SubagentFactory(tools=[])
    with pytest.raises(NotImplementedError):
        factory.create_subagent(make_spec(runtime_type="async_langgraph"))


def test_unknown_runtime_raises():
    factory = SubagentFactory(tools=[])
    spec = make_spec()
    spec.runtime_type = "unknown_runtime"  # type: ignore
    with pytest.raises(ValueError, match="Unknown runtime_type"):
        factory.create_subagent(spec)
