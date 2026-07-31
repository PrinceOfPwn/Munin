"""Each of the 5 runtime types produces the right runnable."""
import pytest

pytest.importorskip("munin.core.autonomy.subagent_factory")

from munin.core.autonomy.spec import SubagentSpec
from munin.core.autonomy.subagent_factory import SubagentFactory


def _fake_model():
    """Offline chat model — no provider credentials needed in CI."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    return GenericFakeChatModel(messages=iter(["ok"]))


def make_spec(**kwargs) -> SubagentSpec:
    defaults = {"name": "test_agent", "purpose": "testing"}
    defaults.update(kwargs)
    return SubagentSpec(**defaults)


def test_persisted_subagent_dict_route():
    factory = SubagentFactory(tools=[], model=_fake_model())
    result = factory.create_subagent(make_spec(runtime_type="persisted_subagent_dict"))
    assert isinstance(result, dict)
    assert result["name"] == "test_agent"
    assert result["description"] == "testing"


def test_deep_agent_route_requires_deepagents():
    pytest.importorskip("deepagents")
    factory = SubagentFactory(tools=[], model=_fake_model())
    result = factory.create_subagent(make_spec(runtime_type="deep_agent"))
    assert result is not None
    assert hasattr(result, "ainvoke")


def test_compiled_langgraph_route():
    factory = SubagentFactory(tools=[], model=_fake_model())
    result = factory.create_subagent(make_spec(runtime_type="compiled_langgraph"))
    assert hasattr(result, "invoke") or hasattr(result, "astream")


@pytest.mark.asyncio
async def test_compiled_langgraph_invocable_offline():
    """The compiled route produces a REAL runnable, not a stub node."""
    factory = SubagentFactory(tools=[], model=_fake_model())
    agent = factory.create_subagent(make_spec(runtime_type="compiled_langgraph"))
    result = await factory.invoke_subagent(agent, "hello")
    assert result["content"] == "ok"


def test_async_langgraph_raises_without_url(monkeypatch):
    monkeypatch.delenv("MUNIN_LANGGRAPH_URL", raising=False)
    factory = SubagentFactory(tools=[], model=_fake_model())
    with pytest.raises(NotImplementedError):
        factory.create_subagent(make_spec(runtime_type="async_langgraph"))


def test_unknown_runtime_raises():
    factory = SubagentFactory(tools=[], model=_fake_model())
    spec = make_spec()
    spec.runtime_type = "unknown_runtime"  # type: ignore
    with pytest.raises(ValueError, match="Unknown runtime_type"):
        factory.create_subagent(spec)
