"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Check,
  X,
  Clock,
  Terminal,
} from "lucide-react";
import { categorize } from "@/lib/categories";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import JsonViewer from "./JsonViewer";
import type { ToolCall } from "@/types/mcp";

interface ToolCallCardProps {
  call: ToolCall;
}

export default function ToolCallCard({ call }: ToolCallCardProps) {
  const cat = categorize(call.name);
  const [open, setOpen] = useState(false);
  const isRunning = call.status === "running";
  const isError = call.status === "error";
  const isGen = call.name.startsWith("gen__");

  const elapsed = call.endTime ? call.endTime - call.startTime : null;

  return (
    <div
      className="my-2 rounded-md border bg-surface/60 animate-fade-slide"
      style={{ borderColor: cat.color + "55" }}
    >
      {/* Header */}
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        <span
          className="inline-flex items-center justify-center w-5 h-5 rounded-sm shrink-0"
          style={{ backgroundColor: cat.color + "22", color: cat.color }}
        >
          {isRunning ? (
            <Loader2 size={12} className="animate-spin" />
          ) : isError ? (
            <X size={13} />
          ) : (
            <Check size={13} />
          )}
        </span>

        <span
          className="text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm"
          style={{ backgroundColor: cat.color + "22", color: cat.color }}
        >
          {cat.label}
        </span>

        <span className="font-mono text-sm text-body flex items-center gap-1">
          <Terminal size={11} className="text-muted" />
          {call.name}
          {isGen && (
            <span className="ml-1 text-amber" title="Forged tool">★</span>
          )}
        </span>

        <span className="ml-auto flex items-center gap-2 text-[11px] text-muted">
          {isRunning ? (
            <span className="flex items-center gap-1 text-rose">
              <Clock size={10} /> running…
            </span>
          ) : elapsed !== null ? (
            <span className="flex items-center gap-1">
              <Clock size={10} /> {formatDuration(elapsed)}
            </span>
          ) : null}
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {/* Body */}
      {open && (
        <div className="border-t border-border px-3 py-2 space-y-2">
          {/* Arguments */}
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted font-mono mb-1">
              Arguments
            </div>
            {Object.keys(call.arguments).length === 0 ? (
              <div className="text-muted text-xs font-mono">(none)</div>
            ) : (
              <JsonViewer data={call.arguments} expanded maxExpandDepth={4} />
            )}
          </div>

          {/* Result / Error */}
          {isError ? (
            <div className="rounded border border-rose/50 bg-rose/5 p-2">
              <div className="text-[10px] uppercase tracking-wider text-rose font-mono mb-1">
                Error
              </div>
              {call.error ? (
                <div className="space-y-1">
                  <div className="text-rose text-xs font-mono">
                    code: <span className="text-body">{String(call.error.code)}</span>
                  </div>
                  <div className="text-rose text-xs font-mono">
                    message: <span className="text-body">{call.error.message}</span>
                  </div>
                  {call.error.data !== undefined && (
                    <div className="mt-1">
                      <JsonViewer data={call.error.data} expanded maxExpandDepth={4} />
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-rose text-xs">Unknown error</div>
              )}
            </div>
          ) : call.result !== undefined ? (
            <div>
              <div className="text-[10px] uppercase tracking-wider text-success font-mono mb-1">
                Result
              </div>
              <JsonViewer data={call.result} expanded maxExpandDepth={4} />
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
