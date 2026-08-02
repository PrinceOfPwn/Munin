---
name: antigravity-setup
description: One-time host setup so the antigravity-coder skill works end-to-end. Install the agy CLI, authenticate, then register the active repository as a trusted workspace and grant scoped write_file/read_file/inspection-command permissions so headless delegation is not soft-denied. Use BEFORE the first antigravity_delegate call on a fresh host or whenever agy emits "a tool required the X permission that headless mode cannot prompt for".
license: MIT
compatibility: opencode
metadata:
  project: Munin
  workflow: host-bootstrap
  tool: antigravity_delegate
  backend: agy
---

# Antigravity Host Setup

One-time host bootstrap so the `antigravity-coder` skill can actually delegate
work to `agy` headlessly. Walking through this skill avoids the silent
soft-deny loop that the activation of `antigravity-coder` alone does not
prevent.

## When to use

Run this skill BEFORE the first `antigravity_delegate` call on a fresh host,
and again any time `agy` returns:

```text
jetski: no output produced — a tool required the "write_file" permission that
headless mode cannot prompt for, so it was auto-denied.
```

or

```text
jetski: no output produced — a tool required the "command" permission that
headless mode cannot prompt for, so it was auto-denied.
```

Both messages mean `agy` recognizes the active repository as a non-workspace
(or does not have a permission rule for the requested action) and is
soft-denying in headless mode.

## What the skill guarantees

After this skill runs, on the active host and for the active repository:

1. The `agy` CLI binary is installed and on `PATH`.
2. `agy` is authenticated via a saved Google Sign-In session (no API key).
3. The repository is registered as a trusted workspace in three places:
   - `~/.gemini/antigravity-cli/settings.json` (`trustedWorkspaces` array)
   - `~/.gemini/trustedFolders.json`
   - `~/.gemini/projects.json`
4. `permissions.allow` in `settings.json` includes scoped
   `write_file`/`read_file` rules for the repository and the inspection
   commands `ls`, `dir`, `Get-ChildItem`, `Test-Path`, `cat`, `type` (the
   validation calls `agy` tends to reach for).
5. `artifactReviewPolicy: always-proceed` is set so headless code writes do
   not stall on the visual artifact-review prompt.
6. No `command(*)` is added to `ask` (Antigravity evaluates `deny > ask >
   allow` — a broad `ask` rule would re-block every grant).

The skill does NOT use `--dangerously-skip-permissions` for real delegations.
It is acceptable to use it only for one-time diagnostic probes.

## Step 1 — Install the official CLI

Windows PowerShell (run as the operator user, NOT as Administrator):

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
```

macOS or Linux:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
```

The installer lands the binary at:

- Windows: `%LOCALAPPDATA%\agy\bin\agy.exe`
- macOS / Linux: `~/.local/bin/agy`

The Windows installer broadcasts a PATH update but it does not affect the
running process. Open a new terminal or prepend the bin directory to the
session PATH:

```powershell
$env:PATH = "$env:LOCALAPPDATA\agy\bin;" + $env:PATH
```

Verify:

```bash
agy --version
```

This skill was verified against `agy 1.1.9`. The `agy` flag set evolves quickly — community blogs in particular often describe flags that are not real on the version installed on the host. **If anything disagrees with the flags documented here, do NOT trust your memory:** use `agy --help` to dump the live flag list on the host, or use the Webfetch / Websearch MCP tools to pull the latest official docs from `https://antigravity.google/docs/cli` (or the live CLI reference page) before scripting against a behaviour you have not verified. Build the setup from runtime evidence, not from a snapshot in this file.

## Step 2 — Authenticate once

Run `agy` once interactively (no flags) and complete Google Sign-In with the
account that holds Google AI Pro or Ultra. The session is stored in the
system keyring; subsequent headless runs reuse it.

```bash
agy
```

Smoke-test headless auth without touching any file:

```bash
agy -p "Say only the word PONG and nothing else."
```

Expected stdout: `PONG`. If `agy` exits non-zero or hangs without output, the
keyring is locked or without a saved session — re-run interactive `agy`.

## Step 3 — Apply Munin's safe coding defaults

From the repository root:

```bash
python .opencode/antigravity/configure_defaults.py
```

The idempotent installer merges `.opencode/antigravity/default-settings.json`
into `~/.gemini/antigravity-cli/settings.json`. It also records the current
repository as a trusted workspace and adds the scoped `write_file` / `read_file`
/ inspection-command `allow` rules needed for headless file edits in this repo.
It writes a `.bak` backup before replacing an existing settings file.

THIS STEP IS NOT OPTIONAL. The base `configure_defaults.py` from before
commit raven-mind/antigravity-localized-readmes only configured inspection
commands; it did NOT register the repo as a trusted workspace, and the
default `trustedWorkspaces` array was empty. Without this step, `agy`
soft-denies every `write_file` even inside the repository, regardless of
permission rules.

Run the installer once, then verify it actually wrote the trust entries:

```bash
python .opencode/antigravity/configure_defaults.py
# Verify
grep -A4 trustedWorkspaces ~/.gemini/antigravity-cli/settings.json
```

The `trustedWorkspaces` array MUST list the absolute path of the active
repository. If it does not, the `--cwd` of the calling process and the
repository root are probably different (e.g. the script was invoked from
elsewhere) — re-run from the repo root, or pass `--workspace <repo-path>` if
the script supports it (it does from commit
raven-mind/antigravity-localized-readmes onward).

## Step 4 — Probe end-to-end before the real delegation

Validate that `agy` can actually write inside the repository without any
`--dangerously-skip-permissions` escape hatch. The Probe uses a small
Python subprocess shim (or a quoting-safe shell) so `agy` args are passed as
a token list, NOT as a single quoted shell string — the latter is the second
silent failure mode that this skill exists to prevent.

