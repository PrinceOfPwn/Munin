# PR-09 — Workflow Factory (declarative DSL → compiled Pregel as CompiledSubAgent)

- **Head**: `raven-mind/migration-issue9/pr-09-workflow-factory`
- **Base**: `raven-mind/migration-issue9/pr-08-agent-registry`
- **Open architectural questions**: None. Stage 0 confirmed via Context7 `/websites/langchain_oss_python_langgraph` "StateGraph add_node add_edge add_conditional_edges compile checkpointer Send dynamic fan-out Annotated list operator.add reducer Command goto update resume interrupt subgraph Pregel RunnableConfigurable thread_id" — confirmed `StateGraph(State)` + `add_node(node)` + `add_edge`/`add_conditional_edges` + `.compile(checkpointer=...)`. `Send(node, state_subset)` for dynamic fan-out. `Annotated[list, operator.add]` reducer for aggregation. `Command(update=, goto=)` + `graph=Command.PARENT` for subgraph state propagation. Compiled subgraph used as node via `add_node("node", subgraph)`.

---

## Goal

Evolve `graph_forge` from a JSON-config-that-gets-re-executed-as-ReAct into a real LangGraph Workflow Factory emitting a compiled `Pregel` runnable usable as a `CompiledSubAgent`. Support both declarative DSL authored by Munin AND sandboxed generated Python when declarative is insufficient. Issue §6 explicit + acceptance #8 ("Generated workflows compile to LangGraph and can run as `CompiledSubAgent`s").

## Acceptance title (one line)

Munin's supervisor emits a spec for a multi-node workflow; `create_workflow(spec)` returns a compiled LangGraph `Pregel` with ≥1 deterministic node + ≥1 agent node + ≥1 conditional routing + ≥1 fan-out Send; invoking the supervisor on a task that uses the workflow as a `CompiledSubAgent` returns the expected `structured_response` via `task` tool's `ToolMessage`.

## Issue required end-to-end scenarios this PR partially unlocks

