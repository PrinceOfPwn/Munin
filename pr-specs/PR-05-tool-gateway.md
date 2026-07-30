# PR-05 — Tool Gateway (FastMCP-native + gen__ unified as LangChain tools)

- **Head**: `raven-mind/migration-issue9/pr-05-tool-gateway`
- **Base**: `raven-mind/migration-issue9/pr-04-supervisor-parity-delete-respond`
- **Open architectural questions**: None. Stage 0 confirmed: DeepWiki `langchain-ai/deepagents` + `langchain-ai/langgraph` revealed that deepagents **ships a concrete `_build_cached_mcp_tool` recipe in `libs/code/deepagents_code/mcp_tools.py`** that uses `StructuredTool.from_function`-equivalent construction with `args_schema=pydantic.BaseModel` derived from `mcp_tool.inputSchema`, an `async coroutine` for tool body, and a `name`/`description` taken directly from the MCP tool metadata. LangGraph's `ToolNode` retrieves tools by `name` attribute and converts non-`BaseTool` inputs via `create_tool`. Issue §9 makes FastMCP preservation an invariant, so we wrap not replace.

---

## Goal

Make every FastMCP/MCP/native/generated tool consumable by the supervisor through ONE execution path that is not bespoke per generator. PR-05 wraps + exposes; PR-06 owns the meta-tool variants. Issue acceptance #4 (`Existing MCP and native tools are usable by Deep Agents`).

## Acceptance title (one line)

Supervisor sees the same 65 fixed MCP tools + N `gen__*` tools as a single typed LangChain `StructuredTool` list — invocation surface identical behaviourally to pre-supervisor dispatch.

## Issue required end-to-end scenarios this PR partially unlocks

