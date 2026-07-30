from __future__ import annotations
import asyncio
from typing import Any, AsyncIterator, Callable


class ProgressEmitMiddleware:
    """
    Converts LangGraph stream_events (v2) into the munin progress event dict format.

    Usage:
        middleware = ProgressEmitMiddleware(progress_sink, run_id)
        async for event in middleware.wrap_stream(graph.astream_events(input, version="v2")):
            pass  # progress_sink already received all events
    """

    def __init__(self, progress_sink: Callable[[dict], None], run_id: str):
        self.progress_sink = progress_sink
        self.run_id = run_id

    async def wrap_stream(self, event_stream: AsyncIterator[dict]) -> AsyncIterator[dict]:
        """Wrap a LangGraph astream_events stream, emitting progress events."""
        async for event in event_stream:
            progress_event = self._translate(event)
            if progress_event:
                self.progress_sink(progress_event)
            yield event

    def _translate(self, event: dict) -> dict | None:
        """Translate a LangGraph event dict to a Munin progress event dict."""
        name = event.get("name", "")
        event_type = event.get("event", "")
        run_id = self.run_id

        # AI model events → reasoning
        if event_type == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                return {"kind": "reasoning", "run_id": run_id, "text": str(chunk.content)}

        # Tool events
        elif event_type == "on_tool_start":
            inputs = event.get("data", {}).get("input", {})
            tool_call_id = event.get("run_id", "")
            return {
                "kind": "tool_intent",
                "run_id": run_id,
                "tool_name": name,
                "tool_call_id": tool_call_id,
                "input": inputs,
            }

        elif event_type == "on_tool_end":
            output = event.get("data", {}).get("output")
            tool_call_id = event.get("run_id", "")
            return {
                "kind": "tool_result",
                "run_id": run_id,
                "tool_call_id": tool_call_id,
                "output": str(output) if output is not None else "",
            }

        # Chain/graph completion
        elif event_type == "on_chain_end" and name in ("LangGraph", "__end__"):
            return {"kind": "run_state", "run_id": run_id, "state": "completed"}

        return None

    def emit(self, event: dict) -> None:
        """Directly emit a progress event (for manual use)."""
        self.progress_sink(event)
