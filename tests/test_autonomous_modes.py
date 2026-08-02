"""Fase 3 (issue #14): autonomous modes — policy, durable plan, goals, timers.

Deterministic unit tests.  No real LLM is invoked: middleware is exercised
with a fake model request/override pair, store methods run on throwaway
SQLite, and the timer wake-up path uses a fake store.
"""

from __future__ import annotations

import asyncio
import json

import pytest


# ---------------------------------------------------------------------------
# Modes policy (munin.core.autonomy.modes)
# ---------------------------------------------------------------------------


def test_operation_mode_parse_accepts_strings_and_defaults():
    from munin.core.autonomy.modes import OperationMode

    assert OperationMode.parse("goal") is OperationMode.GOAL
    assert OperationMode.parse("BEAST") is OperationMode.BEAST
    assert OperationMode.parse(None) is OperationMode.STANDARD
    assert OperationMode.parse("") is OperationMode.STANDARD
    assert OperationMode.parse("bogus") is OperationMode.STANDARD


def test_mode_policy_approval_levels_and_gates():
    from munin.core.autonomy.modes import OperationMode, policy_for

    standard = policy_for(OperationMode.STANDARD)
    assert standard.approval_levels == frozenset({"active", "admin"})
    assert standard.planning_enabled is False
    assert standard.requires_goal is False
    assert standard.requires_scope is False

    yolo = policy_for(OperationMode.YOLO)
    assert yolo.approval_levels == frozenset({"admin"})
    assert yolo.planning_enabled is True
    assert yolo.requires_goal is False

    goal = policy_for(OperationMode.GOAL)
    assert goal.requires_goal is True
    assert goal.planning_enabled is True
    assert goal.requires_scope is False

    beast = policy_for(OperationMode.BEAST)
    assert beast.requires_scope is True
    assert beast.requires_goal is False  # scope is the hard beast gate; goal is optional
    assert beast.delegation is True
    assert beast.planning_enabled is True


def test_critical_approval_floor_is_immutable_across_modes():
    from munin.core.autonomy.modes import (
        CRITICAL_APPROVAL_FLOOR,
        OperationMode,
        policy_for,
    )

    for mode in OperationMode:
        policy = policy_for(mode)
        assert policy.approval_required_for("critical") is True
    assert "critical" in CRITICAL_APPROVAL_FLOOR


def test_beast_limits_are_env_observable_and_capped(monkeypatch):
    from munin.core.autonomy.modes import OperationMode, policy_for

    monkeypatch.setenv("MUNIN_BEAST_MODEL_CALL_LIMIT", "12")
    monkeypatch.setenv("MUNIN_BEAST_TOOL_CALL_LIMIT", "64")
    beast = policy_for(OperationMode.BEAST)
    assert beast.model_call_limit == 12
    assert beast.tool_call_limit == 64


def test_mode_contract_text_mentions_the_mode():
    from munin.core.autonomy.modes import OperationMode, mode_contract

    assert "beast" in mode_contract(OperationMode.BEAST)
    assert "goal" in mode_contract(OperationMode.GOAL)
    assert "standard" in mode_contract(OperationMode.STANDARD)
    assert "approval" in mode_contract(OperationMode.YOLO)


# ---------------------------------------------------------------------------
# Store: goals / todo_events / timers (munin.production.store)
# ---------------------------------------------------------------------------


@pytest.fixture
def production_store(tmp_path):
    from munin.production.store import ProductionStore

    return ProductionStore.for_sqlite(tmp_path / "production.sqlite", master_key=b"m" * 32)


def _seed_conversation(store) -> tuple[dict, dict]:
    operator = store.create_user(username="op", password="strong passphrase", role="operator")
    conversation = store.create_conversation(owner_id=operator["id"], title="Autonomy")
    return operator, conversation


