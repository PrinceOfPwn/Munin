"""
Autonomy Kernel — Tool Factory.

Allows the supervisor (and authorized subagents) to create new tools
at runtime, invoke them within the same run, and persist them for
future runs.
"""
from __future__ import annotations
import uuid
import time
import json
from typing import Any, Callable


class ToolFactory:
    """
    Runtime tool creation and invocation.

    Tools created here are:
    1. Immediately callable in the current run
    2. Persisted to the procedural store with full provenance
    3. Rehydratable across runs via registry.rehydrate()
    """

    def __init__(self, registry: Any = None, store: Any = None, run_id: str = "", agent_id: str = "supervisor",
                 available_tools: list[Any] | None = None):
        self._registry = registry
        self._store = store
        self._run_id = run_id
        self._agent_id = agent_id
        self._live_tools: dict[str, Callable] = {}
        # Backward-compat: tools passed directly (used by stub callers)
        self._available = available_tools or []

    # ------------------------------------------------------------------
    # Stub-compat build() — kept for code that uses the old interface
    # ------------------------------------------------------------------

    def build(self, tool_names: list[str]) -> list[Any]:
        """Return tools matching the given names (backward-compat stub interface)."""
        name_set = set(tool_names)
        return [t for t in self._available if getattr(t, "name", None) in name_set]

    # ------------------------------------------------------------------
    # PR-06: full runtime tool creation interface
    # ------------------------------------------------------------------

    def create_tool(
        self,
        spec: str,
        *,
        name: str | None = None,
        description: str = "",
        parameters: dict | None = None,
        source: str = "",
        deps: list[str] | None = None,
    ) -> str:
        """
        Create a new gen__ tool from a spec string.

        The spec is a natural-language or code description. The tool is
        registered with gen__ prefix and made immediately invocable.

        Returns the tool name (gen__<slug>).
        """
        if name is None:
            slug = spec[:30].lower().replace(" ", "_").replace("-", "_")
            # strip non-alphanumeric except _
            import re
            slug = re.sub(r"[^a-z0-9_]", "", slug)
            name = f"gen__{slug}"
        elif not name.startswith("gen__"):
            name = f"gen__{name}"

        if parameters is None:
            parameters = {
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "Input to the tool"}
                },
                "required": ["input"],
            }

        if not description:
            description = f"Dynamically created tool: {spec}"

        # Create a sandboxed handler from the spec
        handler = self._make_handler(spec, name)
        self._live_tools[name] = handler

        # Register in registry
        if self._registry is not None:
            self._registry.register(
                name=name,
                description=description,
                handler=handler,
                signature=parameters,
            )

            # Record provenance
            self._registry.record_provenance(
                name=name,
                creator_agent=self._agent_id,
                parent_run=self._run_id,
                spec=spec,
                source=source,
                deps=deps or [],
                validation_results=[],
            )

        return name

    def invoke_registered_tool(self, name: str, kwargs: dict) -> Any:
        """
        Invoke a tool by name (gen__ or any registered tool).

        Checks live_tools first (same-run created tools), then falls back
        to the registry's persistent store.
        """
        if name in self._live_tools:
            handler = self._live_tools[name]
        else:
            # Try to get from registry
            handler = self._registry.get_handler(name) if self._registry is not None else None
            if handler is None:
                raise KeyError(f"Tool {name!r} not found in live tools or registry")

        import inspect
        if inspect.iscoroutinefunction(handler):
            import asyncio
            return asyncio.run(handler(**kwargs))
        return handler(**kwargs)

    def list_registered_tools(self, *, gen_only: bool = False) -> list[dict]:
        """List all registered tools, optionally filtering to gen__ only."""
        if self._registry is None:
            return []
        tools = self._registry.rehydrate()
        if gen_only:
            tools = [t for t in tools if t["name"].startswith("gen__")]
        return tools

    def inspect_registered_tool(self, name: str) -> dict:
        """Return full metadata including provenance for a tool."""
        if self._registry is None:
            raise KeyError(f"Tool {name!r} not found")
        tools = self._registry.rehydrate()
        for tool in tools:
            if tool["name"] == name:
                return tool
        raise KeyError(f"Tool {name!r} not found")

    def _make_handler(self, spec: str, name: str) -> Callable:
        """
        Create a sandboxed handler from a spec string.

        For now, creates a stub that echoes the spec. In production,
        this would integrate with the sandbox executor to run generated code.
        """
        def handler(input: str = "", **kwargs: Any) -> str:
            return f"[{name}] Spec: {spec!r} | Input: {input!r} | kwargs: {kwargs}"

        handler.__name__ = name
        handler.__doc__ = spec
        return handler
