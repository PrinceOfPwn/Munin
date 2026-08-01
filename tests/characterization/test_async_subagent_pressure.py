"""Unsupported remote runtime names must not be advertised as local agents."""
import pytest

def test_async_subagent_runtime_is_rejected_by_schema():
    pytest.importorskip("munin.core.autonomy.subagent_factory")
    from munin.core.autonomy.spec import SubagentSpec
    with pytest.raises(Exception):
        SubagentSpec(name="async_test", purpose="test", runtime_type="async_langgraph")