def test_goal_lifecycle_create_get_update_and_audit(production_store):
    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"],
        conversation_id=conversation["id"],
        objective="Map the attack surface",
        success_criteria=["service inventory", "two findings"],
        scope={"targets": ["10.0.0.0/24"]},
        mode="goal",
    )
    assert goal["id"].startswith("goal_")
    assert goal["state"] == "active"

    hydrated = production_store.get_goal_for_conversation(conversation_id=conversation["id"])
    assert hydrated["objective"] == "Map the attack surface"
    assert hydrated["scope"] == {"targets": ["10.0.0.0/24"]}
    assert production_store.list_goals_for_actor(actor_id=operator["id"])[0]["id"] == goal["id"]

    updated = production_store.update_goal(actor_id=operator["id"], goal_id=goal["id"], state="paused")
    assert updated["state"] == "paused"
    with pytest.raises(ValueError, match="invalid goal fields"):
        production_store.update_goal(actor_id=operator["id"], goal_id=goal["id"], nope=1)
    with pytest.raises(KeyError):
        production_store.update_goal(actor_id=operator["id"], goal_id="goal_missing", state="done")


def test_goal_requires_objective(production_store):
    operator, conversation = _seed_conversation(production_store)
    with pytest.raises(ValueError, match="objective is required"):
        production_store.create_goal(actor_id=operator["id"], conversation_id=conversation["id"], objective="   ")


def test_todo_events_reconstruct_plan_and_replan_resets(production_store):
    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Find the hole"
    )
    run_id = f"run-{conversation['id']}"
    ev1 = production_store.append_todo_event(
        run_id=run_id, conversation_id=conversation["id"], goal_id=goal["id"],
        item_id="p-1", op="create",
        item_json=json.dumps({"id": "p-1", "title": "Enumerate hosts", "status": "pending"}),
        reason="plan start",
    )
    ev2 = production_store.append_todo_event(
        run_id=run_id, conversation_id=conversation["id"], goal_id=goal["id"],
        item_id="p-1", op="set_state",
        item_json=json.dumps({"id": "p-1", "title": "Enumerate hosts", "status": "in_progress"}),
        reason="started",
    )
    assert ev2["sequence"] == ev1["sequence"] + 1

    items = production_store.plan_items(conversation_id=conversation["id"])
    assert [item["id"] for item in items] == ["p-1"]
    assert items[0]["status"] == "in_progress"

    production_store.append_todo_event(
        run_id=run_id, conversation_id=conversation["id"], goal_id=goal["id"],
        item_id="", op="replan",
        item_json=json.dumps({"reset_ids": ["p-1"]}),
        reason="hypothesis failed",
    )
    after = production_store.plan_items(conversation_id=conversation["id"])
    assert after[0]["status"] == "pending"

    snapshot = production_store.plan_snapshot(conversation_id=conversation["id"])
    assert snapshot["goal"]["id"] == goal["id"]
    assert snapshot["items"][0]["id"] == "p-1"


def test_hypothesis_events_are_excluded_from_plan_items(production_store):
    operator, conversation = _seed_conversation(production_store)
    production_store.append_todo_event(
        run_id="run-1", conversation_id=conversation["id"], op="hypothesis",
        item_json=json.dumps({"statement": "Host A is vulnerable", "status": "proposed"}),
    )
    assert production_store.plan_items(conversation_id=conversation["id"]) == []


