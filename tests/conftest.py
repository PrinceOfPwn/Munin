"""Shared pytest fixtures for Munin tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Munin at a throwaway workspace so no test pollutes ~/munin/data/."""
    monkeypatch.setenv("OFFX_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("MUNIN_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("MUNIN_SOUL_PATH", str(tmp_path / "soul"))
    # Neutralize network egress + strict opsec for tests.
    monkeypatch.setenv("PREFLIGHT_POLICY", "off")
    # Keep LLM settings absent — tests that hit LLMClient must mock.
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "soul").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def store(isolated_workspace: Path):
    from munin.mcp.config import get_settings
    from munin.mcp.shared_state import SharedStateStore

    return SharedStateStore(get_settings())
