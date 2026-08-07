"""Wipe Munin's operational rows from a configured remote Turso database.

Schema remains intact. The script refuses local SQLite and requires the exact
confirmation phrase so it is safe to keep as a GitHub Actions maintenance job.

Every table in the configured database is wiped (discovered dynamically, so
new tables added by later features — conversations, production suite, autonomy
registries, ... — are covered without maintaining a hard-coded list), except
the ``schema_migrations`` bookkeeping table whose rows track applied
migrations.
"""

from __future__ import annotations

import argparse
from typing import Any

from munin.mcp.config import get_settings
from munin.mcp.shared_state import SharedStateStore

CONFIRMATION = "WIPE_MUNIN_TURSO"
PRESERVED_TABLES = {"schema_migrations"}


def _table_names(conn: Any) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [row[0] for row in rows]


def _count(conn: Any, table: str) -> int:
    # table names come from sqlite_master, never from caller input — S608 is a false positive.
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def reset_remote_state() -> dict[str, int]:
    settings = get_settings()
    if not settings.db_url.startswith(("libsql://", "libsqls://")):
        raise RuntimeError("refusing reset: MUNIN_DB_URL must be a libsql:// or libsqls:// Turso URL")
    if not settings.db_auth_token:
        raise RuntimeError("refusing reset: MUNIN_DB_AUTH_TOKEN is required")
    state = SharedStateStore(settings)
    removed: dict[str, int] = {}
    # Wipe every operational table in a single remote-authoritative connection.
    # Foreign keys are enforced by Turso over Hrana, so deleting tables in an
    # arbitrary order trips ``FOREIGN KEY constraint failed`` and leaves the
    # database half-wiped. Disable FK enforcement for this session (libsql
    # honours ``PRAGMA foreign_keys`` per connection over Hrana); when the
    # server rejects that PRAGMA, a bounded multi-pass drain still completes
    # the wipe so no table survives with rows while its parents are emptied.
    with state._connect(authoritative=True) as conn:  # remote autocommit connection by design
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
        except Exception:  # noqa: S110
            # Some libsql/Turso deployments reject this PRAGMA over Hrana.
            # Fall back to the multi-pass strategy so a partial wipe does not
            # happen again regardless of server-side FK enforcement.
            pass
        tables = _table_names(conn)
        for table in tables:
            if table in PRESERVED_TABLES:
                continue
            removed[table] = _count(conn, table)
        # Multi-pass delete so residual FK edges (when PRAGMA OFF was rejected)
        # drain as their parent rows vanish. Idempotent and bounded: each pass
        # removes at least one row from every table that has any rows, and the
        # FK graph is acyclic for an operational schema, so a finite number of
        # passes (<= number of tables) clears every row.
        max_passes = max(1, len(tables))
        for _ in range(max_passes):
            progress = False
            for table in tables:
                if table in PRESERVED_TABLES:
                    continue
                try:
                    # table comes from sqlite_master; S608 is a false positive.
                    cur = conn.execute(f"DELETE FROM {table}")  # noqa: S608
                    deleted = getattr(cur, "rowcount", -1)
                    if deleted and deleted > 0:
                        progress = True
                except Exception:  # noqa: S112
                    # FOREIGN KEY constraint failed — retry on the next pass
                    # once the referencing rows have been removed.
                    continue
            if not progress:
                break
        # Final verification: surface any rows that survived every pass. These
        # would mean a self-referencing or cyclic FK edge the multi-pass drain
        # cannot untangle — the operator must see them rather than silently
        # believing the wipe succeeded.
        residuals = {
            t: _count(conn, t)
            for t in tables
            if t not in PRESERVED_TABLES and _count(conn, t) > 0
        }
        try:
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:  # noqa: S110
            pass
    if residuals:
        raise RuntimeError(
            f"reset completed with residual rows in {len(residuals)} tables; "
            f"check for cyclic FK edges: {residuals}"
        )
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
