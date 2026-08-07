# tags: [discord, regression, tests, _extract_prompt, send_discord_message, DiscordPublisher, event-loop, mention, dispatch, thread-safety, robre_mention, bot_user_id]
"""Regression tests for the 2026-08-06/07 Discord fixes.

Live-session history that shaped the invocation contract:

1. ``_extract_prompt`` (2026-08-06, fix ``ecc8100``): silently dropped
   legitimate invocations that did not start with the bot's own snowflake
   mention — a startup backlog window (``bot_user_id`` not yet populated)
   and role/nickname mentions the strict ``<@id>``/``<@!id>`` match missed.
   ``ecc8100`` added a lenient leading-mention fallback to ANY entity.

2. ``_extract_prompt`` (2026-08-07, regression ``3a560bb`` / PR #58
   CodeRabbit review): the lenient fallback was hardened to accept ONLY the
   bot's own snowflake tag when ``bot_user_id`` was known, rejecting any
   mention to other members or roles.  This closed a hypothetical
   arbitrary-mention trigger surface but ALSO broke the operator's real
   invocation shape — mentioning a nick/role that resolved to a non-bot
   entity produced a silent ``extract-drop`` with NO dispatch log (live
   sessions 31147196158, 31148018808).

3. ``_extract_prompt`` (2026-08-07, this commit): operator directive —
   if the message says "munin" in ANY form (case-insensitive: native tag,
   literal ``@Munin``/``@munin``, mid-sentence ``hey munin do X``, bare
   ``munin``), the bot must respond.  Channel- and author-level allowlists
   upstream of this function still bound WHO reaches this code path, so the
   broader text gate does not widen the raw trigger surface beyond
   operator-curated channels.  Roles/nicks containing "munin" now invoke;
   roles/nicks WITHOUT "munin" continue to be rejected chatter.

``send_discord_message`` / ``DiscordPublisher`` guards below are unchanged
(cross-thread event-loop safety from fixes ``21fb088``): they use a
background event loop so the main test thread has NO running loop, exactly
like the MCP tool-handler thread on the live runner.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# _extract_prompt — "if it says munin, respond" contract (2026-08-07)
# ---------------------------------------------------------------------------


def _msg(*, content: str, guild: object | None = None) -> Any:
    return SimpleNamespace(content=content, guild=guild, reference=None)


def test_extract_prompt_native_bot_tag_strips_tag() -> None:
    """Native mention of the bot ``<@BOT_ID>`` strips the tag and returns
    the instruction."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@42> scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )


