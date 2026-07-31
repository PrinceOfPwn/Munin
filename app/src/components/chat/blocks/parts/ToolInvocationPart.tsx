"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

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

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
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
        <pre className="mt-1 max-h-48 overflow-auto rounded bg-raised p-2 text-xs font-mono text-body">
          {json}
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
export function ToolInvocationPart({
  toolCallId,
  toolName,
  args,
  state,
  result,
  error,
}: ToolInvocationPartProps) {
  const hasError = Boolean(error) || (typeof result === "string" && result.startsWith("ERROR:"));

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
      </div>

      {/* Args */}
      {args && Object.keys(args).length > 0 && (
        <JsonBlock label="args" value={args} />
      )}

      {/* Result or error */}
      {state === "result" && result !== undefined && (
        <JsonBlock label={hasError ? "error" : "result"} value={result} />
      )}

      {/* Explicit error prop */}
      {error && state !== "result" && (
        <p className="mt-1 text-xs text-danger">{error}</p>
      )}
    </div>
  );
}
