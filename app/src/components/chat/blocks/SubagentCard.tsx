"use client";

import { useState } from "react";
import { Bot, ChevronRight, PictureInPicture2 } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Badge, stateBadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ReasoningEvent, SubagentInvocation, ToolInvocation } from "@/lib/production-api";
import { ThoughtBlock } from "./ThoughtBlock";
import { ToolBlock } from "./ToolBlock";
import {
  openFloatingWindow,
  isFloatingWindowOpen,
  useFloatingWindows,
} from "@/store/floatingWindows";

interface SubagentCardProps {
  subagent: SubagentInvocation;
  /** All run-level reasoning events; will be filtered to this subagent. */
  reasoning: ReasoningEvent[];
  /** All run-level tool calls; will be filtered to this subagent. */
  tools: ToolInvocation[];
  /** v3.1 — the parent run id so a forge window can address its guidance. */
  runId?: string;
}

const FORGE_ROLES = /forge|graph-engineer/i;

/**
 * Nested subagent invocation inside a Munin turn.
 *
 * Reads visually as a card indented one step from the parent's blocks, with
 * its OWN feed of thoughts + tool calls filtered by `agent_name`.  The
 * card is collapsible so a completed subagent doesn't dominate the
 * timeline; while running it stays open so operators watch it live.
 */
export function SubagentCard({ subagent, reasoning, tools, runId }: SubagentCardProps) {
  const running = subagent.state === "running" || subagent.state === "pending";
  const [open, setOpen] = useState<boolean>(running);
  // Subscribe so the button re-renders when a window is opened elsewhere.
  useFloatingWindows();

  const filterName = subagent.agent_name || subagent.profile_id;
  const localReasoning = reasoning.filter((r) => r.agent_name === filterName);
  const localTools = tools.filter((t) => t.agent_name === filterName);

  const isForge = FORGE_ROLES.test(subagent.profile_id);
  const windowId = `forge.${subagent.id}`;
  const alreadyOpen = isFloatingWindowOpen(windowId);

  function openLiveWindow(event: React.MouseEvent) {
    event.stopPropagation();
    if (!runId || alreadyOpen) return;
    openFloatingWindow({
      id: windowId,
      kind: "forge",
      runId,
      subagentId: subagent.id,
      subagentProfileId: subagent.profile_id,
      subagentRole: subagent.profile_id,
    });
  }

  return (
    <div className="rounded-lg border-l-2 border-accent bg-raised/40 pl-3 pr-2 py-2">
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className="flex w-full items-start gap-2">
          <CollapsibleTrigger className="flex flex-1 min-w-0 items-start gap-2 text-left focus-visible:outline-none">
            <Bot className={cn("h-4 w-4 mt-0.5 shrink-0", running ? "text-accent animate-feather" : "text-accent/70")} />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-xs font-medium text-body">{subagent.profile_id}</span>
                <Badge variant={stateBadgeVariant(subagent.state)}>{subagent.state.replaceAll("_", " ")}</Badge>
                {(localReasoning.length > 0 || localTools.length > 0) && (
                  <span className="text-[0.65rem] text-muted">
                    {localReasoning.length} thought{localReasoning.length === 1 ? "" : "s"} · {localTools.length} tool
                    {localTools.length === 1 ? "" : "s"}
                  </span>
                )}
              </div>
              {subagent.objective && (
                <p className="mt-1 text-xs text-secondary line-clamp-2">{subagent.objective}</p>
              )}
            </div>
            <ChevronRight
              className={cn("h-3.5 w-3.5 text-muted transition-transform mt-1", open && "rotate-90")}
              aria-hidden
            />
          </CollapsibleTrigger>
          {isForge && runId && (
            <Button
              variant="ghost"
              size="sm"
              onClick={openLiveWindow}
              disabled={alreadyOpen}
              className="mt-0 h-7 gap-1 px-2 text-[0.65rem]"
              aria-label={alreadyOpen ? "Live window already open" : "Open live window"}
            >
              <PictureInPicture2 className="h-3 w-3" />
              {alreadyOpen ? "In window" : "Open window"}
            </Button>
          )}
        </div>
        <CollapsibleContent className="mt-3 space-y-2 animate-fade-slide">
          {localReasoning.length === 0 && localTools.length === 0 && (
            <p className="text-[0.7rem] text-muted italic pl-4">Waiting for the subagent's first event…</p>
          )}
          {localReasoning.map((event) => (
            <ThoughtBlock
              key={event.id}
              content={event.content}
              kind={event.kind}
              step={event.step}
              running={running && !event.persisted}
            />
          ))}
          {localTools.map((tool) => (
            <ToolBlock key={tool.id} tool={tool} />
          ))}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
