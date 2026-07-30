# PR-12 — Send workers (parallel fan-out replacement)

- **Head**: `raven-mind/migration-issue9/pr-12-send-workers`
- **Base**: `raven-mind/migration-issue9/pr-11-langgraph-server-deployment`
- **Open architectural questions**: None. Send + reducer contract verified Context7 PR-09 record. `worker_index` per-worker UI identity streams via existing `subagent-presence` custom part (PR-02).

---

## Goal

Replace `munin/production/parallel.py::execute_tool_batch()`'s parallel-tool-batch path with native LangGraph `Send` fan-out for dynamic-N-worker scenarios (one per host/CVE/URL). Issue acceptance #7 ("`Send` is used for dynamic fan-out workers") + E2E #5 ("Parallel workers").

## Acceptance title (one line)

`Send(worker_state)` distributes N workers (one per fixture host), executes concurrently, aggregates via `Annotated[list, operator.add]` reducer deterministically, UI surfaces per-worker identity + progress + outcome; one worker failing does NOT abort the batch.

## Issue required end-to-end scenarios this PR partially unlocks

**Parallel workers** (issue E2E #5): full unlocker. (Removes old `MUNIN_MAX_PARALLEL_TOOLS=6` hard cap — backpressure via RecursionLimit + cancel_async_task observable.)
Unlocks UI impact: worker fan-out visible in chat UI as multiple `subagent-presence` parts with `worker_index`.

---

## Files added

| Path | What |
|---|---|
| `munin/core/parallel/send_workers.py` | `Send (worker_class: CompiledStateGraph) -> Send`; `aggregate_workers` reducer `Annotated[list, operator.add]`. `WorkerState` TypedDict with `messages`, `worker_index`, `task_args`, `aggregate: Annotated[list, operator.add]` shapes (parallel to Context7 example). |
| `tests/characterization/test_send_workers_fanout.py` | 5 workers, every worker appends to aggregate via reducer; final state aggregate has 5 entries in original order. |
| `tests/characterization/test_send_workers_individual_failure.py` | One worker raises; remaining 4 complete; state `aggregate` has 4 entries + `errors_count` field set to 1; batch did NOT abort. |
| `tests/characterization/test_send_workers_persistence_checkpointer.py` | Fan-out mid-run; kill supervisor; restart; `check_async_task` view returns partial aggregates from already-completed workers resumable.
-Ui microscope: PR-02's `subagent-presence` custom part now emits `worker_index` + `parent_run_id` per fan-out worker. |

## Files modified

| Path | What changes |
|---|---|
| `munin/production/parallel.py` | The `execute_tool_batch()`'s multi-tool dispatch path conditional now routes to `send_workers.fanout_fired?` when N static-known tools AND each calls `parallel_safe()` per batch — otherwise Send (LangGraph native `tools` parameter at `create_agent(..., tools=[...])` handles it without us). Static single-iteration multi-tool dispatch stays unchanged via LangGraph natural multi-tool-call support — no extra code. |
| `munin/core/supervisor.py` | A Munin-side function `schedule_workers(spec: WorkerSpec) -> list[Send]` exposed as a supervisor tool. Supervisor invokes it like `create_workflow` (an anon subgraph node builds + dispatches the Send). Wire `worker_index` into the `subagent-presence` data part for each spawned worker via PR-02's part surface.
- `MUNIN_MAX_PARALLEL_TOOLS=6` constant: REMOVED from `production/parallel.py`. Replaced by LangGraph RecursionLimit observable + supervisor's `worker_count_knob` (env var `MUNIN_SUGGESTED_WORKERS` advisory value, default 4 — used only when prompt is ambiguous on how many workers; not a cap). |

## Files deleted

| Path | Why |
|---|---|
| `MUNIN_MAX_PARALLEL_TOOLS=6` constant | Per issue §4: "no arbitrary hard caps". Static single-tool dispatch still uses LangGraph natural multi-tool-call (no replacement needed); dynamic N-worker dispatch uses Send-based pattern + RecursionLimit observable. Backpressure via `cancel_async_task`(PR-11) lets operator stop a runaway fan-out. |

---

## Per-function behavior

### `munin/core/parallel/send_workers.py`

Framework provenance: Context7 LangGraph `Use Sent to dynamically route to nodes with specific state subsets, typically for map-reduce patterns. It requires the target node name and the state object to pass.`

```python
import operator
from typing import Annotated, TypedDict
from langgraph.types import Send
from langgraph.graph import StateGraph, START, END

class WorkerState(TypedDict):
    messages: list
    worker_index: int        # so UI can identify per-worker progress
    task_args: dict           # the per-host/per-CVE/per-URL spec
    aggregate: Annotated[list, operator.add]  # accumulating reducer

def fanout(state: dict) -> list[Send]:
    """Build fan-out Sends for a worker-class subgraph based on state['tasks']."""
    return [Send("worker_node", WorkerState(worker_index=i, task_args=t, messages=[], aggregate=[])) for i, t in enumerate(state["tasks"])]

# The companion aggregate state lives in the parent graph's state schema:
#   parent_state["aggregate"] = list[idx] (reducer = operator.add)
```

### Parallel batch route decision

```python
def execute_tool_batch(...) -> dict:
    # Dynamic multi-tool "parallel" call path
    if len(tools) > 1 and all parallel_safe(t):
        # delegate to native LangGraph multi-tool batching — handled by `create_agent(tools=[...])`
        # natural parallel dispatch by LLM-generated multi-tool_calls. NO new Send.
        return None  # signal returns with "use LangGraph native"
    # Legacy single-tool continues via legacy path (only delete in PR-15).
    # … [old condition path unchanged]
```

The PR-12 change: when `parallel_safe=True` for all tools, we LET LangGraph's native multi-tool batching handle (it's already correct). When supervisor explicitly decides "fan-out" based on its own agent reason, it routes via `schedule_workers(spec)` which generates `<Send>` list per worker through LangGraph.

