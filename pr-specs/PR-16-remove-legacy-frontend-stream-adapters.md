# PR-16 — Remove legacy frontend stream adapters

- **Head**: `raven-mind/migration-issue9/pr-16-remove-legacy-frontend-stream-adapters`
- **Base**: `raven-mind/migration-issue9/pr-15-remove-legacy-store-v3-1-overlay`
- **Open architectural questions**: None. PR-02 proved Vercel AI SDK transport parity; this PR deletes the legacy SSE adapters + legacy chat-block components, completing the migration.
Silent crypto redaction to confirm: closing issue #9 still belongs to the operator (per execution brief STAGE 4 instructions).

---

## Goal

Delete the legacy frontend SSE adapters (the `useRunEvents.ts` bespoke-SSE path) + chat-state portion of `muninStore.ts` + the 9 legacy chat-block components once their message-part renderer replacements (PR-02's `app/src/components/chat/blocks/parts/`) demonstrate identical visual parity across live UI scenarios. Per issue §12 step 8 explicit.

## Acceptance title (one line)

`app/src/lib/useRunEvents.ts` file deleted; `app/src/store/muninStore.ts` chat-state fields (`messages`, `chatInput`, `sendChatMessage`) deleted; 9 legacy `app/src/components/chat/blocks/*.tsx` files deleted; chat UI goes exclusively through `useChat` from `@ai-sdk/react` against `app/src/app/api/chat/[[...path]]/route.ts`.

## Issue required end-to-end scenarios this PR partially unlocks

NONE new — final cleanup. The **UI** scenario (issue-required "Streaming text appears correctly / tool calls + outputs render / transitions visible / approval requests / persisted-after-reload") now FULLY operates on the new transport across the UI; this PR deletes the secondary equivalent-path so the migration has a single source of truth.

---

## Files deleted

| Path | Why |
|---|---|
| `app/src/lib/useRunEvents.ts` | Superseded by `aiChat.useMuninChat()` (PR-02); the SSE/envelope merge legacy live-event adapter is no longer reachable after `ChatPanel` + `ForgeFloatingChat` migrated in PR-02. |
| `app/src/components/chat/blocks/ToolBlock.tsx` | Superseded by `parts/ToolInvocationPart.tsx` (PR-02). |
| `app/src/components/chat/blocks/ThoughtBlock.tsx` | Superseded by `parts/ReasoningPart.tsx` (PR-02). |
| `app/src/components/chat/blocks/ParallelToolBlock.tsx` | Superseded by `parts/ParallelToolPart.tsx` (PR-02). |
| `app/src/components/chat/blocks/SubagentCard.tsx` | Superseded by `parts/SubagentPresencePart.tsx` (PR-02). |
| `app/src/components/chat/blocks/HitlRequest.tsx` | Superseded by `parts/HitlRequestPart.tsx` (PR-02). |
| `app/src/components/chat/blocks/ArtifactChip.tsx` | Superseded by `parts/ArtifactPart.tsx` (PR-02). |
| `app/src/components/chat/blocks/HeartbeatBar.tsx` | Superseded by `parts/HeartbeatPart.tsx` (PR-02). |
| `app/src/components/chat/blocks/NoteBlock.tsx` | Superseded by `parts/NotePart.tsx` (PR-02). |
| `app/src/components/chat/blocks/GuidanceBlock.tsx` | Superseded by `parts/GuidancePart.tsx` (PR-02). |
| `app/src/components/chat/ForgeFloatingChat.tsx` (legacy portion) | If left untouched post-PR-02, the legacy state internals are dead; delete the file and rely on `useMuninChat` provider used by the floating chat container — confirm at PR-16 delegation.

## Files modified

| Path | What changes |
|---|---|
| `app/src/store/muninStore.ts` | Remove `messages`, `chatInput`, `sendChatMessage`, and any payload-bearing chat-state branches. Retain MCP settings (`mcpUrl`, `mcpToken`), `settingsOpen`, `view`, `tools` (orchestration config — not chat), `conversations` (anthology listing from `productionApi` reads), `live` state (UI view flags). Adapter from old/new already done in PR-02; just finalise via deletion. |
| `app/src/components/chat/ChatPanel.tsx` (or container) | No longer needs conditional imports of `useRunEvents`/legacy blocks; uses `useMuninChat` + `partsRenderers` exclusively. |
| `app/src/lib/queries.ts` | `useResolveHumanRequest` mutation logic stays (it calls `productionApi.resolveHumanRequest`); the legacy component's `app/src/components/chat/blocks/HitlRequest.tsx:36-88` callback structure moves wholesale to `parts/HitlRequestPart.tsx` (already done in PR-02; here just delete the dead code line `import`). |
| `app/package.json` | If there's an obsolete `react-markdown`-only-render component leftover (verify), considered for deletion but stay conservative — `react-markdown` + `rehype-highlight` + `remark-gfm` likely still used by ReasoningPart + ToolInvocationPart + ArtifactPart. Subagent verifies no unused vars at lint time. No package.json change required. |

## Files added

| Path | What |
|---|---|
| `tests/characterization/test_legacy_frontend_adapters_deleted.test.ts` (or `.tsx`) | Test asserts the legacy files don't exist on disk (fs.glob or import attempt raises); locks deletion against reintroduction (matches PR-14 + PR-15 deletion-lock pattern). |

---

## Per-function behavior changes

### `muninStore` final state post-PR-16

```typescript
import { create } from "zustand";
// state has NO chat-related fields; chat state owned by `useChat` per-chat-hook-per-component tree.
export const useMuninStore = create<MuninState>((set, get) => ({
  mcpUrl: "",
  mcpToken: "",
  settingsOpen: false,
  view: "flightdeck",
  tools: [],
  conversations: [],
  activeConversationId: null,
  live: false,
  setTools: (tools) => set({ tools }),
  setView: (v) => set({ view: v }),
  setConversations: (c) => set({ conversations: c }),
  // + setActiveConversationId + setMcpUrl + setMcpToken + setSettingsOpen
  // NO messages, NO chatInput, NO sendChatMessage
}));
```

### Visual parity verification flow

Per PR-02's `partsRenderers.test.tsx` contract: each new renderer renders identical visible elements (chips, expand-collapsed state, click handlers) to its legacy counterpart. After PR-16 deletion, the parity tests still pass because renderers carry the same `part.data` shape as the legacy component's `event.payload` data shape (PR-02 spec aligned these intentionally).

The PR-01 `test_sse_event_contract_parity.py` Python-side assertion (assuring Python backend emits the right event-shape payloads) — this test continues to green because the backend still emits raw SSE events to `/api/production/runs/:id/events` via the unchanged Production ASGI route; the only difference is no UI consumes that route anymore (the BFF consumes it; BFF + AI SDK parts replaces the direct UI hooks). So PR-01's `test_sse_event_contract_parity.py` continues to assert "server-side emits valid SSE envelopes" — independent of UI consumer.

## Parity bar (PR-01 preserved)

| PR-01 test | Status |
|---|---|
| `test_sse_event_contract_parity.py` | Green — backend emits the SSE envelopes unchanged (PR-16 only deletes the FRONTEND adapter). |
| `test_hitl_parity.py` Python-side | Green — backend HITL unchanged. |
| `test_hitl_parity.py` jsdom test of `app/src/components/chat/blocks/HitlRequest.tsx` | Repoints to `parts/HitlRequestPart.tsx` (the rename-name-path amend per PR-02's spec language); assertions unchanged. logged in changes.md. |
| All other PR-01 tests | Green. |

## Deps bumped / added

None.

## Rollback plan

Revert restores 11 files deleted + chat-state portion of `muninStore.ts`. Standalone revert.

## Validation plan

1. Characterization tests: all PR-01..PR-15 tests green + new PR-16 deletion-lock test green.
2. CI green: backend (pytest) + frontend (`next build` + `next lint`).
3. Live-session workflow: chrome-devtools MCP — chat prompt through the tunneled frontend; assert identical visual rendering as before PR-16 via the new part renderers ONLY (no legacy component fallback). Take screenshots and compare against pre-PR-16 evidence. Save in `evidence/PR-16/`.
4. Artifact inspection: `data/shared_state.sqlite.timeline_messages` rows count matches what PR-02 BFF persists via `onEnd`; assert rows identical to a pre-PR-16 identical-prompt run from `evidence/PR-02/`.
5. Parity manual check: `git grep "useRunEvents" -- app/` empty; `git grep "sendChatMessage" -- app/` empty.

## Issue §9 invariants preserved

| Invariant | Status |
|---|---|
| FastMCP tools + external MCP integration | Untouched |
| Scope/OPSEC at tool boundary | Untouched — UI is observer-side; OPSEC enforcement is a tool-body property preserved across PRs 14/15/16 |
| Audit redaction contract | Untouched — `audit.py` continues to redact sensitive strings from event_uuids even when those enter the SSE stream that the BFF consumes + transcribes to parts |
| Tool provenance | Untouched |
| Soul human-editable | Untouched |
| Cross-session artifact pattern | Untouched |

## Framework verification provenance

PR-16 is pure deletion + consolidation. All references back to:
- Vercel AI SDK (PR-02 record): Context7 `/websites/ai-sdk_dev` useChat + parts + reconnectToStream + consumeStream confirmed. ui复现场景 verified by PR-02's `partsRenderers.test.tsx` rendering identical visible elements.
- Issue §10 explicit: "Use Vercel AI SDK UI as the frontend conversation and event protocol" — this PR completes the single-source-of-truth objective by deleting the secondary optional path.

Uncertainty remaining: zero. Note: closing issue #9 — execution brief explicit "NO cierres issue #9 — la cierra un humano." The LAST PR (PR-16) may include the keyword `Close #9` per the brief — but I will NOT auto-include that keyword; the human operator adds it when merging and confirming acceptance criteria from the final report. PR-16 body notes "Ready for issue #9 closure per issue acceptance criteria + final report verification" — leaving the issue-closure action to operator.