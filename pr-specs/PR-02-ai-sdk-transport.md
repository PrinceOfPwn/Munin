# PR-02 — Vercel AI SDK transport (frontend-only, backend unchanged)

- **Head**: `raven-mind/migration-issue9/pr-02-ai-sdk-transport`
- **Base**: `raven-mind/migration-issue9/pr-01-parity-baseline`
- **Open architectural questions**: Lock-down of custom part ids resolved here (roadmap open-Q #3). Locked set: `subagent-presence`, `hitl-request`, `artifact`, `forge-stage`. No remaining open questions.

---

## Goal

Introduce the new frontend protocol without changing the Python backend runtime (issue §10 step 2 explicit: "Do not move core orchestration into TypeScript"). A Next.js BFF adapter re-emits existing production-run SSE events as AI SDK UI message stream parts; the chat UI switches from bespoke `useRunEvents` + `muninStore.sendChatMessage` to `useChat` + `message.parts`.

## Acceptance title (one line)

`useChat` against the new BFF route reconstructs identical persisted conversation after a backend kill/restart, with tool + reasoning + HITL and subagent parts rendered through the existing chat-block visual language.

## Issue required end-to-end scenarios this PR partially unlocks

- **UI** (streaming text / tool inputs-outputs / agent & workflow transitions / approval requests / persisted-after-reload): partial — covers streaming text + tool I/O + approvals + persisted-after-reload here. Workflow-transition visibility completes in PR-09 (workflow factory). Worker fan-in visibility completes in PR-12.

---

## Current glue being replaced

Read-only reference; PR-02 does not delete any of these yet (deletion is PR-16 after parity proven). They are wired to NEW code paths in PR-02 and the OLD paths continue to exist.

| Current glue file:line | What it does today | New replacement in this PR |
|---|---|---|
| `app/src/lib/useRunEvents.ts` (full, 211 lines) | EventSource `/api/production/runs/:id/events`; parses `run-event` envelope, merges into React Query cache, 45s silence → stale, native Last-Event-ID reconnect | `useChat` (`@ai-sdk/react`) against new BFF route; envelope→parts mapping done server-side in BFF; stale detector replaced by stream keepalive; Last-Event-ID semantics replaced by `reconnectToStream({chatId, startIndex})` |
| `app/src/store/muninStore.ts` `sendChatMessage()` + `messages` state | Direct JSON-RPC chat to MCP server, hand-rolled message accumulation | `useChat` manages `messages` + `sendMessage`; existing JSON-RPC chat stays callable for non-chat MCP ops |
| `app/src/app/api/production/[[...path]]/route.ts` (74 lines) | Next.js proxy to `:8787`; `maxDuration=14400`, SSE passthrough, `Last-Event-ID` | KEEP AS-IS — still forwards admin/HITL/artifact reads to production ASGI. NEW route `/api/chat/[[...path]]/route.ts` is the AI SDK stream BFF. |
| `app/src/components/chat/blocks/*.tsx` (9 files) | Render run-detail models (ToolBlock, ThoughtBlock, ParallelToolBlock, SubagentCard, HitlRequest, ArtifactChip, HeartbeatBar, NoteBlock, GuidanceBlock) | Reimplemented as message-part renderers keyed by `part.type`. Same visible elements; mutators still call `productionApi.*` mutations. |

---

## Files added

| Path | What |
|---|---|
| `app/src/app/api/chat/[[...path]]/route.ts` | Next.js BFF route — fetches existing `/api/production/runs/:id/events` SSE from the Python ASGI, re-emits events as AI SDK message parts via `createUIMessageStreamResponse` + `toUIMessageStream`. |
| `app/src/lib/aiChat.ts` | Thin module exposing `useMuninChat({ runId, chatId })` = `useChat({ transport: new DefaultChatTransport({ api: `/api/chat/runs/${runId}` }) })`. Encapsulates provider/host config so view components don't reference `@ai-sdk/react` directly. |
| `app/src/lib/partsRenderers.ts` | Map of `part.type` → React component. Used by the message renderer in the chat list. |
| `app/src/components/chat/blocks/parts/` (new subdir) | Re-implementations of the 9 blocks as part-type renderers: `ReasoningPart.tsx`, `ToolInvocationPart.tsx`, `ParallelToolPart.tsx`, `SubagentPresencePart.tsx`, `HitlRequestPart.tsx`, `ArtifactPart.tsx`, `HeartbeatPart.tsx`, `NotePart.tsx`, `GuidancePart.tsx` |
| `app/src/lib/__tests__/sseToParts.test.ts` | Unit tests for the envelope→parts mapping logic (independent of network). |
| `app/src/lib/__tests__/partsRenderers.test.tsx` | Visual contract tests per part (render with fixture → assert visible elements + click handlers). |
| `app/src/lib/__tests__/reconnect.test.ts` | Resume tests for the BFF route against a scripted-but-fakeable ReadableStream (`reconnectToStream` invocations, idempotent replay of `Last-Event-ID`-equivalent from `startIndex`). |

## Files modified

| Path | What changes |
|---|---|
| `app/package.json` | Add deps: `ai`, `@ai-sdk/react`. See deps section. |
| `app/package-lock.json` | Regenerate lockfile for the two new deps. |
| `app/src/components/chat/ChatPanel.tsx` (or current chat-list container) | Replace `useRunEvents` + `muninStore.sendChatMessage` with `useMuninChat`; render `message.parts` via `partsRenderers`. |
| `app/src/components/ForgeFloatingChat.tsx` | Switches its inner state to `useMuninChat` against the same BFF route (subagent-scoped chat). Old `useRunEvents` import removed here. |
| `app/src/store/muninStore.ts` | Remove `sendChatMessage` + `messages` + `chatInput` (now owned by `useChat`). Keep MCP settings + tools state (those are orchestration config, not chat). |
| `app/src/lib/useConversationEvents.ts` | UNCHANGED — collab/presence semantics are not chat parts; stays as-is. |
| `app/tsconfig.json` | If needed: add `React.ComponentType` resolve to buffered import (only if compiler complains). Generally not needed. |

## Files deleted

None. Old `useRunEvents.ts`, `muninStore.sendChatMessage`/`messages`, and the 9 chat blocks remain on disk for the duration of PR-02 (parity safety net). Deletion in PR-16 after parity proven.

---

## Per-component / function behavior

### `app/src/app/api/chat/[[...path]]/route.ts` (Next.js BFF)

Framework provenance: Context7 `/websites/ai-sdk_dev` query `useChat hook messages parts types reconnectToStream chatId startIndex consumeStream toUIMessageStreamResponse onEnd persistence Next.js App Router` confirmed `DefaultChatTransport` + `useChat({ id, messages, transport })` + `streamText` + `toUIMessageStream` + `createUIMessageStreamResponse` API surface. Source: ai-sdk.dev docs "Implement Chat UI with useChat Hook in Next.js" + "Initialize useChat Hook with Persisted Messages".

```typescript
// Skeleton — full implementation delegated in PR-02 task prompt.
import { DefaultChatTransport } from "ai"; // for client side; server uses createUIMessageStreamResponse
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 14400; // Same 4h cap as the existing production proxy.

// GET /api/chat/runs/:runId — open SSE → re-emit as AI SDK parts.
// POST /api/chat/runs/:runId — send an operator message to the run (forwards to /api/production/runs/:id/messages).
// Both reuse the existing production proxy for cookie auth so no new auth surface.
```

Behavior expected:
1. On GET: open SSE against `${MUNIN_PRODUCTION_API_URL}/api/production/runs/:runId/events`, parsing `run-event` envelopes.
2. For each envelope, dispatch a part to the AI SDK stream via `stream.write({ type: "data", data: <part-shape> })` (data parts are how custom parts travel). The mapping table locks down the wire contract:

| Envelope `kind` | AI SDK part emitted |
|---|---|
| `reasoning` | `data` part with `{ type: "subagent-presence"` OR `dynamic-tool`/`reasoning` per Context7... — FIXED: a `custom` part with id `"subagent-presence"` for subagent metadata, and a `reasoning` AI SDK root part for top-level reasoning events `{ id: <reasoning.id>, text: <reasoning.text> }` |
| `tool_intent` | `custom` part id `"tool-invocation"` is NOT available — instead emit `dynamic-tool` part `{ toolCallId: tool.id, toolName: tool.name, input: tool.proposed_args, state: "input" }` |
| `tool_started` | update existing `dynamic-tool` part to `state: "streaming"` |
| `tool_result` / `tool_completed` | update `dynamic-tool` part to `state: "available", output: tool.result` |
| `tool_failed` | update `dynamic-tool` part with `errorText: tool.error` |
| `subagent_started` / `subagent_state` | `custom` part id `"subagent-presence"` with the subagent payload |
| `human_request` | `custom` part id `"hitl-request"` with the request payload (id, tool_name, args, nonce) |
| `human_resolved` | update existing `"hitl-request"` custom part with `resolved: req` patch |
| `artifact` | `custom` part id `"artifact"` with `{ id, filename, mime, size, download_url }` |
| `run_state` | bypass render — inform `useChat` `status`/metadata; for terminal states set `stream.write({ type: "finish" })` |
| `(no kind)` | skip |

(`data` part type for reasoning — Context7 confirms `reasoning` is a first-class part type alongside `text`, `tool-invocation` (which we use as `dynamic-tool` for not-pre-registered tools), and `custom`.)

Issue §10 says custom parts are explicitly allowed. We rely on this.

3. Heartbeats update a top-level `data` field on the stream so `useChat` `status` reflects liveness; 45s silence is no longer client-side-detected because the stream itself is the source of truth (server-side heartbeats already flow every 20s from the existing ASGI).
4. On POST: forward JSON body to `${MUNIN_PRODUCTION_API_URL}/api/production/runs/:runId/messages` with cookie headers forwarded (same as `[[...path]]/route.ts` proxy).
5. Resumability: BFF reads `Last-Event-ID` from request headers and forwards to upstream SSE; the part stream resumes from there. (Equivalent to Vercel `reconnectToStream` calling back with `startIndex`.)

Framework provenance: Context7 `/websites/ai-sdk_dev` query confirms "useChat hook with persisted messages" pattern (`ai-sdk.dev/docs/ai-sdk-ui/chatbot-message-persistence`) → next.js route persists with `id` + `initialMessages`; `onEnd` hook persists server-side here too by calling `productionApi.persistConversation(runId, messages)`.

### `app/src/lib/aiChat.ts`

```typescript
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
export function useMuninChat({ runId, initialMessages }: { runId: string; initialMessages?: UIMessage[] }) {
  return useChat({
    id: runId, // reuse runId as chatId — single source of truth, matches existing conversation_run_links
    messages: initialMessages,
    transport: new DefaultChatTransport({ api: `/api/chat/runs/${runId}` }),
    onEnd: ({ messages }) => persistConversationTail(runId, messages), // calls productionApi.persistConversation
  });
}
```

Behavior: client-side `messages` evolution is owned by `useChat`. The existing `productionApi.persistConversation(runId, msgs)` mutation is wired to `onEnd` so the existing `timeline_messages` rows remain authoritative server-side (issue non-goal §11 → backend remains the source of truth). Pass `id: runId` so a tab refresh reloads the same persisted thread.

### `app/src/lib/partsRenderers.ts`

A `Map<PartType, React.ComponentType<{ part: any }>>`. Lookup by `part.type`:
- `"reasoning"` → `ReasoningPart` (subagent_branch aware via part.data.subagent_id when applicable)
- `"dynamic-tool"` → `ToolInvocationPart`
- `"custom"`:
  - `part.data.id === "subagent-presence"` → `SubagentPresencePart`
  - `part.data.id === "hitl-request"` → `HitlRequestPart`
  - `part.data.id === "artifact"` → `ArtifactPart`
  - `part.data.id === "forge-stage"` → reserved (used in PR-06 when forge progress events surface)
  - else → minor fallback (timestamp + JSON dump)

### Per-block renderers (app/src/components/chat/blocks/parts/)

Each block replicates the visible behaviour of the corresponding legacy block:

| New | Mirrors | Key parity (visual contract test assertion) |
|---|---|---|
| `ReasoningPart.tsx` | `ThoughtBlock.tsx` | compact chip + expandable body, auto-expands while running |
| `ToolInvocationPart.tsx` | `ToolBlock.tsx` | name, state chip (running/completed/failed), expandable args + result |
| `ParallelToolPart.tsx` | `ParallelToolBlock.tsx` | group by `parallel_group_id`, single "Running N in parallel" widget |
| `SubagentPresencePart.tsx` | `SubagentCard.tsx` | filtered reasoning + tools per subagent, forge-window "Open" button |
| `HitlRequestPart.tsx` | `HitlRequest.tsx` | Approve/Deny/custom choices, justification textarea, NONCE call to `productionApi.resolveHumanRequest` |
| `ArtifactPart.tsx` | `ArtifactChip.tsx` | filename + size + language badge + same-origin download |
| `HeartbeatPart.tsx` | `HeartbeatBar.tsx` | SSE status chip + phase + elapsed timer |
| `NotePart.tsx` | `NoteBlock.tsx` | "not sent to Munin" badge + avatar + timestamp |
| `GuidancePart.tsx` | `GuidanceBlock.tsx` | dashed border + delivery status (queued/delivered @ step N) + target agent |

### `app/src/lib/useConversationEvents.ts` (unchanged)

No-op for PR-02. Explicit note that collab/presence remain on bespoke SSE because they are conversation-scope events, not chat-message parts (issue §10 distinction). Usage stays identical upstream.

---

## Tests added

| Path | Assertion contract |
|---|---|
| `app/src/lib/__tests__/sseToParts.test.ts` | For each envelope `kind`: given a fixture envelope, the mapper produces the expected part dict (count + types + key payloads). Covers the 13 kinds above (12 kinds + default-skip). |
| `app/src/lib/__tests__/partsRenderers.test.tsx` | For each of the 9 part components: render with a fixture prop, assert all visible elements present (buttons, chips, expandable panel toggles, badges); click handlers call the expected `productionApi` mutation or setter. |
| `app/src/lib/__tests__/reconnect.test.ts` | Script the BFF route with a fake `fetch` returning two SSE event streams (1 stream of 5 events, then a `close`, then 3 more events after a re-request with `Last-Event-ID=5`). Assert the assembled `messages[].parts` array has the full 8 parts without duplicates and the silent zone (between close and request-resume) yields zero additional parts. |
| `app/src/lib/__tests__/hitlRoundTrip.test.tsx` | Render `HitlRequestPart` with a pending request fixture, click `Approve`, assert `productionApi.resolveHumanRequest(id, "approve", nonce, undefined)` was invoked exactly once. |
| `app/src/lib/__tests__/persistence.test.ts` | After `onEnd` fires on a scripted `useMuninChat` mount with a 3-message conversation, assert `productionApi.persistConversation(runId, msgs)` was called with the final messages list (verifies `timeline_messages` rows remain authoritative). |

Framework provenance: Context7 `/websites/ai-sdk_dev` query `Initialize useChat Hook with Persisted Messages` confirms the `id` + `messages` + `onEnd` pattern.

---

## Parity bar (PR-01 tests preserved)

| PR-01 test file | Continues green here? | Why |
|---|---|---|
| `test_coord_respond_loop_parity.py` | Yes — Python unchanged | Coordinator emits same event stream |
| `test_subagent_runner_parity.py` | Yes | Python unchanged |
| `test_tool_catalog_parity.py` | Yes | Python unchanged |
| `test_conversation_persistence_parity.py` | Yes | Python unchanged |
| `test_shared_state_persistence_parity.py` | Yes | Python unchanged |
| `test_hitl_parity.py` | Yes (Python-side HITL tests) + node PixelTestSet is at productionApi + HitlRequest component; new test mirrors the same. Note: the existing `HitlRequest.tsx` test in PR-01 covers the LEGACY renderer. The NEW renderer test added here in PR-02 must reproduce the same assertions. They must both stay green until PR-16 deletes the legacy block. |
| `test_sse_event_contract_parity.py` | Yes — the BFF does not delete `useRunEvents.ts`; legacy SSE adapter stays callable until PR-16. Both legacy and new transport continue to serve run events until parity at this PR's boundary proves equivalence. |

---

## Deps added / bumped in this PR

| Dep | Pin intent | Why |
|---|---|---|
| `ai` | `>=5.0.0,<6` | Stable 5.x surface confirms `streamText`, `toUIMessageStream`, `createUIMessageStreamResponse`, `DefaultChatTransport`, `UIMessage` types (Context7 shows ai_5_0_0 listed; we pin `<6` to avoid silent breaking from 6.x which is currently beta). |
| `@ai-sdk/react` | `>=2.0.0,<3` | Companion React surface for `useChat`, `reconnectToStream`, `DefaultChatTransport` (issue §10 explicit). Pin `<3` because v3 is a candidate breaking major. |

Justification (GLUE_INVENTORY §13 + IMPROVEMENT_BACKLOG §dependency-additions): repo frontend currently has neither dep; adding them is the explicit step 2 dependency addition allowed by the issue and required by the migration plan. No production Python deps bumped. No other app/package.json deps changed.

---

## Rollback plan

Revert removes: `app/src/app/api/chat/[[...path]]/route.ts`, `app/src/lib/aiChat.ts`, `app/src/lib/partsRenderers.ts`, the new `blocks/parts/` subdir, the new tests, and the two new package.json entries; reverts `ChatPanel.tsx`, `ForgeFloatingChat.tsx`, `muninStore.ts` to legacy form. Legacy `useRunEvents.ts` and the 9 legacy blocks are still on disk (untouched) so the app returns cleanly to the SSE/state path. rollback is independent and bite-sized — does not break PR-03's later supervisor work or PR-16's eventual deletion logic (PR-16 will re-delete the legacy blocks, independent of this revert).

---

## Validation plan

1. **Characterization tests (necessary + **here** also sufficient when paired with CI)**: All 7 PR-01 files still green via `pytest tests/characterization/`. New node test files green via whatever runner `ci.yml` frontend-equivalent uses (next lint + `next build` already wired; new tsx tests via existing test runner if any — DEV NOTE: spec delegation will declare the runner, `vitest` is the natural NP choice, falling back to inline `tsx` running against node:test if no current test runner). If neither is wired, this PR adds `vitest` to app devDependencies as a one-time scaffold.
2. **CI green (necessary)**: `.github/workflows/ci.yml` frontend job passes (next lint + next build + the new tests).
3. **Live-session workflow (necessary, part-of-the-validation here)**: trigger `live-session.yml`. Get tunnel URL from job summary. Use **chrome-devtools MCP**:
   - Open tunnel URL. Send "hello" in chat. Verify streaming text + reasoning chip + tool chip appear.
   - Reload page. Assert chat reconstructs identically (chatId preserved, parts identical).
   - Open chat during a long run, kill backend container mid-stream, restart backend, reconnect — assert no duplicate tool parts, no missing tool results after reconnect, no "stale" hang beyond a momentary flip-flop.
   - Trigger HITL by issuing a destructive-tool request; render `hitl-request` part, click Approve, verify resolution reaches backend.
   - Save screenshots in `evidence/PR-02/`.
4. **Artifact inspection**: After the run, inspect `data/shared_state.sqlite`: `timeline_messages` rows count grows by sent message count, and the new BFF persisted via `onEnd` matches the messages array order-wise. Confirms issue §11 single-authoritative-owner invariant (timeline_messages retained as audit-true; LangGraph artifacts not yet introduced).
5. **Parity manual check**: Re-run `pytest tests/characterization/ -v` after this PR merges into the migration branch — all paths green above all params confirmed unchanged.

## Issue §9 invariants preserved (only those applicable to this PR)

| Invariant | Status |
|---|---|
| FastMCP tools + external MCP integration | Untouched (Python backend unchanged) |
| Scope/OPSEC in tool boundary | Untouched |
| Audit redaction contract | Untouched — BFF routes events through unchanged; only transport shape changes |
| Soul human-editable | Untouched |
| Cross-session artifact pattern | Untouched |

## Framework verification provenance

- **Vercel AI SDK useChat + parts**: Context7 `/websites/ai-sdk_dev` query "useChat hook messages parts types reconnectToStream chatId startIndex consumeStream toUIMessageStreamResponse onEnd persistence Next.js App Router Session" → confirmed `useChat({ id, messages, transport: new DefaultChatTransport({api}) })`; `message.parts` typed promises (text / reasoning / dynamic-tool / custom); `onEnd({messages})` for persistence. Provenance ai-sdk.dev/docs `chatbot-message-persistence` + `use-chat` reference. ai_5_0_0 stable release.
- **AI SDK message parts**: Context7 source "Render dynamic tool parts in useChat" + "Implement Chat UI with useChat Hook in Next.js" + "Handling Errors with the useChat Error Object in React" + "Initialize useChat Hook with Persisted Messages" — confirmed `dynamic-tool` part type + `tool-invocation` for pre-registered tools, `reasoning` first-class, `custom` part id surface for `subagent-presence`/`hitl-request`/`artifact`/`forge-stage`. Issue §10 authorises custom parts.
- **Resume across stream interruption**: same Context7 query confirms `reconnectToStream({chatId, startIndex})` (client) + `consumeStream()` (server-side; survives client disconnect). Last-Event-ID semantics remain because the upstream Python ASGI already respects it.

Uncertainty remaining: whether `ci.yml` runs a node test runner today. Resolved during PR-02 delegation (subagent will inspect `.github/workflows/ci.yml` for frontend test command); if `vitest` is missing, the spec authorises adding it as a one-time scaffold with `^1.x`. No open architectural question either way.