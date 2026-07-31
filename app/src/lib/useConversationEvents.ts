"use client";

/**
 * SSE subscription for a conversation's collaboration channel.
 *
 * Mirrors :file:`useRunEvents.ts` but scopes to a single conversation and
 * multiplexes four distinct payload types:
 *
 *   * `note-appended`      → invalidates `["conversation-notes", id]`
 *   * `presence-changed`   → replaces `["conversation-presence", id]`
 *   * `run-transition`     → invalidates `["conversation", id]` so a state
 *                            change (queued → running → completed) refreshes
 *                            the run bar in the composer.
 *   * `guidance-delivered` → invalidates `["run", id, "detail"]` so the
 *                            inline GuidanceBlock renders its "delivered at
 *                            step N" chip immediately.
 *
 * Terminal `close` events stop reconnect; `warning` events keep the
 * connection alive but hint that a store hiccup happened.
 */
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ConversationNote, PresenceEntry } from "./production-api";

export type ConversationStreamStatus = "connecting" | "live" | "stale" | "closed";

interface Options {
  conversationId: string | null;
}

export function useConversationEvents({ conversationId }: Options): {
  status: ConversationStreamStatus;
} {
  const qc = useQueryClient();
  const [status, setStatus] = useState<ConversationStreamStatus>("connecting");
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!conversationId) {
      setStatus("closed");
      return;
    }
    setStatus("connecting");
    const url = `/api/production/conversations/${encodeURIComponent(conversationId)}/events`;
    const es = new EventSource(url, { withCredentials: true });

    const bump = () => {
      setStatus("live");
      if (staleTimer.current) clearTimeout(staleTimer.current);
      staleTimer.current = setTimeout(() => setStatus("stale"), 45_000);
    };
    // Arm the stale timer at connect time so a silent server still trips us.
    bump();

    es.addEventListener("note-appended", (msg) => {
      bump();
      try {
        const note = JSON.parse((msg as MessageEvent).data) as ConversationNote;
        qc.setQueryData<ConversationNote[] | undefined>(
          ["conversation-notes", conversationId],
          (prev) =>
            prev && !prev.some((existing) => existing.id === note.id)
              ? [...prev, note]
              : prev,
        );
      } catch {
        /* ignore malformed */
      }
    });

    es.addEventListener("presence-changed", (msg) => {
      bump();
      try {
        const raw = JSON.parse((msg as MessageEvent).data) as
          | { presence: PresenceEntry[] }
          | PresenceEntry[];
        const presence = Array.isArray(raw) ? raw : raw.presence;
        qc.setQueryData(["conversation-presence", conversationId], presence);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("run-transition", (msg) => {
      bump();
      try {
        const payload = JSON.parse((msg as MessageEvent).data) as {
          run_id?: string;
          state?: string;
        };
        if (payload.run_id) {
          qc.invalidateQueries({ queryKey: ["run", payload.run_id, "detail"] });
        }
      } catch {
        /* ignore */
      }
      qc.invalidateQueries({ queryKey: ["conversation", conversationId] });
    });

    es.addEventListener("guidance-delivered", (msg) => {
      bump();
      try {
        const payload = JSON.parse((msg as MessageEvent).data) as {
          run_id?: string;
          guidance_id?: string;
          delivered_at_step?: number;
        };
        if (payload.run_id) {
          qc.invalidateQueries({ queryKey: ["run", payload.run_id, "detail"] });
          qc.invalidateQueries({ queryKey: ["run", payload.run_id, "guidance"] });
        }
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("heartbeat", () => bump());
    es.addEventListener("warning", () => bump());
    es.addEventListener("close", () => {
      setStatus("closed");
      es.close();
    });

    es.onopen = () => bump();
    es.onerror = () => {
      if (staleTimer.current) clearTimeout(staleTimer.current);
      setStatus("stale");
    };

    return () => {
      es.close();
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  return { status };
}