def test_timer_lifecycle_and_fencing(production_store):
    import time

    operator, conversation = _seed_conversation(production_store)
    now = int(time.time() * 1000)
    timer = production_store.create_timer(
        conversation_id=conversation["id"], actor_id=operator["id"],
        kind="goal_eval", due_at_ms=now - 5_000, cadence_ms=60_000, payload={"wakeup": True},
    )
    assert timer["state"] == "active"
    assert timer["tick_count"] == 0
    assert production_store.get_timer(timer["id"])["kind"] == "goal_eval"
    assert [t["id"] for t in production_store.list_timers(conversation_id=conversation["id"])] == [timer["id"]]

    with pytest.raises(ValueError, match="cadence"):
        production_store.create_timer(
            conversation_id=conversation["id"], actor_id=operator["id"],
            kind="goal_eval", due_at_ms=0, cadence_ms=1_000,
        )

    claimed = production_store.claim_due_timers(worker_id="w1", lease_ms=60_000, now_ms=now)
    assert len(claimed) == 1
    epoch = claimed[0]["fencing_epoch"]
    assert epoch == 1

    # Stale fencing epoch can never complete the tick.
    assert production_store.complete_timer_tick(
        timer_id=timer["id"], fencing_epoch=epoch - 1,
        last_tick_at_ms=2_000, next_due_at_ms=62_000, tick_count=99,
    ) is False
    assert production_store.get_timer(timer["id"])["tick_count"] == 0

    # Fresh fenced owner completes exactly once.
    assert production_store.complete_timer_tick(
        timer_id=timer["id"], fencing_epoch=epoch,
        last_tick_at_ms=2_000, next_due_at_ms=62_000, tick_count=1,
    ) is True
    ticked = production_store.get_timer(timer["id"])
    assert ticked["tick_count"] == 1
    assert ticked["due_at_ms"] == 62_000
    assert ticked["lease_token"] is None

    paused = production_store.pause_timer(actor_id=operator["id"], timer_id=timer["id"])
    assert paused["state"] == "paused"
    with pytest.raises(KeyError):
        production_store.pause_timer(actor_id=operator["id"], timer_id=timer["id"])
    cancelled = production_store.cancel_timer(actor_id=operator["id"], timer_id=timer["id"])
    assert cancelled["state"] == "cancelled"
    # Cancelled timers are no longer claimable.
    assert production_store.claim_due_timers(worker_id="w1", lease_ms=60_000, now_ms=now + 10_000) == []


def test_create_turn_persists_mode_and_goal(production_store):
    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Probe the edge"
    )
    turn = production_store.create_turn(
        actor_id=operator["id"], conversation_id=conversation["id"],
        content="Go", idempotency_key="autonomy-turn-1", mode="beast", goal_id=goal["id"],
    )
    ctx = production_store.run_execution_context(run_id=turn["run"]["id"])
    assert ctx["mode"] == "beast"
    assert ctx["goal_id"] == goal["id"]
    assert production_store.get_run(turn["run"]["id"])["state"] == "queued"


def test_split_store_forwards_goals_plan_and_timers(tmp_path):
    from munin.production.store import MuninStore, ProductionStore

    durable = ProductionStore.for_sqlite(tmp_path / "durable.sqlite", master_key=b"d" * 32)
    hot = ProductionStore.for_sqlite(tmp_path / "hot.sqlite", master_key=b"d" * 32)
    store = MuninStore(hot=hot, durable=durable)
    operator = store.create_user(username="split", password="strong passphrase", role="operator")
    conversation = store.create_conversation(owner_id=operator["id"], title="Split autonomy")
    goal = store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Split goal"
    )
    assert store.get_goal_for_conversation(conversation_id=conversation["id"])["id"] == goal["id"]
    store.append_todo_event(
        run_id="run-x", conversation_id=conversation["id"], goal_id=goal["id"],
        item_id="p-1", op="create",
        item_json=json.dumps({"id": "p-1", "title": "Scan", "status": "pending"}),
    )
    assert store.plan_snapshot(conversation_id=conversation["id"])["items"][0]["id"] == "p-1"
    timer = store.create_timer(
        conversation_id=conversation["id"], actor_id=operator["id"],
        kind="goal_eval", due_at_ms=0, cadence_ms=30_000,
    )
    assert store.get_timer(timer["id"])["state"] == "active"


# ---------------------------------------------------------------------------
# Middleware: durable plan + goal injection (fake model request)
# ---------------------------------------------------------------------------


class _FakeModelRequest:
    """Minimal stand-in for langchain's ``ChatModelRequest``."""

    def __init__(self, system_message=None):
        self.system_message = system_message
        self.overridden = None

    def override(self, **kwargs):
        self.overridden = kwargs
        return self


def _fake_handler(request):
    return request


