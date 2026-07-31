"""E2E: SubagentFactory creates a specialist and it's invocable."""
import pytest
pytest.importorskip("munin.core.autonomy.subagent_factory")

from munin.core.autonomy.spec import SubagentSpec
from munin.core.autonomy.subagent_factory import SubagentFactory


def test_persisted_dict_invocable():
    factory = SubagentFactory(tools=[])
    spec = SubagentSpec(name="recon_specialist", purpose="Perform reconnaissance", runtime_type="persisted_subagent_dict")
    result = factory.create_subagent(spec)
    assert result["name"] == "recon_specialist"
    # Deep Agents SubAgent dict shape: description (carried from spec.purpose), not purpose
    assert result["description"] == "Perform reconnaissance"
    assert result["system_prompt"].startswith("You are recon_specialist")
