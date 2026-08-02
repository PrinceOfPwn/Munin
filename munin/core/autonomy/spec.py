"""
SubagentSpec — Pydantic model describing a subagent to be created.

The spec is the source of truth for rebuilding agents across runs.
All fields must be JSON-serializable so the spec can be stored in
agent_registry.definition_json.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RuntimeType = Literal[
    "persisted_subagent_dict",
    "deep_agent",
    "compiled_langgraph",
]


class SubagentSpec(BaseModel):
    """Full specification for a Munin subagent."""

    # Identity
    name: str = Field(description="Unique agent name within a run")
    purpose: str = Field(description="One-sentence description of what this agent does")

    # LLM configuration
    system_prompt: str = Field(default="", description="System prompt for this agent")
    model: str = Field(default="gpt-4o", description="LLM model identifier")

    # Capabilities
    tools: list[str] = Field(default_factory=list, description="Tool names this agent can use")
    skills: list[str] = Field(
        default_factory=list,
        description="Explicit reviewed Deep Agents skill names (for example hugin-research)",
    )
    memory: dict[str, Any] = Field(default_factory=dict, description="Initial memory state")
    filesystem: dict[str, str] = Field(default_factory=dict, description="Virtual FS mounts")

    # Middleware
    middleware: list[str] = Field(
        default_factory=list,
        description="Additional LangChain/Deep Agents middleware class names to attach"
    )

    # Output
    response_format: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured output schema (JSON Schema or Pydantic model name)"
    )

    # Topology
    interaction_topology: Literal["sequential", "parallel", "swarm", "hierarchical"] = Field(
        default="sequential"
    )

    # Persistence
    persistence_policy: Literal["ephemeral", "session", "permanent"] = Field(
        default="session",
        description="ephemeral=dies with run, session=survives run, permanent=versioned"
    )

    # Nesting
    may_create_child: bool = Field(
        default=True,
        description="Whether this agent may create child subagents"
    )

    # State
    custom_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom LangGraph state fields beyond messages"
    )

    # Execution
    execution_mode: Literal["sync", "async"] = Field(default="sync")
    runtime_type: RuntimeType = Field(
        default="deep_agent",
        description="Which runtime to use for this agent"
    )
    max_iterations_floor: int = Field(
        default=1,
        ge=1,
        description="Minimum iterations allowed (no upper cap)"
    )

    class Config:
        extra = "forbid"

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> SubagentSpec:
        return cls.model_validate_json(data)
