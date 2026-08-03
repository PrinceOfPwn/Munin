// tags: [ui-component, console-surface, ai-sdk, vercel-ai, lucide-icons, client-component, use-conversations, use-ref, use-chat, use-memo, use-stream-insight, use-effect, use-elapsed-seconds, use-munin-chat, use-state, status-badge, agent-console, console-header, part-renderer, message-part-list, live-console, message-bubble, s-t-i-c-k--t-h-r-e-s-h-o-l-d, no-conversation-state, icon, detach-cancel-ui, cancel-run, PR-2C, cancel-fence-ui]
"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { FormEvent } from "react";
import {
  Archive,
  Bot,
  CheckCircle,
  Download,
  LoaderCircle,
  MessageSquare,
  Pencil,
  Send,
  Sparkles,
  TerminalSquare,
  Unplug,
  WifiOff,
  XCircle,
  Zap,
} from "lucide-react";
import type {
  ChatStatus,
  DataUIPart,
  DynamicToolUIPart,
  ReasoningUIPart,
  StepStartUIPart,
  TextUIPart,
  UIMessage,
} from "ai";

import { Badge, stateBadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "@/components/ui/sonner";
import { Markdown } from "@/components/Markdown";

import { OperationalTracePart } from "@/components/chat/blocks/parts/OperationalTracePart";
import { ReasoningPart } from "@/components/chat/blocks/parts/ReasoningPart";
import { ToolInvocationPart } from "@/components/chat/blocks/parts/ToolInvocationPart";
import type { ToolInvocationState } from "@/components/chat/blocks/parts/ToolInvocationPart";
import { SubagentPresencePart } from "@/components/chat/blocks/parts/SubagentPresencePart";
import { HitlRequestPart } from "@/components/chat/blocks/parts/HitlRequestPart";
import type { HitlRequestPartProps } from "@/components/chat/blocks/parts/HitlRequestPart";
import { ArtifactPart } from "@/components/chat/blocks/parts/ArtifactPart";
import { NotePart } from "@/components/chat/blocks/parts/NotePart";
import { GuidancePart } from "@/components/chat/blocks/parts/GuidancePart";
import {
  HypothesisPart,
  PlanSnapshotPart,
  TodoMutationPart,
} from "@/components/chat/blocks/parts/PlanPart";
import type {
  PlanItemPartProps,
  PlanSnapshotPartProps,
} from "@/components/chat/blocks/parts/PlanPart";
import { GoalPart } from "@/components/chat/blocks/parts/GoalPart";
import type { GoalPartProps } from "@/components/chat/blocks/parts/GoalPart";
import { TimerTickPart } from "@/components/chat/blocks/parts/TimerTickPart";
import { CommandOutputPart } from "@/components/chat/blocks/parts/CommandOutputPart";
import { ToolHeartbeatPart } from "@/components/chat/blocks/parts/ToolHeartbeatPart";
import { HeartbeatPart } from "@/components/chat/blocks/parts/HeartbeatPart";
import { ProviderSwitcher } from "@/components/ProviderSwitcher";
import { ModeSwitcher, EMPTY_GOAL_DRAFT } from "@/components/ModeSwitcher";
import type { GoalDraft, OperationMode } from "@/components/ModeSwitcher";

import {
  approveHitlRequest,
  rejectHitlRequest,
  sendOperatorGuidance,
  useMuninChat,
} from "@/lib/aiChat";
import { useConversations } from "@/lib/queries";
import { cancelRun, productionApi, type ProviderProfile } from "@/lib/production-api";
import { logError } from "@/lib/logError";
import { cn, formatDuration, isTerminalRun } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Custom data part shapes emitted by the Munin BFF translator
// ---------------------------------------------------------------------------

interface SubagentData {
  subagentId: string;
  name: string;
  state: string;
}

interface HitlData {
  requestId: string;
  toolName: string;
  args: Record<string, unknown>;
  nonce?: string;
  choices?: string[];
  resolved: boolean;
  resolution?: "approved" | "rejected";
}

interface ArtifactData {
  artifactId: string;
  mimeType: string;
  uri: string;
}

interface NoteData {
  text: string;
}

interface GuidanceData {
  text: string;
}

interface RunStateData {
  state: string;
  runId?: string;
}

interface ActivityData {
  stage: string;
  text: string;
}

// Fase 3 (autonomous modes): plan / goal / timer data part shapes.
interface PlanData {
  goal: PlanSnapshotPartProps["goal"];
  items: PlanItemPartProps[];
  updatedAtMs?: number;
}

interface TodoData {
  op: string;
  item?: PlanItemPartProps;
  reason?: string;
  resetIds?: string[];
}

interface HypothesisData {
  statement: string;
  status: string;
  evidence?: string;
}

interface GoalData {
  goal: GoalPartProps["goal"];
  state?: string;
}

interface TimerTickData {
  timerId: string;
  timerKind?: string;
  goalId?: string;
  tickCount?: number;
  dueAtMs?: number;
  lastTickAtMs?: number;
}

interface CommandOutputData {
  jobId?: string;
  toolCallId?: string;
  toolName: string;
  stream: "stdout" | "stderr" | "meta";
  text: string;
  elapsedMs?: number;
  final?: boolean;
}

interface ToolHeartbeatData {
  jobId?: string;
  toolCallId?: string;
  toolName: string;
  elapsedMs?: number;
  lastOutputMs?: number;
  text?: string;
}

// UIMessage part union helpers — the `parts` array on a UIMessage can contain
// any of these at runtime; we distinguish them by their `type` string.
type AnyUIPart =
  | TextUIPart
  | ReasoningUIPart
  | DynamicToolUIPart
  | StepStartUIPart
  | DataUIPart<Record<string, unknown>>;

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface AgentConsoleProps {
  /** The active Munin conversation id. Null renders an empty state. */
  conversationId: string | null;
}

// ---------------------------------------------------------------------------
// Stream status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: ChatStatus }) {
  const items: Record<
    ChatStatus,
    { label: string; icon: typeof Zap; colour: string }
  > = {
    submitted: {
      label: "Sending…",
      icon: LoaderCircle,
      colour: "text-warning",
    },
    streaming: {
      label: "Streaming",
      icon: Zap,
      colour: "text-success",
    },
    ready: {
      label: "Ready",
      icon: CheckCircle,
      colour: "text-muted",
    },
    error: {
      label: "Error",
      icon: WifiOff,
      colour: "text-danger",
    },
  };
  const item = items[status] ?? items.ready;
  const Icon = item.icon;
  return (
    <span
      className={cn(
        "flex items-center gap-1 font-mono text-[0.65rem] uppercase tracking-widest",
        item.colour,
      )}
    >
      <Icon
        className={cn("h-2.5 w-2.5", status === "submitted" && "animate-spin")}
      />
      {item.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Message bubble shell
// ---------------------------------------------------------------------------

function MessageBubble({
  role,
  children,
}: {
  role: "user" | "assistant" | string;
  children: React.ReactNode;
}) {
  const isUser = role === "user";
  return (
    <div
      className={cn(
        "flex w-full gap-3",
        isUser ? "flex-row-reverse" : "flex-row",
      )}
    >
      {/* Avatar */}
      <div
        className={cn(
          "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-accent/20 text-accent"
            : "border border-border bg-surface text-secondary",
        )}
      >
        {isUser ? (
          <span className="font-mono text-[0.6rem] font-bold">OP</span>
        ) : (
          <Bot className="h-3.5 w-3.5" />
        )}
      </div>

      {/* Content */}
      <div
        className={cn(
          "flex min-w-0 max-w-[80%] flex-col gap-1.5",
          isUser ? "items-end" : "items-start",
        )}
      >
        {children}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-part renderer
// ---------------------------------------------------------------------------

function PartRenderer({
  part,
  messageId,
  idx,
  role,
  hitlApprove,
  hitlReject,
}: {
  part: AnyUIPart;
  messageId: string;
  idx: number;
  role: string;
  hitlApprove: HitlRequestPartProps["onApprove"];
  hitlReject: HitlRequestPartProps["onReject"];
}) {
  const key = `${messageId}-part-${idx}`;

  // Text part
  if (part.type === "text") {
    const tp = part as TextUIPart;
    if (!tp.text) return null;
    return (
      <div
        key={key}
        className={cn(
          "rounded-lg px-3 py-2 text-sm leading-relaxed",
          role === "user"
            ? "bg-accent/15 text-body"
            : "border border-border bg-surface text-body",
        )}
      >
        {role === "user" ? (
          <p className="whitespace-pre-wrap">{tp.text}</p>
        ) : (
          <Markdown text={tp.text} />
        )}
      </div>
    );
  }

  // Dynamic tool part — emitted when translator writes tool-input-start /
  // tool-input-available / tool-output-available / tool-output-error chunks.
  if (part.type === "dynamic-tool") {
    const dp = part as DynamicToolUIPart;
    let invState: ToolInvocationState = "call";
    let result: unknown = undefined;
    let error: string | undefined = undefined;
    if (dp.state === "input-streaming" || dp.state === "input-available") {
      invState = dp.state === "input-streaming" ? "partial-call" : "call";
    } else if (dp.state === "output-available") {
      invState = "result";
      result = dp.output;
    } else if (dp.state === "output-error") {
      invState = "result";
      error = dp.errorText;
    }
    return (
      <ToolInvocationPart
        key={key}
        toolCallId={dp.toolCallId}
        toolName={dp.toolName}
        args={dp.state !== "input-streaming" ? (dp.input as Record<string, unknown> | undefined) : undefined}
        state={invState}
        result={result}
        error={error}
      />
    );
  }

  // step-start is a step boundary marker — not rendered.
  if (part.type === "step-start") {
    return null;
  }

  // Custom data parts — keyed by the `type` prefix used in the translator.
  if (part.type === "data-subagent") {
    const dp = part as DataUIPart<{ subagent: SubagentData }>;
    const d = dp.data as unknown as SubagentData;
    return (
      <SubagentPresencePart
        key={key}
        subagentId={d.subagentId}
        name={d.name}
        state={d.state}
      />
    );
  }

  if (part.type === "data-hitl-request") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as HitlData;
    return (
      <HitlRequestPart
        key={key}
        requestId={d.requestId}
        toolName={d.toolName}
        args={d.args ?? {}}
        nonce={d.nonce}
        choices={d.choices}
        resolution={d.resolution}
        onApprove={hitlApprove}
        onReject={hitlReject}
      />
    );
  }

  if (part.type === "data-artifact") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as ArtifactData;
    return (
      <ArtifactPart
        key={key}
        artifactId={d.artifactId}
        mimeType={d.mimeType}
        uri={d.uri}
      />
    );
  }

  if (part.type === "data-heartbeat") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as { ts?: number };
    return d.ts ? <HeartbeatPart key={key} ts={d.ts} /> : null;
  }

  if (part.type === "data-command-output") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as CommandOutputData;
    return (
      <CommandOutputPart
        key={key}
        toolName={d.toolName ?? "command"}
        stream={d.stream ?? "stdout"}
        text={d.text ?? ""}
        elapsedMs={d.elapsedMs}
        final={d.final}
      />
    );
  }

  if (part.type === "data-tool-heartbeat") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as ToolHeartbeatData;
    return (
      <ToolHeartbeatPart
        key={key}
        toolName={d.toolName ?? "command"}
        elapsedMs={d.elapsedMs}
        lastOutputMs={d.lastOutputMs}
        text={d.text}
      />
    );
  }

  if (part.type === "reasoning") {
    const reasoning = part as ReasoningUIPart;
    return reasoning.text ? <ReasoningPart key={key} id={key} text={reasoning.text} /> : null;
  }

  if (part.type === "data-activity") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as ActivityData;
    return <OperationalTracePart key={key} stage={d.stage ?? "working"} text={d.text ?? "Working"} />;
  }

  if (part.type === "data-note") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as NoteData;
    return <NotePart key={key} text={d.text} />;
  }

  if (part.type === "data-guidance") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as GuidanceData;
    return <GuidancePart key={key} text={d.text} />;
  }

  // Fase 3 (autonomous modes): durable plan / goal / timer visibility.
  if (part.type === "data-plan") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as PlanData;
    return (
      <PlanSnapshotPart
        key={key}
        goal={d.goal ?? null}
        items={d.items ?? []}
        updatedAtMs={d.updatedAtMs}
      />
    );
  }

  if (part.type === "data-todo") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as TodoData;
    return (
      <TodoMutationPart
        key={key}
        op={d.op ?? "update"}
        item={d.item}
        reason={d.reason}
        resetIds={d.resetIds}
      />
    );
  }

  if (part.type === "data-hypothesis") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as HypothesisData;
    return (
      <HypothesisPart
        key={key}
        statement={d.statement}
        status={d.status ?? "proposed"}
        evidence={d.evidence}
      />
    );
  }

  if (part.type === "data-goal") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as GoalData;
    return <GoalPart key={key} goal={d.goal ?? null} state={d.state} />;
  }

  if (part.type === "data-timer-tick") {
    const d = (part as DataUIPart<Record<string, unknown>>).data as unknown as TimerTickData;
    return (
      <TimerTickPart
        key={key}
        timerId={d.timerId}
        timerKind={d.timerKind}
        goalId={d.goalId}
        tickCount={d.tickCount}
        dueAtMs={d.dueAtMs}
        lastTickAtMs={d.lastTickAtMs}
      />
    );
  }

  // data-run-state is metadata — skip rendering.
  if (part.type === "data-run-state") {
    return null;
  }

  // data-operator-guidance is the outgoing marker for guidance UI parts —
  // the BFF intercepts it and forwards to /api/chat/{run}/guidance so no
  // corresponding inline widget is needed.
  if (part.type === "data-operator-guidance") {
    return null;
  }

  // Unknown part — ignore silently.
  return null;
}

