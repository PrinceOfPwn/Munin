// tags: [ui-component, error-boundary, part-render, client-component, PR-4B, part-render-error-boundary]
"use client";

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import { logError } from "@/lib/logError";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// PR-4B — per-part render isolation.
//
// A throwing part render must never take down the live console or its
// siblings. This minimal class boundary logs through the shared structured
// error contract (``logError({context: 'part_render', ...})``) and swaps the
// failed subtree for an inline alert badge. ``react-error-boundary`` is NOT
// installed in this worktree (and must not be added), so the class
// ``componentDidCatch`` contract is implemented directly.
//
// The boundary is keyed per part in ``AgentConsole.MessagePartList``, so a
// failure is confined to one part: adjacent parts and the parent console keep
// streaming. The typed registry boundary inside ``RendererFor`` (PR-2G)
// remains in place — its annotated fallback card handles registry renderer
// failures, this boundary handles anything thrown one level up.
// ---------------------------------------------------------------------------

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Conversation message id owning the failing part (for the error meta). */
  messageId?: string;
  /** Stable part key (see ``stablePartKey`` in AgentConsole) for the error meta. */
  partId?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    logError({
      context: "part_render",
      error,
      meta: {
        messageId: this.props.messageId,
        partId: this.props.partId,
        componentStack: errorInfo.componentStack,
      },
      ts: new Date().toISOString(),
    });
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          aria-label="Part render failed"
          className={cn(
            "rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger",
          )}
        >
          Part failed to render
          {this.props.partId ? (
            <code className="ml-1.5 break-all font-mono text-danger/70">
              {this.props.partId}
            </code>
          ) : null}
        </div>
      );
    }
    return this.props.children;
  }
}
