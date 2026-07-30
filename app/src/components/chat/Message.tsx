"use client";

import type { ReactNode } from "react";
import { Bot, User } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge, stateBadgeVariant } from "@/components/ui/badge";
import { GuidanceBlock } from "./blocks/GuidanceBlock";
import type { QueuedGuidance, TimelineMessage } from "@/lib/production-api";

export type MessageRole = "operator" | "munin" | "system";

/** Timeline entry backing an inline injected operator_guidance block. */
export type GuidanceMessage = TimelineMessage & {
  kind: "operator_guidance";
  guidance: QueuedGuidance;
};

/** Render an inline guidance block when the timeline surfaces one. */
export function GuidanceMessageBlock({ message }: { message: GuidanceMessage }) {
  return <GuidanceBlock guidance={message.guidance} />;
}

interface MessageProps {
  role: MessageRole;
  timestamp?: number;
  state?: string;
  runIdSuffix?: string;
  onSelectRun?: () => void;
  children: ReactNode;
  className?: string;
}

/**
 * Container for a single turn in the conversation.  Renders the role badge
 * + a stack of block children (ThoughtBlock, ToolBlock, SubagentCard, ...).
 *
 * The visual system:
 *   * Operator turns sit flush-left with a neutral badge and a subtle top
 *     border above the composer preview.
 *   * Munin turns get a violet accent-soft spine on the left plus a
 *     "MUNIN" badge; blocks nest inside a shared column so a thought
 *     followed by a tool call reads as one train of thought.
 */
export function Message({ role, state, runIdSuffix, onSelectRun, children, className }: MessageProps) {
  const isMunin = role === "munin";
  const Icon = isMunin ? Bot : User;

  return (
    <article
      className={cn(
        "group grid grid-cols-[36px_minmax(0,1fr)] gap-3 rounded-lg py-3 px-2 -mx-2",
        isMunin && "bg-accent-soft/40",
        className
      )}
    >
      <div className="flex justify-center pt-1">
        <div
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-full border",
            isMunin ? "border-accent/40 bg-accent/10 text-accent" : "border-border bg-raised text-secondary"
          )}
        >
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="min-w-0 flex flex-col gap-2">
        <header className="flex items-center gap-2 text-[0.7rem]">
          <span
            className={cn(
              "font-mono font-semibold tracking-wider uppercase",
              isMunin ? "text-accent" : "text-info"
            )}
          >
            {role === "operator" ? "OPERATOR" : role === "munin" ? "MUNIN" : "SYSTEM"}
          </span>
          {state && (
            <Badge variant={stateBadgeVariant(state)}>{state.replaceAll("_", " ")}</Badge>
          )}
          {runIdSuffix && (
            <button
              className="ml-auto font-mono text-[0.65rem] text-muted hover:text-info transition-colors"
              onClick={onSelectRun}
              type="button"
            >
              run {runIdSuffix}
            </button>
          )}
        </header>
        <div className="flex flex-col gap-2 min-w-0">{children}</div>
      </div>
    </article>
  );
}

/** Plain text body inside a Message.  Uses `whitespace-pre-wrap` so LLM
 *  markdown-lite formatting (indentation, blank lines) survives. */
export function MessageText({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-body whitespace-pre-wrap m-0">{children}</p>;
}
