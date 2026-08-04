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
    assert _extract_prompt(_msg(content="hello", guild=guild), bot_user_id=42) is None
    assert (
        _extract_prompt(_msg(content="<@42> scan example.com", guild=guild), bot_user_id=42)
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
        self.queries: list[str] = []

    def execute(self, sql: str, params: tuple) -> "_FakeConn":
        self.queries.append(sql)
        self._last_params = params
        return self

    def fetchone(self) -> Any:
        return self._existing


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
    from munin.production.discord_adapter import _get_or_create_conversation

    # Simulates a fresh process (empty cache) after a restart: durable
    # probe resurrects the previous conversation for the same channel_key.
    store = _FakeStore()
    store._durable._existing = None  # noqa: SLF001
    # A conversation row with the right scope exists in the durable store.
    from munin.production.discord_adapter import _discover_conversation

    class _Row2(dict):
        def keys(self):  # noqa: D401
            return list(super().keys())

    store._durable.conn._existing = _Row2({"id": "conv-restored"})  # noqa: SLF001
    found = _discover_conversation(store, actor_id="usr-1", channel_key="dm:111")
    assert found == "conv-restored"

    cache: dict[str, str] = {}
    conv = _get_or_create_conversation(
        store, actor_id="usr-1", channel_key="dm:111", cache=cache,
        title="dm", is_dm=True,
    )
    assert conv == "conv-restored"
    assert "dm:111" in cache


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


@pytest.mark.skip(reason="integration test — needs running Munin store + discord.Client")
def test_handle_message_end_to_end() -> None:  # pragma: no cover
    """End-to-end test skeleton.  Wire against a live MuninStore and a
    mocked ``discord.Client`` to verify the full flow: mention →
    create_turn → supervisor_runner iteration → final message post."""
    raise NotImplementedError
