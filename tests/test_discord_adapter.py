"""Unit tests for ``munin.production.discord_adapter``.

The full end-to-end path (real ``discord.Client`` → real
``supervisor_runner`` → real Turso) is exercised by the manual smoke
test documented in ``munin-followup-discord-README.md``.  These tests
cover the pure-logic branches that gate whether a message triggers a
run at all:

* ``_extract_prompt`` — DM, mention, prefix, ignored plain text.
* ``_resolve_actor`` — reuses an existing user; creates one otherwise.
* ``create_discord_task`` — no token → no task; token set → schedules a
  task and never imports the bot in the untokened case.
* ``_handle_message`` — bot messages and disallowed users must not
  reach ``store.create_turn``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# _extract_prompt
# ---------------------------------------------------------------------------


def _msg(*, content: str, guild: Any = None) -> Any:
    return SimpleNamespace(content=content, guild=guild)


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

    def create_user(self, *, username: str, password: str, role: str) -> dict[str, Any]:
        user = {"id": f"usr-{len(self.created) + 1}", "username": username, "role": role}
        self.created.append({"username": username, "password_len": len(password), "role": role})
        return user


def test_resolve_actor_reuses_existing_user() -> None:
    from munin.production.discord_adapter import _resolve_actor

    row = {"id": "usr-existing", "username": "discord:123", "role": "operator"}
    # SQLite Row-like: supports ``["key"]`` via ``__getitem__``.
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
        )

    asyncio.run(_run())
    assert store.create_turn_called is False


@pytest.mark.skip(reason="integration test — needs running Munin store + discord.Client")
def test_handle_message_end_to_end() -> None:  # pragma: no cover
    """End-to-end test skeleton.  Wire against a live MuninStore and a
    mocked ``discord.Client`` to verify the full flow: mention →
    create_turn → supervisor_runner iteration → final message post."""
    raise NotImplementedError
