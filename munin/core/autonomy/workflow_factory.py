"""Workflow Factory — compiles WorkflowSpec into a LangGraph Pregel."""
from __future__ import annotations
import operator
from typing import Annotated, Any
from .workflow_spec import WorkflowSpec


def _make_state_schema(spec: WorkflowSpec) -> type:
    from langgraph.graph import MessagesState
    if not spec.custom_state:
        return MessagesState
    annotations: dict = {"messages": list}
    type_map = {"string": str, "integer": int, "float": float, "boolean": bool, "list": list, "dict": dict}
    for field in spec.custom_state:
        py_type = type_map.get(field.type, str)
        if field.reducer == "append":
            annotations[field.name] = Annotated[list, operator.add]
        else:
            annotations[field.name] = py_type
    from typing import TypedDict
    return TypedDict(f"{spec.name}_State", annotations)  # type: ignore


def _make_deterministic_node(node):
    if node.python_code:
        code = compile(node.python_code, f"<node:{node.name}>", "exec")
        def fn(state: dict) -> dict:
            ns = {"state": state}
            exec(code, {}, ns)
            return ns.get("result", state)
        fn.__name__ = node.name
        return fn
    def passthrough(state: dict) -> dict:
        return state
    passthrough.__name__ = node.name
    return passthrough


def _make_agent_node(node, tools: list):
    from langchain_core.messages import AIMessage
    def stub(state: dict) -> dict:
        return {"messages": state.get("messages", []) + [AIMessage(content=f"[{node.name}] processing")]}
    stub.__name__ = node.name
    return stub


def create_workflow(spec: WorkflowSpec, *, tools: list[Any] | None = None, sandbox=None) -> Any:
    """Compile a WorkflowSpec into a LangGraph CompiledStateGraph."""
    from langgraph.graph import StateGraph
    from langgraph.prebuilt import ToolNode

    tools = tools or []
    state_schema = _make_state_schema(spec)
    builder = StateGraph(state_schema)

    tool_map = {getattr(t, "name", str(t)): t for t in tools}

    for node in spec.nodes:
        if node.kind == "deterministic":
            builder.add_node(node.name, _make_deterministic_node(node))
        elif node.kind == "agent":
            node_tools = [tool_map[n] for n in node.tools if n in tool_map]
            builder.add_node(node.name, _make_agent_node(node, node_tools))
        elif node.kind == "tool":
            node_tools = [tool_map[n] for n in node.tools if n in tool_map]
            builder.add_node(node.name, ToolNode(node_tools))

    for edge in spec.edges:
        if edge.kind == "static":
            builder.add_edge(edge.src, edge.dst)
        elif edge.kind == "conditional":
            cmap = edge.condition_map
            key = edge.condition_key
            def make_cond(k, m):
                def cond(state): return m.get(str(state.get(k, "")), "__end__")
                return cond
            builder.add_conditional_edges(edge.src, make_cond(key, cmap), cmap)
        elif edge.kind == "send":
            from langgraph.types import Send
            fkey = edge.fanout_key
            dst = edge.dst
            def make_fanout(k, d):
                def fanout(state): return [Send(d, {"item": x, "index": i}) for i, x in enumerate(state.get(k, []))]
                return fanout
            builder.add_conditional_edges(edge.src, make_fanout(fkey, dst))

    if spec.entry_point:
        builder.set_entry_point(spec.entry_point)
    elif spec.nodes:
        builder.set_entry_point(spec.nodes[0].name)

    for fp in spec.finish_points:
        builder.set_finish_point(fp)

    interrupt_before = [i.before_node for i in spec.interrupts] or None

    checkpointer = None
    if spec.checkpointer == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
    elif spec.checkpointer == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            import os
            db = os.environ.get("MUNIN_CHECKPOINT_DB", "data/langgraph_checkpoints.sqlite")
            os.makedirs(os.path.dirname(db), exist_ok=True)
            checkpointer = SqliteSaver.from_conn_string(db)
        except Exception:
            pass

    return builder.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)
