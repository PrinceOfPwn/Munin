"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type {
  ArtifactRef,
  HumanRequest,
  ReasoningEvent,
  Run,
  RunDetail,
  RunEventEnvelope,
  SubagentInvocation,
  ToolInvocation,
} from "./production-api";
import { isTerminalRun } from "./utils";

export type RunPhase = {
  phase: string;
  state: string;
  elapsed_seconds: number;
  cursor: number;
  worker_alive: boolean;
  reasoning_count: number;
  tool_count: number;
};

export type StreamStatus = "connecting" | "live" | "stale" | "closed";

type Options = {
  runId: string | null;
  /** Called when the server emits a terminal `close` event. */
  onClose?: (reason: string) => void;
  /** Notify when a heartbeat lands (for a header phase badge, alarms, etc.). */
  onHeartbeat?: (phase: RunPhase) => void;
};

/**
 * Subscribe to `/api/production/runs/:id/events` via SSE.
 *
 * Behaviour:
 *   * Uses the native EventSource so the browser handles reconnect with
 *     `Last-Event-ID` — no manual retry logic needed for transient drops.
 *   * Any incoming `run-event` merges into the cached `["run", id, "detail"]`
 *     React Query entry so components that render RunDetail update without
 *     re-fetching.
 *   * A silence detector flips `status` to "stale" when neither heartbeats
 *     nor events land for >45s (the server heartbeats every 20s, so 45s is
 *     conservative — twice missed).  UI surfaces this as a "Reconnecting…"
 *     banner.
 *   * On terminal `close` the EventSource is closed so the browser doesn't
 *     keep reconnecting to a finished run.
 */
export function useRunEvents({ runId, onClose, onHeartbeat }: Options): {
  status: StreamStatus;
  lastPhase: RunPhase | null;
} {
  const qc = useQueryClient();
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [lastPhase, setLastPhase] = useState<RunPhase | null>(null);

  useEffect(() => {
    if (!runId) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    const url = `/api/production/runs/${encodeURIComponent(runId)}/events`;
    const es = new EventSource(url, { withCredentials: true });

    const bump = () => {
      setStatus("live");
      if (staleTimer.current) clearTimeout(staleTimer.current);
      staleTimer.current = setTimeout(() => setStatus("stale"), 45_000);
    };

    // Arm the stale timer from the moment we connect so a silent stream
    // still flips to "stale" after 45s instead of hanging in "connecting".
    bump();

    es.addEventListener("run-event", (msg) => {
      bump();
      let event: RunEventEnvelope | null = null;
      try {
        event = JSON.parse((msg as MessageEvent).data);
      } catch {
        return;
      }
      if (!event) return;
      qc.setQueryData<RunDetail | undefined>(["run", runId, "detail"], (prev) =>
        prev ? mergeRunEvent(prev, event as RunEventEnvelope) : prev
      );
    });

    es.addEventListener("heartbeat", (msg) => {
      bump();
      try {
        const phase = JSON.parse((msg as MessageEvent).data) as RunPhase;
        setLastPhase(phase);
        onHeartbeat?.(phase);
        // Terminal state also carried through the heartbeat state field.
        if (isTerminalRun(phase.state)) {
          qc.invalidateQueries({ queryKey: ["run", runId, "detail"] });
        }
      } catch {
        /* ignore */
      }
    });

    es.addEventListener("warning", () => bump());

    es.addEventListener("close", (msg) => {
      let reason = "closed";
      try {
        const parsed = JSON.parse((msg as MessageEvent).data);
        reason = parsed.final_state || parsed.reason || "closed";
      } catch {
        /* ignore */
      }
      setStatus("closed");
      onClose?.(reason);
      es.close();
      qc.invalidateQueries({ queryKey: ["run", runId, "detail"] });
    });

    es.onopen = () => bump();
    es.onerror = () => {
      // Flip stale immediately and clear the idle timer — the browser will
      // auto-reconnect and a subsequent event / heartbeat re-bumps us live.
      if (staleTimer.current) clearTimeout(staleTimer.current);
      setStatus("stale");
    };

    return () => {
      es.close();
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { status, lastPhase };
}

/**
 * Merge a single incoming SSE run-event into the cached `RunDetail` snapshot.
 * The server-side event schema keys each event by `kind`; each kind maps to
 * one of the RunDetail arrays.  Unknown kinds are appended to `events` so
 * they still show up in the raw feed / inspector.
 */
export function mergeRunEvent(prev: RunDetail, event: RunEventEnvelope): RunDetail {
  const next: RunDetail = {
    ...prev,
    events: [...prev.events, event],
  };

  switch (event.kind) {
    case "reasoning": {
      const reasoning = event.payload as ReasoningEvent;
      next.reasoning = [...prev.reasoning, reasoning];
      break;
    }
    case "tool_intent":
    case "tool_started": {
      const tool = event.payload as ToolInvocation;
      next.tools = [...prev.tools.filter((t) => t.id !== tool.id), tool];
      break;
    }
    case "tool_result":
    case "tool_completed":
    case "tool_failed": {
      const tool = event.payload as ToolInvocation;
      next.tools = prev.tools.map((existing) =>
        existing.id === tool.id ? { ...existing, ...tool } : existing,
      );
      break;
    }
    case "subagent_started":
    case "subagent_state": {
      const sub = event.payload as SubagentInvocation;
      next.subagents = [...prev.subagents.filter((s) => s.id !== sub.id), sub];
      break;
    }
    case "human_request": {
      const req = event.payload as HumanRequest;
      next.human_requests = [...prev.human_requests, req];
      break;
    }
    case "human_resolved": {
      const req = event.payload as HumanRequest;
      next.human_requests = prev.human_requests.map((existing) =>
        existing.id === req.id ? { ...existing, ...req } : existing,
      );
      break;
    }
    case "artifact": {
      const artifact = event.payload as ArtifactRef;
      next.artifacts = [...prev.artifacts, artifact];
      break;
    }
    case "run_state": {
      const patch = event.payload as Partial<Run>;
      next.run = { ...prev.run, ...patch };
      break;
    }
    default:
      break;
  }

  return next;
}
