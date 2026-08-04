# tags: [tests, artifacts-readmodel, PR-6A, conversation_artifacts, renderer, provenance, preview_url, download_url, FASE3-migration, complete_run, fenced-artifacts, _insert_artifact, get_artifact, add_artifact]
"""PR-6A — rich artifact metadata on the store layer.

Asserts:

* ``conversation_artifacts`` gains the PR-6 renderer-contract columns
  (``renderer``, ``version``, ``provenance``, ``preview_url``, ``download_url``)
  through the idempotent Fase-3 PRAGMA-guarded migration — including on a
  database created with the pre-PR-6 11-column DDL (the forward-compat path).
* ``add_artifact`` / ``get_artifact`` round-trip the rich metadata, with
  sane defaults (``version=1``, ``renderer=None``) when omitted.
* ``complete_run``'s fenced-code extraction still inserts artifacts without
  the new fields — a regression guard for the pre-existing write path.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "artifacts_store.sqlite", master_key=b"a" * 32)


def _actor_and_conversation(store):
    operator = store.create_user(
        username="artifacts-store-op", password="a strong artifacts store password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Artifact store")
    return operator, conversation


def test_migration_adds_pr6_artifact_columns(production_store):
    with production_store._read_only() as conn:
        names = {str(row["name"]) for row in conn.execute("PRAGMA table_info(conversation_artifacts)").fetchall()}
    for column in ("renderer", "version", "provenance", "preview_url", "download_url"):
        assert column in names, f"PR-6 column {column} missing after migrate()"

    # Idempotency: re-running migrate() must be a no-op (no duplicate columns).
    production_store.migrate()
    with production_store._read_only() as conn:
        names_again = {str(row["name"]) for row in conn.execute("PRAGMA table_info(conversation_artifacts)").fetchall()}
    assert names_again == names

    # The composite listing index exists.
    with production_store._read_only() as conn:
        index = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_conversation_artifacts_conv_created'"
        ).fetchone()
    assert index is not None


def test_migration_upgrades_pre_pr6_database(tmp_path: Path):
    """A DB carrying the old 11-column artifact table must gain the PR-6 columns.

    Builds a fully-migrated store, swaps ``conversation_artifacts`` back to its
    pre-PR-6 shape (the exact v1.0.0 DDL) with a legacy row, then re-runs
    ``migrate()`` — the Fase-3 PRAGMA-guarded ADD COLUMN path must upgrade it.
    """
    import sqlite3

    import munin.production.store as store_module

    path = tmp_path / "legacy.sqlite"
    store_module.ProductionStore.for_sqlite(path, master_key=b"a" * 32)

    raw = sqlite3.connect(path)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("DROP TABLE conversation_artifacts")
        raw.execute(
            "CREATE TABLE conversation_artifacts ("
            "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, message_id TEXT, run_id TEXT, filename TEXT NOT NULL,"
            "media_type TEXT NOT NULL, language TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,"
            "size_bytes INTEGER NOT NULL, created_at_ms INTEGER NOT NULL,"
            "FOREIGN KEY(conversation_id) REFERENCES conversations(id)"
            ")"
        )
        raw.execute(
            "INSERT INTO conversation_artifacts (id,conversation_id,message_id,run_id,filename,media_type,language,content,content_hash,size_bytes,created_at_ms) "
            "VALUES ('legacy-artifact','conv','msg','run','old.txt','text/plain','text','body','hash',4,1)"
        )
        raw.commit()
    finally:
        raw.close()

    store = store_module.ProductionStore.for_sqlite(path, master_key=b"a" * 32)
    with store._read_only() as conn:
        names = {str(row["name"]) for row in conn.execute("PRAGMA table_info(conversation_artifacts)").fetchall()}
    for column in ("renderer", "version", "provenance", "preview_url", "download_url"):
        assert column in names

    # Pre-existing rows fall back to version=1 under the DEFAULT.
    with store._read_only() as conn:
        row = conn.execute("SELECT version,renderer,provenance,preview_url,download_url FROM conversation_artifacts WHERE id='legacy-artifact'").fetchone()
    assert int(row["version"]) == 1
    assert row["renderer"] is None
    assert row["provenance"] is None
    assert row["preview_url"] is None
    assert row["download_url"] is None

    # The upgraded table accepts the PR-6 16-column write path.
    operator = store.create_user(
        username="legacy-upgrade-op", password="a strong upgrade password", role="operator"
    )
    conversation = store.create_conversation(owner_id=operator["id"], title="Upgrade test")
    added = store.add_artifact(
        actor_id=operator["id"], conversation_id=conversation["id"], filename="new.txt",
        media_type="text/plain", language="text", content="post-upgrade",
        renderer="text", version=4,
    )
    fetched = store.get_artifact(actor_id=operator["id"], artifact_id=added["id"])
    assert int(fetched["version"]) == 4
    assert fetched["renderer"] == "text"


def test_add_and_get_artifact_round_trip_rich_metadata(production_store):
    operator, conversation = _actor_and_conversation(production_store)

    added = production_store.add_artifact(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        filename="findings.py",
        media_type="text/x-python",
        language="python",
        content="print('scan complete')\n",
        run_id="run-pr6",
        renderer="code",
        version=3,
        provenance="evidence:valravn-report",
        preview_url="/api/artifacts/peek",
        download_url="/api/artifacts/direct",
    )
    assert "content" not in added  # the write path never returns the body

    fetched = production_store.get_artifact(actor_id=operator["id"], artifact_id=added["id"])
    assert fetched["renderer"] == "code"
    assert int(fetched["version"]) == 3
    assert fetched["provenance"] == "evidence:valravn-report"
    assert fetched["preview_url"] == "/api/artifacts/peek"
    assert fetched["download_url"] == "/api/artifacts/direct"
    assert fetched["filename"] == "findings.py"
    assert fetched["media_type"] == "text/x-python"
    assert fetched["language"] == "python"
    assert fetched["content"] == "print('scan complete')\n"
    assert fetched["conversation_id"] == conversation["id"]
    assert fetched["run_id"] == "run-pr6"


def test_add_artifact_defaults(production_store):
    operator, conversation = _actor_and_conversation(production_store)
    added = production_store.add_artifact(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        filename="plain.txt",
        media_type="text/plain",
        language="text",
        content="defaults",
    )
    fetched = production_store.get_artifact(actor_id=operator["id"], artifact_id=added["id"])
    assert int(fetched["version"]) == 1
    assert fetched["renderer"] is None
    assert fetched["provenance"] is None
    assert fetched["preview_url"] is None
    assert fetched["download_url"] is None


def test_complete_run_extraction_still_inserts_without_new_fields(production_store):
    """Regression: the pre-PR-6 fenced-code write path must keep working."""
    operator, conversation = _actor_and_conversation(production_store)
    turn = production_store.create_turn(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        content="Run that finishes with a fenced artifact",
        idempotency_key="artifacts-complete-run",
    )
    run_id = turn["run"]["id"]

    from munin.production.chat import _claim_direct

    lease_token, _assistant_message_id = _claim_direct(production_store, run_id=run_id)
    completed = production_store.complete_run(
        run_id=run_id,
        lease_token=lease_token,
        content="Report:\n\n```python\nprint('done')\n```\n",
        outcome="completed",
    )
    assert completed is True

    artifacts = production_store.list_artifacts_for_run(actor_id=operator["id"], run_id=run_id)
    assert len(artifacts) == 1
    assert artifacts[0]["filename"].endswith(".py")
    assert int(artifacts[0]["version"]) == 1  # DEFAULT applied for the legacy path
    assert artifacts[0]["renderer"] is None
