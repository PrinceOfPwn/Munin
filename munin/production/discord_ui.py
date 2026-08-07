# tags: [coordination, runtime, discord, ui-component, discord-ui, embeds, views, buttons, threads, hitl-approval, run-control, evidence-card, art-direction]
"""Discord UI components for the Munin community adapter.

Reusable ``discord.ui.View`` subclasses, ``discord.Embed`` builders, and
thread helpers that give the operator a structured, geek, actionable
surface — replacing the old plain-text status messages with embeds,
buttons and per-run threads.

Design principles (from the Munin art direction):
- Dark-first, accent violet #7c3aed for primary actions only.
- Semantics: success=green, danger=red, warning=amber, info=cyan.
- Embeds describe *state*, buttons describe *actions*.
- One investigation = one thread; the main channel gets compact summaries.
- HITL buttons always go through the server-side authority boundary
  (``store.reissue_human_decision_nonce`` + ``resolve_human_decision``);
  the button is a surface, not a bypass.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    import discord  # noqa: F401

log = logging.getLogger("munin.discord_ui")

# Discord colour palette — mapped to Munin semantic tokens.
# These are discord.Colour instances, not raw hex, so they integrate
# with the Embed API cleanly.
try:
    import discord

    COLOR_ACCENT = discord.Colour(0x7C3AED)       # violet — primary action
    COLOR_SUCCESS = discord.Colour(0x10B981)      # green — completed
    COLOR_DANGER = discord.Colour(0xF43F5E)        # red — failed / reject
    COLOR_WARNING = discord.Colour(0xF59E0B)       # amber — HITL / caution
    COLOR_INFO = discord.Colour(0x38BDF8)           # cyan — telemetry
    COLOR_DARK = discord.Colour(0x1E1E2E)           # surface — neutral
    _DISCORD_AVAILABLE = True
except ImportError:  # pragma: no cover - discord.py is a runtime dep
    COLOR_ACCENT = COLOR_SUCCESS = COLOR_DANGER = COLOR_WARNING = COLOR_INFO = COLOR_DARK = None
    _DISCORD_AVAILABLE = False

# Run status → emoji prefix for compact channel lines.
STATUS_EMOJI = {
    "running": "🔄",
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
    "waiting_for_human": "⚠️",
    "queued": "⏳",
}

# ----------------------------------------------------------------------------
# Embed builders
# ----------------------------------------------------------------------------


def build_run_status_embed(
    *,
    run_id: str,
    state: str,
    reasoning_summary: str = "",
    tools: list[str] | None = None,
    prompt: str = "",
    conversation_id: str = "",
) -> Any:
    """Build a ``discord.Embed`` for the live run status message.

    This replaces the old ``_render_status()`` plain-text output with a
    structured embed that shows:
    - Title: run state + run_id with the Munin raven emoji.
    - Description: the operator's original objective (truncated).
    - Fields: current reasoning, recent tool activity.
    - Footer: conversation_id + timestamp.
    """
    if not _DISCORD_AVAILABLE:
        return None
    emoji = STATUS_EMOJI.get(state, "🔄")
    color = {
        "running": COLOR_ACCENT,
        "completed": COLOR_SUCCESS,
        "failed": COLOR_DANGER,
        "cancelled": COLOR_DANGER,
        "waiting_for_human": COLOR_WARNING,
        "queued": COLOR_INFO,
    }.get(state, COLOR_DARK)

    title = f"{emoji} Munin · {state.replace('_', ' ').title()}"
    embed = discord.Embed(title=title, color=color or COLOR_DARK)
    embed.set_footer(text=f"run {run_id[:16]}  ·  conv {conversation_id[:16]}")

    if prompt:
        objective = prompt[:400] if len(prompt) > 400 else prompt
        embed.description = f"**Objective**\n```\n{objective}\n```"

    if reasoning_summary:
        # Truncate to stay within embed field limits (1024 chars).
        truncated = reasoning_summary[:900]
        if len(reasoning_summary) > 900:
            truncated = truncated[:897] + "..."
        embed.add_field(
            name="💭 Reasoning",
            value=f">>> {truncated}",
            inline=False,
        )

    if tools:
        tail = tools[-6:]
        embed.add_field(
            name="⚡ Activity",
            value="\n".join(tail) if tail else "_idle_",
            inline=False,
        )

    return embed


def build_approval_embed(
    *,
    request_id: str,
    action: str,
    risk: str,
    run_id: str = "",
    evidence: list[str] | None = None,
) -> Any:
    """Build a ``discord.Embed`` for the HITL approval card.

    The card is the primary HITL surface. It carries the durable
    ``request_id`` and is accompanied by an ``ApprovalView`` with
    buttons that resolve through the server-side authority boundary.
    """
    if not _DISCORD_AVAILABLE:
        return None
    risk_color = {
        "critical": COLOR_DANGER,
        "high": COLOR_DANGER,
        "medium": COLOR_WARNING,
        "low": COLOR_INFO,
    }.get(risk, COLOR_WARNING)

    embed = discord.Embed(
        title="⚠️ Approval Required",
        color=risk_color or COLOR_WARNING,
    )
    embed.description = (
        f"**Action:** `{action}`\n"
        f"**Risk:** `{risk}`\n"
        f"**Request ID:** `{request_id}`"
    )
    if run_id:
        embed.set_footer(text=f"run {run_id[:16]}")

    if evidence:
        ev_text = "\n".join(f"• {e[:200]}" for e in evidence[:5])
        if len(ev_text) > 900:
            ev_text = ev_text[:897] + "..."
        embed.add_field(
            name="📋 Evidence",
            value=ev_text,
            inline=False,
        )

    return embed


def build_completion_embed(
    *,
    run_id: str,
    outcome: str,
    content: str,
    tools_used: list[str] | None = None,
    conversation_id: str = "",
) -> Any:
    """Build a ``discord.Embed`` for the final run result.

    This is what ``close()`` posts/edits as the final message — replacing
    the old ``[completed] plain_text`` format with a structured embed
    that carries the outcome, the content, and a tools summary.
    """
    if not _DISCORD_AVAILABLE:
        return None
    emoji = STATUS_EMOJI.get(outcome, "❌")
    color = {
        "completed": COLOR_SUCCESS,
        "failed": COLOR_DANGER,
        "cancelled": COLOR_DANGER,
        "interrupted": COLOR_WARNING,
        "lease_lost": COLOR_DANGER,
    }.get(outcome, COLOR_DANGER)

    embed = discord.Embed(
        title=f"{emoji} Munin · {outcome.title()}",
        color=color or COLOR_DANGER,
    )
    embed.set_footer(text=f"run {run_id[:16]}  ·  conv {conversation_id[:16]}")

    # The main content — chunked if too long for a single embed field.
    if content:
        # Embed description max is 4096; leave headroom for the title/footer.
        body = content[:4000] if len(content) > 4000 else content
        embed.description = body

    if tools_used:
        tools_summary = ", ".join(f"`{t}`" for t in tools_used[-10:])
        embed.add_field(
            name="🛠️ Tools",
            value=tools_summary[:1000],
            inline=False,
        )

    return embed


def build_error_embed(
    *,
    run_id: str,
    error: str,
    recoverable: bool = False,
    suggestion: str = "",
) -> Any:
    """Build a ``discord.Embed`` for honest, recoverable error messages.

    The error embed explains WHAT failed, WHY (if known), and gives the
    operator actionable options instead of just "Ocurrió un error."
    """
    if not _DISCORD_AVAILABLE:
        return None
    embed = discord.Embed(
        title="❌ Error" if not recoverable else "⚠️ Recoverable Error",
        color=COLOR_DANGER if not recoverable else COLOR_WARNING,
    )
    embed.description = f"```\n{error[:1500]}\n```"
    if run_id:
        embed.set_footer(text=f"run {run_id[:16]}")
    if suggestion:
        embed.add_field(
            name="💡 Suggestion",
            value=suggestion[:500],
            inline=False,
        )
    return embed


def build_help_embed() -> Any:
    """Build the ``/help`` output as a structured embed."""
    if not _DISCORD_AVAILABLE:
        return None
    embed = discord.Embed(
        title="🦅 Munin · Command Surface",
        color=COLOR_ACCENT or COLOR_DARK,
        description=(
            "Talk naturally (DM, mention, or `/munin` prefix) or use commands below.\n"
            "Each complex investigation gets its own thread with an `INV-XXX` identifier."
        ),
    )
    embed.add_field(
        name="📋 Commands",
        value=(
            "`/approvals` — list pending approvals\n"
            "`/approve <id>` / `/reject <id>` — resolve a request\n"
            "`/cancel <run_id>` — cancel a run\n"
            "`/status` — current conversation's runs\n"
            "`/conversations` — your conversations\n"
            "`/history [n]` — last n events\n"
            "`/artifacts [run_id]` — list artifacts\n"
            "`/artifact <id>` — fetch one\n"
            "`/tools` — runtime capabilities\n"
            "`/tool <name> <json>` — raw tool output (admin only)"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎮 Interactive",
        value=(
            "Click **Approve** / **Reject** buttons on HITL cards.\n"
            "During runs, buttons may appear for pause/stop/scope changes.\n"
            "Evidence and sources can be expanded via reaction buttons."
        ),
        inline=False,
    )
    embed.set_footer(text="Knowledge outlives the battle.  ·  Munin v1.0.0")
    return embed


# ----------------------------------------------------------------------------
# Views (interactive buttons)
# ----------------------------------------------------------------------------


class ApprovalView:
    """Interactive HITL approval buttons.

    Replaces the old ``/approve <id>`` text command with a View that has
    ✅ Approve and ❌ Reject buttons. The callbacks go through the SAME
    server-side authority boundary as the text command — the button is
    a surface convenience, not a policy bypass.

    ``on_resolve`` is an async callback ``(choice: str, interaction) ->
    None`` that runs after the operator clicks. It should perform the
    ``store.resolve_human_decision`` + ``_resume_approved_run`` sequence
    exactly like ``_cmd_resolve`` does in the adapter.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        if not _DISCORD_AVAILABLE:
            return None
        return super().__new__(cls)

    def __init__(
        self,
        *,
        request_id: str,
        run_id: str = "",
        on_resolve: Callable[[str, Any], Any] | None = None,
        timeout: float = 3600.0,
    ) -> None:
        import discord as _d

        class _Inner(_d.ui.View):
            def __init__(self_inner: Any) -> None:  # type: ignore[no-untyped-def]
                super().__init__(timeout=timeout)
                self_inner.request_id = request_id
                self_inner.run_id = run_id
                self_inner.on_resolve = on_resolve
                self_inner.message: Any = None

            async def on_timeout(self_inner: Any) -> None:  # type: ignore[no-untyped-def]
                for item in self_inner.children:
                    item.disabled = True
                if self_inner.message is not None:
                    with contextlib.suppress(Exception):
                        await self_inner.message.edit(
                            content="⏰ This approval has expired. The request is no longer actionable.",
                            view=self_inner,
                        )

            @_d.ui.button(label="Approve", style=_d.ButtonStyle.success, emoji="✅")
            async def approve(
                self_inner: Any, interaction: Any, button: Any  # type: ignore[no-untyped-def]
            ) -> None:
                await interaction.response.defer()
                with contextlib.suppress(Exception):
                    await interaction.edit_original_response(
                        content=f"✅ **Approved** `{request_id}` by {interaction.user.mention} — resuming...",
                        view=None,
                    )
                if self_inner.on_resolve:
                    try:
                        await self_inner.on_resolve("approve", interaction)
                    except Exception:  # noqa: BLE001
                        log.exception("discord_ui: approve callback failed")
                self_inner.stop()

            @_d.ui.button(label="Reject", style=_d.ButtonStyle.danger, emoji="❌")
            async def reject(
                self_inner: Any, interaction: Any, button: Any  # type: ignore[no-untyped-def]
            ) -> None:
                await interaction.response.defer()
                with contextlib.suppress(Exception):
                    await interaction.edit_original_response(
                        content=f"❌ **Rejected** `{request_id}` by {interaction.user.mention}.",
                        view=None,
                    )
                if self_inner.on_resolve:
                    try:
                        await self_inner.on_resolve("reject", interaction)
                    except Exception:  # noqa: BLE001
                        log.exception("discord_ui: reject callback failed")
                self_inner.stop()

        self._view = _Inner()

    @property
    def view(self) -> Any:
        return self._view


