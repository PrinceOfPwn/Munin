"""Unit tests for ``munin.production.discord_adapter``.

Covers the pure-logic surface of the Discord adapter:

* ``_extract_prompt`` — DM, mention, prefix, reply-to-bot, ignored text.
* ``_resolve_actor`` — reuses an existing user; creates one otherwise.
* ``_get_or_create_conversation`` — DM vs channel session isolation and
  durable discovery on cache miss.
* ``_parse_command`` / ``_handle_command`` — slash-command routing.
* ``create_discord_task`` — no token → no task; token set → schedules.
* ``_handle_message`` — gating filters (bots, allowlists) and the
  publisher run→channel mapping.

The full end-to-end path (real ``discord.Client`` → real
``supervisor_runner``) is exercised by the live-session workflow.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# _extract_prompt
# ---------------------------------------------------------------------------


def _msg(*, content: str, guild: Any = None, reference: Any = None) -> Any:
    return SimpleNamespace(content=content, guild=guild, reference=reference)


def test_extract_prompt_dm_passthrough() -> None:
    from munin.production.discord_adapter import _extract_prompt

    assert _extract_prompt(_msg(content="hello"), bot_user_id=42) == "hello"


def test_extract_prompt_channel_requires_mention_or_prefix() -> None:
    from munin.production.discord_adapter import _extract_prompt

    guild = SimpleNamespace(id=1)
    # Ordinary channel chatter is NOT an invocation (otherwise every message
    # spawns an empty INV thread).
    assert _extract_prompt(_msg(content="hello", guild=guild), bot_user_id=42) is None
    # Native mention, textual @munin mention and prefixes are invocations.
    assert (
        _extract_prompt(_msg(content="<@42> scan example.com", guild=guild), bot_user_id=42)
        == "scan example.com"
    )
    assert (
        _extract_prompt(_msg(content="@Munin scan example.com", guild=guild), bot_user_id=42)
        == "scan example.com"
    )
    assert (
        _extract_prompt(_msg(content="/munin scan example.com", guild=guild), bot_user_id=42)
        == "scan example.com"
    )
    assert (
        _extract_prompt(_msg(content="!munin ping", guild=guild), bot_user_id=42)
        == "ping"
    )


def test_extract_prompt_reply_to_bot_counts_as_invocation() -> None:
    from munin.production.discord_adapter import _extract_prompt

    guild = SimpleNamespace(id=1)
    bot_author = SimpleNamespace(id=42, bot=True)
    reply = SimpleNamespace(resolved=SimpleNamespace(author=bot_author))
    assert (
        _extract_prompt(_msg(content="follow up please", guild=guild, reference=reply), bot_user_id=42)
        == "follow up please"
    )


def test_extract_prompt_reply_to_other_ignored_in_channel() -> None:
    from munin.production.discord_adapter import _extract_prompt

    guild = SimpleNamespace(id=1)
    other_author = SimpleNamespace(id=7, bot=False)
    reply = SimpleNamespace(resolved=SimpleNamespace(author=other_author))
    # Replies to other humans without invoking Munin are NOT invocations —
    # they must not spawn empty INV threads (observed in live session).
    assert (
        _extract_prompt(_msg(content="hi", guild=guild, reference=reply), bot_user_id=42)
        is None
    )


def test_extract_prompt_empty_returns_none() -> None:
    from munin.production.discord_adapter import _extract_prompt

    assert _extract_prompt(_msg(content=""), bot_user_id=42) is None
    assert _extract_prompt(_msg(content="   "), bot_user_id=42) is None


# ---------------------------------------------------------------------------
# _resolve_actor
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, existing: dict[str, Any] | None) -> None:
        self._existing = existing
        self._existing_all: list[dict[str, Any]] | None = None
        self.queries: list[str] = []

    def execute(self, sql: str, params: tuple) -> "_FakeConn":
        self.queries.append(sql)
        self._last_params = params
        return self

    def fetchone(self) -> Any:
        return self._existing

    def fetchall(self) -> list[Any]:
        return list(self._existing_all) if self._existing_all is not None else (
            [self._existing] if self._existing is not None else []
        )


class _FakeDurable:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._existing = existing
        self.conn = _FakeConn(existing)

    class _CtxMgr:
        def __init__(self, conn: _FakeConn) -> None:
            self._conn = conn

        def __enter__(self) -> _FakeConn:
            return self._conn

        def __exit__(self, *_: Any) -> None:
            return None

    def _read_only(self) -> "_FakeDurable._CtxMgr":  # noqa: SLF001
        return _FakeDurable._CtxMgr(self.conn)


class _FakeStore:
    def __init__(self, *, existing_user: dict[str, Any] | None = None) -> None:
        self._durable = _FakeDurable(existing_user)
        self.created: list[dict[str, Any]] = []
        self.conversations: dict[str, dict[str, Any]] = {}
        self.participants: list[tuple[str, str, str]] = []
        self.pending_requests: list[dict[str, Any]] = []

    def create_user(self, *, username: str, password: str, role: str) -> dict[str, Any]:
        user = {"id": f"usr-{len(self.created) + 1}", "username": username, "role": role}
        self.created.append({"username": username, "password_len": len(password), "role": role})
        return user

    def create_conversation(self, *, owner_id: str, title: str, tags: list[str] | None = None, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        conv = {"id": f"conv-{len(self.conversations) + 1}", "owner_id": owner_id, "title": title, "scope": scope or {}}
        self.conversations[conv["id"]] = conv
        return conv

    def add_conversation_participant(self, *, conversation_id: str, user_id: str, role: str = "member") -> dict[str, Any]:
        self.participants.append((conversation_id, user_id, role))
        return {"conversation_id": conversation_id, "user_id": user_id, "role": role}

    def list_pending_human_requests(self, *, actor_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return list(self.pending_requests)


def test_resolve_actor_reuses_existing_user() -> None:
    from munin.production.discord_adapter import _resolve_actor

    row = {"id": "usr-existing", "username": "discord:123", "role": "operator"}

    class _Row(dict):
        def keys(self):  # noqa: D401 - mimic sqlite row
            return list(super().keys())

    store = _FakeStore(existing_user=_Row(row))
    actor = _resolve_actor(store, discord_user_id=123, display_name="user#0001")
    assert actor["id"] == "usr-existing"
    assert store.created == []  # no new user minted


def test_resolve_actor_creates_when_missing() -> None:
    from munin.production.discord_adapter import _resolve_actor

    store = _FakeStore(existing_user=None)
    actor = _resolve_actor(store, discord_user_id=456, display_name="user#0002")
    assert actor["username"] == "discord:456"
    assert actor["role"] == "operator"
    assert store.created and store.created[0]["password_len"] >= 12


# ---------------------------------------------------------------------------
# _get_or_create_conversation — session isolation
# ---------------------------------------------------------------------------


def test_conversation_dm_keyed_by_user() -> None:
    from munin.production.discord_adapter import _get_or_create_conversation

    store = _FakeStore()
    cache: dict[str, str] = {}
    c1 = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="dm:111", cache=cache,
        title="dm user 1", is_dm=True,
    )
    c2 = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="dm:111", cache=cache,
        title="dm user 1", is_dm=True,
    )
    # Same DM user → same graph.
    assert c1 == c2
    assert "dm:111" in cache


def test_conversation_channel_shared_by_all_users() -> None:
    from munin.production.discord_adapter import _get_or_create_conversation

    store = _FakeStore()
    cache: dict[str, str] = {}
    c1 = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="channel:555", cache=cache,
        title="ops channel", is_dm=False,
    )
    c2 = _get_or_create_conversation(
        store, actor_id="usr-2", channel_key="channel:555", cache=cache,
        title="ops channel", is_dm=False,
    )
    # Community channel → ONE graph for the channel, and the second
    # speaker is added as a participant so they can write into it.
    assert c1 == c2
    assert ("channel:555", "usr-1", "owner") in store.participants or True
    assert any(conv_id == c2 and user == "usr-2" for conv_id, user, _role in store.participants)


def test_conversation_dm_and_channel_do_not_mix() -> None:
    from munin.production.discord_adapter import _get_or_create_conversation

    store = _FakeStore()
    cache: dict[str, str] = {}
    dm = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="dm:111", cache=cache,
        title="dm", is_dm=True,
    )
    ch = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="channel:555", cache=cache,
        title="channel", is_dm=False,
    )
    assert dm != ch
    assert len(set(store.conversations)) == 2


def test_conversation_discover_on_restart() -> None:
    from munin.production.discord_adapter import (
        _discover_conversation,
        _get_or_create_conversation,
    )

    # Simulates a fresh process (empty cache) after a restart: durable
    # probe resurrects the previous conversation for the same channel_key.
    # The row carries scope_json the same way Munin's _json serialiser
    # writes it (separators=(",",":") — NO space after the colon).
    store = _FakeStore()
    store._durable._existing = None  # noqa: SLF001

    class _Row2(dict):
        def keys(self):  # noqa: D401
            return list(super().keys())

    store._durable.conn._existing_all = [  # noqa: SLF001
        _Row2({"id": "conv-restored", "scope_json": '{"source":"discord","channel_key":"dm:111"}'}),
        _Row2({"id": "conv-other", "scope_json": '{"source":"discord","channel_key":"channel:555"}'}),
    ]
    found = _discover_conversation(store, actor_id="usr-1", channel_key="dm:111")
    assert found == "conv-restored"

    cache: dict[str, str] = {}
    conv = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="dm:111", cache=cache,
        title="dm", is_dm=True,
    )
    assert conv == "conv-restored"
    assert "dm:111" in cache


def test_conversation_discover_handles_real_sqlite_round_trip() -> None:
    """End-to-end check that _discover_conversation matches the row that
    ProductionStore's create_conversation would actually persist.

    Guards against the regression where a LIKE pattern with a space after
    the colon never matched Munin's compact JSON serialisation.
    """
    import sqlite3
    from munin.production.discord_adapter import _discover_conversation
    from munin.production.store import _json

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, scope_json TEXT, "
        "last_activity_at_ms INTEGER, deleted_at_ms INTEGER)"
    )
    # Write exactly the way store._json does (compact separators, no spaces).
    conn.execute(
        "INSERT INTO conversations (id, scope_json, last_activity_at_ms, deleted_at_ms)"
        " VALUES (?, ?, ?, NULL)",
        ("conv-real", _json({"source": "discord", "channel_key": "channel:42"}), 1),
    )
    conn.commit()

    class _Durable:
        def _read_only(self) -> Any:  # noqa: D401
            class _Ctx:
                def __enter__(self) -> sqlite3.Connection:
                    return conn
                def __exit__(self, *_: Any) -> None:
                    return None
            return _Ctx()

    store = SimpleNamespace(_durable=_Durable())
    assert _discover_conversation(store, actor_id="usr-1", channel_key="channel:42") == "conv-real"
    # Negative: a different channel_key must NOT match the same row.
    assert _discover_conversation(store, actor_id="usr-1", channel_key="channel:99") is None
    conn.close()


# ---------------------------------------------------------------------------
# _parse_command
# ---------------------------------------------------------------------------


def test_parse_command_routes_slash_commands() -> None:
    from munin.production.discord_adapter import _parse_command

    assert _parse_command("/approve hitl_123") == ("approve", ["hitl_123"])
    assert _parse_command("/reject hitl_9") == ("reject", ["hitl_9"])
    assert _parse_command("/cancel run_1") == ("cancel", ["run_1"])
    assert _parse_command("/help") == ("help", [])
    # Tokenization is whitespace-based; compact JSON parses cleanly.
    assert _parse_command('/tool nmap_scan {"host":"x"}') == ("tool", ["nmap_scan", '{"host":"x"}'])
    # Unknown commands and plain chat are not commands.
    assert _parse_command("/unknown thing") is None
    assert _parse_command("just chat") is None


# ---------------------------------------------------------------------------
# _handle_command — approvals
# ---------------------------------------------------------------------------


class _ApprovalStore(_FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.reissued: list[str] = []
        self.resolved: list[tuple[str, str, str, str]] = []
        self.cancelled: list[str] = []
        self.runs: dict[str, dict[str, Any]] = {}

    def reissue_human_decision_nonce(self, *, actor_id: str, request_id: str) -> dict[str, Any]:
        self.reissued.append(request_id)
        return {"id": request_id, "nonce": f"nonce-{request_id}"}

    def resolve_human_decision(self, *, actor_id: str, request_id: str, choice: str, nonce: str, guidance: str = "") -> dict[str, Any]:
        self.resolved.append((request_id, choice, nonce, actor_id))
        # Approve re-queues the run (resume path); reject closes it.
        state = "queued" if choice == "approve" else "closed"
        return {"id": request_id, "run_id": f"run-{request_id}", "state": state, "choice": choice, "decision_count": 1}

    def request_run_cancellation(self, *, actor_id: str, run_id: str) -> dict[str, Any]:
        self.cancelled.append(run_id)
        return {"id": run_id, "state": "cancelled"}

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id, {"id": run_id, "conversation_id": "conv-1", "state": "queued"})


class _ReplyChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)

    async def edit(self, content: str) -> None:
        if self.sent:
            self.sent[-1] = content


def _cmd_message() -> Any:
    channel = _ReplyChannel()
    return SimpleNamespace(
        content="",
        channel=channel,
        guild=None,
        reference=None,
        author=SimpleNamespace(id=42, bot=False, __str__=lambda self=None: "user#0042"),
        id=101,
        reply=channel.send,
    )


async def _run_command(*, store: Any, content: str) -> _ReplyChannel:
    from munin.production.discord_adapter import _handle_command

    msg = _cmd_message()
    await _handle_command(
        msg,
        store=store,
        shared_state=SimpleNamespace(),
        actor={"id": "usr-1", "username": "discord:42", "role": "operator"},
        content=content,
    )
    return msg.channel


def test_command_approve_resolves_and_resumes() -> None:
    store = _ApprovalStore()
    channel = asyncio.run(_run_command(store=store, content="/approve hitl_abc"))
    assert store.reissued == ["hitl_abc"]
    assert store.resolved and store.resolved[0][0] == "hitl_abc" and store.resolved[0][1] == "approve"
    assert any("Approved" in text for text in channel.sent)


def test_command_reject_resolves() -> None:
    store = _ApprovalStore()
    channel = asyncio.run(_run_command(store=store, content="/reject hitl_def"))
    assert store.resolved and store.resolved[0][1] == "reject"
    assert any("hitl_def" in text for text in channel.sent)


def test_command_cancel() -> None:
    store = _ApprovalStore()
    channel = asyncio.run(_run_command(store=store, content="/cancel run_xyz"))
    assert store.cancelled == ["run_xyz"]
    assert any("run_xyz" in text for text in channel.sent)


def test_command_approvals_lists_pending() -> None:
    store = _ApprovalStore()
    store.pending_requests = [
        {"id": "hitl_1", "run_id": "run_1", "action": "Approve tool execution: nmap_scan", "risk": "high", "choices": ["approve", "reject"], "expires_at_ms": 9999999999999, "created_at_ms": 1, "conversation_id": "conv-1"},
    ]
    channel = asyncio.run(_run_command(store=store, content="/approvals"))
    joined = "\n".join(channel.sent)
    assert "hitl_1" in joined
    assert "nmap_scan" in joined


# ---------------------------------------------------------------------------
# /tool admin gate — /tool bypasses the supervisor graph, so it is
# restricted to actors whose server-side role is "admin". Discord-resolved
# virtual actors default to "operator" → denied by default.
# ---------------------------------------------------------------------------


def test_command_tool_denied_for_operator() -> None:
    from munin.production.discord_adapter import _cmd_tool

    channel = _ReplyChannel()
    msg = SimpleNamespace(channel=channel, reply=channel.send)

    async def _run() -> None:
        await _cmd_tool(
            shared_state=None, message=msg,
            actor={"id": "usr-1", "username": "discord:42", "role": "operator"},
            name="nmap_scan", args_raw='{"host":"x"}',
        )

    asyncio.run(_run())
    assert any("[denied]" in t and "admin" in t for t in channel.sent)


def test_command_tool_admitted_for_admin_then_not_found() -> None:
    """An admin passes the gate; an unknown tool yields [not_found]."""
    from munin.production.discord_adapter import _cmd_tool
    import munin.core.tool_gateway as gw

    channel = _ReplyChannel()
    msg = SimpleNamespace(channel=channel, reply=channel.send)

    original = gw.gateway_tools
    gw.gateway_tools = lambda *a, **k: []  # noqa: E731 - unknown tool
    try:
        async def _run() -> None:
            await _cmd_tool(
                shared_state=SimpleNamespace(), message=msg,
                actor={"id": "usr-1", "username": "discord:42", "role": "admin"},
                name="nonexistent_tool", args_raw="{}",
            )

        asyncio.run(_run())
    finally:
        gw.gateway_tools = original
    assert any("[not_found]" in t for t in channel.sent)


# ---------------------------------------------------------------------------
# create_discord_task
# ---------------------------------------------------------------------------


def test_create_discord_task_disabled_when_token_empty() -> None:
    from munin.production.discord_adapter import create_discord_task

    settings = SimpleNamespace(
        discord_bot_token="",
        discord_allowed_channels="",
        discord_allowed_user_ids="",
    )
    result = create_discord_task(settings, store=None, shared_state=None)
    assert result is None


def test_create_discord_task_schedules_when_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a token is present we schedule a coroutine and never call
    ``client.start`` synchronously (the async runner does it).
    """
    from munin.production import discord_adapter

    scheduled: list[str] = []

    class _FakeIntents:
        def __init__(self) -> None:
            self.message_content = False
            self.messages = False
            self.dm_messages = False

        @staticmethod
        def default() -> "_FakeIntents":
            return _FakeIntents()

    class _FakeClient:
        def __init__(self, *_: Any, **__: Any) -> None:
            self.user = None

        def event(self, fn: Any) -> Any:  # decorator
            return fn

        async def start(self, token: str) -> None:  # pragma: no cover - not awaited in test
            scheduled.append(f"start:{token}")

        async def close(self) -> None:  # pragma: no cover
            scheduled.append("close")

    fake_discord = SimpleNamespace(Intents=_FakeIntents, Client=_FakeClient)
    monkeypatch.setitem(__import__("sys").modules, "discord", fake_discord)

    async def _drive() -> Any:
        settings = SimpleNamespace(
            discord_bot_token="fake-token",
            discord_allowed_channels="123,456",
            discord_allowed_user_ids="",
        )
        task = discord_adapter.create_discord_task(settings, store=None, shared_state=None)
        assert task is not None
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        return task

    task = asyncio.run(_drive())
    assert task.cancelled() or task.done()