def _activate_context(*, vars_and_values):
    """Set contextvars, returning a restore callable (Python 3.14-safe:
    ``Token.reset`` was removed)."""
    old = [(var, var.get()) for var, _ in vars_and_values]
    for var, value in vars_and_values:
        var.set(value)

    def restore() -> None:
        for var, value in old:
            var.set(value)

    return restore


def test_plan_middleware_composes_system_message_from_context(production_store):
    from langchain_core.messages import SystemMessage

    from munin.core.autonomy import planning
    from munin.core.autonomy.context import (
        ACTIVE_GOAL,
        ACTIVE_MODE,
        ACTIVE_PLAN_SNAPSHOT,
        ACTIVE_STORE,
    )

    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Map everything"
    )
    snapshot = {"goal": goal, "items": [{"id": "p-1", "title": "Scan", "status": "in_progress"}]}

    mw = planning.TodoPlanMiddleware()
    request = _FakeModelRequest(system_message=SystemMessage(content="base system"))

    restore = _activate_context(
        vars_and_values=[
            (ACTIVE_STORE, production_store),
            (ACTIVE_MODE, "goal"),
            (ACTIVE_GOAL, goal),
            (ACTIVE_PLAN_SNAPSHOT, snapshot),
        ]
    )
    try:
        result = mw.wrap_model_call(request, _fake_handler)
    finally:
        restore()

    assert result is request
    composed = request.overridden["system_message"]
    assert isinstance(composed, SystemMessage)
    text = "".join(block.get("text", "") for block in composed.content)
    assert "Map everything" in text
    assert "Scan" in text
    assert "Durable plan discipline" in text


def test_plan_middleware_noop_without_plan_or_planning_mode(production_store):
    from langchain_core.messages import SystemMessage

    from munin.core.autonomy import planning
    from munin.core.autonomy.context import ACTIVE_MODE

    mw = planning.TodoPlanMiddleware()
    request = _FakeModelRequest(system_message=SystemMessage(content="base"))
    restore = _activate_context(vars_and_values=[(ACTIVE_MODE, "standard")])
    try:
        result = mw.wrap_model_call(request, _fake_handler)
    finally:
        restore()
    assert result is request
    assert request.overridden is None


def test_goal_middleware_injects_goal_block(production_store):
    from langchain_core.messages import SystemMessage

    from munin.core.autonomy import goals
    from munin.core.autonomy.context import ACTIVE_GOAL

    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"],
        objective="Win the engagement", success_criteria=["intel report"],
    )
    mw = goals.GoalMiddleware()
    request = _FakeModelRequest(system_message=SystemMessage(content="base"))
    restore = _activate_context(vars_and_values=[(ACTIVE_GOAL, goal)])
    try:
        mw.wrap_model_call(request, _fake_handler)
    finally:
        restore()

    composed = request.overridden["system_message"]
    text = "".join(block.get("text", "") for block in composed.content)
    assert "Persistent goal" in text
    assert "Win the engagement" in text
    assert "intel report" in text

    rendered = goals.render_goal_block(None)
    assert rendered == ""
    assert goals.new_goal_id().startswith("goal_")


def test_todo_update_tool_writes_durable_events_and_emits(production_store):
    from langchain_core.messages import ToolMessage

    from munin.core.autonomy import planning
    from munin.core.autonomy.context import (
        ACTIVE_EMITTER,
        ACTIVE_GOAL,
        ACTIVE_STORE,
    )
    from munin.core.middleware.progress_emit import ACTIVE_RUN_ID

    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Probe"
    )
    emitted: list[dict] = []

    def emitter(envelope: dict) -> None:
        emitted.append(envelope)

    restore = _activate_context(
        vars_and_values=[
            (ACTIVE_STORE, production_store),
            (ACTIVE_GOAL, goal),
            (ACTIVE_EMITTER, emitter),
            (ACTIVE_RUN_ID, "run-tool-1"),
        ]
    )
    try:
        result = planning.todo_update.invoke(
            {
                "name": "todo_update",
                "type": "tool_call",
                "id": "call-plan-1",
                "args": {
                    "ops": [
                        {"op": "create", "item_id": "p-1", "title": "Enumerate hosts", "priority": "high"},
                        {"op": "set_state", "item_id": "p-1", "state": "in_progress", "reason": "working"},
                    ]
                },
            }
        )
    finally:
        restore()

    assert result.update["messages"][0].__class__ is ToolMessage
    assert "Enumerate hosts" in result.update["messages"][0].content
    assert len(emitted) == 2
    assert emitted[0]["kind"] == "todo"
    assert emitted[0]["op"] == "create"
    items = production_store.plan_items(conversation_id=conversation["id"])
    assert items[0]["status"] == "in_progress"
    assert items[0]["priority"] == "high"


