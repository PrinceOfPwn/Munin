"use client";

import { useEffect, useRef, useState } from "react";
import { Play, Pause, RefreshCw, Activity, Send } from "lucide-react";
import { useMuninStore } from "@/store/muninStore";
import { getMcpClient, extractToolResultContent } from "@/lib/mcp";
import { relativeTime } from "@/lib/format";
import { log } from "@/lib/logger";
import { cn } from "@/lib/utils";

const L = log.ns("subagent-trace");

interface Props {
  subagent: string;
  autoStart?: boolean;
  onClose?: () => void;
}

interface TraceEvent {
  id: number;
  ts: string;
  action: string;
  input: any;
  output: any;
  tags: string[];
}

interface TraceMessage {
  id: number;
  created_at: string;
  message_type: string;
  subject: string;
  body: string;
  status: string;
}

/**
 * Live view of a subagent's ReAct loop.
 *
 * Polls `subagent_trace` with independent event/message cursors every 1.5s
 * and appends both streams to a scrollable view. The human doesn't control the subagent —
 * they observe it in real time so they can decide whether to intervene at the
 * task level (e.g. cancel the wake, ask Munin something else).
 *
 * Stops polling automatically when the subagent's presence becomes IDLE and
 * we haven't seen new events for ~10 seconds.
 */
export default function SubagentTrace({ subagent, autoStart = true, onClose }: Props) {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);

  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [messages, setMessages] = useState<TraceMessage[]>([]);
  const [presence, setPresence] = useState<any>(null);
  const [polling, setPolling] = useState(autoStart);
  const [error, setError] = useState<string | null>(null);
  const [guidance, setGuidance] = useState("");
  const [sending, setSending] = useState(false);

  const streamRef = useRef<HTMLDivElement>(null);
  const lastEventAt = useRef<number>(Date.now());
  // Refs for anything the interval reads WITHOUT wanting it as a hook dep —
  // dropping it from the deps was the fix for the tight-loop bug: previously
  // `pollCount` was a state that both the tick incremented AND the effect
  // listed as a dep, so every tick tore down and re-armed the interval,
  // firing another tick immediately. Net result: request storm ~every RTT
  // instead of the intended 1.5s.
  const eventSinceIdRef = useRef(0);
  const messageSinceIdRef = useRef(0);
  const pollCountRef = useRef(0);

  useEffect(() => {
    if (!polling) return;

    let cancelled = false;
    const tick = async () => {
      try {
        const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
        const r = await client.callTool("subagent_trace", {
          subagent,
          since_event_id: eventSinceIdRef.current,
          since_message_id: messageSinceIdRef.current,
          include_messages: true,
          limit: 200,
        });
        if (cancelled) return;
        const { json } = extractToolResultContent(r);
        const data = json?.data ?? {};
        const newEvents: TraceEvent[] = Array.isArray(data.events) ? data.events : [];
        const newMsgs: TraceMessage[] = Array.isArray(data.messages) ? data.messages : [];

        if (newEvents.length > 0 || newMsgs.length > 0) {
          setEvents((prev) => [...prev, ...newEvents]);
          setMessages((prev) => [...prev, ...newMsgs]);
          lastEventAt.current = Date.now();
        }
        if (typeof data.next_event_id === "number" && data.next_event_id > eventSinceIdRef.current) {
          eventSinceIdRef.current = data.next_event_id;
        }
        if (typeof data.next_message_id === "number" && data.next_message_id > messageSinceIdRef.current) {
          messageSinceIdRef.current = data.next_message_id;
        }
        setPresence(data.presence ?? null);
        pollCountRef.current += 1;
        setError(null);

        // Auto-stop after 10s of no activity AND agent not RUNNING.
        const idle = Date.now() - lastEventAt.current > 10_000;
        const notRunning = data.presence?.status !== "RUNNING";
        if (idle && notRunning && pollCountRef.current > 3) {
          L.info(`auto-stop: ${subagent} idle + not running`);
          setPolling(false);
        }
      } catch (e: any) {
        setError(e?.message || String(e));
      }
    };

    let timeoutId: number | undefined;
    const poll = async () => {
      await tick();
      if (!cancelled) {
        timeoutId = window.setTimeout(poll, 1500);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
    // NOTE: intentionally NOT listing cursor/pollCount refs here — they're refs.
    // Only re-arm the interval on ACTUAL config change or pause toggle.
  }, [polling, subagent, mcpUrl, mcpToken]);

  // Auto-scroll to bottom on new events
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [events.length, messages.length]);

  const stream = [...events.map((e) => ({ kind: "event" as const, id: e.id, when: e.ts, data: e })),
                  ...messages.map((m) => ({ kind: "message" as const, id: m.id, when: m.created_at, data: m }))]
    .sort((a, b) => {
      const byTime = Date.parse(a.when) - Date.parse(b.when);
      return Number.isNaN(byTime) || byTime === 0 ? a.id - b.id : byTime;
    });

  const status = String(presence?.status || "UNKNOWN").toUpperCase();
  const statusColor = status === "RUNNING" ? "#10b981"
                    : status === "SPAWNING" ? "#f59e0b"
                    : status === "EXITING" ? "#f43f5e"
                    : "#6b7280";

  const sendGuidance = async () => {
    const body = guidance.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      await client.callTool("post_agent_message", {
        sender_agent: "human",
        recipient_agent: subagent,
        subject: "Live operator guidance",
        message_type: "HUMAN",
        body,
        metadata_json: JSON.stringify({ source: "munin-ui", live: true }),
      });
      setGuidance("");
      setPolling(true);
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col h-full bg-bg border border-border rounded overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-surface/50">
        <div className="flex items-center gap-2 min-w-0">
          <Activity size={14} style={{ color: statusColor }} className={polling ? "animate-pulse" : ""} />
          <div className="min-w-0">
            <div className="text-sm font-mono text-body truncate">{subagent}</div>
            <div className="text-[10px] text-muted font-mono flex items-center gap-2">
              <span style={{ color: statusColor }}>{status}</span>
              <span>·</span>
              <span>{stream.length} events</span>
              <span>·</span>
              <span>{pollCountRef.current} polls</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setPolling((p) => !p)}
            className="p-1.5 rounded hover:bg-surface text-muted hover:text-accent"
            title={polling ? "Pause" : "Resume"}
          >
            {polling ? <Pause size={13} /> : <Play size={13} />}
          </button>
          <button
            onClick={() => {
              setEvents([]);
              setMessages([]);
              eventSinceIdRef.current = 0;
              messageSinceIdRef.current = 0;
              lastEventAt.current = Date.now();
            }}
            className="p-1.5 rounded hover:bg-surface text-muted hover:text-accent"
            title="Clear stream"
          >
            <RefreshCw size={13} />
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-surface text-muted hover:text-rose text-sm font-mono"
              title="Close"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* Stream */}
      <div ref={streamRef} className="flex-1 overflow-y-auto px-3 py-2 space-y-1 text-xs font-mono">
        {stream.length === 0 && !error && (
          <div className="text-muted italic text-center py-8">
            {polling ? "Waiting for activity…" : "No events yet — press play to poll."}
          </div>
        )}
        {error && (
          <div className="text-rose text-[11px] p-2 border border-rose/40 rounded">
            trace error: {error}
          </div>
        )}
        {stream.map((item) => (
          <StreamRow key={`${item.kind}-${item.id}`} item={item} />
        ))}
      </div>
      <div className="border-t border-border p-2 bg-surface/40">
        <div className="flex items-end gap-2">
          <textarea
            value={guidance}
            onChange={(event) => setGuidance(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void sendGuidance();
              }
            }}
            rows={2}
            placeholder={`Guide ${subagent} on its next ReAct step…`}
            className="flex-1 resize-none bg-bg border border-border rounded px-2 py-1.5 text-xs font-mono text-body focus:outline-none focus:border-accent/60"
          />
          <button
            onClick={() => void sendGuidance()}
            disabled={sending || !guidance.trim()}
            className="p-2 rounded border border-accent/50 bg-accent/10 text-accent hover:bg-accent/20 disabled:opacity-40"
            title="Send live guidance"
          >
            <Send size={14} className={sending ? "animate-pulse" : ""} />
          </button>
        </div>
        <p className="mt-1 text-[9px] text-muted font-mono">
          Injected after the current tool call and before the next model decision.
        </p>
      </div>
    </div>
  );
}

