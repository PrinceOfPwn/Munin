"use client";

/**
 * Grouped rendering for N tools that share the same `parallel_group_id`.
 *
 * The dispatcher tags every ``tool_calls`` row emitted from the same
 * assistant message with a shared UUID.  Instead of stacking them as N
 * sibling ``<ToolBlock>``s (which reads like a serial cascade), we
 * collapse the group into a single "Running M tools in parallel" widget:
 *
 * ┌ Running 3 tools in parallel ─────────────────
 * │ ● ldap_query   { base: "dc=acme,dc=corp" }   running · 4s
 * │ ✓ http_probe   { url: "https://acme.corp" }  done · 3s
 * │ ✓ scope_check  { target: "acme" }            done · 1s
 * └─────────────────────────────────────────────
 *
 * Behavioural rules:
 *   * If any child is still running the group renders in "live" mode with a
 *     pulsing rail and expanded by default.
 *   * Once every child terminates, the group collapses to a single-line
 *     summary showing total wall-clock time (max of finished_at_ms − min of
 *     started_at_ms; NOT the sum, because the group ran concurrently).  A
 *     click re-expands to the detail view for post-hoc inspection.
 */
import { useState, useMemo } from "react";
import { ChevronRight, CircleCheck, CircleX, Layers } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn, formatDuration } from "@/lib/utils";
import type { ToolInvocation } from "@/lib/production-api";
import { ToolBlock } from "./ToolBlock";

interface Props {
  groupId: string;
  tools: ToolInvocation[];
}

export function ParallelToolBlock({ groupId, tools }: Props) {
  const running = tools.some((t) => t.state === "running" || t.state === "pending");
  const failed = tools.some((t) => t.state === "failed" || t.state === "error");
  const [open, setOpen] = useState(running);

  const summary = useMemo(() => {
    const starts = tools.map((t) => t.started_at_ms || 0).filter(Boolean);
    const finishes = tools.map((t) => t.finished_at_ms || 0).filter(Boolean);
    const minStart = starts.length ? Math.min(...starts) : 0;
    const maxFinish = finishes.length ? Math.max(...finishes) : 0;
    const totalSeconds = maxFinish && minStart ? Math.max(0, (maxFinish - minStart) / 1000) : 0;
    return { totalSeconds };
  }, [tools]);

  const Icon = running ? Layers : failed ? CircleX : CircleCheck;
  const railColor = running
    ? "bg-info animate-feather"
    : failed
      ? "bg-danger/70"
      : "bg-success/70";

  return (
    <div className="relative pl-4">
      <span
        className={cn("absolute left-0 top-2 bottom-2 w-[2px] rounded-full", railColor)}
        aria-hidden
      />
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex w-full items-center gap-1.5 text-left focus-visible:outline-none">
          <Icon
            className={cn(
              "h-3.5 w-3.5",
              running ? "text-info" : failed ? "text-danger" : "text-success",
            )}
          />
          <span className="font-mono text-xs font-medium text-body">
            {running
              ? `Running ${tools.length} tool${tools.length === 1 ? "" : "s"} in parallel`
              : `${tools.length} tool${tools.length === 1 ? "" : "s"} · ${formatDuration(summary.totalSeconds)} total (parallel)`}
          </span>
          <span className="ml-1 font-mono text-[0.6rem] text-muted">
            group {groupId.slice(0, 6)}
          </span>
          <ChevronRight
            className={cn("ml-auto h-3 w-3 text-muted transition-transform", open && "rotate-90")}
            aria-hidden
          />
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 space-y-1.5 animate-fade-slide">
          {tools.map((tool) => (
            <ToolBlock key={tool.id} tool={tool} />
          ))}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
