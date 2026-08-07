"""Online Turso acceptance test using two independent embedded replicas.

The second replica starts from a different empty directory. If it can read the
marker written by the first one, persistence crossed the network and did not
silently fall back to a local SQLite file. Cross-host spawn, task, and wake
claims then compete through direct authoritative Turso connections.

Munin Live Session already executes this acceptance test as its last mandatory
pre-server Python step. In that workflow only, a successful Turso round-trip
also chains into the Valravn live bootstrap so Juice Shop, Burp MCP Ultimate,
and Talons are proven ready before ``munin serve`` can announce presence.
Other workflows and local executions retain the original Turso-only behavior.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from munin.mcp.config import get_settings
from munin.mcp.persistence import describe_backend
from munin.mcp.shared_state import SharedStateStore


def _bootstrap_live_session_mesh() -> None:
    """Start the real Valravn/Burp lab only in the named Live Session workflow."""
    if os.environ.get("GITHUB_WORKFLOW", "") != "Munin Live Session":
        return
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
    bootstrap = workspace / "scripts" / "valravn_live_bootstrap.sh"
    if not bootstrap.is_file():
        raise RuntimeError(f"Live Session bootstrap is missing: {bootstrap}")
    subprocess.run(
        ["bash", str(bootstrap)],
        cwd=workspace,
        env=os.environ.copy(),
        check=True,
    )


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
    # A workflow run can contain more than one job that validates the same
    # Turso database in parallel (for example, the fast backend job and the
    # full LDAP/Apache E2E job).  ``GITHUB_RUN_ID`` alone would make those
    # jobs reuse a task key, turning a healthy authoritative claim into a
    # false collision.  The job id keeps the test namespace isolated while
    # preserving cross-replica contention inside each individual smoke test.
    marker_key = (
        "ci:turso:roundtrip:"
        f"{marker['run_id']}:{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}:"
        f"{os.environ.get('GITHUB_JOB', 'manual')}"
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

        # Cross-host critical sections must execute on Turso's authoritative
        # connection, not independently on each embedded replica.
        agent_name = f"ci_authoritative_agent:{marker_key}"
        spawn_barrier = threading.Barrier(2)

        def claim_spawn(store: SharedStateStore, instance_id: str):
            spawn_barrier.wait()
            return store.try_claim_spawn_slot(
                agent_name=agent_name,
                spawner_pid=os.getpid(),
                instance_id=instance_id,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            spawn_claims = list(
                pool.map(
                    lambda pair: claim_spawn(*pair),
                    ((writer, "smoke-writer"), (reader, "smoke-reader")),
                )
            )

        task_barrier = threading.Barrier(2)
        task_action = f"ci-authoritative-task:{marker_key}"

        def claim_task(store: SharedStateStore, agent: str):
            task_barrier.wait()
            return store.claim_task(
                target_ip="127.0.0.1",
                action=task_action,
                assigned_agent=agent,
                lease_seconds=60,
                metadata_json="{}",
                allow_steal_stale=False,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            task_claims = list(
                pool.map(
                    lambda pair: claim_task(*pair),
                    ((writer, "smoke-writer"), (reader, "smoke-reader")),
                )
            )

        queue_name = f"ci_authoritative_queue:{marker_key}"
        wake_id = writer.enqueue_wake(target_agent=queue_name, task={"marker": marker})
        wake_barrier = threading.Barrier(2)

        def claim_wake(store: SharedStateStore):
            wake_barrier.wait()
            return store.claim_wake_item(target_agent=queue_name, claimer_pid=os.getpid())

        with ThreadPoolExecutor(max_workers=2) as pool:
            wake_claims = list(pool.map(claim_wake, (writer, reader)))

    if semantic != marker:
        raise RuntimeError(f"Turso semantic roundtrip mismatch: {semantic!r}")
    if not cached or cached.get("value") != marker:
        raise RuntimeError(f"Turso cache roundtrip mismatch: {cached!r}")
    if sum(bool(claim.get("claimed")) for claim in spawn_claims) != 1:
        raise RuntimeError(f"Turso authoritative spawn claim mismatch: {spawn_claims!r}")
    if sum(bool(claim.success) for claim in task_claims) != 1:
        raise RuntimeError(f"Turso authoritative task claim mismatch: {task_claims!r}")
    claimed_wakes = [claim for claim in wake_claims if claim is not None]
    if len(claimed_wakes) != 1 or claimed_wakes[0]["id"] != wake_id:
        raise RuntimeError(f"Turso authoritative wake claim mismatch: {wake_claims!r}")

    print(
        "Turso online roundtrip OK "
        f"backend={describe_backend(settings.db_url)} marker={marker_key}"
    )
    _bootstrap_live_session_mesh()


if __name__ == "__main__":
    main()
