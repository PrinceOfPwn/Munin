"""Auto-commit forged artifacts back to the repo.

When Munin runs on a GitHub Actions runner it's ephemeral: everything Munin
forges (Python tools in ``munin/generated/``, graph configs, soul edits) dies
with the runner. We want the OPPOSITE — Munin should evolve session by
session.

This module gives us three ergonomic helpers that a tool can call to persist
its side effects back to the repo. All are no-ops when git isn't available or
when ``MUNIN_AUTO_COMMIT`` is not set — so local dev doesn't accidentally
commit generated files to your working tree.

Enable in the workflow with::

    env:
      MUNIN_AUTO_COMMIT: "1"
      MUNIN_GIT_USER:  "munin-bot"
      MUNIN_GIT_EMAIL: "munin-bot@users.noreply.github.com"
      MUNIN_GIT_BRANCH: "munin/session-${{ github.run_id }}"

Design decisions:

* We commit / push in the background at the END of a forge — never inline in
  the request handler, so slow git pushes never block MCP tool responses. The
  worker is a single daemon thread with a queue.
* We batch commits: multiple ``queue_commit`` calls within a short window
  coalesce into one commit. This avoids 20 micro-commits per session.
* Failures are logged loudly and reported into the audit trail so the operator
  learns their token expired or a hook rejected the push.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Sequence

logger = logging.getLogger("munin-mcp.git_persist")

_QUEUE: queue.Queue[dict] = queue.Queue()
_WORKER: threading.Thread | None = None
_WORKER_LOCK = threading.Lock()

# Coalesce all queued commits within this window into a single commit.
_COALESCE_WINDOW_SECONDS = 2.0

# How long between polls when queue is empty.
_IDLE_POLL_SECONDS = 5.0


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _enabled() -> bool:
    """We only commit when the operator opted in via MUNIN_AUTO_COMMIT."""
    return _env("MUNIN_AUTO_COMMIT") in ("1", "true", "yes", "on")


def _run_git(args: Sequence[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run git with a hard timeout so a hung remote never wedges the worker."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def _repo_root() -> Path | None:
    """Locate the repo root by walking up until we hit .git."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _ensure_git_identity(cwd: Path) -> None:
    """Set user.name / user.email inside the repo scope (never global)."""
    user = _env("MUNIN_GIT_USER", "munin-bot")
    email = _env("MUNIN_GIT_EMAIL", "munin-bot@users.noreply.github.com")
    try:
        _run_git(["config", "user.name", user], cwd=cwd, check=False)
        _run_git(["config", "user.email", email], cwd=cwd, check=False)
    except Exception as exc:  # pragma: no cover
        logger.warning("git identity setup failed: %s", exc)


def _current_branch_or_create(cwd: Path) -> str:
    """Return current branch. If a target branch is set and we're not on it, checkout."""
    target = _env("MUNIN_GIT_BRANCH")
    if not target:
        head = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, check=False)
        return head.stdout.strip() or "HEAD"
    # Check if we're already on the target
    head = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, check=False).stdout.strip()
    if head == target:
        return target
    # Create-or-switch. Silent if already exists locally.
    _run_git(["checkout", "-B", target], cwd=cwd, check=False)
    return target


def _push(cwd: Path, branch: str) -> tuple[bool, str]:
    """Push the branch, with one rebase-and-retry on rejection.

    The most common cause of failure in a runner is that the remote branch
    advanced (another commit landed, or the branch already exists with a
    different tip). Without retry the batch dies silently and Munin's
    "evolves session to session" story quietly stops working. Retry once
    after ``git fetch + rebase`` — if it still fails, surface the error.
    """
    def _do_push() -> subprocess.CompletedProcess[str]:
        return _run_git(["push", "-u", "origin", branch], cwd=cwd, check=False)

    try:
        result = _do_push()
    except subprocess.TimeoutExpired:
        return False, "push timed out after 60s"
    except Exception as exc:
        return False, f"push crashed: {exc}"

    if result.returncode == 0:
        return True, "pushed"

    # Retry path: fetch + rebase + push. If any step fails we keep the last
    # error message intact.
    logger.info("git push rejected, attempting rebase-and-retry: %s",
                (result.stderr or result.stdout).strip()[-200:])
    try:
        _run_git(["fetch", "origin", branch], cwd=cwd, check=False)
        rebase = _run_git(["rebase", f"origin/{branch}"], cwd=cwd, check=False)
        if rebase.returncode != 0:
            # Rebase failed (conflict). Abort so the working tree stays clean.
            _run_git(["rebase", "--abort"], cwd=cwd, check=False)
            return False, f"rebase failed: {(rebase.stderr or rebase.stdout).strip()[-300:]}"
        retry = _do_push()
        if retry.returncode == 0:
            return True, "pushed (after rebase)"
        return False, (retry.stderr or retry.stdout).strip()[-500:]
    except subprocess.TimeoutExpired:
        return False, "rebase/push retry timed out"
    except Exception as exc:
        return False, f"retry crashed: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def queue_commit(paths: list[str] | str, message: str, *, kind: str = "forge") -> None:
    """Enqueue an add+commit+push. Coalesces with any pending commit in the window.

    ``paths``   — repo-relative path(s) to add. E.g. ``"munin/generated/gen__foo.py"``.
    ``message`` — commit message. The worker prefixes with ``[munin][{kind}] ``.
    ``kind``    — free-form label used for grouping in audit logs.

    Safe to call from anywhere. Fully async — never blocks the caller. If auto-
    commit is disabled (default in local dev) this is a no-op logged at DEBUG.
    """
    if not _enabled():
        logger.debug("git_persist: MUNIN_AUTO_COMMIT off — skipping commit for %s", paths)
        return
    payload = {
        "paths": [paths] if isinstance(paths, str) else list(paths),
        "message": message,
        "kind": kind,
        "enqueued_at": time.monotonic(),
    }
    _QUEUE.put(payload)
    _ensure_worker()


