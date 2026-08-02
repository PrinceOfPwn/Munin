# Raven-Mind: Antigravity delegation

Raven-Mind can delegate bounded implementation work through the `antigravity_delegate` custom tool, which invokes Antigravity CLI (`agy`) using the user's authenticated Google subscription session.

Before the first delegated task on a host:

1. Verify `agy --version`.
2. Run `agy` interactively once and complete Google Sign-In.
3. Run `python .opencode/antigravity/configure_defaults.py` from the repository root to merge Munin's safe headless coding permissions into the existing Antigravity settings.

When a coding task is concrete enough to delegate:

1. Load the `antigravity-coder` skill before invoking the tool.
2. Inspect enough of Munin to write a precise, self-contained task.
3. Supply expected paths, constraints, acceptance criteria, and targeted validation commands.
4. Let Antigravity modify the worktree, then inspect the returned real Git diff and exit codes.
5. Independently review every changed file; do not trust the worker report as proof.
6. Run additional checks or re-delegate a narrow correction when needed.
7. Never allow the delegated worker to stage, commit, push, stash, reset, clean, restore, checkout, switch branches, merge, rebase, or alter Git history.

The default profile allows common read-only Git inspection plus Python and frontend validation commands. It denies destructive and Git-history operations and keeps non-workspace access disabled.

Use Antigravity as an implementation worker, not as the final reviewer. Raven-Mind remains responsible for correctness, scope, validation, and the final decision to keep or repair the patch.
