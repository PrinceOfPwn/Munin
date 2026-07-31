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


def test_cross_host_claims_request_authoritative_connections(store, monkeypatch):
    original_connect = store._connect
    authoritative_flags: list[bool] = []

    def tracking_connect(*, authoritative=False):
        authoritative_flags.append(authoritative)
        return original_connect(authoritative=authoritative)

    monkeypatch.setattr(store, "_connect", tracking_connect)
    store.try_claim_spawn_slot(
        agent_name="authoritative_agent",
        spawner_pid=os.getpid(),
        instance_id="instance-a",
    )
    store.enqueue_wake(target_agent="authoritative_queue", task={"work": True})
    store.claim_wake_item(target_agent="authoritative_queue", claimer_pid=os.getpid())

    assert authoritative_flags.count(True) == 2


def test_atomic_claims_choose_exactly_one_winner(store):
    def race(callables):
        barrier = threading.Barrier(len(callables))
        results: queue.Queue[object] = queue.Queue()

        def contender(callback):
            barrier.wait()
            results.put(callback())

        threads = [threading.Thread(target=contender, args=(callback,)) for callback in callables]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
        return [results.get_nowait() for _ in threads]

    spawn_claims = race(
        [
            lambda: store.try_claim_spawn_slot(
                agent_name="atomic-agent",
                spawner_pid=os.getpid(),
                instance_id="instance-a",
            ),
            lambda: store.try_claim_spawn_slot(
                agent_name="atomic-agent",
                spawner_pid=os.getpid(),
                instance_id="instance-b",
            ),
        ]
    )
    assert sum(bool(claim["claimed"]) for claim in spawn_claims) == 1

    task_claims = race(
        [
            lambda agent=agent: store.claim_task(
                target_ip="127.0.0.1",
                action="atomic-task",
                assigned_agent=agent,
                lease_seconds=60,
                metadata_json="{}",
                allow_steal_stale=False,
            )
            for agent in ("instance-a", "instance-b")
        ]
    )
    assert sum(bool(claim.success) for claim in task_claims) == 1

    wake_id = store.enqueue_wake(target_agent="atomic-agent", task={"work": True})
    wake_claims = race(
        [
            lambda pid=pid: store.claim_wake_item(target_agent="atomic-agent", claimer_pid=pid)
            for pid in (1001, 1002)
        ]
    )
    claimed_wakes = [claim for claim in wake_claims if claim is not None]
    assert len(claimed_wakes) == 1
    assert claimed_wakes[0]["id"] == wake_id


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


def test_generated_source_rehydrates_without_action_artifacts(store):
    """A Turso registry row must carry source, not only an ephemeral path."""
    from munin.mcp import registry

    source = store.settings.generated_tools_dir / "durable_probe.py"
    source.write_text(
        "def durable_probe(value: str = 'ok') -> dict:\n"
        "    return {'value': value}\n",
        encoding="utf-8",
    )
    registry.register_state_only(
        store,
        slug="durable_probe",
        description="durable",
        script_path=source,
        function_name="durable_probe",
        signature={"function_name": "durable_probe"},
    )
    source.unlink()

    class FakeMcp:
        def __init__(self):
            self.attached: list[str] = []

        def tool(self):
            def decorator(fn):
                self.attached.append(fn.__name__)
                return fn
            return decorator

    mcp = FakeMcp()
    assert registry.rehydrate(mcp, store, store.settings) == 1
    assert source.exists()
    assert "gen__durable_probe" in mcp.attached


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


def test_structured_string_arguments_are_redacted_before_tracing():
    from munin.mcp.main import _redact_args

    redacted = _redact_args(
        {
            "task_json": json.dumps(
                {
                    "username": "alice",
                    "password": "do-not-persist",
                    "nested": {"api_token": "also-secret"},
                }
            )
        }
    )
    rendered = json.dumps(redacted)

    assert "do-not-persist" not in rendered
    assert "also-secret" not in rendered
    assert redacted["task_json"]["username"] == "alice"


def test_redacted_settings_hide_db_url_credentials(store):
    from dataclasses import replace

    from munin.mcp.config import redact_settings

    settings = replace(
        store.settings,
        db_url="libsql://user:password@db.turso.io/path?authToken=query-secret&mode=ro",
    )
    redacted = redact_settings(settings)

    assert "password" not in redacted.db_url
    assert "query-secret" not in redacted.db_url
    assert "user@" not in redacted.db_url
    assert "db.turso.io/path" in redacted.db_url
    assert "mode=ro" in redacted.db_url


