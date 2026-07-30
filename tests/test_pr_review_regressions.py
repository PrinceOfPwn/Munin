"""Regression coverage for the actionable review threads on PR #1."""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from types import SimpleNamespace

import pytest


def test_idle_live_runner_keeps_spawn_slot(store):
    store.upsert_presence(
        agent_name="ldap_agent",
        role="test",
        status="IDLE",
        current_task_id=None,
        metadata_json=json.dumps({"pid": os.getpid()}),
    )

    claim = store.try_claim_spawn_slot(agent_name="ldap_agent", spawner_pid=999_999)

    assert claim["claimed"] is False
    assert claim["existing_pid"] == os.getpid()
    assert claim["reason"] == "runner_alive"


def test_foreign_runner_lease_blocks_without_local_pid_probe(store):
    store.upsert_presence(
        agent_name="remote_agent",
        role="test",
        status="RUNNING",
        current_task_id=None,
        metadata_json=json.dumps(
            {
                "pid": 4242,
                "instance_id": "github-run-other",
                "lease_expires_at_epoch": time.time() + 60,
            }
        ),
    )
    claim = store.try_claim_spawn_slot(
        agent_name="remote_agent",
        spawner_pid=os.getpid(),
        instance_id="github-run-current",
    )
    assert claim == {
        "claimed": False,
        "existing_pid": 4242,
        "reason": "foreign_lease_active",
    }


def test_expired_foreign_runner_lease_releases_slot(store):
    store.upsert_presence(
        agent_name="remote_agent",
        role="test",
        status="RUNNING",
        current_task_id=None,
        metadata_json=json.dumps(
            {
                "pid": os.getpid(),
                "instance_id": "github-run-other",
                "lease_expires_at_epoch": time.time() - 1,
            }
        ),
    )
    claim = store.try_claim_spawn_slot(
        agent_name="remote_agent",
        spawner_pid=os.getpid(),
        instance_id="github-run-current",
    )
    assert claim["claimed"] is True
    assert store.list_presence()[0]["metadata"]["instance_id"] == "github-run-current"


def test_generated_callable_normalizes_scalar_result(store):
    from munin.mcp.registry import wrap_generated_callable

    wrapped = wrap_generated_callable(
        lambda value: value.upper(),
        tool_name="gen__upper",
        state=store,
    )

    result = wrapped("munin")

    assert result["ok"] is True
    assert result["data"] == {"result": "MUNIN"}


def test_generated_tool_paths_are_persisted_portably(store):
    from munin.mcp import registry

    source = store.settings.generated_tools_dir / "portable_probe.py"
    source.write_text(
        "def portable_probe(value: str = 'ok') -> dict:\n"
        "    return {'value': value}\n",
        encoding="utf-8",
    )
    registry.register_state_only(
        store,
        slug="portable_probe",
        description="portable",
        script_path=source,
        function_name="portable_probe",
        signature={"function_name": "portable_probe"},
    )

    row = store.procedural_get("gen__portable_probe")
    assert row is not None
    assert row["script_path"] == "munin/generated/portable_probe.py"
    assert registry.resolve_script_path(store.settings, row["script_path"]) == source.resolve()


def test_deactivated_tool_is_detached_and_cannot_resolve(store):
    from munin.mcp import registry

    store.procedural_register(
        name="gen__unsafe",
        description="test",
        script_path="generated.py",
        signature={"function_name": "unsafe"},
        tags=[],
        created_by_agent="test",
    )

    class FakeMcp:
        removed: list[str] = []

        def remove_tool(self, name: str) -> None:
            self.removed.append(name)

    mcp = FakeMcp()
    assert registry.resolve_tool_by_name(store, "gen__unsafe") is not None
    assert registry.deactivate(mcp, store, "gen__unsafe") is True
    assert mcp.removed == ["gen__unsafe"]
    assert registry.resolve_tool_by_name(store, "gen__unsafe") is None


def test_progress_redaction_covers_nested_secret_keys():
    from munin.mcp.audit import redact_secrets

    redacted = redact_secrets({
        "tool": "ldap_who_am_i",
        "args": {
            "username": "alice",
            "password": "SuperSecret!",
            "nested": [{"api_token": "token-value"}],
        },
    })

    marker = "***REDACTED***"
    assert redacted["args"]["username"] == "alice"
    assert redacted["args"]["password"] == marker
    assert redacted["args"]["nested"][0]["api_token"] == marker


def test_libsql_context_propagates_sync_failure():
    from munin.mcp.persistence import _LibsqlConnectionProxy

    class Native:
        def commit(self) -> None:
            return None

        def sync(self) -> None:
            raise RuntimeError("turso unavailable")

    with pytest.raises(RuntimeError, match="turso unavailable"):
        with _LibsqlConnectionProxy(Native()):
            pass