// ---------------------------------------------------------------------------
// Message part list
// ---------------------------------------------------------------------------

function MessagePartList({
  message,
  onHitlResolved,
}: {
  message: UIMessage;
  onHitlResolved: () => Promise<void>;
}) {
  const hitlApprove: HitlRequestPartProps["onApprove"] = async (id, choice, nonce) => {
    try {
      await approveHitlRequest(id, choice, nonce);
      void onHitlResolved().catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "Decision recorded; could not reconnect to the run");
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not record approval");
      throw error;
    }
  };
  const hitlReject: HitlRequestPartProps["onReject"] = async (id, choice, nonce, reason) => {
    try {
      await rejectHitlRequest(id, choice, nonce, reason);
      void onHitlResolved().catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : "Decision recorded; could not refresh the run");
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not record rejection");
      throw error;
    }
  };

  return (
    <>
      {(message.parts as AnyUIPart[]).map((part, idx) => (
        <PartRenderer
          key={`${message.id}-part-${idx}`}
          part={part}
          messageId={message.id}
          idx={idx}
          role={message.role}
          hitlApprove={hitlApprove}
          hitlReject={hitlReject}
        />
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Extract active run metadata + last tool call from the message stream
// ---------------------------------------------------------------------------

interface StreamInsight {
  activeRunId: string | null;
  lastToolName: string | null;
  runState: string | null;
  goalId: string | null;
}

function useStreamInsight(messages: UIMessage[]): StreamInsight {
  return useMemo(() => {
    let activeRunId: string | null = null;
    let lastToolName: string | null = null;
    let runState: string | null = null;
    let goalId: string | null = null;
    for (const message of messages) {
      if (message.role !== "assistant") continue;
      for (const rawPart of message.parts as AnyUIPart[]) {
        if (rawPart.type === "data-run-state") {
          const d = (rawPart as DataUIPart<Record<string, unknown>>)
            .data as unknown as RunStateData;
          if (d?.runId) activeRunId = d.runId;
          if (d?.state) runState = d.state;
        } else if (rawPart.type === "dynamic-tool") {
          const dp = rawPart as DynamicToolUIPart;
          if (dp.toolName) lastToolName = dp.toolName;
        } else if (rawPart.type === "data-goal") {
          const d = (rawPart as DataUIPart<Record<string, unknown>>)
            .data as unknown as GoalData;
          if (d?.goal?.id) goalId = d.goal.id;
        }
      }
    }
    return { activeRunId, lastToolName, runState, goalId };
  }, [messages]);
}

// ---------------------------------------------------------------------------
// Elapsed timer (client-only, ticks every second while streaming)
// ---------------------------------------------------------------------------

function useElapsedSeconds(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active) {
      startedAtRef.current = null;
      setElapsed(0);
      return;
    }
    startedAtRef.current = Date.now();
    setElapsed(0);
    const handle = window.setInterval(() => {
      if (startedAtRef.current != null) {
        setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000));
      }
    }, 1_000);
    return () => window.clearInterval(handle);
  }, [active]);

  return elapsed;
}

