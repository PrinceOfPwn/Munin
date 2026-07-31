"use client";

/**
 * TanStack Query wrappers for the v3.1 collaboration endpoints.  Consolidated
 * in one module because the three concerns (collaborators, notes, presence)
 * share cache-key conventions and mutation invalidation targets.
 */
import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  productionApi,
  type Collaborator,
  type ConversationNote,
  type PresenceEntry,
} from "./production-api";

// ── Collaborators ───────────────────────────────────────────────────────

export function useCollaborators(conversationId: string | null) {
  return useQuery({
    queryKey: ["conversation-collaborators", conversationId],
    queryFn: () => productionApi.listCollaborators(conversationId as string),
    enabled: Boolean(conversationId),
    staleTime: 30_000,
  });
}

export function useAddCollaborator(conversationId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      username,
      role,
    }: {
      username: string;
      role: Collaborator["role"];
    }) => productionApi.addCollaborator(conversationId as string, username, role),
    onSuccess: (data) => {
      qc.setQueryData(["conversation-collaborators", conversationId], data);
    },
  });
}

// ── Notes ────────────────────────────────────────────────────────────────

export function useNotes(conversationId: string | null) {
  return useQuery<ConversationNote[]>({
    queryKey: ["conversation-notes", conversationId],
    queryFn: () => productionApi.listNotes(conversationId as string, 0),
    enabled: Boolean(conversationId),
    staleTime: 15_000,
  });
}

export function usePostNote(conversationId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: string) =>
      productionApi.postNote(conversationId as string, body),
    onSuccess: (note) => {
      qc.setQueryData<ConversationNote[] | undefined>(
        ["conversation-notes", conversationId],
        (prev) => (prev ? [...prev, note] : [note]),
      );
    },
  });
}

// ── Presence ─────────────────────────────────────────────────────────────

/**
 * Pure read of the active presence list.  Fed by the SSE `presence-changed`
 * broadcast in :file:`useConversationEvents.ts`; the periodic refetch is a
 * safety net for the case where SSE is down.
 */
export function usePresence(conversationId: string | null) {
  return useQuery<PresenceEntry[]>({
    queryKey: ["conversation-presence", conversationId],
    queryFn: async () => {
      // A GET route doesn't exist for presence; falling back to a passive
      // heartbeat (typing=false) is a no-op update on the server but returns
      // the current snapshot.  Only used as a fallback when SSE is stale.
      return productionApi.presenceHeartbeat(conversationId as string, false);
    },
    enabled: Boolean(conversationId),
    staleTime: 30_000,
    // Refetch only when SSE isn't feeding this cache — the hook consumer
    // (`useConversationEvents`) writes fresh data on every broadcast, so
    // React Query's cache is authoritative when SSE is live.
    refetchInterval: false,
  });
}

/**
 * Heartbeat the presence endpoint on a 15s idle cadence.  Typing signal is
 * debounced: the FIRST keystroke after quiescence fires `typing=true`; a 3s
 * timer resets on subsequent keystrokes and clears back to `typing=false`.
 * Ratio: ~2 writes per typing session, not one per keystroke.
 */
const TYPING_IDLE_MS = 3_000;
const HEARTBEAT_MS = 15_000;
/** After this many consecutive failed beats we stop the interval entirely.
 * The next mount / conversation change re-arms it. */
const HEARTBEAT_MAX_FAILURES = 3;

export function usePresenceHeartbeat(conversationId: string | null): {
  onKeystroke: () => void;
  onIdle: () => void;
} {
  const qc = useQueryClient();
  const idleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const typingRef = useRef(false);
  // Guard against pile-up: if the previous beat is still in flight, skip
  // the tick. Fixes 20+ concurrent presence POSTs when the backend takes
  // longer than HEARTBEAT_MS to respond.
  const inFlightRef = useRef(false);
  // Circuit breaker: stop the interval after N consecutive failures so a
  // dead backend does not generate 4 req/min per open tab.
  const failStreakRef = useRef(0);
  const stoppedRef = useRef(false);

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    stoppedRef.current = false;
    inFlightRef.current = false;
    failStreakRef.current = 0;

    const beat = async (typing: boolean) => {
      if (cancelled || stoppedRef.current) return;
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      try {
        const presence = await productionApi.presenceHeartbeat(conversationId, typing);
        failStreakRef.current = 0;
        if (!cancelled) {
          qc.setQueryData(["conversation-presence", conversationId], presence);
        }
      } catch (err) {
        failStreakRef.current += 1;
        if (failStreakRef.current >= HEARTBEAT_MAX_FAILURES) {
          stoppedRef.current = true;
          // eslint-disable-next-line no-console
          console.warn(
            `[presence] backing off after ${HEARTBEAT_MAX_FAILURES} failures on conv=${conversationId}`,
            err,
          );
        }
      } finally {
        inFlightRef.current = false;
      }
    };
    void beat(false);
    const handle = setInterval(() => {
      if (stoppedRef.current) return;
      void beat(typingRef.current);
    }, HEARTBEAT_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [conversationId, qc]);

  return {
    onKeystroke: () => {
      if (!conversationId || stoppedRef.current) return;
      const wasTyping = typingRef.current;
      typingRef.current = true;
      // Only POST on the transition (idle → typing).  Subsequent keystrokes
      // just push the idle deadline forward — no write amplification.
      if (!wasTyping) {
        void productionApi
          .presenceHeartbeat(conversationId, true)
          .then((p) => qc.setQueryData(["conversation-presence", conversationId], p))
          .catch(() => { /* silent — the interval loop tracks failures */ });
      }
      if (idleTimer.current) clearTimeout(idleTimer.current);
      idleTimer.current = setTimeout(() => {
        typingRef.current = false;
        void productionApi
          .presenceHeartbeat(conversationId, false)
          .then((p) => qc.setQueryData(["conversation-presence", conversationId], p))
          .catch(() => { /* silent */ });
      }, TYPING_IDLE_MS);
    },
    onIdle: () => {
      typingRef.current = false;
      if (idleTimer.current) clearTimeout(idleTimer.current);
      if (conversationId && !stoppedRef.current) {
        void productionApi
          .presenceHeartbeat(conversationId, false)
          .then((p) => qc.setQueryData(["conversation-presence", conversationId], p))
          .catch(() => { /* silent */ });
      }
    },
  };
}
