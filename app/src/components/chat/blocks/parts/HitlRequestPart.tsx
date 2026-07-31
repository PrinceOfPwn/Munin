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
  nonce?: string;
  choices?: string[];
  resolution?: "approved" | "rejected";
  onApprove: (requestId: string, choice: string, nonce: string) => void;
  onReject: (requestId: string, choice: string, nonce: string, reason: string) => void;
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
  nonce = "",
  choices = [],
  resolution,
  onApprove,
  onReject,
}: HitlRequestPartProps) {
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectInput, setShowRejectInput] = useState(false);

  const resolved = Boolean(resolution);
  const approveChoice = choices.find((c) => c === "approve") ?? choices[0] ?? "approve";
  const rejectChoice = choices.find((c) => c === "deny" || c === "reject") ?? "deny";

  function handleApprove() {
    if (resolved) return;
    onApprove(requestId, approveChoice, nonce);
  }

  function handleRejectSubmit() {
    if (resolved) return;
    onReject(requestId, rejectChoice, nonce, rejectReason);
    setShowRejectInput(false);
  }

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        resolved
          ? resolution === "approved"
            ? "border-success/40 bg-success/10"
            : "border-danger/40 bg-danger/10"
          : "border-warning/40 bg-warning/10"
      )}
      data-request-id={requestId}
      role="alertdialog"
      aria-label={`Human approval required: ${toolName}`}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="font-mono font-medium text-body">{toolName}</span>
        {resolved ? (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-xs font-medium",
              resolution === "approved"
                ? "bg-success/20 text-success"
                : "bg-danger/20 text-danger"
            )}
          >
            {resolution}
          </span>
        ) : (
          <span className="rounded bg-warning/20 px-1.5 py-0.5 text-xs font-medium text-warning">
            awaiting approval
          </span>
        )}
      </div>

      {/* Args */}
      {Object.keys(args).length > 0 && (
        <pre className="mt-2 max-h-40 overflow-auto rounded bg-raised p-2 text-xs font-mono text-secondary">
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
                "bg-success text-white hover:bg-success/80"
              )}
            >
              Approve
            </button>
            <button
              onClick={() => setShowRejectInput((v) => !v)}
              className={cn(
                "rounded px-3 py-1 text-xs font-semibold transition-colors",
                "bg-danger text-white hover:bg-danger/80"
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
                  "flex-1 rounded border border-borderStrong bg-surface px-2 py-1 text-xs text-body",
                  "focus:outline-none focus:ring-1 focus:ring-accent"
                )}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRejectSubmit();
                }}
              />
              <button
                onClick={handleRejectSubmit}
                className="rounded bg-danger px-3 py-1 text-xs font-semibold text-white hover:bg-danger/80"
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
