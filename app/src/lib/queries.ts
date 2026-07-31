"use client";

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  productionApi,
  type Conversation,
  type ConversationDetail,
  type TimelineMessage,
} from "./production-api";
import { isTerminalRun } from "./utils";

/** Grow the refetch interval when the query is failing, so a slow/broken
 * backend does not accumulate concurrent in-flight requests. Capped at 60s
 * so recovery is snappy once the backend returns. */
function backoffMs(failureCount: number, baseMs: number): number {
  if (failureCount <= 0) return baseMs;
  const grown = baseMs * 2 ** failureCount;
  return Math.min(60_000, grown);
}

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

/** Base list of conversations, filtered by an optional server-side query. */
export function useConversations(query = "") {
  return useQuery({
    queryKey: ["conversations", query],
    queryFn: () => productionApi.conversations(query),
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}

/**
 * One conversation with its message timeline + runs.
 *
 * SSE is the fast path, but it is deliberately not the only path. A short
 * polling fallback remains active while a run is non-terminal so a tunnel or
 * Turso stream hiccup cannot leave an empty assistant placeholder on screen.
 */
export function useConversation(id: string | null, sseHealthy = false) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => (id ? productionApi.conversation(id) : Promise.resolve<ConversationDetail | null>(null)),
    enabled: !!id,
    staleTime: 10_000,
    retry: 2,
    retryDelay: (attempt) => Math.min(15_000, 1_000 * 2 ** attempt),
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      const failures = query.state.errorUpdateCount;
      if (failures > 0) return backoffMs(failures, 5_000);
      const data = query.state.data as ConversationDetail | null | undefined;
      if (!data) return 5_000;
      if (!data.runs.some((run) => !isTerminalRun(run.state))) return false;
      return sseHealthy ? 10_000 : 5_000;
    },
  });
}

/** Fine-grained run detail. SSE updates this cache and polling repairs gaps. */
export function useRunDetail(id: string | null) {
  return useQuery({
    queryKey: ["run", id, "detail"],
    queryFn: () => (id ? productionApi.runDetail(id) : null),
    enabled: !!id,
    staleTime: 5_000,
    retry: 2,
    retryDelay: (attempt) => Math.min(15_000, 1_000 * 2 ** attempt),
    refetchIntervalInBackground: false,
    refetchInterval: (query) => {
      const failures = query.state.errorUpdateCount;
      if (failures > 0) return backoffMs(failures, 5_000);
      const data = query.state.data as Awaited<ReturnType<typeof productionApi.runDetail>> | null | undefined;
      if (!data) return 5_000;
      return isTerminalRun(data.run.state) ? false : 5_000;
    },
  });
}

/** Queued + delivered operator guidance for a run. */
export function useRunGuidance(id: string | null) {
  return useQuery({
    queryKey: ["run", id, "guidance"],
    queryFn: () => (id ? productionApi.listRunGuidance(id) : Promise.resolve([])),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: () => productionApi.agents(),
    staleTime: 60_000,
  });
}

export function useProviderProfiles() {
  return useQuery({
    queryKey: ["provider-profiles"],
    queryFn: () => productionApi.providerProfiles(),
    staleTime: 30_000,
  });
}

export function useArtifact(id: string | null) {
  return useQuery({
    queryKey: ["artifact", id],
    queryFn: () => (id ? productionApi.artifact(id) : null),
    enabled: !!id,
  });
}

// ── Mutations with optimistic cache updates ────────────────────────────────

export function useSendTurn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { conversationId: string; content: string; idempotencyKey: string }) =>
      productionApi.turn(input.conversationId, input.content, input.idempotencyKey),
    onMutate: async (variables) => {
      const key = ["conversation", variables.conversationId] as const;
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<ConversationDetail | null>(key);
      const previousLists = qc.getQueriesData<Conversation[]>({ queryKey: ["conversations"] });
      const now = Date.now();

      if (previous) {
        const lastSequence = previous.messages.at(-1)?.sequence || 0;
        const optimistic: TimelineMessage = {
          id: `optimistic-${variables.idempotencyKey}`,
          kind: "user",
          status: "pending",
          content: variables.content,
          sequence: lastSequence + 1,
        };
        qc.setQueryData<ConversationDetail>(key, {
          ...previous,
          conversation: {
            ...previous.conversation,
            last_activity_at_ms: now,
            message_count: Number(previous.conversation.message_count || 0) + 1,
          },
          messages: [...previous.messages, optimistic],
        });
      }

      qc.setQueriesData<Conversation[]>({ queryKey: ["conversations"] }, (items) =>
        items?.map((item) =>
          item.id === variables.conversationId
            ? { ...item, last_activity_at_ms: now, message_count: Number(item.message_count || 0) + 1 }
            : item,
        ),
      );

      return { previous, previousLists };
    },
    onError: (_error, variables, context) => {
      if (context?.previous) {
        qc.setQueryData(["conversation", variables.conversationId], context.previous);
      }
      context?.previousLists?.forEach(([key, data]) => qc.setQueryData(key, data));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
    onSettled: (_data, _error, variables) => {
      qc.invalidateQueries({ queryKey: ["conversation", variables.conversationId] });
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => productionApi.cancelRun(runId),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["run", run.id, "detail"] });
      qc.invalidateQueries({ queryKey: ["conversation"], refetchType: "active" });
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}

export function useRetryRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => productionApi.retryRun(runId),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["run", run.id, "detail"] });
      qc.invalidateQueries({ queryKey: ["conversation"], refetchType: "active" });
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}

export function useGuideRun() {
  return useMutation({
    mutationFn: (input: { runId: string; guidance: string }) =>
      productionApi.guideRun(input.runId, input.guidance),
  });
}

export function useResolveHumanRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { requestId: string; choice: string; nonce?: string; guidance?: string }) =>
      productionApi.resolveHumanRequest(input.requestId, input.choice, input.nonce, input.guidance),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
      qc.invalidateQueries({ queryKey: ["run"], refetchType: "active" });
    },
  });
}

export function useArchiveConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; version: number; archived: boolean }) =>
      productionApi.archiveConversation(input.id, input.version, input.archived),
    onSuccess: (_conversation, variables) => {
      qc.setQueriesData<Conversation[]>({ queryKey: ["conversations"] }, (items) =>
        variables.archived ? items?.filter((item) => item.id !== variables.id) : items,
      );
      qc.invalidateQueries({ queryKey: ["conversation", variables.id] });
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (title?: string) =>
      normalizeCreatedConversation(
        (await productionApi.createConversation(title)) as CreatedConversation,
      ),
    onSuccess: (conversation) => {
      qc.setQueriesData<Conversation[]>({ queryKey: ["conversations"] }, (items) => {
        if (!items) return [conversation];
        if (items.some((item) => item.id === conversation.id)) return items;
        return [conversation, ...items];
      });
      qc.invalidateQueries({ queryKey: ["conversations"], refetchType: "active" });
    },
  });
}
