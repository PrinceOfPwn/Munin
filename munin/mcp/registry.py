"""Dynamic MCP tool registry — hot-loads tools produced by `tool_forge`.

When `tool_forge` finishes generating and validating a Python script, we import the
callable and expose it as an MCP tool named ``gen__<slug>``. All generated tools live
under a common prefix so any MCP client can enumerate them. Metadata (name, description,
signature, tags, script path) persists in `procedural` table and is re-loaded on server
startup by :func:`rehydrate`.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings, safe_slug
from .shared_state import SharedStateStore

logger = logging.getLogger("munin-mcp.registry")

GENERATED_PREFIX = "gen__"


class ToolRegistryError(RuntimeError):
    pass


    # Cache: (resolved_path, function_name) → (mtime_ns, callable).
    # `_current_catalog()` in MuninAgent calls _load_callable for EVERY known
    # generated tool on EVERY ReAct iteration. Without a cache, 40 iterations
    # × 15 tools = 600 imports + AST parses per chat, each replaying that
    # tool's top-level side effects (session objects, counters, cached data).
    # We key on mtime_ns so an operator editing a generated script on disk
    # still gets picked up on the next call.
_CALLABLE_CACHE: dict[tuple[str, str], tuple[int, Callable[..., Any]]] = {}


def clear_callable_cache() -> None:
    """Empty the callable cache. Call from tests or after `procedural_purge_all`."""
    _CALLABLE_CACHE.clear()


def resolve_script_path(settings: Settings, stored_path: Path | str) -> Path:
    """Resolve a persisted generated-tool path across ephemeral runner roots."""
    path = Path(stored_path)
    if not path.is_absolute():
        path = settings.workspace_root / path
    if path.exists():
        return path.resolve()

    # Rows written by older releases persisted an absolute GitHub workspace
    # path. A subsequent runner has a different root, but restored sources keep
    # the same generated filename.
    fallback = settings.generated_tools_dir / path.name
    return fallback.resolve()


def portable_script_path(settings: Settings, script_path: Path | str) -> str:
    """Persist repo-relative paths when possible so Turso rows are portable."""
    resolved = Path(script_path).resolve()
    try:
        return resolved.relative_to(settings.workspace_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _restore_source_if_needed(settings: Settings, row: dict[str, Any]) -> Path:
    """Materialize a Turso-backed generated source on a new runner if needed."""
    path = resolve_script_path(settings, row["script_path"])
    if path.exists():
        return path
    source = str(row.get("source_code") or "")
    if not source:
        raise ToolRegistryError(
            f"durable source missing for {row.get('name', 'generated tool')}; "
            "the legacy runner stored only its path"
        )
    destination = settings.generated_tools_dir / Path(row["script_path"]).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".py.tmp")
    temporary.write_text(source, encoding="utf-8")
    temporary.replace(destination)
    return destination.resolve()


def _load_callable(script_path: Path, function_name: str) -> Callable[..., Any]:
    """Import a generated script and return the requested callable.

    Runs the sandbox AST guard on the file BEFORE importing it — this closes the
    bypass where a forged tool could smuggle banned imports/attributes at module
    top level and have them executed during ``spec.loader.exec_module`` (which
    happens without the sandbox's restricted-builtins env). If the AST guard
    rejects the file, we raise ``ToolRegistryError`` and never import it.

    Cached by (resolved_path, function_name, mtime_ns). Editing the script on
    disk invalidates the cache automatically.
    """
    if not script_path.exists():
        raise ToolRegistryError(f"script not found: {script_path}")

    resolved = str(script_path.resolve())
    cache_key = (resolved, function_name)
    try:
        current_mtime = script_path.stat().st_mtime_ns
    except OSError:
        current_mtime = 0

    cached = _CALLABLE_CACHE.get(cache_key)
    if cached is not None and cached[0] == current_mtime:
        return cached[1]

    # Re-validate on disk. Lazy import to avoid circular dependency between
    # subagents and mcp packages.
    try:
        from ..subagents.sandbox import SandboxViolation, validate_source_file  # noqa: TID252,PLC0415
        validate_source_file(script_path, allowed_imports=None)
    except SandboxViolation as exc:
        raise ToolRegistryError(f"sandbox re-validation failed for {script_path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — a broken re-check must NOT silently pass
        raise ToolRegistryError(f"could not re-validate {script_path}: {exc}") from exc

    module_name = f"munin_generated__{safe_slug([script_path.stem])}"
    spec = importlib.util.spec_from_file_location(module_name, str(script_path))
    if not spec or not spec.loader:
        raise ToolRegistryError(f"failed to build import spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, function_name):
        raise ToolRegistryError(f"function '{function_name}' not found in {script_path}")
    obj = getattr(module, function_name)
    if not callable(obj):
        raise ToolRegistryError(f"'{function_name}' in {script_path} is not callable")

    _CALLABLE_CACHE[cache_key] = (current_mtime, obj)
    return obj


def _tool_name(slug: str) -> str:
    slug = safe_slug([slug])
    return f"{GENERATED_PREFIX}{slug}"


def wrap_generated_callable(
    fn: Callable[..., Any],
    *,
    tool_name: str,
    state: SharedStateStore,
) -> Callable[..., dict[str, Any]]:
    """Return the common safe envelope used by MCP and in-process subagents."""
    sig = inspect.signature(fn)

    def _handler(*args: Any, **kwargs: Any) -> dict[str, Any]:
        row = state.procedural_get(tool_name)
        if row is not None and not row.get("active", False):
            return {
                "ok": False,
                "tool": tool_name,
                "mode": "sync",
                "summary": f"{tool_name} is inactive",
                "error": {"code": "inactive_tool", "message": tool_name},
            }
        try:
            result = fn(*args, **kwargs)
            output = result if isinstance(result, dict) else {"result": result}
            state.episodic_record(
                agent="generated_tool_runtime",
                action=tool_name,
                input_data={"args": args, "kwargs": kwargs},
                output_data=output,
                tags=["autogenerated"],
            )
            return {
                "ok": True,
                "tool": tool_name,
                "mode": "sync",
                "summary": f"{tool_name} ran",
                "data": output,
            }
        except Exception as exc:
            state.episodic_record(
                agent="generated_tool_runtime",
                action=tool_name,
                input_data={"args": args, "kwargs": kwargs},
                output_data={"error": str(exc)},
                tags=["autogenerated", "error"],
            )
            return {
                "ok": False,
                "tool": tool_name,
                "mode": "sync",
                "summary": f"{tool_name} failed",
                "error": {"code": "generated_tool_failed", "message": str(exc)},
            }

    _handler.__name__ = tool_name
    _handler.__doc__ = fn.__doc__
    _handler.__signature__ = sig  # type: ignore[attr-defined]
    return _handler


def register(
    mcp: Any,
    state: SharedStateStore,
    *,
    slug: str,
    description: str,
    script_path: Path | str,
    function_name: str,
    signature: dict[str, Any],
    tags: list[str] | None = None,
    created_by_agent: str = "tool_forge",
) -> dict[str, Any]:
    """Register a generated tool: persist row + attach handler to MCP server."""
    tool_name = _tool_name(slug)
    script_path = resolve_script_path(state.settings, script_path)
    fn = _load_callable(script_path, function_name)
    source_code = script_path.read_text(encoding="utf-8")
    handler_doc = (description or fn.__doc__ or "").strip() or f"Autogenerated tool {tool_name}"
    _handler = wrap_generated_callable(fn, tool_name=tool_name, state=state)
    _handler.__doc__ = handler_doc

    try:
        mcp.tool()(_handler)
    except Exception as exc:  # pragma: no cover - guardrail
        raise ToolRegistryError(f"failed to attach {tool_name} to MCP server: {exc}") from exc

    state.procedural_register(
        name=tool_name,
        description=handler_doc,
        script_path=portable_script_path(state.settings, script_path),
        source_code=source_code,
        signature=signature,
        tags=tags or [],
        created_by_agent=created_by_agent,
    )
    logger.info("registered generated tool %s (%s)", tool_name, script_path)
    return {"name": tool_name, "script_path": str(script_path), "signature": signature, "tags": tags or []}


def register_state_only(
    state: SharedStateStore,
    *,
    slug: str,
    description: str,
    script_path: Path | str,
    function_name: str,
    signature: dict[str, Any],
    tags: list[str] | None = None,
    created_by_agent: str = "tool_forge",
) -> dict[str, Any]:
    """Persist a generated tool WITHOUT attaching it to a running MCP server.

    Called from subprocess contexts (the subagent runner) where the FastMCP
    instance lives in a different process. The tool becomes callable the next
    time the parent MCP server invokes ``rehydrate()`` at startup — or right
    away via ``run_generated_tool`` / ``describe_generated_tool`` which read
    directly from the ``procedural`` table.

    Validates the script imports cleanly before persisting, so we never store
    a row pointing at a broken file.
    """
    tool_name = _tool_name(slug)
    script_path = resolve_script_path(state.settings, script_path)
    # Validate callable is importable — if this fails we don't persist the row.
    _ = _load_callable(script_path, function_name)
    source_code = script_path.read_text(encoding="utf-8")
    handler_doc = (description or "").strip() or f"Autogenerated tool {tool_name}"

    state.procedural_register(
        name=tool_name,
        description=handler_doc,
        script_path=portable_script_path(state.settings, script_path),
        source_code=source_code,
        signature=signature,
        tags=tags or [],
        created_by_agent=created_by_agent,
    )
    logger.info("registered (state-only) generated tool %s (%s)", tool_name, script_path)
    return {
        "name": tool_name,
        "script_path": str(script_path),
        "signature": signature,
        "tags": tags or [],
        "attached_to_mcp": False,  # will attach on next server restart via rehydrate
    }


def rehydrate(mcp: Any, state: SharedStateStore, settings: Settings) -> int:
    """Re-attach every active generated tool at server startup."""
    active = state.procedural_list(include_inactive=False, include_source=True)
    count = 0
    for row in active:
        try:
            slug = row["name"].removeprefix(GENERATED_PREFIX)
            function_name = row["signature"].get("function_name") or row["signature"].get("name") or slug
            register(
                mcp,
                state,
                slug=slug,
                description=row.get("description", ""),
                script_path=_restore_source_if_needed(settings, row),
                function_name=function_name,
                signature=row.get("signature", {}),
                tags=row.get("tags", []),
                created_by_agent=row.get("created_by_agent", "tool_forge"),
            )
            count += 1
        except Exception as exc:
            logger.warning("failed to rehydrate %s: %s", row.get("name"), exc)
    return count


def resolve_tool_by_name(
    state: SharedStateStore,
    name: str,
    *,
    include_inactive: bool = False,
) -> dict[str, Any] | None:
    if not name.startswith(GENERATED_PREFIX):
        name = _tool_name(name)
    row = state.procedural_get(name)
    if row is None or (not include_inactive and not row.get("active", False)):
        return None
    return row


def list_generated(state: SharedStateStore, *, tag: str = "") -> list[dict[str, Any]]:
    return state.procedural_list(tag=tag, include_inactive=False)


def deactivate(mcp: Any, state: SharedStateStore, name: str) -> bool:
    if not name.startswith(GENERATED_PREFIX):
        name = _tool_name(name)
    deactivated = state.procedural_deactivate(name)
    if not deactivated:
        return False
    try:
        mcp.remove_tool(name)
    except Exception as exc:  # pragma: no cover - version-specific FastMCP guard
        logger.warning("could not detach inactive generated tool %s: %s", name, exc)
    return True


def purge_all(state: SharedStateStore) -> int:
    """Purge every generated tool. Used by `munin reset`."""
    return state.procedural_purge_all()


def signature_to_json_schema(sig: inspect.Signature) -> dict[str, Any]:
    """Best-effort inspect.Signature → JSON schema. Used by tool_forge before persistence."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        annotation = param.annotation if param.annotation is not inspect._empty else str
        json_type = "string"
        if annotation in (int,):
            json_type = "integer"
        elif annotation in (float,):
            json_type = "number"
        elif annotation in (bool,):
            json_type = "boolean"
        elif annotation in (list, tuple):
            json_type = "array"
        elif annotation in (dict,):
            json_type = "object"
        properties[name] = {"type": json_type}
        if param.default is inspect._empty:
            required.append(name)
        else:
            try:
                json.dumps(param.default)
                properties[name]["default"] = param.default
            except TypeError:
                pass
    return {"type": "object", "properties": properties, "required": required}
