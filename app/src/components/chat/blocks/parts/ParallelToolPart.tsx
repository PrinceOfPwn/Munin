import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ParallelInvocation {
  toolCallId: string;
  toolName: string;
  state: string;
}

export interface ParallelToolPartProps {
  groupId: string;
  invocations: ParallelInvocation[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function chipColour(state: string): string {
  switch (state) {
    case "partial-call":
      return "bg-muted text-muted-foreground border-muted";
    case "call":
      return "bg-yellow-100 text-yellow-800 border-yellow-200 dark:bg-yellow-900/40 dark:text-yellow-300 dark:border-yellow-800";
    case "result":
      return "bg-green-100 text-green-800 border-green-200 dark:bg-green-900/40 dark:text-green-300 dark:border-green-800";
    default:
      return "bg-destructive/10 text-destructive border-destructive/20";
  }
}

function stateIcon(state: string): string {
  switch (state) {
    case "partial-call":
      return "⋯";
    case "call":
      return "⟳";
    case "result":
      return "✓";
    default:
      return "✕";
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Displays a horizontal grid of tool status chips for invocations that were
 * dispatched in parallel within the same agent step.
 */
export function ParallelToolPart({ groupId, invocations }: ParallelToolPartProps) {
  return (
    <div
      className="rounded-md border border-border bg-card px-3 py-2"
      data-parallel-group-id={groupId}
    >
      <p className="mb-1.5 text-xs text-muted-foreground">Parallel tools</p>
      <div className="flex flex-wrap gap-2">
        {invocations.map((inv) => (
          <span
            key={inv.toolCallId}
            className={cn(
              "inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-mono font-medium",
              chipColour(inv.state)
            )}
            title={`${inv.toolName} — ${inv.state}`}
          >
            <span aria-hidden>{stateIcon(inv.state)}</span>
            {inv.toolName}
          </span>
        ))}
      </div>
    </div>
  );
}
