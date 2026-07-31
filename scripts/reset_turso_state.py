"""Wipe Munin's operational rows from a configured remote Turso database.

Schema remains intact. The script refuses local SQLite and requires the exact
confirmation phrase so it is safe to keep as a GitHub Actions maintenance job.
"""

from __future__ import annotations

import argparse
from typing import Any

from munin.mcp.config import get_settings
from munin.mcp.shared_state import SharedStateStore

CONFIRMATION = "WIPE_MUNIN_TURSO"
TABLES = (
    "agent_messages",
    "agent_presence",
    "active_tasks",
    "agent_wake_queue",
    "shared_intel",
    "episodic",
    "semantic",
    "procedural",
    "generated_graphs",
    "runtime_cache",
)


def _count(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def reset_remote_state() -> dict[str, int]:
    settings = get_settings()
    if not settings.db_url.startswith(("libsql://", "libsqls://")):
        raise RuntimeError("refusing reset: MUNIN_DB_URL must be a libsql:// or libsqls:// Turso URL")
    if not settings.db_auth_token:
        raise RuntimeError("refusing reset: MUNIN_DB_AUTH_TOKEN is required")
    state = SharedStateStore(settings)
    removed: dict[str, int] = {}
    with state._connect(authoritative=True) as conn:  # remote autocommit connection by design
        for table in TABLES:
            removed[table] = _count(conn, table)
            conn.execute(f"DELETE FROM {table}")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset operational data in the configured Turso database")
    parser.add_argument("--confirm", required=True, help=f"must equal {CONFIRMATION}")
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit("confirmation phrase did not match; no data was changed")
    removed = reset_remote_state()
    print({"ok": True, "backend": "turso", "rows_removed": sum(removed.values()), "tables": removed})


if __name__ == "__main__":
    main()
