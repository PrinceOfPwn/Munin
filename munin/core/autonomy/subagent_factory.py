"""
Subagent Factory — 5-way runtime routing. Final version (PR-13).

Routing:
  persisted_subagent_dict -> dict
  deep_agent              -> create_deep_agent
  compiled_langgraph      -> StateGraph.compile()
  async_langgraph         -> AsyncSubAgentProxy (needs MUNIN_LANGGRAPH_URL)
  swarm_member            -> swarm with handoff tools
"""
from __future__ import annotations
import os
from typing import Any
from .spec import SubagentSpec


class SubagentFactory:
    def __init__(self, tools: list[Any], registry: Any | None = None):
        self._tools = tools
        self._registry = registry

    def create_subagent(self, spec: SubagentSpec) -> Any:
        router = {
            "persisted_subagent_dict": self._make_persisted_subagent_dict,
            "deep_agent": self._make_deep_agent,
            "compiled_langgraph": self._make_compiled_langgraph,
            "async_langgraph": self._make_async_langgraph,
            "swarm_member": self._make_swarm_member,
        }
        maker = router.get(spec.runtime_type)
        if maker is None:
            raise ValueError(f"Unknown runtime_type: {spec.runtime_type!r}")
        return maker(spec)

    def _make_persisted_subagent_dict(self, spec: SubagentSpec) -> dict:
        return {
            "name": spec.name,
            "purpose": spec.purpose,
            "system_prompt": spec.system_prompt,
            "model": spec.model,
            "tools": self._filter_tools(spec.tools),
            "runtime_type": "persisted_subagent_dict",
        }

    def _make_deep_agent(self, spec: SubagentSpec) -> Any:
        try:
            from deepagents import create_deep_agent
        except ImportError:
            raise ImportError("deepagents required for deep_agent runtime")
        return create_deep_agent(
            name=spec.name,
            model=spec.model,
            tools=self._filter_tools(spec.tools),
            system_message=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
        )

    def _make_compiled_langgraph(self, spec: SubagentSpec) -> Any:
        from langgraph.graph import StateGraph, MessagesState
        from langgraph.prebuilt import ToolNode
        from langchain_core.messages import AIMessage

        tools = self._filter_tools(spec.tools)
        builder = StateGraph(MessagesState)

        def agent_node(state):
            return {"messages": [AIMessage(content=f"[{spec.name}] processing")]}

        builder.add_node("agent", agent_node)
        if tools:
            builder.add_node("tools", ToolNode(tools))
            builder.add_edge("agent", "tools")
            builder.add_edge("tools", "agent")
        builder.set_entry_point("agent")
        builder.set_finish_point("agent")
        return builder.compile()

    def _make_async_langgraph(self, spec: SubagentSpec) -> Any:
        url = os.environ.get("MUNIN_LANGGRAPH_URL", "")
        if not url:
            raise NotImplementedError(
                "AsyncSubAgent requires MUNIN_LANGGRAPH_URL. "
                "Start the LangGraph server first (scripts/langgraph_start.sh)."
            )
        try:
            from langgraph_sdk import get_sync_client
        except ImportError:
            raise ImportError("langgraph-sdk required for async_langgraph runtime")

        client = get_sync_client(url=url)

        class AsyncSubAgentProxy:
            def __init__(self, spec, client):
                self.name = spec.name
                self._spec = spec
                self._client = client

            def invoke(self, state: dict) -> dict:
                thread = self._client.threads.create()
                return self._client.runs.wait(
                    thread["thread_id"], assistant_id="munin_supervisor", input=state
                )

            async def ainvoke(self, state: dict) -> dict:
                return self.invoke(state)

        return AsyncSubAgentProxy(spec, client)

    def _make_swarm_member(self, spec: SubagentSpec) -> Any:
        try:
            from munin.core.coordination.handoff_tools import make_handoff_tools_for_agents
            handoff_tools = make_handoff_tools_for_agents(spec.tools)
        except ImportError:
            handoff_tools = []

        all_tools = self._filter_tools(spec.tools) + handoff_tools

        try:
            from deepagents import create_deep_agent
            return create_deep_agent(
                name=spec.name,
                model=spec.model,
                tools=all_tools,
                system_message=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            )
        except ImportError:
            from langgraph.graph import StateGraph, MessagesState
            from langchain_core.messages import AIMessage
            builder = StateGraph(MessagesState)
            def stub(state):
                return {"messages": [AIMessage(content=f"[{spec.name}] swarm stub")]}
            builder.add_node("agent", stub)
            builder.set_entry_point("agent")
            builder.set_finish_point("agent")
            return builder.compile()

    def _filter_tools(self, tool_names: list[str]) -> list[Any]:
        if not tool_names:
            return self._tools
        name_set = set(tool_names)
        return [t for t in self._tools if getattr(t, "name", None) in name_set]