// ---------------------------------------------------------------------------
// Cancel durable run button (distinct from Detach)
// ---------------------------------------------------------------------------
//
// PR-2C contract:
//   * Shown whenever a run is active (queued/running/waiting_for_human/
//     cancelling).  A terminal run hides it.
//   * ``idle`` → "Cancel", enabled.  Posts ``/api/chat/{run_id}/cancel``;
//     on 202 ACK the parent flips ``cancelState`` to ``canceling``.
//   * ``canceling`` → "Canceling…" with a spinner, disabled.  This is the
//     truthful "we asked the server to cancel; the executor is observing
//     the fence" state.  We do NOT pretend the run is cancelled until the
//     durable SSE emits ``state: 'cancelled'``.
//   * ``canceled`` → "Canceled" with a check, disabled.  Reached only when
//     the SSE ``run.cancelled`` event arrives.
//   * ``error`` → "Cancel" re-enabled so the operator can retry after a
//     4xx/5xx (logged via ``logError`` upstream).
function CancelButton({
  state,
  runActive,
  onClick,
}: {
  state: "idle" | "requested" | "canceling" | "canceled" | "error";
  runActive: boolean;
  onClick: () => void;
}) {
  if (!runActive && state === "idle") return null;

  const canceling = state === "canceling" || state === "requested";
  const canceled = state === "canceled";

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={onClick}
      disabled={canceling || canceled}
      title={
        canceled
          ? "Run cancelled"
          : canceling
            ? "Canceling the durable run; waiting for the executor to observe the fence"
            : "Cancel the durable run (the operator reader detaches, the server stops the run)"
      }
      className={cn(
        "shrink-0",
        canceled ? "border-success/40 text-success" : "border-danger/50 text-danger hover:bg-danger/10",
      )}
    >
      {canceling ? (
        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
      ) : canceled ? (
        <CheckCircle className="h-3.5 w-3.5" />
      ) : (
        <XCircle className="h-3.5 w-3.5" />
      )}
      {canceling ? "Canceling…" : canceled ? "Canceled" : "Cancel"}
    </Button>
  );
}

