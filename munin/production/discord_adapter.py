# tags: [coordination, runtime, core, subagent, hitl-approval, presence, shared-intel, discord.py, DiscordAdapter, on_message, _resolve_actor, DISCORD_FLUSH_INTERVAL, virtual-actor, rate-limiting, bot-integration, commands, /approve, /reject, /cancel, /status, /conversations, /history, /artifacts, /tools, /tool, memory-scoping, actor_id]
"""Discord chat + command adapter (community-channel redesign).

This module is the inbound Discord surface of the Munin runtime.  It runs
**in the same process** as :mod:`munin.server` (uvicorn ASGI): a single
``asyncio.Task`` runs ``discord.Client.start(token)`` and every
supervisor run streams directly on that event loop — no daemon thread,
no queue, no second process.

Session isolation
-----------------

* **DM** — each Discord user gets their own graph: the conversation is
  keyed by ``dm:{author_id}``.  Private chat is private.
* **Guild channel** — the channel is a *community* surface: ONE graph per
  channel, shared by every member who talks to the bot.  Everyone is
  resolved to their own virtual actor (``discord:{id}``) for the audit
  trail, but they all stream into the same conversation/thread, so the
  operation stays coherent and nothing leaks between channels.

Surface
-------

1. Natural language (DM, bot mention, ``/munin`` / ``!munin`` prefix):
   a full supervisor turn exactly like the web GUI path.
2. Slash commands for operator control:
   ``/help``, ``/approvals``, ``/approve <id>``, ``/reject <id>``,
   ``/cancel <run_id>``, ``/status``, ``/conversations``,
   ``/history [n]``, ``/artifacts [run_id]``, ``/artifact <id>``,
   ``/tools``, ``/tool <name> <json args>``.
3. Outbound: the agent itself can speak into the channel via the MCP
   ``send_discord_message`` tool — the publisher maps the active
   ``run_id`` to the channel it is streaming into
   (:mod:`munin.production.discord_publisher`).

Rendering policy
----------------

We deliberately do **not** shove the whole run into one message:

* A single *status* message is edited in place at most every
  ``DISCORD_FLUSH_INTERVAL`` (2.5 s) — live progress, tools tail.
* Reasoning and tool output are posted as **separate** short messages
  when they complete (spaced by ``DISCORD_POST_INTERVAL`` so we stay
  under the 5 msg / 5 s per-channel cap).
* The final assistant content is posted last, chunked at the 1900-char
  cap.
* HITL pauses post a dedicated approval card containing the durable
  ``request_id`` so the operator can act with ``/approve <id>``.

Per operator decision there is **no redaction** on the Discord surface
and no ``max_iterations`` cap for Discord-triggered runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from typing import Any

log = logging.getLogger("munin.production.discord_adapter")

# --- Rate-limit tunables ----------------------------------------------------
DISCORD_FLUSH_INTERVAL = 2.5       # status message edit cadence
DISCORD_POST_INTERVAL = 1.15       # min spacing between separate posts (5 msg/5s cap)
DISCORD_MAX_MESSAGE_CHARS = 1900   # 2000-char hard cap, headroom for markdown
DISCORD_STATUS_MAX_CHARS = 1800
DISCORD_TOOL_TAIL = 6              # last N tool events in the status message
DISCORD_TOOL_POST_CHARS = 900      # separate tool posts are kept short
DISCORD_REASONING_POST_CHARS = 1400

# --- Command surface --------------------------------------------------------
COMMAND_PREFIXES = ("/munin ", "!munin ")
COMMAND_NAMES = {
    "help", "approvals", "approve", "reject", "cancel", "status",
    "conversations", "history", "artifacts", "artifact", "tools", "tool",
}
HELP_TEXT = """**Munin Discord surface**
- Natural language: just talk (DM, mention, or `/munin` / `!munin` prefix)
- `/approvals` — list pending approvals with their `request_id`
- `/approve <request_id>` / `/reject <request_id>` — resolve a request
- `/cancel <run_id>` — cancel a run
- `/status` — state of the current conversation's runs
- `/conversations` — your conversations
- `/history [n]` — last n events of this session's graph
- `/artifacts [run_id]` — list artifacts; `/artifact <id>` — fetch one
- `/tools` — list runtime capabilities
- `/tool <name> <json-args>` — invoke a runtime tool and get raw output"""


def _parse_id_list(raw: str) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _resolve_actor(store: Any, *, discord_user_id: int, display_name: str) -> dict[str, Any]:
    """Return the Munin actor row for a Discord user, creating it lazily.

    The Discord bearer is the auth boundary: we mint ``discord:{id}``
    virtual actors with a strong random password so HTTP login for them
    is intentionally impossible while the audit trail stays honest.
    """
    username = f"discord:{discord_user_id}"

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

    password = secrets.token_urlsafe(48)
    user = store.create_user(username=username, password=password, role="operator")
    log.info("discord: created virtual actor username=%s id=%s", username, user["id"])
    return {**user, "display_name": display_name}


def _discover_conversation(
    store: Any, *, actor_id: str, channel_key: str
) -> str | None:
    """Find a durable conversation previously created for this channel_key.

    This makes the (DM|channel) → graph mapping survive a process restart:
    the scope JSON stores ``{"source": "discord", "channel_key": ...}`` so
    we can resurrect the same conversation instead of starting fresh.

    Note: Munin serialises JSON with ``separators=(",", ":")`` (no spaces),
    so the candidate filter parses ``scope_json`` and matches in Python
    rather than relying on a brittle SQL ``LIKE`` pattern.
    """
    durable = getattr(store, "_durable", None) or store
    try:
        with durable._read_only() as conn:  # noqa: SLF001 - documented probe
            rows = conn.execute(
                "SELECT id, scope_json FROM conversations"
                " WHERE scope_json LIKE ? AND deleted_at_ms IS NULL"
                " ORDER BY last_activity_at_ms DESC LIMIT 50",
                ('%"channel_key"%',),
            ).fetchall()
        for row in rows:
            try:
                scope = json.loads((row["scope_json"] if hasattr(row, "keys") else row[1]) or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(scope, dict) and scope.get("channel_key") == channel_key:
                return row["id"] if hasattr(row, "keys") else row[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("discord: conversation discovery failed for %s: %s", channel_key, exc)
    return None


def _get_or_create_conversation(
    store: Any,
    *,
    actor_id: str,
    channel_key: str,
    cache: dict[str, str],
    title: str,
    is_dm: bool,
) -> str:
    """Return the conversation id for a session key.

    Keying rules:
    * DM → ``dm:{author_id}`` (per-user graph).
    * Guild channel → ``channel:{channel_id}`` (ONE shared community graph).

    A cache miss first probes the durable store (restart resilience) and
    only then allocates a new conversation.  For shared channel graphs
    every new speaker is added as a participant so ``create_turn`` and
    approval resolution work for the whole channel.
    """
    conv_id = cache.get(channel_key)
    if conv_id:
        # Community channel: make sure this speaker is a participant too.
        if not is_dm:
            with contextlib.suppress(Exception):
                store.add_conversation_participant(
                    conversation_id=conv_id, user_id=actor_id, role="member"
                )
        return conv_id

    existing = _discover_conversation(store, actor_id=actor_id, channel_key=channel_key)
    if existing:
        cache[channel_key] = existing
        if not is_dm:
            with contextlib.suppress(Exception):
                store.add_conversation_participant(
                    conversation_id=existing, user_id=actor_id, role="member"
                )
        return existing

    conversation = store.create_conversation(
        owner_id=actor_id,
        title=title[:160] or "Discord conversation",
        tags=["discord"],
        scope={"source": "discord", "channel_key": channel_key},
    )
    cache[channel_key] = conversation["id"]
    return conversation["id"]


def _chunk_message(text: str, *, size: int = DISCORD_MAX_MESSAGE_CHARS) -> list[str]:
    text = text or ""
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


class _RateLimitedPoster:
    """Spaced sender so separate posts respect the 5 msg / 5 s channel cap."""

    def __init__(self, channel: Any, *, interval: float = DISCORD_POST_INTERVAL) -> None:
        self.channel = channel
        self.interval = interval
        self._last = 0.0

    async def post(self, content: str) -> None:
        now = time.monotonic()
        wait = self._last + self.interval - now
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()
        for chunk in _chunk_message(content):
            with contextlib.suppress(Exception):
                await self.channel.send(chunk)


class _RunSession:
    """State for a single in-flight Discord-triggered run.

    Renders the live run as: one editable status message (progress +
    tools tail), separate short posts for completed reasoning/tool
    blocks, a dedicated HITL approval card, and a final assistant
    message.  Nothing is concatenated into a single megapost.
    """

    def __init__(self, *, channel: Any, run_id: str) -> None:
        self.channel = channel
        self.run_id = run_id
        self.reasoning_buffer = ""
        self.tools: list[str] = []
        self.status_message: Any = None
        self.last_request_id: str | None = None
        self._dirty = False
        self._closed = False
        self._flush_task: asyncio.Task | None = None
        self._poster = _RateLimitedPoster(channel)
        self._final_consumed = False  # run_state provided canonical final text

    def add_reasoning(self, text: str) -> None:
        if text:
            self.reasoning_buffer += text
            self._dirty = True
            # Long reasoning is posted separately so the status message
            # stays small; keep the tail in the status buffer.
            if len(self.reasoning_buffer) >= DISCORD_REASONING_POST_CHARS:
                self._post_reasoning_block()

    def _post_reasoning_block(self) -> None:
        block = self.reasoning_buffer[: DISCORD_REASONING_POST_CHARS]
        if not block.strip():
            return
        self.reasoning_buffer = self.reasoning_buffer[len(block):]
        asyncio.create_task(self._poster.post(f"💭 {block.strip()}"))

    def add_tool_event(self, line: str) -> None:
        if line:
            self.tools.append(line)
            self._dirty = True
            # Post each completed tool as its own short message — the
            # operator sees the operation as it happens, not buried.
            asyncio.create_task(self._poster.post(f"🔧 {line[:DISCORD_TOOL_POST_CHARS]}"))

    def _render_status(self) -> str:
        body = self.reasoning_buffer.strip()
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
        content = self._render_status()
        try:
            if self.status_message is None:
                self.status_message = await self.channel.send(content or "_working..._")
            else:
                await self.status_message.edit(content=content or "_working..._")
        except Exception as exc:  # noqa: BLE001
            log.debug("discord: flush failed run_id=%s: %s", self.run_id, exc)

    async def post_approval_card(self, request_id: str, action: str, risk: str) -> None:
        """Dedicated, visible HITL card with the durable request id."""
        self.last_request_id = request_id
        await self._poster.post(
            f"⚠️ **Approval required** — `{request_id}`\n"
            f"Action: {action}\nRisk: {risk}\n\n"
            f"Reply `/approve {request_id}` or `/reject {request_id}`. "
            f"(Admins can resolve any pending request; expiry is enforced server-side.)"
        )

    async def close(self, *, final_content: str, ok: bool, paused: bool = False) -> None:
        self._closed = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(BaseException):
                await self._flush_task
        if paused:
            with contextlib.suppress(Exception):
                await self._flush()
            return
        prefix = "[completed]" if ok else "[failed]"
        rendered = f"{prefix} {final_content}".rstrip()
        # Reuse the live status message if we have one: edit it to the
        # final text so the operator sees ONE message in the channel
        # (the running status -> final answer), not two. If the final
        # text exceeds the Discord edit body limit (~2000 chars) or we
        # never created a status message, fall back to posting new
        # chunks. The reasoning buffer is already captured in
        # ``final_content`` when ``_final_consumed`` is set, so we drop
        # it to avoid duplicating it in the edited body.
        if self._final_consumed:
            self.reasoning_buffer = ""
            self._dirty = True
        if self.status_message is not None and len(rendered) <= DISCORD_MAX_MESSAGE_CHARS:
            with contextlib.suppress(Exception):
                await self.status_message.edit(content=rendered)
                if self.tools:
                    # Edit keeps the prefix; append a compact tools tail.
                    tail = "\n\n**Tools**\n" + "\n".join(self.tools[-DISCORD_TOOL_TAIL:])
                    if len(rendered) + len(tail) <= DISCORD_MAX_MESSAGE_CHARS:
                        await self.status_message.edit(content=rendered + tail)
                return
        # Fallback: post the final text as new chunk(s).
        for chunk in _chunk_message(rendered):
            with contextlib.suppress(Exception):
                await self.channel.send(chunk)


def _extract_prompt(message: Any, *, bot_user_id: int | None) -> str | None:
    """Return the trimmed prompt text, or ``None`` if the message should be ignored.

    Rules:
    * DMs → whole content (including slash commands).
    * Guild channel → require a mention of the bot, a reply to the bot,
      or one of the ``COMMAND_PREFIXES``.  This keeps the bot out of
      unrelated channel chatter while letting the whole community talk
      to it deliberately.
    """
    content = (message.content or "").strip()
    if not content:
        return None

    is_dm = getattr(message, "guild", None) is None
    if is_dm:
        return content

    # Reply-to-bot counts as an invocation (community convenience).
    reference = getattr(message, "reference", None)
    if reference is not None:
        resolved = getattr(reference, "resolved", None)
        if resolved is not None and getattr(resolved, "author", None) is not None:
            if int(getattr(resolved.author, "id", 0) or 0) == int(bot_user_id or 0):
                return content

    if bot_user_id is not None:
        for tag in (f"<@{bot_user_id}>", f"<@!{bot_user_id}>"):
            if tag in content:
                return content.replace(tag, "", 1).strip()

    for prefix in COMMAND_PREFIXES:
        if content.startswith(prefix):
            return content[len(prefix):].strip()

    return None


def _parse_command(content: str) -> tuple[str, list[str]] | None:
    """Split ``/name arg1 arg2``; returns (name, args) or None for chat."""
    stripped = content.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped[1:].split()
    if not parts:
        return None
    name = parts[0].lower()
    if name not in COMMAND_NAMES:
        return None
    return name, parts[1:]


async def _cmd_approvals(*, store: Any, actor: dict[str, Any], message: Any) -> None:
    pending = store.list_pending_human_requests(actor_id=actor["id"], limit=20)
    if not pending:
        with contextlib.suppress(Exception):
            await message.reply("No pending approvals.")
        return
    lines = [f"**{len(pending)} pending approval(s)**"]
    for req in pending:
        lines.append(f"- `{req['id']}` · {req['action']} · risk={req['risk']}")
    lines.append("Resolve with `/approve <request_id>` or `/reject <request_id>`.")
    for chunk in _chunk_message("\n".join(lines)):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_resolve(
    *,
    store: Any,
    actor: dict[str, Any],
    message: Any,
    request_id: str,
    choice: str,
) -> None:
    request_id = request_id.strip()
    if not request_id:
        with contextlib.suppress(Exception):
            await message.reply(f"Usage: `/{'approve' if choice == 'approve' else 'reject'} <request_id>`")
        return
    try:
        nonce = store.reissue_human_decision_nonce(
            actor_id=actor["id"], request_id=request_id
        )["nonce"]
        result = store.resolve_human_decision(
            actor_id=actor["id"],
            request_id=request_id,
            choice=choice,
            nonce=nonce,
        )
    except PermissionError as exc:
        with contextlib.suppress(Exception):
            await message.reply(f"[denied] {exc}")
        return
    except KeyError:
        with contextlib.suppress(Exception):
            await message.reply(f"[not_found] no human request `{request_id}`")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: %s failed request_id=%s", choice, request_id)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not {choice} `{request_id}`: {exc}")
        return

    state = result.get("state")
    if state == "queued":
        with contextlib.suppress(Exception):
            await message.reply(f"✅ Approved `{request_id}` — resuming run {result.get('run_id')}")
        # Resume the checkpointed graph exactly like the web path.
        await _resume_approved_run(
            store=store,
            message=message,
            run_id=str(result["run_id"]),
            decision_count=int(result.get("decision_count") or 1),
        )
    else:
        with contextlib.suppress(Exception):
            await message.reply(f"`{request_id}` → {state} ({choice})")


async def _resume_approved_run(
    *, store: Any, message: Any, run_id: str, decision_count: int
) -> None:
    """Claim and resume a run whose HITL request was just approved."""
    try:
        run = store.get_run(run_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("discord: resume lookup failed run_id=%s: %s", run_id, exc)
        return
    conversation_id = str(run.get("conversation_id") or "")
    try:
        lease_token, assistant_message_id = _claim_direct(store, run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("discord: resume claim failed run_id=%s: %s", run_id, exc)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not claim run for resume: {exc}")
        return
    # Inject a continuation directive so the model knows to proceed with
    # the approved tool work instead of hallucinating "standing by".
    # OperatorGuidanceMiddleware drains this before the first post-resume
    # model call and delivers it as HumanMessage(name="operator").
    with contextlib.suppress(Exception):
        store.enqueue_guidance(
            run_id=run_id,
            actor_id=str(getattr(message.author, "id", "") or ""),
            actor_username=str(getattr(message.author, "name", "operator")),
            body=(
                "Operator approved the pending tool execution. "
                "Resume the approved action, incorporate its result, "
                "and continue the workflow toward the original objective."
            ),
        )
    await _stream_run(
        message=message,
        store=store,
        shared_state=None,
        settings=None,
        run_id=run_id,
        conversation_id=conversation_id,
        prompt="",
        conversation_history=[],
        assistant_message_id=assistant_message_id,
        lease_token=lease_token,
        resume_decisions=[{"type": "approve"}] * max(1, decision_count),
    )


async def _cmd_cancel(*, store: Any, actor: dict[str, Any], message: Any, run_id: str) -> None:
    run_id = run_id.strip()
    if not run_id:
        with contextlib.suppress(Exception):
            await message.reply("Usage: `/cancel <run_id>`")
        return
    try:
        result = store.request_run_cancellation(actor_id=actor["id"], run_id=run_id)
    except PermissionError as exc:
        with contextlib.suppress(Exception):
            await message.reply(f"[denied] {exc}")
        return
    except KeyError:
        with contextlib.suppress(Exception):
            await message.reply(f"[not_found] no run `{run_id}`")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: cancel failed run_id=%s", run_id)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not cancel `{run_id}`: {exc}")
        return
    state = result.get("state") or "cancelled"
    with contextlib.suppress(Exception):
        await message.reply(f"`{run_id}` → {state}")


async def _cmd_status(*, store: Any, actor: dict[str, Any], message: Any) -> None:
    convs = store.list_conversations(actor_id=actor["id"], limit=5)
    convs = convs.get("conversations") or []
    if not convs:
        with contextlib.suppress(Exception):
            await message.reply("No conversations yet. Talk to the bot to start one.")
        return
    lines = ["**Recent runs**"]
    for conv in convs:
        try:
            detail = store.get_conversation(actor_id=actor["id"], conversation_id=conv["id"])
        except Exception:  # noqa: BLE001
            continue
        runs = detail.get("runs") or []
        for run in runs[-3:]:
            lines.append(
                f"- `{run.get('id')}` · {run.get('state')} · {run.get('mode') or ''}"
            )
    for chunk in _chunk_message("\n".join(lines)):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_conversations(*, store: Any, actor: dict[str, Any], message: Any) -> None:
    convs = store.list_conversations(actor_id=actor["id"], limit=10)
    convs = convs.get("conversations") or []
    if not convs:
        with contextlib.suppress(Exception):
            await message.reply("No conversations yet.")
        return
    lines = [f"**{len(convs)} conversation(s)**"]
    for conv in convs:
        lines.append(
            f"- `{conv['id']}` · {conv['title'][:60]} · msgs={conv.get('message_count', '?')}"
        )
    for chunk in _chunk_message("\n".join(lines)):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_history(*, store: Any, actor: dict[str, Any], message: Any, count: str) -> None:
    limit = 10
    if count.strip().isdigit():
        limit = max(1, min(int(count), 50))
    convs = store.list_conversations(actor_id=actor["id"], limit=1)
    convs = convs.get("conversations") or []
    if not convs:
        with contextlib.suppress(Exception):
            await message.reply("No conversation yet in this session.")
        return
    conv_id = convs[0]["id"]
    detail = store.get_conversation(actor_id=actor["id"], conversation_id=conv_id)
    runs = detail.get("runs") or []
    if not runs:
        with contextlib.suppress(Exception):
            await message.reply("No runs yet in this session.")
        return
    run_id = str(runs[-1]["id"])
    try:
        events = store.run_events_after(run_id=run_id, after_sequence=0)
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not read history: {exc}")
        return
    lines = [f"**Graph events for `{run_id}`**"]
    for event in events[-limit:]:
        kind = event.get("kind") or "?"
        payload = event.get("payload") or {}
        text = str(payload.get("message") or payload.get("content") or payload.get("state") or "")
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(f"- `{kind}` {text}")
    for chunk in _chunk_message("\n".join(lines)):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_artifacts(*, store: Any, actor: dict[str, Any], message: Any, run_id: str) -> None:
    run_id = run_id.strip()
    if not run_id:
        convs = store.list_conversations(actor_id=actor["id"], limit=1)
        convs = convs.get("conversations") or []
        if not convs:
            with contextlib.suppress(Exception):
                await message.reply("No conversation yet.")
            return
        detail = store.get_conversation(actor_id=actor["id"], conversation_id=convs[0]["id"])
        runs = detail.get("runs") or []
        if not runs:
            with contextlib.suppress(Exception):
                await message.reply("No runs yet.")
            return
        run_id = str(runs[-1]["id"])
    try:
        detail = store.get_run_detail_for_actor(actor_id=actor["id"], run_id=run_id)
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not read run: {exc}")
        return
    artifacts = detail.get("artifacts") or []
    if not artifacts:
        with contextlib.suppress(Exception):
            await message.reply(f"No artifacts for `{run_id}`.")
        return
    lines = [f"**Artifacts for `{run_id}`**"]
    for art in artifacts:
        lines.append(f"- `{art['id']}` · {art.get('filename') or '?'} · {art.get('media_type') or '?'}")
    lines.append("Fetch with `/artifact <artifact_id>`.")
    for chunk in _chunk_message("\n".join(lines)):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_artifact(*, store: Any, actor: dict[str, Any], message: Any, artifact_id: str) -> None:
    artifact_id = artifact_id.strip()
    if not artifact_id:
        with contextlib.suppress(Exception):
            await message.reply("Usage: `/artifact <artifact_id>`")
        return
    try:
        artifact = store.get_artifact(actor_id=actor["id"], artifact_id=artifact_id)
    except PermissionError as exc:
        with contextlib.suppress(Exception):
            await message.reply(f"[denied] {exc}")
        return
    except KeyError:
        with contextlib.suppress(Exception):
            await message.reply(f"[not_found] no artifact `{artifact_id}`")
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: artifact fetch failed %s", artifact_id)
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not fetch artifact: {exc}")
        return
    try:
        content = json.dumps(artifact, default=str)
    except Exception:  # noqa: BLE001
        content = str(artifact)
    for chunk in _chunk_message(f"**Artifact `{artifact_id}`**\n{content}"):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_tools(*, shared_state: Any, message: Any) -> None:
    from ..core.tool_gateway import catalog_names  # noqa: PLC0415
    try:
        names = sorted(catalog_names(shared_state))
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not list tools: {exc}")
        return
    lines = [f"**{len(names)} runtime capability(-ies)**"]
    lines.append(", ".join(f"`{name}`" for name in names))
    lines.append("Invoke with `/tool <name> <json-args>`.")
    for chunk in _chunk_message("\n".join(lines)):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _cmd_tool(*, shared_state: Any, message: Any, actor: dict[str, Any], name: str, args_raw: str) -> None:
    """Invoke a runtime tool directly and return the raw output.

    SECURITY: this bypasses the supervisor graph (and thus the approval
    interrupts / OPSEC pre-post-flight / run-id audit binding) — it is a
    deliberately server-side-restricted operator shortcut. Only actors
    whose server-side role is ``admin`` may use it; Discord-resolved
    virtual actors default to ``operator`` so this is *denied* unless a
    human operator promotes the account in the store.
    """
    if str(actor.get("role") or "") != "admin":
        with contextlib.suppress(Exception):
            await message.reply(
                "[denied] `/tool` is an admin-only shortcut that bypasses the "
                "supervisor. Ask the bot in natural language instead, or have "
                "an operator promote your account."
            )
        return
    name = name.strip()
    if not name:
        with contextlib.suppress(Exception):
            await message.reply("Usage: `/tool <name> <json-args>`")
        return
    from ..core.tool_gateway import gateway_tools  # noqa: PLC0415
    try:
        tools = gateway_tools(shared_state, allowed={name})
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await message.reply(f"[failed] could not build tool `{name}`: {exc}")
        return
    if not tools:
        with contextlib.suppress(Exception):
            await message.reply(f"[not_found] unknown tool `{name}`")
        return
    tool = tools[0]
    args: dict[str, Any] = {}
    if args_raw.strip():
        try:
            parsed = json.loads(args_raw)
            if isinstance(parsed, dict):
                args = parsed
            else:
                with contextlib.suppress(Exception):
                    await message.reply("[invalid_args] JSON args must be an object")
                return
        except json.JSONDecodeError as exc:
            with contextlib.suppress(Exception):
                await message.reply(f"[invalid_args] could not parse JSON: {exc}")
            return
    try:
        if asyncio.iscoroutinefunction(tool.coroutine):
            result = await tool.coroutine(**args)
        else:
            result = tool.func(**args)
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: /tool %s failed", name)
        with contextlib.suppress(Exception):
            await message.reply(f"❌ `{name}` failed: {exc}")
        return
    try:
        raw = json.dumps(result, default=str)
    except Exception:  # noqa: BLE001
        raw = str(result)
    for chunk in _chunk_message(f"**`{name}` raw output**\n{raw}"):
        with contextlib.suppress(Exception):
            await message.channel.send(chunk)


async def _handle_command(
    message: Any,
    *,
    store: Any,
    shared_state: Any,
    actor: dict[str, Any],
    content: str,
) -> None:
    parsed = _parse_command(content)
    if parsed is None:
        return
    name, args = parsed

    if name == "help":
        with contextlib.suppress(Exception):
            await message.reply(HELP_TEXT)
        return
    if name == "approvals":
        await _cmd_approvals(store=store, actor=actor, message=message)
        return
    if name == "approve":
        await _cmd_resolve(store=store, actor=actor, message=message, request_id=args[0] if args else "", choice="approve")
        return
    if name == "reject":
        await _cmd_resolve(store=store, actor=actor, message=message, request_id=args[0] if args else "", choice="reject")
        return
    if name == "cancel":
        await _cmd_cancel(store=store, actor=actor, message=message, run_id=args[0] if args else "")
        return
    if name == "status":
        await _cmd_status(store=store, actor=actor, message=message)
        return
    if name == "conversations":
        await _cmd_conversations(store=store, actor=actor, message=message)
        return
    if name == "history":
        await _cmd_history(store=store, actor=actor, message=message, count=args[0] if args else "10")
        return
    if name == "artifacts":
        await _cmd_artifacts(store=store, actor=actor, message=message, run_id=args[0] if args else "")
        return
    if name == "artifact":
        await _cmd_artifact(store=store, actor=actor, message=message, artifact_id=args[0] if args else "")
        return
    if name == "tools":
        await _cmd_tools(shared_state=shared_state, message=message)
        return
    if name == "tool":
        await _cmd_tool(shared_state=shared_state, message=message, actor=actor, name=args[0] if args else "", args_raw=" ".join(args[1:]))
        return


async def _handle_message(
    message: Any,
    *,
    settings: Any,
    store: Any,
    shared_state: Any,
    conversation_cache: dict[str, str],
    bot_user_id: int | None,
    allowed_channels: set[str],
    allowed_users: set[str],
    publisher: Any,
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

    is_dm = getattr(message, "guild", None) is None
    channel_key = f"dm:{author_id}" if is_dm else f"channel:{channel_id}"

    # Slash commands are handled without creating a graph turn.
    if _parse_command(prompt) is not None:
        await _handle_command(
            message, store=store, shared_state=shared_state, actor=actor, content=prompt,
        )
        return

    try:
        conversation_id = _get_or_create_conversation(
            store,
            actor_id=actor["id"],
            channel_key=channel_key,
            cache=conversation_cache,
            title=f"discord:{message.author}",
            is_dm=is_dm,
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
    publisher.map_run(run_id=run_id, channel_id=channel_id)

    # Idempotent replay (Discord retry / duplicate message id): don't
    # re-run, just acknowledge.
    if turn.get("idempotent_replay"):
        with contextlib.suppress(Exception):
            await message.reply(f"[replay] run {run_id} already exists")
        return

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

    try:
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
            actor_id=str(actor["id"]),
        )
    finally:
        publisher.unmap_run(run_id=run_id)


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
    resume_decisions: list[dict[str, Any]] | None = None,
    actor_id: str = "",
) -> None:
    from ..core.llm_client import LLMClient  # noqa: PLC0415
    from ..core.runtime_adapter import supervisor_runner  # noqa: PLC0415

    if settings is None:
        # Resume path: borrow the durable settings through the shared state.
        settings = getattr(shared_state, "settings", None) if shared_state is not None else None
    if settings is None:
        from ..mcp.config import get_settings  # noqa: PLC0415
        settings = get_settings()

    # The resume path (approved HITL) has no shared_state; build the same
    # composition chat.py uses so guidance/tool-authorization still resolve.
    if shared_state is None:
        from ..mcp.shared_state import SharedStateStore  # noqa: PLC0415
        shared_state = SharedStateStore(settings)
        with contextlib.suppress(Exception):
            shared_state.consume_pending_guidance = store.consume_pending_guidance  # type: ignore[assignment]
        with contextlib.suppress(Exception):
            shared_state.authorize_approved_tool_call = store.authorize_approved_tool_call  # type: ignore[assignment]

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
    paused_for_human = False
    lease_lost: asyncio.Event | None = None
    lease_heartbeat: asyncio.Task[None] | None = None
    lease_heartbeat_stop: asyncio.Event | None = None
    # Mirror the web path (chat._renew_chat_lease): keep the lease alive
    # while we stream so chat_recovery_loop cannot fence+double-stream a
    # long-running turn. Falls back to "no heartbeat" only when the store
    # facade cannot renew leases (e.g. unit-test fakes).
    renew = getattr(store, "renew_run_lease", None)
    if callable(renew):
        try:
            from .chat import _renew_chat_lease  # noqa: PLC0415
            lease_heartbeat_stop = asyncio.Event()
            lease_lost = asyncio.Event()
            lease_heartbeat = asyncio.create_task(
                _renew_chat_lease(
                    store=store, run_id=run_id, lease_token=lease_token,
                    stop=lease_heartbeat_stop, lease_lost=lease_lost,
                ),
                name=f"munin-discord-lease-{run_id}",
            )
        except Exception:  # noqa: BLE001 - heartbeat is best-effort, not mandatory
            lease_heartbeat = None
            lease_lost = None
    # Register with chat._ACTIVE_RUN_TASKS so recover_persisted_chat_runs'
    # idempotency guard sees this in-flight run and does not relaunch it.
    try:
        from . import chat as _chat_mod  # noqa: PLC0415
        _chat_mod._ACTIVE_RUN_TASKS[run_id] = asyncio.current_task()  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass

    runner_stream = supervisor_runner(
        prompt,
        run_id=run_id,
        conversation_id=conversation_id,
        store=shared_state,
        model=model,
        conversation_history=conversation_history,
        thread_id=conversation_id or run_id,
        human_request_store=store,
        resume_decisions=resume_decisions,
        actor_id=actor_id,
    )
    try:
        async for envelope in runner_stream:
            if lease_lost is not None and lease_lost.is_set():
                # Another executor now owns the run; stop streaming.
                outcome = "lease_lost"
                ok = False
                paused_for_human = False
                break
            kind = envelope.get("kind")
            if kind == "assistant_text":
                session.add_reasoning(str(envelope.get("text") or ""))
            elif kind == "human_request":
                paused_for_human = True
                outcome = "waiting_for_human"
                ok = False
                request_id = str(envelope.get("request_id") or "")
                action = str(envelope.get("tool_name") or "tool execution")
                risk = str(envelope.get("risk") or "high")
                await session.post_approval_card(
                    request_id=request_id, action=action, risk=risk,
                )
                break
            elif kind == "tool_intent":
                session.add_tool_event(
                    f"calling `{envelope.get('tool_name') or 'unknown'}`"
                )
            elif kind == "tool_result":
                out = str(envelope.get("output") or "")[:140]
                session.add_tool_event(
                    f"ok `{envelope.get('tool_name') or 'unknown'}` — {out}"
                )
            elif kind == "tool_failed":
                err = str(envelope.get("error") or "")[:140]
                session.add_tool_event(
                    f"failed `{envelope.get('tool_name') or 'unknown'}` — {err}"
                )
            elif kind == "run_state":
                state = str(envelope.get("state") or "")
                if state in {"completed", "failed", "cancelled", "interrupted"}:
                    outcome = state
                    ok = state == "completed"
                    final_content = str(envelope.get("content") or "") or session.reasoning_buffer
                    # The supervisor's final content already contains the
                    # complete assistant answer; mark it so close() does
                    # not duplicate it alongside the reasoning buffer.
                    if envelope.get("content"):
                        session._final_consumed = True
                    break
    except Exception as exc:  # noqa: BLE001
        log.exception("discord: supervisor_runner failed run_id=%s", run_id)
        outcome = "failed"
        ok = False
        final_content = f"Operation failed: {exc}"
    finally:
        # Close the generator explicitly so supervisor_runner's finally
        # resets its ContextVars on THIS task, not on a deferred GC finaliser.
        with contextlib.suppress(Exception):
            await runner_stream.aclose()
        if lease_heartbeat_stop is not None:
            lease_heartbeat_stop.set()
        if lease_heartbeat is not None:
            lease_heartbeat.cancel()
            with contextlib.suppress(BaseException):
                await lease_heartbeat
        try:
            from . import chat as _chat_mod2  # noqa: PLC0415
            if _chat_mod2._ACTIVE_RUN_TASKS.get(run_id) is asyncio.current_task():
                _chat_mod2._ACTIVE_RUN_TASKS.pop(run_id, None)
        except Exception:  # noqa: BLE001
            pass
        if not final_content:
            final_content = session.reasoning_buffer or "(no response)"
        if not paused_for_human and outcome != "lease_lost":
            _finalize(
                store,
                run_id=run_id, lease_token=lease_token,
                content=final_content or "(no response)", outcome=outcome,
                conversation_id=conversation_id,
            )
        await session.close(
            final_content=final_content or "(no response)",
            ok=ok,
            paused=paused_for_human,
        )


def create_discord_task(
    settings: Any,
    store: Any,
    shared_state: Any,
    publisher: Any = None,
) -> asyncio.Task | None:
    """Build and schedule the Discord adapter task.

    Returns ``None`` when ``settings.discord_bot_token`` is empty — this
    is the default and keeps existing deployments unaffected.  When the
    token is present we import ``discord.py`` lazily and schedule a
    single ``client.start(token)`` coroutine on the running loop.
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

    if publisher is None:
        from .discord_publisher import PUBLISHER as publisher  # noqa: PLC0415

    allowed_channels = _parse_id_list(getattr(settings, "discord_allowed_channels", "") or "")
    allowed_users = _parse_id_list(getattr(settings, "discord_allowed_user_ids", "") or "")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    intents.dm_messages = True

    client = discord.Client(intents=intents)

    # Process-local accelerator for the (dm|channel) → conversation map.
    # Durable discovery on miss keeps the graph stable across restarts.
    conversation_cache: dict[str, str] = {}

    @client.event
    async def on_ready() -> None:  # noqa: D401
        publisher.attach(
            loop=asyncio.get_running_loop(),
            client=client,
            default_channel_id=next(iter(allowed_channels), None),
        )
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
                publisher=publisher,
            )
        except Exception:  # noqa: BLE001
            log.exception("discord: on_message dispatch failed")

    async def _runner() -> None:
        try:
            await client.start(token)
        except asyncio.CancelledError:
            log.info("discord: shutdown requested, closing client")
            publisher.detach()
            with contextlib.suppress(Exception):
                await client.close()
            raise
        except Exception:  # noqa: BLE001
            log.exception("discord: client crashed")
            publisher.detach()

    task = asyncio.create_task(_runner(), name="munin-discord-adapter")
    log.info("discord: adapter task scheduled")
    return task
