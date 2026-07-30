# PR-07 — Subagent Factory (5 runtime selection + `MUNIN_MAX_NESTED_SUBAGENTS` removal)

- **Head**: `raven-mind/migration-issue9/pr-07-subagent-factory`
- **Base**: `raven-mind/migration-issue9/pr-06-tool-factory-evolution`
- **Open architectural questions**: One — AsyncSubAgent requires a LangGraph server URL from PR-11. Resolved per roadmap §"deviation from issue §12": PR-07 ships AsyncSubAgent support **stubbed to raise `NotImplementedError` when no `MUNIN_LANGGRAPH_URL` is configured**; PR-11 unlocks full functionality. Documented explicitly in spec body, not hidden.

---

## Goal

Build a Subagent Factory that materializes different agent runtimes according to requested design, choosing the lightest correct runtime. Removes `MUNIN_MAX_NESTED_SUBAGENTS=5` (issue §4). Issue acceptance #6 (the main agent and generated subagents can create additional tools, agents, and workflows).

## Acceptance title (one line)

`create_subagent(spec=...)` returns the correct runtime type per spec's `runtime_type` field (`persisted_subagent_dict`, `compiled_langgraph`, `async_langgraph`, `swarm_member`, `deep_agent`); spec复杂性 correctly routed; nested depth 7+ succeeds when LangGraph RecursionLimit allows.

## Issue required end-to-end scenarios this PR partially unlocks