def test_todo_update_rejects_invalid_ops(production_store):
    from munin.core.autonomy import planning
    from munin.core.autonomy.context import ACTIVE_GOAL, ACTIVE_STORE
    from munin.core.middleware.progress_emit import ACTIVE_RUN_ID

    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Probe"
    )
    restore = _activate_context(
        vars_and_values=[
            (ACTIVE_STORE, production_store),
            (ACTIVE_GOAL, goal),
            (ACTIVE_RUN_ID, "run-tool-2"),
        ]
    )
    try:
        result = planning.todo_update.invoke(
            {"name": "todo_update", "type": "tool_call", "id": "call-bad-1",
             "args": {"ops": [{"op": "set_state", "item_id": "nope", "state": "done"}]}}
        )
        created = planning.todo_update.invoke(
            {"name": "todo_update", "type": "tool_call", "id": "call-ok-1",
             "args": {"ops": [{"op": "create", "item_id": "p-1", "title": "x"}]}}
        )
        assert created.update["messages"][0].status == "success"
        duplicate = planning.todo_update.invoke(
            {"name": "todo_update", "type": "tool_call", "id": "call-bad-2",
             "args": {"ops": [{"op": "create", "item_id": "p-1", "title": "x"}]}}
        )
        blank = planning.todo_update.invoke(
            {"name": "todo_update", "type": "tool_call", "id": "call-bad-3",
             "args": {"ops": [{"op": "create", "item_id": "p-2", "title": "  "}]}}
        )
    finally:
        restore()

    for outcome in (result, duplicate, blank):
        message = outcome.update["messages"][0]
        assert message.status == "error"
        assert "failed" in message.content


def test_hypothesis_tool_records_and_validates(production_store):
    from munin.core.autonomy import planning
    from munin.core.autonomy.context import (
        ACTIVE_EMITTER,
        ACTIVE_GOAL,
        ACTIVE_STORE,
    )
    from munin.core.middleware.progress_emit import ACTIVE_RUN_ID

    operator, conversation = _seed_conversation(production_store)
    goal = production_store.create_goal(
        actor_id=operator["id"], conversation_id=conversation["id"], objective="Probe"
    )
    emitted: list[dict] = []
    restore = _activate_context(
        vars_and_values=[
            (ACTIVE_STORE, production_store),
            (ACTIVE_GOAL, goal),
            (ACTIVE_EMITTER, emitted.append),
            (ACTIVE_RUN_ID, "run-tool-3"),
        ]
    )
    try:
        ok = planning.hypothesis.invoke(
            {"name": "hypothesis", "type": "tool_call", "id": "call-hyp-1",
             "args": {"statement": "Host A runs Apache 2.4.49", "status": "confirmed", "evidence": "banner"}}
        )
        bad = planning.hypothesis.invoke(
            {"name": "hypothesis", "type": "tool_call", "id": "call-hyp-2",
             "args": {"statement": "x", "status": "maybe"}}
        )
    finally:
        restore()

    assert "Hypothesis recorded" in ok.update["messages"][0].content
    assert bad.update["messages"][0].status == "error"
    assert emitted[0]["kind"] == "hypothesis"
    assert emitted[0]["status"] == "confirmed"


# ---------------------------------------------------------------------------
# Chat API gates + plan/timer/goal routes (TestClient)
# ---------------------------------------------------------------------------


