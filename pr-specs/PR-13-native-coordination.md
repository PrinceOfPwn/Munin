# PR-13 — Native coordination (swarm handoffs, Command goto/update, AsyncSubAgent pressure)

- **Head**: `raven-mind/migration-issue9/pr-13-native-coordination`
- **Base**: `raven-mind/migration-issue9/pr-12-send-workers`
- **Open architectural questions**: None. DeepWiki `langchain-ai/langgraph-swarm-py` query "What is the exact public API of langgraph-swarm-py" answered the swarm contract: `create_swarm(agents: list[Pregel], default_active_agent: str, state_schema=SwarmState)`, `create_handoff_tool(agent_name)` returns BaseTool with `handoff_to_agent` returning `Command(goto=agent_name, graph=Command.PARENT, update={"messages": [...+ToolMessage confirming transfer], "active_agent": agent_name})`. `SwarmState` extends `MessagesState`; defaults `messages: list[BaseMessage]` + `active_agent: str|None`.

---

## Goal

Adopt LangGraph native coordination primitives for inter-agent communication:
1. **Swarm handoffs** — peer specialists hand control via `create_handoff_tool(agent_name)` → `Command(goto=, update={active_agent, messages})`.
2. **Command goto/update** — supervisor delegates via `task` (parent/child pattern already in deepagents). The PR-13 piece focuses on **peer** handoff via swarm graph.
3. **AsyncSubAgent pressure** — multi-task scale test: many AsyncSubAgents submitted concurrently; verify `start_async_task`, `check_async_task` per each task, no executor contention failures; cancels propagate.
Indexes issue acceptance #10 ("Agents can use parent/child delegation, handoffs, and shared state") + E2E #6 ("Communication").

## Acceptance title (one line)

Two specialists (e.g. `ldap_specialist` + `kerberoast_specialist`) hand off via `create_handoff_tool`; both see shared `messages`; sub-spec publishes to `shared_intel`, both can read via `query_shared_intel`; AsyncSubAgent brute (10+ concurrent) survives 5 cancellations + 5 completions without loss.

## Issue required end-to-end scenarios this PR partially unlocks

