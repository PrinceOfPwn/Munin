// tags: [ui-component, data-part, chat-stream-part, subagent-presence-part, react-memo, PR-4A]
import { memo } from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SubagentPresencePartProps {
  subagentId: string;
  name: string;
  state: string;
  onOpenWindow?: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function stateLabel(state: string): string {
  switch (state) {
    case "started":
      return "Starting";
    case "running":
      return "Running";
    case "completed":
      return "Done";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
    default:
      return state;
  }
}

function stateDotColour(state: string): string {
  switch (state) {
    case "started":
    case "running":
      return "bg-warning animate-pulse";
    case "completed":
      return "bg-success";
    case "failed":
    case "cancelled":
      return "bg-danger";
    default:
      return "bg-muted";
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Renders a subagent presence card with the agent name, current state, and an
 * optional button that opens the forge floating chat window for that subagent.
 */
export const SubagentPresencePart = memo(function SubagentPresencePart({
  subagentId,
  name,
  state,
  onOpenWindow,
}: SubagentPresencePartProps) {
  return (
    <div
      className="flex items-center justify-between rounded-md border border-border bg-surface px-3 py-2"
      data-subagent-id={subagentId}
    >
      <div className="flex items-center gap-2">
        {/* Status dot */}
        <span
          className={cn("h-2 w-2 rounded-full shrink-0", stateDotColour(state))}
          aria-hidden
        />

        {/* Agent name & state */}
        <div className="leading-tight">
          <p className="text-sm font-medium text-body">{name}</p>
          <p className="text-xs text-secondary">{stateLabel(state)}</p>
        </div>
      </div>

      {/* Open window button */}
      {onOpenWindow && (
        <button
          onClick={onOpenWindow}
          className={cn(
            "rounded px-2 py-1 text-xs font-medium transition-colors",
            "bg-raised text-body hover:bg-active"
          )}
          aria-label={`Open window for subagent ${name}`}
        >
          Open window
        </button>
      )}
    </div>
  );
});
