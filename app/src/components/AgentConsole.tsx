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
  LoaderCircle,
  MessageSquare,
  Send,
  Sparkles,
  Square,
  TerminalSquare,
  WifiOff,
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
import { ProviderSwitcher } from "@/components/ProviderSwitcher";

import {
  approveHitlRequest,
  rejectHitlRequest,
  useMuninChat,
} from "@/lib/aiChat";
import { useConversations } from "@/lib/queries";
import { productionApi, type ProviderProfile } from "@/lib/production-api";
import { cn, formatDuration } from "@/lib/utils";

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
        <p className="whitespace-pre-wrap">{tp.text}</p>
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
    // Transient heartbeats are not rendered inline.
    return null;
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
}

function useStreamInsight(messages: UIMessage[]): StreamInsight {
  return useMemo(() => {
    let activeRunId: string | null = null;
    let lastToolName: string | null = null;
    let runState: string | null = null;
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
        }
      }
    }
    return { activeRunId, lastToolName, runState };
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
  providerProfiles,
  providerBusy,
  onActivateProvider,
  onCreateProvider,
  onArchive,
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
  onArchive: () => void;
}) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-4">
      <div className="min-w-0 space-y-1">
        <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">
          AGENT CONSOLE / AI SDK v5 / LIVE STREAM
        </p>
        <div className="flex min-w-0 items-center gap-2">
          <TerminalSquare className="h-5 w-5 shrink-0 text-accent" />
          <h1 className="min-w-0 truncate text-xl font-medium tracking-tight">
            {title}
          </h1>
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
            title="Stop streaming and leave the durable run recoverable"
          >
            <Square className="h-3.5 w-3.5" /> Stop
          </Button>
        )}
        <ProviderSwitcher
          profiles={providerProfiles}
          busy={providerBusy}
          onActivate={onActivateProvider}
          onCreate={onCreateProvider}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={onArchive}
          disabled={conversationStatus === "archived"}
        >
          <Archive className="h-3.5 w-3.5" /> Archive
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
  const bottomRef = useRef<HTMLDivElement | null>(null);

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

  // Auto-scroll to the bottom when new messages arrive.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const isStreaming = status === "streaming" || status === "submitted";
  const insight = useStreamInsight(messages);
  const elapsedSeconds = useElapsedSeconds(isStreaming);

  async function submitTurn(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(draftKey);
    }
    await sendMessage({ text });
  }

  async function sendGuidance() {
    const text = input.trim();
    if (!text) return;
    if (!insight.activeRunId) {
      toast.error("No active run to guide");
      return;
    }
    setInput("");
    if (typeof window !== "undefined") {
      window.localStorage.removeItem(draftKey);
    }
    try {
      await sendMessage({
        // The AI SDK v5 `sendMessage` accepts an arbitrary parts array; the
        // BFF (Fase 1a) intercepts `data-operator-guidance` and forwards it
        // to `POST /api/chat/{run_id}/guidance` out-of-band. It never
        // becomes a new user turn on the timeline.
        parts: [
          {
            type: "data-operator-guidance",
            data: {
              runId: insight.activeRunId,
              body: text,
            },
          },
        ],
      } as Parameters<typeof sendMessage>[0]);
      toast.success("Guidance queued for the active run");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Guidance failed");
    }
  }

  async function resumeAfterHitl() {
    await resumeStream();
  }

  function archive() {
    // Archive lives behind the legacy PATCH endpoint (see Fase 1c/2). Fase 1b
    // keeps this a stub so the header UI is correct without dragging the
    // legacy production-api dependency into the new shell.
    toast.message("Archive coming in phase 3", {
      description:
        "The legacy PATCH endpoint stays behind the FlightDeckStable shell during the parity window.",
    });
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
        providerProfiles={providerProfiles}
        providerBusy={providerBusy}
        onActivateProvider={activateProvider}
        onCreateProvider={createProvider}
        onArchive={archive}
      />

      {/* Service degraded banner — reflects `useChat.error`. */}
      {degraded && (
        <div className="flex items-center gap-2 border-b border-warning/30 bg-warning/10 px-6 py-2 text-xs text-warning">
          <WifiOff className="h-3.5 w-3.5 shrink-0" />
          <span>
            Service degraded · stream error: {error?.message ?? "unknown"}
          </span>
        </div>
      )}

      {/* Message stream */}
      <ScrollArea className="min-h-0 flex-1">
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
              <p className="rounded-md border border-border bg-surface px-4 py-2 font-mono text-[0.65rem] text-muted">
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

          <div ref={bottomRef} aria-hidden />
        </div>
      </ScrollArea>

      {/* Composer */}
      <footer className="border-t border-border bg-surface px-4 py-4 md:px-8">
        <div className="mx-auto max-w-4xl space-y-2">
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
                  if (isStreaming && insight.activeRunId) {
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
                disabled={!input.trim() || isStreaming}
                className="shrink-0"
              >
                {isStreaming ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
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
                disabled={!input.trim() || !isStreaming || !insight.activeRunId}
                className="shrink-0"
                title={
                  isStreaming
                    ? "Send as operator guidance to the active run"
                    : "Guidance is only available while a run is streaming"
                }
              >
                <Sparkles className="h-4 w-4" />
                Guidance
              </Button>
            </div>
          </form>
          <small className="block text-[0.65rem] text-muted">
            Streams via <code className="font-mono">/api/chat</code> (AI SDK v5
            BFF). Draft auto-saves locally per conversation. Turso remains the
            authoritative archive.
          </small>
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
        <p className="rounded-md border border-border bg-surface px-4 py-2 font-mono text-[0.65rem] text-muted">
          CONSOLE / AI SDK v5 / BFF transport active
        </p>
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
 * visible UI surface wired to the BFF route at `/api/chat` (Fase 1a) and the
 * translator in `lib/chat/translator.ts`.
 *
 * Fase 1b extensions:
 *   - Header shows the conversation title + status + Archive button (stub).
 *   - Inline run status: elapsed timer, current run state, last tool call —
 *     derived from `useChat.status` and stream parts (no polling).
 *   - Draft auto-save per conversation id in localStorage.
 *   - Send-as-guidance button — emits a `data-operator-guidance` UI part that
 *     the BFF intercepts and forwards to `POST /api/chat/{run_id}/guidance`.
 *
 * The legacy `ConversationView` (driven by `useRunEvents`) is kept alive in
 * parallel during the parity window; the entrypoint switch and deletion
 * happen in Fase 1c and Fase 2.
 */
export default function AgentConsole({ conversationId }: AgentConsoleProps) {
  if (!conversationId) {
    return <NoConversationState />;
  }
  return <LiveConsole conversationId={conversationId} />;
}
