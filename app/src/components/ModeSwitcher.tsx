// tags: [ui-component, console-surface, lucide-icons, client-component, use-state, m-o-d-e-s, mode-switcher, e-m-p-t-y--g-o-a-l--d-r-a-f-t]
"use client";

import { useState } from "react";
import { Gauge, Target } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type OperationMode = "standard" | "yolo" | "goal" | "beast";

export interface GoalDraft {
  objective: string;
  successCriteria: string;
  scopeJson: string;
}

export const EMPTY_GOAL_DRAFT: GoalDraft = {
  objective: "",
  successCriteria: "",
  scopeJson: "",
};

export const MODES: {
  value: OperationMode;
  label: string;
  description: string;
  badge: "neutral" | "warning" | "info" | "danger";
}[] = [
  { value: "standard", label: "Standard", description: "HITL approvals as usual", badge: "neutral" },
  { value: "yolo", label: "YOLO", description: "No approvals — admin only", badge: "warning" },
  { value: "goal", label: "GOAL", description: "Autonomous toward a persistent goal", badge: "info" },
  { value: "beast", label: "BEAST", description: "Goal + explicit scope, full autonomy", badge: "danger" },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * `ModeSwitcher` — operation mode selector for the console composer.
 *
 * Standard keeps the human-in-the-loop approval flow; YOLO / GOAL / BEAST
 * relax approvals per `OperationMode` policy server-side. GOAL and BEAST
 * carry a goal draft (objective + success criteria + optional JSON scope)
 * which is sent to `/api/chat` as `body.goal` on the next turn.
 */
export function ModeSwitcher({
  mode,
  onChangeMode,
  goal,
  onChangeGoal,
}: {
  mode: OperationMode;
  onChangeMode: (mode: OperationMode) => void;
  goal: GoalDraft;
  onChangeGoal: (goal: GoalDraft) => void;
}) {
  const [open, setOpen] = useState(false);
  const needsGoal = mode === "goal" || mode === "beast";
  const objectiveReady = goal.objective.trim().length > 0;

  return (
    <div className="flex items-center gap-2">
      {MODES.map((item) => {
        const active = mode === item.value;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onChangeMode(item.value)}
            title={item.description}
            aria-pressed={active}
            className={cn(
              "rounded px-2 py-1 font-mono text-[0.65rem] uppercase tracking-widest transition-colors",
              active
                ? "border border-border bg-accent-soft text-accent"
                : "border border-transparent text-muted hover:text-body",
            )}
          >
            {item.label}
          </button>
        );
      })}

      <details
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
        className={cn("relative", needsGoal && "ml-1")}
      >
        <summary className="flex cursor-pointer list-none items-center gap-1 rounded border border-border bg-surface px-2 py-1 text-[0.65rem] text-secondary hover:text-body">
          <Target className="h-3 w-3 text-accent" />
          Goal
          {needsGoal && (
            <Badge variant={objectiveReady ? "info" : "warning"}>
              {objectiveReady ? "set" : "required"}
            </Badge>
          )}
        </summary>
        <div className="absolute bottom-9 left-0 z-30 w-96 rounded-md border border-border bg-surface p-3 shadow-xl">
          <label className="mb-1 block text-[0.65rem] uppercase tracking-widest text-muted">
            Objective
          </label>
          <Textarea
            rows={2}
            placeholder="e.g. Map the exposed attack surface of the target and confirm two findings"
            value={goal.objective}
            onChange={(event) => onChangeGoal({ ...goal, objective: event.target.value })}
            className="mb-2 text-xs"
          />
          <label className="mb-1 block text-[0.65rem] uppercase tracking-widest text-muted">
            Success criteria <span className="normal-case">(one per line)</span>
          </label>
          <Textarea
            rows={3}
            placeholder={"e.g.\nLive web service identified\nCredentials not exfiltrated"}
            value={goal.successCriteria}
            onChange={(event) => onChangeGoal({ ...goal, successCriteria: event.target.value })}
            className="mb-2 text-xs"
          />
          <label className="mb-1 block text-[0.65rem] uppercase tracking-widest text-muted">
            Scope <span className="normal-case">(JSON, required in BEAST)</span>
          </label>
          <Textarea
            rows={3}
            placeholder={'{"targets": ["10.0.0.0/24"], "exclude": []}'}
            value={goal.scopeJson}
            onChange={(event) => onChangeGoal({ ...goal, scopeJson: event.target.value })}
            className="mb-2 text-xs font-mono"
          />
          <p className="mb-2 text-[0.65rem] leading-relaxed text-muted">
            {mode === "beast"
              ? "BEAST refuses turns without an explicit scope. The goal persists on the conversation; later turns may reuse its id."
              : "GOAL runs autonomously toward this objective until it is marked done, paused, or cancelled."}
          </p>
          <Button
            type="button"
            size="sm"
            onClick={() => setOpen(false)}
            disabled={needsGoal && !objectiveReady}
            className="w-full"
          >
            <Gauge className="h-3.5 w-3.5" /> Apply goal
          </Button>
        </div>
      </details>
    </div>
  );
}
