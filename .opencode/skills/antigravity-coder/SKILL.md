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

## Recommended invocation per OS

This is the bottom-line summary of "what to actually run" on each supported OS, in priority order. The MCP wrapper (`antigravity_delegate`) is the recommended path when it works; on hosts where it fails reproducibly, fall back to the per-OS form below. The `agy` flag set documented here was verified against `agy 1.1.9` on the host that produced the Russian and Korean README localizations in commit `raven-mind/antigravity-localized-readmes`; verify the live flags with `agy --help` before relying on them.

### Tier 1 — MCP wrapper (preferred on every OS when it works)

```text
antigravity_delegate(
    task="<self-contained task>",
    allowed_paths=["repo/relative/path"],
    validation=["<read-only check command after worker finishes>"],
    agent_timeout=...,  # seconds, only if longer default needed
)
```

If the wrapper returns `{"status":"error","message":"Antigravity wrapper returned invalid JSON"}` with `process_exit_code 143`, or crashes with `undefined is not an object (evaluating 'args.validation.length')` when `validation` is omitted, you are on a hostile host — move to Tier 2 for that OS. The wrapper bug is a wrapper-layer issue, not a flaw in the worker; the underlying `agy` can still complete real coding tasks when invoked with a quoting-safe token list.

### Tier 2 — Quoting-safe token-list fallback

Identical core flags on every OS. The only thing that changes is the tool used to assemble the token list safely.

| OS | Recommended tool | Avoid |
|---|---|---|
| Windows / PowerShell 5.x | Python `subprocess.run(cmd_list, cwd=repo, capture_output=True, encoding="utf-8")` | `Start-Process -ArgumentList`, `System.Diagnostics.ProcessStartInfo.Arguments` (single-string form), `cmd /c` chains |
| macOS / Linux (bash/sh) | `bash -c 'agy -p "$PROMPT" --mode=accept-edits --print-timeout 30m --output-format text'` with `PROMPT` exported as an env var, or Python `subprocess.run` | Single-quoted PowerShell-style strings passed through any shell that re-parses them |

Core command line (token list form):

```text
["agy", "-p", "<delegated task>", "--mode=accept-edits",
 "--print-timeout", "30m", "--output-format", "text"]
```

Notes:

- `-p` / `--print` / `--prompt` all accept the prompt as the next positional token. Keep the prompt as ONE list element, never re-quote it as a single shell string.
- `--mode=accept-edits` (with the `=`) is the headless auto-approve flag on `agy 1.1.9`. The form `--mode accept-edits` (space-separated) sometimes parses fine but has been observed to fail on hostile shells — prefer the `=` form.
- `--headless` and `--approve` are NOT real flags on `agy 1.1.9` despite community blog posts.
- Set `cwd` of the subprocess to the registered trusted workspace (the repo path that `configure_defaults.py` registered). `agy` will soft-deny writes if its cwd is not a trusted workspace.
- Default `--print-timeout` is 5m. For README-sized work (a few MB of generated text), `agy` finished in ~70s. For bounded feature work, set `--print-timeout 30m` to avoid premature timeouts.

### Tier 2 reference shim (Python, cross-platform)

Drop this into a runnable Python script and adapt `prompt`, `repo_path`, and `validate_cmd`:

```python
import os, subprocess, time

AGY = os.path.join(os.environ["LOCALAPPDATA"], "agy", "bin", "agy.exe")  # Windows
# macOS / Linux: AGY = os.path.expanduser("~/.local/bin/agy")

repo_path = r"C:\path\to\repo"  # must be a registered trusted workspace
prompt = "<your single-string delegated task; newlines fine, avoid unbalanced quotes>"
validate_cmd = ["git", "diff", "--stat"]  # read-only check; "DY validation"

cmd = [AGY, "-p", prompt, "--mode=accept-edits",
       "--print-timeout", "30m", "--output-format", "text"]

t = time.time()
r = subprocess.run(cmd, cwd=repo_path, capture_output=True, timeout=2100,
                   encoding="utf-8", errors="replace")
print(f"agy exit={r.returncode} time={time.time()-t:.1f}s")
print("STDOUT:"); print(r.stdout)
err = r.stderr or ""
if err: print("STDERR:"); print(err[:5000])

# Independent validation (NOT executed by the worker)
v = subprocess.run(validate_cmd, cwd=repo_path, capture_output=True,
                   encoding="utf-8", errors="replace")
print(f"validate exit={v.returncode}")
print(v.stdout)
```

### Real coding task flow (not just docs)

`agy` can perform real coding tasks under the same invocation scheme used for the README localizations. The operator workflow is:

1. **Inspect** the relevant code with Read / Grep / Glob to write a precise task spec.
2. **Compose** the prompt with: exact desired behavior, expected module paths, constraints to preserve (public API, schemas, behavior invariants), acceptance criteria, and "do not stage / push / commit" guardrails.
3. **Invoke** `agy` with the Tier 2 token-list form (or Tier 1 MCP wrapper if working on the host).
4. **Verify** independently with the same tooling the wrapper would have used: `git diff --check`, `git diff --stat`, Read/Grep over every changed file, and the project validation command (`poetry run pytest`, `cd app && npm run build`, etc.) as appropriate to the touched surface.
5. **Iterate** — if the patch is close but has a specific defect, re-delegate a narrow correction with the observable defect quoted in the prompt; each invocation is a fresh headless turn.
6. **Decide** — accept the patch (and report what was delegated, what was independently verified, and what risk remains) or revert and re-delegate.

`agy` itself is not the reviewer; this skill is. The mandatory-review section above applies whichever tier the worker was invoked through.

### What downstream hosts need before Tier 2 works

Run the `antigravity-setup` skill first on any fresh host. Without its repository-trust and scoped `write_file` / `read_file` / inspection-command rules, `agy` will soft-deny every `write_file` (even inside the repo) with `a tool required the "write_file" permission that headless mode cannot prompt for`, regardless of which tier the invocation goes through.

### What you should NEVER do

- Use `--dangerously-skip-permissions` for a real delegation. It is acceptable only for a one-off diagnostic probe that you discard.
- Pass the prompt as a single re-quoted shell string on Windows. Always prefer a token list (Python `subprocess.run` with `cmd` as a list).
- Trust the worker's word that the file was written. Inspect the worktree diff; the `agy` worker has been observed answering "DONE" without actually creating the file when its `write_file` call was silently denied.
- Re-introduce a delegation result without running project validation when the task touched executable code (`munin/**`, `app/src/**`). Validation is skipped only for documentation-only changes.
