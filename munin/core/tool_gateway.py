"""
Tool Gateway — wraps every Munin tool (MCP, native, gen__) as a
LangChain StructuredTool consumable by the LangGraph supervisor.
"""
from __future__ import annotations
import inspect
from typing import Any, Callable
from pydantic import BaseModel, create_model, Field
import asyncio


def _signature_to_pydantic(name: str, signature: dict) -> type[BaseModel]:
    """
    Convert a Munin tool signature dict (OpenAI function schema shape) to a
    Pydantic BaseModel suitable as StructuredTool.args_schema.

    signature = {
        "type": "object",
        "properties": {"param": {"type": "string", "description": "..."}},
        "required": ["param"],
    }
    """
    props = signature.get("properties", {})
    required = set(signature.get("required", []))
    fields: dict[str, Any] = {}

    type_map = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    for param_name, param_schema in props.items():
        py_type = type_map.get(param_schema.get("type", "string"), str)
        description = param_schema.get("description", "")
        if param_name in required:
            fields[param_name] = (py_type, Field(description=description))
        else:
            fields[param_name] = (py_type | None, Field(default=None, description=description))

    return create_model(f"{name}_Args", **fields)


def wrap_mcp_tool(
    name: str,
    description: str,
    signature: dict,
    handler: Callable,
) -> Any:
    """
    Wrap a Munin MCP tool as a LangChain StructuredTool.

    Args:
        name: Tool name (e.g. "port_scan", "gen__recon_script")
        description: Human-readable description for the LLM
        signature: OpenAI-style function schema (properties, required, etc.)
        handler: Async or sync callable that implements the tool

    Returns:
        A LangChain StructuredTool instance
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        raise ImportError("langchain-core required for Tool Gateway")

    args_schema = _signature_to_pydantic(name, signature)

    if inspect.iscoroutinefunction(handler):
        async def _async_invoke(**kwargs: Any) -> Any:
            return await handler(**kwargs)
        return StructuredTool(
            name=name,
            description=description,
            args_schema=args_schema,
            coroutine=_async_invoke,
        )
    else:
        def _sync_invoke(**kwargs: Any) -> Any:
            return handler(**kwargs)
        return StructuredTool(
            name=name,
            description=description,
            args_schema=args_schema,
            func=_sync_invoke,
        )


def wrap_all_tools(registry: Any) -> list[Any]:
    """
    Wrap all registered Munin tools (from mcp.registry) as StructuredTools.

    Iterates registry.iter_signature_specs() which yields:
        (name, description, signature_dict, handler_callable)

    Returns list of StructuredTool instances.
    """
    tools = []
    for name, description, signature, handler in registry.iter_signature_specs():
        try:
            tool = wrap_mcp_tool(name, description, signature, handler)
            tools.append(tool)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Tool Gateway: failed to wrap %r: %s", name, exc
            )
    return tools
