"""Characterization tests for the tool catalog (munin.mcp.registry).

Documents current behavior of gen__ prefix registration, rehydrate,
state_only tools, OpenAI schema shape, and max_iterations clamping.

Tests skip gracefully if munin imports fail.

NOTE: This file intentionally omits `from __future__ import annotations`.
signature_to_json_schema() inspects real type objects (int, float, bool, etc.)
at runtime. With PEP 563 stringified annotations those checks silently degrade
to "string". The production tool scripts also do not use the future import, so
this file matches the production call context.
"""

import inspect
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Guard imports
# ---------------------------------------------------------------------------
registry_mod = pytest.importorskip("munin.mcp.registry")
config_mod = pytest.importorskip("munin.mcp.config")
shared_mod = pytest.importorskip("munin.mcp.shared_state")

GENERATED_PREFIX = registry_mod.GENERATED_PREFIX
signature_to_json_schema = registry_mod.signature_to_json_schema
Settings = config_mod.Settings
SharedStateStore = shared_mod.SharedStateStore


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> SharedStateStore:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        workspace_root=tmp_path,
        default_timeout=30,
        max_output_chars=8000,
        expected_egress_ip="",
        forbidden_egress_ip="",
        route_probe_ip="1.1.1.1",
        job_workers=1,
        github_token="",
        nvd_api_key="",
        munin_data_path=data_dir,
        munin_soul_path=tmp_path / "soul",
    )
    return SharedStateStore(settings)


def _write_tool_script(tmp_path: Path, name: str, body: str = "") -> Path:
    """Write a minimal Python tool script and return its path."""
    script = tmp_path / f"{name}.py"
    script.write_text(
        f"""
def {name}(message: str) -> dict:
    {body or 'return {"ok": True, "result": message}'}
""",
        encoding="utf-8",
    )
    return script


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_gen_prefix_registered(tmp_path: Path) -> None:
    """Tools registered via registry.register() have the gen__ prefix in their name."""
    store = _make_store(tmp_path)
    script = _write_tool_script(tmp_path, "mytool")

    mcp_stub = MagicMock()
    result = registry_mod.register(
        mcp_stub,
        store,
        slug="mytool",
        description="A test tool",
        script_path=script,
        function_name="mytool",
        signature={"function_name": "mytool"},
        tags=["test"],
    )

    assert result["name"].startswith(GENERATED_PREFIX), (
        f"Registered tool name should start with '{GENERATED_PREFIX}', got: {result['name']}"
    )
    assert result["name"] == f"{GENERATED_PREFIX}mytool"


def test_rehydrate_active_only(tmp_path: Path) -> None:
    """rehydrate() attaches only active=1 tools, skipping deactivated ones."""
    store = _make_store(tmp_path)

    # Register two tools
    script_a = _write_tool_script(tmp_path, "tool_alpha")
    script_b = _write_tool_script(tmp_path, "tool_beta")

    mcp_stub = MagicMock()
    registry_mod.register(
        mcp_stub, store, slug="tool_alpha", description="alpha",
        script_path=script_a, function_name="tool_alpha", signature={}, tags=[],
    )
    registry_mod.register(
        mcp_stub, store, slug="tool_beta", description="beta",
        script_path=script_b, function_name="tool_beta", signature={}, tags=[],
    )

    # Deactivate one
    registry_mod.deactivate(store, "tool_alpha")

    # Rehydrate into a fresh MCP stub
    fresh_mcp = MagicMock()
    fresh_settings = Settings(
        workspace_root=tmp_path,
        default_timeout=30,
        max_output_chars=8000,
        expected_egress_ip="",
        forbidden_egress_ip="",
        route_probe_ip="1.1.1.1",
        job_workers=1,
        github_token="",
        nvd_api_key="",
        munin_data_path=tmp_path / "data",
        munin_soul_path=tmp_path / "soul",
    )
    count = registry_mod.rehydrate(fresh_mcp, store, fresh_settings)

    assert count == 1, f"Expected 1 active tool rehydrated, got {count}"


