// tags: [ui-component, data-part, chat-stream-part, lucide-icons, i-t-e-m--s-t-a-t-u-s--v-a-r-i-a-n-t, todo-mutation-part, hypothesis-part, o-p--l-a-b-e-l-s, plan-snapshot-part, react-memo, PR-4A, PR-4E, optional-chaining]
import { memo } from "react";
import { Circle, ListChecks, RotateCcw, TestTube2 } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types (mirror of the translator's PlanItemEnvelope / plan data parts)
// ---------------------------------------------------------------------------

export interface PlanItemPartProps {
  id: string;
  title: string;
  status: "pending" | "in_progress" | "blocked" | "done" | "discarded";
  priority?: "low" | "normal" | "high" | "critical";
  dependencies?: string[];
  hypothesis?: string;
  evidence?: string;
  owner?: "agent" | "operator";
  change_reason?: string;
  updated_at_ms?: number;
}

export interface PlanSnapshotPartProps {
  goal: {
    id?: string;
    objective?: string;
    state?: string;
    success_criteria?: string[];
  } | null;
  items: PlanItemPartProps[];
  updatedAtMs?: number;
}

export interface TodoMutationPartProps {
  op: string;
  item?: PlanItemPartProps;
  reason?: string;
  resetIds?: string[];
}

export interface HypothesisPartProps {
  statement: string;
  status: string;
  evidence?: string;
}

// ---------------------------------------------------------------------------
// Plan item status mapping
// ---------------------------------------------------------------------------

const ITEM_STATUS_VARIANT: Record<PlanItemPartProps["status"], BadgeProps["variant"]> = {
  pending: "neutral",
  in_progress: "info",
  blocked: "warning",
  done: "success",
  discarded: "neutral",
};

const OP_LABELS: Record<string, string> = {
  add: "added",
  update: "updated",
  complete: "completed",
  block: "blocked",
  unblock: "unblocked",
  discard: "discarded",
  replan: "plan reset",
};

/** Safe status text — a partial payload must not crash the render. */
function statusText(status: string | undefined): string {
  return (status ?? "pending").replace("_", " ");
}

// ---------------------------------------------------------------------------
// Plan snapshot (data-plan) — durable goal + item board for the turn
// ---------------------------------------------------------------------------

export const PlanSnapshotPart = memo(function PlanSnapshotPart({
  goal,
  items,
  updatedAtMs,
}: PlanSnapshotPartProps) {
  return (
    <div className="w-full rounded-lg border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <ListChecks className="h-3.5 w-3.5 text-accent" />
        <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">
          Operation plan
        </p>
        {goal?.state && (
          <Badge variant={goal.state === "active" ? "info" : "neutral"} className="ml-auto">
            goal · {goal.state}
          </Badge>
        )}
      </div>

      {goal?.objective ? (
        <div className="space-y-1 px-3 py-2">
          <p className="text-xs uppercase tracking-wide text-secondary">Objective</p>
          <p className="text-sm text-body">{goal.objective}</p>
        </div>
      ) : null}

      {goal && goal.success_criteria && goal.success_criteria.length > 0 && (
        <ul className="space-y-1 px-3 pb-2">
          {goal.success_criteria.map((criterion) => (
            <li key={criterion} className="flex items-start gap-2 text-xs text-secondary">
              <Circle className="mt-1 h-1.5 w-1.5 shrink-0 fill-secondary" />
              <span>{criterion}</span>
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <ul className="divide-y divide-border border-t border-border">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-2 px-3 py-1.5 text-xs">
              <Badge variant={ITEM_STATUS_VARIANT[item.status] ?? "neutral"} className="w-16 shrink-0 justify-center">
                {statusText(item.status)}
              </Badge>
              <span className="min-w-0 truncate text-body" title={item.title}>
                {item.title}
              </span>
              {item.owner === "operator" && (
                <span className="ml-auto shrink-0 font-mono text-[0.6rem] uppercase text-accent">
                  op
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {items.length === 0 && !goal?.objective && (
        <p className="px-3 py-2 text-xs text-muted">No plan yet — the agent may draft one.</p>
      )}

      {updatedAtMs ? (
        <p className="border-t border-border px-3 py-1 font-mono text-[0.6rem] text-muted">
          updated {new Date(updatedAtMs).toLocaleTimeString()}
        </p>
      ) : null}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Todo mutation (data-todo / data-replan) — incremental plan deltas
// ---------------------------------------------------------------------------

export const TodoMutationPart = memo(function TodoMutationPart({
  op,
  item,
  reason,
  resetIds,
}: TodoMutationPartProps) {
  const label = OP_LABELS[op] ?? op;
  if (op === "replan") {
    return (
      <div className="flex items-start gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-xs text-secondary">
        <RotateCcw className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
        <span>
          <span className="text-warning">Plan reset</span>
          {resetIds && resetIds.length > 0 ? ` — ${resetIds.length} item(s) re-opened` : ""}
          {reason ? ` · ${reason}` : ""}
        </span>
      </div>
    );
  }

  if (!item) return null;
  return (
    <div className="flex items-start gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-xs text-secondary">
      <Badge variant={ITEM_STATUS_VARIANT[item.status] ?? "neutral"} className="w-16 shrink-0 justify-center">
        {statusText(item.status)}
      </Badge>
      <span className="min-w-0">
        <span className={cn("text-body")} title={item.title}>
          {item.title}
        </span>
        <span className="text-muted"> · {label}</span>
        {reason ? <span className="text-muted"> — {reason}</span> : null}
      </span>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Hypothesis (data-hypothesis) — a falsifiable claim the agent tracks
// ---------------------------------------------------------------------------

export const HypothesisPart = memo(function HypothesisPart({
  statement,
  status,
  evidence,
}: HypothesisPartProps) {
  return (
    <div className="border-l-4 border-info/60 bg-info/10 px-3 py-2 text-sm" role="note">
      <p className="mb-0.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-info">
        <TestTube2 className="h-3 w-3" /> Hypothesis · {status}
      </p>
      <p className="text-body">{statement}</p>
      {evidence && <p className="mt-1 text-xs text-secondary">Evidence: {evidence}</p>}
    </div>
  );
});
