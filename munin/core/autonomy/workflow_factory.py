# tags: [workflow, langgraph, subagent, core, runtime, WorkflowFactory, WorkflowSpec, deterministic_node, StateGraph, MessagesState, compile_workflow, _make_state_schema, send-edges, conditional-edges, agent_node]
"""
Workflow Factory — compiles a declarative ``WorkflowSpec`` into a real
LangGraph runnable (issue #9 §6).  No stub nodes:

* ``agent`` nodes run a genuine LangChain ``create_agent`` subgraph (LLM +
  tools) — the compiled workflow is itself a valid ``CompiledSubAgent``
  because the state always carries ``messages`` with the ``add_messages``
  reducer.
* ``deterministic`` nodes execute their Python through the same AST guard +
  restricted-builtins sandbox as forged tools (``munin.subagents.sandbox``) —
  raw ``exec`` of generated code is never used.
* ``send`` edges use LangGraph ``Send`` fan-out; fan-in happens through
  explicit ``append`` reducers declared in ``custom_state``.
* ``conditional`` edges map condition *keys* to destinations correctly
  (the condition function returns a key of the path map, never a value).
"""
from __future__ import annotations

import json
import logging
import operator
from typing import Annotated, Any

from .workflow_spec import WorkflowSpec

logger = logging.getLogger(__name__)

_NODE_TIMEOUT_SECONDS = 60


def _make_state_schema(spec: WorkflowSpec) -> type:
    from langgraph.graph import MessagesState  # noqa: PLC0415
    from langgraph.graph.message import add_messages  # noqa: PLC0415

    if not spec.custom_state:
        return MessagesState
    annotations: dict[str, Any] = {"messages": Annotated[list, add_messages]}
    type_map = {"string": str, "integer": int, "float": float, "boolean": bool, "list": list, "dict": dict}
    for field in spec.custom_state:
        py_type = type_map.get(field.type, str)
        if field.reducer in {"append", "add"}:
            annotations[field.name] = Annotated[list, operator.add]
        else:
            annotations[field.name] = py_type
    from typing import TypedDict

    return TypedDict(f"{spec.name}_State", annotations)  # type: ignore[operator]


def _serialize_state(state: dict) -> str:
    """JSON-safe snapshot of node input state for sandboxed node code."""
    from langchain_core.messages import messages_to_dict  # noqa: PLC0415

    out: dict[str, Any] = {}
    for key, value in state.items():
        if key == "messages":
            try:
                out[key] = messages_to_dict(value)
            except Exception:  # noqa: BLE001
                out[key] = [str(m) for m in value]
        else:
            try:
                json.dumps(value)
                out[key] = value
            except (TypeError, ValueError):
                out[key] = str(value)
    return json.dumps(out, default=str)


def _make_deterministic_node(node):
    """Sandboxed deterministic node.

    The node code receives a ``state`` dict (messages serialized to plain
    dicts) and must assign ``result`` — a dict of state updates.  The code is
    AST-validated once at build time and executed per invocation through the
    restricted-builtins sandbox.
    """
    from ...subagents.sandbox import _validate_ast, run_code  # noqa: TID252, PLC0415

    if not node.python_code:
        def passthrough(state: dict) -> dict:
            return {}

        passthrough.__name__ = node.name
        return passthrough

    import ast

    try:
        tree = ast.parse(node.python_code, mode="exec")
        _validate_ast(tree, set())
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"deterministic node {node.name!r} rejected by AST guard: {exc}") from exc

    def fn(state: dict) -> dict:
        harness = (
            "import json as _json\n"
            f"state = _json.loads({_serialize_state(state)!r})\n"
            f"{node.python_code}\n"
        )
        outcome = run_code(harness, allowed_imports=None, timeout_seconds=_NODE_TIMEOUT_SECONDS)
        if not outcome.ok:
            raise RuntimeError(f"deterministic node {node.name!r} failed: {outcome.error}")
        result = outcome.return_value
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise TypeError(
                f"deterministic node {node.name!r} must assign a dict to `result`, "
                f"got {type(result).__name__}"
            )
        return result

    fn.__name__ = node.name
    return fn