### Token-list invocation: the second silent failure

If you call `agy` from PowerShell with `Start-Process -ArgumentList` or
`System.Diagnostics.ProcessStartInfo.Arguments` as a single string, the
prompt is mangled in some flag orderings and `agy` ends up reading the
`--print-timeout` flag value as the prompt itself (it answers with
documentation about the flag instead of running the task). The wrapper of
the `antigravity_delegate` MCP tool exposes the same class of bug and can
return `{"status":"error","message":"Antigravity wrapper returned invalid
JSON"}` even when `agy` actually succeeded. Always invoke `agy` via a true
token list.

Probe shim (cross-platform, Python 3.8+):

```python
import os, subprocess, time

AGY = os.path.join(os.environ["LOCALAPPDATA"], "agy", "bin", "agy.exe")  # Windows
# On macOS / Linux use: os.path.expanduser("~/.local/bin/agy")
WORKDIR = os.getcwd()  # the repository root the probe runs from
PROBE = os.path.join(WORKDIR, "agy_probe.txt")

prompt = (
    f'Use the built-in write_file tool to create a NEW file at this exact '
    f'absolute path: {PROBE}\n'
    f'The file content must be EXACTLY one line of text: AGY_PROBE_OK\n'
    f'Do NOT run any shell command. Do NOT stage or commit anything.'
)

cmd = [AGY, "-p", prompt, "--mode=accept-edits",
       "--print-timeout", "120s", "--output-format", "text"]

r = subprocess.run(cmd, cwd=WORKDIR, capture_output=True, timeout=180,
                   encoding="utf-8", errors="replace")
print("exit", r.returncode)
print("STDERR", r.stderr[:500])
print("PROBE EXISTS", os.path.exists(PROBE))
if os.path.exists(PROBE):
    os.remove(PROBE)
```

Expected: `exit 0`, `STDERR` empty, `PROBE EXISTS True`. The probe file is
created and deleted cleanly.

If the probe fails with `write_file` denied, return to Step 3 — the trust
registration or the scoped `write_file` rule is missing for this repo. If it
" succeeds" with the message `DONE absolute-write` but the file is not on
disk, the prompt was probably parsed wrong (`agy` answered a meta question
about its own flags); switch to the token-list invocation form above.

## Step 5 — Use the `antigravity-coder` skill

After probe success, return to the `antigravity-coder` skill and proceed
with `antigravity_delegate` (or with the Python subprocess shim if the
wrapper is misbehaving on this host).

Per-call flags that work:

```text
agy -p "<delegated task>" --mode=accept-edits \
    --print-timeout 30m \
    --output-format text
```

Notes:

- `--mode=accept-edits` is the headless equivalent of the old `--yolo` for
  file edits. Shell commands are still governed by `settings.json`.
- `--headless` and `--approve` are NOT real flags on `agy 1.1.9` despite some
  community blog posts. The actual flag is `--mode=accept-edits`.
- Default `--print-timeout` is 5m. For README-sized generation tasks (two
  full translations) `agy` finished in just over a minute; for larger
  bounded tasks, set `--print-timeout 30m` explicitly.
- Stay inside the workspace `cwd` you registered in Step 3. A delegation
  that switches `cwd` mid-run will soft-deny writes again.

## Quota warning

Subagents burn Google AI quota in parallel. A single `agy` goal can spawn
10+ nested model calls. Keep complex delegations to a few clearly-bounded
tasks per session and watch `/usage` if running interactively. The probe
shim above costs roughly one prompt worth of quota.

## What NOT to commit

This skill changes files OUTSIDE the repository (`~/.gemini/**`). Never
commit those. Never commit `agy_probe.txt` or any artifact produced by the
probe shim — delete them after the probe succeeds.

The only files inside the Munin repository that legitimately belong to this
skill's setup are:

- `.opencode/skills/antigravity-setup/SKILL.md` (this file)
- `.opencode/skills/antigravity-coder/SKILL.md` (gained a cross-reference
  in commit raven-mind/antigravity-localized-readmes)
- `.opencode/antigravity/configure_defaults.py` (gained trust-workspace
  registration in the same commit)
- `.opencode/antigravity/default-settings.json` (gained inspection commands
  `ls` / `dir` / `Get-ChildItem` / `Test-Path` / `cat` / `type` in the same
  commit)

Anything else is operator-machine state and stays out of Git.

## Reference

- Official `agy` headless mode documentation:
  `https://antigravity.google/docs/cli/headless`
- Permissions reference:
  `https://antigravity.google/docs/cli/permissions`
- The companion skill: `antigravity-coder` (`.opencode/skills/antigravity-coder/SKILL.md`)

## Cross-reference log

The host-bootstrap steps above were reverse-engineered from a real
end-to-end debugging session on a Windows host where:

1. The `Antigravity.exe` desktop GUI was installed but the `agy` CLI was
   not. The wrapper `antigravity_delegate` returned `process_exit_code 143`
   (SIGTERM) every time, with no stdout/stderr.
2. After `agy` install + Google Sign-In, `-p "Say PONG"` worked interactively
   but file-write tasks were soft-denied with `write_file autodenied`.
3. The repository path was missing from `trustedWorkspaces`,
   `trustedFolders.json` and `projects.json`; adding it to all three plus
   scoped `allow` rules finally let `agy` edit files headlessly inside the
   workspace.
4. The wrapper `antigravity_delegate` returned `invalid JSON` even after all
   of the above, while direct token-list `agy` calls succeeded — the root
   cause was argument mangling by an intermediate PowerShell shell-quote
   layer. The Python subprocess shim sidesteps it entirely.

This skill exists so the next host does not have to repeat that path.