def flush(timeout: float = 30.0) -> None:
    """Block until the queue is empty. Use in workflow's post step so pending commits ship before the runner dies."""
    if not _enabled():
        return
    _ensure_worker()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _QUEUE.empty():
            # give the worker a beat to process the last batch
            time.sleep(_COALESCE_WINDOW_SECONDS + 0.5)
            if _QUEUE.empty():
                return
        time.sleep(0.5)
    logger.warning("git_persist.flush: timeout after %.1fs, %d items still queued", timeout, _QUEUE.qsize())


# ─────────────────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_worker() -> None:
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker_loop, name="munin-git-persist", daemon=True)
        _WORKER.start()


def _worker_loop() -> None:
    logger.info("git_persist worker started (auto_commit=%s)", _enabled())
    while True:
        try:
            first = _QUEUE.get(timeout=_IDLE_POLL_SECONDS)
        except queue.Empty:
            continue

        batch = [first]
        # Coalesce: pull anything queued within the window into the same commit.
        window_end = time.monotonic() + _COALESCE_WINDOW_SECONDS
        while time.monotonic() < window_end:
            try:
                remaining = window_end - time.monotonic()
                item = _QUEUE.get(timeout=max(0.05, remaining))
                batch.append(item)
            except queue.Empty:
                break

        try:
            _process_batch(batch)
        except Exception:  # noqa: BLE001 — worker must survive any commit failure
            logger.exception("git_persist: batch failed; %d items dropped", len(batch))


def _process_batch(batch: list[dict]) -> None:
    repo = _repo_root()
    if repo is None:
        logger.warning("git_persist: no .git found — dropping batch of %d items", len(batch))
        return

    _ensure_git_identity(repo)
    branch = _current_branch_or_create(repo)

    all_paths: list[str] = []
    kinds: set[str] = set()
    messages: list[str] = []
    for item in batch:
        all_paths.extend(item["paths"])
        kinds.add(item["kind"])
        messages.append(item["message"])
    # dedupe preserving order
    seen: set[str] = set()
    unique_paths = [p for p in all_paths if not (p in seen or seen.add(p))]

    # git add
    try:
        _run_git(["add", "--", *unique_paths], cwd=repo)
    except subprocess.CalledProcessError as exc:
        logger.warning("git add failed: %s", exc.stderr.strip() if exc.stderr else exc)
        return

    # Bail if nothing was actually staged (files unchanged or ignored).
    diff = _run_git(["diff", "--staged", "--quiet"], cwd=repo, check=False)
    if diff.returncode == 0:
        logger.info("git_persist: nothing staged for batch of %d items", len(batch))
        return

    combined = "\n\n".join(messages) if len(messages) > 1 else messages[0]
    header = f"[munin][{'+'.join(sorted(kinds))}] "
    commit_msg = header + combined[:5000]

    try:
        _run_git(["commit", "-m", commit_msg], cwd=repo)
    except subprocess.CalledProcessError as exc:
        logger.warning("git commit failed: %s", exc.stderr.strip() if exc.stderr else exc)
        return

    ok, msg = _push(repo, branch)
    if ok:
        logger.info("git_persist: pushed %d files to %s", len(unique_paths), branch)
        return

    # Push failed even after rebase-retry. Re-enqueue the batch (up to 3 total
    # tries) so a transient failure doesn't lose the commit. After 3 the
    # commit stays on disk / in local branch — the workflow's "Final git push"
    # step is the last line of defense.
    attempts_max = 3
    total_retries = max((item.get("retry_count", 0) for item in batch), default=0)
    if total_retries + 1 < attempts_max:
        logger.warning(
            "git_persist: push failed on %s (attempt %d/%d) — re-queueing: %s",
            branch, total_retries + 1, attempts_max, msg,
        )
        # Small backoff so we don't hammer a broken remote.
        time.sleep(min(2 ** total_retries, 8))
        for item in batch:
            item["retry_count"] = total_retries + 1
            _QUEUE.put(item)
    else:
        logger.error(
            "git_persist: push failed on %s after %d attempts — commit stays local: %s",
            branch, attempts_max, msg,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Helper for the tool_forge / graph_forge caller sites
# ─────────────────────────────────────────────────────────────────────────────

def commit_forged_tool(script_path: str, tool_name: str, description: str = "") -> None:
    """Convenience wrapper for tool_forge callers."""
    rel = _make_relpath(script_path)
    if rel is None:
        return
    msg = f"forge tool {tool_name}"
    if description:
        msg += f"\n\n{description[:400]}"
    queue_commit([rel], msg, kind="tool")


def commit_forged_graph(manifest_path: str, name: str, purpose: str) -> None:
    """Queue the committed JSON source of truth for a forged graph."""
    rel = _make_relpath(manifest_path)
    if rel is None:
        return
    queue_commit([rel], f"forge graph {name}\n\n{purpose[:400]}", kind="graph")


def _make_relpath(path_str: str) -> str | None:
    """Convert an absolute path to repo-relative. Returns None if outside repo."""
    repo = _repo_root()
    if repo is None:
        return None
    try:
        return str(Path(path_str).resolve().relative_to(repo))
    except ValueError:
        logger.warning("git_persist: %s is outside repo %s — skipping", path_str, repo)
        return None
