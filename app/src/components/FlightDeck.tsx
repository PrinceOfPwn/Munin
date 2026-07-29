"use client";

import { type FormEvent, useEffect, useRef, useState } from "react";
import { Activity, Archive, Bot, BrainCircuit, ChevronRight, Command, FileBox, KeyRound, LogOut, Menu, MessageSquare, Network, PanelRight, Plus, Search, Send, Settings2, ShieldCheck, Sparkles, TriangleAlert, UserRound } from "lucide-react";
import { cn } from "@/lib/utils";
import { type Actor, type Conversation, type ConversationDetail, productionApi, type Run, type RunDetail } from "@/lib/production-api";

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
    productionApi.session().then(setActor).catch(() => setActor(null)).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="flight-loader">Opening the Raven&apos;s Memory…</div>;
  if (!actor) return <Login error={error} setError={setError} authenticated={setActor} />;
  return <Desk actor={actor} error={error} setError={setError} logout={() => {
    void productionApi.logout().catch((cause) => setError(message(cause)));
    setActor(null);
  }} />;
}

function Login({ error, setError, authenticated }: { error: string; setError: (error: string) => void; authenticated: (actor: Actor) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [bootstrap, setBootstrap] = useState(false);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      if (bootstrap) await productionApi.bootstrap(username, password);
      await productionApi.login(username, password);
      authenticated(await productionApi.session());
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  return <main className="archive-login">
    <section className="archive-login__masthead">
      <img src="/raven-mark.png" width={76} height={76} alt="Munin raven mark" />
      <p className="eyebrow">MUNIN / OPERATOR ARCHIVE</p><h1>The Raven&apos;s Memory</h1>
      <p>Every authorized operation, preserved with provenance.</p>
      <span className="service-status"><i /> Authenticated production boundary</span>
    </section>
    <form className="archive-login__form" onSubmit={submit}>
      <div className="form-heading"><KeyRound size={17} />{bootstrap ? "Bootstrap the archive" : "Operator sign in"}</div>
      <label>Operator ID<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
      <label>Passphrase<input type="password" minLength={12} autoComplete={bootstrap ? "new-password" : "current-password"} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
      {error && <p className="form-error"><TriangleAlert size={15} /> {error}</p>}
      <button className="signal-button" disabled={busy}>{busy ? "Verifying…" : bootstrap ? "Establish admin" : "Enter workspace"}</button>
      <button type="button" className="quiet-button" onClick={() => setBootstrap(!bootstrap)}>{bootstrap ? "Use existing account" : "First deployment? Bootstrap admin"}</button>
      <small>Session tokens stay HttpOnly. Provider and MCP credentials never enter browser storage.</small>
    </form>
  </main>;
}

function Desk({ actor, error, setError, logout }: { actor: Actor; error: string; setError: (error: string) => void; logout: () => void }) {
  const [view, setView] = useState<View>("command");
  const [rail, setRail] = useState(false);
  const [inspector, setInspector] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [query, setQuery] = useState("");
  const restoredActive = useRef(false);
  async function refresh(search = query) {
    try { setConversations(await productionApi.conversations(search)); setError(""); } catch (cause) { setError(message(cause)); }
  }
  useEffect(() => { void refresh(""); }, []); // initial archive hydrate
  useEffect(() => {
    if (restoredActive.current || !conversations.length || typeof window === "undefined") return;
    restoredActive.current = true;
    const remembered = window.localStorage.getItem("munin.activeConversationId");
    if (remembered && conversations.some((conversation) => conversation.id === remembered)) void select(remembered);
  }, [conversations]); // restore only an explicitly remembered server-side conversation
  useEffect(() => {
    if (!selectedRun) { setRunDetail(null); return; }
    productionApi.runDetail(selectedRun.id).then(setRunDetail).catch((cause) => setError(message(cause)));
  }, [selectedRun?.id]);
  useEffect(() => {
    if (!selectedRun || ["completed", "failed", "interrupted", "cancelled"].includes(selectedRun.state)) return;
    let stopped = false;
    const poll = async () => {
      try {
        const latest = await productionApi.runDetail(selectedRun.id);
        if (stopped) return;
        setRunDetail(latest); setSelectedRun(latest.run);
        if (detail?.conversation.id) setDetail(await productionApi.conversation(detail.conversation.id));
      } catch (cause) { if (!stopped) setError(message(cause)); }
    };
    const timer = window.setInterval(() => { void poll(); }, 2_000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [selectedRun?.id, selectedRun?.state, detail?.conversation.id]);
  async function select(id: string) {
    try {
      const next = await productionApi.conversation(id);
      window.localStorage.setItem("munin.activeConversationId", id);
      setDetail(next); setSelectedRun(next.runs.at(-1) || null); setView("conversations"); setRail(false);
    } catch (cause) { setError(message(cause)); }
  }
  async function create() {
    try { const conversation = await productionApi.createConversation(); await refresh(); await select(conversation.id); } catch (cause) { setError(message(cause)); }
  }
  async function cancelRun() {
    if (!selectedRun) return;
    try { setSelectedRun(await productionApi.cancelRun(selectedRun.id)); if (detail) setDetail(await productionApi.conversation(detail.conversation.id)); await refresh(); } catch (cause) { setError(message(cause)); }
  }
  async function retryRun() {
    if (!selectedRun) return;
    try { setSelectedRun(await productionApi.retryRun(selectedRun.id)); if (detail) setDetail(await productionApi.conversation(detail.conversation.id)); await refresh(); } catch (cause) { setError(message(cause)); }
  }
  async function archiveConversation() {
    if (!detail) return;
    try {
      await productionApi.archiveConversation(detail.conversation.id, detail.conversation.version, true);
      setDetail(null); setSelectedRun(null); setView("command"); await refresh();
    } catch (cause) { setError(message(cause)); }
  }
  const center = view === "command" ? <CommandCenter conversations={conversations} create={create} select={select} /> :
    view === "conversations" ? <ConversationView detail={detail} selectRun={setSelectedRun} refresh={refresh} update={setDetail} create={create} archive={archiveConversation} setError={setError} /> :
      <Registry view={view} conversations={conversations} select={select} />;
  return <main className="flight-deck">
    <header className="flight-header">
      <button className="icon-button" onClick={() => setRail(!rail)} aria-label="Toggle navigation"><Menu size={18} /></button>
      <img src="/raven-mark.png" width={29} height={29} alt="" /><div className="header-brand"><b>MUNIN</b><small>{NAV.find((item) => item.id === view)?.label}</small></div>
      <div className="header-crumb">Intelligence flight deck <ChevronRight size={13} /> {detail?.conversation.title || "Archive"}</div><span className="header-fill" />
      <span className={cn("connection", error && "connection--error")}><i /> {error ? "Service needs attention" : "Turso authority online"}</span>
      <button className="icon-button" onClick={() => setInspector(!inspector)} aria-label="Toggle inspector"><PanelRight size={18} /></button>
      <button className="profile" onClick={logout}><UserRound size={14} />{actor.username}<LogOut size={14} /></button>
    </header>
    <aside className={cn("flight-rail", rail && "flight-rail--open")}>
      <button className="primary-create" onClick={create}><Plus size={16} /> Ask Munin</button>
      <nav>{NAV.map(({ id, label, icon: Icon }) => <button key={id} className={cn("nav-entry", view === id && "nav-entry--active")} onClick={() => { setView(id); setRail(false); }}><Icon size={17} /><span>{label}</span></button>)}</nav>
      <section className="rail-archive"><header>Recent operations <button onClick={create} aria-label="New operation"><Plus size={14} /></button></header>
        <div className="rail-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void refresh(); }} placeholder="Search durable archive" /></div>
        {conversations.length ? conversations.slice(0, 10).map((item) => <button className={cn("conversation-row", detail?.conversation.id === item.id && "conversation-row--active")} onClick={() => void select(item.id)} key={item.id}><span>{item.title}</span><small>{formatTime(item.last_activity_at_ms)}</small></button>) : <p className="empty-rail">No operations yet.</p>}
      </section>
    </aside>
    <section className="flight-workspace">{center}</section>
    {inspector && <Inspector run={selectedRun} detail={runDetail} conversation={detail?.conversation || null} error={error} cancelRun={cancelRun} retryRun={retryRun} />}
  </main>;
}

function CommandCenter({ conversations, create, select }: { conversations: Conversation[]; create: () => void; select: (id: string) => void }) {
  return <div className="workspace-scroll"><section className="command-hero"><div><p className="eyebrow">OPERATOR BRIEFING</p><h1>What needs your attention?</h1><p>Chats, runs, evidence and approvals are one durable investigative story.</p></div><button className="signal-button" onClick={create}><Sparkles size={16} /> Ask Munin / Start an operation</button></section>
    <section className="briefing-band"><Metric icon={MessageSquare} label="Recent operations" value={String(conversations.length)} note="Server-side archive" /><Metric icon={Activity} label="Active scope" value={String(conversations.filter((item) => item.status === "active").length)} note="Durable state" /><Metric icon={ShieldCheck} label="Human decisions" value="—" note="No requests loaded" /><Metric icon={BrainCircuit} label="Memory changes" value="—" note="Provenance per run" /></section>
    <section className="data-panel"><header className="panel-heading"><div><p className="eyebrow">CONTINUITY</p><h2>Resume an operation</h2></div><button className="quiet-button" onClick={create}><Plus size={14} /> New</button></header>{conversations.length ? <div className="operation-table">{conversations.map((item) => <button key={item.id} onClick={() => select(item.id)}><span>{item.title}</span><span>{item.message_count} messages</span><span>{formatTime(item.last_activity_at_ms)}</span><ChevronRight size={15} /></button>)}</div> : <Empty title="The archive is quiet" detail="Start an operation to persist the first conversation, run, placeholder and event trail." action={<button className="signal-button" onClick={create}>Start operation</button>} />}</section>
  </div>;
}

function Metric({ icon: Icon, label, value, note }: { icon: typeof Activity; label: string; value: string; note: string }) { return <article className="metric"><Icon size={17} /><div><small>{label}</small><strong>{value}</strong><span>{note}</span></div></article>; }

function ConversationView({ detail, selectRun, refresh, update, create, archive, setError }: { detail: ConversationDetail | null; selectRun: (run: Run) => void; refresh: () => Promise<void>; update: (value: ConversationDetail) => void; create: () => void; archive: () => Promise<void>; setError: (error: string) => void }) {
  const [input, setInput] = useState(""); const [busy, setBusy] = useState(false); const pendingKey = useRef<string | null>(null);
  async function send() {
    if (!detail || !input.trim()) return;
    setBusy(true); const key = pendingKey.current || crypto.randomUUID(); pendingKey.current = key;
    try {
      const result = await productionApi.turn(detail.conversation.id, input.trim(), key);
      pendingKey.current = null; setInput(""); update(await productionApi.conversation(detail.conversation.id)); selectRun(result.run); await refresh();
    } catch (cause) { setError(message(cause)); } finally { setBusy(false); }
  }
  if (!detail) return <div className="workspace-scroll"><Empty title="Choose a conversation" detail="Select a durable operation from the archive or start a new one. Browser state is never the only copy." action={<button className="signal-button" onClick={create}><Plus size={16} /> Start operation</button>} /></div>;
  return <div className="conversation-workspace"><header className="conversation-header"><div><p className="eyebrow">CONVERSATION / {detail.conversation.status}</p><h1>{detail.conversation.title}</h1><span>{detail.messages.length} persisted timeline objects · Turso-authoritative</span></div><button className="quiet-button" onClick={() => void archive()}><Archive size={14} /> Archive</button></header>
    <section className="timeline">{detail.messages.map((item) => { const run = detail.runs.find((candidate) => candidate.id === item.run_id); const assistant = item.kind.includes("assistant"); return <article className={cn("timeline-object", assistant && "timeline-object--assistant")} key={item.id}><div className="timeline-index">{String(item.sequence).padStart(2, "0")}</div><div className="timeline-body"><header><b>{assistant ? "MUNIN" : "OPERATOR"}</b><span className={cn("state-label", `state-label--${item.status}`)}>{item.status.replaceAll("_", " ")}</span>{run && <button className="run-link" onClick={() => selectRun(run)}>run {run.id.slice(-8)}</button>}</header>{item.content ? <p>{item.content}</p> : <div className="cognition"><BrainCircuit size={17} /><div><b>{run?.state === "queued" ? "Run is queued" : "Cognition stream"}</b><p>Native provider reasoning appears only when explicitly exposed and permitted. Tool intent, observation and decision remain distinct.</p></div></div>}</div></article>; })}</section>
    <footer className="composer"><label htmlFor="guidance">Operator guidance</label><div><textarea id="guidance" rows={3} value={input} onChange={(event) => setInput(event.target.value)} placeholder="State the objective, evidence, scope, or guidance…" /><button className="signal-button" disabled={!input.trim() || busy} onClick={send}>{busy ? "Persisting…" : "Commit turn"}<Send size={15} /></button></div><small>User message, queued run, assistant placeholder and initial event commit together before processing.</small></footer>
  </div>;
}

function Registry({ view, conversations, select }: { view: View; conversations: Conversation[]; select: (id: string) => void }) {
  const catalog: Record<string, [string, string, typeof Activity]> = { agents: ["Agent roster and command chain", "Profiles, leases, budgets and hierarchy attach to durable runs. No active roster has been loaded.", Bot], operations: ["Long-horizon operation ledger", "Queued, running, waiting for human, completed, failed, interrupted and cancelled are durable states.", Activity], memory: ["Provenance before compression", "Summaries cite their source range and raw events remain retained for audit and replay.", BrainCircuit], graph: ["Connected evidence, not a hairball", "The graph progressively loads authorized conversations, runs, assets and findings.", Network], artifacts: ["Evidence with provenance", "Artifacts are hash-addressed and permission-checked. Select an operation to inspect created evidence.", FileBox], hitl: ["Human decision boundary", "Risky actions pause until a scope-bound, authenticated approval is consumed once.", ShieldCheck], settings: ["Provider and policy controls", "BYOK stays encrypted in Turso. Page Agent and runtime extensions remain disabled until permission gates are configured.", Settings2] };
  const [title, detail, Icon] = catalog[view];
  return <div className="workspace-scroll registry"><section className="registry-heading"><Icon size={28} /><div><p className="eyebrow">{view.toUpperCase()}</p><h1>{title}</h1><p>{detail}</p></div></section>{conversations.length > 0 && <section className="data-panel"><header className="panel-heading"><h2>Related durable operations</h2></header><div className="operation-table">{conversations.map((item) => <button key={item.id} onClick={() => select(item.id)}><span>{item.title}</span><span>{item.status}</span><span>{formatTime(item.last_activity_at_ms)}</span><ChevronRight size={15} /></button>)}</div></section>}</div>;
}

function Inspector({ run, detail, conversation, error, cancelRun, retryRun }: { run: Run | null; detail: RunDetail | null; conversation: Conversation | null; error: string; cancelRun: () => void; retryRun: () => void }) {
  return <aside className="flight-inspector"><header><p className="eyebrow">CONTEXT INSPECTOR</p><h2>{run ? "Run state" : "System boundary"}</h2></header>{run ? <><dl><dt>State</dt><dd><span className={cn("state-label", `state-label--${run.state}`)}>{run.state}</span></dd><dt>Attempt</dt><dd>{run.attempt}</dd><dt>Fencing</dt><dd className="mono">{run.fencing_epoch}</dd><dt>Run</dt><dd className="mono">{run.id}</dd></dl><div className="inspector-actions">{["queued", "running", "waiting_for_human"].includes(run.state) && <button className="quiet-button" onClick={cancelRun}>Cancel safely</button>}{["completed", "failed", "interrupted", "cancelled"].includes(run.state) && <button className="quiet-button" onClick={retryRun}>Retry attempt</button>}</div>{detail?.reasoning.length ? <section className="inspector-feed"><p className="eyebrow">COGNITION</p>{detail.reasoning.slice(-3).map((event) => <article key={event.id}><b>{event.kind.replaceAll("_", " ")}</b><span>{event.agent_name} · step {event.step}</span><p>{event.content}</p></article>)}</section> : <p className="inspector-note">No provider reasoning has been persisted. Operational summaries and tool intent remain distinguishable when events arrive.</p>}{detail?.tools.length ? <section className="inspector-feed"><p className="eyebrow">TOOLS</p>{detail.tools.slice(-3).map((tool) => <article key={tool.id}><b>{tool.tool_name}</b><span>{tool.agent_name} · {tool.state}</span></article>)}</section> : null}<p className="inspector-note">Late workers cannot complete an expired or fenced lease. Raven Replay is recorded-only until a sandbox gate is explicitly configured.</p></> : <><dl><dt>Conversation</dt><dd>{conversation?.title || "No selection"}</dd><dt>Storage</dt><dd>Turso authority</dd><dt>Client</dt><dd>Acceleration only</dd><dt>Service</dt><dd>{error ? "Needs attention" : "Available"}</dd></dl><p className="inspector-note">Select a run to inspect reasoning provenance, tool intent, evidence, scope and artifacts without leaving the operation.</p></>}</aside>;
}

function Empty({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) { return <section className="empty-object"><img src="/raven-mark.png" width={48} height={48} alt="" /><h2>{title}</h2><p>{detail}</p>{action}</section>; }
function message(cause: unknown) { return cause instanceof Error ? cause.message : "Request failed"; }
function formatTime(value: number) { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