**Dynamic tool** (issue required E2E #1): partially unlocks — once PR-06's `create_tool` meta-tool exists, the full dynamic-tool scenario completes. PR-05 alone gives the supervisor the ability to call ANY existing fixed+gen__ tool, but not the act of creating one mid-run.

---

## Files added

| Path | What |
|---|---|
| `munin/core/tool_gateway.py` | `wrap_mcp_tool(mcp_handler, signature_dict) -> StructuredTool` (single function); `wrap_all_tools(mcp_server, registry, settings) -> list[StructuredTool]` (builds the full catalog). Reuses deepagents recipe (`_build_cached_mcp_tool` shape) — we don't copy that file but follow the same `StructuredTool(cors=coroutine, name=, description=, args_schema=)` pattern. |
| `tests/characterization/test_tool_gateway_parity.py` | Wrapped tools behave identically to legacy `MuninAgent._NATIVE_TOOLS` lookup + dispatch + result reinjection: same name, args schema, description, return shape. |
| `tests/characterization/test_tool_gateway_opsec.py` | OPSEC preflight still triggers on wrapped tools; scope/authorization enforced before tool body runs (issue §9 invariant). |

## Files modified

| Path | What changes |
|---|---|
| `munin/core/supervisor.py` | `build_supervisor(...)` constructs `tools = tool_gateway.wrap_all_tools(mcp_server, registry, settings)` and passes to `create_deep_agent(tools=...)`. Currently (post-PR-04) it uses `MuninAgent`'s legacy catalog; rewrites here. |
| `munin/mcp/registry.py` | Expose the dictionary of `(name, signature_dict)` pairs for `wrap_all_tools` via a new method `iter_signature_specs() -> Iterator[tuple[str, dict]]`. Existing `rehydrate()` and `register()` unchanged; this is read-only addition. |

## Files deleted

None. `munin/mcp/main.py` keeps `mcp.tool()(handler)` registration intact — the FastMCP server is the source of truth for callable handlers; the gateway layer creates typed wrappers for the supervisor on demand.

---

## Per-function behavior

### `munin/core/tool_gateway.py`

```python
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model
from typing import Any, Awaitable, Callable

def wrap_mcp_tool(
    name: str,
    description: str,
    signature: dict,
    handler: Callable[..., Awaitable[Any] | Any],
) -> StructuredTool:
    """Wrap a FastMCP-registered callable as a LangChain StructuredTool.

    Args schema derives from the existing Munin `signature` JSON shape (already
    OpenAI-compatible per registry._make_tool_schema); converted to pydantic.

    Issue §9: OPSEC preflight + scope enforcement live INSIDE the handler body
    (munin/mcp/opsec.py). The wrapper invokes handler directly so OPSEC runs
    unchanged before the side effect.
    """
    args_schema = _signature_to_pydantic(name, signature)
    async def _coro(**kwargs) -> Any:
        return await handler(**kwargs) if callable_awaitable else handler(**kwargs)
    return StructuredTool(
        name=name,
        description=description,
        args_schema=args_schema,
        coroutine=_coro,
    )

def wrap_all_tools(mcp_server, registry, settings) -> list[StructuredTool]:
    """Build the full supervisor tool catalog: 65 fixed + N gen__.

    Source: FastMCP-registered handlers (via mcp_server.list_tools()) merged with
    registry.iter_signature_specs() so gen__ tools not yet attached to MCP still
    resolve. Dedup by name; gen__ overrides native if a name collision exists
    (legacy Munin behaviour).
    """
    ...
```

Framework provenance: DeepWiki `langchain-ai/deepagents` confirmed `_build_cached_mcp_tool` recipe (libs/code/deepagents_code/mcp_tools.py) uses `StructuredTool(coroutine=, name=, description=, args_schema=)`. Context7 `/websites/langchain_oss_python_langgraph` confirmed `ToolNode` stores tools by `name`; `create_tool` converts non-`BaseTool` inputs (so we raise directly via StructuredTool instance).

OPSEC invariant: the wrapper is structurally transparent. Issue §9 invariant "Scope, authorization, OPSEC enforced inside the tool boundary" is preserved because `wrap_mcp_tool` calls the existing FastMCP `handler` directly, and `munin/mcp/opsec.py` preflight is called from inside each destructive/restricted native tool's body — no prompt-level bypass introduced. New `test_tool_gateway_opsec.py` re-asserts this.

### `munin/mcp/registry.py::iter_signature_specs()`

```python
def iter_signature_specs(self) -> Iterator[tuple[str, dict]]:
    """Yield (name, signature_dict) for every active tool — fixed MCP + procedural
    table gen__ rows — with the same JSON shape _make_tool_schema() emits.
    Read-only; no IPC side effects.
    """
    for name, sig in self._make_tool_schemas():
        yield name, sig
    for row in self._state.procedural_active_rows():
        yield row["name"], json.loads(row["signature"])
```

## Tests added

| Path | Assertion contract |
|---|---|
| `test_tool_gateway_parity.py` | For 5 representative tools (one LDAP, one recon, one intel, one memory, one gen__ existing-procedural): wrapped `StructuredTool` `name`/`description`/`args_schema` match legacy `_make_tool_schema` output; invoking wrapped tool with fixed kwargs returns identical value to invoking the original handler with same kwargs. |
| `test_tool_gateway_opsec.py` | A destructive/restricted tool wrapped (e.g. `nmap_scan`); with `PREFLIGHT_POLICY=on` and no scope configured, wrapped tool raises (suspending scope error). Same opsec-check behaviour as legacy MuninAgent dispatch. |

## Parity bar (PR-01 preserved)

All 7 PR-01 characterization tests remain green because:
- Coordinator parity (`test_coord_respond_loop_parity.py`) calls supervisor via PR-04's repointed assertions; supervisor now uses tool_gateway catalog — assert tool list shape identical to MuninAgent's catalog (covered by `test_tool_gateway_parity.py` additional layer).
- Tool catalog (`test_tool_catalog_parity.py`) keeps asserting `gen__` prefix + `rehydrate` active=1 + `register_state_only` + signature→schema shape — the deepest contract still passes; new `iter_signature_specs()` reuses existing signature emission paths.
- Subagent / HITL / streaming / conversation / shared-state tests touch unchanged surfaces.

## Deps bumped / added in this PR

None. `langchain-core` (which provides `StructuredTool`, `BaseModel`, `create_model`) is already transitively required by `langchain >= 0.3.0` and `langgraph >= 0.2.40`. Confirm transitive during pyproject audit at delegation — if `langchain-core` is NOT pinned transitively by `langchain`, add an explicit `langchain-core>=0.3` line here with rationale.

## Rollback plan

Revert removes `tool_gateway.py` + 2 tests; restores `build_supervisor()` to legacy `_NATIVE_TOOLS + gen__` catalog (the MuninAgent internals or whatever was left). Standalone — does not break PR-06 since PR-06 builds the meta-tools on top of this gateway.

## Validation plan

1. Characterization tests: `pytest tests/characterization/ -v` → all 7 PR-01 + 2 new PR-05 + 4 PR-03 tests green.
2. CI green necessary: ci.yml backend + e2e_lab jobs.
3. Live-session workflow: tunnel URL; chrome-devtools MCP: trigger chat prompt that calls a fixed MCP tool (e.g. `cve_lookup`) and a gen__ tool (if any in procedural table from a prior run). Confirm both succeed with identical payloads to legacy. Screenshots in `evidence/PR-05/`.
4. Artifact inspection: `data/shared_state.sqlite` `episodic` rows show `tool_start` + `tool_result` events with same `tool_use_id`-correlated envelope shape (issue §11 single-owner invariant: tool_calls rows continue to populate ProductionStore).
5. Parity manual check: `pytest tests/characterization/test_tool_catalog_parity.py tests/characterization/test_tool_gateway_parity.py -v` after merge.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools + external MCP integration | **Preserved explicitly** — wrap not replace (issue non-goal §10 explicit) |
| Hugin + offensive tool wrappers | Preserved — Hugin tool appears via the same gateway as any native tool |
| Scope/OPSEC in tool boundary | Preserved — `test_tool_gateway_opsec.py` re-asserts preflight is triggered pre-exec |
| Audit redaction contract | Untouched — handler still writes via audit.py; gateway layer doesn't intercept args results |
| Tool provenance | Preserved — gateway's catalog source includes the same `procedural` table columns |
| Cross-session artifact pattern | Untouched |

## Framework verification provenance

- **StructuedTool wrapping recipe**: DeepWiki `langchain-ai/deepagents` ask_question "For Munin's Tool Gateway design" — confirmed `_build_cached_mcp_tool` recipe at libs/code/deepagents_code/mcp_tools.py uses `StructuredTool(coroutine=, name=, description=, args_schema=)`. We reuse the pattern; do NOT prematurely depend on the specific function name (may not be public API across deepagents versions); only the construction shape is the contract.
- **`StructuredTool` + args_schema from pydantic**: same DeepWiki source.
- **ToolNode invocation by `name` + non-BaseTool conversion**: DeepWiki `langchain-ai/langgraph` confirmed.
- **Subagent's `tools=[...]` replaces parent's tools entirely**: DeepWiki `langchain-ai/deepagents` "When a subagent's spec provides its own `tools=[...]` list" — required for PR-07's subagent spec construction.
- **Pydantic model creation from JSON signature**: standard API via `pydantic.create_model(name, **{field: (type, default), ...})`. Subagent verifies against current pydantic version during PR-05 implementation; documented as `create_model` invocation recipe.

Uncertainty remaining: the exact path `libs/code/deepagents_code/mcp_tools.py` may vary across deepagents versions. We follow the construction shape (StructuredTool from signature+coroutine) NOT the function name. If deepagents exposes a public wrapping helper, prefer it; otherwise our own `wrap_mcp_tool` is observational and tested.