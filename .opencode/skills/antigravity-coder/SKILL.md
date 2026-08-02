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

Raven-Mind must inspect enough of Munin to define a precise task, call `antigravity_delegate` with explicit scope and acceptance criteria, inspect the returned Git diff, check validation exit codes, and independently decide whether to accept, repair, or narrowly re-delegate the patch.

Antigravity may inspect and modify files in the current worktree and run preapproved development commands. It must not stage, commit, push, stash, reset, clean, restore, checkout, switch branches, merge, rebase, or modify Git history.

## Installation and authentication

Install the official CLI.

Windows PowerShell:

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

macOS or Linux:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

Then run:

```bash
agy --version
agy
```

Complete Google Sign-In using the account that owns Google AI Pro or Ultra. The CLI stores the session in the system keyring. No API key belongs in Munin, OpenCode configuration, prompts, diffs, logs, or final responses.

## Install Munin's safe coding defaults

Run this once from the repository root:

```bash
python .opencode/antigravity/configure_defaults.py
```

The installer merges `.opencode/antigravity/default-settings.json` into:

```text
~/.gemini/antigravity-cli/settings.json
```

It is idempotent, preserves existing user settings, adds only missing entries, and writes a `.bak` backup before replacing an existing file.

The defaults configure:

- `artifactReviewPolicy: always-proceed`, so headless code writes do not stop on the visual artifact-review prompt;
- `allowNonWorkspaceAccess: false`;
- the repository as a **trusted workspace** in `settings.json.trustedWorkspaces`, `~/.gemini/trustedFolders.json`, and `~/.gemini/projects.json`, plus scoped `write_file(<repo-path>)` / `read_file(<repo-path>)` allow rules in both backslash and forward-slash spellings. **This step is required**: without it, `agy` soft-denies every `write_file` even inside the repository with the message `a tool required the "write_file" permission that headless mode cannot prompt for`;
- safe inspection commands such as `git status`, `git diff`, `git log`, `git show`, `git grep`, `git ls-files`, plus `ls`, `dir`, `Get-ChildItem`, `Test-Path`, `cat`, `type` so the worker can verify directory state without being soft-denied;
- common test, lint, build, and type-check commands for Python and the Munin frontend;
- explicit denial of staging, commits, pushes, branch/history mutations, destructive deletion, privilege escalation, and writes to `.git` or credential directories.

Do not add `command(*)` to `ask`: Antigravity evaluates `deny > ask > allow`, so a broad ask rule would override every safe command grant and make headless delegation block again.

Do not use `--dangerously-skip-permissions`.

If after running the installer `agy` still emits `write_file` or `command` soft-deny notices, switch to the `antigravity-setup` skill (`.opencode/skills/antigravity-setup/SKILL.md`) and run the end-to-end probe shim. That skill walks the full host-bootstrap path that was reverse-engineered from a real debugging session.

## When to use

Delegate concrete and bounded work such as a defined feature, reproducible bug, regression test, constrained refactor, small API migration, or repetitive related changes.

Do not delegate vague architectural decisions, destructive operations, tasks requiring unavailable secrets, or work that cannot be independently reviewed.

## Before calling the tool

Inspect the relevant implementation and provide:

1. Exact desired behavior.
2. Relevant modules or expected paths.
3. Constraints and public behavior to preserve.
4. Acceptance criteria.
5. Targeted validation commands.

Do not send prompts such as `fix the project` or `improve the code`.

Before the first delegation in a new environment, verify `agy --version`. If authentication or permission setup is missing, report that requirement instead of falling back to a Gemini API key.

## Invocation

Call `antigravity_delegate` with:

- `task`: self-contained implementation specification;
- `allowed_paths`: expected repository-relative modification scope;
- `validation`: commands the wrapper executes after the worker finishes;
- `agent`: optional custom Antigravity agent name;
- `agent_timeout`: optional maximum headless runtime.

The wrapper executes the equivalent of:

```bash
agy -p "<delegated task>" --mode=accept-edits --print-timeout 30m --output-format text
```

Example:

```text
Fix cancellation handling in the run event stream. Trace the current lifecycle,
cancel and await the persistence task during disconnect cleanup, preserve normal
completion behavior, and add a regression test. Do not change the public event
schema or unrelated queue behavior.
```

### Wrapper fallback on hostile hosts

On some hosts the wrapper MCP tool returns `{"status":"error","message":"Antigravity wrapper returned invalid JSON"}` with `process_exit_code 143` even when `agy` itself is installed and authenticated. The root cause observed in the wild was argument-string mangling by an intermediate PowerShell shell-quote layer: the wrapper's single-string `agy --print "<prompt>"` call ended up with the `--print-timeout` flag value being parsed as the prompt, so `agy` answered a meta question about its own flags and the wrapper mistimed its wait. The flag set on `agy 1.1.9` is `-p/--print/--prompt` taking the prompt as the next token, plus `--mode=accept-edits` for headless auto-approve of file edits (NOT `--headless`/`--approve`, which are not real flags on this version despite community blog posts).

When the wrapper fails this way, fall back to invoking `agy` directly through a quoting-safe token list — a Python `subprocess.run(cmd, ...)` with `cmd` as a list, or a `bash -c 'agy -p ...'` form on Unix. Never assemble `agy` arguments as a single quoted string through PowerShell `Start-Process -ArgumentList` or `System.Diagnostics.ProcessStartInfo.Arguments`, both of which mishandle internal quotes. See the `antigravity-setup` skill for a copy-paste probe shim that uses the token-list form.

The `agy` flag set evolves quickly. Before scripting against any flag you have not personally verified on the host run, dump the live flags with `agy --help` and, if still in doubt, use the Webfetch / Websearch MCP tools to pull the latest official Antigravity CLI docs from `https://antigravity.google/docs/cli`. The flags `--headless` and `--approve` are NOT real on `agy 1.1.9`; the real flag for headless auto-approve of file edits is `--mode=accept-edits`. Future versions may change this; verify at runtime.

## Mandatory review

After the tool returns:

1. Confirm `backend` is `antigravity-cli`.
2. Inspect `worker_exit_code`, `worker_output`, and `worker_stderr`.
3. Compare `dirty_before` with `dirty_after`.
4. Inspect every changed file and the complete returned `diff`.
5. Compare all changes with the delegated scope.
6. Inspect every validation exit code and output.
7. Use OpenCode read, grep, LSP, and bash tools for independent verification.
8. Run `git diff --check` before reporting success.

A zero `agy` exit code is not proof that the patch is correct. The actual worktree diff and independently executed validation are authoritative.

Reject or repair a patch that touches unrelated files, overwrites pre-existing work, changes public behavior unintentionally, suppresses errors or tests, introduces needless dependencies, contains secrets or generated junk, or attempts a prohibited Git operation.

## Iteration

When the patch is close, re-delegate a narrow correction based on observable defects. Each invocation is a fresh headless turn, so include all context required for the correction.

## Completion report

Report what Antigravity changed, what Raven-Mind independently verified, exact validation results, remaining risks, pre-existing worktree changes, and whether `agy` successfully used its authenticated Google session.
