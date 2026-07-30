"""Tool factory: max_iterations > 12 is honoured (no silent abort at 12)."""
import pytest
pytest.importorskip("munin.mcp.tools.forge_tool")

from munin.mcp.tools.forge_tool import get_max_iterations


def test_max_iterations_above_12_not_clamped():
    assert get_max_iterations(50) == 50
    assert get_max_iterations(100) == 100


def test_max_iterations_floor_at_1():
    assert get_max_iterations(0) == 1
    assert get_max_iterations(-5) == 1


def test_max_iterations_default_range():
    assert get_max_iterations(5) == 5
    assert get_max_iterations(12) == 12
    assert get_max_iterations(13) == 13  # no cap
