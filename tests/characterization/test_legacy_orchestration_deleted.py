"""Verify legacy orchestration files were deleted."""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
MUST_NOT_EXIST = [
    "munin/subagents/ldap_agent.py",
    "munin/subagents/tool_forge.py",
    "munin/subagents/graph_forge.py",
    "munin/subagents/process_control.py",
    "munin/production/store_v3_1.py",
]


def test_legacy_files_deleted():
    survivors = [p for p in MUST_NOT_EXIST if (REPO_ROOT / p).exists()]
    if survivors:
        assert False, "Legacy files still exist:\n" + "\n".join(f"  {p}" for p in survivors)


def test_munin_agent_deleted():
    path = REPO_ROOT / "munin/core/munin_agent.py"
    assert not path.exists(), "munin_agent.py must be deleted (supervisor replaced it)"