def test_extract_prompt_legacy_nickname_tag_strips_tag() -> None:
    """Legacy nickname mention ``<@!BOT_ID>`` strips the tag and returns
    the instruction."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@!42> scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )


def test_extract_prompt_textual_atmunin_strips_prefix() -> None:
    """Literal ``@Munin`` (typed, not a native tag) strips the prefix and
    returns the instruction.  Case-insensitive: ``@munin`` likewise."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="@Munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )
    assert (
        _extract_prompt(_msg(content="@munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )


def test_extract_prompt_munin_mid_sentence_invokes() -> None:
    """Operator directive: even ``munin`` appearing mid-sentence (not as a
    stripped leading mention) is an invocation — the instruction carries
    intent.  The whole content is returned as the prompt (the word is not
    stripped because it isn't a leading mention)."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="hey munin what can you do in our LDAP", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "hey munin what can you do in our LDAP"
    )
    assert (
        _extract_prompt(_msg(content="Munin, do a thing", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "Munin, do a thing"
    )


def test_extract_prompt_role_named_munin_invokes() -> None:
    """A role mention the client renders as ``@Munin`` (literal text, not a
    nativetag) still triggers because the content contains ``munin``.  This
    is the live failure mode ``ecc8100`` documented and ``3a560bb`` broke."""
    from munin.production.discord_adapter import _extract_prompt

    # Literal "@Munin" text the client shows when a role named Munin is
    # mentioned without native resolution.
    assert _extract_prompt(
        _msg(content="@Munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42
    ) is not None


def test_extract_prompt_role_tag_without_munin_chars_rejected() -> None:
    """A role mention ``<@&ROLE_ID>`` whose role name does NOT contain
    ``munin`` is NOT an invocation — there is no authoritative invocation
    role configured, and the content carries no ``munin`` token.  This
    replaces the old strict-role-rejection test: roles are now gated by
    the text rule, not by the tag shape."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@&102938475612345678> scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        is None
    )


def test_extract_prompt_command_prefix_invokes() -> None:
    """``/munin ...`` and ``!munin ...`` prefixes are invocations
    (COMMAND_PREFIXES)."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="/munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )
    assert (
        _extract_prompt(_msg(content="!munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "scan example.com"
    )


def test_extract_prompt_backlog_when_bot_user_id_unknown() -> None:
    """Startup backlog timing: bot_user_id=None must NOT drop a leading
    bot-tag mention once client.user populates, AND the text gate catches
    any literal ``munin`` even when the bot id is still unknown."""
    from munin.production.discord_adapter import _extract_prompt

    # Bot-tag mention during the unknown-bot window: the tag doesn't match
    # (no bot_user_id to compare), but the text gate below catches nothing
    # because there's no "munin" token here — so this is a known limitation:
    # during the brief startup window, a bare bot-tag mention WITHOUT the
    # word "munin" may still drop.  The text gate covers the operator's
    # documented invocation shapes; the tag path is best-effort then.
    #
    # The text gate DOES cover the main case:
    assert (
        _extract_prompt(_msg(content="@munin scan example.com", guild=SimpleNamespace(id=1)), bot_user_id=None)
        == "scan example.com"
    )
    # And any role/nick containing "munin":
    assert (
        _extract_prompt(_msg(content="<@&123456789012345678> @munin do something", guild=SimpleNamespace(id=1)), bot_user_id=None)
        is not None
    )


def test_extract_prompt_mention_stripped_clean() -> None:
    """Multi-tag messages: the leading bot tag is stripped, secondary
    mentions kept as plain text."""
    from munin.production.discord_adapter import _extract_prompt

    assert (
        _extract_prompt(_msg(content="<@42> <@1234> hi both", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "<@1234> hi both"
    )
    # Leading whitespace before the tag is tolerated.
    assert (
        _extract_prompt(_msg(content="   <@42> ping", guild=SimpleNamespace(id=1)), bot_user_id=42)
        == "ping"
    )


def test_extract_prompt_bare_mention_only_still_no_invocation() -> None:
    """A bare mention with no instruction must NOT spawn a run (returns
    None because the stripped rest is empty)."""
    from munin.production.discord_adapter import _extract_prompt

    assert not _extract_prompt(_msg(content="<@42>", guild=SimpleNamespace(id=1)), bot_user_id=42)
    # Bare literal @Munin with nothing after it likewise.
    assert not _extract_prompt(_msg(content="@Munin", guild=SimpleNamespace(id=1)), bot_user_id=42)


def test_extract_prompt_chatter_without_munin_never_invocation() -> None:
    """The 78c6bb4 regression guard: ordinary channel chatter that does NOT
    contain ``munin`` (in any casing) NEVER spawns a run."""
    from munin.production.discord_adapter import _extract_prompt

    guild = SimpleNamespace(id=1)
    for content in (
        "hello everyone",
        "ajajaj",
        "los artistas de oblock",
        ":eyes:",
        "x@y",
        "<@999> what are my options",        # other-user mention, no munin token
        "<@&102938475612345678> scan",       # role tag, no munin text
        "@NIGHT do a thing",                 # nick mention, no munin
    ):
        assert _extract_prompt(_msg(content=content, guild=guild), bot_user_id=42) is None, (
            f"chatter {content!r} must not be an invocation"
        )


def test_extract_prompt_non_bot_user_mention_with_munin_invokes() -> None:
    """Regression of the 3a560bb hardening: a mention to another member
    that ALSO contains the word ``munin`` IS an invocation.  This was the
    live failure (operator wrote ``@NIGHT munin do X``-style or the role
    rendered as ``@Munin``) and produced a silent extract-drop."""
    from munin.production.discord_adapter import _extract_prompt

    assert _extract_prompt(
        _msg(content="<@999> munin what are my options", guild=SimpleNamespace(id=1)),
        bot_user_id=42,
    ) is not None


def test_extract_prompt_case_variants_all_invoke() -> None:
    """``MUNIN``, ``mUnIn``, ``MuNiN`` — case-insensitive gate."""
    from munin.production.discord_adapter import _extract_prompt

    for content in ("@MUNIN scan", "@mUnIn scan", "MUNIN do X", "munin do X"):
        assert _extract_prompt(
            _msg(content=content, guild=SimpleNamespace(id=1)), bot_user_id=42
        ) is not None, f"{content!r} must invoke"


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


# ---------------------------------------------------------------------------
# classify_runner_exception — provider error surface (2026-08-07)
# ---------------------------------------------------------------------------


def test_classify_runner_exception_provider_timeout_readable() -> None:
    """A real live-session failure surfaced as a raw ``httpcore.ReadTimeout``
    ("Operation failed: httpcore.ReadTimeout(...)").  The Discord surface
    must instead name the provider timeout so the operator knows the run did
    not complete and can retry it, rather than decoding a transport type."""
    from munin.production.discord_adapter import classify_runner_exception

    exc = TimeoutError("timed out")
    exc.__cause__ = RuntimeError("httpcore.ReadTimeout('timed out')")
    message = classify_runner_exception(exc)

    assert message.startswith("⚠️ **Operation failed — provider timeout")
    assert "did NOT complete" in message
    assert "provider" in message.lower()


def test_classify_runner_exception_generic_keeps_contract() -> None:
    """Non-provider failures keep the historical ``Operation failed: {exc}``
    contract that operators and automation already rely on."""
    from munin.production.discord_adapter import classify_runner_exception

    exc = ValueError("boom")
    message = classify_runner_exception(exc)

    assert message == "Operation failed: boom"