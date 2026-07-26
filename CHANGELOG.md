# Munin — Changelog

Only the load-bearing changes. For architectural detail see `ARCHITECTURE.md`.

## Unreleased — the persistent-multi-agent rewrite

**58 bugs fixed** across 4 adversarial review rounds. The system is
ready to ship for either free-tier (artifact roundtrip) or Turso persistence.

### Added — brand new capabilities

- **Persistence backend abstraction** (`munin/mcp/persistence.py`):
  same `sqlite3` API for either a local file (default, ephemeral in runner
  → free-tier persistence via artifact roundtrip) or a libsql/Turso remote
  (opt-in via separate `MUNIN_DB_URL` + `MUNIN_DB_AUTH_TOKEN`, cross-session
  persistence + safe concurrency). Zero code changes to switch.
- **Auto-commit of forged artifacts** (`munin/mcp/git_persist.py`):
  async worker thread with coalescing (2s window). `tool_forge` and
  `graph_forge` outputs commit to a session branch when `MUNIN_AUTO_COMMIT=1`.
  Retry with rebase on push rejection; up to 3 attempts with backoff.
  `SIGTERM` flushes the queue before exit.
- **Soul edits create PRs**: `soul_propose_edit` on the runner opens a
  labeled PR (`soul-proposal`) with the diff and rationale, so the human
  approves changes to Munin's identity via git flow.
- **`munin_diagnostics` tool** (`munin/mcp/tools/diagnostics_tool.py`):
  probes every subsystem (DB, LLM, LDAP, recon binaries, Hugin, Tavily,
  forge registry, graphs, wake queue, presence, auth, git-persist).
  `paranoid` mode runs a full end-to-end forge → wake → RESULT verification.
- **Live subagent trace** — new `subagent_trace(subagent, since_id)` MCP
  tool + `SubagentTrace.tsx` frontend panel. Human observes iteration
  in real time; the subagent posts `PROGRESS` messages before every tool
  call so the stream is human-readable.
- **`munin/generated/` tools reachable to forged subagents**:
  `build_tool_catalog` now loads `gen__*` on demand when a forged graph's
  whitelist references them. Previously forged graphs could only use
  native tools — the whole point of forging was defeated.
- **GitHub Actions Kali runner**: `.github/workflows/live-session.yml`
  rewritten to run inside `kalilinux/kali-rolling` with nmap, nuclei,
  feroxbuster, ffuf, sqlmap, hydra, smbmap, netexec, katana, searchsploit
  preinstalled. OpenLDAP as a service container. State artifact
  roundtrip on start/finish. Auto-commit + PR permissions declared.

### Fixed — bugs that broke functionality

Selected highlights (see `ARCHITECTURE.md` "What was broken and now works"
for the full list):

- **`munin_wake` didn't spawn a runner** — enqueued the task, then nobody
  spawned `python -m munin.subagents.runner`. Now delegates to
  `Orchestrator.wake` which is atomic + idempotent
  (`try_claim_spawn_slot` with `BEGIN IMMEDIATE`).
- **Runner idle-timeout dropped wakes** — a wake arriving in the closing
  window landed in the queue but nobody picked it up. Now the runner
  marks presence `EXITING` before break, does a last claim, upgrades back
  to RUNNING if it caught one.
- **`graph_drop` was cosmetic** — `graph_get` didn't filter `active=1`, so
  dropped graphs still woke. Now enforced.
- **Subagents returned `ok=True` on LLM error / max_iter / empty content**
  — parent Munin saw false `RESULT` messages. Now `subagent_ok` tracked
  through the loop; runner posts `RESULT` vs `ERROR` honestly.
- **RESULT truncated to 6000 bytes** produced invalid JSON mid-object.
  Now overflow → `data/wake_artifacts/wake_<id>.json` + pointer body.
- **`munin_read_source` crashed** on `settings.munin_db_path` (attr
  didn't exist). Fixed + restricted read to `munin/` and `app/`.
- **`_load_callable` re-imported every gen tool on every ReAct step**
  (600 imports per chat with 15 tools × 40 iters). Now cached by
  `(path, function_name, mtime_ns)`.
- **Middleware app re-instantiated per FastMCP access** — `lambda`
  invoked `original_app()` each time. Now capture once.
- **ReAct guard only detected identical repeats**, missing A/B/A/B
  loops. Now window-of-6 with unique-count threshold.
- **Orphan-kill ran on every module import** — `poetry --help` from a
  developer's terminal could SIGTERM every running Munin process. Moved
  into `main()`.
- **`MUNIN_MCP_AUTH_TOKEN` not stripped** — trailing newline in `.env`
  caused every request to 403. Now `.strip()` at config load.
- **libsql cursor missing `rowcount`** — every deactivate/drop crashed
  with `AttributeError`. Now proxied.
- **Frontend `SubagentTrace` tight-loop** — `pollCount` in `useEffect`
  deps caused tick storms every RTT. Refs everywhere; interval only
  re-arms on pause / config change.
- **`subagent_trace` lost the middle of long histories** — used
  `ORDER BY id DESC LIMIT` and filtered in Python. Now `episodic_since`
  with `WHERE id > ? ORDER BY id ASC`. Same fix for messages via
  `messages_from_sender_since`.

### Added — defensive posture

- Structured logging middleware around every tool call via
  `audited_tool`: trace_id + timing + redacted args.
- Bearer-token auth enforced on HTTP transport with `hmac.compare_digest`.
  Server refuses to start on HTTP without a token unless
  `MUNIN_MCP_ALLOW_ANON=1`.
- Sandbox `_BANNED_ATTRS` extended with shell/exec/fork primitives.
- `validate_source_file` re-runs the AST guard before every generated
  script import.
- `install_hint` field in `missing_dependency` errors so the LLM knows
  what to install instead of retrying blindly.
- OpenLDAP + AD schema tolerance: LDAP tools detect the server flavor
  and choose filters/attrs accordingly. Mock LDIF no longer leaks
  vulnerability hints in `description`.

### Changed — behavior

- ReAct loop has NO fixed iteration cap by default (`HARD_CEILING=10_000`).
  Set `MUNIN_MAX_ITERATIONS` to bound it.
- Tool results are NOT truncated when fed back to the LLM. If context
  overflows the LLM API reports it explicitly.
- Free-tier session state now lives on artifacts, not just the runner
  disk. Workflow input `persist_state` (default `true`) controls this.