class RunControlView:
    """Interactive controls during a live run.

    Provides buttons for:
    - ⏸️ Pause (gracefully stop streaming, keep checkpoint)
    - ⏹️ Stop (cancel the run)
    - 📊 Status (post the current status embed)
    - 📁 Artifacts (list artifacts for this run)

    These are convenience shortcuts that call the same adapter functions
    as the corresponding text commands.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        if not _DISCORD_AVAILABLE:
            return None
        return super().__new__(cls)

    def __init__(
        self,
        *,
        run_id: str,
        on_cancel: Callable[[Any], Any] | None = None,
        on_status: Callable[[Any], Any] | None = None,
        on_artifacts: Callable[[Any], Any] | None = None,
        timeout: float = 14400.0,  # 4h — covers a full live session
    ) -> None:
        import discord as _d

        class _Inner(_d.ui.View):
            def __init__(self_inner: Any) -> None:  # type: ignore[no-untyped-def]
                super().__init__(timeout=timeout)
                self_inner.run_id = run_id
                self_inner.on_cancel = on_cancel
                self_inner.on_status = on_status
                self_inner.on_artifacts = on_artifacts
                self_inner.message: Any = None

            @_d.ui.button(label="Stop", style=_d.ButtonStyle.danger, emoji="⏹️")
            async def stop_run(
                self_inner: Any, interaction: Any, button: Any  # type: ignore[no-untyped-def]
            ) -> None:
                await interaction.response.defer()
                if self_inner.on_cancel:
                    try:
                        await self_inner.on_cancel(interaction)
                    except Exception:  # noqa: BLE001
                        log.exception("discord_ui: cancel callback failed")
                with contextlib.suppress(Exception):
                    await interaction.followup.send(
                        f"🚫 Stop requested for run `{run_id}`.", ephemeral=False
                    )
                self_inner.stop()

            @_d.ui.button(label="Status", style=_d.ButtonStyle.secondary, emoji="📊")
            async def get_status(
                self_inner: Any, interaction: Any, button: Any  # type: ignore[no-untyped-def]
            ) -> None:
                await interaction.response.defer(ephemeral=True)
                if self_inner.on_status:
                    try:
                        await self_inner.on_status(interaction)
                    except Exception:  # noqa: BLE001
                        log.exception("discord_ui: status callback failed")

            @_d.ui.button(label="Artifacts", style=_d.ButtonStyle.secondary, emoji="📁")
            async def get_artifacts(
                self_inner: Any, interaction: Any, button: Any  # type: ignore[no-untyped-def]
            ) -> None:
                await interaction.response.defer(ephemeral=True)
                if self_inner.on_artifacts:
                    try:
                        await self_inner.on_artifacts(interaction)
                    except Exception:  # noqa: BLE001
                        log.exception("discord_ui: artifacts callback failed")

        self._view = _Inner()

    @property
    def view(self) -> Any:
        return self._view


# ----------------------------------------------------------------------------
# Thread helpers
# ----------------------------------------------------------------------------


def _thread_name(run_id: str, prompt: str) -> str:
    """Build the canonical INV thread name from a run_id and prompt.

    Used both by ``create_run_thread`` (initial creation) and by the adapter
    when it renames the thread to the *real* ``run_id`` after ``create_turn``
    returns.  Keeping the logic in one place guarantees the rename produces
    the same string the original name would have used.
    """
    objective = prompt.strip().split("\n")[0] if prompt else "operation"
    if len(objective) > 56:
        objective = objective[:53] + "..."
    return f"🔍 INV-{run_id[:8].upper()} · {objective}"


async def create_run_thread(
    message: Any,
    *,
    run_id: str,
    prompt: str = "",
) -> Any:
    """Create a Discord thread for a run, with a structured name.

    The thread name follows the pattern ``INV-XXXX · <objective snippet>``
    so investigations are easy to identify in the channel sidebar.
    Returns the ``discord.Thread`` or ``None`` if threads are not
    available (DMs, missing permissions, etc.).

    The thread is **only** created for guild channels — DMs don't
    support threads, so the run stays in the DM channel directly.
    """
    if not _DISCORD_AVAILABLE:
        return None
    if message.guild is None:
        return None  # DMs don't have threads

    try:
        thread = await message.create_thread(
            # One investigation = one thread: INV-XXXX pattern so runs are
            # easy to find in the channel sidebar.
            name=_thread_name(run_id, prompt),
            auto_archive_duration=1440,  # 24h
            reason=f"Munin run {run_id}",
        )
        return thread
    except Exception as exc:  # noqa: BLE001
        log.debug("discord_ui: thread creation failed run_id=%s: %s", run_id, exc)
        return None


async def post_investigation_header(
    thread: Any,
    *,
    run_id: str,
    prompt: str,
    conversation_id: str = "",
) -> None:
    """Post an investigation header to the thread with context info.

    This gives the operator a visible "context utilized" block at the
    start of each investigation thread:
    - This thread
    - The conversation/case ID
    - The original objective
    """
    if not _DISCORD_AVAILABLE or thread is None:
        return

    embed = discord.Embed(
        title="🔍 Investigation Started",
        color=COLOR_ACCENT or COLOR_DARK,
        description=(
            f"**Run ID:** `{run_id}`\n"
            f"**Conversation:** `{conversation_id[:32]}`" if conversation_id
            else f"**Run ID:** `{run_id}`"
        ),
    )

    if prompt:
        objective = prompt[:1000]
        embed.add_field(
            name="📋 Objective",
            value=f">>> {objective}",
            inline=False,
        )

    embed.add_field(
        name="🧠 Context Utilized",
        value=(
            "• Thread-scoped conversation\n"
            "• Munin conversation history (last 16 messages)\n"
            "• LangGraph checkpoint state (if resuming)\n"
            "• Hugin knowledge graph (if relevant)\n"
            "• Shared intel store (scoped to this conversation)"
        ),
        inline=False,
    )

    embed.set_footer(text="Knowledge outlives the battle.")

    with contextlib.suppress(Exception):
        await thread.send(embed=embed)


def truncate_for_embed(text: str, max_chars: int = 1000) -> str:
    """Truncate text for an embed field, adding ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
