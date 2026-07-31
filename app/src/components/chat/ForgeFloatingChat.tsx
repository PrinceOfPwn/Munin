"use client";

/**
 * Live trace + composer for a forge-style subagent, rendered in a floating
 * draggable window separate from the main conversation.
 *
 * Rationale — forge subagents (tool-forge, graph-forge, extension-forge) can
 * spend many minutes typechecking / sandbox-running proposed diffs.  While
 * they work, the operator wants to (a) watch the raw stdout tail without
 * losing sight of the main conversation, and (b) nudge the forge with
 * subagent-scoped guidance ("try the failing test first", "extend budget",
 * "abort and try approach B") without breaking the coordinator's context.
 *
 * The window content splits into three regions:
 *   1. Header — profile icon, current phase chip, elapsed timer.
 *   2. Body — filtered timeline (reasoning + tool events for THIS agent
 *      name) with a rolling terminal tail for `forge_*_output` events.
 *   3. Footer — mini composer that submits guidance addressed to this
 *      specific subagent (via `target_agent_id`).  If the current phase is a
 *      timing-out step, an "Extend budget +5min" affordance appears.
 */
import { useMemo, useRef, useState, useEffect } from "react";
import { Cog, Hammer, Network, Send, Sparkles, Terminal } from "lucide-react";
import { FloatingWindow } from "@/components/ui/floating-window";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThoughtBlock } from "@/components/chat/blocks/ThoughtBlock";
import { ToolBlock } from "@/components/chat/blocks/ToolBlock";
import { toast } from "@/components/ui/sonner";
import { cn, formatDuration, messageFromError } from "@/lib/utils";
import { productionApi, type RunDetail, type SubagentInvocation } from "@/lib/production-api";

interface Props {
  windowId: string;
  runId: string;
  subagent: SubagentInvocation;
  runDetail: RunDetail | undefined;
  onClose: () => void;
}

const ICON_BY_ROLE: Record<string, typeof Hammer> = {
  "tool-forge": Hammer,
  "graph-engineer": Network,
  "graph-forge": Network,
  "extension-forge": Cog,
};

interface ForgeStageMeta {
  stage?: string;
  message?: string;
  [key: string]: unknown;
}

function iconFor(profileId: string) {
  return ICON_BY_ROLE[profileId] ?? Cog;
}

function extractStage(content: string): ForgeStageMeta | null {
  // The store MAY carry forge stage metadata prefix-encoded in the content
  // when the base column is not present.  Reasoning events shipped through
  // the new metadata_json column already appear on the RunDetail payload as
  // a metadata field on the reasoning row — but the fallback prefix lives
  // here so both shapes render.
  try {
    if (content.startsWith("{")) {
      const nl = content.indexOf("\n");
      const jsonSlice = nl > 0 ? content.slice(0, nl) : content;
      const parsed = JSON.parse(jsonSlice);
      if (parsed && typeof parsed === "object" && "forge" in parsed) {
        return (parsed as { forge: ForgeStageMeta }).forge || null;
      }
    }
  } catch {
    /* not JSON */
  }
  return null;
}

