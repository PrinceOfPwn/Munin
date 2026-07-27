# Munin — Persistent Architecture

*"What was once seen is never forgotten."* — for the tagline to hold, Munin's
state must survive the death of any single runner.

This document explains how that works, what breaks otherwise, and how to
migrate from the free ephemeral setup to real cloud state.

## The three layers of state

Munin has three flavors of state, each with its own lifecycle and persistence
story:

| Layer | Where it lives | Survives runner death? | How |
|---|---|---|---|
| **Soul** — identity, principles, goals, skills | `soul/*.md` in the repo | Yes | Human merges `soul-proposal/*` PRs opened by Munin |
| **Forged tools + graphs** — code Munin wrote at runtime | `munin/generated/*.py` (code) + `procedural` / `generated_graphs` SQLite tables (registry) | Yes | Auto-committed to branch `munin/session-<run_id>` |
| **Memory + working state** — episodic events, semantic facts, wake queue, agent messages, shared intel | `data/shared_state.sqlite` (WAL) | Yes (either mode) | Free tier: rolled between runs via a GitHub Actions artifact.  Turso / libsql cloud DB |

## Persistence modes

### 1. Ephemeral file SQLite (default, free)

- **When**: `MUNIN_DB_URL` is empty.
- **Where**: `data/shared_state.sqlite` inside the runner's workspace.
- **Between sessions**: the workflow uploads the file as a `munin-state`
  artifact at the end of each run, and downloads it at the start of the next.
- **Retention**: 90 days per GitHub artifact retention policy (configurable).
- **Cost**: $0. Free-tier friendly.
- **Trade-offs**:
  - No concurrent sessions (last write wins).
  - Restore step takes 1-3 seconds per session.
  - If someone deletes the artifact, the memory is gone. Consider committing
    a snapshot manually with `munin snapshot-soul`.

### 2. Turso / libsql cloud (opt-in, ~free for hobby scale)

- **When**: `MUNIN_DB_URL=libsql://<host>` and `MUNIN_DB_AUTH_TOKEN=<t>` are
  set as separate repo secrets.
- **Where**: an embedded libSQL replica in each runner, synchronized with
  Turso's remote SQLite service.
- **Between sessions**: nothing to do — the DB is remote and persistent.
- **Cost**: Free tier gives 9 GB storage + 1 B row reads / month. Munin's
  entire state stays well under that.
- **Trade-offs**:
  - Initial sync adds a network roundtrip; normal reads stay local to the
    embedded replica and writes synchronize to Turso.
  - Enables concurrent sessions and multiple runners sharing state safely.

Switch is a single env var — no code changes needed. The abstraction lives
in `munin/mcp/persistence.py`.

## Session lifecycle in the runner

```
┌──────────────────────────────────────────────────────────────────┐
│  0. actions/checkout@v4                                          │
│     └── full history so we can commit + push                     │
│                                                                  │
│  1. Restore state artifact                        (if free-tier) │
│     └── munin-state → data/shared_state.sqlite                   │
│                                                                  │
│  2. Install Munin (poetry) + verify Kali toolchain               │
│                                                                  │
│  3. Wait for LDAP service container → seed mock data             │
│                                                                  │
│  4. Start Munin MCP server (streamable-http on :8890)            │
│     └── auth middleware validates Bearer token                   │
│                                                                  │
│  5. Open public tunnel → post URL + token to Job Summary         │
│                                                                  │
│  6. Session runs for N minutes ─────────────────────────────┐    │
│     │  Munin's ReAct loop calls tools:                      │    │
│     │  ├─ tool_forge → writes .py to munin/generated/       │    │
│     │  │  └── git_persist.queue_commit(...)   [async]       │    │
│     │  ├─ graph_forge → INSERT into generated_graphs        │    │
│     │  ├─ munin_wake → spawns subagent runner              │    │
│     │  │  └── runner processes wake, posts RESULT/ERROR    │    │
│     │  └─ soul_propose_edit → opens PR via gh CLI          │    │
│     │                                                       │    │
│     │  git-persist worker (daemon thread) batches commits: │    │
│     │  every 2s window it commits + pushes to bot branch.  │    │
│     └────────────────────────────────────────────────────── │    │
│                                                              │    │
│  7. SIGTERM → git_persist.flush()   (up to 20s)              │    │
│     └── ensures pending commits ship before process dies     │    │
│                                                              │    │
│  8. Stop MCP cleanly (up to 15s)                             │    │
│                                                              │    │
│  9. Final git push (safety net for anything queued)          │    │
│                                                              │    │
│ 10. Upload state artifact:                                    │    │
│     - data/shared_state.sqlite (+ WAL / SHM)                 │    │
│     - data/soul_pending/                                     │    │
│     - data/wake_artifacts/                                   │    │
│                                                              │    │
│ 11. Upload logs (retention 7d)                               │    │
└──────────────────────────────────────────────────────────────┘
```

