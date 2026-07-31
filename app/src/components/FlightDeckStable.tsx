"use client";

import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Archive,
  Bot,
  BrainCircuit,
  ChevronRight,
  Command,
  Database,
  FileBox,
  KeyRound,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquare,
  Network,
  PanelRight,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  TriangleAlert,
  UserRound,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge, stateBadgeVariant } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "@/components/ui/sonner";
import {
  Message,
  MessageText,
  GuidanceMessageBlock,
  type GuidanceMessage,
} from "@/components/chat/Message";
import { ThoughtBlock } from "@/components/chat/blocks/ThoughtBlock";
import { ToolBlock } from "@/components/chat/blocks/ToolBlock";
import { SubagentCard } from "@/components/chat/blocks/SubagentCard";
import { HitlRequest } from "@/components/chat/blocks/HitlRequest";
import { ArtifactChip } from "@/components/chat/blocks/ArtifactChip";
import { HeartbeatBar } from "@/components/chat/blocks/HeartbeatBar";
import { NoteBlock } from "@/components/chat/blocks/NoteBlock";
import { ComposerModeToggle, type ComposerMode } from "@/components/chat/ComposerModeToggle";
import { FloatingWindowsHost } from "@/components/chat/FloatingWindowsHost";
import { PresenceRow } from "@/components/collab/PresenceRow";
import { CollaboratorManager } from "@/components/collab/CollaboratorManager";

import {
  productionApi,
  type Actor,
  type Conversation,
  type ConversationDetail,
  type Run,
  type RunDetail,
} from "@/lib/production-api";
import {
  useAgents,
  useArchiveConversation,
  useConversation,
  useConversations,
  useCreateConversation,
  useProviderProfiles,
  useRunDetail,
  useSendTurn,
} from "@/lib/queries";
import { useConversationEvents } from "@/lib/useConversationEvents";
import { useRunEvents, type RunPhase } from "@/lib/useRunEvents";
import { useNotes, usePostNote, usePresence, usePresenceHeartbeat } from "@/lib/useCollab";
import {
  clearMuninQueryCache,
  muninQueryCacheInfo,
} from "@/lib/query-cache";
import {
  cn,
  formatDuration,
  formatWhen,
  isTerminalRun,
  messageFromError,
  shortId,
} from "@/lib/utils";

type View =
  | "command"
  | "conversations"
  | "agents"
  | "operations"
  | "memory"
  | "graph"
  | "artifacts"
  | "hitl"
  | "settings";

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

const VIEW_IDS = new Set<View>(NAV.map((item) => item.id));

function storedView(): View {
  if (typeof window === "undefined") return "command";
  const value = window.localStorage.getItem("munin.activeView") as View | null;
  return value && VIEW_IDS.has(value) ? value : "command";
}

function storedConversation(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("munin.activeConversationId");
}

