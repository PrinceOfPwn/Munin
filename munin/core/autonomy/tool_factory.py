"""
Autonomy Kernel — Tool Factory.

Real runtime tool creation for the Deep Agents supervisor and authorized
subagents (issue #9 §3).  This is NOT a stub: created tools are

1. validated by the same AST guard + in-process sandbox as ``tool_forge``
   (``munin.subagents.sandbox``),
2. persisted to the ``procedural`` table through
   ``registry.register_state_only`` with full provenance, and
3. immediately invocable in the same run via ``invoke_registered_tool`` —
   no supervisor graph recompilation required.

Natural-language → code generation stays with the MCP ``tool_forge`` tool
(LLM loop); the factory accepts model-authored *source code* and owns the
validate → register → invoke path.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "_", name.strip().lower().replace("-", "_").replace(" ", "_"))
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "unnamed"


def _normalize_tool_name(name: str) -> tuple[str, str]:
    """Return (tool_name, slug) ensuring the gen__ prefix."""
    raw = name.strip()
    slug = _slugify(raw.removeprefix("gen__"))
    return f"gen__{slug}", slug


def run_maybe_async(fn: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    """Call ``fn`` which may be sync or async, from any thread/loop context."""
    if not inspect.iscoroutinefunction(fn):
        return fn(**kwargs)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fn(**kwargs))

    # A loop is already running in this thread (LangGraph astream): execute the
    # coroutine in a helper thread with its own loop and wait for it.
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(fn(**kwargs))
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class ToolFactory:
    """Validate → register → invoke generated tools at runtime."""

    def __init__(self, state: Any, *, run_id: str = "", agent_id: str = "supervisor"):
        self._state = state
        self._run_id = run_id
        self._agent_id = agent_id
        self._live: dict[str, Callable[..., Any]] = {}

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    def create_tool(
        self,
        *,
        name: str,
        source: str,
        description: str = "",
        function_name: str | None = None,
        parameters: dict[str, Any] | None = None,
        allowed_imports: list[str] | None = None,
        test_args: dict[str, Any] | None = None,
        spec: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a ``gen__`` tool from model-authored Python ``source``.

        The source must define ``function_name`` (default: the tool slug).
        The file is written under ``settings.generated_tools_dir``, validated
        by the AST guard, optionally exercised in the sandbox with
        ``test_args``, then persisted (active=1) in the procedural table.
        """
        from ..mcp import registry  # noqa: TID252, PLC0415
        from ..subagents.sandbox import run_code, validate_source_file  # noqa: TID252, PLC0415

        tool_name, slug = _normalize_tool_name(name)
        fn_name = function_name or slug

        if not source.strip():
            return {"ok": False, "tool": tool_name, "error": "source is required"}
        if f"def {fn_name}" not in source:
            return {
                "ok": False,
                "tool": tool_name,
                "error": f"source must define function {fn_name!r}",
            }

        settings = self._state.settings
        script_path = Path(settings.generated_tools_dir) / f"{slug}.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(source, encoding="utf-8")

        # 1. AST guard (same guard used at forge time).
        try:
            validate_source_file(script_path, set(allowed_imports) if allowed_imports else None)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "tool": tool_name, "error": f"AST guard rejected: {exc}"}

        # 2. Optional sandbox smoke test.
        validation: dict[str, Any] = {"ast_guard": "pass"}
        if test_args is not None:
            harness = f"{source}\nresult = {fn_name}(**{test_args!r})\n"
            outcome = run_code(
                harness,
                allowed_imports=set(allowed_imports) if allowed_imports else None,
                timeout_seconds=20,
                workspace_dir=Path(settings.munin_data_path) / "sandbox_runs",
            )
            validation["sandbox"] = {
                "ok": outcome.ok,
                "error": outcome.error,
                "duration_seconds": outcome.duration_seconds,
            }
            if not outcome.ok:
                return {
                    "ok": False,
                    "tool": tool_name,
                    "error": f"sandbox test failed: {outcome.error}",
                    "validation": validation,
                }

        # 3. Persist with provenance (procedural table is the Tool Registry).
        signature = {
            "function_name": fn_name,
            "parameters": parameters or {"type": "object", "properties": {}},
            "provenance": {
                "creator_agent": self._agent_id,
                "parent_run": self._run_id,
                "spec": spec or description,
                "dependencies": [],
                "validation": validation,
            },
        }
        tool_tags = ["tool-factory", f"run:{self._run_id or 'unknown'}", *(tags or [])]
        registry.register_state_only(
            self._state,
            slug=slug,
            description=description or f"Tool-factory tool {tool_name}",
            script_path=script_path,
            function_name=fn_name,
            signature=signature,
            tags=tool_tags,
            created_by_agent=self._agent_id,
        )

        # 4. Load live handle for same-run invocation.
        fn = registry._load_callable(script_path.resolve(), fn_name)
        self._live[tool_name] = registry.wrap_generated_callable(
            fn, tool_name=tool_name, state=self._state
        )

        logger.info("tool_factory: created %s (%s)", tool_name, script_path)
        return {
            "ok": True,
            "tool": tool_name,
            "script_path": str(script_path),
            "validation": validation,
            "invocable_now": True,
        }

    # ------------------------------------------------------------------
    # invoke (same-run generic execution path)
    # ------------------------------------------------------------------

    def _load_from_registry(self, name: str) -> Callable[..., Any]:
        from ..mcp import registry  # noqa: TID252, PLC0415

        row = self._state.procedural_get(name)
        if row is None:
            raise KeyError(f"Tool {name!r} not found in the Tool Registry")
        if not row.get("active", False):
            raise KeyError(f"Tool {name!r} is inactive")
        sig = row.get("signature") or {}
        fn_name = sig.get("function_name") or name.removeprefix("gen__")
        script = registry.resolve_script_path(self._state.settings, row["script_path"])
        if not script.exists() and row.get("source_code"):
            # Turso-mode restore: materialize the durable source on this runner.
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(row["source_code"], encoding="utf-8")
        fn = registry._load_callable(script, fn_name)
        return registry.wrap_generated_callable(fn, tool_name=name, state=self._state)

    def invoke_registered_tool(self, name: str, arguments: dict[str, Any] | str | None = None) -> Any:
        """Invoke any registered tool (gen__* or fixed catalog) by name.

        Same-run created tools resolve from the live cache; everything else
        resolves from the procedural table / fixed catalog.  This is the
        single generic execution path required by issue #9 §2.
        """
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}
        arguments = dict(arguments or {})

        handler = self._live.get(name)
        if handler is None:
            if name.startswith("gen__"):
                handler = self._load_from_registry(name)
                self._live[name] = handler
            else:
                from ..subagents.base import build_tool_catalog  # noqa: TID252, PLC0415

                catalog = build_tool_catalog(self._state, {name})
                handler = catalog.get(name)
                if handler is None:
                    raise KeyError(f"Tool {name!r} not found in the Tool Registry or catalog")
        return run_maybe_async(handler, arguments)

    # ------------------------------------------------------------------
    # list / inspect
    # ------------------------------------------------------------------

    def list_registered_tools(self, *, gen_only: bool = False) -> list[dict[str, Any]]:
        rows = self._state.procedural_list()
        if gen_only:
            rows = [r for r in rows if r["name"].startswith("gen__")]
        for row in rows:
            row.pop("source_code", None)
        return rows

    def inspect_registered_tool(self, name: str, *, include_source: bool = False) -> dict[str, Any]:
        row = self._state.procedural_get(name)
        if row is None:
            raise KeyError(f"Tool {name!r} not found")
        if not include_source:
            row.pop("source_code", None)
        return row