## Multi-agent flow (unchanged conceptually; now durable)

```
       ┌──────────────────────────┐
       │      Munin core          │  <-- munin_chat / MCP tool
       │      (ReAct loop)        │
       └───────────┬──────────────┘
                   │
                   │  graph_forge(name="kerberos_specialist",
                   │              tools=[ldap_search, find_kerberoastable_users])
                   ▼
       ┌──────────────────────────┐
       │  generated_graphs        │  <-- SQLite row (persistent)
       │  active=1                │
       └───────────┬──────────────┘
                   │
                   │  munin_wake("kerberos_specialist", task={...})
                   ▼
       ┌──────────────────────────┐
       │  Orchestrator.wake       │
       │  try_claim_spawn_slot    │  <-- BEGIN IMMEDIATE, atomic
       └───────────┬──────────────┘
                   │
                   ├─── claimed=True ───────┐
                   │                        ▼
                   │              python -m munin.subagents.runner kerberos_specialist
                   │              (detached subprocess)
                   │                        │
                   │                        │  claim_wake_item → task
                   │                        │  ReAct loop with tool_whitelist
                   │                        │  publish_shared_intel, memory_remember, ...
                   │                        │
                   │                        ▼
                   │              post_agent_message("munin", RESULT/ERROR)
                   ▼
       fetch_agent_messages("munin") ← Munin polls
```

## Free-tier flow: forged tools evolving across sessions

Session 1:
1. Munin gets a task, decides it needs a new tool.
2. `tool_forge(spec="detect kerberoastable")` → LLM generates Python.
3. AST guard passes → sandbox exec passes → written to
   `munin/generated/gen__detect_kerberoastable.py`.
4. Registered into MCP + persisted to `procedural` table.
5. **`git_persist.queue_commit(...)`** — the async worker adds the file to a
   branch `munin/session-<id>` and pushes.
6. Session ends. Human sees commit, reviews the tool, merges to `main` if they
   like it.

Session 2 (whether `main` was updated or not):
1. Runner checks out repo (has the file if merged).
2. State artifact restores `shared_state.sqlite` (has the `procedural` row).
3. Munin starts → `registry.rehydrate()` re-imports the tool → available immediately.
4. Munin uses the same tool without re-forging.

## Free-tier flow: soul evolution

1. Munin observes a pattern across sessions.
2. Calls `soul_propose_edit(path="principles.md", new_content="...", rationale="...")`.
3. When `MUNIN_AUTO_PR=1`:
   - Writes the file to `soul/principles.md` on branch `soul-proposal/principles-<sha12>`.
   - Commits with rationale in the message.
   - Pushes.
   - Runs `gh pr create` with a body summarizing the change.
4. Human sees PR, reviews rationale, merges (or closes).

Merged PRs are picked up in the next session's checkout — Munin's identity
literally evolves through human-approved patches.

## Configuration reference

Env vars added by the persistence layer:

| Variable | Default | What it controls |
|---|---|---|
| `MUNIN_DB_URL` | `""` | `libsql://...` for Turso; empty = local file (default) |
| `MUNIN_DB_AUTH_TOKEN` | `""` | Turso database token, kept separate from the URL |
| `MUNIN_AUTO_COMMIT` | `0` | `1` to auto-commit forged artifacts to git |
| `MUNIN_AUTO_PR` | `0` | `1` to open PRs on `soul_propose_edit` (requires `gh` CLI) |
| `MUNIN_GIT_USER` | `munin-bot` | commit author name |
| `MUNIN_GIT_EMAIL` | `munin-bot@users.noreply.github.com` | commit author email |
| `MUNIN_GIT_BRANCH` | *(unset)* | target branch for auto-commits; unset = current |

Workflow inputs added:

