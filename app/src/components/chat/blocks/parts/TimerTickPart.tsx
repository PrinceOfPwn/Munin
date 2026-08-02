import { Timer } from "lucide-react";

import { cn, formatDuration } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TimerTickPartProps {
  timerId: string;
  timerKind?: string;
  goalId?: string;
  tickCount?: number;
  dueAtMs?: number;
  lastTickAtMs?: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Surfaces a durable server-side timer tick (data-timer-tick). Ticks may
 * wake a GOAL run for re-evaluation; the widget stays monospace and compact
 * so the stream reads as an audit trail.
 */
export function TimerTickPart({
  timerId,
  timerKind,
  goalId,
  tickCount,
  dueAtMs,
  lastTickAtMs,
}: TimerTickPartProps) {
  const label = timerKind ? `${timerKind} timer` : "timer";
  return (
    <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 font-mono text-[0.65rem] text-secondary">
      <Timer className={cn("h-3 w-3 shrink-0", tickCount && tickCount > 0 ? "text-accent" : "text-muted")} />
      <span className="uppercase tracking-widest text-muted">{label}</span>
      <span>
        tick · <span className="text-body">{tickCount ?? 0}</span>
      </span>
      {goalId && <span className="truncate">goal · {goalId.slice(0, 8)}</span>}
      {lastTickAtMs && (
        <span className="ml-auto">
          {new Date(lastTickAtMs).toLocaleTimeString()}
          {dueAtMs && dueAtMs > 0 ? ` · next ${formatDuration(Math.max(0, Math.round((dueAtMs - Date.now()) / 1000)))}` : ""}
        </span>
      )}
    </div>
  );
}
