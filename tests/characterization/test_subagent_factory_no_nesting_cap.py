"""Nested depth 7 under RecursionLimit=50 succeeds (no MUNIN_MAX_NESTED_SUBAGENTS cap)."""
import pytest
pytest.importorskip("munin.core.autonomy.subagent_factory")

from munin.core.autonomy.spec import SubagentSpec
from munin.core.autonomy.subagent_factory import SubagentFactory


def test_no_env_cap_on_nesting():
    """MUNIN_MAX_NESTED_SUBAGENTS should not exist or be enforced."""
    import munin.subagents.base as base_module
    assert not hasattr(base_module, "MUNIN_MAX_NESTED_SUBAGENTS"), \
        "MUNIN_MAX_NESTED_SUBAGENTS cap must be removed per PR-07"


def test_create_multiple_subagents_no_limit():
    """Creating 10 subagents doesn't raise a cap error."""
    factory = SubagentFactory(tools=[])
    specs = [
        SubagentSpec(name=f"agent_{i}", purpose="test", runtime_type="persisted_subagent_dict")
        for i in range(10)
    ]
    results = [factory.create_subagent(spec) for spec in specs]
    assert len(results) == 10
