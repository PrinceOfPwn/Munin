# tags: [discord, regression, tests, _extract_prompt, send_discord_message, DiscordPublisher, event-loop, mention, dispatch, thread-safety, robre_mention, bot_user_id]
"""Regression tests for the 2026-08-06 Discord fixes.

Three live-session failures led to these guards:

1. ``_extract_prompt`` silently dropped legitimate invocations that do not
   start with the bot's own snowflake mention.  Two real-world shapes
   produced ``None`` and NO dispatch log:

   - A native mention tag whose ``bot_user_id`` was not yet known (startup
     backlog window before ``client.user`` populated) skipped the
     ``tag in content`` check entirely.
   - A mention rendered as ``<@&ROLE_ID>`` / arbitrary `<@ID>` fails the
     `<@id>`/`<@!id>`-only tag match (the historical "Nico wrote @Munin and
     got nothing" cause already called out in PR #52).

   Fix ``ecc8100`` adds a *lenient final fallback*: a leading mention tag
   to ANY entity (user, legacy nickname, role) is an invocation.  The
   guard explicitly does NOT resurrect the 78c6bb4 regression (every
   channel message spawns an empty INV thread): ordinary chatter without a
   leading mention tag still returns None.

2. ``send_discord_message`` (MCP tool handler thread) raised
   ``RuntimeError: no running event loop`` when the adapter had attached a
   loop because the pre-fix guard called ``asyncio.get_running_loop()`` in
   a thread with no loop.  Any such failure fell back to the legacy bridge
   with a scary WARNING.  Fix ``21fb088``: the tool now only checks
   ``loop.is_running()`` and uses ``run_coroutine_threadsafe``.

3. ``DiscordPublisher.publish`` hit the same ``get_running_loop()`` trap;
   the same commit wraps the check in a ``try/except RuntimeError`` so a
   cross-thread caller uses ``run_coroutine_threadsafe`` instead of
   raising.

These tests use a background event loop (``_Probe``) so the main test
thread has NO running loop — exactly like the MCP tool-handler thread on
the live runner — without a real Discord client.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# _extract_prompt — lenient leading-mention fallback (fix ecc8100)
# ---------------------------------------------------------------------------


def _msg(*, content: str, guild: object | None = None) -> Any:
    return SimpleNamespace(content=content, guild=guild, reference=None)


def test_extract_prompt_role_mention_rejected_when_bot_known() -> None:
    """Role mentions <@&ID> are NEVER invocations (no authoritative
    invocation role is configured; security review finding #1)."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@&102938475612345678> scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        is None
    )


def test_extract_prompt_legacy_nickname_mention_matches_bot() -> None:
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@!42> scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )


