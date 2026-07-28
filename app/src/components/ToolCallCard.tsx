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
  FileCode2,
} from "lucide-react";
import { categorize } from "@/lib/categories";
import { formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";
import JsonViewer from "./JsonViewer";
import ArtifactActions from "./ArtifactActions";
import type { ToolCall } from "@/types/mcp";

interface ToolCallCardProps {
  call: ToolCall;
}

export default function ToolCallCard({ call }: ToolCallCardProps) {
  const cat = categorize(call.name);
  const [open, setOpen] = useState(call.status === "running");
  const isRunning = call.status === "running";
  const isError = call.status === "error";
  const isGen = call.name.startsWith("gen__");

  const elapsed = call.endTime ? call.endTime - call.startTime : null;
  const summary = describeCall(call);
  const artifact = findArtifact(call.result);

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

        {!open && summary && <span className="hidden md:block text-xs text-muted truncate max-w-[32ch]">{summary}</span>}

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
          {summary && (
            <div className={cn("rounded border px-2 py-1.5 text-xs", isError ? "border-rose/30 bg-rose/5 text-rose" : "border-border bg-bg/40 text-body")}>
              <span className="uppercase tracking-wider text-[10px] font-mono text-muted mr-2">Outcome</span>
              {summary}
            </div>
          )}
          {/* Arguments */}
          <details className="group">
            <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-muted font-mono hover:text-body">Request details</summary>
            <div className="mt-1">
              {Object.keys(call.arguments).length === 0 ? <div className="text-muted text-xs font-mono">(no arguments)</div> : <JsonViewer data={call.arguments} maxExpandDepth={3} />}
            </div>
          </details>

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
            <div className="space-y-2">
              {artifact && (
                <div className="rounded border border-accent/30 bg-accent/5 p-2">
                  <div className="mb-1 flex items-center gap-1.5 text-xs text-accent"><FileCode2 size={13} /> Generated artifact</div>
                  <ArtifactActions content={artifact.content} language={artifact.language} filename={`${call.name}-output`} />
                </div>
              )}
              <details>
                <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-muted font-mono hover:text-body">{isRunning ? "Live execution details" : "Raw result"}</summary>
                <div className="mt-1"><JsonViewer data={call.result} maxExpandDepth={3} /></div>
              </details>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function describeCall(call: ToolCall): string {
  if (call.status === "running") {
    const status = (call.result as any)?.status;
    const progress = (call.result as any)?.progress;
    const last = Array.isArray(progress) ? progress.at(-1) : undefined;
    if (last?.summary || last?.message) return String(last.summary || last.message);
    return status === "queued" ? "Waiting for a worker" : "Executing in Munin";
  }
  if (call.status === "error") return call.error?.message || "The tool did not complete.";
  const result: any = call.result;
  if (result?.summary) return String(result.summary);
  if (result?.data?.summary) return String(result.data.summary);
  if (result?.ok === true) return "Completed successfully";
  if (typeof result === "string") return result.replace(/\s+/g, " ").slice(0, 180);
  if (Array.isArray(result)) return `${result.length} result${result.length === 1 ? "" : "s"} returned`;
  if (result && typeof result === "object") {
    const keys = Object.keys(result).filter((key) => !["data", "progress", "tool_calls"].includes(key));
    return keys.length ? `Completed: ${keys.slice(0, 3).join(", ")}` : "Completed successfully";
  }
  return "Completed successfully";
}

function findArtifact(value: unknown): { content: string; language: string } | null {
  if (typeof value === "string") {
    const match = value.match(/```([\w+-]+)?\n([\s\S]*?)```/);
    return match ? { language: match[1] || "text", content: match[2] } : null;
  }
  const candidate: any = value && typeof value === "object" ? value : null;
  const content = candidate?.artifact?.content || candidate?.content || candidate?.data?.artifact?.content;
  const language = candidate?.artifact?.language || candidate?.language || candidate?.data?.artifact?.language;
  return typeof content === "string" && content.length > 0 ? { content, language: String(language || "text") } : null;
}
