import { useChat } from "@ai-sdk/react";
import type { UIMessage } from "ai";
import { DefaultChatTransport } from "ai";
import { useEffect, useMemo, useState } from "react";

import { useBrowserCache } from "@/lib/cache";
import { currentCsrfToken } from "@/lib/production-api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UseMuninChatOptions {
  /** Backend conversation (operation) id — doubles as the useChat id. */
  conversationId: string;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * `useMuninChat` — Vercel AI SDK v5 `useChat` wired to the Munin BFF.
 *
 * The transport posts to `/api/chat` with the active conversation id and the
 * operator's CSRF token; the BFF commits a turn in the authoritative
 * production API and streams the run back as a v5 UI message stream
 * (text deltas, dynamic tool parts, data-* operational parts).
 *
 * Browser-cache integration (issue #9 cache layer):
 *   * On mount, the last-known timeline for this conversation is pushed into
 *     the chat via `setMessages` so the console paints instantly while the
 *     stream starts (cache-first render). v5 `useChat` keeps the server the
 *     source of truth for *live* streaming; the cache only seeds the visible
 *     history on (re)mount.
 *   * `onFinish` writes the final message batch through to IndexedDB so a
 *     page refresh mid-run rehydrates the visible history.
 *   * A run marker (state `running`/`completed`/`failed`) is set so the UI
 *     can surface a "resume streaming?" hint after a mid-run refresh.
 */
export function useMuninChat({ conversationId }: UseMuninChatOptions) {
  const cache = useBrowserCache();
  const [seeded, setSeeded] = useState(false);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: { conversation_id: conversationId },
        headers: () => {
          const token = currentCsrfToken();
          const headers: Record<string, string> = {};
          if (token) headers["X-CSRF-Token"] = token;
          return headers;
        },
      }),
    [conversationId],
  );

  const chat = useChat({
    id: conversationId,
    transport,
    onError: (err) => {
      console.error(`[useMuninChat] conversation=${conversationId}:`, err);
      cache.setRunMarker(conversationId, {
        runId: "",
        state: "failed",
        startedAt: Date.now(),
      });
    },
    onFinish: ({ messages }) => {
      // Write-through: persist the final multi-message batch so the next
      // mount rehydrates from cache. The cache layer drops `data-heartbeat`
      // parts so the stored timeline stays compact.
      cache.setMessages(conversationId, messages as UIMessage[]);
      cache.setRunMarker(conversationId, {
        runId: "",
        state: "completed",
        startedAt: Date.now(),
      });
    },
  });

  // Cache-first seed: once per (conversation) mount, if the chat is empty and
  // the cache holds a timeline for this conversation, push it into `useChat`
  // so the UI paints before the first stream chunk arrives. Idempotent: we
  // only seed when `messages` is empty and we have not seeded yet.
  useEffect(() => {
    if (seeded) return;
    let cancelled = false;
    void cache.getMessages(conversationId).then((rows) => {
      if (cancelled || seeded || rows.length === 0) return;
      const seed = rows.map((row) => ({
        id: row.id,
        role: row.role,
        parts: row.parts,
        createdAt: new Date(row.created_at),
      })) as UIMessage[];
      // v5: setMessages replaces the local message state without a round-trip.
      chat.setMessages(seed);
      setSeeded(true);
    });
    return () => {
      cancelled = true;
    };
    // `chat` identity changes per render; we only want the seed to fire once
    // per conversation, so depend on the conversation id + `seeded` only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, seeded]);

  return chat;
}

// ---------------------------------------------------------------------------
// HITL resolves — Fase 2 (issue #9): stubbed pending Fase 3 rewire.
//
// The pre-migration BFF called `productionApi.resolveHumanRequest` against
// `POST /api/human-requests/{id}/resolve`, which was deleted along with the
// rest of Arch A.  Fase 3 will re-introduce HITL as an AI SDK v5 data-part
// flow: the client will send a `data-hitl-resolve` UI part through the same
// `/api/chat` transport, and the Python handler will intercept it before
// `supervisor_runner` sees the next iteration.  Until that lands the
// approve/reject helpers below only warn — the render path in
// `HitlRequestPart` still fires callbacks so we don't remove the API
// entirely, but no server round-trip happens yet.
// ---------------------------------------------------------------------------

/** Approve a human-in-the-loop request. Stub until Fase 3 wires HITL via /api/chat. */
export async function approveHitlRequest(
  requestId: string,
  choice: string,
  _nonce: string,
): Promise<void> {
  console.warn(
    "[aiChat] approveHitlRequest is a Fase 2 stub — HITL resolves are not " +
      "wired through /api/chat yet (Fase 3).",
    { requestId, choice },
  );
}

/** Reject a human-in-the-loop request. Stub until Fase 3 wires HITL via /api/chat. */
export async function rejectHitlRequest(
  requestId: string,
  choice: string,
  _nonce: string,
  reason?: string,
): Promise<void> {
  console.warn(
    "[aiChat] rejectHitlRequest is a Fase 2 stub — HITL resolves are not " +
      "wired through /api/chat yet (Fase 3).",
    { requestId, choice, reason },
  );
}
