"""Discord chat adapter (follow-up to Fase 2 of issue #9).

Historically Munin shipped a ``discord_adapter.py`` whose only job was to
spawn a ``threading.Thread(target=dispatcher.run_once)`` per inbound
message.  Fase 2 deleted the dispatcher (and with it the adapter) because
``ProductionDispatcher.run_once`` no longer exists — the whole run path
is Deep Agents + :func:`munin.core.runtime_adapter.supervisor_runner`
now.

This module reintroduces the Discord bridge on top of the new
architecture:

* Lives **in the same process** as :mod:`munin.server` (uvicorn ASGI).
  There is no daemon thread, no second process, and no shared queue.
  A single ``asyncio.Task`` runs ``discord.Client.start(token)`` and the
  supervisor runs directly on that event loop.

* Uses the async ``discord.py >= 2.4`` client library (already declared
  in ``pyproject.toml``); no self-bot forks.

* Reuses the exact same store composition Fase 3 wired up for the HTTP
  ``/api/chat`` path: :class:`SharedStateStore` (tools/soul/settings)
  with :meth:`ProductionStore.consume_pending_guidance` attribute-bound
  onto it, so :class:`OperatorGuidanceMiddleware` finds guidance without
  an adapter class.

Message flow
------------

1. ``on_message`` fires for every message the bot can see.  We filter
   out messages authored by bots (including self) and require either
   a DM channel, an explicit mention of the bot, or a ``/munin <text>``
   / ``!munin <text>`` prefix.  A configurable channel-ID and user-ID
   allowlist is applied on top of that.

2. The Discord author is mapped to a Munin actor via
   :func:`_resolve_actor`.  We look up ``username='discord:{author.id}'``
   in the users table; if absent, we call ``store.create_user(...)`` with
   a random 64-byte password (Munin's password policy demands >= 12
   chars; this user never logs in via HTTP so the password is
   deliberately unreachable).  Rationale is documented in the README —
   the Discord bearer *is* the auth boundary, so a virtual actor keeps
   the audit trail honest without opening a password login path.

3. Conversations are keyed by ``(channel_id, author_id)``.  A first
   message from a given (channel, author) pair creates a new
   conversation; subsequent messages reuse the same conversation id, so
   the multi-turn thread stays coherent even if the operator interleaves
   messages with other Discord activity.  The mapping is process-local
   — a restart starts fresh conversations (documented limitation).

4. ``store.create_turn(...)`` creates the durable ``agent_runs`` row,
   assistant placeholder, and idempotency key.  We then run
   ``supervisor_runner`` directly and consume its envelope stream in the
   same event loop — no thread hop.

5. Streaming envelopes are collected into an in-flight buffer keyed by
   Discord message.  A per-run flush task edits a *single* bot message
   at most every ``DISCORD_FLUSH_INTERVAL`` seconds so we stay within
   the 5-msg / 5-sec / channel rate limit.  Reasoning tokens are
   concatenated; ``tool_intent`` / ``tool_result`` / ``tool_failed``
   become bullet lines under a "Tools" section.  On terminal
   ``run_state`` we post a final message containing the completed
   ``content`` (broken into 2000-char chunks — Discord's per-message
   cap).

Interface with the server
-------------------------

:mod:`munin.server` calls :func:`create_discord_task` in its startup
hook.  When ``MUNIN_DISCORD_BOT_TOKEN`` is unset the function is a
no-op and returns ``None``; when the token is set it spawns an
``asyncio.Task`` running the client and returns it.  Startup does not
block on Discord readiness — a bad token surfaces as a background task
failure that is logged but does not fail-fast the uvicorn process (the
HTTP API is the primary interface; Discord is opt-in gravy).

The adapter deliberately does **not** import from
:mod:`munin.production.chat` or :mod:`munin.production.asgi` — the
request-handler stack is untouched.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from typing import Any

log = logging.getLogger("munin.production.discord_adapter")

# --- Rate-limit tunables ----------------------------------------------------
#
# Discord enforces 5 messages / 5 seconds / channel and even stricter caps on
# per-message edits.  Editing a single "status" message every 2.5s buys us
# a comfortable margin without blocking the run loop.
DISCORD_FLUSH_INTERVAL = 2.5
DISCORD_MAX_MESSAGE_CHARS = 1900  # 2000-char hard cap, keep headroom for markdown
DISCORD_STATUS_MAX_CHARS = 1800
DISCORD_TOOL_TAIL = 8            # last N tool events shown in the status message

# --- Command surface --------------------------------------------------------
COMMAND_PREFIXES = ("/munin ", "!munin ")


def _parse_id_list(raw: str) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _resolve_actor(store: Any, *, discord_user_id: int, display_name: str) -> dict[str, Any]:
    """Return the Munin actor row for a Discord user, creating it lazily.

    We look up ``discord:{id}`` in the durable users table via the store's
    read-only cursor.  When absent, we mint a fresh user with a strong
    random password (>= 12 chars per policy).  The password is *never*
    stored anywhere — the Discord bearer authenticates the request, so
    HTTP login for this virtual user is intentionally impossible.
    """
    username = f"discord:{discord_user_id}"

    # The MuninStore façade routes ``users`` reads through the durable
    # backend; ``ProductionStore._read_only`` gives us a cursor without a
    # transaction which is what we want for a pure lookup.
    durable = getattr(store, "_durable", None) or store
    try:
        with durable._read_only() as conn:  # noqa: SLF001 - documented probe
            row = conn.execute(
                "SELECT id, username, role FROM users WHERE username=? AND disabled_at_ms IS NULL",
                (username,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.warning("discord: user lookup failed for %s: %s", username, exc)
        row = None

    if row:
        return {
            "id": row["id"] if hasattr(row, "keys") else row[0],
            "username": row["username"] if hasattr(row, "keys") else row[1],
            "role": row["role"] if hasattr(row, "keys") else row[2],
            "display_name": display_name,
        }

    password = secrets.token_urlsafe(48)  # 64+ chars, satisfies policy
    user = store.create_user(username=username, password=password, role="operator")
    log.info("discord: created virtual actor username=%s id=%s", username, user["id"])
    return {**user, "display_name": display_name}


def _get_or_create_conversation(
    store: Any,
    *,
    actor_id: str,
    channel_key: str,
    cache: dict[tuple[str, str], str],
    title: str,
) -> str:
    """Return the conversation id used for a (channel, actor) pair.

    A process-local cache maps ``(actor_id, channel_key)`` to the
    conversation id.  On cache miss we allocate a new conversation
    through the store.  We intentionally do *not* try to discover a
    prior conversation on disk — a Munin restart starts a fresh
    Discord conversation to avoid mis-associating threads across
    deployments (documented limitation).
    """
    key = (actor_id, channel_key)
    conv_id = cache.get(key)
    if conv_id:
        return conv_id
    conversation = store.create_conversation(
        owner_id=actor_id,
        title=title[:160] or "Discord conversation",
        tags=["discord"],
        scope={"source": "discord", "channel_key": channel_key},
    )
    cache[key] = conversation["id"]
    return conversation["id"]


def _chunk_message(text: str, *, size: int = DISCORD_MAX_MESSAGE_CHARS) -> list[str]:
    text = text or ""
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


class _RunSession:
    """State for a single in-flight Discord-triggered run.

    Owns the buffer of reasoning tokens, the rolling list of tool
    events, and the "status message" that gets edited in-place while
    the run streams.  A per-session flush task edits at most every
    ``DISCORD_FLUSH_INTERVAL`` seconds so we stay under the Discord
    edit-rate ceiling.
    """

    def __init__(self, *, channel: Any, run_id: str) -> None:
        self.channel = channel
        self.run_id = run_id
        self.reasoning: list[str] = []
        self.tools: list[str] = []
        self.status_message: Any = None
        self._dirty = False
        self._closed = False
        self._flush_task: asyncio.Task | None = None
        self._last_flush = 0.0

    def add_reasoning(self, text: str) -> None:
        if text:
            self.reasoning.append(text)
            self._dirty = True

    def add_tool_event(self, line: str) -> None:
        if line:
            self.tools.append(line)
            self._dirty = True

    def _render_status(self) -> str:
        body = "".join(self.reasoning).strip()
        if len(body) > DISCORD_STATUS_MAX_CHARS:
            body = "..." + body[-DISCORD_STATUS_MAX_CHARS:]
        parts: list[str] = []
        if body:
            parts.append(body)
        if self.tools:
            tail = self.tools[-DISCORD_TOOL_TAIL:]
            parts.append("**Tools**\n" + "\n".join(tail))
        text = "\n\n".join(parts) if parts else "_working..._"
        return text[:DISCORD_MAX_MESSAGE_CHARS]

    async def flush_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(DISCORD_FLUSH_INTERVAL)
            if not self._dirty:
                continue
            await self._flush()

    async def _flush(self) -> None:
        self._dirty = False
        self._last_flush = time.monotonic()
        content = self._render_status()
        try:
            if self.status_message is None:
                self.status_message = await self.channel.send(content or "_working..._")
            else:
                await self.status_message.edit(content=content or "_working..._")
        except Exception as exc:  # noqa: BLE001
            log.debug("discord: flush failed run_id=%s: %s", self.run_id, exc)

    async def close(self, *, final_content: str, ok: bool) -> None:
        self._closed = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(BaseException):
                await self._flush_task
        # One last edit so the status message reflects the run just before
        # we post the final content.
        with contextlib.suppress(Exception):
            await self._flush()
        prefix = "[completed]" if ok else "[failed]"
        for chunk in _chunk_message(f"{prefix} {final_content}".rstrip()):
            with contextlib.suppress(Exception):
                await self.channel.send(chunk)


def _extract_prompt(message: Any, *, bot_user_id: int | None) -> str | None:
    """Return the trimmed prompt text, or ``None`` if the message should be ignored.

    Rules:
    * DMs → whole content.
    * Guild channel → require either a mention of the bot OR one of the
      ``COMMAND_PREFIXES``.  This keeps the bot from replying to every
      chit-chat in a busy channel where it happens to be present.
    """
    content = (message.content or "").strip()
    if not content:
        return None

    # DM channel — treat as an implicit invocation.
    is_dm = getattr(message, "guild", None) is None
    if is_dm:
        return content

    if bot_user_id is not None:
        mention_tag = f"<@{bot_user_id}>"
        role_mention_tag = f"<@!{bot_user_id}>"
        for tag in (mention_tag, role_mention_tag):
            if tag in content:
                return content.replace(tag, "", 1).strip()

    for prefix in COMMAND_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix) :].strip()

    return None


async def _handle_message(
    message: Any,
    *,
    settings: Any,
    store: Any,
    shared_state: Any,
    conversation_cache: dict[tuple[str, str], str],
    bot_user_id: int | None,
    allowed_channels: set[str],
    allowed_users: set[str],
) -> None:
    if getattr(message.author, "bot", False):
        return
    if bot_user_id is not None and message.author.id == bot_user_id:
        return

    channel_id = str(getattr(message.channel, "id", ""))
    author_id = str(message.author.id)

    if allowed_channels and channel_id not in allowed_channels:
        return
    if allowed_users and author_id not in allowed_users:
        return

    prompt = _extract_prompt(message, bot_user_id=bot_user_id)
    if not prompt:
        return

    log.info(
        "discord: dispatch channel=%s author=%s prompt_len=%d",
        channel_id, author_id, len(prompt),
    )

    try:
        actor = _resolve_actor(
            store, discord_user_id=int(author_id), display_name=str(message.author),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: actor resolution failed: %s", exc)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not resolve actor: {exc}")
        return

    try:
        conversation_id = _get_or_create_conversation(
            store,
            actor_id=actor["id"],
            channel_key=channel_id,
            cache=conversation_cache,
            title=f"discord:{message.author}",
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: conversation bootstrap failed: %s", exc)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not open conversation: {exc}")
        return

    try:
        turn = store.create_turn(
            actor_id=actor["id"],
            conversation_id=conversation_id,
            content=prompt,
            idempotency_key=f"discord:{message.id}",
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: create_turn failed: %s", exc)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not queue run: {exc}")
        return

    run_id = turn["run"]["id"]

    # Idempotent replay (Discord retry / duplicate message id): don't
    # re-run, just acknowledge.
    if turn.get("idempotent_replay"):
        with contextlib.suppress(Exception):
            await message.reply(f"[replay] run {run_id} already exists")
        return

    # Claim the run so it moves to 'running' — we own it end-to-end in
    # this coroutine, no worker will steal it.
    try:
        lease_token, assistant_message_id = _claim_direct(store, run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: claim_run_direct failed: %s", exc)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not claim run: {exc}")
        return

    try:
        exec_ctx = store.run_execution_context(run_id=run_id)
    except Exception:  # noqa: BLE001
        exec_ctx = {"message": prompt, "history": []}

    await _stream_run(
        message=message,
        store=store,
        shared_state=shared_state,
        settings=settings,
        run_id=run_id,
        conversation_id=conversation_id,
        prompt=str(exec_ctx.get("message") or prompt),
        conversation_history=list(exec_ctx.get("history") or []),
        assistant_message_id=assistant_message_id,
        lease_token=lease_token,
    )


def _claim_direct(store: Any, *, run_id: str) -> tuple[str, str]:
    """Move a queued run to 'running'.  Mirrors ``chat._claim_direct``."""
    claim = getattr(store, "claim_run_direct", None)
    if callable(claim):
        return claim(run_id=run_id)
    raise RuntimeError("store does not support direct claim (needs MuninStore façade)")


def _finalize(
    store: Any,
    *,
    run_id: str,
    lease_token: str,
    content: str,
    outcome: str,
    conversation_id: str,
) -> None:
    try:
        store.complete_run(
            run_id=run_id, lease_token=lease_token, content=content, outcome=outcome,
        )
    except Exception:  # noqa: BLE001
        log.debug("discord: complete_run raised", exc_info=True)
    try:
        store.append_conversation_broadcast(
            conversation_id=conversation_id,
            kind="run-transition",
            payload={"run_id": run_id, "state": outcome},
        )
    except Exception:  # noqa: BLE001
        log.debug("discord: broadcast failed", exc_info=True)


async def _stream_run(
    *,
    message: Any,
    store: Any,
    shared_state: Any,
    settings: Any,
    run_id: str,
    conversation_id: str,
    prompt: str,
    conversation_history: list[dict],
    assistant_message_id: str,
    lease_token: str,
) -> None:
    from ..core.llm_client import LLMClient  # noqa: PLC0415
    from ..core.runtime_adapter import supervisor_runner  # noqa: PLC0415

    try:
        model = LLMClient(settings).make_langchain()
    except Exception as exc:  # noqa: BLE001
        log.warning("discord: model init failed: %s", exc)
        _finalize(
            store,
            run_id=run_id, lease_token=lease_token,
            content=f"Model unavailable: {exc}", outcome="failed",
            conversation_id=conversation_id,
        )
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] model unavailable: {exc}")
        return

    session = _RunSession(channel=message.channel, run_id=run_id)
    session._flush_task = asyncio.create_task(session.flush_loop())

    final_content = ""
    outcome = "completed"
    ok = True
    try:
        async for envelope in supervisor_runner(
            prompt,
            run_id=run_id,
            conversation_id=conversation_id,
            store=shared_state,
            model=model,
            conversation_history=conversation_history,
            thread_id=conversation_id or run_id,
        ):
            kind = envelope.get("kind")
            if kind == "reasoning":
                session.add_reasoning(str(envelope.get("text") or ""))
            elif kind == "tool_intent":
                session.add_tool_event(
                    f"- calling `{envelope.get('tool_name') or 'unknown'}`"
                )
            elif kind == "tool_result":
                out = str(envelope.get("output") or "")[:140]
                session.add_tool_event(
                    f"- ok `{envelope.get('tool_name') or 'unknown'}` — {out}"
                )
            elif kind == "tool_failed":
                err = str(envelope.get("error") or "")[:140]
                session.add_tool_event(
                    f"- failed `{envelope.get('tool_name') or 'unknown'}` — {err}"
                )
            elif kind == "run_state":
                state = str(envelope.get("state") or "")
                if state in {"completed", "failed", "cancelled", "interrupted"}:
                    outcome = state
                    ok = state == "completed"
                    final_content = str(envelope.get("content") or "") or "".join(session.reasoning)
                    break
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: supervisor_runner failed run_id=%s", run_id)
        outcome = "failed"
        ok = False
        final_content = f"Operation failed: {exc}"
    finally:
        if not final_content:
            final_content = "".join(session.reasoning) or "(no response)"
        _finalize(
            store,
            run_id=run_id, lease_token=lease_token,
            content=final_content or "(no response)", outcome=outcome,
            conversation_id=conversation_id,
        )
        await session.close(final_content=final_content or "(no response)", ok=ok)


def create_discord_task(
    settings: Any,
    store: Any,
    shared_state: Any,
) -> asyncio.Task | None:
    """Build and schedule the Discord adapter task.

    Returns ``None`` when ``settings.discord_bot_token`` is empty — this
    is the default and keeps existing deployments unaffected.  When the
    token is present we import ``discord.py`` lazily (so environments
    without the extra do not pay the import cost) and schedule a single
    ``client.start(token)`` coroutine on the running loop.
    """
    token = (getattr(settings, "discord_bot_token", "") or "").strip()
    if not token:
        log.info("discord: MUNIN_DISCORD_BOT_TOKEN unset — adapter disabled")
        return None

    try:
        import discord  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - dependency is declared
        log.warning("discord: discord.py unavailable (%s) — adapter disabled", exc)
        return None

    allowed_channels = _parse_id_list(getattr(settings, "discord_allowed_channels", "") or "")
    allowed_users = _parse_id_list(getattr(settings, "discord_allowed_user_ids", "") or "")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.dm_messages = True

    client = discord.Client(intents=intents)

    # Process-local state.  A restart wipes the conversation cache; that
    # is intentional — see module docstring.
    conversation_cache: dict[tuple[str, str], str] = {}

    @client.event
    async def on_ready() -> None:  # noqa: D401
        log.info(
            "discord: bot ready as %s (allowed_channels=%d allowed_users=%d)",
            getattr(client, "user", "?"),
            len(allowed_channels), len(allowed_users),
        )

    @client.event
    async def on_message(message: Any) -> None:  # noqa: D401
        bot_user = getattr(client, "user", None)
        bot_user_id = int(bot_user.id) if bot_user else None
        try:
            await _handle_message(
                message,
                settings=settings,
                store=store,
                shared_state=shared_state,
                conversation_cache=conversation_cache,
                bot_user_id=bot_user_id,
                allowed_channels=allowed_channels,
                allowed_users=allowed_users,
            )
        except Exception:  # noqa: BLE001
            log.exception("discord: on_message dispatch failed")

    async def _runner() -> None:
        try:
            await client.start(token)
        except asyncio.CancelledError:
            log.info("discord: shutdown requested, closing client")
            with contextlib.suppress(Exception):
                await client.close()
            raise
        except Exception:  # noqa: BLE001
            log.exception("discord: client crashed")

    task = asyncio.create_task(_runner(), name="munin-discord-adapter")
    log.info("discord: adapter task scheduled")
    return task
