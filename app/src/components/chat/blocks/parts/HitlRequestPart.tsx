// tags: [ui-component, data-part, chat-stream-part, client-component, use-state, hitl-request-part]
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
  onApprove: (requestId: string, choice: string, nonce: string) => Promise<void>;
  onReject: (requestId: string, choice: string, nonce: string, reason: string) => Promise<void>;
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
  const [showRejectInput, setShowRejectInput] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [localResolution, setLocalResolution] = useState<"approved" | "rejected" | undefined>();

  const effectiveResolution = resolution ?? localResolution;
  const resolved = Boolean(effectiveResolution);
  const effectiveChoices = choices.length > 0 ? choices : ["approve", "deny"];

  async function handleChoiceClick(choice: string) {
    if (resolved || pending) return;
    const normalized = choice.trim().toLowerCase();
    const isApproval = normalized === "approve" || normalized === "allow" || normalized === "accept";
    if (isApproval) {
      setPending(true);
      try {
        await onApprove(requestId, choice, nonce);
        setLocalResolution("approved");
      } catch {
        // The caller presents the authenticated mutation error. Keep the
        // card actionable so a nonce/transport failure is never mistaken for
        // an approval.
      } finally {
        setPending(false);
      }
    } else {
      setShowRejectInput(choice);
    }
  }

  async function handleRejectSubmit(choice: string) {
    if (resolved || pending) return;
    setPending(true);
    try {
      await onReject(requestId, choice, nonce, rejectReason);
      setLocalResolution("rejected");
      setShowRejectInput(null);
      setRejectReason("");
    } catch {
      // See approval path: retain the form and let the parent surface the
      // server error without producing an unhandled event-handler promise.
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        resolved
          ? effectiveResolution === "approved"
            ? "border-success/40 bg-success/10"
            : "border-danger/40 bg-danger/10"
          : "border-warning/40 bg-warning/10"
      )}
      data-request-id={requestId}
      aria-busy={pending || undefined}
      aria-label={`Human approval required: ${toolName}`}
    >
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="font-mono font-medium text-body">{toolName}</span>
        {resolved ? (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-xs font-medium",
              effectiveResolution === "approved"
                ? "bg-success/20 text-success"
                : "bg-danger/20 text-danger"
            )}
          >
            {effectiveResolution}
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
            {effectiveChoices.map((choice) => {
              const normalized = choice.trim().toLowerCase();
              const isApproval = normalized === "approve" || normalized === "allow" || normalized === "accept";
              return (
                <button
                  key={choice}
                  type="button"
                  onClick={() => void handleChoiceClick(choice)}
                  disabled={pending}
                  className={cn(
                    "rounded px-3 py-1 text-xs font-semibold transition-colors capitalize disabled:cursor-wait disabled:opacity-60",
                    isApproval
                      ? "bg-success text-white hover:bg-success/80"
                      : "bg-danger text-white hover:bg-danger/80"
                  )}
                >
                  {choice}
                </button>
              );
            })}
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
                  if (e.key === "Enter") void handleRejectSubmit(showRejectInput);
                }}
              />
              <button
                type="button"
                disabled={pending}
                onClick={() => void handleRejectSubmit(showRejectInput)}
                className="rounded bg-danger px-3 py-1 text-xs font-semibold text-white hover:bg-danger/80 disabled:cursor-wait disabled:opacity-60"
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