def _make_agent_node(node, tools: list, model: Any):
    """Real agent node: a LangChain create_agent subgraph invoked per step."""
    from langchain.agents import create_agent  # noqa: PLC0415

    agent = create_agent(
        model=model or node.model,
        tools=tools,
        system_prompt=node.system_prompt or f"You are workflow node {node.name}.",
        name=node.name,
    )

    async def agent_node(state: dict) -> dict:
        incoming = list(state.get("messages", []))
        result = await agent.ainvoke({"messages": incoming})
        produced = result.get("messages", [])[len(incoming):]
        return {"messages": produced}

    agent_node.__name__ = node.name
    return agent_node


def create_workflow(
    spec: WorkflowSpec,
    *,
    tools: list[Any] | None = None,
    model: Any = None,
    checkpointer: Any = None,
) -> Any:
    """Compile a ``WorkflowSpec`` into a LangGraph ``CompiledStateGraph``."""
    from langgraph.graph import END, StateGraph  # noqa: PLC0415
    from langgraph.prebuilt import ToolNode  # noqa: PLC0415

    tools = tools or []
    state_schema = _make_state_schema(spec)
    builder = StateGraph(state_schema)

    tool_map = {getattr(t, "name", str(t)): t for t in tools}

    for node in spec.nodes:
        if node.kind == "deterministic":
            builder.add_node(node.name, _make_deterministic_node(node))
        elif node.kind == "agent":
            node_tools = [tool_map[n] for n in node.tools if n in tool_map]
            builder.add_node(node.name, _make_agent_node(node, node_tools, model))
        elif node.kind == "tool":
            node_tools = [tool_map[n] for n in node.tools if n in tool_map]
            builder.add_node(node.name, ToolNode(node_tools))

    for edge in spec.edges:
        if edge.kind == "static":
            builder.add_edge(edge.src, edge.dst or END)
        elif edge.kind == "conditional":
            key = edge.condition_key
            path_map = dict(edge.condition_map)

            def make_cond(k: str, mapping: dict[str, str]):
                def cond(state: dict) -> str:
                    value = str(state.get(k, ""))
                    # Return a KEY of the path map (LangGraph contract), never
                    # a destination directly; unknown values end the graph.
                    if value in mapping:
                        return value
                    return "__end__"

                return cond

            builder.add_conditional_edges(
                edge.src, make_cond(key, path_map), {**path_map, "__end__": END}
            )
        elif edge.kind == "send":
            from langgraph.types import Send  # noqa: PLC0415

            fanout_key = edge.fanout_key
            dst = edge.dst

            def make_fanout(k: str, target: str):
                def fanout(state: dict) -> list:
                    return [
                        Send(target, {"item": item, "index": i})
                        for i, item in enumerate(state.get(k, []))
                    ]

                return fanout

            builder.add_conditional_edges(edge.src, make_fanout(fanout_key, dst))

    if spec.entry_point:
        builder.set_entry_point(spec.entry_point)
    elif spec.nodes:
        builder.set_entry_point(spec.nodes[0].name)

    for fp in spec.finish_points:
        builder.set_finish_point(fp)

    interrupt_before = [i.before_node for i in spec.interrupts] or None

    if checkpointer is None and spec.checkpointer == "sqlite":
        checkpointer = _default_sqlite_checkpointer()
    elif checkpointer is None and spec.checkpointer == "memory":
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def _default_sqlite_checkpointer() -> Any:
    """Default checkpointer for generated workflows.

    Mirrors ``supervisor.make_checkpointer``: ``AsyncSqliteSaver.from_conn_string``
    returns an async context manager, not a ``BaseCheckpointSaver``, so we use
    ``MemorySaver`` for now. Truly durable cross-workflow checkpointing belongs
    to a follow-up PR (see IMPLEMENTATION_ROADMAP.md).
    """
    from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

    return MemorySaver()
