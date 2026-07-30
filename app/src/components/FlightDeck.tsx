"use client";

import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Archive,
  Bot,
  BrainCircuit,
  ChevronRight,
  Command,
  FileBox,
  KeyRound,
  LogOut,
  Menu,
  MessageSquare,
  Network,
  PanelRight,
  Plus,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  UserRound,
} from "lucide-react";
import { cn, formatWhen, isTerminalRun, messageFromError, shortId, formatDuration } from "@/lib/utils";
import {
  productionApi,
  type Actor,
  type Conversation,
  type Run,
} from "@/lib/production-api";
import {
  useConversation,
  useConversations,
  useRunDetail,
  useRunGuidance,
  useCancelRun,
  useRetryRun,
  useSendTurn,
  useArchiveConversation,
  useCreateConversation,
} from "@/lib/queries";
import { useRunEvents, type RunPhase } from "@/lib/useRunEvents";
import { toast } from "@/components/ui/sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge, stateBadgeVariant } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Message, MessageText, GuidanceMessageBlock, type GuidanceMessage } from "@/components/chat/Message";
import { ThoughtBlock } from "@/components/chat/blocks/ThoughtBlock";
import { ToolBlock } from "@/components/chat/blocks/ToolBlock";
import { SubagentCard } from "@/components/chat/blocks/SubagentCard";
import { HitlRequest } from "@/components/chat/blocks/HitlRequest";
import { ArtifactChip } from "@/components/chat/blocks/ArtifactChip";
import { HeartbeatBar } from "@/components/chat/blocks/HeartbeatBar";
import { ParallelToolBlock } from "@/components/chat/blocks/ParallelToolBlock";
import { NoteBlock } from "@/components/chat/blocks/NoteBlock";
import { GuidanceBlock } from "@/components/chat/blocks/GuidanceBlock";
import { ComposerModeToggle, type ComposerMode } from "@/components/chat/ComposerModeToggle";
import { FloatingWindowsHost } from "@/components/chat/FloatingWindowsHost";
import { PresenceRow } from "@/components/collab/PresenceRow";
import { CollaboratorManager } from "@/components/collab/CollaboratorManager";
import { useNotes, usePostNote, usePresence, usePresenceHeartbeat } from "@/lib/useCollab";
import { useConversationEvents } from "@/lib/useConversationEvents";

type View = "command" | "conversations" | "agents" | "operations" | "memory" | "graph" | "artifacts" | "hitl" | "settings";

const NAV: Array<{ id: View; label: string; icon: typeof Command }> = [
  { id: "command", label: "Command Center", icon: Command },
  { id: "conversations", label: "Conversations", icon: MessageSquare },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "operations", label: "Operations", icon: Activity },
  { id: "memory", label: "Memory", icon: BrainCircuit },
  { id: "graph", label: "Intelligence Graph", icon: Network },
  { id: "artifacts", label: "Artifacts", icon: FileBox },
  { id: "hitl", label: "HITL Inbox", icon: ShieldCheck },
  { id: "settings", label: "Settings", icon: Settings2 },
];