**Communication** (issue E2E #6): FULL unlocker via swarm graph + inter-agent messaging via shared_intel preserve. (PR-07's "Dynamic specialist" already proved parent/child delegation; here we alias the broader coordination surface.)

---

## Files added

| Path | What |
|---|---|
| `munin/core/coordination/swarm.py` | `build_swarm(specialists: list[Pregel], default_active_agent: str, state_schema=SwarmState) -> CompiledStateGraph` — wraps `create_swarm` from `langgraph-swarm`. Adds Munin's `SwarmState` extension with `shared_intel_published: Annotated[list, operator.add]` to surface findings published-to-shared_intel by members. |
| `munin/core/coordination/handoff_tools.py` | `make_handoff_tool(agent_name: str) -> BaseTool` — thin wrapper around `langgraph_swarm.create_handoff_tool`; returns the tool to register on parent specialists' tool lists. |
| `tests/characterization/test_swarm_handoff.py` | Two specialists built; each registers `handoff_to_<other>` tool; specialist A invokes handoff; `Command(goto="specialist_b", update={"messages":[...], "active_agent":"specialist_b"})` routes; specialist_b sees A's last messages via SwarmState.messages; specialist_b publishes to shared_intel via `publish_shared_intel` meta-tool; assert both can query it. |
| `tests/characterization/test_swarm_back_to_originator.py` | Round-trip: A → B → A. Verify B can hand back to A; A re-receives state preserved (`shared_intel_published` accumulated). |
| `tests/characterization/test_async_subagent_pressure.py` | Submit 10 AsyncSubAgents concurrently; each `start_async_task` returns task_id; poll `check_async_task` over 30s; assert 5 finish + 5 cancellations via `cancel_async_task` propagate; partial snapshots preserved per cancelled task. |
| `tests/characterization/test_command_parent_state_update.py` | Subgraph using `Command(update=, goto=, graph=Command.PARENT)` propagates state upstream into parent's `aggregate` field (reuse PR-09 reducer contract); assert parent state mutated by child's Command + not by direct field update. |

## Files modified

| Path | What changes |
|---|---|
| `munin/core/autonomy/subagent_factory.py` | `runtime_type="swarm_member"` branch (PR-07 partly stubbed) now wraps with `build_swarm(...)` from `munin/core/coordination/swarm.py`. |
| `munin/core/autonomy/agent_registry.py` | When rebuilding from definition, also rebuilds handoff tools from subagent spec's `peer_handoffs` list (mapping of `other_agent_id` → enabled bool). PR-08 already persists definition; PR-13 adds the swarm dimension to definition storage. |
| `munin/mcp/tools/munin_tools.py` | Add `create_handoff(from_agent, to_agent)` meta-tool for the supervisor's general use — surfaces swarm primitives as Autonomy Kernel capability. |
| `.github/workflows/live-session.yml` | Add `MUNIN_SWARM_DEFAULT_AGENT=munin_supervisor` env so swarm graphs pick a deterministic default active agent. |

## Files deleted

None in PR-13 (deletion blooms in PR-14 for legacy runner subprocess + wake queue).

---

## Per-function behavior

### `build_swarm`

```python
from langgraph_swarm import create_swarm, SwarmState

def build_swarm(specialists: list[Pregel], default_active_agent: str) -> CompiledStateGraph:
    """Wrap create_swarm; subagents registered as swarm members; Munin adds
    extended state schema (SwarmState + additional fields)."""
    return create_swarm(agents=specialists, default_active_agent=default_active_agent).compile()
```

### `make_handoff_tool`

```python
from langgraph_swarm import create_handoff_tool

def make_handoff_tool(target_agent_name: str) -> BaseTool:
    """Single handoff tool. Tool name = `handoff_to_<target_agent_name>`."""
    return create_handoff_tool(target_agent_name)
```

## Tests added

Per Files Added table. The shared_intel accumulation test asserts:

```python
# After A runs, publishes via `publish_shared_intel` tool:
swarm.ainvoke(initial_state)
assert state["shared_intel_published"][-1] == {"agent": "A", "finding": "krb5_user foo"}
# After handoff to B + B reads it via `query_shared_intel`:
assert swarm.state["messages"][-1].content contains reference to the published finding
# After B handoff back to A:
assert swarm.state["active_agent"] == "A"
```

## Parity bar (PR-01 preserved)

All tests PR-01..PR-12 green. Specifically:
- The legacy `agent_messages` Mailbox path (PR-01 `test_subagent_runner_parity.py`) continues running — revert of PR-13 does not happen because that's a deletion path (PR-14). Here PR-13 just adds the swarm surface alongside the legacy path.

## Deps bumped / added

None — `langgraph-swarm` already added PR-03.

## Rollback plan

Revert removes `munin/core/coordination/{swarm,handoff_tools}.py` + 5 PR-13 tests + workflow env addition; restores PR-07's swarm_member subagent factory branch to `NotImplementedError` for swarm graph. Standalone.

## Validation plan

1. Characterization tests: PR-01..PR-12 + 5 PR-13 tests green.
2. CI green.
3. Live-session workflow: chrome-devtools MCP — ask Munin "have ldap_specialist and kerberoast_specialist collaborate: ldap finds the target DN, hands off to kerberoast who enumerates SPNs on it, hands back to ldap with the result, ldap publishes the finding to shared_intel." Verify both specialists' traces surface in `subagent-presence` parts; final message from ldap_specialist visible in chat; `shared_intel` populated.
4. Artifact inspection: `data/shared_state.sqlite.shared_intel` AND `langgraph_checkpoints.sqlite` both have entries from both specialists.
5. AsyncSubAgent pressure: chrome-devtools MCP finds the run summary admitting all 10 tasks + 5 cancellations + 5 completions via the live stream.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| Shared intel + task semantics (issue §9 "durable offensive findings") | **Preserved explicitly** — `publish_shared_intel` meta-tool callable from any swarm member; provenance of who published a finding carried via `shared_intel.published_by_agent` column (existing schema). |
| All other invariants | Untouched. Tool call → audit. Agent identity persists via agent_registry (PR-08). |

## Framework verification provenance

- create_swarm + SwarmState + create_handoff_tool contract: DeepWiki `langchain-ai/langgraph-swarm-py` query confirmed in PR-08 records.
- Deep Agent output is Pregel → integrable as swarm member: same DeepWiki answer.
- `Command(goto=, graph=Command.PARENT, update={...})`: Context7 LangGraph (PR-09 record).

Uncertainty remaining: zero."}

for base in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do base=$(echo "$base" | sed 's/^0*//'); echo "Missing pattern: base=$base"; done
```