**Dynamic workflow** (issue E2E #4): unlocks full "Munin generates multi-node workflow → compiles → invoked as CompiledSubAgent → contains ≥1 deterministic + ≥1 agent node → produces structured_response" cycle. (Cross-session persistence comes in PR-10.)

---

## Files added

| Path | What |
|---|---|
| `munin/core/autonomy/workflow_factory.py` | `create_workflow(spec: WorkflowSpec) -> Pregel` — compiles a Munin-authored declarative graph spec to a real LangGraph `Pregel`. Supports deterministic nodes, agent nodes, tools, conditional routing, loops, fan-out via Send, Command goto/update, handoffs, subgraphs, interrupts, custom state + Annotated reducers. Sandboxed-Python fallback via existing `munin/subagents/sandbox.py` AST guard + safe_exec when the spec includes user-supplied `python_code` blocks beyond the declarative DSL. |
| `munin/core/autonomy/workflow_spec.py` | `WorkflowSpec` pydantic model + `Node`, `Edge`, `ConditionalEdge`, `SendFanOut`, `StepInterrupt`, `CustomState` types. Defined to mirror LangGraph StateGraph primitives, not custom ones. |
| `tests/characterization/test_workflow_factory_declarative.py` | Declarative DSL spec → compiled Pregel; asserts graph node count, edges, start/end. |
| `tests/characterization/test_workflow_factory_compiled_subagent.py` | Workflow compiled; inserted as `CompiledSubAgent` to `create_deep_agent(subagents=...)`; supervisor invocation routes via `task` tool which sees the workflow runnable + returns `structured_response` per TypedDict contract. |
| `tests/characterization/test_workflow_factory_fanout.py` | Workflow with `Send("worker", {"index": 0/1/2/3/4})` settles via `Annotated[list, operator.add]` aggregation in parent state. Resulting aggregate has 5 entries. |
| `tests/characterization/test_workflow_factory_sandbox_fallback.py` | Spec where declarative insufficient (arbitrary node function); user provides `python_code="<node body>"` blocks. Sandbox parses + runs AST guard + safe_exec; workflow includes the resulting deterministic node. |

## Files modified

| Path | What changes |
|---|---|
| `munin/mcp/tools/graph_forge_tool.py` | Repoint MCP `graph_forge` tool to `workflow_factory.create_workflow`. The old behaviour (write raw JSON to `generated_graphs`) is preserved via a parameter `legacy_format=True` default false → reads from `generated_graphs` table for backward-compat rows; new writes go to `workflow_registry` (introduced in PR-10). |
| `munin/core/autonomy/subagent_factory.py` | When `spec.runtime_type == "compiled_langgraph"` AND spec.definition contains a workflow spec, route via `workflow_factory.create_workflow(spec.definition)` rather than expecting user-supplied `Pregel`. (Skip if user directly passed a precompiled runnable.) |
| `pyproject.toml` | No new deps (langgraph already added; state graphs are native). |

## Files deleted

None. The old graph_forge path (`subagents/graph_forge.py`) becomes the bridge for legacy `generated_graphs` rows; new generations use `workflow_factory.create_workflow` via `graph_forge_tool.py` MCP entry.

---

## Per-class/function behavior

### `create_workflow`

Framework provenance: Context7 LangGraph graph-api page.

```python
def create_workflow(spec: WorkflowSpec) -> CompiledStateGraph:
    """Build a LangGraph StateGraph from the spec:
        builder = StateGraph(spec.state_schema)
        for node in spec.nodes:
            if node.kind == "deterministic": builder.add_node(node.name, node.fn or sandbox.safe_exec(node.python_code))
            elif node.kind == "agent": builder.add_node(node.name, _make_agent_node(node))  # uses create_agent or create_deep_agent
            elif node.kind == "tool": builder.add_node(node.name, ToolNode(node.tools))
        for edge in spec.edges:
            if edge.kind == "static": builder.add_edge(edge.src, edge.dst)
            elif edge.kind == "conditional": builder.add_conditional_edges(edge.src, edge.condition_fn_or_key, edge.mapping)
            elif edge.kind == "send": builder.add_conditional_edges(edge.src, lambda s: [Send(target, payload) for payload in edge.fanout_fn(s)])
        compile_kwargs = {"checkpointer": spec.checkpointer} if spec.checkpointer else {}
        graph = builder.compile(**compile_kwargs)
        return graph
    """
```

### `WorkflowSpec` pydantic shape

```python
class WorkflowSpec(BaseModel):
    name: str
    description: str
    state_schema: type | TypedDict  # MUST include `messages: Annotated[list, operator.add]` per CompiledSubAgent contract
    nodes: list[Node]
    edges: list[Edge]
    response_format: type | None  # pydantic model for structured_response
```

### `send_fanout` reducer

`State.aggregate: Annotated[list, operator.add]` — confirmed Context7 state-with-reducer example. Used in `test_workflow_factory_fanout.py`.

### `task` tool output (unchanged)

DeepWiki `langchain-ai/deepagents` PR-03 record: `task` returns `Command(update={**state, "messages":[ToolMessage(content=JSON.dumps(structured_response) or AIMessage.text)]})`. Workflow's `structured_response` field on its state terminates the run cleanly.

---

## Tests added

| Path | Assertion contract |
|---|---|
| `test_workflow_factory_declarative.py` | Spec with 3 deterministic nodes (`a → b → c`) + 1 agent node (`d`) + 1 conditional edge (`b → d if state.has_more else END`) → compiled Pregel has node count = 4 (deterministic + agent); a + b + c + d nodes in `graph.nodes`; edges start with START → a; a → b; b's conditional maps to {True:"d", False:END}; traceable workflow `.ainvoke({"messages": [...]})` returns `{"messages":[...]}` with workflow output appended. |
| `test_workflow_factory_compiled_subagent.py` | Insert the compiled workflow into `create_deep_agent(subagents=[CompiledSubAgent(name="recon_workflow", description="...", runnable=workflow_pregel)])`; supervisor's `task` tool invokes it; final ToolMessage content = JSON of `workflow.state["structured_response"]` (fixture uses pydantic `ReconSummary` model). |
| `test_workflow_factory_fanout.py` | Spec with: `node_b` → conditional returning `[Send("worker", {"index": i}) for i in range(5)]`; `node_collect` with `state.aggregate: Annotated[list, operator.add]`; worker returns `{"aggregate": ["w"+str(s["index"])]}`. Invoke workflow; assert `state["aggregate"] == ["w0", "w1", "w2", "w3", "w4"]` exactly.
- Also assert that one worker raising `RuntimeError` does NOT fail the entire batch (workflow continues, partial result remains accessible via state `errors_count`). |
| `test_workflow_factory_sandbox_fallback.py` | Spec with `Node.kind="deterministic"` and `python_code="return {'aggregate': ['from_python']}"` (existing AST guard OK). `create_workflow` runs `sandbox.safe_exec` on the code, attaches as node fn; compiled graph returns `state["aggregate"] == ["from_python"]` after a single-step cast. (Behaviour unchanged from `munin/subagents/sandbox.py`'s contract; PR-01 `test_sandbox.py` covers sandbox basics.) |

## Parity bar (PR-01 preserved)

All 7 PR-01 + subsequent PRs green. `graph_forge`'s old generated_graphs rows remain readable by legacy runner until PR-14 → compat parameter `legacy_format=True` keeps the old path tested by `test_graph_persist.py` (existing repo test).

## Deps bumped / added

None. `langgraph>=0.2.40` already covers StateGraph + Send + Command.

## Rollback plan

Revert removes `workflow_factory.py` + spec file + 4 tests; restores `graph_forge_tool.py` to direct `generated_graphs` JSON write without the bridging. Standalone.

## Validation plan

1. Characterization tests: all PRs-1-to-07 + 4 new tests green.
2. CI green.
3. Live-session workflow: chrome-devtools MCP — ask Munin "create a recon workflow with: (1) deterministic node that enumerates hosts; (2) agent node that runs nmap on each; (3) aggregate via fan-out Send; collect summaries." Workflow emits Visible UIMessage parts via the BFF (PR-02's `forge-stage` + `subagent-presence` parts populate). Assert the workflow structurally_response field rendered.
4. Artifact inspection: `data/shared_state.sqlite` `episodic` table shows `workflow_step` events with `worker_index` for fan-out workers.
5. Parity manual check: live run succeeded (i.e. no regressions in `test_graph_persist.py`).

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools | Untouched — workflows reference Bound tools from same catalog |
| Scope/OPSEC at tool boundary | Untouched — workflow tool calls go through the same gateway as direct supervisor tool calls. Deterministic nodes are NOT side-effectful by default (no network/files unless they explicitly invoke a scoped LangChain tool). |
| Audit redaction contract | Untouched — every tool call (even inside workflow) passes through ToolNode + audit |
| Tool provenance | Untouched |
| Soul human-editable | Untouched |
| Cross-session artifact pattern | Untouched (Workflow Registry cross-session persistence comes in PR-10) |

## Framework verification provenance

- **StateGraph + Send + Command + subgraph-as-node**: Context7 LangGraph graph-api examples (URL signatures verified). State-with-reducer parent graph + subgraph shows `Annotated[str, operator.add]` shape for `foo`; carried over to our `aggregate` field directly.
- **`graph=Command.PARENT` + reducer aggregation across subgraph boundary**: same Context7 page.
- **`task` tool return shape**: DeepWiki `langchain-ai/deepagents` confirmed at PR-03 record.
- **sandbox.safe_exec contract**: PR-01 `test_sandbox.py` already in repo — workflow's sandbox fallback reuses existing path; no redefinition.

Uncertainty remaining: whether `create_workflow` builds a workflow graph that contains both deterministic nodes (selected by `state_schema` typing) AND agent nodes (selected by spec only). The spec distinguishes by `node.kind`; LangGraph StateGraph accepts any node fn. No framework uncertainty; spec authoring is the only remaining uncertainty and the spec authorship happens via `WorkflowSpec` field types enforced at validation.