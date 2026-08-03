// tags: [ui-component, data-part, chat-stream-part, client-component, use-state, status-badge, json-block, tool-invocation-part, react-memo, PR-4A, expand-copy, PR-4H]
"use client";

import { memo, useState } from "react";
import { Maximize2, Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { logError } from "@/lib/logError";
import { FloatingWindow } from "@/components/ui/floating-window";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ToolInvocationState = "partial-call" | "call" | "result";

export interface ToolInvocationPartProps {
  toolCallId: string;
  toolName: string;
  args?: Record<string, unknown>;
  state: ToolInvocationState;
  result?: unknown;
  error?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Serialize the full invocation payload for the clipboard — never crash on a non-JSON value. */
function buildCopyText(
  toolName: string,
  state: ToolInvocationState,
  args: Record<string, unknown> | undefined,
  result: unknown,
  error: string | undefined,
): string {
  const payload: Record<string, unknown> = { tool: toolName, state };
  if (args && Object.keys(args).length > 0) payload.args = args;
  if (state === "result" && result !== undefined) payload.result = result;
  if (error) payload.error = error;
  try {
    return JSON.stringify(payload, null, 2);
  } catch (cause) {
    logError({
      context: "clipboard",
      error: cause,
      meta: { component: "ToolInvocationPart", toolCallId: "copy-build", toolName },
    });
    return String(payload);
  }
}

function StatusBadge({ state, hasError }: { state: ToolInvocationState; hasError: boolean }) {
  if (hasError) {
    return (
      <span className="rounded bg-danger/15 px-1.5 py-0.5 text-xs font-medium text-danger">
        failed
      </span>
    );
  }

  const label =
    state === "partial-call"
      ? "preparing"
      : state === "call"
      ? "running"
      : "done";

  const colourClass =
    state === "partial-call"
      ? "bg-raised text-muted"
      : state === "call"
      ? "bg-warning/20 text-warning"
      : "bg-success/20 text-success";

  return (
    <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", colourClass)}>
      {label}
    </span>
  );
}

function JsonBlock({
  label,
  value,
  initiallyOpen = false,
}: {
  label: string;
  value: unknown;
  initiallyOpen?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const json = JSON.stringify(value, null, 2);

  return (
    <div className="mt-1">
      <button
        onClick={() => setOpen((o) => !o)}
        className="text-xs text-muted underline-offset-2 hover:text-secondary hover:underline"
        aria-expanded={open}
      >
        {open ? "▾" : "▸"} {label}
      </button>
      {open && (
        <pre className="mt-1 max-h-72 overflow-auto rounded bg-raised p-2 text-xs font-mono text-body">
          {json ?? String(value ?? "")}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Renders a tool invocation card.
 * Shows the tool name, status badge, and collapsible args / result sections.
 */
export const ToolInvocationPart = memo(function ToolInvocationPart({
  toolCallId,
  toolName,
  args,
  state,
  result,
  error,
}: ToolInvocationPartProps) {
  const hasError = Boolean(error) || (typeof result === "string" && result.startsWith("ERROR:"));
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copyInvocation() {
    if (typeof navigator === "undefined" || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(buildCopyText(toolName, state, args, result, error));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_600);
    } catch (cause) {
      logError({
        context: "clipboard",
        error: cause,
        meta: { component: "ToolInvocationPart", toolCallId },
      });
    }
  }

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        hasError ? "border-danger/40 bg-danger/5" : "border-border bg-surface"
      )}
      data-tool-call-id={toolCallId}
    >
      {/* Header row */}
      <div className="flex items-center gap-2">
        <span className="font-mono font-medium text-body">{toolName}</span>
        <StatusBadge state={state} hasError={hasError} />
        <span className="ml-auto flex items-center gap-1">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            title="Expand full invocation payload"
            aria-label="Expand full invocation payload"
            className="rounded p-1 text-muted transition-colors hover:bg-bg hover:text-body"
          >
            <Maximize2 className="h-3.5 w-3.5" aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => void copyInvocation()}
            title="Copy full invocation payload"
            aria-label="Copy full invocation payload"
            className="rounded p-1 text-muted transition-colors hover:bg-bg hover:text-body"
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-success" aria-hidden />
            ) : (
              <Copy className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        </span>
      </div>

      {/* Args */}
      {args && Object.keys(args).length > 0 && (
        <JsonBlock label="args" value={args} />
      )}

      {/* Result or error */}
      {state === "result" && result !== undefined && (
        <JsonBlock
          label={hasError ? "error" : "result"}
          value={result}
          initiallyOpen
        />
      )}

      {/* Explicit error prop */}
      {error && state !== "result" && (
        <p className="mt-1 text-xs text-danger">{error}</p>
      )}

      {expanded && (
        <FloatingWindow
          id={`tool-invocation:${toolCallId || toolName}`}
          title={`${toolName} · invocation payload`}
          onClose={() => setExpanded(false)}
          defaultSize={{ width: 640, height: 480 }}
        >
          <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words bg-bg p-4 font-mono text-xs leading-relaxed text-secondary">
            {buildCopyText(toolName, state, args, result, error)}
          </pre>
        </FloatingWindow>
      )}
    </div>
  );
});
