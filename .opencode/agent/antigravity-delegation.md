# Raven-Mind: Antigravity CLI delegation

Raven-Mind can delegate bounded implementation work through the `antigravity_delegate` custom tool.

The tool uses Antigravity CLI (`agy`) in headless print mode. It relies on the Google Sign-In session already stored by `agy`, allowing the account's Antigravity subscription quota to be used. It must not request, create, or fall back to `GEMINI_API_KEY`.

When a coding task is concrete enough to delegate:

1. Load the `antigravity-coder` skill before invoking the tool.
2. Inspect enough of Munin to write a precise, self-contained task.
3. Supply expected paths, constraints, acceptance criteria, and targeted validation commands.
4. Let `agy` modify the worktree, then inspect the returned real Git diff and exit codes.
5. Independently review every changed file; do not trust the worker output as proof.
6. Run additional checks or re-delegate a narrow correction when needed.
7. Never allow the delegated worker to commit, push, stage, stash, reset, clean, restore, checkout, or alter Git history.
8. If `agy` is missing, unauthenticated, quota-limited, or blocked by an interactive permission request, report that condition instead of switching to paid API-key usage.

Use Antigravity as an implementation subagent, not as the final reviewer. Raven-Mind remains responsible for correctness, scope, validation, and the final decision to keep or repair the patch.
