---
name: antigravity-coder
description: Delegate a bounded coding task to Antigravity CLI using the user's Google Sign-In subscription quota, then independently inspect the real Git diff and validation results before accepting or repairing the patch.
license: MIT
compatibility: opencode
metadata:
  project: Munin
  workflow: supervised-coding
  tool: antigravity_delegate
  backend: agy
---

# Antigravity Coder

Use Antigravity CLI (`agy`) as an implementation subagent while Raven-Mind remains the supervisor and final reviewer.

The custom tool invokes Antigravity in headless print mode from the current Git worktree. Authentication is owned by `agy` through the user's saved Google Sign-In session, so a Google AI Pro or Ultra account can use its Antigravity subscription quota. This workflow does not require `GEMINI_API_KEY` and does not use the Python Antigravity SDK.

## Responsibilities

Raven-Mind must:

- inspect enough of Munin to define a precise task;
- call `antigravity_delegate` with explicit scope and acceptance criteria;
- review the returned Git diff rather than trusting the worker summary;
- check every changed file and validation exit code;
- run additional checks when necessary;
- repair or re-delegate narrow defects;
- never commit or push merely because Antigravity says the task is complete.

Antigravity may inspect and modify files in the current worktree and run allowed local development commands. It must not stage, commit, push, stash, reset, clean, restore, checkout, or modify Git history.

## Prerequisites

Install the official Antigravity CLI in the environment that runs OpenCode.

macOS or Linux:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

Verify installation:

```bash
agy --version
```

Run `agy` interactively once and sign in with the Google account that owns the Google AI Pro or Ultra subscription. The CLI stores and reuses the authenticated session through the system keyring. On SSH, follow the authorization URL shown by the CLI.

No API key belongs in Munin, OpenCode configuration, prompts, diffs, logs, or final responses.

## Permissions required for headless coding

Headless mode cannot answer interactive permission prompts reliably. Configure Antigravity permissions before delegation using `/permissions` or:

```text
~/.gemini/antigravity-cli/settings.json
```

Allow workspace file reads and writes plus only the development commands needed by Munin. Explicitly deny Git-history and destructive commands. Deny rules have precedence over ask and allow rules.

Recommended minimum deny rules:

```text
command(git commit)
command(git push)
command(git reset)
command(git clean)
command(git checkout)
command(git restore)
write_file(.git/)
command(rm -rf)
command(sudo)
```

Do not enable unrestricted `always-proceed` merely to make delegation work. Prefer narrow command grants and workspace-scoped file access.

## When to use

Delegate when the task is concrete and bounded, for example:

- implement a defined feature;
- fix a reproducible bug;
- add regression tests;
- perform a constrained refactor;
- migrate a small API surface;
- make repetitive changes across related files.

Do not delegate vague architecture work, destructive operations, tasks requiring unavailable secrets, or work that cannot be independently reviewed.

## Before calling the tool

Inspect the relevant code and provide:

1. Exact desired behavior.
2. Relevant modules or expected paths.
3. Constraints and public behavior to preserve.
4. Acceptance criteria.
5. Targeted validation commands.

Do not send prompts such as "fix the project" or "improve the code".

Before the first delegation in a new environment, verify:

```bash
agy --version
```

If `agy` asks for authentication or permissions, stop the delegation and report the required interactive setup instead of falling back to an API key.

## Invocation

Call `antigravity_delegate` with:

- `task`: self-contained implementation specification;
- `allowed_paths`: expected repository-relative modification scope;
- `validation`: commands that the wrapper must execute after the worker finishes;
- `agent`: optional custom Antigravity agent name;
- `agent_timeout`: optional maximum headless runtime.

The wrapper runs the equivalent of:

```bash
agy --print "<delegated task>" --output-format json
```

When `agent` is supplied, it also passes:

```bash
--agent "<agent name>"
```

Example task:

```text
Fix cancellation handling in the run event stream. Trace the current lifecycle,
cancel and await the persistence task during disconnect cleanup, preserve normal
completion behavior, and add a regression test. Do not change the public event
schema or unrelated queue behavior.
```

## Mandatory review

After the tool returns:

1. Confirm `backend` is `antigravity-cli`.
2. Inspect `worker_exit_code`, `worker_output`, and `worker_stderr`.
3. Read `dirty_before` and `dirty_after`.
4. Inspect `changed_files` and `diff_stat`.
5. Read the complete returned `diff`.
6. Compare every change with the delegated scope.
7. Inspect each validation command, exit code, stdout, and stderr.
8. Use OpenCode read, grep, LSP, and bash tools for independent verification.
9. Run `git diff --check` before reporting success.

A zero `agy` exit code is not proof that the patch is correct. The real worktree diff and independent validation are authoritative.

Reject or repair a patch that:

- touches unrelated files without justification;
- overwrites pre-existing work;
- changes public behavior unintentionally;
- suppresses errors or tests instead of fixing behavior;
- introduces needless dependencies or duplicate abstractions;
- claims checks passed without a successful observed exit code;
- includes generated or secret material that should not be committed;
- performs or attempts any prohibited Git-history operation.

## Iteration

When the patch is close, re-delegate a narrow correction based on observable defects. Do not ask Antigravity to redo the entire task when a focused repair is sufficient.

Each invocation is a fresh headless turn unless an explicit, safely isolated session mechanism is added later. Include all context required for the requested correction in the delegated task.

## Completion report

Report:

- what Antigravity changed;
- what Raven-Mind independently verified;
- exact validation results;
- remaining risks or unresolved issues;
- whether the working tree already contained unrelated changes;
- whether `agy` successfully used its authenticated Google session.