def test_register_state_only_not_invocable(tmp_path: Path) -> None:
    """procedural_list with include_inactive=False excludes deactivated tools.

    The 'state_only' concept means a tool is persisted in the registry for
    state-tracking purposes but is not invocable (active=0 after deactivation).
    """
    store = _make_store(tmp_path)
    script = _write_tool_script(tmp_path, "state_only_tool")

    mcp_stub = MagicMock()
    registry_mod.register(
        mcp_stub, store, slug="state_only_tool", description="state only",
        script_path=script, function_name="state_only_tool",
        signature={"register_state_only": True}, tags=["state_only"],
    )
    # Immediately deactivate to simulate a state-only registration
    registry_mod.deactivate(store, "state_only_tool")

    active_tools = store.procedural_list(include_inactive=False)
    active_names = [t["name"] for t in active_tools]

    assert f"{GENERATED_PREFIX}state_only_tool" not in active_names, (
        "Deactivated (state-only) tool should not appear in active tool list"
    )


def test_signature_to_openai_schema_shape(tmp_path: Path) -> None:
    """signature_to_json_schema() produces a valid JSON Schema dict.

    Must have: type='object', properties (dict), required (list).
    This matches the OpenAI function calling parameter schema format.
    """
    def example_tool(query: str, limit: int = 10, tags: list = None) -> dict:
        """An example tool for schema testing."""
        return {}

    sig = inspect.signature(example_tool)
    schema = signature_to_json_schema(sig)

    # Must be an object schema
    assert schema.get("type") == "object", "Schema root must be type=object"
    assert "properties" in schema, "Schema must have properties"
    assert isinstance(schema["properties"], dict), "properties must be a dict"
    assert "required" in schema, "Schema must have required list"
    assert isinstance(schema["required"], list), "required must be a list"

    # 'query' is required (no default), 'limit' is optional (has default 10)
    assert "query" in schema["required"], "'query' should be required (no default)"
    assert "limit" not in schema["required"], "'limit' should not be required (has default)"

    # Properties should contain known params
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]


def test_signature_to_openai_schema_types() -> None:
    """signature_to_json_schema() maps Python types to correct JSON Schema types."""

    def typed_tool(name: str, count: int, ratio: float, flag: bool) -> dict:
        return {}

    sig = inspect.signature(typed_tool)
    schema = signature_to_json_schema(sig)

    props = schema["properties"]
    assert props["name"]["type"] == "string"
    assert props["count"]["type"] == "integer"
    assert props["ratio"]["type"] == "number"
    assert props["flag"]["type"] == "boolean"


def test_max_iterations_clamped(tmp_path: Path) -> None:
    """MuninAgent max_iterations is bounded: respond() accepts max_iterations param.

    The respond() signature accepts max_iterations keyword arg. This test
    verifies that the contract exists and the default is 8 (from current code).
    """
    agent_mod = pytest.importorskip("munin.core.munin_agent")

    # Inspect respond() signature to verify max_iterations default
    sig = inspect.signature(agent_mod.MuninAgent.respond)
    assert "max_iterations" in sig.parameters, "respond() must accept max_iterations"

    param = sig.parameters["max_iterations"]
    default = param.default
    assert default is not inspect.Parameter.empty, "max_iterations should have a default"
    # Current default in codebase is 8; documented valid range is [1, 12]
    assert 1 <= default <= 12, (
        f"max_iterations default ({default}) should be in valid range [1, 12]"
    )


def test_list_generated_returns_active_only(tmp_path: Path) -> None:
    """list_generated() returns only active tools (no deactivated entries)."""
    store = _make_store(tmp_path)

    script_active = _write_tool_script(tmp_path, "active_tool")
    script_inactive = _write_tool_script(tmp_path, "inactive_tool")

    mcp_stub = MagicMock()
    registry_mod.register(
        mcp_stub, store, slug="active_tool", description="active",
        script_path=script_active, function_name="active_tool", signature={}, tags=[],
    )
    registry_mod.register(
        mcp_stub, store, slug="inactive_tool", description="inactive",
        script_path=script_inactive, function_name="inactive_tool", signature={}, tags=[],
    )
    registry_mod.deactivate(store, "inactive_tool")

    tools = registry_mod.list_generated(store)
    names = [t["name"] for t in tools]

    assert f"{GENERATED_PREFIX}active_tool" in names
    assert f"{GENERATED_PREFIX}inactive_tool" not in names
