"""
ASGI application — FastAPI HTTP + SSE API for Munin.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def create_app(store=None) -> Any:
    if not HAS_FASTAPI:
        raise ImportError("fastapi required: pip install fastapi uvicorn")

    from munin.production.store import ProductionStore

    if store is None:
        db_path = os.environ.get("MUNIN_DB_PATH", "data/munin.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        store = ProductionStore(db_path)

    app = FastAPI(title="Munin API", version="3.5.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    async def ping():
        return {"status": "ok", "version": "3.5.0"}

    @app.post("/api/runs")
    async def create_run(request: Request):
        body = await request.json()
        run_id = store.create_run(
            conversation_id=body.get("conversation_id", "default"),
            goal=body.get("goal", ""),
            model=body.get("model", "gpt-4o"),
        )
        return {"run_id": run_id, "state": "running"}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request):
        last_event_id = request.headers.get("last-event-id", "0")

        async def event_generator() -> AsyncIterator[str]:
            event_id = int(last_event_id) if last_event_id.isdigit() else 0
            silence_deadline = time.time() + 45

            while True:
                if await request.is_disconnected():
                    break

                run = store.get_run(run_id)
                if run is None:
                    yield _sse({"kind": "error", "message": "run not found"}, event_id)
                    break

                broadcasts = store.get_broadcasts_since(run_id, since_id=event_id)
                for bc in broadcasts:
                    event_id = bc["id"]
                    payload = json.loads(bc["payload_json"])
                    yield _sse(payload, event_id)
                    silence_deadline = time.time() + 45

                if run["state"] in ("completed", "failed", "interrupted", "cancelled"):
                    yield _sse({"kind": "run_state", "run_id": run_id, "state": run["state"]}, event_id)
                    break

                if time.time() > silence_deadline:
                    yield _sse({"kind": "heartbeat", "run_id": run_id, "ts": int(time.time())}, event_id)
                    silence_deadline = time.time() + 45

                await asyncio.sleep(0.5)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/runs/{run_id}/approve")
    async def approve_tool(run_id: str, request: Request):
        body = await request.json()
        request_id = body.get("request_id")
        if not request_id:
            raise HTTPException(status_code=400, detail="request_id required")
        store.resolve_human_request(request_id, "approved")
        return {"status": "approved"}

    @app.post("/api/runs/{run_id}/reject")
    async def reject_tool(run_id: str, request: Request):
        body = await request.json()
        request_id = body.get("request_id")
        if not request_id:
            raise HTTPException(status_code=400, detail="request_id required")
        store.resolve_human_request(request_id, f"rejected:{body.get('reason', '')}")
        return {"status": "rejected"}

    @app.post("/api/runs/{run_id}/guidance")
    async def queue_guidance(run_id: str, request: Request):
        body = await request.json()
        text = body.get("text", "")
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        store.queue_guidance(run_id, text)
        return {"status": "queued"}

    return app


def _sse(data: dict, event_id: int) -> str:
    return f"id: {event_id}\ndata: {json.dumps(data)}\n\n"
