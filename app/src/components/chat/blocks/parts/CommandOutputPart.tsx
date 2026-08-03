// tags: [ui-component, data-part, chat-stream-part, lucide-icons, client-component, command-output-part, react-memo, PR-4A, expand-copy, PR-4H]
"use client";

import { memo, useState } from "react";
import { Terminal, AlertTriangle, CheckCircle2, Maximize2, Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { logError } from "@/lib/logError";
import { FloatingWindow } from "@/components/ui/floating-window";

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
export const CommandOutputPart = memo(function CommandOutputPart({
  toolName,
  stream,
  text,
  elapsedMs = 0,
  final = false,
}: CommandOutputPartProps) {
  const isError = stream === "stderr";
  const isMeta = stream === "meta";
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copyOutput() {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_600);
    } catch (error) {
      logError({
        context: "clipboard",
        error,
        meta: { component: "CommandOutputPart", toolName, stream },
      });
    }
  }

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
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            title="Expand full output"
            aria-label="Expand full output"
            className="rounded p-1 text-muted transition-colors hover:bg-bg hover:text-body"
          >
            <Maximize2 className="h-3 w-3" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => void copyOutput()}
            title="Copy full output"
            aria-label="Copy full output"
            className="rounded p-1 text-muted transition-colors hover:bg-bg hover:text-body"
          >
            {copied ? (
              <Check className="h-3 w-3 text-success" aria-hidden />
            ) : (
              <Copy className="h-3 w-3" aria-hidden />
            )}
          </button>
          <span>{elapsedLabel(elapsedMs)}</span>
        </span>
      </div>
      <pre
        className={cn(
          "max-h-48 overflow-auto whitespace-pre-wrap break-words px-3 py-2 leading-relaxed",
          isError ? "text-danger/90" : isMeta ? "text-warning/90" : "text-secondary",
        )}
      >
        {text}
      </pre>

      {expanded && (
        <FloatingWindow
          id={`command-output:${toolName}:${stream}`}
          title={`${toolName} · ${stream} output`}
          icon={<Terminal className="h-3.5 w-3.5" />}
          onClose={() => setExpanded(false)}
          defaultSize={{ width: 720, height: 480 }}
        >
          <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-bg p-4 font-mono text-xs leading-relaxed text-secondary">
            {text}
          </pre>
        </FloatingWindow>
      )}
    </div>
  );
});
