"use client";

import { useState } from "react";
import { Brain, ChevronRight } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn, formatDuration } from "@/lib/utils";

interface ThoughtBlockProps {
  content: string;
  agent?: string;
  kind?: string;
  running?: boolean;
  durationSeconds?: number;
  step?: number;
}

/**
 * Renders a reasoning / thought event.  Compact by default (title chip only),
 * expanding on click into the full body.  Auto-expands while the associated
 * step is still running so the operator sees streaming reasoning.
 */
export function ThoughtBlock({ content, agent, kind, running = false, durationSeconds, step }: ThoughtBlockProps) {
  const [open, setOpen] = useState<boolean>(running);
  const chip = running
    ? "thinking…"
    : durationSeconds !== undefined
      ? `thought · ${formatDuration(durationSeconds)}`
      : "thought";

  return (
    <div className="relative pl-4">
      <span
        className={cn(
          "absolute left-0 top-2 bottom-2 w-[2px] rounded-full bg-accent/60",
          running && "animate-feather"
        )}
        aria-hidden
      />
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          className={cn(
            "flex items-center gap-1.5 text-[0.7rem] font-mono uppercase tracking-wider text-accent hover:text-accent-hover transition-colors",
            "focus-visible:outline-none"
          )}
        >
          <Brain className="h-3.5 w-3.5" />
          <span>{chip}</span>
          {kind && kind !== "provider_reasoning" && (
            <span className="text-muted normal-case tracking-normal">· {kind.replaceAll("_", " ")}</span>
          )}
          {agent && <span className="text-muted normal-case tracking-normal">· {agent}</span>}
          {step !== undefined && <span className="text-muted">· step {step}</span>}
          <ChevronRight
            className={cn("h-3 w-3 transition-transform", open && "rotate-90")}
            aria-hidden
          />
        </CollapsibleTrigger>
        <CollapsibleContent className="mt-2 text-xs leading-relaxed text-secondary whitespace-pre-wrap animate-fade-slide">
          {content || <span className="italic text-muted">(no reasoning content)</span>}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