def test_extract_prompt_other_user_mention_rejected_when_bot_known() -> None:
    """A mention to another member is NOT an invocation whenever the bot id
    is known (security review #1): no arbitrary-mention trigger surface."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@999> what are my options", guild=SimpleNamespace(id=1)), bot_user_id=42)
        is None
    )


def test_extract_prompt_backlog_when_bot_user_id_unknown() -> None:
    """Startup backlog timing: bot_user_id=None must NOT drop a leading
    user mention.  Pre-fix this returned None and the operator's message was
    silently swallowed (no dispatch log at all).  Role tags remain rejected
    even in the unknown-bot window (security review #1)."""
    from munin.production.discord_adapter import _extract_prompt

    content = "<@1532383753959476344> scan example.com"
    assert (
        _extract_prompt(_msg(content=content, guild=SimpleNamespace(id=1)), bot_user_id=None)
        == "scan example.com"
    )
    # Role mention in the unknown-bot window: STILL rejected.
    assert (
        _extract_prompt(_msg(content="<@&123456789012345678> scan", guild=SimpleNamespace(id=1)), bot_user_id=None)
        is None
    )


def test_extract_prompt_mention_stripped_clean() -> None:
    from munin.production.discord_adapter import _extract_prompt

    # Multi-tag messages keep the secondary mention as plain text.
    assert (
        _extract_prompt(_msg(content="<@42> <@1234> hi both", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "<@1234> hi both"
    )
    # Leading whitespace before the tag is tolerated.
    assert (
        _extract_prompt(_msg(content="   <@42> ping", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "ping"
    )


def test_extract_prompt_mention_only_still_no_invocation() -> None:
    """A bare mention with no following instruction must NOT spawn a run."""
    from munin.production.discord_adapter import _extract_prompt

    assert not _extract_prompt(_msg(content="<@42>", guild=SimpleNamespace(id=1)), bot_user_id=42)
    # Arbitrary-user bare mention likewise not an invocation.
    assert not _extract_prompt(_msg(content="<@999>", guild=SimpleNamespace(id=1)), bot_user_id=42)


def test_extract_prompt_chatter_without_mention_never_invocation() -> None:
    """The 78c6bb4 regression guard: ordinary channel chatter NEVER spawns
    a run even though the leading-mention fallback is now lenient."""
    from munin.production.discord_adapter import _extract_prompt

    guild = SimpleNamespace(id=1)
    for content in ("hello everyone", "ajajaj", "los artistas de oblock", ":eyes:", "x@y"):
        assert _extract_prompt(_msg(content=content, guild=guild), bot_user_id=42) is None, (
            f"chatter {content!r} must not be an invocation"
        )


def test_extract_prompt_textual_atmunin_still_works() -> None:
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="@Munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )


# ---------------------------------------------------------------------------
# send_discord_message — cross-thread event-loop safety (fix 21fb088)
# ---------------------------------------------------------------------------


class _FakeChannel:
    def __init__(self, out: list[str]) -> None:
        self._out = out

    async def send(self, content: str) -> None:
        self._out.append(content)


class _FakeClient:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id: int) -> _FakeChannel:
        return self._channel

    async def fetch_channel(self, channel_id: int) -> _FakeChannel:
        return self._channel


class _Probe:
    """Runs an asyncio loop in a background thread so the test thread has
    NO running loop — exactly like the MCP tool-handler thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._errors: list[BaseException] = []
        self.loop.set_exception_handler(lambda _l, ctx: self._errors.append(ctx.get("exception") or Exception(str(ctx))))

    def __enter__(self) -> "_Probe":
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=3)
        self.loop.close()


def test_send_discord_message_from_thread_without_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP tool handler runs on a worker thread; with the pre-fix code
    this raised RuntimeError('no running event loop') and fell back to the
    bridge with a WARNING.  The adapter path must now deliver synchronously."""
    from munin.production.discord_publisher import DiscordPublisher
    from munin.mcp.tools import discord_tool

    delivered: list[str] = []
    channel = _FakeChannel(delivered)

    with _Probe() as probe:
        publisher = DiscordPublisher()
        publisher.attach(loop=probe.loop, client=_FakeClient(channel), default_channel_id="123456")
        publisher.map_run(run_id="run_x", channel_id="123456")

        monkeypatch.setattr(discord_tool, "PUBLISHER", publisher)
        # Bridge calls must NOT happen while the adapter is attached.
        monkeypatch.setattr(discord_tool, "get_discord_config", lambda: SimpleNamespace(outbound_enabled=True))
        monkeypatch.setattr(discord_tool, "post_to_discord", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bridge reached while adapter attached")))
        monkeypatch.setattr(discord_tool, "get_bridge", lambda: None)

        try:
            result = discord_tool.send_discord_message("hello from agent", run_id="run_x")
        finally:
            publisher.detach()

    assert result["ok"] is True
    assert result["summary"] == "Discord message queued"
    assert delivered == ["hello from agent"]
    assert probe._errors == []  # no unhandled loop exceptions


def test_send_discord_message_same_loop_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller already runs ON the adapter loop, blocking on
    future.result() would deadlock the loop; the same-loop branch schedules
    PUBLISHER.publish via create_task and returns an immediate queued ack
    (CodeRabbit stability review #3)."""
    from munin.production.discord_publisher import DiscordPublisher
    from munin.mcp.tools import discord_tool

    delivered: list[str] = []
    channel = _FakeChannel(delivered)

    with _Probe() as probe:
        publisher = DiscordPublisher()
        publisher.attach(loop=probe.loop, client=_FakeClient(channel), default_channel_id="999")
        publisher.map_run(run_id="run_s", channel_id="999")

        monkeypatch.setattr(discord_tool, "PUBLISHER", publisher)
        monkeypatch.setattr(discord_tool, "get_discord_config", lambda: SimpleNamespace(outbound_enabled=True))
        monkeypatch.setattr(discord_tool, "post_to_discord", lambda *a, **k: (_ for _ in ()).throw(AssertionError("bridge reached while adapter attached")))
        monkeypatch.setattr(discord_tool, "get_bridge", lambda: None)

        async def _drive() -> dict[str, Any]:
            result = discord_tool.send_discord_message("same-loop hello", run_id="run_s")
            await asyncio.sleep(0)  # let the created publish task run a tick
            await asyncio.sleep(0)
            return result

        try:
            future = asyncio.run_coroutine_threadsafe(_drive(), probe.loop)
            result = future.result(timeout=10)
        finally:
            publisher.detach()

    assert result["ok"] is True
    assert result["summary"] == "Discord message queued"
    assert delivered == ["same-loop hello"]
    assert probe._errors == []  # no unhandled loop exceptions


def test_send_discord_message_empty_content_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from munin.mcp.tools import discord_tool

    monkeypatch.setattr(discord_tool, "PUBLISHER", SimpleNamespace(attached=False))
    result = discord_tool.send_discord_message("   ")
    assert result["ok"] is False
    assert result["error"]["code"] == "empty_content"


# ---------------------------------------------------------------------------
# DiscordPublisher.publish — cross-thread event loop safety (fix)
# ---------------------------------------------------------------------------


def test_publisher_publish_from_thread_without_loop_delivers() -> None:
    """publish() is invoked through run_coroutine_threadsafe from the tool
    thread; the same_loop guard must not raise RuntimeError when callers
    have no event loop (previously a bare get_running_loop() check in a
    wrapper would have crashed the MCP handler thread)."""
    from munin.production.discord_publisher import DiscordPublisher

    delivered: list[str] = []
    channel = _FakeChannel(delivered)

    with _Probe() as probe:
        publisher = DiscordPublisher()
        publisher.attach(loop=probe.loop, client=_FakeClient(channel), default_channel_id="777")
        publisher.map_run(run_id="run_y", channel_id="777")

        # Main thread has no running loop — exactly the MCP handler case.
        future = asyncio.run_coroutine_threadsafe(
            publisher.publish(run_id="run_y", content="banner"), probe.loop
        )
        ok = future.result(timeout=10)
        publisher.detach()

    assert ok is True
    assert delivered == ["banner"]


def test_publisher_publish_false_when_detached() -> None:
    from munin.production.discord_publisher import DiscordPublisher

    p = DiscordPublisher()
    result = asyncio.run(p.publish(run_id="run_z", content="hello"))
    assert result is False