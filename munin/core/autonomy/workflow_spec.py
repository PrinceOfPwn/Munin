"""WorkflowSpec — Pydantic model for LangGraph workflow definitions."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

NodeKind = Literal["deterministic", "agent", "tool"]
EdgeKind = Literal["static", "conditional", "send"]


class Node(BaseModel):
    name: str
    kind: NodeKind
    description: str = ""
    python_code: str = ""
    agent_spec_name: str = ""
    model: str = "gpt-4o"
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)


class Edge(BaseModel):
    src: str
    dst: str = ""
    kind: EdgeKind = "static"
    condition_key: str = ""
    condition_map: dict[str, str] = Field(default_factory=dict)
    fanout_key: str = ""


class StepInterrupt(BaseModel):
    before_node: str
    message: str = "Human approval required"
    timeout_seconds: int = 300


class CustomState(BaseModel):
    name: str
    type: Literal["string", "integer", "float", "boolean", "list", "dict"] = "string"
    default: Any = None
    reducer: Literal["replace", "append", "add"] = "replace"
    description: str = ""


class WorkflowSpec(BaseModel):
    name: str
    description: str = ""
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    entry_point: str = ""
    finish_points: list[str] = Field(default_factory=list)
    custom_state: list[CustomState] = Field(default_factory=list)
    interrupts: list[StepInterrupt] = Field(default_factory=list)
    checkpointer: Literal["sqlite", "memory", "none"] = "sqlite"
    recursion_limit: int = 50

    class Config:
        extra = "allow"

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> "WorkflowSpec":
        return cls.model_validate_json(data)