def _authed_client(production_store, monkeypatch, username="autonomy-admin"):
    """TestClient with a fake chat stream that completes the run."""
    import json as _json

    from starlette.testclient import TestClient

    from munin.production.asgi import create_app

    monkeypatch.setenv("MUNIN_ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("MUNIN_COOKIE_SECURE", "0")

    async def fake_chat_stream(request, **kwargs):  # noqa: ARG001
        yield b": munin-chat-stream v1\n\n"
        kwargs["store"].complete_run(
            run_id=kwargs["run_id"],
            lease_token=kwargs["lease_token"],
            content="fake autonomous stream",
            outcome="completed",
        )
        envelope = _json.dumps(
            {"kind": "run_state", "run_id": kwargs["run_id"], "state": "completed", "content": "done"}
        )
        yield f"id: 1\nevent: run-event\ndata: {envelope}\n\n".encode()
        yield b"event: close\ndata: {}\n\n"

    monkeypatch.setattr("munin.production.chat._stream_chat", fake_chat_stream)
    client = TestClient(create_app(production_store))
    assert client.post("/api/auth/bootstrap", json={"username": username, "password": "very secure test password"}).status_code == 201
    login = client.post("/api/auth/login", json={"username": username, "password": "very secure test password"})
    csrf = login.json()["csrf_token"]
    headers = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin", "X-CSRF-Token": csrf}
    conversation = client.post("/api/conversations", headers=headers, json={"title": "Autonomy"}).json()["data"]
    return client, headers, conversation


def _send(client, headers, conversation_id, *, mode=None, goal=None, scope=None, content="Do the thing", key="chat-1"):
    body = {"conversation_id": conversation_id, "content": content}
    if mode:
        body["mode"] = mode
    if goal is not None:
        body["goal"] = goal
    if scope is not None:
        body["scope"] = scope
    return client.post("/api/chat", headers={**headers, "Idempotency-Key": key}, json=body)


def test_chat_rejects_unknown_mode(production_store, monkeypatch):
    client, headers, conversation = _authed_client(production_store, monkeypatch)
    response = _send(client, headers, conversation["id"], mode="ultra", key="gate-unknown-mode")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_body"


def test_chat_goal_mode_requires_persistent_goal(production_store, monkeypatch):
    client, headers, conversation = _authed_client(production_store, monkeypatch)
    missing = _send(client, headers, conversation["id"], mode="goal", key="gate-goal-missing")
    assert missing.status_code == 400
    assert "goal" in missing.json()["error"]["message"]

    with_goal = _send(
        client, headers, conversation["id"], mode="goal",
        goal={"objective": "Map the target", "success_criteria": ["inventory"]},
        key="gate-goal-ok",
    )
    assert with_goal.status_code == 200
    goal = production_store.get_goal_for_conversation(conversation_id=conversation["id"])
    assert goal["objective"] == "Map the target"


def test_chat_beast_mode_requires_scope(production_store, monkeypatch):
    client, headers, conversation = _authed_client(production_store, monkeypatch)
    no_scope = _send(
        client, headers, conversation["id"], mode="beast",
        goal={"objective": "Deep dive", "success_criteria": []},
        key="gate-beast-noscope",
    )
    assert no_scope.status_code == 400
    assert "scope" in no_scope.json()["error"]["message"]

    scoped = _send(
        client, headers, conversation["id"], mode="beast",
        goal={"objective": "Deep dive", "success_criteria": ["x"], "scope": {"targets": ["10.0.0.0/24"]}},
        key="gate-beast-ok",
    )
    assert scoped.status_code == 200


def test_chat_goal_payload_with_existing_id_must_match_conversation(production_store, monkeypatch):
    client, headers, conversation = _authed_client(production_store, monkeypatch)
    _send(
        client, headers, conversation["id"], mode="goal",
        goal={"objective": "Real goal"}, key="attach-goal-1",
    )
    real_goal = production_store.get_goal_for_conversation(conversation_id=conversation["id"])
    wrong = _send(
        client, headers, conversation["id"], mode="goal",
        goal={"id": "goal_doesnotexist"}, key="attach-goal-2",
    )
    assert wrong.status_code == 404
    attached = _send(
        client, headers, conversation["id"], mode="goal",
        goal={"id": real_goal["id"]}, key="attach-goal-3",
    )
    assert attached.status_code == 200


def test_plan_timers_and_goal_routes(production_store, monkeypatch):
    client, headers, conversation = _authed_client(production_store, monkeypatch)
    conversation_id = conversation["id"]

    # Seed a goal + one todo via the durable store.
    actor = production_store.authenticate(
        production_store.login(
            username="autonomy-admin", password="very secure test password",
            ip_address="127.0.0.1", user_agent="pytest",
        )["token"]
    )
    goal = production_store.create_goal(
        actor_id=actor["id"], conversation_id=conversation_id, objective="Planned objective"
    )
    production_store.append_todo_event(
        run_id="run-plan-route", conversation_id=conversation_id, goal_id=goal["id"],
        item_id="p-1", op="create",
        item_json=json.dumps({"id": "p-1", "title": "Step one", "status": "pending"}),
    )

    plan = client.get(f"/api/chat/{conversation_id}/plan", headers=headers)
    assert plan.status_code == 200
    plan_body = plan.json()["data"]
    assert plan_body["goal"]["id"] == goal["id"]
    assert plan_body["items"][0]["title"] == "Step one"

    timer = client.post(
        f"/api/chat/{conversation_id}/timers", headers=headers,
        json={"kind": "goal_eval", "cadence_seconds": 30, "payload": {"wakeup": True}},
    )
    assert timer.status_code == 201
    timer_id = timer.json()["data"]["id"]
    assert production_store.get_timer(timer_id)["state"] == "active"

    bad_timer = client.post(
        f"/api/chat/{conversation_id}/timers", headers=headers,
        json={"kind": "goal_eval", "cadence_seconds": 1},
    )
    assert bad_timer.status_code == 400

    paused = client.post(f"/api/chat/{conversation_id}/timers/{timer_id}/pause", headers=headers)
    assert paused.status_code == 200
    assert paused.json()["data"]["state"] == "paused"

    cancelled = client.post(f"/api/chat/{conversation_id}/timers/{timer_id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["state"] == "cancelled"

    patched = client.patch(
        f"/api/goals/{goal['id']}", headers=headers,
        json={"state": "completed", "objective": "Renamed"},
    )
    assert patched.status_code == 200
    updated = patched.json()["data"]
    assert updated["state"] == "completed"
    assert updated["objective"] == "Renamed"

    invalid_state = client.patch(
        f"/api/goals/{goal['id']}", headers=headers, json={"state": "sideways"}
    )
    assert invalid_state.status_code == 400


# ---------------------------------------------------------------------------
# Timer scheduler (munin.production.timers)
# ---------------------------------------------------------------------------


def test_emit_tick_is_pure_and_complete():
    from munin.production.timers import _emit_tick

    timer = {
        "id": "timer-1", "goal_id": "goal-1", "conversation_id": "conv-1",
        "kind": "goal_eval", "tick_count": 3, "due_at_ms": 5000, "last_tick_at_ms": 1000,
    }
    envelope = _emit_tick(timer)
    assert envelope["kind"] == "timer_tick"
    assert envelope["timer_id"] == "timer-1"
    assert envelope["tick_count"] == 3
    assert envelope["state"] == "active"


def test_dispatch_tick_wakes_goal_eval_run_only_when_eligible(monkeypatch):
    from munin.production import timers

    monkeypatch.setattr(timers, "TIMER_WAKEUP_ENABLED", True)
    created: list[dict] = []

    class FakeStore:
        def get_goal_for_conversation(self, conversation_id):  # noqa: ARG002
            return {
                "id": "goal-1", "actor_id": "actor-1", "conversation_id": "conv-1",
                "state": "active", "mode": "goal",
            }

        def get_conversation(self, actor_id, conversation_id):  # noqa: ARG002
            return {"runs": []}

        def create_turn(self, **kwargs):
            created.append(kwargs)
            return {"id": "turn-1", "run": {"id": "run-1"}, "assistant_message_id": "msg-1"}

    timer = {
        "id": "timer-1", "goal_id": "goal-1", "conversation_id": "conv-1",
        "kind": "goal_eval", "tick_count": 1, "due_at_ms": 0, "last_tick_at_ms": 0,
        "payload": {"wakeup": True},
    }
    asyncio.run(timers._dispatch_tick(store=FakeStore(), shared_state=None, timer=timer))

    assert len(created) == 1
    assert created[0]["idempotency_key"] == "timer:timer-1:1"
    assert created[0]["mode"] == "goal"
    assert created[0]["goal_id"] == "goal-1"
    assert "[munin:timer]" in created[0]["content"]


def test_dispatch_tick_skips_non_goal_timers_and_inactive_goals(monkeypatch):
    from munin.production import timers

    monkeypatch.setattr(timers, "TIMER_WAKEUP_ENABLED", True)
    created: list[dict] = []

    class FakeStore:
        def get_goal_for_conversation(self, conversation_id):  # noqa: ARG002
            return {"id": "goal-1", "actor_id": "actor-1", "conversation_id": "conv-1", "state": "paused", "mode": "goal"}

        def get_conversation(self, actor_id, conversation_id):  # noqa: ARG002
            return {"runs": []}

        def create_turn(self, **kwargs):
            created.append(kwargs)
            return {"run": {"id": "run-1"}}

    # Paused goal → no wake-up.
    asyncio.run(timers._dispatch_tick(
        store=FakeStore(), shared_state=None,
        timer={"id": "t", "goal_id": "g", "conversation_id": "c", "kind": "goal_eval", "tick_count": 1, "due_at_ms": 0, "last_tick_at_ms": 0, "payload": {"wakeup": True}},
    ))
    # Non-goal kind → no wake-up.
    asyncio.run(timers._dispatch_tick(
        store=FakeStore(), shared_state=None,
        timer={"id": "t2", "goal_id": "g", "conversation_id": "c", "kind": "reminder", "tick_count": 1, "due_at_ms": 0, "last_tick_at_ms": 0, "payload": {}},
    ))
    assert created == []


def test_dispatch_tick_skips_wakeup_when_a_run_is_active(monkeypatch):
    from munin.production import timers

    monkeypatch.setattr(timers, "TIMER_WAKEUP_ENABLED", True)
    created: list[dict] = []

    class FakeStore:
        def get_goal_for_conversation(self, conversation_id):  # noqa: ARG002
            return {"id": "goal-1", "actor_id": "actor-1", "conversation_id": "conv-1", "state": "active", "mode": "goal"}

        def get_conversation(self, actor_id, conversation_id):  # noqa: ARG002
            return {"runs": [{"state": "running"}]}

        def create_turn(self, **kwargs):
            created.append(kwargs)
            return {"run": {"id": "run-1"}}

    asyncio.run(timers._dispatch_tick(
        store=FakeStore(), shared_state=None,
        timer={"id": "t", "goal_id": "g", "conversation_id": "c", "kind": "goal_eval", "tick_count": 1, "due_at_ms": 0, "last_tick_at_ms": 0, "payload": {"wakeup": True}},
    ))
    assert created == []


@pytest.mark.asyncio
async def test_timer_tick_loop_ticks_durably_and_cancels_cleanly(production_store):
    import time

    from munin.production import timers

    operator, conversation = _seed_conversation(production_store)
    timer = production_store.create_timer(
        conversation_id=conversation["id"], actor_id=operator["id"],
        kind="goal_eval", due_at_ms=int(time.time() * 1000), cadence_ms=60_000,
    )

    task = asyncio.create_task(timers.timer_tick_loop(store=production_store, shared_state=None))
    await asyncio.sleep(0)  # let the loop body run synchronously up to its first await
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    ticked = production_store.get_timer(timer["id"])
    assert ticked["tick_count"] >= 1
    assert ticked["last_tick_at_ms"] is not None
    assert ticked["due_at_ms"] > 0