function StreamRow({ item }: { item: any }) {
  if (item.kind === "event") {
    const e: TraceEvent = item.data;
    const isTool = e.action?.startsWith("tool:");
    const toolName = isTool ? e.action.slice(5) : "";
    const ok = e.output?.ok !== false;
    const summary = e.output?.summary || e.output?.content_snippet || "";
    return (
      <div className={cn("flex items-start gap-2 py-0.5", !ok && "text-rose")}>
        <span className="text-muted text-[10px] whitespace-nowrap w-14 shrink-0">
          {new Date(e.ts).toLocaleTimeString([], { hour12: false })}
        </span>
        {isTool ? (
          <>
            <span className={cn("font-bold", ok ? "text-accent" : "text-rose")}>▸</span>
            <span className="text-body">{toolName}</span>
            {summary && <span className="text-muted truncate">— {summary}</span>}
          </>
        ) : (
          <>
            <span className="text-muted">·</span>
            <span className="text-muted">{e.action}</span>
            {summary && <span className="text-muted truncate">{summary}</span>}
          </>
        )}
      </div>
    );
  }
  // message
  const m: TraceMessage = item.data;
  const isProgress = m.message_type === "PROGRESS";
  const isError = m.message_type === "ERROR";
  return (
    <div className={cn("flex items-start gap-2 py-0.5 pl-16",
      isError ? "text-rose" : isProgress ? "text-ice" : "text-emerald")}>
      <span className="text-[10px]">✉</span>
      <span className="uppercase text-[9px] font-bold tracking-widest shrink-0">
        {m.message_type}
      </span>
      <span className="truncate">{m.subject}</span>
    </div>
  );
}