| Input | Default | What it does |
|---|---|---|
| `persist_state` | `true` | roll `shared_state.sqlite` between sessions via artifact |

Workflow permissions added:

```yaml
permissions:
  contents: write        # commit + push
  pull-requests: write   # open soul-proposal PRs
```

## Migration path — free → Turso

When you want stronger persistence and concurrent sessions:

1. Sign up at <https://turso.tech> (free tier).
2. Create a database. Grab the URL (`libsql://xxxx.turso.io`) and auth token.
3. Set `MUNIN_DB_URL=libsql://xxxx.turso.io` as a repo secret.
4. Set the token as a second repo secret: `MUNIN_DB_AUTH_TOKEN=<token>`.
5. The official `libsql` package is declared in `pyproject.toml`.
6. First run: `poetry run munin mcp` will see the URL and go remote. Local
   file `data/shared_state.sqlite` becomes irrelevant.
7. Optionally seed the Turso DB with your last artifact:
   `sqlite3 data/shared_state.sqlite ".dump" | turso db shell munin`.

No code changes required. Rollback is symmetric: unset `MUNIN_DB_URL`.

## Things that still won't survive a runner

- **In-flight tool calls**: if a scan is running when the 55-min cap hits, the
  subprocess is killed. The wake item is left claimed but not completed. The
  next session's runner will see it as stale after 30 min and can re-claim.
- **Public tunnel URL**: temporary by design. Every session gets a new one.
- **Frontend state**: the Next.js frontend has its own localStorage; that's
  the user's browser, not Munin's problem.

## Non-goals (deliberate)

- **Sharing state between concurrent sessions on free tier**: last-writer
  wins on the artifact. If you need concurrency, go Turso.
- **Fine-grained access control on soul edits**: any user with write access
  to the repo can merge a soul PR. Add branch protection + CODEOWNERS if you
  need controls.
- **Real-time messaging between Munin and the operator during a session**:
  MCP + the frontend handle this; not persistence's job.

## Where the code lives

| Path | Purpose |
|---|---|
| `munin/mcp/persistence.py` | Storage backend abstraction (SQLite ↔ libsql) with `rowcount`, comment-safe splitter |
| `munin/mcp/git_persist.py` | Async commit worker; `queue_commit` API; retry with rebase; up-to-3 backoff |
| `munin/mcp/tools/forge_tool.py` | Wires `commit_forged_tool` after successful forge |
| `munin/mcp/tools/graph_forge_tool.py` | `graph_get(include_inactive=True)` for describe |
| `munin/mcp/tools/diagnostics_tool.py` | `munin_diagnostics` (quick / deep / paranoid) |
| `munin/mcp/tools/munin_tools.py` | `soul_propose_edit` (with PR), `subagent_trace`, `deactivate_generated_tool` |
| `munin/subagents/runner.py` | Wake-based forgers persist; EXITING race window closed; RESULT overflow → artifact |
| `munin/subagents/base.py` | `build_tool_catalog` loads gen__ tools; `_make_wake_tools` delegates to Orchestrator |
| `munin/mcp/main.py` | SIGTERM → `git_persist.flush`; auth middleware captured once; orphan-kill in `main()` |
| `.github/workflows/live-session.yml` | Restore + upload artifact steps; permissions; Kali container |

---

## Are forged subagents + tools ACTUALLY invokable?

This is the load-bearing question of the whole system. The end-to-end chain
is now verified — and there's a first-class probe (`munin_diagnostics
mode=paranoid`) that runs it live before any demo.

### The chain

```
Munin (ReAct)
  │
  │ 1. list_subagent_tools()  → includes "forged" category with every gen__* row
  │
  ├─ 2. tool_forge(spec="…")
  │      • LLM writes Python
  │      • sandbox AST + exec passes
  │      • script written to munin/generated/gen__foo.py
  │      • registry.register()  →  procedural table + attached to MCP
  │      • git_persist.queue_commit()  →  async push to branch
  │
  ├─ 3. graph_forge(name="specialist",
  │                  tool_whitelist=["gen__foo", "ldap_search", "post_agent_message"])
  │      • LLM refines the system prompt
  │      • state.graph_register()  →  generated_graphs table
  │
  ├─ 4. munin_wake(subagent="specialist", task_json={...})
  │      • try_claim_spawn_slot   →  atomic under BEGIN IMMEDIATE
  │      • Orchestrator._spawn_runner  →  detached subprocess
  │      • presence: SPAWNING → RUNNING
  │
  ├─ 5. runner subprocess
  │      • _load_subagent("specialist")  →  reads generated_graphs (active=1 only)
  │      • _ForgedGraphRunner wraps it with ReActSubagentBase
  │      • build_tool_catalog(state, allowed_tools)
  │        └─ LOADS gen__foo FROM procedural  (this was broken before — key fix)
  │      • ReAct loop:
  │          step 0 → PROGRESS msg "step 0: ldap_search {...}"
  │                 → execute ldap_search
  │          step 1 → PROGRESS msg "step 1: gen__foo {...}"
  │                 → execute gen__foo
  │          step 2 → LLM emits final content
  │      • post RESULT (or ERROR) to agent_messages
  │      • runner exits after sleep_after_idle
  │
  └─ 6. Munin polls fetch_agent_messages / subagent_trace
       and sees the RESULT + full iteration history
