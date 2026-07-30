"""Online Turso acceptance test using two independent embedded replicas.

The second replica starts from a different empty directory. If it can read the
marker written by the first one, persistence crossed the network and did not
silently fall back to a local SQLite file.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from munin.mcp.config import get_settings
from munin.mcp.persistence import describe_backend
from munin.mcp.shared_state import SharedStateStore


def main() -> None:
    settings = get_settings()
    if not settings.db_url.startswith(("libsql://", "libsqls://")):
        raise SystemExit("Turso smoke requires MUNIN_DB_URL=libsql://...")
    if not settings.db_auth_token:
        raise SystemExit("Turso smoke requires MUNIN_DB_AUTH_TOKEN")

    marker = {
        "run_id": os.environ.get("GITHUB_RUN_ID", "manual"),
        "sha": os.environ.get("GITHUB_SHA", "local"),
        "written_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    marker_key = (
        "ci:turso:roundtrip:"
        f"{marker['run_id']}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    )
    with tempfile.TemporaryDirectory(prefix="munin-turso-smoke-") as temp_dir:
        root = Path(temp_dir)
        writer_settings = replace(settings, munin_data_path=root / "writer")
        reader_settings = replace(settings, munin_data_path=root / "reader")

        writer = SharedStateStore(writer_settings)
        writer.semantic_remember(marker_key, marker)
        writer.cache_put("ci", marker_key, marker, ttl_seconds=3600)

        reader = SharedStateStore(reader_settings)
        semantic = reader.semantic_recall(marker_key)
        cached = reader.cache_get("ci", marker_key)

    if semantic != marker:
        raise RuntimeError(f"Turso semantic roundtrip mismatch: {semantic!r}")
    if not cached or cached.get("value") != marker:
        raise RuntimeError(f"Turso cache roundtrip mismatch: {cached!r}")

    print(
        "Turso online roundtrip OK "
        f"backend={describe_backend(settings.db_url)} marker={marker_key}"
    )


if __name__ == "__main__":
    main()