export default function FlightDeckStable() {
  const qc = useQueryClient();
  const [actor, setActor] = useState<Actor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function acceptActor(next: Actor) {
    if (typeof window !== "undefined") {
      const previousActor = window.localStorage.getItem("munin.cacheActorId");
      if (previousActor && previousActor !== next.id) {
        qc.clear();
        await clearMuninQueryCache();
        window.localStorage.removeItem("munin.activeConversationId");
      }
      window.localStorage.setItem("munin.cacheActorId", next.id);
    }
    setActor(next);
  }

  useEffect(() => {
    let cancelled = false;
    productionApi
      .session()
      .then((next) => {
        if (!cancelled) void acceptActor(next);
      })
      .catch(() => {
        if (!cancelled) setActor(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-bg font-mono text-xs uppercase tracking-widest text-secondary">
        Opening the Raven&apos;s Memory…
      </div>
    );
  }

  if (!actor) {
    return <Login error={error} setError={setError} authenticated={acceptActor} />;
  }

  return (
    <Workspace
      actor={actor}
      globalError={error}
      setGlobalError={setError}
      logout={async () => {
        try {
          await productionApi.logout();
        } catch (cause) {
          setError(messageFromError(cause));
        } finally {
          qc.clear();
          await clearMuninQueryCache();
          if (typeof window !== "undefined") {
            window.localStorage.removeItem("munin.cacheActorId");
            window.localStorage.removeItem("munin.activeConversationId");
          }
          setActor(null);
        }
      }}
    />
  );
}

function Login({
  error,
  setError,
  authenticated,
}: {
  error: string;
  setError: (error: string) => void;
  authenticated: (actor: Actor) => Promise<void>;
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
      await authenticated(await productionApi.session());
    } catch (cause) {
      setError(messageFromError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen bg-bg text-body md:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
      <section className="hidden flex-col justify-center border-r border-border px-16 py-24 md:flex">
        <img src="/raven-mark.png" width={76} height={76} alt="Munin raven mark" className="mb-8 opacity-90" />
        <p className="font-mono text-[0.65rem] uppercase tracking-[0.16em] text-muted">MUNIN / OPERATOR ARCHIVE</p>
        <h1 className="mt-3 text-6xl font-medium leading-none tracking-tighter">The Raven&apos;s Memory</h1>
        <p className="mt-6 max-w-lg text-base leading-relaxed text-secondary">
          Cache-first navigation over an authoritative durable archive.
        </p>
      </section>
      <form onSubmit={submit} className="grid place-content-center px-6 py-12">
        <div className="grid w-full max-w-sm gap-4">
          <div className="flex items-center gap-2 text-base font-semibold">
            <KeyRound className="h-4 w-4 text-accent" />
            {bootstrap ? "Bootstrap the archive" : "Operator sign in"}
          </div>
          <label className="grid gap-1.5 text-[0.7rem] font-medium uppercase tracking-wider text-secondary">
            Operator ID
            <Input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required />
          </label>
          <label className="grid gap-1.5 text-[0.7rem] font-medium uppercase tracking-wider text-secondary">
            Passphrase
            <Input
              type="password"
              minLength={12}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={bootstrap ? "new-password" : "current-password"}
              required
            />
          </label>
          {error && <ErrorLine>{error}</ErrorLine>}
          <Button type="submit" disabled={busy}>{busy ? "Verifying…" : bootstrap ? "Establish admin" : "Enter workspace"}</Button>
          <Button type="button" variant="outline" onClick={() => setBootstrap((value) => !value)}>
            {bootstrap ? "Use existing account" : "First deployment? Bootstrap admin"}
          </Button>
        </div>
      </form>
    </main>
  );
}

function Workspace({
  actor,
  globalError,
  setGlobalError,
  logout,
}: {
  actor: Actor;
  globalError: string;
  setGlobalError: (error: string) => void;
  logout: () => Promise<void>;
}) {
  const qc = useQueryClient();
  const [view, setViewState] = useState<View>(storedView);
  const [rail, setRail] = useState(false);
  const [inspector, setInspector] = useState(true);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(storedConversation);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const createConversation = useCreateConversation();

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => clearTimeout(handle);
  }, [query]);

  const conversationsQuery = useConversations(debouncedQuery);
  const conversations = conversationsQuery.data || [];
  const { status: conversationStreamStatus } = useConversationEvents({ conversationId: activeConversationId });
  const detailQuery = useConversation(activeConversationId, conversationStreamStatus === "live");
  const detail = detailQuery.data || null;
  const runs = detail?.runs || [];

  useEffect(() => {
    if (!detail) return;
    if (selectedRunId && detail.runs.some((run) => run.id === selectedRunId)) return;
    setSelectedRunId(detail.runs.at(-1)?.id || null);
  }, [detail, selectedRunId]);

  const selectedRun = runs.find((run) => run.id === selectedRunId) || null;
  const runDetailQuery = useRunDetail(selectedRunId);
  const runDetail = runDetailQuery.data || null;
  const streamActive = Boolean(selectedRun && !isTerminalRun(selectedRun.state));
  const [phase, setPhase] = useState<RunPhase | null>(null);
  const { status: runStreamStatus, lastPhase } = useRunEvents({
    runId: streamActive ? selectedRunId : null,
    onHeartbeat: setPhase,
    onClose: () => setPhase(null),
  });

  function setView(next: View) {
    setViewState(next);
    setRail(false);
    if (typeof window !== "undefined") window.localStorage.setItem("munin.activeView", next);
  }

  async function selectConversation(id: string) {
    setActiveConversationId(id);
    setSelectedRunId(null);
    setView("conversations");
    if (typeof window !== "undefined") window.localStorage.setItem("munin.activeConversationId", id);
  }

  function prefetchConversation(id: string) {
    void qc.prefetchQuery({
      queryKey: ["conversation", id],
      queryFn: () => productionApi.conversation(id),
      staleTime: 30_000,
    });
  }

  async function create() {
    setGlobalError("");
    try {
      const conversation = await createConversation.mutateAsync(undefined);
      qc.setQueryData<ConversationDetail>(["conversation", conversation.id], {
        conversation,
        messages: [],
        runs: [],
      });
      await selectConversation(conversation.id);
    } catch (cause) {
      const message = messageFromError(cause);
      setGlobalError(message);
      toast.error(message);
    }
  }

  const selectedListItem = conversations.find((item) => item.id === activeConversationId) || null;
  const serviceError = globalError || messageFromQueryError(conversationsQuery.error) || messageFromQueryError(detailQuery.error);

  let center: ReactNode;
  switch (view) {
    case "command":
      center = <CommandCenter conversations={conversations} create={create} select={selectConversation} />;
      break;
    case "conversations":
      center = (
        <ConversationView
          actor={actor}
          detail={detail}
          fallbackConversation={selectedListItem}
          loading={detailQuery.isPending || detailQuery.isFetching}
          error={messageFromQueryError(detailQuery.error)}
          retry={() => void detailQuery.refetch()}
          selectedRunId={selectedRunId}
          selectRun={setSelectedRunId}
          runDetail={runDetail}
          runStreamStatus={runStreamStatus}
          runPhase={lastPhase || phase}
          create={create}
          onArchived={() => {
            setActiveConversationId(null);
            setSelectedRunId(null);
            if (typeof window !== "undefined") window.localStorage.removeItem("munin.activeConversationId");
          }}
          setGlobalError={setGlobalError}
        />
      );
      break;
    case "agents":
      center = <AgentsView />;
      break;
    case "operations":
      center = <OperationsView conversations={conversations} select={selectConversation} />;
      break;
    case "memory":
      center = <MemoryView conversations={conversations} />;
      break;
    case "graph":
      center = <GraphView conversations={conversations} select={selectConversation} />;
      break;
    case "artifacts":
      center = <ArtifactsView detail={runDetail} />;
      break;
    case "hitl":
      center = <HitlView detail={runDetail} />;
      break;
    case "settings":
      center = <SettingsView />;
      break;
  }

  return (
    <main className="grid h-screen grid-cols-1 grid-rows-[52px_minmax(0,1fr)] overflow-hidden bg-bg text-body lg:grid-cols-[240px_minmax(0,1fr)_340px]">
      <header className="flex items-center gap-2 border-b border-border bg-surface px-3 lg:col-span-3">
        <Button variant="ghost" size="icon" onClick={() => setRail((value) => !value)} aria-label="Toggle navigation" className="lg:hidden">
          <Menu className="h-4 w-4" />
        </Button>
        <img src="/raven-mark.png" width={28} height={28} alt="" className="rounded-sm" />
        <div className="flex min-w-[86px] flex-col leading-tight">
          <b className="text-xs font-semibold tracking-wider">MUNIN</b>
          <small className="text-[0.65rem] text-muted">{NAV.find((item) => item.id === view)?.label}</small>
        </div>
        <div className="hidden min-w-0 items-center gap-1 text-xs text-muted md:flex">
          Intelligence flight deck <ChevronRight className="h-3 w-3" />
          <span className="truncate">{detail?.conversation.title || selectedListItem?.title || "Archive"}</span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {streamActive && <HeartbeatBar status={runStreamStatus} phase={lastPhase || phase} compact />}
          <span className={cn("hidden items-center gap-1 text-[0.7rem] sm:flex", serviceError ? "text-danger" : "text-secondary")}>
            <span className={cn("h-2 w-2 rounded-full", serviceError ? "bg-danger" : "bg-success")} />
            {serviceError ? "Service degraded · cache active" : "Turso authority online · local cache ready"}
          </span>
          <Button variant="ghost" size="icon" onClick={() => setInspector((value) => !value)} aria-label="Toggle inspector">
            <PanelRight className="h-4 w-4" />
          </Button>
          <Button variant="outline" size="sm" onClick={() => void logout()}>
            <UserRound className="h-3.5 w-3.5" /> {actor.username} <LogOut className="h-3.5 w-3.5" />
          </Button>
        </div>
      </header>

      <aside className={cn(
        "min-h-0 flex-col gap-3 border-r border-border bg-surface p-3",
        "hidden lg:flex",
        rail && "!flex absolute inset-y-[52px] left-0 z-30 w-64 shadow-2xl lg:relative lg:z-auto lg:w-auto lg:shadow-none",
      )}>
        <Button onClick={() => void create()} disabled={createConversation.isPending} className="w-full justify-center">
          {createConversation.isPending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
          Ask Munin
        </Button>
        <nav className="flex flex-col gap-0.5 border-b border-border pb-3">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setView(id)}
              className={cn(
                "flex items-center gap-2 rounded px-2 py-1.5 text-xs text-secondary transition-colors",
                view === id ? "border-l-2 border-accent bg-active pl-[6px] text-body" : "hover:bg-raised hover:text-body",
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
            <Button variant="ghost" size="icon" onClick={() => void conversationsQuery.refetch()} aria-label="Refresh operations" className="h-5 w-5">
              <RefreshCw className={cn("h-3 w-3", conversationsQuery.isFetching && "animate-spin")} />
            </Button>
          </div>
          <div className="flex items-center gap-1.5 rounded border border-border bg-bg px-2">
            <Search className="h-3.5 w-3.5 text-muted" />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search durable archive" className="w-full bg-transparent py-1.5 text-xs placeholder:text-muted focus:outline-none" />
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="flex flex-col">
              {conversations.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onMouseEnter={() => prefetchConversation(item.id)}
                    onFocus={() => prefetchConversation(item.id)}
                    onClick={() => void selectConversation(item.id)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 border-b border-border/50 px-2 py-2 text-left text-xs transition-colors",
                      activeConversationId === item.id ? "bg-active text-body" : "text-secondary hover:bg-raised hover:text-body",
                    )}
                  >
                    <span className="truncate">{item.title}</span>
                    <small className="text-[0.65rem] text-muted">{item.message_count} objects · {formatWhen(item.last_activity_at_ms)}</small>
                  </button>
                </li>
              ))}
              {!conversations.length && conversationsQuery.isFetching && <SidebarLoading />}
              {!conversations.length && !conversationsQuery.isFetching && (
                <li className="p-3 text-xs leading-relaxed text-muted">No matching operations. Existing cached chats remain available when you clear the search.</li>
              )}
            </ul>
          </ScrollArea>
        </div>
      </aside>

      <section className="min-h-0 min-w-0 overflow-hidden">{center}</section>

      {inspector && (
        <Inspector
          conversation={detail?.conversation || selectedListItem}
          run={selectedRun}
          detail={runDetail}
          streamStatus={runStreamStatus}
          phase={lastPhase || phase}
          error={serviceError}
        />
      )}
      <FloatingWindowsHost />
    </main>
  );
}

function CommandCenter({
  conversations,
  create,
  select,
}: {
  conversations: Conversation[];
  create: () => Promise<void>;
  select: (id: string) => Promise<void>;
}) {
  return (
    <ScrollArea className="h-full">
      <div className="mx-auto max-w-5xl space-y-8 px-6 py-10 lg:px-10">
        <section className="flex flex-col gap-4 border-b border-border pb-8 md:flex-row md:items-end md:justify-between">
          <div className="max-w-2xl space-y-2">
            <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">OPERATOR BRIEFING</p>
            <h1 className="text-4xl font-medium leading-none tracking-tight md:text-5xl">Continue without waiting on the tunnel.</h1>
            <p className="max-w-xl leading-relaxed text-secondary">Cached read models render immediately; Turso refreshes them in the background.</p>
          </div>
          <Button onClick={() => void create()} size="lg"><Sparkles className="h-4 w-4" /> Ask Munin</Button>
        </section>
        <section className="grid grid-cols-2 overflow-hidden rounded-lg border border-border md:grid-cols-4">
          <Metric label="Operations" value={String(conversations.length)} note="cached + authoritative" />
          <Metric label="Active" value={String(conversations.filter((item) => item.status === "active").length)} note="server status" />
          <Metric label="Messages" value={String(conversations.reduce((sum, item) => sum + item.message_count, 0))} note="visible archive" />
          <Metric label="Cache" value="7d" note="IndexedDB retention" />
        </section>
        <section className="overflow-hidden rounded-lg border border-border">
          <header className="flex items-center justify-between border-b border-border p-4">
            <div><p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">CONTINUITY</p><h2 className="text-lg font-medium">Resume an operation</h2></div>
            <Button variant="outline" size="sm" onClick={() => void create()}><Plus className="h-3.5 w-3.5" /> New</Button>
          </header>
          {conversations.length ? conversations.slice(0, 20).map((item) => (
            <button key={item.id} type="button" onClick={() => void select(item.id)} className="grid w-full grid-cols-[minmax(0,1fr)_100px_130px_18px] items-center gap-4 border-b border-border/60 px-4 py-3 text-left text-xs text-secondary transition-colors hover:bg-raised hover:text-body">
              <span className="truncate text-body">{item.title}</span><span>{item.message_count} objects</span><span>{formatWhen(item.last_activity_at_ms)}</span><ChevronRight className="h-4 w-4 text-muted" />
            </button>
          )) : <EmptyPanel title="The archive is quiet" detail="Create an operation to persist its first turn." />}
        </section>
      </div>
    </ScrollArea>
  );
}

function ConversationView({
  actor,
  detail,
  fallbackConversation,
  loading,
  error,
  retry,
  selectedRunId,
  selectRun,
  runDetail,
  runStreamStatus,
  runPhase,
  create,
  onArchived,
  setGlobalError,
}: {
  actor: Actor;
  detail: ConversationDetail | null;
  fallbackConversation: Conversation | null;
  loading: boolean;
  error: string;
  retry: () => void;
  selectedRunId: string | null;
  selectRun: (id: string | null) => void;
  runDetail: RunDetail | null;
  runStreamStatus: ReturnType<typeof useRunEvents>["status"];
  runPhase: RunPhase | null;
  create: () => Promise<void>;
  onArchived: () => void;
  setGlobalError: (value: string) => void;
}) {
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ComposerMode>("turn");
  const send = useSendTurn();
  const archive = useArchiveConversation();
  const notesQuery = useNotes(detail?.conversation.id || null);
  const postNote = usePostNote(detail?.conversation.id || null);
  const presenceQuery = usePresence(detail?.conversation.id || null);
  const presenceHeartbeat = usePresenceHeartbeat(detail?.conversation.id || null);
  const timelineRef = useRef<HTMLDivElement | null>(null);

  const conversationId = detail?.conversation.id || null;
  const draftKey = conversationId ? `munin.draft.${conversationId}` : "";
  const activeRun = detail?.runs.find((run) => !isTerminalRun(run.state)) || null;

  useEffect(() => {
    if (!draftKey || typeof window === "undefined") {
      setInput("");
      return;
    }
    setInput(window.localStorage.getItem(draftKey) || "");
  }, [draftKey]);

  useEffect(() => {
    if (!draftKey || typeof window === "undefined") return;
    const handle = setTimeout(() => window.localStorage.setItem(draftKey, input), 200);
    return () => clearTimeout(handle);
  }, [draftKey, input]);

  useEffect(() => {
    if (activeRun && mode === "turn") setMode("guidance");
    if (!activeRun && mode === "guidance") setMode("turn");
  }, [activeRun, mode]);

  useEffect(() => {
    const element = timelineRef.current;
    if (!element) return;
    if (element.scrollHeight - element.scrollTop - element.clientHeight < 240) element.scrollTop = element.scrollHeight;
  }, [detail?.messages.length, runDetail?.events.length]);

  async function submit() {
    if (!detail || !input.trim()) return;
    const body = input.trim();
    try {
      if (mode === "note") {
        await postNote.mutateAsync(body);
      } else if (mode === "guidance") {
        if (!activeRun) throw new Error("No active run to guide");
        await productionApi.guideRun(activeRun.id, body);
        toast.success("Guidance queued for the next model iteration");
      } else {
        const result = await send.mutateAsync({
          conversationId: detail.conversation.id,
          content: body,
          idempotencyKey: crypto.randomUUID(),
        });
        selectRun(result.run.id);
      }
      setInput("");
      if (typeof window !== "undefined") window.localStorage.removeItem(draftKey);
    } catch (cause) {
      const message = messageFromError(cause);
      setGlobalError(message);
      toast.error(message);
    }
  }

  if (!detail) {
    if (loading || fallbackConversation) {
      return <ConversationLoading title={fallbackConversation?.title || "Loading operation"} error={error} retry={retry} />;
    }
    return (
      <div className="grid h-full place-content-center px-8 text-center">
        <div className="flex max-w-md flex-col items-center gap-3"><MessageSquare className="h-10 w-10 text-accent" /><h2 className="text-xl font-medium">Choose a conversation</h2><p className="text-sm leading-relaxed text-secondary">Select a cached operation from the left rail or start a new one.</p><Button onClick={() => void create()}><Plus className="h-4 w-4" /> Start operation</Button></div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-start justify-between gap-4 border-b border-border bg-surface px-6 py-4">
        <div className="min-w-0 space-y-1">
          <p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">CONVERSATION / {detail.conversation.status}</p>
          <h1 className="truncate text-2xl font-medium tracking-tight md:text-3xl">{detail.conversation.title}</h1>
          <span className="text-[0.7rem] text-muted">{detail.messages.length} timeline objects · cached locally · Turso authoritative {loading && "· refreshing…"}</span>
        </div>
        <div className="flex items-center gap-3">
          <PresenceRow presence={presenceQuery.data || []} />
          <CollaboratorManager conversationId={detail.conversation.id} actor={actor} />
          <Button variant="outline" size="sm" disabled={archive.isPending} onClick={() => archive.mutate({ id: detail.conversation.id, version: detail.conversation.version, archived: true }, { onSuccess: onArchived })}>
            <Archive className="h-3.5 w-3.5" /> Archive
          </Button>
        </div>
      </header>
      {error && <div className="flex items-center justify-between border-b border-warning/30 bg-warning/10 px-6 py-2 text-xs text-warning"><span>Remote refresh failed; showing the last local snapshot. {error}</span><Button variant="outline" size="sm" onClick={retry}><RefreshCw className="h-3 w-3" /> Retry</Button></div>}
      <div ref={timelineRef} className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-2 px-4 py-6 md:px-8">
          {!detail.messages.length && <EmptyPanel title="Ready for the first turn" detail="Write an objective below. It appears optimistically before the remote request completes." />}
          {detail.messages.map((message) => {
            if (message.kind === "operator_guidance") return <GuidanceMessageBlock key={message.id} message={message as GuidanceMessage} />;
            const attachedRun = detail.runs.find((run) => run.id === message.run_id);
            const isMunin = message.kind.includes("assistant") || message.kind.includes("munin");
            const selected = attachedRun?.id === selectedRunId;
            return (
              <Message key={message.id} role={isMunin ? "munin" : "operator"} state={message.status} runIdSuffix={attachedRun ? shortId(attachedRun.id) : undefined} onSelectRun={attachedRun ? () => selectRun(attachedRun.id) : undefined}>
                {message.content ? <MessageText>{message.content}</MessageText> : isMunin ? <p className="text-xs italic text-muted">Munin is working…</p> : null}
                {isMunin && attachedRun && selected && runDetail && <RunTrace detail={runDetail} streamStatus={runStreamStatus} phase={runPhase} />}
                {isMunin && attachedRun && !selected && <button type="button" onClick={() => selectRun(attachedRun.id)} className="self-start font-mono text-[0.7rem] text-muted hover:text-info">show live trace for this run →</button>}
              </Message>
            );
          })}
          {(notesQuery.data || []).map((note) => <NoteBlock key={note.id} note={note} />)}
        </div>
      </div>
      <footer className="border-t border-border bg-surface px-4 py-4 md:px-8">
        <div className="mx-auto max-w-4xl space-y-2">
          <ComposerModeToggle mode={mode} onModeChange={setMode} runActive={Boolean(activeRun)} />
          <div className="flex items-end gap-2">
            <Textarea rows={3} value={input} onChange={(event) => { setInput(event.target.value); presenceHeartbeat.onKeystroke(); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder={mode === "note" ? "Add a note that is never sent to the model…" : mode === "guidance" ? "Guide the active run…" : "State the objective, evidence, scope, or guidance…"} className="flex-1" />
            <Button onClick={() => void submit()} disabled={!input.trim() || send.isPending || postNote.isPending}>
              {send.isPending || postNote.isPending ? "Persisting…" : mode === "note" ? "Post note" : mode === "guidance" ? "Queue guidance" : "Commit turn"}<Send className="h-4 w-4" />
            </Button>
          </div>
          <small className="block text-[0.65rem] text-muted">Draft auto-saves locally. The visible conversation remains cached while Turso refreshes.</small>
        </div>
      </footer>
    </div>
  );
}

function RunTrace({ detail, streamStatus, phase }: { detail: RunDetail; streamStatus: ReturnType<typeof useRunEvents>["status"]; phase: RunPhase | null }) {
  const subagentNames = new Set(detail.subagents.map((item) => item.agent_name || item.profile_id));
  const reasoning = detail.reasoning.filter((item) => !subagentNames.has(item.agent_name));
  const tools = detail.tools.filter((item) => !subagentNames.has(item.agent_name));
  const running = !isTerminalRun(detail.run.state);
  return (
    <div className="min-w-0 space-y-2">
      {reasoning.map((event) => <ThoughtBlock key={event.id} content={event.content} agent={event.agent_name} kind={event.kind} step={event.step} running={running && !event.persisted} />)}
      {tools.map((tool) => <ToolBlock key={tool.id} tool={tool} />)}
      {detail.subagents.map((subagent) => <SubagentCard key={subagent.id} subagent={subagent} reasoning={detail.reasoning} tools={detail.tools} runId={detail.run.id} />)}
      {detail.human_requests.filter((request) => request.state === "pending").map((request) => <HitlRequest key={request.id} request={request} />)}
      {!!detail.artifacts.length && <div className="flex flex-wrap gap-2">{detail.artifacts.map((artifact) => <ArtifactChip key={artifact.id} artifact={artifact} />)}</div>}
      {running && <HeartbeatBar status={streamStatus} phase={phase} />}
    </div>
  );
}

function AgentsView() {
  const query = useAgents();
  return <RegistryPage eyebrow="AGENTS" title="Agent roster" detail="Profiles loaded from the production API, not a placeholder.">{query.isPending ? <PageLoading /> : query.error ? <ErrorLine>{messageFromQueryError(query.error)}</ErrorLine> : <div className="grid gap-3 md:grid-cols-2">{(query.data || []).map((agent) => <article key={agent.id} className="rounded-lg border border-border bg-surface p-4"><div className="flex items-center justify-between gap-2"><h3 className="font-mono text-sm font-semibold">{agent.id}</h3><Badge>{agent.risk}</Badge></div><p className="mt-2 text-sm text-secondary">{agent.objective || agent.description}</p><div className="mt-3 flex flex-wrap gap-1">{agent.tools.slice(0, 12).map((tool) => <span key={tool} className="rounded bg-raised px-2 py-1 font-mono text-[0.65rem] text-muted">{tool}</span>)}</div></article>)}</div>}</RegistryPage>;
}

function OperationsView({ conversations, select }: { conversations: Conversation[]; select: (id: string) => Promise<void> }) {
  return <RegistryPage eyebrow="OPERATIONS" title="Durable operation ledger" detail="Each row is clickable and opens its actual conversation."><div className="overflow-hidden rounded-lg border border-border">{conversations.map((item) => <button key={item.id} type="button" onClick={() => void select(item.id)} className="grid w-full grid-cols-[minmax(0,1fr)_90px_100px_18px] items-center gap-4 border-b border-border/60 px-4 py-3 text-left text-xs hover:bg-raised"><span className="truncate text-body">{item.title}</span><Badge variant={stateBadgeVariant(item.status)}>{item.status}</Badge><span className="text-muted">{item.message_count} objects</span><ChevronRight className="h-4 w-4 text-muted" /></button>)}{!conversations.length && <EmptyPanel title="No operations" detail="The server and local cache returned no operations." />}</div></RegistryPage>;
}

function MemoryView({ conversations }: { conversations: Conversation[] }) {
  const qc = useQueryClient();
  const [info, setInfo] = useState<{ savedAt: number; queryCount: number } | null>(null);
  useEffect(() => { void muninQueryCacheInfo().then(setInfo); }, []);
  return <RegistryPage eyebrow="MEMORY" title="Local acceleration + durable authority" detail="Conversation bodies are mirrored into IndexedDB for instant navigation; Turso remains the source of truth."><div className="grid gap-4 md:grid-cols-3"><MetricCard icon={<Database className="h-5 w-5" />} label="Cached query records" value={String(info?.queryCount || 0)} note={info ? `saved ${formatWhen(info.savedAt)}` : "no snapshot yet"} /><MetricCard icon={<MessageSquare className="h-5 w-5" />} label="Visible operations" value={String(conversations.length)} note="from current read model" /><MetricCard icon={<BrainCircuit className="h-5 w-5" />} label="Retention" value="7 days" note="refreshes on successful reads" /></div><div className="mt-6 rounded-lg border border-warning/30 bg-warning/5 p-4"><h3 className="font-medium">Cache controls</h3><p className="mt-1 text-sm text-secondary">Clearing local acceleration does not delete Turso data. The next read repopulates it.</p><Button className="mt-3" variant="outline" onClick={() => void clearMuninQueryCache().then(() => { qc.clear(); setInfo(null); toast.success("Local cache cleared"); })}><Trash2 className="h-4 w-4" /> Clear local cache</Button></div></RegistryPage>;
}

function GraphView({ conversations, select }: { conversations: Conversation[]; select: (id: string) => Promise<void> }) {
  return <RegistryPage eyebrow="INTELLIGENCE GRAPH" title="Operation topology" detail="The production boundary does not expose Hugin graph nodes yet, so this view honestly renders the available operation topology instead of recycling a generic placeholder."><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{conversations.map((item) => <button key={item.id} type="button" onClick={() => void select(item.id)} className="rounded-lg border border-border bg-surface p-4 text-left transition hover:border-accent/50 hover:bg-raised"><Network className="h-5 w-5 text-accent" /><h3 className="mt-3 truncate font-medium">{item.title}</h3><p className="mt-1 text-xs text-muted">{item.message_count} timeline objects · {item.status}</p></button>)}</div></RegistryPage>;
}

function ArtifactsView({ detail }: { detail: RunDetail | null }) {
  return <RegistryPage eyebrow="ARTIFACTS" title="Evidence from the selected run" detail="Select a run in Conversations to inspect its generated files.">{detail?.artifacts.length ? <div className="flex flex-wrap gap-3">{detail.artifacts.map((artifact) => <ArtifactChip key={artifact.id} artifact={artifact} />)}</div> : <EmptyPanel title="No selected artifacts" detail="Open a conversation and select a run with generated evidence." />}</RegistryPage>;
}

function HitlView({ detail }: { detail: RunDetail | null }) {
  const pending = detail?.human_requests.filter((request) => request.state === "pending") || [];
  return <RegistryPage eyebrow="HITL INBOX" title="Human decision boundary" detail="Pending decisions for the currently selected run.">{pending.length ? <div className="space-y-3">{pending.map((request) => <HitlRequest key={request.id} request={request} />)}</div> : <EmptyPanel title="No pending decisions" detail="Select a run or wait for Munin to request approval." />}</RegistryPage>;
}

function SettingsView() {
  const qc = useQueryClient();
  const profiles = useProviderProfiles();
  const [form, setForm] = useState({ label: "", provider: "openai-compatible", base_url: "", model: "", key: "" });
  const [saving, setSaving] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await productionApi.saveProviderProfile({ ...form, uses: ["chat"], key: form.key });
      setForm({ label: "", provider: "openai-compatible", base_url: "", model: "", key: "" });
      await qc.invalidateQueries({ queryKey: ["provider-profiles"] });
      toast.success("Provider profile saved");
    } catch (cause) { toast.error(messageFromError(cause)); } finally { setSaving(false); }
  }
  return <RegistryPage eyebrow="SETTINGS" title="Provider and policy controls" detail="This is a live provider-profile view. Secret keys are sent to the authenticated backend and are never cached by the query persister."><div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]"><section className="overflow-hidden rounded-lg border border-border"><header className="border-b border-border p-4 font-medium">Configured profiles</header>{profiles.isPending ? <PageLoading /> : (profiles.data || []).map((profile) => <div key={profile.id} className="flex items-center justify-between gap-4 border-b border-border/60 p-4"><div className="min-w-0"><div className="flex items-center gap-2"><b className="truncate">{profile.label}</b>{profile.active && <Badge variant="success">active</Badge>}</div><p className="mt-1 truncate font-mono text-[0.7rem] text-muted">{profile.provider} · {profile.model} · {profile.base_url}</p></div>{!profile.active && <Button variant="outline" size="sm" onClick={() => void productionApi.providerProfileAction(profile.id, "activate").then(() => qc.invalidateQueries({ queryKey: ["provider-profiles"] }))}>Activate</Button>}</div>)}{!profiles.isPending && !(profiles.data || []).length && <EmptyPanel title="No provider profiles" detail="The environment fallback can still serve runs; add a profile to manage it here." />}</section><form onSubmit={save} className="space-y-3 rounded-lg border border-border bg-surface p-4"><h3 className="font-medium">Add provider profile</h3><Input placeholder="Label" value={form.label} onChange={(event) => setForm({ ...form, label: event.target.value })} required /><Input placeholder="Base URL (https://…/v1)" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required /><Input placeholder="Model" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} required /><Input type="password" placeholder="API key" value={form.key} onChange={(event) => setForm({ ...form, key: event.target.value })} required /><Button type="submit" disabled={saving}>{saving ? "Saving…" : "Save encrypted profile"}</Button></form></div></RegistryPage>;
}

function Inspector({ conversation, run, detail, streamStatus, phase, error }: { conversation: Conversation | null; run: Run | null; detail: RunDetail | null; streamStatus: ReturnType<typeof useRunEvents>["status"]; phase: RunPhase | null; error: string }) {
  return <aside className="hidden h-full min-h-0 overflow-auto border-l border-border bg-surface p-4 lg:block"><header className="border-b border-border pb-4"><p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">CONTEXT INSPECTOR</p><h2 className="text-base font-medium">{run ? "Run state" : "System boundary"}</h2></header><dl className="mt-4 grid grid-cols-[90px_minmax(0,1fr)] gap-x-4 gap-y-3 text-xs"><dt className="text-muted">Conversation</dt><dd className="break-words text-body">{conversation?.title || "No selection"}</dd><dt className="text-muted">Storage</dt><dd>Turso + IndexedDB cache</dd><dt className="text-muted">Service</dt><dd className={error ? "text-warning" : "text-success"}>{error ? "Degraded; cached snapshot visible" : "Available"}</dd>{run && <><dt className="text-muted">State</dt><dd><Badge variant={stateBadgeVariant(run.state)}>{run.state}</Badge></dd><dt className="text-muted">Run</dt><dd className="break-all font-mono text-[0.65rem]">{run.id}</dd><dt className="text-muted">Attempt</dt><dd>{run.attempt}</dd><dt className="text-muted">Reasoning</dt><dd>{detail?.reasoning.length || 0}</dd><dt className="text-muted">Tools</dt><dd>{detail?.tools.length || 0}</dd></>}</dl>{run && <div className="mt-4"><HeartbeatBar status={streamStatus} phase={phase} /></div>}</aside>;
}

function RegistryPage({ eyebrow, title, detail, children }: { eyebrow: string; title: string; detail: string; children: ReactNode }) {
  return <ScrollArea className="h-full"><div className="mx-auto max-w-6xl space-y-6 px-6 py-10 lg:px-10"><section className="flex items-start gap-4 border-b border-border pb-6"><div><p className="font-mono text-[0.65rem] uppercase tracking-widest text-muted">{eyebrow}</p><h1 className="mt-1 text-3xl font-medium tracking-tight">{title}</h1><p className="mt-2 max-w-3xl leading-relaxed text-secondary">{detail}</p></div></section>{children}</div></ScrollArea>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) { return <article className="border-b border-r border-border p-4 last:border-r-0 md:border-b-0"><small className="uppercase tracking-wider text-muted">{label}</small><strong className="mt-1 block text-2xl font-medium">{value}</strong><span className="text-[0.65rem] text-muted">{note}</span></article>; }
function MetricCard({ icon, label, value, note }: { icon: ReactNode; label: string; value: string; note: string }) { return <article className="rounded-lg border border-border bg-surface p-4"><div className="text-accent">{icon}</div><small className="mt-3 block uppercase tracking-wider text-muted">{label}</small><strong className="mt-1 block text-2xl">{value}</strong><span className="text-xs text-muted">{note}</span></article>; }
function EmptyPanel({ title, detail }: { title: string; detail: string }) { return <div className="grid place-items-center px-8 py-14 text-center"><div><h3 className="font-medium">{title}</h3><p className="mt-1 max-w-md text-sm text-secondary">{detail}</p></div></div>; }
function PageLoading() { return <div className="flex items-center gap-2 p-6 text-sm text-muted"><LoaderCircle className="h-4 w-4 animate-spin" /> Loading authoritative data…</div>; }
function SidebarLoading() { return <li className="flex items-center gap-2 p-3 text-xs text-muted"><LoaderCircle className="h-3.5 w-3.5 animate-spin" /> Loading operations without clearing cache…</li>; }
function ErrorLine({ children }: { children: ReactNode }) { return <p className="flex items-center gap-1.5 text-xs text-danger"><TriangleAlert className="h-3.5 w-3.5" /> {children}</p>; }
function ConversationLoading({ title, error, retry }: { title: string; error: string; retry: () => void }) { return <div className="grid h-full place-content-center px-8 text-center"><div className="flex max-w-md flex-col items-center gap-3"><LoaderCircle className="h-8 w-8 animate-spin text-accent" /><h2 className="text-xl font-medium">{title}</h2><p className="text-sm text-secondary">Restoring the cached timeline and refreshing it from Turso. The selected chat is not being discarded.</p>{error && <><ErrorLine>{error}</ErrorLine><Button variant="outline" onClick={retry}><RefreshCw className="h-4 w-4" /> Retry</Button></>}</div></div>; }
function messageFromQueryError(error: unknown): string { return error ? messageFromError(error) : ""; }