export function ForgeFloatingChat({ windowId, runId, subagent, runDetail, onClose }: Props) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const Icon = iconFor(subagent.profile_id);
  const filterName = subagent.agent_name || subagent.profile_id;

  const { reasoning, tools, terminalTail, phase, startedAt } = useMemo(() => {
    const rs = (runDetail?.reasoning || []).filter((r) => r.agent_name === filterName);
    const ts = (runDetail?.tools || []).filter((t) => t.agent_name === filterName);
    const tail: Array<{ id: string; when: number; body: string }> = [];
    let lastStage: string | undefined;
    for (const evt of rs) {
      const meta = extractStage(evt.content);
      if (meta?.stage) {
        lastStage = meta.stage;
        if (
          meta.stage === "forge_typecheck_output" ||
          meta.stage === "forge_sandbox_output"
        ) {
          tail.push({
            id: evt.id,
            when: evt.created_at_ms,
            body: String(meta.message || ""),
          });
        }
      }
    }
    return {
      reasoning: rs,
      tools: ts,
      terminalTail: tail,
      phase: lastStage,
      startedAt: subagent.started_at_ms || rs[0]?.created_at_ms || Date.now(),
    };
  }, [runDetail, filterName, subagent]);

  // Live wall-clock elapsed so the header advances during quiet sandbox waits.
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(() =>
    Math.max(0, (Date.now() - startedAt) / 1000),
  );
  useEffect(() => {
    setElapsedSeconds(Math.max(0, (Date.now() - startedAt) / 1000));
    const handle = setInterval(() => {
      setElapsedSeconds(Math.max(0, (Date.now() - startedAt) / 1000));
    }, 1_000);
    return () => clearInterval(handle);
  }, [startedAt]);

  // Auto-scroll the terminal tail when new output lands.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [terminalTail.length]);

  async function sendGuidance(extra: {
    budget_extension_seconds?: number;
  } = {}) {
    const trimmed = body.trim();
    if (!trimmed && !extra.budget_extension_seconds) return;
    setBusy(true);
    try {
      await productionApi.guideRun(runId, trimmed || "extend budget", {
        target_agent_id: subagent.profile_id,
        budget_extension_seconds: extra.budget_extension_seconds,
      });
      setBody("");
      toast.success(`Delivered to ${subagent.profile_id}`);
    } catch (cause) {
      toast.error(messageFromError(cause));
    } finally {
      setBusy(false);
    }
  }

  const budgetExtensionAvailable = phase === "forge_budget_extension_available";

  return (
    <FloatingWindow
      id={windowId}
      title={`${subagent.profile_id}`}
      icon={<Icon className="h-3.5 w-3.5" />}
      onClose={onClose}
      defaultSize={{ width: 520, height: 460 }}
      headerRight={
        <div className="flex items-center gap-1.5">
          {phase && (
            <Badge variant={budgetExtensionAvailable ? "warning" : "neutral"}>
              {phase.replace(/^forge_/, "").replace(/_/g, " ")}
            </Badge>
          )}
          <span className="font-mono text-[0.65rem] text-muted">
            {formatDuration(elapsedSeconds)}
          </span>
        </div>
      }
    >
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-2 p-3">
          {reasoning.length === 0 && tools.length === 0 && (
            <p className="text-xs italic text-muted">
              Waiting for the subagent's first event…
            </p>
          )}
          {reasoning.map((event) => (
            <ThoughtBlock
              key={event.id}
              content={event.content}
              kind={event.kind}
              step={event.step}
            />
          ))}
          {tools.map((tool) => (
            <ToolBlock key={tool.id} tool={tool} />
          ))}
          {terminalTail.length > 0 && (
            <div className="rounded border border-border bg-bg/60">
              <div className="flex items-center gap-1 border-b border-border px-2 py-1 font-mono text-[0.65rem] uppercase tracking-wider text-muted">
                <Terminal className="h-3 w-3" /> sandbox output
              </div>
              <div
                ref={scrollRef}
                className="max-h-40 overflow-auto whitespace-pre-wrap p-2 font-mono text-[0.65rem] leading-relaxed text-body"
              >
                {terminalTail.map((line) => (
                  <div key={line.id}>{line.body}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>

      <footer className="border-t border-border p-2">
        {budgetExtensionAvailable && (
          <div className="mb-2 flex items-center justify-between rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[0.7rem]">
            <span className="text-body">
              <Sparkles className="mr-1 inline h-3 w-3" /> Initial budget exhausted
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => void sendGuidance({ budget_extension_seconds: 300 })}
            >
              Extend +5 min
            </Button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <Textarea
            rows={2}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void sendGuidance();
              }
            }}
            placeholder={`Ask ${subagent.profile_id} to adjust course…`}
            className={cn("flex-1 text-xs")}
          />
          <Button
            size="sm"
            onClick={() => void sendGuidance()}
            disabled={busy || !body.trim()}
          >
            <Send className="h-3.5 w-3.5" />
            Send
          </Button>
        </div>
      </footer>
    </FloatingWindow>
  );
}