export default function FlightDeck() {
  const [actor, setActor] = useState<Actor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    productionApi
      .session()
      .then(setActor)
      .catch(() => setActor(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-bg text-secondary font-mono text-xs uppercase tracking-widest">
        Opening the Raven&apos;s Memory…
      </div>
    );
  }
  if (!actor) return <Login error={error} setError={setError} authenticated={setActor} />;
  return (
    <Desk
      actor={actor}
      error={error}
      setError={setError}
      logout={() => {
        void productionApi.logout().catch((cause) => setError(messageFromError(cause)));
        setActor(null);
      }}
    />
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Login
// ────────────────────────────────────────────────────────────────────────────

function Login({
  error,
  setError,
  authenticated,
}: {
  error: string;
  setError: (error: string) => void;
  authenticated: (actor: Actor) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [bootstrap, setBootstrap] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (bootstrap) await productionApi.bootstrap(username, password);
      await productionApi.login(username, password);
      authenticated(await productionApi.session());
      // Politely ask for notification permission so long-running runs can
      // alert an away-from-desk operator when they need HITL / complete.
      if (typeof Notification !== "undefined" && Notification.permission === "default") {
        Notification.requestPermission().catch(() => {});
      }
    } catch (cause) {
      setError(messageFromError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen bg-bg text-body md:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
      <section className="hidden md:flex flex-col justify-center border-r border-border px-16 py-24 bg-[radial-gradient(circle_at_15%_42%,rgba(124,58,237,0.08),transparent_25%)]">
        <img src="/raven-mark.png" width={76} height={76} alt="Munin raven mark" className="mb-8 opacity-90" />
        <p className="font-mono text-[0.65rem] uppercase tracking-[0.16em] text-muted">MUNIN / OPERATOR ARCHIVE</p>
        <h1 className="mt-3 text-6xl font-medium leading-none tracking-tighter">The Raven&apos;s Memory</h1>
        <p className="mt-6 max-w-lg text-base leading-relaxed text-secondary">
          Every authorized operation, preserved with provenance.
        </p>
        <div className="mt-10 flex items-center gap-2 text-xs text-secondary">
          <span className="h-2 w-2 rounded-full bg-success shadow-[0_0_0_4px_rgba(16,185,129,0.14)]" />
          Authenticated production boundary
        </div>
      </section>
      <form onSubmit={submit} className="grid place-content-center px-6 py-12">
        <div className="grid w-full max-w-sm gap-4">
          <div className="flex items-center gap-2 text-base font-semibold">
            <KeyRound className="h-4 w-4 text-accent" />
            {bootstrap ? "Bootstrap the archive" : "Operator sign in"}
          </div>
          <label className="grid gap-1.5 text-[0.7rem] font-medium uppercase tracking-wider text-secondary">
            Operator ID
            <Input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              required
            />
          </label>
          <label className="grid gap-1.5 text-[0.7rem] font-medium uppercase tracking-wider text-secondary">
            Passphrase
            <Input
              type="password"
              minLength={12}
              autoComplete={bootstrap ? "new-password" : "current-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error && (
            <p className="flex items-center gap-1.5 text-xs text-danger">
              <TriangleAlert className="h-3.5 w-3.5" /> {error}
            </p>
          )}
          <Button type="submit" disabled={busy}>
            {busy ? "Verifying…" : bootstrap ? "Establish admin" : "Enter workspace"}
          </Button>
          <Button type="button" variant="outline" onClick={() => setBootstrap(!bootstrap)}>
            {bootstrap ? "Use existing account" : "First deployment? Bootstrap admin"}
          </Button>
          <small className="text-[0.7rem] leading-relaxed text-muted">
            Session tokens stay HttpOnly. Provider and MCP credentials never enter browser storage.
          </small>
        </div>
      </form>
    </main>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Desk (root chrome once authenticated)
// ────────────────────────────────────────────────────────────────────────────

function Desk({
  actor,
  error,
  setError,
  logout,
}: {
  actor: Actor;
  error: string;
  setError: (error: string) => void;
  logout: () => void;
}) {
  const [view, setView] = useState<View>("command");
  const [rail, setRail] = useState(false);
  const [inspector, setInspector] = useState(true);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const conversationsQuery = useConversations(query);
  // Subscribe to the conversation SSE at the Desk level so its status gates
  // the polling fallback in `useConversation`.  Consumers downstream still
  // read from the TanStack Query cache the SSE writes into.
  const { status: conversationSseStatus } = useConversationEvents({
    conversationId: activeConversationId,
  });
  const detailQuery = useConversation(activeConversationId, conversationSseStatus === "live");
  const runDetailQuery = useRunDetail(selectedRunId);
  const createConversation = useCreateConversation();
  const restored = useRef(false);

  const conversations = conversationsQuery.data || [];
  const detail = detailQuery.data || null;
  const runs = detail?.runs || [];
  const selectedRun = runs.find((run) => run.id === selectedRunId) || null;

  // Restore the last active conversation exactly once.
  useEffect(() => {
    if (restored.current || !conversations.length || typeof window === "undefined") return;
    restored.current = true;
    const remembered = window.localStorage.getItem("munin.activeConversationId");
    if (remembered && conversations.some((conversation) => conversation.id === remembered)) {
      setActiveConversationId(remembered);
      setView("conversations");
    }
  }, [conversations]);

  // Auto-select the tail run ONLY when the operator has no active selection,
  // OR the previously-selected run disappeared (archived, GC'd).  Never
  // override a manually chosen run with the latest one.
  useEffect(() => {
    if (!detail) return;
    if (selectedRunId && detail.runs.some((run) => run.id === selectedRunId)) return;
    const tail = detail.runs.at(-1);
    if (tail) setSelectedRunId(tail.id);
    else setSelectedRunId(null);
  }, [detail, selectedRunId]);

  // Live event stream + phase display + browser notifications on state changes.
  const [phase, setPhase] = useState<RunPhase | null>(null);
  const previousStateRef = useRef<string | null>(null);
  const streamActive = selectedRun && !isTerminalRun(selectedRun.state);
  const { status: streamStatus, lastPhase } = useRunEvents({
    runId: streamActive ? selectedRun!.id : null,
    onHeartbeat: setPhase,
    onClose: () => setPhase(null),
  });

  useEffect(() => {
    if (!selectedRun) return;
    const previous = previousStateRef.current;
    const next = selectedRun.state;
    if (previous !== next && previous !== null) {
      if (next === "waiting_for_human") {
        toast.warning("Munin needs a human decision", { description: "A HITL request is waiting in this run." });
        maybeNotify("Munin needs a human decision", `Run ${shortId(selectedRun.id)}`);
      }
      if (next === "completed") {
        toast.success("Run completed", { description: `Run ${shortId(selectedRun.id)}` });
        maybeNotify("Munin run completed", `Run ${shortId(selectedRun.id)}`);
      }
      if (next === "failed" || next === "interrupted") {
        toast.error(`Run ${next}`, { description: `Run ${shortId(selectedRun.id)}` });
      }
    }
    previousStateRef.current = next;
  }, [selectedRun]);

  // Recovered-run banner when the app first mounts on a durable long-run.
  useEffect(() => {
    if (!detail) return;
    const running = detail.runs.find((run) => run.state === "running" && Date.now() - run.updated_at_ms > 5 * 60_000);
    if (running) {
      toast(`Recovered a run from ${Math.floor((Date.now() - running.updated_at_ms) / 60_000)} minutes ago`, {
        description: "The durable worker kept executing while you were away. Reconnecting stream…",
        duration: 8_000,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail?.conversation.id]);

  async function select(id: string) {
    setActiveConversationId(id);
    setView("conversations");
    setRail(false);
    if (typeof window !== "undefined") window.localStorage.setItem("munin.activeConversationId", id);
  }

  async function create() {
    try {
      const conversation = await createConversation.mutateAsync(undefined);
      await select(conversation.id);
    } catch (cause) {
      setError(messageFromError(cause));
    }
  }

  const center =
    view === "command" ? (
      <CommandCenter conversations={conversations} create={create} select={select} />
    ) : view === "conversations" ? (
      <ConversationView
        detail={detail || null}
        runDetail={runDetailQuery.data || null}
        selectedRunId={selectedRunId}
        selectRun={setSelectedRunId}
        create={create}
        setError={setError}
        streamStatus={streamStatus}
        phase={lastPhase || phase}
        actor={actor}
      />
    ) : (
      <Registry view={view} conversations={conversations} select={select} />
    );

  return (
    <main className="grid h-screen grid-rows-[52px_minmax(0,1fr)] grid-cols-1 lg:grid-cols-[240px_minmax(0,1fr)_340px] bg-bg text-body overflow-hidden">
      <header className="flex items-center gap-2 border-b border-border bg-surface px-3 lg:col-span-3">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setRail(!rail)}
          aria-label="Toggle navigation"
          className="lg:hidden"
        >
          <Menu className="h-4 w-4" />
        </Button>
        <img src="/raven-mark.png" width={28} height={28} alt="" className="rounded-sm" />
        <div className="flex flex-col leading-tight min-w-[86px]">
          <b className="text-xs font-semibold tracking-wider">MUNIN</b>
          <small className="text-[0.65rem] text-muted">{NAV.find((item) => item.id === view)?.label}</small>
        </div>
        <div className="hidden md:flex min-w-0 items-center gap-1 text-xs text-muted">
          Intelligence flight deck <ChevronRight className="h-3 w-3" />{" "}
          <span className="truncate">{detail?.conversation.title || "Archive"}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {streamActive && <HeartbeatBar status={streamStatus} phase={lastPhase || phase} compact />}
          <span
            className={cn(
              "hidden sm:flex items-center gap-1 text-[0.7rem]",
              error ? "text-danger" : "text-secondary"
            )}
          >
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                error ? "bg-danger shadow-[0_0_0_3px_rgba(244,63,94,0.14)]" : "bg-success shadow-[0_0_0_3px_rgba(16,185,129,0.14)]"
              )}
            />
            {error ? "Service needs attention" : "Turso authority online"}
          </span>
          <Button variant="ghost" size="icon" onClick={() => setInspector(!inspector)} aria-label="Toggle inspector">
            <PanelRight className="h-4 w-4" />
          </Button>
          <Button variant="outline" size="sm" onClick={logout}>
            <UserRound className="h-3.5 w-3.5" /> {actor.username} <LogOut className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      <aside
        className={cn(
          "flex flex-col gap-3 border-r border-border bg-surface p-3 min-h-0",
          "hidden lg:flex",
          rail && "!flex absolute inset-y-[52px] left-0 z-30 w-64 shadow-2xl lg:relative lg:z-auto lg:w-auto lg:shadow-none"
        )}
      >
        <Button onClick={create} className="w-full justify-center">
          <Plus className="h-4 w-4" /> Ask Munin
        </Button>
        <nav className="flex flex-col gap-0.5 border-b border-border pb-3">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => {
                setView(id);
                setRail(false);
              }}
              className={cn(
                "flex items-center gap-2 rounded px-2 py-1.5 text-xs text-secondary transition-colors",
                view === id
                  ? "bg-active border-l-2 border-accent text-body pl-[6px]"
                  : "hover:bg-raised hover:text-body"
              )}
            >
              <Icon className="h-4 w-4" />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="flex min-h-0 flex-1 flex-col gap-2">
          <div className="flex items-center justify-between px-1 pb-1 text-[0.65rem] font-semibold uppercase tracking-wider text-muted">
            Recent operations
            <Button variant="ghost" size="icon" onClick={create} aria-label="New operation" className="h-5 w-5">
              <Plus className="h-3 w-3" />
            </Button>
          </div>
          <div className="flex items-center gap-1.5 rounded border border-border bg-bg px-2">
            <Search className="h-3.5 w-3.5 text-muted" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search durable archive"
              className="w-full bg-transparent py-1.5 text-xs placeholder:text-muted focus:outline-none"
            />
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="flex flex-col">
              {conversations.length ? (
                conversations.slice(0, 30).map((item) => (
                  <li key={item.id}>
                    <button
                      onClick={() => void select(item.id)}
                      className={cn(
                        "flex w-full flex-col gap-0.5 border-b border-border/50 px-2 py-2 text-left text-xs transition-colors",
                        activeConversationId === item.id ? "bg-active text-body" : "text-secondary hover:bg-raised hover:text-body"
                      )}
                    >
                      <span className="truncate">{item.title}</span>
                      <small className="text-[0.65rem] text-muted">{formatWhen(item.last_activity_at_ms)}</small>
                    </button>
                  </li>
                ))
              ) : (
                <li className="p-3 text-xs leading-relaxed text-muted">No operations yet.</li>
              )}
            </ul>
          </ScrollArea>
        </div>
      </aside>

      <section className="min-w-0 min-h-0 overflow-hidden">{center}</section>

      {inspector && (
        <Inspector
          run={selectedRun}
          detail={runDetailQuery.data || null}
          conversation={detail?.conversation || null}
          error={error}
          streamPhase={lastPhase || phase}
          streamStatus={streamStatus}
        />
      )}
      {/* v3.1 — Floating windows portal to document.body; must live once. */}
      <FloatingWindowsHost />
    </main>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Command Center
// ────────────────────────────────────────────────────────────────────────────

function CommandCenter({
  conversations,
  create,
  select,
}: {
  conversations: Conversation[];
  create: () => void;
  select: (id: string) => Promise<void>;
}) {
  return (
    <ScrollArea className="h-full">
      <div className="mx-auto max-w-5xl px-6 lg:px-10 py-10 space-y-8">
        <section className="flex flex-col md:flex-row md:items-end md:justify-between gap-4 border-b border-border pb-8">
          <div className="max-w-2xl space-y-2">
            <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">OPERATOR BRIEFING</p>
            <h1 className="text-4xl md:text-5xl font-medium leading-none tracking-tight">What needs your attention?</h1>
            <p className="text-secondary leading-relaxed max-w-xl">
              Chats, runs, evidence and approvals are one durable investigative story.
            </p>
          </div>
          <Button onClick={create} size="lg">
            <Sparkles className="h-4 w-4" /> Ask Munin
          </Button>
        </section>

        <section className="grid grid-cols-2 md:grid-cols-4 border border-border rounded-lg overflow-hidden">
          <Metric icon={MessageSquare} label="Recent operations" value={String(conversations.length)} note="Server-side archive" />
          <Metric
            icon={Activity}
            label="Active scope"
            value={String(conversations.filter((item) => item.status === "active").length)}
            note="Durable state"
          />
          <Metric icon={ShieldCheck} label="Human decisions" value="—" note="No requests loaded" />
          <Metric icon={BrainCircuit} label="Memory changes" value="—" note="Provenance per run" />
        </section>

        <section className="rounded-lg border border-border">
          <header className="flex items-center justify-between border-b border-border p-4">
            <div>
              <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">CONTINUITY</p>
              <h2 className="text-lg font-medium">Resume an operation</h2>
            </div>
            <Button variant="outline" size="sm" onClick={create}>
              <Plus className="h-3.5 w-3.5" /> New
            </Button>
          </header>
          {conversations.length ? (
            <ul>
              {conversations.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => void select(item.id)}
                    className="grid w-full grid-cols-[minmax(0,1fr)_120px_130px_18px] items-center gap-4 border-b border-border/60 px-4 py-3 text-left text-xs text-secondary hover:bg-raised hover:text-body transition-colors"
                  >
                    <span className="truncate text-body">{item.title}</span>
                    <span>{item.message_count} messages</span>
                    <span>{formatWhen(item.last_activity_at_ms)}</span>
                    <ChevronRight className="h-4 w-4 text-muted" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="flex flex-col items-center gap-3 px-8 py-16 text-center">
              <img src="/raven-mark.png" width={48} height={48} alt="" className="opacity-60" />
              <h3 className="text-lg font-medium">The archive is quiet</h3>
              <p className="max-w-md text-sm text-secondary leading-relaxed">
                Start an operation to persist the first conversation, run, placeholder and event trail.
              </p>
              <Button onClick={create}>Start operation</Button>
            </div>
          )}
        </section>
      </div>
    </ScrollArea>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  note,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  note: string;
}) {
  return (
    <article className="flex items-start gap-3 border-r border-b border-border p-4 last:border-r-0 md:border-b-0 md:last:border-r-0 md:[&:nth-child(2)]:border-r md:[&:nth-child(-n+2)]:border-b-0">
      <Icon className="h-4 w-4 text-accent shrink-0 mt-1" />
      <div className="flex flex-col gap-0.5">
        <small className="text-[0.65rem] uppercase tracking-wider text-muted">{label}</small>
        <strong className="text-2xl font-medium text-body">{value}</strong>
        <span className="text-[0.65rem] text-muted">{note}</span>
      </div>
    </article>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Conversation
// ────────────────────────────────────────────────────────────────────────────

function ConversationView({
  detail,
  runDetail,
  selectedRunId,
  selectRun,
  create,
  setError,
  streamStatus,
  phase,
  actor,
}: {
  detail: ReturnType<typeof useConversation>["data"];
  runDetail: ReturnType<typeof useRunDetail>["data"];
  selectedRunId: string | null;
  selectRun: (id: string) => void;
  create: () => void;
  setError: (error: string) => void;
  streamStatus: ReturnType<typeof useRunEvents>["status"];
  phase: RunPhase | null;
  actor: Actor;
}) {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ComposerMode>("turn");
  const draftKey = detail ? `munin.draft.${detail.conversation.id}` : "";
  const send = useSendTurn();
  const archive = useArchiveConversation();
  const pendingKey = useRef<string | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);

  // v3.1 — presence, notes, and conversation SSE stream.
  const conversationId = detail?.conversation.id || null;
  const presenceQuery = usePresence(conversationId);
  const notesQuery = useNotes(conversationId);
  const postNote = usePostNote(conversationId);
  const presenceHeartbeat = usePresenceHeartbeat(conversationId);
  // Subscription lives at the Desk level so `useConversation`'s polling
  // can be gated on the SSE status.  No local subscription here.

  // Detect whether any run in this conversation is still non-terminal so
  // the composer disables "Turn" mode and prefers "Guidance".
  const activeRun = useMemo(
    () =>
      (detail?.runs || []).find(
        (run) => !isTerminalRun(run.state),
      ) || null,
    [detail?.runs],
  );
  const runActive = Boolean(activeRun);

  // Auto-shift mode: if a run becomes active while the operator has "Turn"
  // selected, flip to "Guidance" so they don't get a 409 on submit.
  useEffect(() => {
    if (runActive && mode === "turn") setMode("guidance");
    if (!runActive && mode === "guidance") setMode("turn");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runActive]);

  // Draft persistence — restore on mount and per-conversation.
  useEffect(() => {
    if (!draftKey || typeof window === "undefined") return;
    const stored = window.localStorage.getItem(draftKey) || "";
    setInput(stored);
  }, [draftKey]);

  useEffect(() => {
    if (!draftKey || typeof window === "undefined") return;
    const handle = setTimeout(() => window.localStorage.setItem(draftKey, input), 200);
    return () => clearTimeout(handle);
  }, [draftKey, input]);

  // Auto-scroll to bottom as new messages/events arrive.
  useEffect(() => {
    const el = timelineRef.current;
    if (!el) return;
    // Only auto-scroll if already close to the bottom (avoid stealing scroll
    // from an operator inspecting history).
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) {
      el.scrollTop = el.scrollHeight;
    }
  }, [detail?.messages.length, runDetail?.events.length]);

  async function submit() {
    if (!detail || !input.trim() || send.isPending) return;
    const body = input.trim();
    try {
      if (mode === "note") {
        await postNote.mutateAsync(body);
        setInput("");
        if (typeof window !== "undefined") window.localStorage.removeItem(draftKey);
        toast.success("Note added (not sent to Munin)");
        return;
      }
      if (mode === "guidance") {
        if (!activeRun) {
          toast.error("No active run to guide");
          return;
        }
        await productionApi.guideRun(activeRun.id, body);
        setInput("");
        if (typeof window !== "undefined") window.localStorage.removeItem(draftKey);
        toast.success("Guidance queued for next iteration");
        return;
      }
      // mode === "turn"
      const key = pendingKey.current || crypto.randomUUID();
      pendingKey.current = key;
      const result = await send.mutateAsync({
        conversationId: detail.conversation.id,
        content: body,
        idempotencyKey: key,
      });
      pendingKey.current = null;
      setInput("");
      if (typeof window !== "undefined") window.localStorage.removeItem(draftKey);
      selectRun(result.run.id);
    } catch (cause) {
      setError(messageFromError(cause));
      toast.error(messageFromError(cause));
    }
  }

  if (!detail) {
    return (
      <div className="grid h-full place-content-center px-8 text-center">
        <div className="flex flex-col items-center gap-3">
          <img src="/raven-mark.png" width={48} height={48} alt="" className="opacity-60" />
          <h2 className="text-lg font-medium">Choose a conversation</h2>
          <p className="max-w-md text-sm text-secondary">
            Select a durable operation from the archive or start a new one. Browser state is never the only copy.
          </p>
          <Button onClick={create}>
            <Plus className="h-4 w-4" /> Start operation
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start justify-between gap-4 border-b border-border bg-surface px-6 py-4">
        <div className="min-w-0 space-y-1">
          <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">
            CONVERSATION / {detail.conversation.status}
          </p>
          <h1 className="text-2xl md:text-3xl font-medium tracking-tight truncate">{detail.conversation.title}</h1>
          <span className="text-[0.7rem] text-muted">
            {detail.messages.length} persisted timeline objects · Turso-authoritative
          </span>
        </div>
        <div className="flex items-center gap-3">
          <PresenceRow presence={presenceQuery.data || []} />
          <CollaboratorManager conversationId={detail.conversation.id} actor={actor} />
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              archive.mutate({ id: detail.conversation.id, version: detail.conversation.version, archived: true })
            }
          >
            <Archive className="h-3.5 w-3.5" /> Archive
          </Button>
        </div>
      </header>

      <ScrollArea className="flex-1 min-h-0">
        <div ref={timelineRef} className="mx-auto max-w-4xl space-y-2 px-4 md:px-8 py-6">
          {detail.messages.map((message) => {
            if (message.kind === "operator_guidance") {
              return (
                <GuidanceMessageBlock
                  key={message.id}
                  message={message as GuidanceMessage}
                />
              );
            }
            const attachedRun = detail.runs.find((run) => run.id === message.run_id);
            const isMunin = message.kind.includes("assistant") || message.kind.includes("munin");
            const isSelectedRun = attachedRun?.id === selectedRunId;
            return (
              <Message
                key={message.id}
                role={isMunin ? "munin" : "operator"}
                state={message.status}
                runIdSuffix={attachedRun ? shortId(attachedRun.id) : undefined}
                onSelectRun={attachedRun ? () => selectRun(attachedRun.id) : undefined}
              >
                {message.content && <MessageText>{message.content}</MessageText>}
                {isMunin && attachedRun && isSelectedRun && runDetail && (
                  <RunContent runDetail={runDetail} phase={phase} streamStatus={streamStatus} />
                )}
                {isMunin && attachedRun && !isSelectedRun && (
                  <button
                    onClick={() => selectRun(attachedRun.id)}
                    className="text-[0.7rem] font-mono text-muted hover:text-info transition-colors self-start"
                  >
                    show reasoning + tools for this run →
                  </button>
                )}
              </Message>
            );
          })}
          {(notesQuery.data || []).map((note) => (
            <NoteBlock key={note.id} note={note} />
          ))}
          {send.isPending && (
            <Message role="munin" state="pending">
              <p className="text-xs text-muted italic">Munin is thinking…</p>
            </Message>
          )}
        </div>
      </ScrollArea>

      <footer className="border-t border-border bg-surface px-4 md:px-8 py-4">
        <div className="mx-auto max-w-4xl space-y-2">
          <div className="flex items-center justify-between gap-2">
            <ComposerModeToggle
              mode={mode}
              onModeChange={setMode}
              runActive={runActive}
            />
            {runActive && mode === "turn" && (
              <span className="text-[0.65rem] text-warning">
                Turn disabled — run in progress
              </span>
            )}
          </div>
          <div className="flex flex-col md:flex-row gap-2 items-stretch md:items-end">
            <Textarea
              rows={3}
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                presenceHeartbeat.onKeystroke();
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
              placeholder={
                mode === "note"
                  ? "Add a sidebar note (never sent to Munin)…"
                  : mode === "guidance"
                    ? "Nudge the active run…"
                    : "State the objective, evidence, scope, or guidance…"
              }
              className="flex-1"
            />
            <Button
              onClick={() => void submit()}
              disabled={!input.trim() || send.isPending || postNote.isPending || (mode === "turn" && runActive)}
              className="md:self-end"
            >
              {send.isPending
                ? "Persisting…"
                : mode === "note"
                  ? "Post note"
                  : mode === "guidance"
                    ? "Queue guidance"
                    : "Commit turn"}
              <Send className="h-4 w-4" />
            </Button>
          </div>
          <small className="mt-2 block text-[0.65rem] text-muted">
            Enter to send · Shift+Enter for newline · draft auto-saves locally · notes stay client-side of the model.
          </small>
        </div>
      </footer>
    </div>
  );
}

function RunContent({
  runDetail,
  phase,
  streamStatus,
}: {
  runDetail: NonNullable<ReturnType<typeof useRunDetail>["data"]>;
  phase: RunPhase | null;
  streamStatus: ReturnType<typeof useRunEvents>["status"];
}) {
  const { reasoning, tools, subagents, human_requests: humanRequests, artifacts } = runDetail;
  const running = !isTerminalRun(runDetail.run.state);
  const guidanceQuery = useRunGuidance(runDetail.run.id);
  const guidance = guidanceQuery.data || [];

  // Reasoning that belongs to the orchestrator (top-level agent).  Anything
  // attributed to a subagent name is rendered inside the SubagentCard below.
  const subagentNames = new Set(subagents.map((s) => s.agent_name || s.profile_id));
  const orchestratorReasoning = reasoning.filter((r) => !subagentNames.has(r.agent_name));
  const orchestratorTools = tools.filter((t) => !subagentNames.has(t.agent_name));

  // Collapse very long timelines: if we have >60 reasoning events, only show
  // the last 20 by default with an expand affordance.
  const [showAllHistory, setShowAllHistory] = useState(false);
  const collapseThreshold = 60;
  const visibleReasoning = useMemo(() => {
    if (orchestratorReasoning.length <= collapseThreshold || showAllHistory) return orchestratorReasoning;
    return orchestratorReasoning.slice(-20);
  }, [orchestratorReasoning, showAllHistory]);
  const hidden = orchestratorReasoning.length - visibleReasoning.length;

  return (
    <div className="space-y-2 min-w-0">
      {hidden > 0 && (
        <button
          onClick={() => setShowAllHistory(true)}
          className="text-[0.7rem] font-mono text-muted hover:text-body transition-colors self-start"
        >
          ↑ Show {hidden} earlier thought{hidden === 1 ? "" : "s"}
        </button>
      )}
      {visibleReasoning.map((event) => (
        <ThoughtBlock
          key={event.id}
          content={event.content}
          agent={event.agent_name}
          kind={event.kind}
          step={event.step}
        />
      ))}
      {(() => {
        // v3.1 — Group orchestrator tools by parallel_group_id so a batch
        // renders as a single ParallelToolBlock instead of a stack.
        const groups = new Map<string, typeof orchestratorTools>();
        const singles: typeof orchestratorTools = [];
        for (const tool of orchestratorTools) {
          const gid = (tool as { parallel_group_id?: string | null }).parallel_group_id;
          if (gid) {
            const existing = groups.get(gid) || [];
            existing.push(tool);
            groups.set(gid, existing);
          } else {
            singles.push(tool);
          }
        }
        // Preserve original order: use the earliest tool of each group as
        // the group's timeline position.
        type Node = { key: string; index: number; render: () => JSX.Element };
        const nodes: Node[] = [];
        singles.forEach((tool, idx) => {
          const originalIdx = orchestratorTools.indexOf(tool);
          nodes.push({
            key: tool.id,
            index: originalIdx,
            render: () => <ToolBlock key={tool.id} tool={tool} />,
          });
        });
        for (const [gid, group] of groups.entries()) {
          const firstIdx = Math.min(...group.map((t) => orchestratorTools.indexOf(t)));
          nodes.push({
            key: `group-${gid}`,
            index: firstIdx,
            render: () => <ParallelToolBlock key={gid} groupId={gid} tools={group} />,
          });
        }
        nodes.sort((a, b) => a.index - b.index);
        return nodes.map((n) => n.render());
      })()}
      {subagents.map((subagent) => (
        <SubagentCard
          key={subagent.id}
          subagent={subagent}
          reasoning={reasoning}
          tools={tools}
          runId={runDetail.run.id}
        />
      ))}
      {guidance.map((entry) => (
        <GuidanceBlock key={entry.id} guidance={entry} />
      ))}
      {humanRequests
        .filter((request) => request.state === "pending" || (running && request.state !== "resolved"))
        .map((request) => (
          <HitlRequest key={request.id} request={request} />
        ))}
      {artifacts.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {artifacts.map((artifact) => (
            <ArtifactChip key={artifact.id} artifact={artifact} />
          ))}
        </div>
      )}
      {running && streamStatus !== "closed" && (
        <div className="pt-1">
          <HeartbeatBar status={streamStatus} phase={phase} />
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Registry (placeholder tabs for Agents / Operations / Memory / ...)
// ────────────────────────────────────────────────────────────────────────────

function Registry({
  view,
  conversations,
  select,
}: {
  view: View;
  conversations: Conversation[];
  select: (id: string) => Promise<void>;
}) {
  const catalog: Record<string, { title: string; detail: string; Icon: typeof Activity }> = {
    agents: {
      title: "Agent roster and command chain",
      detail:
        "Profiles, leases, budgets and hierarchy attach to durable runs. Open the Agents endpoint to inspect the manifest.",
      Icon: Bot,
    },
    operations: {
      title: "Long-horizon operation ledger",
      detail: "Queued, running, waiting for human, completed, failed, interrupted and cancelled are durable states.",
      Icon: Activity,
    },
    memory: {
      title: "Provenance before compression",
      detail: "Summaries cite their source range and raw events remain retained for audit and replay.",
      Icon: BrainCircuit,
    },
    graph: {
      title: "Connected evidence, not a hairball",
      detail: "The graph progressively loads authorized conversations, runs, assets and findings.",
      Icon: Network,
    },
    artifacts: {
      title: "Evidence with provenance",
      detail: "Artifacts are hash-addressed and permission-checked. Select an operation to inspect created evidence.",
      Icon: FileBox,
    },
    hitl: {
      title: "Human decision boundary",
      detail: "Risky actions pause until a scope-bound, authenticated approval is consumed once.",
      Icon: ShieldCheck,
    },
    settings: {
      title: "Provider and policy controls",
      detail: "BYOK stays encrypted in Turso. Page Agent and runtime extensions remain disabled until permission gates are configured.",
      Icon: Settings2,
    },
  };
  const entry = catalog[view];
  if (!entry) return null;
  const { title, detail, Icon } = entry;
  return (
    <ScrollArea className="h-full">
      <div className="mx-auto max-w-5xl space-y-6 px-6 lg:px-10 py-10">
        <section className="flex items-start gap-4 border-b border-border pb-6 text-accent">
          <Icon className="h-8 w-8 shrink-0" />
          <div className="space-y-2">
            <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">{view.toUpperCase()}</p>
            <h1 className="text-3xl font-medium tracking-tight text-body">{title}</h1>
            <p className="text-secondary leading-relaxed max-w-2xl">{detail}</p>
          </div>
        </section>
        {conversations.length > 0 && (
          <section className="rounded-lg border border-border">
            <header className="border-b border-border p-4">
              <h2 className="text-base font-medium">Related durable operations</h2>
            </header>
            <ul>
              {conversations.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => void select(item.id)}
                    className="grid w-full grid-cols-[minmax(0,1fr)_120px_130px_18px] items-center gap-4 border-b border-border/60 px-4 py-3 text-left text-xs text-secondary hover:bg-raised hover:text-body transition-colors last:border-b-0"
                  >
                    <span className="truncate text-body">{item.title}</span>
                    <span>{item.status}</span>
                    <span>{formatWhen(item.last_activity_at_ms)}</span>
                    <ChevronRight className="h-4 w-4 text-muted" />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </ScrollArea>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Inspector — right rail
// ────────────────────────────────────────────────────────────────────────────

function Inspector({
  run,
  detail,
  conversation,
  error,
  streamPhase,
  streamStatus,
}: {
  run: Run | null;
  detail: ReturnType<typeof useRunDetail>["data"];
  conversation: Conversation | null;
  error: string;
  streamPhase: RunPhase | null;
  streamStatus: ReturnType<typeof useRunEvents>["status"];
}) {
  const cancelRun = useCancelRun();
  const retryRun = useRetryRun();

  if (!run) {
    return (
      <aside className="hidden lg:block h-full min-h-0 overflow-auto border-l border-border bg-surface p-4">
        <header className="border-b border-border pb-4">
          <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">CONTEXT INSPECTOR</p>
          <h2 className="text-base font-medium">System boundary</h2>
        </header>
        <dl className="mt-4 grid grid-cols-[90px_minmax(0,1fr)] gap-x-4 gap-y-3 text-xs">
          <dt className="text-muted">Conversation</dt>
          <dd className="text-body">{conversation?.title || "No selection"}</dd>
          <dt className="text-muted">Storage</dt>
          <dd className="text-body">Turso authority</dd>
          <dt className="text-muted">Client</dt>
          <dd className="text-body">Acceleration only</dd>
          <dt className="text-muted">Service</dt>
          <dd className="text-body">{error ? "Needs attention" : "Available"}</dd>
        </dl>
        <p className="mt-4 rounded border-l-2 border-info bg-raised p-3 text-xs leading-relaxed text-secondary">
          Select a run to inspect reasoning provenance, tool intent, evidence, scope and artifacts without leaving the operation.
        </p>
      </aside>
    );
  }

  const active = ["queued", "running", "waiting_for_human"].includes(run.state);
  const terminal = isTerminalRun(run.state);

  return (
    <aside className="hidden lg:block h-full min-h-0 overflow-auto border-l border-border bg-surface p-4 space-y-4">
      <header className="border-b border-border pb-4">
        <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">CONTEXT INSPECTOR</p>
        <h2 className="text-base font-medium">Run state</h2>
      </header>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Badge variant={stateBadgeVariant(run.state)}>{run.state.replaceAll("_", " ")}</Badge>
          {streamPhase && (
            <span className="text-[0.65rem] font-mono text-muted">
              {formatDuration(streamPhase.elapsed_seconds)} elapsed
            </span>
          )}
        </div>
        <HeartbeatBar status={streamStatus} phase={streamPhase} />
      </div>

      <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-4 gap-y-3 text-xs">
        <dt className="text-muted">Attempt</dt>
        <dd className="text-body">{run.attempt}</dd>
        <dt className="text-muted">Fencing</dt>
        <dd className="font-mono text-[0.7rem] text-body break-all">{run.fencing_epoch}</dd>
        <dt className="text-muted">Run</dt>
        <dd className="font-mono text-[0.7rem] text-body break-all">{run.id}</dd>
        {detail?.reasoning.length !== undefined && (
          <>
            <dt className="text-muted">Reasoning</dt>
            <dd className="text-body">{detail.reasoning.length}</dd>
          </>
        )}
        {detail?.tools.length !== undefined && (
          <>
            <dt className="text-muted">Tools</dt>
            <dd className="text-body">{detail.tools.length}</dd>
          </>
        )}
        {detail?.subagents.length !== undefined && (
          <>
            <dt className="text-muted">Subagents</dt>
            <dd className="text-body">{detail.subagents.length}</dd>
          </>
        )}
      </dl>

      <div className="flex flex-wrap gap-2">
        {active && (
          <Button size="sm" variant="outline" onClick={() => cancelRun.mutate(run.id)} disabled={cancelRun.isPending}>
            {cancelRun.isPending ? "Cancelling…" : "Cancel safely"}
          </Button>
        )}
        {terminal && (
          <Button size="sm" variant="outline" onClick={() => retryRun.mutate(run.id)} disabled={retryRun.isPending}>
            {retryRun.isPending ? "Retrying…" : "Retry attempt"}
          </Button>
        )}
      </div>

      {detail?.reasoning.length ? (
        <section className="space-y-1 border-t border-border pt-3">
          <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">COGNITION</p>
          {detail.reasoning.slice(-4).map((event) => (
            <article key={event.id} className="border-l-2 border-accent pl-2 space-y-0.5">
              <b className="block text-[0.7rem] text-body capitalize">{event.kind.replaceAll("_", " ")}</b>
              <span className="block text-[0.65rem] text-muted">
                {event.agent_name} · step {event.step}
              </span>
              <p className="text-[0.7rem] leading-relaxed text-secondary line-clamp-3 m-0">{event.content}</p>
            </article>
          ))}
        </section>
      ) : (
        <p className="rounded border-l-2 border-info bg-raised p-2 text-[0.7rem] leading-relaxed text-secondary">
          No provider reasoning has been persisted yet.
        </p>
      )}

      {detail?.tools.length ? (
        <section className="space-y-1 border-t border-border pt-3">
          <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">TOOLS</p>
          {detail.tools.slice(-4).map((tool) => (
            <article key={tool.id} className="border-l-2 border-info pl-2 space-y-0.5">
              <b className="block font-mono text-[0.7rem] text-body">{tool.tool_name}</b>
              <span className="block text-[0.65rem] text-muted">
                {tool.agent_name} · {tool.state}
              </span>
            </article>
          ))}
        </section>
      ) : null}

      <p className="rounded border-l-2 border-info bg-raised p-2 text-[0.7rem] leading-relaxed text-secondary">
        Late workers cannot complete an expired or fenced lease. Raven Replay is recorded-only until a sandbox gate is explicitly configured.
      </p>
    </aside>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Notification helper
// ────────────────────────────────────────────────────────────────────────────

function maybeNotify(title: string, body: string) {
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  try {
    new Notification(title, { body, icon: "/raven-mark.png", tag: "munin-run-state" });
  } catch {
    /* Notification constructor unavailable (older Safari, PWAs, etc). */
  }
}
