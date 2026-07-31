"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { productionApi, type ConversationDetail } from "./production-api";
import { isTerminalRun } from "./utils";

/** Base list of conversations, filtered by an optional server-side query. */
export function useConversations(query = "") {
  return useQuery({
    queryKey: ["conversations", query],
    queryFn: () => productionApi.conversations(query),
    staleTime: 15_000,
  });
}

/**
 * One conversation with its message timeline + runs.  Polling is a fallback
 * for when SSE is degraded — pass `sseHealthy=true` and this stops polling
 * entirely.  Consumers wire this to the status returned by
 * :file:`useConversationEvents`.
 */
export function useConversation(id: string | null, sseHealthy = false) {
  return useQuery({
    queryKey: ["conversation", id],
    queryFn: () => (id ? productionApi.conversation(id) : Promise.resolve<ConversationDetail | null>(null)),
    enabled: !!id,
    refetchInterval: (query) => {
      if (sseHealthy) return false;
      const data = query.state.data as ConversationDetail | null | undefined;
      if (!data) return false;
      return data.runs.some((run) => !isTerminalRun(run.state)) ? 15_000 : false;
    },
  });
}

/** Fine-grained run detail — SSE keeps this alive, no polling. */
export function useRunDetail(id: string | null) {
  return useQuery({
    queryKey: ["run", id, "detail"],
    queryFn: () => (id ? productionApi.runDetail(id) : null),
    enabled: !!id,
    staleTime: 30_000,
  });
}

/**
 * Queued + delivered operator guidance for a run.  Invalidated by the
 * conversation SSE stream on `guidance-delivered`.
 */
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

// ── Mutations with cache invalidation ─────────────────────────────────────

export function useSendTurn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { conversationId: string; content: string; idempotencyKey: string }) =>
      productionApi.turn(input.conversationId, input.content, input.idempotencyKey),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["conversation", variables.conversationId] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => productionApi.cancelRun(runId),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["run", run.id, "detail"] });
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useRetryRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) => productionApi.retryRun(runId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
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
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["run"] });
    },
  });
}

export function useArchiveConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; version: number; archived: boolean }) =>
      productionApi.archiveConversation(input.id, input.version, input.archived),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
    },
  });
}

export function useCreateConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => productionApi.createConversation(title),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
}