def test_git_flush_waits_for_dequeued_inflight_task(monkeypatch):
    from munin.mcp import git_persist

    pending: queue.Queue[dict] = queue.Queue()
    pending.put({"paths": [], "message": "test", "kind": "test"})
    # Simulate the worker having dequeued the item: empty(), but unfinished=1.
    pending.get_nowait()
    monkeypatch.setattr(git_persist, "_QUEUE", pending)
    monkeypatch.setattr(git_persist, "_enabled", lambda: True)
    monkeypatch.setattr(git_persist, "_ensure_worker", lambda: None)

    finished = threading.Event()
    waiter = threading.Thread(
        target=lambda: (git_persist.flush(timeout=1.0), finished.set()),
        daemon=True,
    )
    waiter.start()
    time.sleep(0.05)
    assert not finished.is_set()

    pending.task_done()
    waiter.join(timeout=0.5)
    assert finished.is_set()


def test_git_push_retry_reuses_existing_commit(monkeypatch, tmp_path):
    from munin.mcp import git_persist

    git_calls: list[list[str]] = []
    pushes = iter([(False, "offline"), (False, "offline"), (True, "ok")])

    def fake_git(args, **kwargs):
        git_calls.append(args)
        return SimpleNamespace(returncode=1 if args[:3] == ["diff", "--staged", "--quiet"] else 0)

    monkeypatch.setattr(git_persist, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(git_persist, "_ensure_git_identity", lambda repo: None)
    monkeypatch.setattr(git_persist, "_current_branch_or_create", lambda repo: "munin/session-test")
    monkeypatch.setattr(git_persist, "_run_git", fake_git)
    monkeypatch.setattr(git_persist, "_push", lambda repo, branch: next(pushes))
    monkeypatch.setattr(git_persist.time, "sleep", lambda seconds: None)

    git_persist._process_batch(
        [{"paths": ["munin/generated/probe.py"], "message": "forge probe", "kind": "tool"}]
    )

    assert sum(call[0] == "commit" for call in git_calls) == 1
    assert sum(call[0] == "add" for call in git_calls) == 1


def test_wake_artifact_reader_is_bounded_and_pathless(store, monkeypatch):
    from munin.mcp.tools import munin_tools

    monkeypatch.setattr(munin_tools, "STATE", store)
    monkeypatch.setattr(munin_tools, "_get_settings", lambda: store.settings)
    artifact_dir = store.settings.munin_data_path / "wake_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "wake_7.json").write_text("abcdefghij", encoding="utf-8")

    first = munin_tools.read_wake_artifact(7, max_chars=4)
    second = munin_tools.read_wake_artifact(7, offset=first["data"]["next_offset"], max_chars=10)

    assert first["data"]["content"] == "abcd"
    assert first["data"]["eof"] is False
    assert second["data"]["content"] == "efghij"
    assert second["data"]["eof"] is True
    assert munin_tools.read_wake_artifact(-1)["error"]["code"] == "bad_input"


def test_hugin_neighbors_is_available_to_main_and_subagents():
    from munin.core.munin_agent import _NATIVE_TOOLS
    from munin.subagents.base import _STATIC_TOOLS

    assert "hugin_neighbors" in _NATIVE_TOOLS
    assert "hugin_neighbors" in _STATIC_TOOLS


def test_cors_preflight_bypasses_bearer_and_exposes_session_header():
    from munin.mcp.main import _make_auth_middleware

    inner_called = False
    sent: list[dict] = []

    async def inner(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "OPTIONS",
        "path": "/mcp/",
        "headers": [
            (b"origin", b"http://localhost:3000"),
            (b"access-control-request-method", b"POST"),
            (b"access-control-request-headers", b"authorization,content-type"),
        ],
    }
    middleware = _make_auth_middleware("secret")(inner)
    asyncio.run(middleware(scope, receive, send))

    assert inner_called is False
    assert sent[0]["status"] == 204
    headers = dict(sent[0]["headers"])
    assert headers[b"access-control-allow-origin"] == b"http://localhost:3000"
    assert b"mcp-session-id" in headers[b"access-control-expose-headers"]


def test_hugin_diagnostics_uses_current_cache_tuple(monkeypatch):
    from munin.mcp.tools import diagnostics_tool, hugin_tool

    monkeypatch.setattr(
        hugin_tool,
        "_load_cached",
        lambda **kwargs: ({"entities": [{"id": "a"}, {"id": "b"}]}, 12, False),
    )

    result = diagnostics_tool._probe_hugin(deep=False)

    assert result["ok"] is True
    assert result["cached_entities"] == 2
    assert result["cache_age_seconds"] == 12
    assert result["cache_stale"] is False


def test_subagent_trace_uses_independent_cursors(store, monkeypatch):
    from munin.mcp.tools import munin_tools

    monkeypatch.setattr(munin_tools, "STATE", store)
    for index in range(5):
        store.episodic_record(
            agent="worker",
            action=f"step-{index}",
            input_data={},
            tags=[],
        )
    store.post_message(
        sender_agent="worker",
        recipient_agent="munin",
        subject="progress",
        message_type="PROGRESS",
        body="one",
        related_task_id=None,
        related_target_ip="",
        metadata_json="{}",
    )

    result = munin_tools.subagent_trace(
        "worker",
        since_event_id=4,
        since_message_id=0,
    )
    data = result["data"]

    assert [event["id"] for event in data["events"]] == [5]
    assert len(data["messages"]) == 1
    assert data["next_event_id"] == 5
    assert data["next_message_id"] == data["messages"][0]["id"]