```

### What was broken and now works

| Was | Now |
|---|---|
| `munin_wake` only enqueued — no subprocess spawn | Orchestrator.wake spawns AND is idempotent |
| Two rapid wakes → two subprocesses racing | `try_claim_spawn_slot` atomic in SQLite |
| Runner idle-timeout dropped wakes arriving in the closing window | Runner marks EXITING + last claim + retry |
| Wake-based `tool_forge` didn't persist to registry | `_WrappedToolForge` calls `register_state_only` + commits |
| Wake-based `graph_forge` didn't persist to state | `_WrappedGraphForge` calls `state.graph_register` |
| Forged subagent couldn't invoke gen__* tools | `build_tool_catalog` loads them on-demand from `procedural` |
| `graph_drop` cosmetic — dropped graphs still wake-invocable | `graph_get(active=1)` filter |
| Subagent returned `ok=True` on LLM error / max_iter / empty | `subagent_ok` tracked; `RESULT` vs `ERROR` selected honestly |
| RESULT body truncated to 6000 bytes → invalid JSON | Overflow → `data/wake_artifacts/wake_<id>.json` + pointer message |
| Diagnostics only listed binaries | Full probe of every subsystem + `paranoid` E2E chain |

---

## Live observability of subagent iterations

The frontend `Agents → Live trace` panel (component: `SubagentTrace.tsx`)
gives the human a real-time view of what a subagent is doing without
interfering. Two-column layout in the AgentsPanel — click any agent in the
presence table on the left, its trace opens on the right.

**Backend** — `subagent_trace(subagent, since_id, include_messages, limit)`:

- `episodic_since(agent, since_id, limit)` — SQL `WHERE id > ?` + `ORDER BY id ASC`.
  Truly incremental: no lost events even in the middle of a long-running
  subagent's history.
- `messages_from_sender_since(sender, recipient, since_id)` — sender-filtered
  in SQL, not in Python. Doesn't lose messages in a chatty system where
  many subagents share the same recipient.
- `presence` snapshot for the status pill.

**Frontend** — polls every 1.5s with `since_id` incremental (append-only
stream). Uses `useRef` for the counters so the interval isn't torn down
between ticks — fixes a subtle bug where the effect deps caused a request
storm at RTT frequency.

**Subagent side** — before every tool call, the ReAct loop posts a
`PROGRESS` message to `agent_messages` with the tool name + args + step
number. The frontend renders these interleaved with episodic events.

The human observes but doesn't direct — the subagent is autonomous once
awakened. If the operator wants to intervene, they cancel the wake or ask
Munin (in the chat) to reason about what they've observed.

---

## Diagnostics tool — pre-flight before demo

`munin_diagnostics(mode="quick" | "deep" | "paranoid")` — see the tool doc
and the README section 7 for the full list of probes.

The `paranoid` mode is the load-bearing probe: it forges a throwaway
`gen__echo_text_<ts>` tool, forges an `e2e_probe_<ts>` graph that uses it,
wakes the subagent, polls for the RESULT message for up to 45s, then
cleans up (drops the graph AND deactivates the tool). If it returns
`ok=true`, the entire forge → registry → wake → runner → messages pipeline
is verified live.

Recommended cadence:

- `quick` (~500ms): every session start, in the workflow post-setup step.
- `deep` (~2-5s): manually before a demo to verify LDAP + Hugin.
- `paranoid` (~30-60s): once per repo change that touches forge / wake code.
