"use client";

import { Activity, WifiOff } from "lucide-react";
import { cn, formatDuration } from "@/lib/utils";
import type { RunPhase, StreamStatus } from "@/lib/useRunEvents";

interface HeartbeatBarProps {
  status: StreamStatus;
  phase: RunPhase | null;
  compact?: boolean;
}

/**
 * Slim header-bar that surfaces "we are still connected and something is
 * happening even if you see nothing new".  Rendered in the FlightDeck header
 * whenever a run is live.  Under the hood it consumes the SSE heartbeat
 * payload — no polling required.
 */
export function HeartbeatBar({ status, phase, compact }: HeartbeatBarProps) {
  const isStale = status === "stale";
  const isConnecting = status === "connecting";
  const label = isStale
    ? "Reconnecting to run stream…"
    : isConnecting
      ? "Connecting to run stream…"
      : phase
        ? renderPhase(phase)
        : "Waiting for the first event…";

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded border px-2 py-1 text-xs",
        isStale
          ? "border-warning/40 bg-warning/10 text-warning"
          : "border-border bg-raised text-secondary"
      )}
    >
      {isStale ? (
        <WifiOff className="h-3.5 w-3.5" />
      ) : (
        <span className="relative flex h-2 w-2 items-center justify-center">
          <span
            className={cn(
              "absolute inline-flex h-full w-full rounded-full opacity-75",
              phase?.worker_alive ? "bg-success animate-ping" : "bg-muted"
            )}
          />
          <span className={cn("relative h-2 w-2 rounded-full", phase?.worker_alive ? "bg-success" : "bg-muted")} />
        </span>
      )}
      <span className={cn("font-mono", compact && "truncate max-w-[240px]")}>{label}</span>
      {phase && !isStale && !isConnecting && (
        <>
          <Activity className="h-3 w-3 text-muted" />
          <span className="font-mono text-muted">{formatDuration(phase.elapsed_seconds)}</span>
        </>
      )}
    </div>
  );
}

function renderPhase(phase: RunPhase): string {
  if (!phase.phase || phase.phase === "waiting") return `state: ${phase.state}`;
  const [kind, name] = phase.phase.split(":");
  if (kind === "tool") return `running tool · ${name}`;
  if (kind === "reasoning") return `reasoning · ${name}`;
  return phase.phase;
}
