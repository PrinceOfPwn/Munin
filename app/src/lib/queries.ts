// tags: [utility-library, tanstack-query, react-query, mutations, indexeddb, client-component, use-conversations, use-archive-conversation, use-browser-cache, use-create-conversation, use-rename-conversation, use-effect, use-mutation, use-query, use-munin-chat, use-query-client]
﻿"use client";

// -----------------------------------------------------------------------------
// queries.ts â€” Fase 2 (issue #9) trimmed to the conversation aggregate.
//
// Everything else (runs, run detail, agents, provider profiles, HITL,
// artifacts, conversation-detail with SSE polling) used dispatcher-only
// endpoints that were deleted in Fase 2.  Runtime state now flows through
// `useMuninChat` in `aiChat.ts`.
//
// Browser-cache integration (issue #9 cache layer):
//   * `useConversations` paints instantly from the IndexedDB mirror via
//     `placeholderData` (v5 name â€” `keepPreviousData` the option is gone),
//     then background-refetches; every successful server list is written
//     through to IndexedDB.
//   * create / rename / archive run the v5 optimistic pattern
//     (onMutate â†’ setQueryData + IndexedDB write-through â†’ server call â†’
//     onSuccess reconcile / onError rollback â†’ onSettled invalidate).
// -----------------------------------------------------------------------------

import { useEffect } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";

import { useBrowserCache } from "@/lib/cache";
import { uuid } from "@/lib/utils";
import { productionApi, type Conversation } from "./production-api";

type CreatedConversation = Partial<Conversation> & {
  id: string;
  title: string;
  created_at_ms?: number;
};

function normalizeCreatedConversation(raw: CreatedConversation): Conversation {
  const now = raw.last_activity_at_ms || raw.created_at_ms || Date.now();
  return {
    id: raw.id,
    title: raw.title || "New operation",
    status: raw.status || "active",
    tags: Array.isArray(raw.tags) ? raw.tags : [],
    last_activity_at_ms: now,
    message_count: Number(raw.message_count || 0),
    version: Number(raw.version || 1),
  };
}

/** Roll back every conversation query to a captured snapshot. */
function restoreConversationQueries(
  qc: ReturnType<typeof useQueryClient>,
  previous: Array<[unknown, Conversation[] | undefined]>,
): void {
  for (const [key, data] of previous) {
    if (data !== undefined) qc.setQueryData(key as never, data as never);
  }
}

/** Snapshot all `["conversations", ...]` queries for optimistic rollback. */
function snapshotConversationQueries(
  qc: ReturnType<typeof useQueryClient>,
): Array<[unknown, Conversation[] | undefined]> {
  return qc.getQueriesData<Conversation[]>({ queryKey: ["conversations"] });
}

/** Base list of conversations, filtered by an optional server-side query. */
export function useConversations(query = "") {
  const { cachedConversations, writeConversations } = useBrowserCache();

  const result = useQuery({
    queryKey: ["conversations", query],
    queryFn: () => productionApi.conversations(query),
    staleTime: 30_000,
    // Cache-first: the unfiltered cached list paints instantly while the
    // server list refetches in the background. Search queries stay on
    // `keepPreviousData` â€” the server-side filter does not match the cache.
    placeholderData: (previous) => {
      if (previous) return previous;
      return query === "" ? cachedConversations : undefined;
    },
  });

  // Write-through: persist every successful server list into IndexedDB.
  // (v5 removed onSuccess from useQuery â€” an effect on data is the pattern.)
  useEffect(() => {
    if (result.data && result.data.length > 0) {
      writeConversations(result.data);
    }
  }, [result.data, writeConversations]);

  return result;
}

export function useCreateConversation() {
  const qc = useQueryClient();
  const { upsertConversation, removeConversation } = useBrowserCache();

  return useMutation({
    mutationFn: async (title?: string) =>
      normalizeCreatedConversation(
        (await productionApi.createConversation(title)) as CreatedConversation,
      ),
    onMutate: async (title) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const previous = snapshotConversationQueries(qc);
      const tempId = `local-${uuid()}`;
      const temp: Conversation = {
        id: tempId,
        title: (title ?? "").trim() || "New operation",
        status: "active",
        tags: [],
        last_activity_at_ms: Date.now(),
        message_count: 0,
        version: 1,
      };
      qc.setQueriesData<Conversation[]>(
        { queryKey: ["conversations"] },
        (items) => [temp, ...(items ?? [])],
      );
      upsertConversation(temp);
      return { previous, tempId };
    },
    onSuccess: (conversation, _variables, context) => {
      if (!context) return;
      qc.setQueriesData<Conversation[]>(
        { queryKey: ["conversations"] },
        (items) =>
          items?.map((item) =>
            item.id === context.tempId ? conversation : item,
          ),
      );
      upsertConversation(conversation);
    },
    onError: (_error, _variables, context) => {
      if (!context) return;
      restoreConversationQueries(qc, context.previous);
      removeConversation(context.tempId);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  const { upsertConversation } = useBrowserCache();

  return useMutation({
    mutationFn: (input: { id: string; version: number; title: string }) =>
      productionApi.renameConversation(input.id, input.version, input.title),
    onMutate: async ({ id, title }) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const previous = snapshotConversationQueries(qc);
      const current = qc
        .getQueryData<Conversation[]>(["conversations", ""])
        ?.find((item) => item.id === id);
      qc.setQueriesData<Conversation[]>(
        { queryKey: ["conversations"] },
        (items) =>
          items?.map((item) => (item.id === id ? { ...item, title } : item)),
      );
      if (current) upsertConversation({ ...current, title });
      return { previous };
    },
    onSuccess: (updated, variables) => {
      qc.setQueriesData<Conversation[]>(
        { queryKey: ["conversations"] },
        (items) =>
          items?.map((item) =>
            item.id === variables.id ? { ...item, ...updated } : item,
          ),
      );
      upsertConversation(updated);
    },
    onError: (_error, _variables, context) => {
      if (!context) return;
      restoreConversationQueries(qc, context.previous);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}

export function useArchiveConversation() {
  const qc = useQueryClient();
  const { upsertConversation, removeConversation } = useBrowserCache();

  return useMutation({
    mutationFn: (input: { id: string; version: number; archived: boolean }) =>
      productionApi.archiveConversation(input.id, input.version, input.archived),
    onMutate: async ({ id, archived }) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const previous = snapshotConversationQueries(qc);
      const current = qc
        .getQueryData<Conversation[]>(["conversations", ""])
        ?.find((item) => item.id === id);
      qc.setQueriesData<Conversation[]>(
        { queryKey: ["conversations"] },
        (items) =>
          archived ? items?.filter((item) => item.id !== id) : items,
      );
      if (current) {
        if (archived) removeConversation(id);
        else upsertConversation({ ...current, status: "active" });
      }
      return { previous };
    },
    onSuccess: (updated, variables) => {
      qc.setQueriesData<Conversation[]>(
        { queryKey: ["conversations"] },
        (items) =>
          variables.archived
            ? items?.filter((item) => item.id !== variables.id)
            : items?.map((item) =>
                item.id === variables.id ? { ...item, ...updated } : item,
              ),
      );
      if (variables.archived) removeConversation(variables.id);
      else upsertConversation(updated);
    },
    onError: (_error, _variables, context) => {
      if (!context) return;
      restoreConversationQueries(qc, context.previous);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}