**Dynamic specialist** (issue E2E #2): unlocks the "create + invoke + return traceable result" leg; "uses existing tools and factory capabilities" requires the same-run tool creation (PR-06 already unlocked that). Persistent specialist comes with PR-08 Agent Registry.

---

## Files added

| Path | What |
|---|---|
| `munin/core/autonomy/subagent_factory.py` | `create_subagent(spec: SubagentSpec) -> Runnable` with the 5-way routing. Stub for AsyncSubAgent `runtime_type="async_langgraph"` (raises `NotImplementedError("AsyncSubAgent requires MUNIN_LANGGRAPH_URL; configure via PR-11")`). |
| `munin/core/autonomy/spec.py` | `SubagentSpec` pydantic model with fields per issue §4 (name, purpose, system_prompt, model, tools, skills, memory, filesystem, middleware, response_format, interaction_topology, persistence_policy, may_create_child, custom_state, execution_mode, runtime_type, max_iterations_floor — NOT a hard cap). |
| `tests/characterization/test_subagent_factory_routes.py` | Each of 5 runtime types produces the right runnable:
  - `runtime_type="persisted_subagent_dict"` → `SubAgent` TypedDict (name/description/system_prompt/tools/model)
  - `runtime_type="deep_agent"` → recursive `create_deep_agent(...)`
  - `runtime_type="compiled_langgraph"` → custom `StateGraph` provided by client; verifies `messages` in state schema; returns CompiledSubAgent dict
  - `runtime_type="async_langgraph"` → `NotImplementedError` (no MUNIN_LANGGRAPH_URL configured in this PR's test environment)
  - `runtime_type="swarm_member"` → `langgraph-swarm` via `create_swarm()` (covered in PR-13 extension tests).
- `tests/characterization/test_subagent_factory_no_nesting_cap.py` | Spec with `max_iterations_floor=15` and nested invocation depth 7 under LangGraph RecursionLimit=50 → succeeds. |
| `tests/characterization/test_subagent_factory_invokes_in_run.py` | E2E "Dynamic specialist": Munin's natural-language need routed to `create_subagent`, returns runtime that returns traceable result via task-tool. |

## Files modified

| Path | What changes |
|---|---|
| `munin/core/supervisor.py` | `build_supervisor(...)` accepts `subagents=[]` default empty; meta-tool `create_subagent` registered in the supervisor's tool list; factory returns runnables passed back as `subagents=[]` argument if called during a run. **Iterative registry mode**: supervisor rebuilds with extended subagents list when factory returns a new subagent during a run; conversely static-subagent registration at supervisor startup. |
| `munin/core/autonomy/__init__.py` | Exposes `subagent_factory.create_subagent` + the inspect tools. |
| `munin/mcp/tools/munin_tools.py` | Add 4 new MCP entries (Autonomy Kernel meta-tools for subagents): `create_subagent`, `invoke_registered_agent`, `list_registered_agents`, `inspect_registered_agent`. (Agent Registry underlying logic comes in PR-08; here the `invoke_registered_agent` raises `NotImplementedError` PR-07 strain if a created subagent isn't persisted and rebuilt — it does invoke via `task` tool directly from supervisor). |
| `munin/subagents/base.py` | Remove `MUNIN_MAX_NESTED_SUBAGENTS=5` constant + the depth check. Replaced with LangGraph RecursionLimit semantic — observer surfaces remaining depth via stream_events (handled in PR-12). |
| `pyproject.toml` | No new deps (deepagents/langgraph-swarm already added PR-03). |

## Files deleted

| Path | Why |
|---|---|
| (NONE in PR-07) | `runner.py:_load_subagent` is the legacy subagent-factory path. It remains callable as a compat shim (via `munin_wake` MCP tool) until PR-14 deletes it after `start_async_task` proves parity. This PR's "factory" is a new path that may coexist. Spec demographics: PR-14 must verify no callers of legacy path. |

---

## Per-function behavior

### `munin/core/autonomy/subagent_factory.py::create_subagent()`

Framework provenance: DeepWiki `langchain-ai/deepagents` "SubAgent vs CompiledSubAgent vs AsyncSubAgent TypedDicts" + "A create_deep_agent compiled Pregel can be used directly as a swarm member" (per `create_swarm(agents: list[Pregel], default_active_agent, state_schema=SwarmState)`).

```python
RuntimeType = Literal["persisted_subagent_dict", "deep_agent", "compiled_langgraph", "async_langgraph", "swarm_member"]

def create_subagent(spec: SubagentSpec) -> Runnable:
    """Routes to the correct runtime.

    persisted_subagent_dict → dict `SubAgent` shape, langchain.agents.create_agent calleable underneath. Orbited by `create_deep_agent(subagents=...)`.
    deep_agent → recursive `create_deep_agent(model=spec.model, tools=spec.tools, subagents=spec.subagents, ...)`.
    compiled_langgraph → spec.user_compiled_pregel (caller) wrapped as CompiledSubAgent dict `{"name","description","runnable":Pregel}`. Required: spec.user_compiled_pregel.state_schema includes `messages`.
    async_langgraph: spec.graph_id assigned → AsyncSubAgent dict `{"name","description","graph_id","url?,"headers?"}`. If MUNIN_LANGGRAPH_URL unset → raise NotImplementedError("AsyncSubAgent requires MUNIN_LANGGRAPH_URL; configure via PR-11").
    swarm_member → wrapped via `create_swarm(agents=[...], default_active_agent=...)`. Only the "peer collaboration" arm of factory; PR-13 deepens this for handoffs.

    No hard cap on tools/depth/"max_subagent_count"; issue §4 explicit.
    """
```

### `SubagentSpec` (pydantic model)

Per issue §4 list: `name, purpose, system_prompt, model, tools, skills, memory, filesystem, middleware, response_format, interaction_topology, persistence_policy, may_create_child, custom_state, execution_mode, runtime_type, max_iterations_floor`.

No hard cap constants; `max_iterations_floor` defaults to None (Langgraph RecursionLimit defaults take over).

### `create_subagent` meta-tool registered on supervisor

The supervisor's tool list gains a `create_subagent(spec: str)` callable. Calling it constructs a `SubagentSpec`, runs `subagent_factory.create_subagent(spec)`, and:
- If runtime_type in {"persisted_subagent_dict", "deep_agent", "compiled_langgraph"}: the supervisor's `subagents=[...]` list grows; the new subagent is invokable via the `task` tool in the next supervisor step (LangGraph re-entry). Re-evaluation of `subagents` arg — verified by PR-06's same-run pattern (which establishes that LangGraph ToolNode resolves tool catalog per call).
- If `runtime_type == "swarm_member"`: enqueue into `swarm_state.active_agent` graph defer (only relevant once PR-13 wires swarm flow).
- If `runtime_type == "async_langgraph"`: NO raise here; the factory raises instead. Test verifies the error-message includes "configure via PR-11".

### `invoke_registered_agent` (PR-07 strain)

```python
def invoke_registered_agent(agent_id: str, task: dict) -> dict:
    """PR-07: assumes the subagent was created in this run already (in-memory).
    PR-08 adds persistent registry retrieval via rebuild_agent.

    Returns {"ok", "traceable_result", "tool_messages_unwrapped"}.

    "traceable" means the returned result dict includes `subagent_run_id`,
    `subagent_name`, and `iteration_count` — visible in UI via custom part
    `subagent-presence` (PR-02 part id surface)."""
```

### Remove `MUNIN_MAX_NESTED_SUBAGENTS=5`

Search + replace: `base.py` contains the constant. Drop it; replaced by LangGraph RecursionLimit. Observable via `stream_events` — supervisor reports remaining depth when at-risk (NO hard cap warning off; outline via a small optional middleware in PR-12.

## Tests added

| Path | Assertion contract |
|---|---|
| `test_subagent_factory_routes.py` | 5 fixture specs (one per runtime_type); asserted routing outcomes:
  - `persisted_subagent_dict` → returned dict has `name`/`description`/`system_prompt` (`SubAgent` shape, langchain.agents.create_agent calleable)
  - `deep_agent` → returned runnable is `create_deep_agent` output (assert subclass `CompiledStateGraph` or similar)
  - `compiled_langgraph` → user-provided `Pregel` wrapped in `CompiledSubAgent` dict
  - `async_langgraph` → raises `NotImplementedError` with the explicit message
  - `swarm_member` → wrapped in `create_swarm(...).compile()` with `default_active_agent=spec.name` |
| `test_subagent_factory_no_nesting_cap.py` | Recursively build subagent depth 7 (`subagent7` spawns `subagent8`, ... `subagent14`) — supervisor built with `recursion_limit=50`. Assertion: deepest subagent's `task_result` returns. Also assert env var `MAX_NESTED_SUBAGENTS=5` doesn't truncate the call anymore (env var remains read but treated as advisory warning only, not cap. If removed entirely, accept any env-set value as ignored). |
| `test_subagent_factory_invokes_in_run.py` | Mirror dynamic-specialist scenario: Munin receives "Create a kerberoastable finder specialist" via chat prompt; supervisor emits a `create_subagent` tool call; the resulting subagent is invoked via `task` tool in the same run; the result unwinds to Munin's state via the ToolMessage decode (`structured_response` or last non-empty AIMessage). Assert `traceable_result` includes `iteration_count`, `subagent_run_id`, `subagent_name`. |

## Parity bar (PR-01 preserved)

| PR-01 test | Status |
|---|---|
| `test_subagent_runner_parity.py` (legacy wake runner atomicity + PROGRESS + overflow) | **Remains green** — the legacy path is not deleted in PR-07; runner subprocess and `munin_wake` MCP tool continue to spawn via `shared_state.try_claim_spawn_slot` until PR-14. |
| `test_coord_respond_loop_parity.py` | Green (supervisor unchanged in re-emitted progress events) |
| Other 5 PR-01 tests | Green |

## Deps bumped / added in this PR

None. `langgraph-swarm` (added PR-03) + deepagents provide `create_swarm` + `SubAgent`/`CompiledSubAgent`/`AsyncSubAgent` shapes.

## Rollback plan

Revert removes `subagent_factory.py` + `spec.py` + 3 tests; restores `MUNIN_MAX_NESTED_SUBAGENTS=5` constant + check in `base.py`; removes the 4 new MCP entries. Legacy subprocess-runner path untouched → revert is standalone.

## Validation plan

1. Characterization tests: 7 PR-01 + 5 PR-03 + 2 PR-05 + 4 PR-06 + 3 PR-07 tests all green.
2. CI green: ci.yml backend + e2e_lab.
3. Live-session workflow: chrome-devtools MCP — send "create a subagent that can search LDAP for kerberoastable users, wake it, and ask it to count them on the mock domain". Verify subagent creation, hand-off via `task`, result receives by supervisor in chat UI as part `subagent-presence` + `reasoning`.
4. Artifact inspection: `data/shared_state.sqlite` `agent_presence` row appears for the new subagent with state `RUNNING` → `IDLE`; `episodic` log rows carry `subagent_run_id`.
5. Parity manual check: `pytest tests/characterization/test_subagent_.py -v` after merge.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools + external MCP integration | Untouched — subagent tools come from same ToolGateway catalog (PR-05) |
| Scope/OPSEC in tool boundary | Untouched — subagent tool dispatch routes through the same boundary. Subagent inherits parent's tool whitelist OR explicitly seeds its own (`SubAgent` spec ensures only allow-listed tools). |
| Audit redaction contract | Untouched — every subagent tool call enters audit.py |
| Tool provenance | Expanded in PR-06; this PR doesn't modify tool provenance but adds subagent_trace concept (issue §5 explicit) |
| Soul human-editable | Untouched |
| Cross-session artifact pattern | Untouched (no checkpointer yet) |

## Framework verification provenance

- **SubAgent dict compiles via langchain.agents.create_agent**: DeepWiki `langchain-ai/deepagents` query confirmed.
- **CompiledSubAgent with `runnable=Pregel`**: same source — confirmed at `/wiki/langchain-ai/deepagents#2.3` Sub-Agent Delegation.
- **AsyncSubAgent `graph_id` + `url?` + `headers?`**: DeepWiki "AsyncSubAgent TypedDict" confirmed — fields per spec; raises from middleware when no async backend configured (DeepWiki source).
- **create_swarm accepts list[Pregel]; create_handoff_tool(agent_name) → BaseTool**: DeepWiki `langchain-ai/langgraph-swarm-py` query answered: `agents: list[Pregel]`, `default_active_agent`, `state_schema=SwarmState`, `SwarmState(messages, active_agent)` class (not TypedDict), handoff tool when invoked returns `Command(goto=agent_name, graph=Command.PARENT, update={"messages":[...],"active_agent":agent_name})`. We rely on this for swarm_member branch.
- **Deep Agent output is a compiled Pregel → accepted as swarm member**: same langchain-ai/langgraph-swarm-py answer — "a compiled `Pregel` object (which create_deep_agent would return) can be used directly as a swarm member".
- **Remove MAX_NESTED_SUBAGENTS**: per issue §4 explicit, replaced by LangGraph `recursion_limit` (default 25; operator-configurable; observable via stream_events).

Uncertainty remaining: AsyncSubAgent stubbing — will `create_deep_agent(async_subagents=[AsyncSubAgent spec])` accept an AsyncSubAgent spec without a URL? Need to verify the AsyncSubAgent schema shape: defaults `url` to something or raises at construction? Subagent should test in PR-07 that the stubbed `NotImplementedError` is raised by OUR factory (NOT by deepagents build) when MUNIN_LANGGRAPH_URL unset — meaning our factory never reaches the deepagents call in that case. Test asserts that path; subagent cannot modify the test assertion-without-wrapper because our factory is the gatekeeper to the deepagents call.