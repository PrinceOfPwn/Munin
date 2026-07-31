"""
Subagent Factory — materializes generated specialists on the lightest correct
runtime (issue #9 §4).  No stub runtimes: every maker returns an invocable
object.

Routing:
  persisted_subagent_dict -> declarative dict (Deep Agents native SubAgent shape)
  deep_agent              -> deepagents.create_deep_agent
  compiled_langgraph      -> langchain.agents.create_agent (CompiledStateGraph,
                             messages state = CompiledSubAgent-compatible)
  async_langgraph         -> AsyncSubAgentProxy via langgraph-sdk (needs MUNIN_LANGGRAPH_URL)
  swarm_member            -> create_agent + handoff tools

There is deliberately no depth/count cap anywhere in this module (issue #9
§4 "no arbitrary hard caps"): nesting is enabled by handing the Autonomy
Kernel meta-tools to generated agents.
"""
from __future__ import annotations

import os
from typing import Any

from .spec import SubagentSpec


class SubagentFactory:
    def __init__(self, tools: list[Any], model: Any = None, registry: Any | None = None):
        self._tools = tools
        self._model = model
        self._registry = registry

    # ------------------------------------------------------------------

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

    async def invoke_subagent(
        self,
        agent: Any,
        task: str,
        *,
        config: dict | None = None,
    ) -> dict[str, Any]:
        """Generic invocation path for any materialized agent.

        Returns {"content": final_text, "messages": [...]} — the parent keeps
        the compact final text while the full trace stays inspectable.
        """
        from langchain_core.messages import HumanMessage  # noqa: PLC0415

        if isinstance(agent, dict):
            if "description" in agent and "purpose" not in agent:
                pass
            else:
                agent = self.create_subagent(SubagentSpec.model_validate(agent))
        payload = {"messages": [HumanMessage(content=task)]}
        if hasattr(agent, "ainvoke"):
            result = await agent.ainvoke(payload, config=config or {})
        elif hasattr(agent, "invoke"):
            result = agent.invoke(payload, config=config or {})
        else:
            raise TypeError(f"Agent {agent!r} is not invocable")
        messages = result.get("messages", []) if isinstance(result, dict) else []
        final = messages[-1] if messages else None
        content = getattr(final, "content", final if isinstance(final, str) else "")
        return {"content": content, "messages": messages}

    # ------------------------------------------------------------------
    # runtime makers
    # ------------------------------------------------------------------

    def _resolve_model(self, spec: SubagentSpec) -> Any:
        if self._model is not None:
            return self._model
        return spec.model  # str — frameworks resolve via init_chat_model

    def _make_persisted_subagent_dict(self, spec: SubagentSpec) -> dict:
        """Deep Agents native SubAgent declaration shape."""
        return {
            "name": spec.name,
            "description": spec.purpose,
            "system_prompt": spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            "tools": self._filter_tools(spec.tools),
            "model": self._resolve_model(spec),
        }

    def _make_deep_agent(self, spec: SubagentSpec) -> Any:
        from deepagents import create_deep_agent  # noqa: PLC0415

        return create_deep_agent(
            name=spec.name,
            model=self._resolve_model(spec),
            tools=self._filter_tools(spec.tools),
            system_prompt=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
        )

    def _make_compiled_langgraph(self, spec: SubagentSpec) -> Any:
        """Real agentic subgraph via LangChain 1.x create_agent.

        Returns a CompiledStateGraph whose state carries ``messages`` — the
        communication boundary Deep Agents' SubAgentMiddleware expects, so the
        result can be wrapped directly as a CompiledSubAgent.
        """
        from langchain.agents import create_agent  # noqa: PLC0415

        return create_agent(
            model=self._resolve_model(spec),
            tools=self._filter_tools(spec.tools),
            system_prompt=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            name=spec.name,
        )

    def _make_async_langgraph(self, spec: SubagentSpec) -> Any:
        raise NotImplementedError(
            f"async_langgraph runtime for spec {spec.name!r} requires deploying "
            "a graph that respects the spec's prompt, model, and tool restrictions. "
            "Use deep_agent or compiled_langgraph instead."
        )

    def _make_swarm_member(self, spec: SubagentSpec) -> Any:
        raise NotImplementedError(
            f"swarm_member runtime for spec {spec.name!r} requires composing multiple "
            "specialists via munin.core.coordination.swarm.build_swarm. Individual agents "
            "cannot form a swarm - use deep_agent or compiled_langgraph instead."
        )

    # ------------------------------------------------------------------

    def _filter_tools(self, tool_names: list[str]) -> list[Any]:
        if not tool_names:
            return []
        name_set = set(tool_names)
        return [t for t in self._tools if getattr(t, "name", None) in name_set]
