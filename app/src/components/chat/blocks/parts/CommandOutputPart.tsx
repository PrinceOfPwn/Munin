// tags: [ui-component, data-part, chat-stream-part, lucide-icons, client-component, command-output-part]
"use client";

import { Terminal, AlertTriangle, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface CommandOutputPartProps {
  toolName: string;
  stream: "stdout" | "stderr" | "meta";
  text: string;
  elapsedMs?: number;
  final?: boolean;
}

function elapsedLabel(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

/** A compact terminal line streamed from an authorized external command. */
export function CommandOutputPart({
  toolName,
  stream,
  text,
  elapsedMs = 0,
  final = false,
}: CommandOutputPartProps) {
  const isError = stream === "stderr";
  const isMeta = stream === "meta";
  return (
    <div
      className={cn(
        "overflow-hidden rounded-md border bg-[#0b0d12] font-mono text-[0.7rem]",
        isError ? "border-danger/40" : "border-border",
      )}
      role="log"
      aria-label={`${toolName} ${stream} output`}
    >
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-1.5 text-[0.6rem] uppercase tracking-widest text-muted">
        {isError ? (
          <AlertTriangle className="h-3 w-3 text-danger" aria-hidden />
        ) : final ? (
          <CheckCircle2 className="h-3 w-3 text-success" aria-hidden />
        ) : (
          <Terminal className="h-3 w-3 text-accent" aria-hidden />
        )}
        <span>{toolName}</span>
        <span className={cn(isError ? "text-danger" : isMeta ? "text-warning" : "text-secondary")}>
          {stream}
        </span>
        <span className="ml-auto text-muted">{elapsedLabel(elapsedMs)}</span>
      </div>
      <pre
        className={cn(
          "max-h-48 overflow-auto whitespace-pre-wrap break-words px-3 py-2 leading-relaxed",
          isError ? "text-danger/90" : isMeta ? "text-warning/90" : "text-secondary",
        )}
      >
        {text}
      </pre>
    </div>
  );
}
