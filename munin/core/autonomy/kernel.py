"""
Autonomy Kernel (issue #9 §2) — the minimal Munin-owned layer that lets the
main Deep Agent AND any generated subagent create, invoke, persist, compose
and discover runtime capabilities at run time.

One generic execution path per capability type:

  create_tool / invoke_registered_tool / list_registered_tools / inspect_registered_tool
  create_subagent / invoke_registered_agent / list_registered_agents / inspect_registered_agent
  create_workflow / invoke_registered_workflow / list_registered_workflows
  schedule_workers   — real LangGraph Send fan-out with per-worker isolation,
                       individual failure capture and reducer aggregation

Everything persists through Munin's domain stores (procedural table /
agent_registry / workflow_registry in the shared DB), so capabilities forged
in one run are discoverable in the next — and same-run invocation never
requires recompiling the supervisor graph.

No hard caps: no depth, count, or topology limits are enforced anywhere here;
runaway protection lives in observable middleware (repetition guard) and
operator controls, per issue #9 §4.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .agent_registry import AgentRegistry
from .spec import SubagentSpec
from .subagent_factory import SubagentFactory
from .tool_factory import ToolFactory
from .workflow_factory import create_workflow
from .workflow_registry import WorkflowRegistry
from .workflow_spec import WorkflowSpec

logger = logging.getLogger(__name__)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, default=str)


class AutonomyKernel:
    """Owns the factories + registries and exposes them as agent tools."""

    def __init__(
        self,
        state: Any,
        *,
        model: Any = None,
        run_id: str = "",
        agent_id: str = "supervisor",
        tools_provider: Callable[[], list[Any]] | None = None,
    ):
        self._state = state
        self._model = model
        self._run_id = run_id
        self._agent_id = agent_id
        self._tools_provider = tools_provider or (lambda: [])

        self.tool_factory = ToolFactory(state, run_id=run_id, agent_id=agent_id)
        self.agent_registry = AgentRegistry(state=state)
        self.workflow_registry = WorkflowRegistry(state=state)
        self._ephemeral_agents: dict[str, SubagentSpec] = {}
        self._ephemeral_workflows: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # subagent / workflow builders
    # ------------------------------------------------------------------

    def _subagent_factory(self) -> SubagentFactory:
        return SubagentFactory(tools=self._tools_provider(), model=self._model)

    async def _invoke_agent(self, agent_id_or_name: str, task: str) -> dict[str, Any]:
        factory = self._subagent_factory()
        record: dict[str, Any] = {}
        try:
            row = self.agent_registry.inspect_registered_agent(agent_id_or_name)
            agent = self.agent_registry.rebuild_agent(agent_id_or_name, factory=factory)
            record = {"agent_id": agent_id_or_name, "version": row["version"]}
        except KeyError:
            spec = self._ephemeral_agents.get(agent_id_or_name)
            if spec is None:
                raise KeyError(
                    f"Agent {agent_id_or_name!r} not found in registry or ephemeral run set"
                )
            agent = factory.create_subagent(spec)
            record = {"agent_id": agent_id_or_name, "version": None, "ephemeral": True}

        result = await factory.invoke_subagent(agent, task)
        if record.get("version") is not None:
            self.agent_registry.record_invocation(
                agent_id_or_name, record["version"], str(result.get("content", ""))[:500]
            )
        return {**record, "content": result.get("content", "")}

    async def _invoke_workflow(
        self, workflow_id: str, input_state: dict, thread_id: str | None
    ) -> dict[str, Any]:
        try:
            compiled = self.workflow_registry.rebuild_workflow(
                workflow_id, tools=self._tools_provider(), model=self._model
            )
            row = self.workflow_registry.inspect_registered_workflow(workflow_id)
            version = row["version"]
        except KeyError:
            compiled = self._ephemeral_workflows.get(workflow_id)
            if compiled is None:
                raise
            version = None

        config = {"configurable": {"thread_id": thread_id}} if thread_id else {}
        result = await compiled.ainvoke(input_state, config=config)
        if version is not None:
            self.workflow_registry.record_workflow_exec(workflow_id, version, "invoked")
        return {"workflow_id": workflow_id, "version": version, "result": result}

    # ------------------------------------------------------------------
    # Send fan-out (issue #9 §7)
    # ------------------------------------------------------------------

    async def _schedule_workers(self, tool_name: str, items: list[dict]) -> dict[str, Any]:
        """Execute ``tool_name`` over ``items`` via real LangGraph Send fan-out."""
        from langgraph.graph import END, StateGraph  # noqa: PLC0415

        from ..parallel.send_workers import WorkerState, fanout, make_worker_node  # noqa: PLC0415
        from ..tool_gateway import gateway_tools  # noqa: PLC0415

        tool = next(
            (t for t in gateway_tools(self._state) if getattr(t, "name", None) == tool_name),
            None,
        )
        if tool is None:
            return {"ok": False, "error": f"tool {tool_name!r} not in gateway catalog"}

        async def _invoke_tool(**kwargs: Any) -> Any:
            return await tool.ainvoke(kwargs)

        builder = StateGraph(WorkerState)
        builder.add_node("start", lambda s: {})
        builder.add_node("tool_worker", make_worker_node(_invoke_tool))
        builder.add_conditional_edges("start", lambda s: fanout("tool_worker", s["items"]))
        builder.add_edge("tool_worker", END)
        graph = builder.compile()

        final = await graph.ainvoke(
            {"messages": [], "items": items, "aggregate": [], "worker_index": -1, "task_args": {}}
        )
        aggregate = sorted(final.get("aggregate", []), key=lambda o: o.get("index", 0))
        failures = [o for o in aggregate if o.get("error")]
        return {
            "ok": True,
            "tool": tool_name,
            "workers": len(items),
            "succeeded": len(aggregate) - len(failures),
            "failed": len(failures),
            "results": aggregate,
        }

    # ------------------------------------------------------------------
    # meta-tool surface
    # ------------------------------------------------------------------

    def meta_tools(self) -> list[Any]:
        from langchain_core.tools import StructuredTool  # noqa: PLC0415
        from pydantic import BaseModel, Field  # noqa: PLC0415

        kernel = self

        # -- schemas -----------------------------------------------------
        class CreateToolArgs(BaseModel):
            name: str = Field(description="Tool name (gen__ prefix auto-applied)")
            source: str = Field(description="Complete Python source defining the tool function")
            description: str = ""
            function_name: str | None = None
            allowed_imports: list[str] | None = None
            test_args: dict | None = Field(
                default=None, description="Optional smoke-test kwargs run in the sandbox"
            )

        class InvokeToolArgs(BaseModel):
            name: str
            arguments: dict = Field(default_factory=dict)

        class ListToolsArgs(BaseModel):
            gen_only: bool = False

        class InspectArgs(BaseModel):
            name: str

        class CreateSubagentArgs(BaseModel):
            name: str
            purpose: str
            system_prompt: str = ""
            tools: list[str] = Field(default_factory=list)
            runtime_type: str = "compiled_langgraph"
            persist: bool = Field(default=False, description="Register in the Agent Registry")

        class InvokeAgentArgs(BaseModel):
            agent_id: str = Field(description="Registry agent_id or ephemeral agent name")
            task: str

        class CreateWorkflowArgs(BaseModel):
            spec_json: str = Field(description="WorkflowSpec as JSON")
            persist: bool = False

        class InvokeWorkflowArgs(BaseModel):
            workflow_id: str
            input_json: str = "{}"
            thread_id: str | None = None

        class ScheduleWorkersArgs(BaseModel):
            tool_name: str = Field(description="Gateway tool to run per item")
            items: list[dict] = Field(description="One kwargs dict per worker (N items = N workers)")

        # -- tool handlers -------------------------------------------------
        async def create_tool(
            name: str,
            source: str,
            description: str = "",
            function_name: str | None = None,
            allowed_imports: list[str] | None = None,
            test_args: dict | None = None,
        ) -> str:
            return _json(
                kernel.tool_factory.create_tool(
                    name=name,
                    source=source,
                    description=description,
                    function_name=function_name,
                    allowed_imports=allowed_imports,
                    test_args=test_args,
                )
            )

        async def invoke_registered_tool(name: str, arguments: dict | None = None) -> str:
            try:
                result = kernel.tool_factory.invoke_registered_tool(name, arguments or {})
                return _json({"ok": True, "result": result})
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        async def list_registered_tools(gen_only: bool = False) -> str:
            return _json(kernel.tool_factory.list_registered_tools(gen_only=gen_only))

        async def inspect_registered_tool(name: str) -> str:
            try:
                return _json(kernel.tool_factory.inspect_registered_tool(name))
            except KeyError as exc:
                return _json({"ok": False, "error": str(exc)})

        async def create_subagent(
            name: str,
            purpose: str,
            system_prompt: str = "",
            tools: list[str] | None = None,
            runtime_type: str = "compiled_langgraph",
            persist: bool = False,
        ) -> str:
            spec = SubagentSpec(
                name=name,
                purpose=purpose,
                system_prompt=system_prompt,
                tools=tools or [],
                runtime_type=runtime_type,  # type: ignore[arg-type]
            )
            # Validate buildability immediately — a broken definition is an error now.
            kernel._subagent_factory().create_subagent(spec)
            if persist:
                agent_id, version = kernel.agent_registry.register_agent(
                    spec, created_by=kernel._agent_id, parent_run=kernel._run_id or None
                )
                return _json({"ok": True, "agent_id": agent_id, "version": version, "persisted": True})
            kernel._ephemeral_agents[name] = spec
            return _json({"ok": True, "agent_id": name, "persisted": False})

        async def invoke_registered_agent(agent_id: str, task: str) -> str:
            try:
                return _json({"ok": True, **await kernel._invoke_agent(agent_id, task)})
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        async def list_registered_agents() -> str:
            return _json(kernel.agent_registry.list_registered_agents())

        async def inspect_registered_agent(name: str) -> str:
            try:
                return _json(kernel.agent_registry.inspect_registered_agent(name))
            except KeyError as exc:
                return _json({"ok": False, "error": str(exc)})

        async def create_workflow(spec_json: str, persist: bool = False) -> str:
            try:
                spec = WorkflowSpec.from_json(spec_json)
                compiled = create_workflow(spec, tools=kernel._tools_provider(), model=kernel._model)
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": f"workflow build failed: {exc}"})
            if persist:
                workflow_id, version = kernel.workflow_registry.register_workflow(
                    spec, created_by=kernel._agent_id, parent_run=kernel._run_id or None
                )
                return _json(
                    {"ok": True, "workflow_id": workflow_id, "version": version, "persisted": True}
                )
            kernel._ephemeral_workflows[spec.name] = compiled
            return _json({"ok": True, "workflow_id": spec.name, "persisted": False})

        async def invoke_registered_workflow(
            workflow_id: str, input_json: str = "{}", thread_id: str | None = None
        ) -> str:
            try:
                input_state = json.loads(input_json) if input_json.strip() else {}
                if "messages" not in input_state:
                    input_state.setdefault("messages", [])
                return _json(
                    {"ok": True, **await kernel._invoke_workflow(workflow_id, input_state, thread_id)}
                )
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        async def list_registered_workflows() -> str:
            return _json(kernel.workflow_registry.list_registered_workflows())

        async def schedule_workers(tool_name: str, items: list[dict]) -> str:
            try:
                return _json(await kernel._schedule_workers(tool_name, items))
            except Exception as exc:  # noqa: BLE001
                return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        def st(coro: Callable, name: str, description: str, schema: type[BaseModel]) -> Any:
            return StructuredTool.from_function(
                coroutine=coro, name=name, description=description, args_schema=schema
            )

        return [
            st(create_tool, "create_tool",
               "Create a new gen__ tool from Python source. Validated by the AST guard, "
               "optionally sandbox-tested, registered and immediately invocable this run.",
               CreateToolArgs),
            st(invoke_registered_tool, "invoke_registered_tool",
               "Invoke any registered tool (gen__* or catalog) by name with a kwargs dict. "
               "This is how you call tools you just created in the same run.",
               InvokeToolArgs),
            st(list_registered_tools, "list_registered_tools",
               "List tools in the Tool Registry (procedural table).", ListToolsArgs),
            st(inspect_registered_tool, "inspect_registered_tool",
               "Full metadata + provenance for one registered tool.", InspectArgs),
            st(create_subagent, "create_subagent",
               "Materialize a specialist agent from a spec. runtime_type: compiled_langgraph "
               "(default), deep_agent, persisted_subagent_dict, async_langgraph, swarm_member. "
               "persist=true registers it in the Agent Registry for later runs.",
               CreateSubagentArgs),
            st(invoke_registered_agent, "invoke_registered_agent",
               "Invoke a registered or ephemeral agent by id with a task string; returns the "
               "agent's final answer (trace stays in the registry).", InvokeAgentArgs),
            st(list_registered_agents, "list_registered_agents",
               "List agents in the Agent Registry.", ListToolsArgs),
            st(inspect_registered_agent, "inspect_registered_agent",
               "Full definition + provenance + exec history for one agent.", InspectArgs),
            st(create_workflow, "create_workflow",
               "Compile a WorkflowSpec JSON (deterministic/agent/tool nodes, static/conditional/"
               "send edges, reducers, interrupts) into a LangGraph runnable.",
               CreateWorkflowArgs),
            st(invoke_registered_workflow, "invoke_registered_workflow",
               "Invoke a registered/ephemeral workflow with a JSON input state.",
               InvokeWorkflowArgs),
            st(list_registered_workflows, "list_registered_workflows",
               "List workflows in the Workflow Registry.", ListToolsArgs),
            st(schedule_workers, "schedule_workers",
               "Fan out N parallel workers with LangGraph Send: runs one gateway tool over a "
               "list of kwargs (one worker per host/URL/CVE). Individual failures do not abort "
               "the batch; results aggregate deterministically by worker index.",
               ScheduleWorkersArgs),
        ]
