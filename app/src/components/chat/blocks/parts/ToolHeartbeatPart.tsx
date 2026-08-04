// tags: [ui-component, data-part, chat-stream-part, lucide-icons, client-component, tool-heartbeat-part, react-memo, PR-4A]
"use client";

import { memo } from "react";
import { LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ToolHeartbeatPartProps {
  toolName: string;
  elapsedMs?: number;
  lastOutputMs?: number;
  text?: string;
}

function seconds(ms: number): string {
  return `${Math.max(0, Math.floor(ms / 1000))}s`;
}

/** Visible liveness marker for a quiet command; avoids a frozen-looking UI. */
export const ToolHeartbeatPart = memo(function ToolHeartbeatPart({
  toolName,
  elapsedMs = 0,
  lastOutputMs = 0,
  text = "command still running",
}: ToolHeartbeatPartProps) {
  return (
    <div
      className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning"
      role="status"
      aria-live="off"
      aria-label={`${toolName} ${text}`}
    >
      <LoaderCircle className={cn("h-3.5 w-3.5 animate-spin", "motion-reduce:animate-none")} aria-hidden />
      <span className="font-mono uppercase tracking-wide">{toolName}</span>
      <span className="text-warning/80">{text}</span>
      <span aria-hidden className="ml-auto font-mono text-[0.65rem] text-warning/70">
        {seconds(elapsedMs)} · quiet {seconds(lastOutputMs)}
      </span>
    </div>
  );
});
