import { CircleDashed } from "lucide-react";

import { cn } from "@/lib/utils";

export interface OperationalTracePartProps {
  stage: string;
  text: string;
}

/** A concise, replayable activity record — never private model reasoning. */
export function OperationalTracePart({ stage, text }: OperationalTracePartProps) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-start gap-x-2 gap-y-1 rounded-md border border-dashed border-border/80 bg-muted/10 px-3 py-2 sm:flex-nowrap sm:items-center",
        "text-xs text-secondary",
      )}
      role="group"
      aria-label={`Agent activity: ${stage}`}
    >
      <CircleDashed className="h-3.5 w-3.5 shrink-0 animate-spin text-accent motion-reduce:animate-none" aria-hidden />
      <span className="shrink-0 font-mono uppercase tracking-wide text-muted">{stage}</span>
      <span className="min-w-0 break-words sm:truncate">{text}</span>
    </div>
  );
}