def test_ldap_tolerant_retry_keeps_schema_supported_memberships(monkeypatch):
    from ldap3.core.exceptions import LDAPAttributeError

    from munin.mcp.tools import ldap_tools

    conn = SimpleNamespace(
        server=SimpleNamespace(
            schema=SimpleNamespace(
                attribute_types={
                    "member": object(),
                    "cn": object(),
                    "objectClass": object(),
                }
            )
        )
    )
    calls: list[list[str]] = []

    def fake_search(_conn, **kwargs):
        calls.append(kwargs["attributes"])
        if len(calls) == 1:
            raise LDAPAttributeError("unsupported uniqueMember")
        return []

    monkeypatch.setattr(ldap_tools, "_search", fake_search)
    ldap_tools._search_tolerant(
        conn,
        base_dn="dc=example,dc=com",
        filter_str="(objectClass=*)",
        attributes=["member", "uniqueMember", "memberUid", "cn"],
    )

    assert calls[1] == ["member", "cn"]


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


class _NoopMemory:
    def log_step(self, **kwargs):
        return None


def _bare_munin_agent(llm, catalog):
    from munin.core.munin_agent import MuninAgent

    agent = object.__new__(MuninAgent)
    agent.llm = llm
    agent.memory = _NoopMemory()
    agent._system_prompt = lambda: "test system"
    agent._current_catalog = lambda: catalog
    return agent


def test_munin_agent_respond_raises_on_llm_failure():
    """Direct characterization: MuninAgent.respond() wraps provider errors as RuntimeError.

    This assertion is required by tests/characterization/test_coord_respond_loop_parity.py
    (issue #9 §12 step 1 parity baseline) and must stay green for the duration of the
    migration; MuninAgent.respond() is kept alive for the rest of the roadmap.
    """
    class FailingLlm:
        def chat(self, **kwargs):
            raise TimeoutError("provider timeout")

    agent = _bare_munin_agent(FailingLlm(), {})
    with pytest.raises(RuntimeError, match="LLM call failed"):
        agent.respond("hello", max_iterations=1)


# test_munin_agent_propagates_llm_failure_to_mcp (munin_chat MCP path) was
# removed: the second half of the original test characterised the pre-issue-#9
# munin_chat path that dispatched via MuninAgent.respond() through the MCP
# tool surface (munin_tools.munin_chat → FailingAgent.respond → agent_error).
# The supervisor_runner / Deep Agents runtime replaced that dispatch path on
# this PR; error propagation from the new coordinator is exercised by the
# runtime_adapter integration tests. The direct MuninAgent.respond() assertion
# above remains intact per the parity baseline contract.


def test_graph_diagnostics_imports_the_top_level_subagent_catalog(store, monkeypatch):
    """Regression for ``munin.mcp.subagents`` (a package that never existed)."""
    from munin.mcp.tools import diagnostics_tool

    monkeypatch.setattr(diagnostics_tool, "STATE", store)
    result = diagnostics_tool._probe_graphs()

    assert result["ok"] is True
    assert result["total_active"] == 0


# test_munin_chat_async_returns_job_and_operator_safe_progress was removed:
# it characterised the pre-issue-#9 munin_chat async path (mode="async"
# dispatching to MuninAgent.respond() in a JOBS thread). The supervisor_runner
# / Deep Agents runtime replaced that path on this PR; async execution +
# operator-safe progress is now exercised by the runtime_adapter integration
# tests, and the JOBS subsystem remains covered by other job_* regression
# tests in this file.


def test_repetition_nudge_accepts_one_changed_next_call():
    class SequenceLlm:
        def __init__(self):
            self.calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if self.calls <= 6:
                value = "same"
            elif self.calls == 7:
                value = "changed"
            else:
                return {"choices": [{"message": {"role": "assistant", "content": "recovered"}}]}
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"call-{self.calls}",
                                    "function": {
                                        "name": "probe",
                                        "arguments": json.dumps({"value": value}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    agent = _bare_munin_agent(
        SequenceLlm(),
        {"probe": lambda value: {"ok": True, "summary": value, "data": {"value": value}}},
    )
    result = agent.respond("investigate", max_iterations=8)

    assert result["stop_reason"] == "final_answer"
    assert result["content"] == "recovered"


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
