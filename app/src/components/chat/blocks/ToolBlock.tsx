"use client";

import { useState } from "react";
import { ChevronRight, Terminal, CircleCheck, CircleX } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn, formatDuration } from "@/lib/utils";
import type { ToolInvocation } from "@/lib/production-api";

interface ToolBlockProps {
  tool: ToolInvocation;
}

const STATE_STYLE: Record<string, { color: string; label: (secs?: number) => string; Icon: typeof Terminal; running?: boolean }> = {
  running: { color: "text-info", label: () => "running…", Icon: Terminal, running: true },
  pending: { color: "text-info", label: () => "waiting…", Icon: Terminal, running: true },
  completed: { color: "text-success", label: (secs) => `done · ${formatDuration(secs || 0)}`, Icon: CircleCheck },
  success: { color: "text-success", label: (secs) => `done · ${formatDuration(secs || 0)}`, Icon: CircleCheck },
  failed: { color: "text-danger", label: () => "failed", Icon: CircleX },
  error: { color: "text-danger", label: () => "failed", Icon: CircleX },
  cancelled: { color: "text-muted", label: () => "cancelled", Icon: CircleX },
};

/**
 * A single tool call inside an assistant turn.  Header shows the tool name
 * and a live state chip; body expands to arguments and result JSON when
 * clicked (or automatically while running for the operator to watch stream).
 */
export function ToolBlock({ tool }: ToolBlockProps) {
  const style = STATE_STYLE[tool.state] || STATE_STYLE.pending;
  const [open, setOpen] = useState(style.running || false);
  const { Icon } = style;

  const durationSeconds =
    tool.started_at_ms && tool.finished_at_ms
      ? Math.max(0, (tool.finished_at_ms - tool.started_at_ms) / 1000)
      : undefined;

  return (
    <div className="relative pl-4">
      <span
        className={cn(
          "absolute left-0 top-2 bottom-2 w-[2px] rounded-full",
          style.running ? "bg-info animate-feather" : "bg-secondary/50"
        )}
        aria-hidden
      />
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex w-full items-center gap-1.5 text-left focus-visible:outline-none">
          <Icon className={cn("h-3.5 w-3.5", style.color)} />
          <span className="font-mono text-xs font-medium text-body">{tool.tool_name}</span>
          <span className={cn("font-mono text-[0.7rem] uppercase tracking-wider", style.color)}>
            {style.label(durationSeconds)}
          </span>
          {tool.agent_name && tool.agent_name !== "orchestrator" && (
            <span className="text-[0.7rem] text-muted">· {tool.agent_name}</span>
          )}
          {style.running && (
            <span className="ml-1 inline-flex gap-1">
              <span className="h-1 w-1 rounded-full bg-info animate-blink" style={{ animationDelay: "0ms" }} />
              <span className="h-1 w-1 rounded-full bg-info animate-blink" style={{ animationDelay: "200ms" }} />
              <span className="h-1 w-1 rounded-full bg-info animate-blink" style={{ animationDelay: "400ms" }} />
            </span>
          )}
          <ChevronRight
            className={cn("h-3 w-3 ml-auto text-muted transition-transform", open && "rotate-90")}
            aria-hidden
          />
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 space-y-2 animate-fade-slide">
          {tool.arguments !== undefined && tool.arguments !== null && (
            <ToolPayload label="arguments" value={tool.arguments} />
          )}
          {tool.result !== undefined && tool.result !== null && (
            <ToolPayload label="result" value={tool.result} />
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

function ToolPayload({ label, value }: { label: string; value: unknown }) {
  const pretty = typeof value === "string" ? value : safeStringify(value);
  return (
    <div className="rounded border border-border bg-bg/50">
      <div className="border-b border-border px-2 py-1 font-mono text-[0.65rem] uppercase tracking-wider text-muted">
        {label}
      </div>
      <pre className="max-h-[280px] overflow-auto p-2 font-mono text-[0.7rem] leading-relaxed text-body whitespace-pre-wrap">
        {pretty}
      </pre>
    </div>
  );
}

function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
