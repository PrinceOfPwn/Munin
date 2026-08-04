// tags: [ui-component, data-part, chat-stream-part, client-component, use-effect, use-state, heartbeat-part, react-memo, PR-4A]
"use client";

import { memo, useEffect, useState } from "react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HeartbeatPartProps {
  /** Unix timestamp in seconds (from the backend heartbeat event). */
  ts: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatRelative(ts: number): string {
  const diffMs = Date.now() - ts * 1000;
  const diffSec = Math.floor(diffMs / 1000);

  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  return `${Math.floor(diffMin / 60)}h ago`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Compact heartbeat indicator showing "last active: X seconds ago".
 * The relative time refreshes every second while the component is mounted.
 */
export const HeartbeatPart = memo(function HeartbeatPart({ ts }: HeartbeatPartProps) {
  const [label, setLabel] = useState(() => formatRelative(ts));

  useEffect(() => {
    const interval = setInterval(() => {
      setLabel(formatRelative(ts));
    }, 1000);
    return () => clearInterval(interval);
  }, [ts]);

  return (
    <div
      className="flex items-center gap-1.5 text-xs text-muted"
      aria-label={`Last active: ${label}`}
    >
      {/* Pulsing dot */}
      <span
        className={cn(
          "inline-block h-1.5 w-1.5 rounded-full bg-success",
          "animate-pulse"
        )}
        aria-hidden
      />
      <span>last active: {label}</span>
    </div>
  );
});