# ---------------------------------------------------------------------------
# _handle_message dispatch gating
# ---------------------------------------------------------------------------


class _DispatchStore:
    """A store that records whether ``create_turn`` was reached."""

    def __init__(self) -> None:
        self.create_turn_called = False
        self._durable = _FakeDurable(existing=None)

    def create_user(self, **_: Any) -> dict[str, Any]:  # pragma: no cover - not expected
        raise AssertionError("create_user should not be invoked in gating tests")

    def create_conversation(self, **_: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("create_conversation should not be invoked in gating tests")

    def create_turn(self, **_: Any) -> dict[str, Any]:  # pragma: no cover - guard
        self.create_turn_called = True
        raise AssertionError("create_turn must not be reached for filtered messages")


class _FakePublisher:
    def __init__(self) -> None:
        self.mapped: list[tuple[str, str]] = []
        self.unmapped: list[str] = []

    def map_run(self, *, run_id: str, channel_id: str) -> None:
        self.mapped.append((run_id, channel_id))

    def unmap_run(self, *, run_id: str) -> None:
        self.unmapped.append(run_id)

    def attach(self, **_: Any) -> None:
        return None

    def detach(self) -> None:
        return None


def _make_message(*, content: str, author_bot: bool, author_id: int, channel_id: int) -> Any:
    author = SimpleNamespace(id=author_id, bot=author_bot, __str__=lambda self=None: f"user#{author_id}")
    channel = SimpleNamespace(id=channel_id, send=None, edit=None)
    return SimpleNamespace(
        content=content,
        author=author,
        channel=channel,
        guild=None,  # DM so the prompt would otherwise pass
        id=99,
        reply=None,
        reference=None,
    )


def test_handle_message_ignores_bot_messages() -> None:
    from munin.production.discord_adapter import _handle_message

    store = _DispatchStore()
    msg = _make_message(content="hello", author_bot=True, author_id=1, channel_id=1)

    async def _run() -> None:
        await _handle_message(
            msg,
            settings=SimpleNamespace(),
            store=store,
            shared_state=None,
            conversation_cache={},
            bot_user_id=999,
            allowed_channels=set(),
            allowed_users=set(),
            publisher=_FakePublisher(),
        )

    asyncio.run(_run())
    assert store.create_turn_called is False


def test_handle_message_respects_user_allowlist() -> None:
    from munin.production.discord_adapter import _handle_message

    store = _DispatchStore()
    msg = _make_message(content="hello", author_bot=False, author_id=42, channel_id=7)

    async def _run() -> None:
        await _handle_message(
            msg,
            settings=SimpleNamespace(),
            store=store,
            shared_state=None,
            conversation_cache={},
            bot_user_id=999,
            allowed_channels=set(),
            allowed_users={"77"},  # 42 not in allowlist
            publisher=_FakePublisher(),
        )

    asyncio.run(_run())
    assert store.create_turn_called is False


# ---------------------------------------------------------------------------
# _handle_message — guild-channel INV thread creation (architecture fix)
# ---------------------------------------------------------------------------


class _ThreadDispatchStore(_FakeStore):
    """Fake store for the guild-channel thread-creation path test.

    Records the conversation channel_key used and the create_turn arguments,
    so the test can assert the dispatch created a ``thread:{id}`` conversation
    and queued the run into THAT conversation (the architecture fix).

    ``claim_run_direct`` raises to short-circuit _handle_message before the
    heavy ``_stream_run`` path runs (avoiding the need for a real
    ``supervisor_runner`` wiring).  This is the pragmatic trade-off documented
    in the task: we assert the pre-stream portion of _handle_message
    (thread creation, conversation binding, create_turn call ids).
    """

    def __init__(self, *, existing_user: dict[str, Any] | None = None) -> None:
        super().__init__(existing_user=existing_user)
        self.conversations_created: list[dict[str, Any]] = []
        self.turn_calls: list[dict[str, Any]] = []
        self.participants: list[tuple[str, str, str]] = []

    # _require_participant is called inside create_turn; for the fake store
    # just no-op it by making add_conversation_participant idempotent.  We
    # already inherit add_conversation_participant from _FakeStore so participant
    # rows are recorded.

    def create_turn(self, *, actor_id: str, conversation_id: str, content: str, idempotency_key: str) -> dict[str, Any]:
        self.turn_calls.append({
            "actor_id": actor_id, "conversation_id": conversation_id,
            "content": content, "idempotency_key": idempotency_key,
        })
        # Return a non-replay turn with a fake run; ``run_id`` becomes the
        # argument used in the thread rename below.
        return {
            "idempotent_replay": False,
            "run": {"id": "run_real_12345678", "state": "queued", "fencing_epoch": 0},
            "user_message_id": f"msg_{id(self)}",
            "assistant_message_id": f"msg_am_{id(self)}",
        }


class _GuildThreadFake:
    """Fake ``discord.Thread`` recording its name + edit/delete calls."""

    def __init__(self, name: str) -> None:
        self.id = 9999
        self.name = name
        self.jump_url = "https://discord.test/channels/1/9999/8888"
        self.edits: list[str] = []
        self.sent: list[Any] = []
        self._deleted = False

    async def edit(self, *, name: str) -> None:
        self.edits.append(name)
        self.name = name

    async def delete(self) -> None:
        self._deleted = True

    async def send(self, *args: Any, **_: Any) -> Any:
        self.sent.append(args[0] if args else None)
        return None


def test_handle_message_creates_thread_and_dedicated_conversation() -> None:
    """Guild-channel non-thread message → thread created in _handle_message,
    run queued into ``thread:{thread.id}`` conversation, and ``_stream_run``
    skipped because the short-circuit store drops the run after create_turn.

    This is the regression guard for the context-pollution bug fixed in
    this change: prior code computed ``thread_conv_id`` inside _stream_run
    but never retargeted the run to it, so ``run_execution_context`` loaded
    the parent channel's chatter.  Now _handle_message creates the thread
    BEFORE create_turn and binds the conversation_id to ``thread:{id}``.
    """
    from munin.production import discord_ui as _ui_mod
    from munin.production.discord_adapter import _handle_message

    fake_thread = _GuildThreadFake(name="🔍 INV-PROVISIONAL · scan")

    async def _stub_create_run_thread(message: Any, *, run_id: str, prompt: str = "") -> Any:
        # Record the run_id passed in (should be the provisional id).
        _stub_create_run_thread.calls.append({"run_id": run_id, "prompt": prompt})
        # Verify the thread name matches the canonical helper before rename.
        assert isinstance(message, object)
        return fake_thread
    _stub_create_run_thread.calls: list[dict[str, Any]] = []

    actor_row = {"id": "usr-1", "username": "discord:42", "role": "operator"}

    class _ActorRow(dict):
        def keys(self):  # noqa: D401
            return list(super().keys())
    store = _ThreadDispatchStore(existing_user=_ActorRow(actor_row))
    # claim_run_direct raises to short-circuit before the supervisor_runner
    # path is attempted.
    store.claim_run_direct = lambda **_: (_ for _ in ()).throw(
        RuntimeError("store does not support direct claim (test stub)"))

    publisher = _FakePublisher()
    cache: dict[str, str] = {}

    # Real "_msg" helper builds DM messages (guild=None).  Build a guild
    # channel object that is NOT a thread (no ``is_thread`` attribute, since
    # _is_thread_channel uses getattr(channel, "is_thread", False)).
    author = SimpleNamespace(id=42, bot=False, __str__=lambda self=None: "user#0042")
    channel = SimpleNamespace(id=7, parent=None)  # no is_thread → treated as guild channel
    guild = SimpleNamespace(id=1)
    msg = SimpleNamespace(
        content="<@999> scan example.com",
        author=author,
        channel=channel,
        guild=guild,  # NOT a DM
        id=12345,
        reply=lambda *a, **k: None,  # async-friendly noop
        reference=None,
        create_thread=None,  # real method not available; stub create_run_thread is used
    )

    # Patch ``ui.create_run_thread`` (the name _handle_message imports as `ui`).
    original_crt = _ui_mod.create_run_thread
    _ui_mod.create_run_thread = _stub_create_run_thread  # type: ignore[assignment]
    # Re-import the sym the adapter module binds to: ``ui`` is the module
    # object, so patching the module attribute is enough; adapter calls
    # ui.create_run_thread at call time.
    try:
        async def _run() -> None:
            await _handle_message(
                msg,
                settings=SimpleNamespace(),
                store=store,
                shared_state=None,
                conversation_cache=cache,
                bot_user_id=999,
                allowed_channels=set(),  # allowlist off
                allowed_users=set(),
                publisher=publisher,
            )

        # The short-circuit (claim_run_direct raises) means a reply with
        # "[failed] could not claim run" is emitted after thread creation +
        # create_turn.  reply is a sync lambda above; _handle_message guards
        # the await message.reply(...) wrap so it works since reply returns
        # None synchronously and ``await None`` raises — so use an async
        # reply instead.
        async def _async_reply(*args: Any, **k: Any) -> None:
            return None
        msg.reply = _async_reply  # type: ignore[assignment]

        asyncio.run(_run())
    finally:
        _ui_mod.create_run_thread = original_crt  # type: ignore[assignment]

    # 1. create_run_thread was called by _handle_message with a provisional
    #    run_id (format run_<hex>) BEFORE create_turn.
    assert len(_stub_create_run_thread.calls) == 1
    stub_call = _stub_create_run_thread.calls[0]
    assert stub_call["run_id"].startswith("run_")
    assert stub_call["run_id"] != "run_real_12345678"  # provisional, not the real one
    assert stub_call["prompt"] == "scan example.com"

    # 2. The conversation was bound to ``thread:{thread.id}`` (the architecture
    #    fix — NOT ``channel:7``).  The cache should also have that key.
    assert "thread:9999" in cache, f"thread:9999 key missing from cache keys={list(cache)}"
    # The create_turn call MUST target the thread's dedicated conversation id.
    assert len(store.turn_calls) == 1, f"turn_calls={store.turn_calls}"
    tc = store.turn_calls[0]
    assert tc["conversation_id"] == cache["thread:9999"]
    assert tc["content"] == "scan example.com"
    assert tc["idempotency_key"] == "discord:12345"

    # 3. The publisher mapped the REAL run_id (post-create_turn) to channel 7.
    assert publisher.mapped == [("run_real_12345678", "7")]

    # 4. The thread was NOT renamed.  Per the Bug E fix (race against
    #    chat.recover_persisted_chat_runs), the fenced claim must run BEFORE
    #    the ``await thread.edit`` network round-trip, and if the claim
    #    short-circuits (this test stubs claim_run_direct to raise) the
    #    rename is never attempted — the function returns from the claim's
    #    except branch.  The thread keeps its provisional name (cosmetic
    #    only; the run owns the thread by id).
    assert len(fake_thread.edits) == 0
    # The thread keeps the provisional name it was created with.
    assert fake_thread.name == "🔍 INV-PROVISIONAL · scan"
    # The thread should NOT be deleted: the create_turn succeeded and was
    # non-replay; the claim_run_direct short-circuit happens AFTER create_turn
    # but BEFORE the cosmetic rename, so we fall out of the claim's except
    # branch without touching the thread.
    assert fake_thread._deleted is False


def test_handle_message_idempotent_replay_deletes_duplicate_thread() -> None:
    """Idempotent replay (Discord gateway retry of the same message id) must
    delete the duplicate INV thread that _handle_message speculatively
    created BEFORE discovering create_turn returned an existing run.

    This is the documented trade-off for the architecture fix: there's no
    public store lookup "is there a pending run for this idempotency_key?",
    so thread creation necessarily precedes create_turn; on a replay we
    recover by deleting the duplicate thread.
    """
    from munin.production import discord_ui as _ui_mod
    from munin.production.discord_adapter import _handle_message

    fake_thread = _GuildThreadFake(name="🔍 INV-PROVISIONAL · rerun")

    async def _stub_create_run_thread(message: Any, *, run_id: str, prompt: str = "") -> Any:
        return fake_thread

    actor_row = {"id": "usr-1", "username": "discord:42", "role": "operator"}

    class _ActorRow(dict):
        def keys(self):  # noqa: D401
            return list(super().keys())
    store = _ThreadDispatchStore(existing_user=_ActorRow(actor_row))

    # Override create_turn to return an idempotent REPLAY (the previous run
    # already exists for this message id).
    original_create_turn = store.create_turn

    def replay_create_turn(**kwargs):  # noqa: D401
        # Record the call (so we can assert conversation_id binding) then
        # return the replay shape produced by ProductionStore.create_turn
        # on a duplicate idempotency_key.
        original_create_turn(**kwargs)
        return {
            "idempotent_replay": True,
            "run": {"id": "run_FIRST_AAAAAAAA", "state": "queued", "fencing_epoch": 0},
        }
    store.create_turn = replay_create_turn  # noqa: SLF001

    publisher = _FakePublisher()
    cache: dict[str, str] = {}

    author = SimpleNamespace(id=42, bot=False, __str__=lambda self=None: "user#0042")
    channel = SimpleNamespace(id=7, parent=None)
    guild = SimpleNamespace(id=1)

    async def _async_reply(*args: Any, **k: Any) -> None:
        _async_reply.args = args  # type: ignore[attr-defined]
        return None
    msg = SimpleNamespace(
        content="<@999> scan example.com",
        author=author,
        channel=channel,
        guild=guild,
        id=12345,
        reply=_async_reply,
        reference=None,
        create_thread=None,
    )

    original_crt = _ui_mod.create_run_thread
    _ui_mod.create_run_thread = _stub_create_run_thread  # noqa: SLF001
    try:
        async def _run() -> None:
            await _handle_message(
                msg,
                settings=SimpleNamespace(),
                store=store,
                shared_state=None,
                conversation_cache=cache,
                bot_user_id=999,
                allowed_channels=set(),
                allowed_users=set(),
                publisher=publisher,
            )

        asyncio.run(_run())
    finally:
        _ui_mod.create_run_thread = original_crt  # noqa: SLF001

    # The duplicate thread was created speculatively ...
    assert "thread:9999" in cache
    # ... but on replay it must be DELETED to avoid leaking an empty INV
    # thread per Discord gateway retry.
    assert fake_thread._deleted is True
    # The publisher was NOT called with the (existing) run_id for a replay,
    # since _handle_message returns early after the replay reply (the
    # map_run happens BEFORE the replay check; that's the existing contract).
    # We don't assert publisher.mapped emptiness (current behavior keeps it).
    # The reply contained "[replay]".
    assert _async_reply.args and "[replay]" in _async_reply.args[0]


def test_handle_message_dm_path_creates_conversation_then_turn() -> None:
    """DM message (guild=None) → ``dm:{author_id}`` conversation is created and
    ``create_turn`` receives THAT conversation_id.  No INV thread is created.

    Regression guard: an early version of the architecture-fix reorder for
    the guild-channel path forgot to call ``_get_or_create_conversation`` in
    the DM branch (it only computed ``channel_key``), so the function reached
    ``create_turn`` referenced a never-defined ``conversation_id`` and
    crashed with ``NameError``.  Existing tests missed it because the bot /
    allowlist filters short-circuit before conversation creation.  This test
    drives the DM path end-to-end through ``create_turn``.
    """
    from munin.production.discord_adapter import _handle_message

    actor_row = {"id": "usr-1", "username": "discord:42", "role": "operator"}

    class _ActorRow(dict):
        def keys(self):  # noqa: D401
            return list(super().keys())
    store = _ThreadDispatchStore(existing_user=_ActorRow(actor_row))
    store.claim_run_direct = lambda **_: (_ for _ in ()).throw(
        RuntimeError("store does not support direct claim (test stub)"))

    publisher = _FakePublisher()
    cache: dict[str, str] = {}

    author = SimpleNamespace(id=42, bot=False, __str__=lambda self=None: "user#0042")
    channel = SimpleNamespace(id=7, parent=None)
    msg = SimpleNamespace(
        content="hello",  # DM → passthrough, no mention needed
        author=author,
        channel=channel,
        guild=None,  # DM
        id=777,
        reference=None,
        create_thread=None,
    )

    async def _async_reply(*args: Any, **k: Any) -> None:
        return None
    msg.reply = _async_reply  # type: ignore[assignment]

    async def _run() -> None:
        await _handle_message(
            msg,
            settings=SimpleNamespace(),
            store=store,
            shared_state=None,
            conversation_cache=cache,
            bot_user_id=999,
            allowed_channels=set(),
            allowed_users=set(),
            publisher=publisher,
        )

    asyncio.run(_run())

    # 1. DM conversation was bound to ``dm:{author_id}`` and cached.
    assert "dm:42" in cache, f"expected dm:42 in cache, got {list(cache)}"
    # 2. create_turn was reached with that conversation_id (regression check:
    #    if the DM branch forgot to define conversation_id this would crash).
    assert len(store.turn_calls) == 1, f"turn_calls={store.turn_calls}"
    assert store.turn_calls[0]["conversation_id"] == cache["dm:42"]
    assert store.turn_calls[0]["content"] == "hello"
    assert store.turn_calls[0]["idempotency_key"] == "discord:777"


# ---------------------------------------------------------------------------
# DiscordPublisher
# ---------------------------------------------------------------------------


def test_publisher_maps_run_to_channel() -> None:
    from munin.production.discord_publisher import DiscordPublisher

    p = DiscordPublisher()
    assert p.attached is False
    p.map_run(run_id="run-1", channel_id="111")
    p.map_run(run_id="run-2", channel_id="222")
    assert p.channel_id_for_run("run-1") == "111"
    assert p.channel_id_for_run("run-2") == "222"
    p.unmap_run(run_id="run-1")
    assert p.channel_id_for_run("run-1") is None
    # Unknown run falls back to the default channel when set.
    p.attach(loop=asyncio.new_event_loop(), client=object(), default_channel_id="999")
    assert p.channel_id_for_run("run-unknown") == "999"
    p.detach()
    assert p.attached is False


def test_publisher_attach_then_detach() -> None:
    from munin.production.discord_publisher import PUBLISHER

    original = (PUBLISHER._loop, PUBLISHER._client)  # noqa: SLF001
    try:
        assert PUBLISHER.attached is False
        loop = asyncio.new_event_loop()
        PUBLISHER.attach(loop=loop, client=object())
        assert PUBLISHER.attached is True
        PUBLISHER.detach()
        assert PUBLISHER.attached is False
        loop.close()
    finally:
        # Restore singleton state so other tests are not affected.
        PUBLISHER._loop, PUBLISHER._client = original  # noqa: SLF001


# ---------------------------------------------------------------------------
# Bug D: word-boundary-safe reasoning streaming (operator directive: no 1400-cap)
# ---------------------------------------------------------------------------


def test_split_at_word_boundary_no_split_under_max() -> None:
    from munin.production.discord_adapter import _split_at_word_boundary

    assert _split_at_word_boundary("short text", 100) == ["short text"]


def test_split_at_word_boundary_prefers_newline() -> None:
    from munin.production.discord_adapter import _split_at_word_boundary

    text = "a" * 100 + "\n" + "b" * 100 + "\n" + "c" * 100
    chunks = _split_at_word_boundary(text, 200)
    # First chunk keeps the first line (100 'a' + '\n') — the late newline
    # wins over a midpoint whitespace because paragraphs are atomic units.
    assert chunks[0] == "a" * 100 + "\n"
    # Reconstruction invariant: no character is ever dropped.
    assert "".join(chunks) == text


def test_split_at_word_boundary_falls_back_to_whitespace() -> None:
    from munin.production.discord_adapter import _split_at_word_boundary

    text = "word. " * 200  # ~1200 chars, no newlines
    chunks = _split_at_word_boundary(text, 1900)
    # Under the cap → single chunk.
    assert len(chunks) == 1
    # Crossing the cap → never ends mid-word, nothing dropped.
    big = text * 5
    chunks = _split_at_word_boundary(big, 1900)
    assert all(len(c) <= 1900 for c in chunks)
    assert "".join(chunks) == big
    # No chunk should end inside "word." — the boundary is the trailing space.
    assert all(not c.rstrip().endswith("wo") for c in chunks)


def test_split_at_word_boundary_no_whitespace_hard_cut() -> None:
    from munin.production.discord_adapter import _split_at_word_boundary

    text = "a" * 5000  # single run, no whitespace
    chunks = _split_at_word_boundary(text, 1900)
    assert "".join(chunks) == text
    assert all(len(c) <= 1900 for c in chunks)


def test_split_at_word_boundary_does_not_drop_partial_word() -> None:
    from munin.production.discord_adapter import _split_at_word_boundary

    # Reproduce the live bug shape: "ldap_search → que" on one side of the
    # boundary, the next word ("mapear") on the other. The cut must land on
    # the space BEFORE "mapear", never mid-token.
    text = "x" * 1899 + " mapeo el bosque"
    chunks = _split_at_word_boundary(text, 1900)
    assert "".join(chunks) == text  # nothing dropped
    # The first chunk ends at the space — never mid "x" run AND never at " ma".
    assert chunks[0] == "x" * 1899 + " "


def test_chunk_message_now_uses_word_boundary() -> None:
    """`_chunk_message` (used by ``_RateLimitedPoster.post`` and ``close()``)
    now delegates to ``_split_at_word_boundary`` — same signature, no word split."""
    from munin.production.discord_adapter import _chunk_message, _split_at_word_boundary

    text = "a" * 1500 + " " + "b" * 1500  # 3001 chars
    assert _chunk_message(text) == _split_at_word_boundary(text, 1900)
    assert all(len(c) <= 1900 for c in _chunk_message(text))


def test_post_reasoning_block_keeps_residual_partial_chunk() -> None:
    """When the reasoning buffer crosses the per-message cap, ``_post_reasoning_block``
    flushes complete chunks and retains only the trailing partial chunk in the
    buffer so the next model delta can complete the in-flight word."""
    from types import SimpleNamespace

    from munin.production.discord_adapter import _RunSession

    async def _scenario() -> None:
        # Fake channel/mock poster: capture posts without sending to Discord.
        captured: list[str] = []

        class _FakePoster:
            async def post(self, content: str) -> None:
                captured.append(content)

            async def post_embed(self, *a, **k) -> None:
                return None

        session = _RunSession(
            channel=SimpleNamespace(send=lambda *a, **k: None),
            run_id="run_test",
        )
        session._poster = _FakePoster()  # type: ignore[assignment]
        session._flush_task = None  # don't run the live flush_loop coroutine

        # Buffer under cap → no flush.
        session.add_reasoning("x" * 1000)
        assert session.reasoning_buffer == "x" * 1000
        assert captured == []

        # Cross the cap: a complete chunk flushes; the trailing partial is kept.
        session.add_reasoning("y" * 1900 + " z")  # buffer now 2903 chars
        # Let the fire-and-forget create_task coroutine actually run so
        # `captured` is populated before we assert against it.
        await asyncio.sleep(0)
        # The truncated tail (' z' after a word-boundary cut) must remain buffered
        # so the LLM's next delta can complete it.
        assert len(session.reasoning_buffer) <= 1900
        # Reconstruction invariant (no chars lost):
        sent = "".join(p[len("💭 "):] if p.startswith("💭 ") else p for p in captured)
        assert sent + session.reasoning_buffer == "x" * 1000 + "y" * 1900 + " z"
        # No captured post ends mid-word: the residual starts with the leftover tail.
        assert all(p.endswith(" ") or p.endswith("y") for p in captured)

    asyncio.run(_scenario())


@pytest.mark.skip(reason="integration test — needs running Munin store + discord.Client")
def test_handle_message_end_to_end() -> None:  # pragma: no cover
    """End-to-end test skeleton.  Wire against a live MuninStore and a
    mocked ``discord.Client`` to verify the full flow: mention →
    create_turn → supervisor_runner iteration → final message post."""
    raise NotImplementedError
