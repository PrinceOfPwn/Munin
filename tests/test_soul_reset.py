"""SoulManager — snapshot + restore idempotency."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def soul_setup(isolated_workspace):
    from munin.mcp.config import get_settings
    from munin.core.soul import SoulManager

    settings = get_settings()
    soul_root: Path = settings.munin_soul_path
    soul_root.mkdir(parents=True, exist_ok=True)
    (soul_root / "identity.md").write_text("# Identity\nMunin.", encoding="utf-8")
    (soul_root / "principles.md").write_text("# Principles\nBe careful.", encoding="utf-8")
    manager = SoulManager(soul_root, settings.munin_data_path)
    return manager, soul_root


def test_snapshot_then_restore_unchanged(soul_setup):
    manager, root = soul_setup
    manager.snapshot()
    assert manager.snapshot_path.exists()
    # Modify soul.
    (root / "identity.md").write_text("# Identity\nHacked at runtime.", encoding="utf-8")
    assert "Hacked" in (root / "identity.md").read_text()
    # Restore.
    manager.restore()
    assert "Hacked" not in (root / "identity.md").read_text()
    assert "Munin" in (root / "identity.md").read_text()


def test_restore_removes_files_added_after_snapshot(soul_setup):
    manager, root = soul_setup
    manager.snapshot()
    # Runtime drift: agent added a new soul file.
    (root / "sneaky.md").write_text("# sneaky", encoding="utf-8")
    assert (root / "sneaky.md").exists()
    manager.restore()
    assert not (root / "sneaky.md").exists()


def test_pending_edits_are_queued_not_applied(soul_setup, isolated_workspace):
    manager, root = soul_setup
    manager.snapshot()

    # Simulate soul_propose_edit call: proposal goes under data/soul_pending/, not into soul/.
    from munin.mcp.tools import munin_tools  # noqa: WPS433

    result = munin_tools.soul_propose_edit(path="identity.md", new_content="# Identity\nProposed", rationale="test")
    assert result["ok"]
    # soul/identity.md is UNCHANGED.
    assert "Munin" in (root / "identity.md").read_text()
    # pending edit is queued in the manager.
    pending = manager.pending_edits()
    assert len(pending) == 1
    assert pending[0]["target_path"] == "identity.md"
