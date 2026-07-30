"""
runner.py — thin shim delegating to start_async_task via LangGraph SDK.
Legacy subprocess-based subagent spawning removed in PR-14.
"""
from __future__ import annotations
import warnings
from typing import Any


def wake_and_claim(run_id: str, db_path: str) -> dict | None:
    warnings.warn(
        "wake_and_claim() is deprecated. Use the start_async_task MCP tool.",
        DeprecationWarning,
        stacklevel=2,
    )
    return None


async def start_async_task(
    graph_id: str,
    input: dict,
    *,
    thread_id: str | None = None,
    config: dict | None = None,
) -> dict:
    import os
    url = os.environ.get("MUNIN_LANGGRAPH_URL", "")
    if not url:
        raise RuntimeError("MUNIN_LANGGRAPH_URL not configured. Start langgraph server first.")

    try:
        from langgraph_sdk import get_async_client
    except ImportError:
        raise ImportError("langgraph-sdk required: pip install langgraph-sdk")

    client = get_async_client(url=url)

    if thread_id is None:
        thread = await client.threads.create()
        thread_id = thread["thread_id"]

    run = await client.runs.create(
        thread_id=thread_id,
        assistant_id=graph_id,
        input=input,
        config=config or {},
    )

    return {
        "thread_id": thread_id,
        "run_id": run["run_id"],
        "status": run["status"],
    }


async def cancel_async_task(run_id: str, thread_id: str) -> dict:
    import os
    url = os.environ.get("MUNIN_LANGGRAPH_URL", "")
    if not url:
        raise RuntimeError("MUNIN_LANGGRAPH_URL not configured.")
    from langgraph_sdk import get_async_client
    client = get_async_client(url=url)
    await client.runs.cancel(thread_id=thread_id, run_id=run_id)
    return {"run_id": run_id, "status": "cancelled"}


async def check_async_task(run_id: str, thread_id: str) -> dict:
    import os
    url = os.environ.get("MUNIN_LANGGRAPH_URL", "")
    if not url:
        raise RuntimeError("MUNIN_LANGGRAPH_URL not configured.")
    from langgraph_sdk import get_async_client
    client = get_async_client(url=url)
    run = await client.runs.get(thread_id=thread_id, run_id=run_id)
    return {"run_id": run_id, "thread_id": thread_id, "status": run["status"]}
