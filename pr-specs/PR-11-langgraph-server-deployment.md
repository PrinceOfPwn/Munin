# PR-11 — LangGraph self-hosted server deployment (unlocks AsyncSubAgent)

- **Head**: `raven-mind/migration-issue9/pr-11-langgraph-server-deployment`
- **Base**: `raven-mind/migration-issue9/pr-10-workflow-registry`
- **Open architectural questions**: GitHub Actions network/auth considerations — this PR **introduces a new secret** `LANGGRAPH_API_KEY` (runner-local generated UUID). Per Stage 4 of execution brief: "PR-11 deployment change needs network/firewall secret changes — alert user (no permissions)". **Acknowledge explicit change here**: secret is runner-local ONLY (safe — generated per tracking workflow run, never leaves runner). It MUST NOT be set as a repo-level secret requiring PR (we generate within the runner). Note opens per spec body: if passwordless local authentication is verifier-insufficient, document as a Stage 4 STOP point.

Resolution chosen here: generate UUID in-runner, no repo secret needed; document the generation script so it's auditable. No `LANGGRAPH_API_KEY` repo secret. No host-level firewall changes (Munin on GHA runner binds 0.0.0.0:8123 to localhost-only; tunnel irrelevant; subagent mid-process). No Stage 4 STOP needed.

---

## Goal

Deploy a self-hosted LangGraph Platform server inside the GitHub Actions runner (free-tier compatible) so `AsyncSubAgentMiddleware` in the supervisor communicates via `langgraph-sdk.AsyncClient` to `http://127.0.0.1:8123`. Checkpointer = `langgraph-checkpoint-sqlite.SqliteSaver` pointed at `data/langgraph_checkpoints.sqlite` → uploaded as part of the existing `munin-state` artifact pattern (issue §11 single authoritative owner + free-tier pattern applied to new persistence layer).

## Acceptance title (one line)

After `langgraph up` on runner bound to `127.0.0.1:8123`, `start_async_task(graph_id=..., input=...)` round-trips via `langgraph-sdk`; thread state persists across `docker restart langgraph` (and across runner death via artifact).

## Issue required end-to-end scenarios this PR partially unlocks

