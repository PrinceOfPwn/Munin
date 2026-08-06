# tags: [subagent, runtime, core, orchestrator, langgraph, SubagentFactory, create_subagent, invoke_subagent, _make_deep_agent, _make_compiled_langgraph, _make_persisted_subagent_dict, BundledSkillLibrary, SubagentSpec, runtime-routing, dynamic-materialization]
"""
Subagent Factory — materializes generated specialists on the lightest correct
runtime (issue #9 §4).  No stub runtimes: every maker returns an invocable
object.

Routing:
  persisted_subagent_dict -> declarative dict (Deep Agents native SubAgent shape)
  deep_agent              -> deepagents.create_deep_agent
  compiled_langgraph      -> langchain.agents.create_agent (CompiledStateGraph,
                             messages state = CompiledSubAgent-compatible)

There is deliberately no depth/count cap anywhere in this module (issue #9
§4 "no arbitrary hard caps"): nesting is enabled by handing the Autonomy
Kernel meta-tools to generated agents.
"""
from __future__ import annotations

from typing import Any

from .skill_library import BundledSkillLibrary, bundled_skill_library
from .spec import SubagentSpec


class SubagentFactory:
    def __init__(
        self,
        tools: list[Any],
        model: Any = None,
        registry: Any | None = None,
        skill_library: BundledSkillLibrary | None = None,
    ):
        self._tools = tools
        self._model = model
        self._registry = registry
        self._skill_library = skill_library or bundled_skill_library()

    # ------------------------------------------------------------------

    def create_subagent(self, spec: SubagentSpec) -> Any:
        router = {
            "persisted_subagent_dict": self._make_persisted_subagent_dict,
            "deep_agent": self._make_deep_agent,
            "compiled_langgraph": self._make_compiled_langgraph,
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
            normalized = dict(agent)
            if "description" in normalized:
                normalized["purpose"] = normalized.pop("description")
            model = normalized.get("model")
            if model is not None and not isinstance(model, str):
                normalized.pop("model")
            tools = normalized.get("tools") or []
            normalized["tools"] = [
                getattr(t, "name", str(t)) if not isinstance(t, str) else t for t in tools
            ]
            normalized["runtime_type"] = "compiled_langgraph"
            agent = self.create_subagent(SubagentSpec.model_validate(normalized))
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
        agent = {
            "name": spec.name,
            "description": spec.purpose,
            "system_prompt": spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            "tools": self._filter_tools(spec.tools, may_create_child=spec.may_create_child),
            "model": self._resolve_model(spec),
        }
        binding = self._skill_library.bind(spec.skills)
        if binding is not None:
            # Custom Deep Agents subagents explicitly receive their own skill
            # sources; they do not inherit the parent skill list implicitly.
            agent["skills"] = binding.sources
        return agent

    def _make_deep_agent(self, spec: SubagentSpec) -> Any:
        from deepagents import create_deep_agent  # noqa: PLC0415

        from ..tool_gateway import approval_policy_for_tools  # noqa: PLC0415

        tools = self._filter_tools(spec.tools, may_create_child=spec.may_create_child)
        binding = self._skill_library.bind(spec.skills)
        kwargs: dict[str, Any] = {}
        if binding is not None:
            kwargs.update(
                skills=binding.sources,
                backend=binding.backend,
                permissions=binding.permissions,
            )

        return create_deep_agent(
            name=spec.name,
            model=self._resolve_model(spec),
            tools=tools,
            system_prompt=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            interrupt_on=approval_policy_for_tools(tools),
            **kwargs,
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
            tools=self._filter_tools(spec.tools, may_create_child=spec.may_create_child),
            system_prompt=spec.system_prompt or f"You are {spec.name}: {spec.purpose}",
            name=spec.name,
        )

    # ------------------------------------------------------------------

    def _filter_tools(self, tool_names: list[str], *, may_create_child: bool = False) -> list[Any]:
        from .kernel import KERNEL_META_TOOL_NAMES  # noqa: PLC0415

        name_set = set(tool_names)
        if may_create_child:
            # Kernel-owned meta tools are safe to inherit as *capability
            # constructors*. Any resulting side effect still encounters the
            # gateway's scope/audit/HITL policy.
            name_set |= KERNEL_META_TOOL_NAMES
        return [t for t in self._tools if getattr(t, "name", None) in name_set]
