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
        url = os.environ.get("MUNIN_LANGGRAPH_URL", "")
        if not url:
            raise NotImplementedError(
                "async_langgraph requires MUNIN_LANGGRAPH_URL "
                "(start the LangGraph server: scripts/langgraph_start.sh)"
            )
        from langgraph_sdk import get_client  # noqa: PLC0415

        client = get_client(url=url)
        model = self._resolve_model(spec)

        class AsyncSubAgentProxy:
            """Preview async-subagent adapter (launch/wait through Agent Protocol)."""

            def __init__(self, spec: SubagentSpec, client: Any):
                self.name = spec.name
                self._spec = spec
                self._client = client

            async def ainvoke(self, state: dict, config: dict | None = None) -> dict:
                thread = await self._client.threads.create()
                return await self._client.runs.wait(
                    thread["thread_id"],
                    assistant_id="munin_supervisor",
                    input=state,
                )

            def invoke(self, state: dict, config: dict | None = None) -> dict:
                from .tool_factory import run_maybe_async  # noqa: PLC0415

                async def _call() -> dict:
                    return await self.ainvoke(state, config=config)

                return run_maybe_async(_call, {})

        return AsyncSubAgentProxy(spec, client)

    def _make_swarm_member(self, spec: SubagentSpec) -> Any:
        try:
            from munin.core.coordination.handoff_tools import (  # noqa: PLC0415
                make_handoff_tools_for_agents,
            )

            handoff_tools = make_handoff_tools_for_agents(
                [t for t in spec.tools if not t.startswith("gen__")]
            )
        except ImportError:
            handoff_tools = []

        all_tools = self._filter_tools(spec.tools) + handoff_tools
        model = self._resolve_model(spec)

        try:
            from deepagents import create_deep_agent  # noqa: PLC0415

            return create_deep_agent(
                name=spec.name,
                model=model,
                tools=all_tools,
                system_prompt=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            )
        except ImportError:
            from langchain.agents import create_agent  # noqa: PLC0415

            return create_agent(
                model=model,
                tools=all_tools,
                system_prompt=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
                name=spec.name,
            )

    # ------------------------------------------------------------------

    def _filter_tools(self, tool_names: list[str]) -> list[Any]:
        if not tool_names:
            return list(self._tools)
        name_set = set(tool_names)
        return [t for t in self._tools if getattr(t, "name", None) in name_set]
