"use client";

/**
 * Inline block rendered inside a Munin turn to show a collaborator's guidance
 * that was queued while the run was executing.
 *
 * Visual grammar:
 *   * Left rail: dashed accent border so the block reads as "injected from
 *     outside the model's own reasoning".
 *   * Avatar + username + timestamp header.
 *   * Body renders the guidance body verbatim (preserving whitespace).
 *   * Footer chip shows delivery status:
 *       - "queued"                → not yet consumed by the ReAct loop
 *       - "delivered at step N"   → consumed_at_ms + delivered_at_step set
 *
 * The block is intentionally small; multi-line guidance is fine, but very
 * long copy is capped with a "show more" affordance to keep the timeline
 * scannable.
 */
import { useState } from "react";
import { Sparkles, ChevronDown, ChevronUp, Clock } from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { cn, formatWhen } from "@/lib/utils";
import type { QueuedGuidance } from "@/lib/production-api";

interface Props {
  guidance: QueuedGuidance;
}

const MAX_INLINE = 320;

export function GuidanceBlock({ guidance }: Props) {
  const [expanded, setExpanded] = useState(false);
  const overflows = guidance.body.length > MAX_INLINE;
  const preview = overflows && !expanded ? guidance.body.slice(0, MAX_INLINE) + "…" : guidance.body;
  const delivered = Boolean(guidance.consumed_at_ms);

  return (
    <div
      className={cn(
        "rounded-md border border-dashed border-accent/60 bg-accent-soft/30 px-3 py-2",
        "text-body",
      )}
    >
      <header className="flex items-center gap-2">
        <Avatar name={guidance.actor_username || guidance.actor_id} size="xs" />
        <span className="font-mono text-[0.65rem] uppercase tracking-wider text-accent">
          <Sparkles className="mr-1 inline h-3 w-3" />
          Operator guidance
        </span>
        <span className="text-[0.65rem] text-muted">
          from {guidance.actor_username || guidance.actor_id} · {formatWhen(guidance.created_at_ms)}
        </span>
        <span className="ml-auto">
          {delivered ? (
            <Badge variant="success">
              delivered
              {guidance.delivered_at_step ? ` @ step ${guidance.delivered_at_step}` : ""}
            </Badge>
          ) : (
            <Badge variant="warning">
              <Clock className="mr-1 h-3 w-3" /> queued
            </Badge>
          )}
        </span>
      </header>
      <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed">{preview}</p>
      {overflows && (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="mt-1 inline-flex items-center gap-1 text-[0.65rem] text-muted transition-colors hover:text-body"
        >
          {expanded ? (
            <>
              <ChevronUp className="h-3 w-3" /> collapse
            </>
          ) : (
            <>
              <ChevronDown className="h-3 w-3" /> show more
            </>
          )}
        </button>
      )}
      {guidance.target_agent_id && (
        <div className="mt-1 text-[0.6rem] text-muted">
          → routed to <span className="font-mono">{guidance.target_agent_id}</span>
        </div>
      )}
    </div>
  );
}
