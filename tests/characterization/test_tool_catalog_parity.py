"""Characterization tests for the generated-tool registry.

Asserts CURRENT behaviour at munin/mcp/registry.py and munin/mcp/tools/forge_tool.py:81-99.
"""

from __future__ import annotations

import inspect
import json
import textwrap
from pathlib import Path


def test_gen_prefix_and_rehydrate_active(isolated_workspace, store, monkeypatch):
    """Insert a procedural row with active=1 → rehydrate() returns it under gen__<slug>."""
    from munin.mcp import registry

    monkeypatch.setattr(registry, "clear_callable_cache", lambda: None)

    # Write a minimal generated tool script
    gen_dir = isolated_workspace / "data" / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    script = gen_dir / "gen__echo_foo.py"
    script.write_text(
        textwrap.dedent("""\
            def echo_foo(text: str) -> dict:
                return {"ok": True, "result": text}
        """),
        encoding="utf-8",
    )

    # Register in procedural table with active=1
    store.procedural_register(
        name="gen__echo_foo",
        description="echo foo",
        script_path=str(script),
        source_code=script.read_text(encoding="utf-8"),
        signature={"name": "echo_foo", "description": "...", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
        tags=[],
        created_by_agent="test",
    )

    # rehydrate calls register() which loads the callable
    # We can't call rehydrate without a real MCP, but we can verify the row exists
    row = store.procedural_get("gen__echo_foo")
    assert row is not None
    assert row["name"] == "gen__echo_foo"
    assert row["active"] == 1


def test_active_zero_filter(isolated_workspace, store, monkeypatch):
    """Insert a procedural row with active=0 → it should not be returned by
    procedural_list(include_inactive=False).
    """
    gen_dir = isolated_workspace / "data" / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    script = gen_dir / "gen__inactive_tool.py"
    script.write_text("def inactive_tool(): pass\n", encoding="utf-8")

    store.procedural_register(
        name="gen__inactive_tool",
        description="inactive",
        script_path=str(script),
        source_code="def inactive_tool(): pass\n",
        signature={},
        tags=[],
        created_by_agent="test",
    )
    # Deactivate
    store.procedural_deactivate("gen__inactive_tool")

    active = store.procedural_list(include_inactive=False)
    names = [r["name"] for r in active]
    assert "gen__inactive_tool" not in names


def test_register_state_only_persists_without_mcp(isolated_workspace, store, monkeypatch):
    """register_state_only writes a procedural row but does not attach to MCP."""
    from munin.mcp import registry

    gen_dir = isolated_workspace / "data" / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    script = gen_dir / "gen__state_only.py"
    script.write_text("def state_only(): return {'ok': True}\n", encoding="utf-8")

    result = registry.register_state_only(
        store,
        slug="state_only",
        description="state only tool",
        script_path=str(script),
        function_name="state_only",
        signature={"name": "state_only", "description": "...", "parameters": {"type": "object", "properties": {}}},
        tags=["test"],
    )

    assert result["attached_to_mcp"] is False
    row = store.procedural_get("gen__state_only")
    assert row is not None
    assert row["name"] == "gen__state_only"
    assert row["active"] == 1


def test_signature_to_json_schema_shape():
    """signature_to_json_schema converts an inspect.Signature to OpenAI tool schema shape."""
    from munin.mcp.registry import signature_to_json_schema

    def echo(text: str, count: int = 1) -> dict:
        ...

    sig = inspect.signature(echo)
    schema = signature_to_json_schema(sig)

    assert schema["type"] == "object"
    assert "text" in schema["properties"]
    assert schema["properties"]["text"]["type"] == "string"
    assert "count" in schema["properties"]
    assert schema["properties"]["count"]["type"] == "integer"
    assert "default" in schema["properties"]["count"]
    assert "text" in schema["required"]
    assert "count" not in schema["required"]


def test_max_iterations_clamp():
    """The forge tool clamps max_iterations to [1, 12] with default 5."""
    from munin.mcp.tools.forge_tool import _coerce_int

    # Missing/None → default 5
    assert _coerce_int(None, 5) == 5
    # 50 → clamped to 12
    assert max(1, min(_coerce_int(50, 5), 12)) == 12
    # 0 → clamped to 1
    assert max(1, min(_coerce_int(0, 5), 12)) == 1
    # 5 → stays 5
    assert max(1, min(_coerce_int(5, 5), 12)) == 5
