---
name: antigravity-coder
description: Delegate a bounded coding task to the Google Antigravity SDK, then independently inspect the real Git diff and validation results before accepting or repairing the patch.
license: MIT
compatibility: opencode
metadata:
  project: Munin
  workflow: supervised-coding
  tool: antigravity_delegate
---

# Antigravity Coder

Use Antigravity as an implementation worker while Raven-Mind remains the supervisor and reviewer.

## Responsibilities

Raven-Mind must:

- inspect enough of Munin to define a precise task;
- call `antigravity_delegate` with explicit scope and acceptance criteria;
- review the returned Git diff rather than trusting the worker summary;
- check every changed file and validation exit code;
- run additional checks when necessary;
- repair or re-delegate narrow defects;
- never commit or push merely because Antigravity says the task is complete.

Antigravity may inspect and modify files in the current worktree and run local development commands. It must not stage, commit, push, stash, reset, clean, or modify Git history.

## Prerequisites

Install the official preview SDK in the environment that runs OpenCode:

```bash
python -m pip install google-antigravity
```

Provide one of the authentication methods supported by the SDK. The simplest local setup is:

```bash
export GEMINI_API_KEY="..."
```

Never place credentials in the repository, prompts, diffs, logs, or final responses.

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

## Invocation

Call `antigravity_delegate` with:

- `task`: self-contained implementation specification;
- `allowed_paths`: expected repository-relative modification scope;
- `validation`: commands that the wrapper must execute after the worker finishes.

Example task:

```text
Fix cancellation handling in the run event stream. Trace the current lifecycle,
cancel and await the persistence task during disconnect cleanup, preserve normal
completion behavior, and add a regression test. Do not change the public event
schema or unrelated queue behavior.
```

## Mandatory review

After the tool returns:

1. Read `dirty_before` and `dirty_after`.
2. Inspect `changed_files` and `diff_stat`.
3. Read the complete returned `diff`.
4. Compare every change with the delegated scope.
5. Inspect each validation command, exit code, stdout, and stderr.
6. Use OpenCode read, grep, LSP, and bash tools for independent verification.
7. Run `git diff --check` before reporting success.

Reject or repair a patch that:

- touches unrelated files without justification;
- overwrites pre-existing work;
- changes public behavior unintentionally;
- suppresses errors or tests instead of fixing behavior;
- introduces needless dependencies or duplicate abstractions;
- claims checks passed without a successful observed exit code;
- includes generated or secret material that should not be committed.

## Iteration

When the patch is close, re-delegate a narrow correction based on observable defects. Do not ask Antigravity to redo the entire task when a focused repair is sufficient.

## Completion report

Report:

- what Antigravity changed;
- what Raven-Mind independently verified;
- exact validation results;
- remaining risks or unresolved issues;
- whether the working tree already contained unrelated changes.