// ---------------------------------------------------------------------------
// Console header (title + status + archive + inline run insight)
// ---------------------------------------------------------------------------

function ConsoleHeader({
  conversationId,
  title,
  conversationStatus,
  status,
  runState,
  elapsedSeconds,
  lastToolName,
  isStreaming,
  onStop,
  onCancel,
  cancelState,
  providerProfiles,
  providerBusy,
  onActivateProvider,
  onCreateProvider,
  onArchive,
  onRename,
  onExport,
}: {
  conversationId: string;
  title: string;
  conversationStatus: string;
  status: ChatStatus;
  runState: string | null;
  elapsedSeconds: number;
  lastToolName: string | null;
  isStreaming: boolean;
  onStop: () => void;
  onCancel: () => void;
  /** idle | requested | canceling | canceled | error */
  cancelState: "idle" | "requested" | "canceling" | "canceled" | "error";
  providerProfiles: ProviderProfile[];
  providerBusy: boolean;
  onActivateProvider: (profileId: string) => Promise<void>;
  onCreateProvider: (draft: {
    label: string;
    provider: string;
    base_url: string;
    model: string;
    api_key: string;
  }) => Promise<void>;
  onArchive: () => Promise<void>;
  onRename: (title: string) => Promise<void>;
  onExport: () => Promise<void>;
}) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const savingTitle = useRef(false);

  useEffect(() => {
    if (!editingTitle) setDraftTitle(title);
  }, [editingTitle, title]);

  async function saveTitle() {
    if (savingTitle.current) return;
    const nextTitle = draftTitle.trim();
    if (!nextTitle || nextTitle === title) {
      setEditingTitle(false);
      return;
    }
    savingTitle.current = true;
    try {
      await onRename(nextTitle);
    } catch {
      // The parent already reports the API failure to the operator.
    } finally {
      savingTitle.current = false;
      setEditingTitle(false);
    }
  }

  return (
    <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-4">
      <div className="min-w-0 space-y-1">
        <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">
          AGENT CONSOLE / LIVE
        </p>
        <div className="flex min-w-0 items-center gap-2">
          <TerminalSquare className="h-5 w-5 shrink-0 text-accent" />
          {editingTitle ? (
            <input
              autoFocus
              value={draftTitle}
              onChange={(event) => setDraftTitle(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void saveTitle();
                if (event.key === "Escape") setEditingTitle(false);
              }}
              onBlur={() => void saveTitle()}
              className="min-w-0 rounded border border-accent/50 bg-bg px-2 py-1 text-xl font-medium tracking-tight outline-none"
              aria-label="Conversation title"
            />
          ) : (
            <button
              type="button"
              className="group flex min-w-0 items-center gap-1 text-left"
              onClick={() => setEditingTitle(true)}
              title="Rename conversation"
            >
              <h1 className="min-w-0 truncate text-xl font-medium tracking-tight">{title}</h1>
              <Pencil className="h-3.5 w-3.5 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
            </button>
          )}
          <Badge variant={stateBadgeVariant(conversationStatus)}>
            {conversationStatus}
          </Badge>
        </div>
        <span className="block font-mono text-[0.65rem] text-muted">
          {conversationId}
        </span>
        {(isStreaming || runState) && (
          <div className="flex items-center gap-3 pt-1 font-mono text-[0.65rem] uppercase tracking-widest text-muted">
            {runState && (
              <span>
                run · <span className="text-body">{runState}</span>
              </span>
            )}
            {isStreaming && (
              <span>
                elapsed ·{" "}
                <span className="text-body">
                  {formatDuration(elapsedSeconds)}
                </span>
              </span>
            )}
            {lastToolName && (
              <span className="truncate">
                tool · <span className="text-body">{lastToolName}</span>
              </span>
            )}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <StatusBadge status={status} />
        {isStreaming && (
          <Button
            variant="outline"
            size="sm"
            onClick={onStop}
            title="Detach the local reader; the durable run keeps running in the background"
          >
            <Unplug className="h-3.5 w-3.5" /> Detach
          </Button>
        )}
        <CancelButton
          state={cancelState}
          runActive={isStreaming && runState !== null && !isTerminalRun(runState ?? "")}
          onClick={onCancel}
        />
        <ProviderSwitcher
          profiles={providerProfiles}
          busy={providerBusy}
          onActivate={onActivateProvider}
          onCreate={onCreateProvider}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => void onArchive()}
          disabled={conversationStatus === "archived"}
        >
          <Archive className="h-3.5 w-3.5" /> Archive
        </Button>
        <Button variant="outline" size="sm" onClick={() => void onExport()} title="Export conversation">
          <Download className="h-3.5 w-3.5" /> Export
        </Button>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Live console (requires a conversationId)
// ---------------------------------------------------------------------------

function LiveConsole({ conversationId }: { conversationId: string }) {
  const { messages, sendMessage, resumeStream, stop, status, error } = useMuninChat({
    conversationId,
  });

  // Conversation metadata is read from the cached list (staleTime 30s, no
  // polling). Fetching a single conversation would require the legacy
  // detail endpoint which is scheduled for deletion in Fase 2.
  const conversationsQuery = useConversations("");
  const conversationMeta = useMemo(
    () =>
      conversationsQuery.data?.find((item) => item.id === conversationId) ??
      null,
    [conversationsQuery.data, conversationId],
  );

  const draftKey = `munin.draft.${conversationId}`;
  const [input, setInput] = useState("");
  const [draftReady, setDraftReady] = useState(false);
  const [providerProfiles, setProviderProfiles] = useState<ProviderProfile[]>([]);
  const [providerBusy, setProviderBusy] = useState(false);
  const [mode, setMode] = useState<OperationMode>("standard");
  const [goalDraft, setGoalDraft] = useState<GoalDraft>(EMPTY_GOAL_DRAFT);
  const [attachedGoalId, setAttachedGoalId] = useState<string | null>(null);

  // Persist the chosen mode per conversation so a refresh keeps the
  // operation contract instead of silently falling back to Standard.
  const modeKey = `munin.mode.${conversationId}`;
  useEffect(() => {
    const saved = window.localStorage.getItem(modeKey) as OperationMode | null;
    if (saved === "standard" || saved === "yolo" || saved === "goal" || saved === "beast") {
      setMode(saved);
    }
  }, [modeKey]);
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(modeKey, mode);
  }, [modeKey, mode]);

  useEffect(() => {
    let cancelled = false;
    void productionApi.providerProfiles().then((profiles) => {
      if (!cancelled) setProviderProfiles(profiles);
    }).catch(() => {
      // Provider configuration is optional; the process environment remains
      // the fallback when the profile endpoint is unavailable.
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function activateProvider(profileId: string) {
    setProviderBusy(true);
    try {
      await productionApi.activateProviderProfile(profileId);
      setProviderProfiles(await productionApi.providerProfiles());
      toast.success("AI provider switched for the next turn");
    } finally {
      setProviderBusy(false);
    }
  }

  async function createProvider(draft: {
    label: string;
    provider: string;
    base_url: string;
    model: string;
    api_key: string;
  }) {
    setProviderBusy(true);
    try {
      const profile = await productionApi.createProviderProfile({ ...draft, activate: false });
      await productionApi.activateProviderProfile(profile.id);
      setProviderProfiles(await productionApi.providerProfiles());
      toast.success("Provider saved and selected for the next turn");
    } finally {
      setProviderBusy(false);
    }
  }

  // Load persisted draft when the conversation changes.
  useEffect(() => {
    if (typeof window === "undefined") {
      setDraftReady(true);
      return;
    }
    const value = window.localStorage.getItem(draftKey) || "";
    setInput(value);
    setDraftReady(true);
    return () => setDraftReady(false);
  }, [draftKey]);

  // Autosave the draft (debounced 200ms) once the initial load is in.
  useEffect(() => {
    if (!draftReady || typeof window === "undefined") return;
    const handle = window.setTimeout(() => {
      if (input) window.localStorage.setItem(draftKey, input);
      else window.localStorage.removeItem(draftKey);
    }, 200);
    return () => window.clearTimeout(handle);
  }, [draftKey, draftReady, input]);

  // Auto-scroll: only follow the stream while the operator is reading near
  // the bottom (within STICK_THRESHOLD px). If they scrolled up to inspect
  // earlier content, an in-progress stream must not drag the view down.
  // `pendingJump` forces a jump to the bottom right after sending a turn so
  // the new response is visible even if the operator was scrolled up.
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const pendingJumpRef = useRef(false);
  const STICK_THRESHOLD = 120;

  function handleViewportScroll() {
    const el = viewportRef.current;
    if (!el) return;
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_THRESHOLD;
  }

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    if (pendingJumpRef.current) {
      pendingJumpRef.current = false;
      stickToBottomRef.current = true;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    } else if (stickToBottomRef.current) {
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    }
  }, [messages]);

  const isStreaming = status === "streaming" || status === "submitted";
  const insight = useStreamInsight(messages);
  const runIsActive = Boolean(
    insight.activeRunId && !isTerminalRun(insight.runState ?? ""),
  );
  const elapsedSeconds = useElapsedSeconds(isStreaming);

  // PR-2C durable cancel UI state.  The SSE ``data-run-state`` part is the
  // source of truth for the terminal transition; we only render "Canceled"
  // once the server has actually finalised the run (not on the 202 ACK).
  const [cancelState, setCancelState] = useState<
    "idle" | "requested" | "canceling" | "canceled" | "error"
  >("idle");

  // Reset cancel UI when the active run id changes (new turn → fresh button).
  useEffect(() => {
    setCancelState("idle");
  }, [insight.activeRunId]);

  // Flip to ``canceled`` ONLY when the durable SSE emits state: cancelled.
  // ``cancelling`` (the fence marker ACK) is shown through ``canceling``.
  useEffect(() => {
    if (insight.runState === "cancelled") {
      setCancelState("canceled");
    } else if (insight.runState === "cancelling" && cancelState === "idle") {
      // A reconnect arrived mid-cancel (server replayed run.cancelling).
      setCancelState("canceling");
    }
    // Reset the button back to idle if the run terminates a different way
    // (completed/failed/interrupted) — we never showed "Canceled" anyway.
    if (
      insight.runState &&
      insight.runState !== "cancelled" &&
      insight.runState !== "cancelling" &&
      isTerminalRun(insight.runState) &&
      cancelState !== "idle" &&
      cancelState !== "canceled"
    ) {
      setCancelState("idle");
    }
  }, [insight.runState, cancelState]);

  async function handleCancelRun() {
    const runId = insight.activeRunId;
    if (!runId) return;
    setCancelState("requested");
    try {
      const result = await cancelRun(runId);
      if (result.status === "cancelling") {
        // 202 ACK — the executor is observing the fence; switch to the
        // truthful ``canceling`` state until the SSE ``cancelled`` lands.
        setCancelState("canceling");
        toast.success("Cancel requested; the durable run will stop at the next step");
      } else {
        // 200 — the run was already terminal. The SSE already rendered the
        // terminal state; keep the button truthful.
        setCancelState(result.status === "cancelled" ? "canceled" : "idle");
      }
    } catch (error) {
      logError({
        context: "cancel",
        error,
        meta: { runId },
      });
      setCancelState("error");
      toast.error(error instanceof Error ? error.message : "Could not cancel the durable run");
    }
  }

  // Once the stream reveals the durable goal id, keep it so later turns
  // attach to the same goal instead of creating a duplicate.
  useEffect(() => {
    if (insight.goalId && insight.goalId !== attachedGoalId) {
      setAttachedGoalId(insight.goalId);
    }
  }, [insight.goalId, attachedGoalId]);

  function buildGoalPayload(): Record<string, unknown> | undefined {
    const needsGoal = mode === "goal" || mode === "beast";
    if (!needsGoal) return undefined;
    const objective = goalDraft.objective.trim();
    const payload: Record<string, unknown> = {};
    if (attachedGoalId) payload.id = attachedGoalId;
    if (objective) payload.objective = objective;
    const criteria = goalDraft.successCriteria
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (criteria.length > 0) payload.success_criteria = criteria;
    const scopeText = goalDraft.scopeJson.trim();
    if (scopeText) {
      try {
        payload.scope = JSON.parse(scopeText) as Record<string, unknown>;
      } catch {
        // Invalid scope JSON is rejected server-side for BEAST; skip silently
        // here so the turn still goes through Standard semantics if the
        // operator toggled the mode off.
      }
    }
    return Object.keys(payload).length > 0 ? payload : undefined;
  }

  async function submitTurn(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text) return;
    if ((mode === "goal" || mode === "beast") && !goalDraft.objective.trim() && !attachedGoalId) {
      toast.error(`${mode.toUpperCase()} mode requires a goal — open the Goal panel and set an objective`);
      return;
    }
    pendingJumpRef.current = true;
    setInput("");
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(draftKey);
    }
    const body: Record<string, unknown> = { mode };
    const goal = buildGoalPayload();
    if (goal) body.goal = goal;
    if (runIsActive) {
      await sendGuidance(text);
      return;
    }
    await sendMessage({ text }, { body });
  }

  async function sendGuidance(guidanceText = input.trim()) {
    const text = guidanceText.trim();
    if (!text) return;
    if (!insight.activeRunId) {
      toast.error("No active run to guide");
      return;
    }
    pendingJumpRef.current = true;
    setInput("");
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(draftKey);
    }
    try {
      await sendOperatorGuidance(insight.activeRunId, text);
      toast.success("Guidance queued for the active run");
      // Stop detaches the browser reader; it must not strand the durable
      // executor. Reattach immediately when there is no live reader left.
      if (!isStreaming) {
        await resumeStream();
      }
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Guidance failed");
    }
  }

  async function resumeAfterHitl() {
    await resumeStream();
  }

  async function archive() {
    if (!conversationMeta) return;
    try {
      await productionApi.archiveConversation(conversationId, conversationMeta.version, true);
      await conversationsQuery.refetch();
      toast.success("Conversation archived");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Could not archive conversation");
    }
  }

  async function renameConversation(title: string) {
    if (!conversationMeta) return;
    try {
      await productionApi.renameConversation(conversationId, conversationMeta.version, title);
      await conversationsQuery.refetch();
      toast.success("Conversation renamed");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Could not rename conversation");
      throw cause;
    }
  }

  async function exportConversation() {
    try {
      const payload = await productionApi.exportConversation(conversationId);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `${conversationTitle.replace(/[^a-z0-9._-]+/gi, "-") || "munin-conversation"}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 0);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Could not export conversation");
    }
  }

  const conversationTitle =
    conversationMeta?.title ?? `Conversation ${conversationId.slice(0, 8)}…`;
  const conversationStatus = conversationMeta?.status ?? "active";
  const degraded = Boolean(error);

  return (
    <div className="flex h-full flex-col">
      <ConsoleHeader
        conversationId={conversationId}
        title={conversationTitle}
        conversationStatus={conversationStatus}
        status={status}
        runState={insight.runState}
        elapsedSeconds={elapsedSeconds}
        lastToolName={insight.lastToolName}
        isStreaming={isStreaming}
        onStop={stop}
        onCancel={handleCancelRun}
        cancelState={cancelState}
        providerProfiles={providerProfiles}
        providerBusy={providerBusy}
        onActivateProvider={activateProvider}
        onCreateProvider={createProvider}
        onArchive={archive}
        onRename={renameConversation}
        onExport={exportConversation}
      />

      {/* Service degraded banner — reflects `useChat.error`. */}
      {degraded && (
        <div className="flex items-center gap-2 border-b border-warning/30 bg-warning/10 px-6 py-2 text-xs text-warning">
          <WifiOff className="h-3.5 w-3.5 shrink-0" />
          <span>
            Service degraded · stream error: {error?.message ?? "unknown"}
          </span>
          {runIsActive && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-auto h-7 border-warning/40 px-2 text-warning hover:bg-warning/10"
              onClick={() => void resumeAfterHitl().catch((cause) => {
                toast.error(cause instanceof Error ? cause.message : "Could not reconnect to the active run");
              })}
            >
              Reconnect
            </Button>
          )}
        </div>
      )}

      {/* Message stream */}
      <ScrollArea
        className="min-h-0 flex-1"
        viewportRef={viewportRef}
        onViewportScroll={handleViewportScroll}
      >
        <div className="mx-auto max-w-4xl space-y-4 px-4 py-6 md:px-8">
          {messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent/10 text-accent">
                <TerminalSquare className="h-7 w-7" />
              </div>
              <div className="space-y-1">
                <h2 className="text-lg font-medium">Console ready</h2>
                <p className="max-w-sm text-sm text-secondary">
                  Type a message to start a live Munin stream. Activity traces,
                  tool calls, subagent presence, and HITL requests will appear
                  in&nbsp;real-time.
                </p>
              </div>
              <p className="hidden">
                Transport: /api/chat · AI SDK v5 UIMessageStream
              </p>
            </div>
          )}

          {messages.map((message) => (
            <MessageBubble key={message.id} role={message.role}>
              <MessagePartList message={message} onHitlResolved={resumeAfterHitl} />
            </MessageBubble>
          ))}

          {/* Streaming indicator */}
          {isStreaming && (
            <div className="flex items-center gap-2 pl-10 text-xs text-muted">
              <LoaderCircle className="h-3 w-3 animate-spin text-accent" />
              <span>Munin is working…</span>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Composer */}
      <footer className="border-t border-border bg-surface px-4 py-4 md:px-8">
        <div className="mx-auto max-w-4xl space-y-2">
          <div className="flex items-center justify-between gap-2">
            <ModeSwitcher
              mode={mode}
              onChangeMode={setMode}
              goal={goalDraft}
              onChangeGoal={setGoalDraft}
            />
            {attachedGoalId && (
              <span className="font-mono text-[0.6rem] text-muted" title="Durable goal attached to this conversation">
                goal · {attachedGoalId.slice(0, 8)}…
              </span>
            )}
          </div>
          <form
            onSubmit={(e) => void submitTurn(e)}
            className="flex items-end gap-2"
          >
            <Textarea
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (runIsActive) {
                    void sendGuidance();
                  } else {
                    void submitTurn();
                  }
                }
              }}
              placeholder={
                isStreaming && insight.activeRunId
                  ? "Guide the active run…"
                  : "State the objective, evidence, scope, or guidance…"
              }
              className="flex-1"
            />
            <div className="flex flex-col gap-2">
              <Button
                type="submit"
                disabled={!input.trim() || (isStreaming && !runIsActive)}
                className="shrink-0"
              >
                {isStreaming && !runIsActive ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : runIsActive ? (
                  <>
                    <Sparkles className="h-4 w-4" />
                    Guide
                  </>
                ) : (
                  <>
                    <Send className="h-4 w-4" />
                    Send
                  </>
                )}
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void sendGuidance()}
                disabled={!input.trim() || !runIsActive}
                className="shrink-0"
                title={
                  runIsActive
                    ? "Send as operator guidance to the active run"
                    : "Guidance is only available while a run is active"
                }
              >
                <Sparkles className="h-4 w-4" />
                Guidance
              </Button>
            </div>
          </form>
        </div>
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state (no conversation selected)
// ---------------------------------------------------------------------------

function NoConversationState() {
  return (
    <div className="grid h-full place-content-center px-8 text-center">
      <div className="flex max-w-md flex-col items-center gap-4">
        <div className="flex h-14 w-14 items-center justify-center rounded-full bg-accent/10 text-accent">
          <MessageSquare className="h-7 w-7" />
        </div>
        <div className="space-y-1">
          <h2 className="text-xl font-medium">No conversation selected</h2>
          <p className="text-sm leading-relaxed text-secondary">
            Open a conversation from the sidebar or create a new one to open
            its live AI SDK console stream.
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

/**
 * `AgentConsole` — the AI SDK v5 live-stream console view.
 *
 * Mounts `useMuninChat` against the active conversation and renders every
 * message part through the corresponding part-renderer component. This is the
 * visible UI surface wired to the durable chat stream and its event translator.
 *
 * The console provides:
 *   - Header shows the conversation title + status + Archive button.
 *   - Inline run status: elapsed timer, current run state, last tool call —
 *     derived from `useChat.status` and stream parts (no polling).
 *   - Draft auto-save per conversation id in localStorage.
 *   - Send-as-guidance button — uses the dedicated guidance mutation and
 *     reconnects the durable stream after a stopped browser reader.
 *
 * Legacy event rendering remains isolated from this durable stream surface.
 */
export default function AgentConsole({ conversationId }: AgentConsoleProps) {
  if (!conversationId) {
    return <NoConversationState />;
  }
  return <LiveConsole key={conversationId} conversationId={conversationId} />;
}