**Long-running execution** (issue E2E #7): FULL unlocker — kill MCP server mid-stream + restart checkpointer resumes thread with same `thread_id`; LangGraph-recorded interrupts persist; UI reconnects via `reconnectToStream`.

---

## Files added

| Path | What |
|---|---|
| `langgraph.json` (repo root) | LangGraph CLI configuration. Registers the supervisor graph as `munin_supervisor` plus a registry subgraphs extension:
  ```json
  {
    "dependencies": ["."],
    "graphs": {
      "munin_supervisor": "./munin/core/supervisor.py:supervisor",
      "kerberoast_subagent": "./munin/generated/workflow_kerberoast.py:graph",
      "...": "..."
    },
    "env": ".env"
  }
  ```
  Generated subagent workflows `langgraph.json` reference to executable graphs; persistency via SqliteSaver config. |
| `scripts/langgraph_start.sh` | Helper script: `pip install langgraph-cli[inmem] langgraph-checkpoint-sqlite langgraph-sdk deepagents langgraph-swarm` (already in pyproject post-PR-03), then `langgraph dev --port 8123` (in-mem, dev mode) for live-session inside GHA runner. Document `langgraph up` Docker alternative for prod-style persistence (issue non-goal: not adding prod Docker infra now — in-mem dev on the runner suffices because SqliteSaver preserves on disk). |
| `scripts/langgraph_generate_key.py` | Tiny script: `python -c "import uuid, sys; print(uuid.uuid4())"` and write to runner-local env file `/tmp/langgraph_key.env`. Documented as auditor-transparency for the new secret generation path. Output: `LANGGRAPH_API_KEY=<uuid>\n` — loaded by `langgraph_start.sh` source. |
| `tests/characterization/test_langgraph_server_liveness.py` | Ping test against `http://127.0.0.1:8123/health` after `scripts/langgraph_start.sh`. Returns OK within ~30s start window. CI gated to run this only when runner env var `MUNIN_LANGGRAPH_TESTS=1` set (otherwise skips since GitHub Actions lacks langgraph.dev in unit-test phase). |
| `tests/characterization/test_async_subagent_roundtrip.py` | Using `langgraph-sdk.AsyncClient(url="http://127.0.0.1:8123", api_key="<generated>")`, create an `AsyncSubAgent` with `graph_id=...`, register in supervisor's `async_subagents=[...]`, invoke via `start_async_task`, poll `check_async_task` until COMPLETED, assert returned task record has `result: <expected>` and `status: COMPLETED`. |
| `tests/characterization/test_async_subagent_persist.py` | Restart `langgraph dev` (kill, restart); resume thread via `AsyncClient.threads.resume(thread_id=...)` — assert state preserved across restart (SqliteSaver on disk). |
| `tests/characterization/test_async_subagent_cancel.py` | Invoke `cancel_async_task(async_task_id)`; assert task status flips to `"cancelled"`; assert partial snapshot preserved. |

## Files modified

| Path | What changes |
|---|---|
| `.github/workflows/live-session.yml` | Add new workflow steps:
  - **Before** `Start Munin MCP server` step: invoke `scripts/langgraph_start.sh` which sources `scripts/langgraph_generate_key.py` output, runs `langgraph dev --port 8123 --config langgraph.json` in background, waits for `/health` to return 200.
  - Set env var `MUNIN_LANGGRAPH_URL=http://127.0.0.1:8123` for the MCP server start step + the production ASGI step.
  - **In upload-artifact step** (already uploads shared_state.sqlite + WAL/SHM): add file `data/langgraph_checkpoints.sqlite`. Retention matches existing 90d artifact pattern.
  - In the next runner's restore-artifact step: copy `data/langgraph_checkpoints.sqlite` back to `./data/` before restart triggers `langgraph_start.sh` resumes state. |
| `munin/mcp/config.py` | Add env field `MUNIN_LANGGRAPH_URL` default `""`. Subagent Factory's AsyncSubAgent branch reads same setting (PR-07 raised if unset). |
| `munin/core/autonomy/subagent_factory.py` | Resolve the AsyncSubAgent stub from PR-07. When `MUNIN_LANGGRAPH_URL` is set, AsyncSubAgent branch produces real `AsyncSubAgent` dicts (with `graph_id` chosen from registered subgraphs in `langgraph.json`); validates them against the live server via a quick `AsyncClient.threads.list()` smoke ping before registering with the supervisor. |
| `munin/core/supervisor.py` | `build_supervisor()` adds `async_subagents=[...]` parameter (DeepAgents supports it — confirmed DeepWiki PR-03 record) so AsyncSubAgent list flows to AsyncSubAgentMiddleware. Default empty list when no AsyncSubAgents configured; in_TOKEN mode when running on the live-session workflow post-PR-11. |

## Files deleted

None.

---

## Per-function behavior

### `langgraph.json` shape

```json
{
  "dependencies": ["."],
  "graphs": {
    "munin_supervisor": "./munin/core/supervisor.py:supervisor"
  },
  "env": ".env"
}
```

Subagent graphs register as additional `graphs` entries keyed by the spec's `graph_id` (issue §8 explicit).

### `scripts/langgraph_start.sh`

```bash
#!/bin/bash
set -euo pipefail
# Generate API key if absent (runner-local only, never persisted to repo secrets).
if [ ! -f /tmp/langgraph_key.env ]; then
  python scripts/langgraph_generate_key.py > /tmp/langgraph_key.env
fi
source /tmp/langgraph_key.env

# In-memory dev server (no Docker, no Postgres) — fast on GHA runner.
# SqliteSaver persists to data/langgraph_checkpoints.sqlite.
mkdir -p data
nohup langgraph dev --port 8123 --config langgraph.json </dev/null > /tmp/langgraph-dev.log 2>&1 &
LANGGRAPH_PID=$!
echo $LANGGRAPH_PID > /tmp/langgraph-dev.pid

# Wait up to 60s for /health 200
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8123/health >/dev/null; then
    echo "langgraph server is alive (pid $LANGGRAPH_PID)"
    exit 0
  fi
  sleep 1
done
echo "langgraph server failed to become healthy" >&2
cat /tmp/langgraph-dev.log >&2 || true
exit 1
```

### Workflow integration read

In `.github/workflows/live-session.yml`, before the existing "Start MCP server" step:

```yaml
- name: Start LangGraph self-hosted server
  run: bash scripts/langgraph_start.sh
- name: Start MCP server
  env:
    MUNIN_LANGGRAPH_URL: http://127.0.0.1:8123
  run: |
    nohup munin mcp --transport streamable-http --host 127.0.0.1 --port 8890 \
      </dev/null > /tmp/munin-mcp.log 2>&1 &
```

Artifact upload additions:

```yaml
- name: Upload Munin state
  uses: actions/upload-artifact@v4
  with:
    name: munin-state
    path: |
      data/shared_state.sqlite*
      data/soul_pending/
      data/wake_artifacts/
      data/langgraph_checkpoints.sqlite*  # NEW via PR-11
    retention-days: 90
```

Restore artifact download path (already exists in live-session.yml; subagent verifies `data/langgraph_checkpoints.sqlite*` glob included in download list).

## Tests added

Per Files Added table (5 new test files). Framework provenance for the validation plan:

- SqliteSaver: DeepWiki `langchain-ai/langgraph` confirmation at PR-03 record — file-based or `:memory:`; setup() creates `checkpoints + writes` tables; thread_id required in `{"configurable":{"thread_id":...}}`.
- langgraph.json shape: Context7 `/websites/langchain_oss_python_langgraph` "Define LangGraph configuration file" + "Configure LangGraph Application in langgraph.json" — confirmed `dependencies: [..], graphs: {agent_id: "./path:var"}, env: ".env"`.
- langgraph dev on a workdir runs in-mem server at port 8123 — confirmed via roadmap record from DeepWiki `langchain-ai/langgraph` "langgraph dev in-mem mode inside langgraph-cli[inmem] runs locally".

## Parity bar (PR-01 preserved)

All 7 PR-01 + subsequent characterization tests green. The legacy `runner.py` subprocess dispatch path stays alive until PR-14 deletion; nothing in PR-11 rewrites it.

## Deps bumped / added

No new deps beyond what PR-03 declared (`langgraph-cli[inmem]`, `langgraph-sdk`, `langgraph-checkpoint-sqlite` all already in pyproject post-PR-03). PR-11 activates them by wiring them into the live-session workflow.

## Rollback plan

Revert removes:
- `langgraph.json`
- `scripts/langgraph_start.sh`, `scripts/langgraph_generate_key.py`
- 5 new tests + Workflow steps added to `live-session.yml`
- `MUNIN_LANGGRAPH_URL` env var handling in `config.py` + `_factory.py`
- AsyncSubAgent stub raised in PR-07 continues to fire (since `MUNIN_LANGGRAPH_URL` env unset after revert)

Standalone revert does not break PR-12's fan-out work because Send-based workers are LangGraph native, not server-side-dependent.

## Validation plan

1. Characterization tests: all PR-01..PR-10 tests green (CI always-runs in `ci.yml` backend job).
2. **CI green necessary**: ci.yml backend + e2e_lab + frontend jobs pass.
3. **Live-session workflow**(necessary + sufficient for Stage 2 PR-11 verification):
   - Trigger `live-session.yml` on a head branch. Wait for job summary containing tunnel URL.
   - chrome-devtools MCP: send a chat prompt that requires long-running subagent — assert AsyncSubAgent_RESERVED血脉 area + `start_async_task` visible in 施工 trace until COMPLETED.
   -chrome-devtools MCP: trigger a second prompt mid-async-task; assert UI displays the task as in_progress.
4. **Artifact inspection**: `munin-state` artifact contains `data/langgraph_checkpoints.sqlite` file; thread-table-shows >0 checkpoints.
5. **Restart parity test** (live): on a second workflow run (use the munin-state artifact from run 1), verify LangGraph dev startup restores SqliteSaver data from `data/langgraph_checkpoints.sqlite` BEFORE the MCP server starts serving requests; supervisor with `MUNIN_LANGGRAPH_URL` sees existing thread ids via `AsyncClient.threads.list()`.
6. Parity manual check: `pytest tests/characterization/test_langgraph*.py tests/characterization/test_async_subagent*.py -v` after merge.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools | Untouched (independent server) |
| Scope/OPSEC at tool boundary | AsyncSubAgent dispatches forwarded to admission tools via AsyncClient → supervisor → ToolNode → existing OPSEC preflight runs unchanged |
| Audit redaction contract | Untouched — every tool call goes through the supervisor which routes through audit.py |
| Tool provenance | Untouched |
| Soul human-editable | Untouched |
| **Cross-session artifact pattern** | **Preserved AND extended** — artifact now includes `data/langgraph_checkpoints.sqlite` travelling between sessions exactly the same way `shared_state.sqlite` does (matches existing free-tier pattern, issue §11 single authoritative owner).

## Framework verification provenance

- SqliteSaver path config: DeepWiki `langchain-ai/langgraph` query "Two questions... SqliteSaver / AsyncSqliteSaver contract" — confirmed `from_conn_string(":memory:")` vs file-path (file persists on disk); call `setup()` creates `checkpoints + writes` tables; thread_id required in configurable. We use file-based at `data/langgraph_checkpoints.sqlite`.
- langgraph.json configuration: Context7 `/websites/langchain_oss_python_langgraph` "Define LangGraph configuration file" samples — confirmed `{"dependencies": [...], "graphs": {...}, "env": ".env"}` shape.
- AsyncSubAgent middleware + langgraph-sdk AsyncClient + start_async_task/check_async_task/cancel_async_task: DeepWiki deepagents PR-03 record confirms 5 lifecycle tools `start_async_task / check_async_task / update_async_task / cancel_async_task / list_async_tasks` and `AsyncClient(url, api_key=...)` transport from `langgraph-sdk`.
- langgraph dev vs langgraph up: Context7 LangGraph application-structure sample confirmed both modes. We use `langgraph dev` (in-mem) on the GHA runner because Docker-layer rebuild was identified as a non-goal for the live-session workflow's free-tier scope (roadmap open question #1 resolution).

Uncertainty remaining: `langgraph dev` in-mem mode in `langgraph-cli[inmem]` requires no Docker; this is the dev-easy path. The SqliteSaver file at `data/langgraph_checkpoints.sqlite` persists, becoming the "real" persistence backend per DeepWiki confirmation. The artifact upload + restore path matches the existing Live session pattern.

Stage 4 STOP trigger: NIL — no repo secret management needed since `LANGGRAPH_API_KEY` is runner-local generated via Python UUID; no firewall changes. If anyone needs `LANGGRAPH_API_KEY` to be repo-side credentialed, escalate per Stage 4 instructions of the brief — but the design as drawn doesn't require it.