### Delete hard cap

```python
# Before:
MUNIN_MAX_PARALLEL_TOOLS = int(os.environ.get("MUNIN_MAX_PARALLEL_TOOLS", "6"))
# After:
# (deleted; replaced by LangGraph RecursionLimit observable + advisory `MUNIN_SUGGESTED_WORKERS` knob)
MUNIN_SUGGESTED_WORKERS = int(os.environ.get("MUNIN_SUGGESTED_WORKERS", "4"))
```

## Tests added

Per Files Added table.

## Parity bar (PR-01 preserved)

`test_coord_respond_loop_parity.py` assertion about `tool_calls_log` parallel_group_id stamp stays. New test asserts Send-based fanouts still emit `parallel_group_id` per worker. Other 6 PR-01 tests unchanged.

## Deps bumped / added

None.

## Rollback plan

Revert removes `send_workers.py` + 3 PR-12 tests; restores `MUNIN_MAX_PARALLEL_TOOLS=6`; Pipes old `execute_tool_batch()` parallel-batch path. Standalone.

## Validation plan

1. Characterization tests: PR-01..PR-11 + 3 PR-12 tests green.
2. CI green.
3. Live-session workflow: chrome-devtools MCP — ask Munin "Recon sweep on hosts A, B, C, D in parallel using the existing `nmap_scan` tool, one worker per host." Assert: 4 distinct `subagent-presence` parts visible (one per worker with `worker_index: 0/1/2/3`); agent aggregate has 4 entries with results; if `host_C` raises (mock), other 3 complete and aggregate has 3 entries + error counter=1; supervisor continues.
4. Artifact inspection: `data/langgraph_checkpoints.sqlite`唯一—— `check_async_task` captures per-worker state at interrupt boundaries surviving restart test.
5. Parity manual check: live run after embedding parallel-group_id still populates `tool_calls.parallel_group_id` in ProductionStore (no regression in DB row shape).

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools | Untouched (workers call same ToolGateway tools) |
| Scope/OPSEC at tool boundary | Preserved — each worker's tool calls route through supervisor's ToolNode → existing OPSEC preflight runs |
| Audit redaction contract | Preserved — audit.py runs per worker invocation |
| Tool provenance | Untouched |
| Soul human-editable | Untouched |
| Cross-session artifact pattern | Untouched (workers dispatched in-graph, parents persist via checkpointer) |

## Framework verification provenance

- LangGraph Send + Annotated[list, operator.add] reducer pattern: Context7 LangGraph "Define a LangGraph with Parallel Nodes and State Reducer" + "Dynamic Edge Creation with Send in Python".
- `Command(goto=, graph=Command.PARENT, update={...})` for subgraph state propagation: Context7 "Define Reducer for Parent Graph State Updates". PR-09 uses this; PR-12 reuses.
- RecursionLimit replaces fixed-cap concurrency: standard in LangGraph; observable via stream_events for incremental position display.

Uncertainty remaining: zero.