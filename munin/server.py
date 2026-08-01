"""Unified Munin ASGI server (Fase 3 of issue #9 migration).

Historically Munin ran as **two** processes on **two** ports:

* ``munin mcp --transport streamable-http --port 8890`` — the FastMCP
  offensive-security tool surface.
* ``munin production-api --port 8787`` — the authenticated HTTP API + AI
  SDK ``/api/chat`` stream that the Next.js frontend talks to.

The two servers shared the same libsql database (via
``settings.db_url``) but no in-process state, so keeping them coherent
required duplicating settings, tunnels, cookies, allowed-origins tables,
and health-checks.  Fase 3 collapses them into a single Starlette app:

* ``/mcp/**``       → FastMCP streamable-http transport (was port 8890)
* everything else   → the ``munin.production.asgi`` HTTP API (was 8787)
* ``/health``       → unified snapshot of both subsystems

One process. One port (``8787`` by convention — the value the frontend
proxy and tunnels already assume).  One ``ProductionStore`` +
``SharedStateStore`` pair, instantiated once here and shared between the
HTTP handlers and the MCP tools.  The old ``_ChatStateAdapter`` shim
that ``munin.production.chat`` used to compose the two stores is gone;
guidance drain is wired onto the ``SharedStateStore`` instance directly
so :class:`OperatorGuidanceMiddleware` finds it without an adapter.

Launch:

    poetry run uvicorn munin.server:app --host 0.0.0.0 --port 8787

or via the CLI convenience wrapper:

    poetry run munin serve --port 8787
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

log = logging.getLogger("munin.server")


def _bind_guidance(shared_state: Any, production_store: Any) -> None:
    """Wire ProductionStore's guidance drain onto the SharedStateStore instance.

    :class:`munin.core.middleware.OperatorGuidanceMiddleware` calls
    ``state.consume_pending_guidance(run_id=...)`` on whatever object is
    passed as ``state`` to ``build_munin_supervisor``.  Before Fase 3 the
    ``munin.production.chat`` module composed two stores through the
    ``_ChatStateAdapter`` façade to satisfy that requirement; here we do
    the same by attribute-binding a bound method onto the single shared
    ``SharedStateStore``.  The adapter class is gone.
    """
    try:
        shared_state.consume_pending_guidance = production_store.consume_pending_guidance  # type: ignore[assignment]
    except Exception as exc:  # noqa: BLE001 - guidance is best-effort
        log.warning("server: unable to bind guidance drain onto shared state: %s", exc)


def _build_health(mcp_module: Any, shared_state: Any, production_store: Any) -> Any:
    async def health(_: Request) -> JSONResponse:
        # Best-effort counters — never let a probe raise.
        try:
            overview = shared_state.overview()
        except Exception:  # noqa: BLE001
            overview = {}
        try:
            # ``MuninStore._read_only`` routes to the durable backend, so
            # the health probe reports the canonical conversation count.
            with production_store._read_only() as conn:  # noqa: SLF001 - read-only probe
                row = conn.execute("SELECT COUNT(1) AS n FROM conversations").fetchone()
                convs = int(row["n"] if isinstance(row, dict) or hasattr(row, "keys") else row[0])
        except Exception:  # noqa: BLE001
            convs = -1
        try:
            tools_count = len(getattr(mcp_module.MCP, "_tool_manager", None)._tools)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            tools_count = -1
        return JSONResponse(
            {
                "ok": True,
                "service": "munin-server",
                "subsystems": {
                    "http_api": {"ok": True, "conversations_total": convs},
                    "mcp": {"ok": True, "tools_registered": tools_count},
                    "shared_state": {
                        "ok": True,
                        "intel_total": overview.get("intel_total", 0),
                        "tasks_running": overview.get("tasks_running", 0),
                    },
                },
            }
        )

    return health


def create_app() -> Starlette:
    """Build the composed Starlette application.

    Order matters: the MCP transport is mounted first so any request
    beginning with ``/mcp/`` is handled by FastMCP; everything else
    falls through to the auth-protected HTTP surface.
    """
    from .mcp import main as mcp_module  # noqa: PLC0415 - lazy so `ast.parse` still works standalone
    from .mcp.config import get_settings  # noqa: PLC0415
    from .production.asgi import create_http_app  # noqa: PLC0415
    from .production.store import MuninStore, ProductionStore  # noqa: PLC0415

    settings = get_settings()

    # ── Store bootstrap ──────────────────────────────────────────────
    # Fase 4 (issue #9): replace the single ``ProductionStore`` with the
    # split-backend :class:`MuninStore` façade.  Auth sessions, rate
    # limits, recovery tokens, guidance queue, and in-progress agent
    # runs live in the local ``hot_db_path`` SQLite file; the durable
    # Turso store keeps users, conversations, messages, artifacts, the
    # audit trail, and finalised runs.  See :mod:`munin.production.store`
    # for the routing table.  ``MuninStore.from_settings`` transparently
    # falls back to hot-only mode when ``durable_db_url`` is empty
    # (matches pre-Fase-4 sqlite-only development setups).
    shared_state = mcp_module.STATE
    production_store = MuninStore.from_settings(
        settings, master_key=ProductionStore.master_key_from_environment()
    )
    _bind_guidance(shared_state, production_store)

    # ── Compose sub-apps ─────────────────────────────────────────────
    http_app = create_http_app(store=production_store, shared_state=shared_state)
    mcp_app = mcp_module.create_mcp_app()

    routes = [
        Route("/health", _build_health(mcp_module, shared_state, production_store)),
        Mount("/mcp", app=mcp_app),
        Mount("/", app=http_app),
    ]

    # ── Discord adapter (follow-up to Fase 2 of issue #9) ────────────
    # A single-process bridge that turns Discord messages into
    # ``create_turn`` + ``supervisor_runner`` invocations on the *same*
    # uvicorn event loop.  ``create_discord_task`` returns ``None`` when
    # ``MUNIN_DISCORD_BOT_TOKEN`` is unset (the default), so the ASGI
    # app is unchanged for every deployment that has not opted in.  When
    # enabled, the task is created inside the lifespan ``startup`` (so
    # ``asyncio.get_running_loop`` sees the right loop) and cancelled
    # during ``shutdown``.  See :mod:`munin.production.discord_adapter`.
    discord_task_holder: dict[str, Any] = {}

    async def _startup_discord() -> None:
        try:
            from .production.discord_adapter import create_discord_task  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001 - adapter is optional
            log.warning("server: discord adapter import failed: %s", exc)
            return
        try:
            task = create_discord_task(settings, production_store, shared_state)
        except Exception as exc:  # noqa: BLE001
            log.warning("server: discord adapter startup failed: %s", exc)
            return
        if task is not None:
            discord_task_holder["task"] = task

    async def _shutdown_discord() -> None:
        task = discord_task_holder.get("task")
        if task is None:
            return
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001 - shutdown must not raise
            pass

    # ── Shutdown hook ────────────────────────────────────────────────
    # Fase 5: the DurableStore now owns a bounded libsql connection pool.
    # Uvicorn/Starlette dispatches the ``shutdown`` lifespan event during a
    # graceful restart; without this hook every rolling deploy would leak
    # every socket the pool pre-warmed.  ``close_pools`` is idempotent and
    # never raises — safe to run even when the process is being killed
    # forcefully.
    async def _shutdown_pools() -> None:
        try:
            production_store.close_pools()
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            log.warning("server: close_pools failed during shutdown: %s", exc)

    @asynccontextmanager
    async def _lifespan(app: Any) -> AsyncIterator[None]:
        await _startup_discord()
        yield
        await _shutdown_discord()
        await _shutdown_pools()

    return Starlette(
        debug=False,
        routes=routes,
        lifespan=_lifespan,
    )


def main() -> None:
    """CLI shim: ``python -m munin.server`` boots via uvicorn."""
    import uvicorn  # noqa: PLC0415 - keep import lazy for test-time introspection

    host = os.environ.get("MUNIN_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("MUNIN_SERVER_PORT", "8787"))
    uvicorn.run("munin.server:app", host=host, port=port, log_level="info")


# Module-level ``app`` so ``uvicorn munin.server:app`` works without a factory.
# ``app_from_environment`` is retained (in ``asgi.py``) as a backward-compatible
# alias for the pre-Fase-3 test suite that imported it directly.
app = create_app()


if __name__ == "__main__":
    main()
