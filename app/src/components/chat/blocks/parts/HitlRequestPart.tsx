"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HitlRequestPartProps {
  requestId: string;
  toolName: string;
  args: Record<string, unknown>;
  resolution?: "approved" | "rejected";
  onApprove: (requestId: string) => void;
  onReject: (requestId: string, reason: string) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Human-in-the-loop approval request card.
 *
 * Displays the tool name, its arguments, and approve/reject buttons.
 * Buttons are disabled (and replaced by a resolution badge) once a resolution
 * has been recorded.
 */
export function HitlRequestPart({
  requestId,
  toolName,
  args,
  resolution,
  onApprove,
  onReject,
}: HitlRequestPartProps) {
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectInput, setShowRejectInput] = useState(false);

  const resolved = Boolean(resolution);

  function handleApprove() {
    if (resolved) return;
    onApprove(requestId);
  }

  function handleRejectSubmit() {
    if (resolved) return;
    onReject(requestId, rejectReason);
    setShowRejectInput(false);
  }

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        resolved
          ? resolution === "approved"
            ? "border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950"
            : "border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950"
          : "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950"
      )}
      data-request-id={requestId}
      role="alertdialog"
      aria-label={`Human approval required: ${toolName}`}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="font-mono font-medium">{toolName}</span>
        {resolved ? (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-xs font-medium",
              resolution === "approved"
                ? "bg-green-200 text-green-800 dark:bg-green-800 dark:text-green-200"
                : "bg-red-200 text-red-800 dark:bg-red-800 dark:text-red-200"
            )}
          >
            {resolution}
          </span>
        ) : (
          <span className="rounded bg-amber-200 px-1.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-800 dark:text-amber-200">
            awaiting approval
          </span>
        )}
      </div>

      {/* Args */}
      {Object.keys(args).length > 0 && (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-black/5 p-2 text-xs font-mono dark:bg-white/5">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}

      {/* Action buttons */}
      {!resolved && (
        <div className="mt-2 flex flex-col gap-2">
          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              className={cn(
                "rounded px-3 py-1 text-xs font-semibold transition-colors",
                "bg-green-600 text-white hover:bg-green-700 dark:bg-green-700 dark:hover:bg-green-600"
              )}
            >
              Approve
            </button>
            <button
              onClick={() => setShowRejectInput((v) => !v)}
              className={cn(
                "rounded px-3 py-1 text-xs font-semibold transition-colors",
                "bg-red-600 text-white hover:bg-red-700 dark:bg-red-700 dark:hover:bg-red-600"
              )}
            >
              Reject
            </button>
          </div>

          {showRejectInput && (
            <div className="flex gap-2">
              <input
                type="text"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Reason (optional)"
                className={cn(
                  "flex-1 rounded border border-input bg-background px-2 py-1 text-xs",
                  "focus:outline-none focus:ring-1 focus:ring-ring"
                )}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRejectSubmit();
                }}
              />
              <button
                onClick={handleRejectSubmit}
                className="rounded bg-red-600 px-3 py-1 text-xs font-semibold text-white hover:bg-red-700"
              >
                Confirm
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
