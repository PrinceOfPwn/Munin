"use client";

import {
  useEffect,
  useRef,
  useState,
} from "react";
import type { FormEvent } from "react";
import {
  Bot,
  CheckCircle,
  LoaderCircle,
  MessageSquare,
  Send,
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

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";

import { ReasoningPart } from "@/components/chat/blocks/parts/ReasoningPart";
import { ToolInvocationPart } from "@/components/chat/blocks/parts/ToolInvocationPart";
import type { ToolInvocationState } from "@/components/chat/blocks/parts/ToolInvocationPart";
import { SubagentPresencePart } from "@/components/chat/blocks/parts/SubagentPresencePart";
import { HitlRequestPart } from "@/components/chat/blocks/parts/HitlRequestPart";
import type { HitlRequestPartProps } from "@/components/chat/blocks/parts/HitlRequestPart";
import { ArtifactPart } from "@/components/chat/blocks/parts/ArtifactPart";
import { HeartbeatPart } from "@/components/chat/blocks/parts/HeartbeatPart";
import { NotePart } from "@/components/chat/blocks/parts/NotePart";
import { GuidancePart } from "@/components/chat/blocks/parts/GuidancePart";

import {
  approveHitlRequest,
  rejectHitlRequest,
  useMuninChat,
} from "@/lib/aiChat";
import { cn } from "@/lib/utils";

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
  resolved: boolean;
  resolution?: "approved" | "rejected";
}

interface ArtifactData {
  artifactId: string;
  mimeType: string;
  uri: string;
}

interface HeartbeatData {
  ts: number;
  elapsedSeconds?: number;
}

interface NoteData {
  text: string;
}

interface GuidanceData {
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
      colour: "text-yellow-400",
    },
    streaming: {
      label: "Streaming",
      icon: Zap,
      colour: "text-green-400",
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

  // Reasoning part (chain-of-thought forwarded by backend)
  if (part.type === "reasoning") {
    const rp = part as ReasoningUIPart;
    return (
      <ReasoningPart
        key={key}
        id={key}
        text={rp.text ?? ""}
      />
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

  // Unknown part — ignore silently.
  return null;
}

// ---------------------------------------------------------------------------
// Message part list
// ---------------------------------------------------------------------------

function MessagePartList({ message }: { message: UIMessage }) {
  const hitlApprove: HitlRequestPartProps["onApprove"] = (id) => {
    void approveHitlRequest(id);
  };
  const hitlReject: HitlRequestPartProps["onReject"] = (id, reason) => {
    void rejectHitlRequest(id, reason);
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
// Console header
// ---------------------------------------------------------------------------

function ConsoleHeader({
  conversationId,
  status,
}: {
  conversationId: string;
  status: ChatStatus;
}) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-border bg-surface px-6 py-4">
      <div className="min-w-0 space-y-1">
        <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">
          AGENT CONSOLE / AI SDK v5 / LIVE STREAM
        </p>
        <h1 className="flex items-center gap-2 text-xl font-medium tracking-tight">
          <TerminalSquare className="h-5 w-5 text-accent" />
          Agent Console
        </h1>
        <span className="font-mono text-[0.65rem] text-muted">
          {conversationId}
        </span>
      </div>
      <StatusBadge status={status} />
    </header>
  );
}

// ---------------------------------------------------------------------------
// Live console (requires a conversationId)
// ---------------------------------------------------------------------------

function LiveConsole({ conversationId }: { conversationId: string }) {
  const { messages, sendMessage, status, error } = useMuninChat({
    conversationId,
  });

  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the bottom when new messages arrive.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    await sendMessage({ text });
  }

  const isStreaming = status === "streaming" || status === "submitted";

  return (
    <div className="flex h-full flex-col">
      <ConsoleHeader conversationId={conversationId} status={status} />

      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 border-b border-danger/30 bg-danger/10 px-6 py-2 text-xs text-danger">
          <WifiOff className="h-3.5 w-3.5 shrink-0" />
          <span>Stream error: {error.message}</span>
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
                  Type a message to start a live Munin stream. Reasoning, tool
                  calls, subagent presence, and HITL requests will appear
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
              <MessagePartList message={message} />
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
        <div className="mx-auto max-w-4xl">
          <form
            onSubmit={(e) => void submit(e)}
            className="flex items-end gap-2"
          >
            <Textarea
              rows={3}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void submit();
                }
              }}
              placeholder="State the objective, evidence, scope, or guidance…"
              disabled={isStreaming}
              className="flex-1"
            />
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
          </form>
          <small className="mt-2 block text-[0.65rem] text-muted">
            Streams via{" "}
            <code className="font-mono">/api/chat</code> (AI SDK v5 BFF). Turso
            remains the authoritative archive.
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
 * visible UI surface wired to the existing BFF route at `/api/chat` and the
 * translator in `lib/chat/translator.ts`.
 *
 * The legacy `ConversationView` (driven by `useRunEvents`) is kept alive in
 * parallel during the parity window; deletion is deferred to PR-16.
 */
export default function AgentConsole({ conversationId }: AgentConsoleProps) {
  if (!conversationId) {
    return <NoConversationState />;
  }
  return <LiveConsole conversationId={conversationId} />;
}
