// tags: [ui-component, data-part, chat-stream-part, lucide-icons, goal-part]
import { Target } from "lucide-react";

import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GoalPartProps {
  goal: {
    id?: string;
    objective?: string;
    state?: string;
    success_criteria?: string[];
  } | null;
  state?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Displays the persistent goal bound to the current conversation (data-goal).
 * GOAL / BEAST modes operate against this goal; the panel surfaces its state
 * transitions (active / paused / done / cancelled).
 */
export function GoalPart({ goal, state }: GoalPartProps) {
  const goalState = state ?? goal?.state ?? "unknown";
  if (!goal?.objective && goalState === "unknown") return null;

  return (
    <div className="w-full rounded-lg border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Target className="h-3.5 w-3.5 text-accent" />
        <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">Goal</p>
        <Badge
          variant={
            goalState === "active"
              ? "info"
              : goalState === "done"
                ? "success"
                : goalState === "cancelled"
                  ? "danger"
                  : "neutral"
          }
          className="ml-auto"
        >
          {goalState}
        </Badge>
      </div>
      {goal?.objective ? (
        <div className="space-y-1 px-3 py-2">
          <p className="text-sm text-body">{goal.objective}</p>
          {goal.success_criteria && goal.success_criteria.length > 0 && (
            <ul className="space-y-0.5 pt-1">
              {goal.success_criteria.map((criterion) => (
                <li key={criterion} className="flex items-start gap-2 text-xs text-secondary">
                  <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent/60" />
                  <span>{criterion}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <p className="px-3 py-2 text-xs text-muted">
          {goalState === "active" ? "Goal attached (no objective text yet)" : `Goal state: ${goalState}`}
        </p>
      )}
    </div>
  );
}
