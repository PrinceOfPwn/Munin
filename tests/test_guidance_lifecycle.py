# tags: [tests, guidance-lifecycle, transition_guidance_state, guidance.queued, guidance.delivered_to_runtime, guidance.applied_to_model_step, GUIDANCE_STATES, run_guidance_queue, PR-2D]
"""PR-2D — guidance lifecycle columns + ``guidance.*`` SSE event contracts.

Covers:

* ``run_guidance_queue`` exposes the PR-2D lifecycle columns
  (``state`` with a CHECK constraint over the six lifecycle values,
  ``state_updated_at_ms``, ``applied_message_id``, ``superseded_by_id``)
  installed idempotently via the PRAGMA-guarded ADD COLUMN path.
* ``ProductionStore.enqueue_guidance`` seeds a row in ``queued`` state and
  emits a durable ``guidance.queued`` event.
* ``ProductionStore.consume_pending_guidance`` advances the row to
  ``delivered_to_runtime`` and emits the matching lifecycle event.
* ``ProductionStore.transition_guidance_state`` advances an arbitrary
  row to any lifecycle state and emits ``guidance.<state>``; invalid
  states raise ``ValueError`` before any write (a smoother error than a
  SQLite ``IntegrityError``).
* The ``state`` CHECK constraint rejects bad payloads at the DB layer when
  bypassed (defence in depth — confirmed via a raw ``INSERT`` /
  ``transition_guidance_state`` round trip).

Uses a dedicated ``production_store`` fixture (a real
``ProductionStore.for_sqlite``) instead of the conftest ``store`` fixture so
the test can read the ``run_guidance_queue`` row directly and call
``transition_guidance_state`` against the hot SQLite backend.  E2E coverage
(float the lifecycle through a live supervisor) is the job of
``tests/test_guidance_lifecycle_e2e.py`` (card 2E).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def production_store(tmp_path: Path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "guidance.sqlite", master_key=b"g" * 32)


def _make_actor(store):
    return store.create_user(
        username="guidance-op", password="a strong guidance password", role="operator"
    )


def _make_conversation(store, *, owner_id):
    return store.create_conversation(owner_id=owner_id, title="Guidance lifecycle")


def _queued_run(store, *, actor_id, conversation_id, key="guidance-key"):
    return store.create_turn(
        actor_id=actor_id,
        conversation_id=conversation_id,
        content="Run that will receive operator guidance",
        idempotency_key=key,
    )


def _states_seen(store, *, run_id):
    return [event["kind"] for event in store.list_run_events(run_id)]


def test_enqueue_guidance_seeds_queued_state_and_emits_event(production_store):
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(production_store, actor_id=operator["id"], conversation_id=conversation["id"])
    run_id = turn["run"]["id"]

    entry = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="pivot to lateral movement",
        target_agent_id=None,
    )

    assert entry["state"] == "queued"
    assert entry["state_updated_at_ms"] == entry["created_at_ms"]
    assert entry["applied_message_id"] is None
    assert entry["superseded_by_id"] is None

    rows = production_store.list_run_guidance(run_id=run_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["state"] == "queued"
    assert row["state_updated_at_ms"] == entry["created_at_ms"]

    # ``guidance.queued`` is the first lifecycle event against the run.
    kinds = _states_seen(production_store, run_id=run_id)
    assert "guidance.queued" in kinds


def test_consume_pending_guidance_transitions_to_delivered_to_runtime(production_store):
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="guidance-deliver",
    )
    run_id = turn["run"]["id"]

    entry = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="confirm scope on the file share",
    )

    drained = production_store.consume_pending_guidance(run_id=run_id, delivered_at_step=1)
    assert len(drained) == 1
    assert drained[0]["id"] == entry["id"]
    assert drained[0]["state"] == "delivered_to_runtime"
    assert drained[0]["state_updated_at_ms"] == drained[0]["consumed_at_ms"]
    assert drained[0]["delivered_at_step"] == 1

    rows = production_store.list_run_guidance(run_id=run_id)
    assert rows[0]["state"] == "delivered_to_runtime"

    kinds = _states_seen(production_store, run_id=run_id)
    assert "guidance.queued" in kinds
    assert "guidance.delivered_to_runtime" in kinds


def test_transition_to_applied_to_model_step_appends_event(production_store):
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="guidance-apply",
    )
    run_id = turn["run"]["id"]

    entry = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="describe the persistence mechanic",
    )
    production_store.consume_pending_guidance(run_id=run_id, delivered_at_step=1)

    result = production_store.transition_guidance_state(
        entry["id"],
        "applied_to_model_step",
        applied_message_id="msg_applied",
    )
    assert result["state"] == "applied_to_model_step"
    assert result["applied_message_id"] == "msg_applied"
    assert result["state_updated_at_ms"] > 0

    rows = production_store.list_run_guidance(run_id=run_id)
    assert rows[0]["state"] == "applied_to_model_step"
    assert rows[0]["applied_message_id"] == "msg_applied"

    kinds = _states_seen(production_store, run_id=run_id)
    assert "guidance.applied_to_model_step" in kinds


def test_transition_to_expired_and_superseded_carries_aux_ids(production_store):
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="guidance-supersede",
    )
    run_id = turn["run"]["id"]

    old = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="initial hint",
    )
    new = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="corrected hint",
    )

    production_store.transition_guidance_state(
        old["id"],
        "superseded",
        superseded_by_id=new["id"],
    )
    rows = production_store.list_run_guidance(run_id=run_id)
    by_id = {row["id"]: row for row in rows}
    assert by_id[old["id"]]["state"] == "superseded"
    assert by_id[old["id"]]["superseded_by_id"] == new["id"]

    production_store.transition_guidance_state(new["id"], "expired")
    rows = production_store.list_run_guidance(run_id=run_id)
    by_id = {row["id"]: row for row in rows}
    assert by_id[new["id"]]["state"] == "expired"

    kinds = _states_seen(production_store, run_id=run_id)
    assert "guidance.superseded" in kinds
    assert "guidance.expired" in kinds


def test_transition_to_undelivered_runs_for_unconsumed_row(production_store):
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="guidance-undelivered",
    )
    run_id = turn["run"]["id"]

    entry = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="never reached the model step",
    )

    production_store.transition_guidance_state(entry["id"], "undelivered")
    rows = production_store.list_run_guidance(run_id=run_id)
    assert rows[0]["state"] == "undelivered"
    kinds = _states_seen(production_store, run_id=run_id)
    assert "guidance.undelivered" in kinds


def test_transition_guidance_state_rejects_invalid_state(production_store):
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="guidance-bad-state",
    )
    run_id = turn["run"]["id"]
    entry = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="look before we leap",
    )

    with pytest.raises(ValueError):
        production_store.transition_guidance_state(entry["id"], "in_flight")


def test_transition_guidance_state_rejects_missing_guidance_id(production_store):
    with pytest.raises(KeyError):
        production_store.transition_guidance_state("no_such_guidance", "expired")


def test_state_check_constraint_rejects_bad_value_at_db_layer(production_store):
    """The CHECK constraint is the defence-in-depth layer underneath the
    Python-level ``ValueError`` from ``transition_guidance_state``.  Writing a
    bogus state through the store's own connection must fail — this confirms
    the constraint is installed by ``_install_fase2_essentials``."""
    operator = _make_actor(production_store)
    conversation = _make_conversation(production_store, owner_id=operator["id"])
    turn = _queued_run(
        production_store,
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        key="guidance-db-check",
    )
    run_id = turn["run"]["id"]
    entry = production_store.enqueue_guidance(
        run_id=run_id,
        actor_id=operator["id"],
        actor_username="guidance-op",
        body="probe the constraint",
    )

    with production_store._transaction() as conn:  # noqa: SLF001
        # ``state`` CHECK should reject the bogus value at the SQLite layer.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE run_guidance_queue SET state=? WHERE id=?",
                ("totally_unknown", entry["id"]),
            )
