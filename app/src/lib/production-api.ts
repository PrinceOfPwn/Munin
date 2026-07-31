export type Actor = { id: string; username: string; role: "admin" | "operator" | "viewer" };

export type Conversation = {
  id: string;
  title: string;
  status: string;
  tags: string[];
  last_activity_at_ms: number;
  message_count: number;
  version: number;
};

export type Run = {
  id: string;
  state: string;
  attempt: number;
  fencing_epoch: number;
  assistant_message_id: string;
  updated_at_ms: number;
  lease_expires_at_ms?: number;
};

export type TimelineMessage = {
  id: string;
  kind: string;
  status: string;
  content: string;
  sequence: number;
  run_id?: string;
};

export type ConversationDetail = {
  conversation: Conversation;
  messages: TimelineMessage[];
  runs: Run[];
};

export type ReasoningEvent = {
  id: string;
  kind:
    | "provider_reasoning"
    | "operational_summary"
    | "tool_intent"
    | "observation"
    | "decision"
    | "model_request";
  content: string;
  agent_name: string;
  provider: string;
  step: number;
  persisted: boolean;
  created_at_ms: number;
};

export type ToolInvocation = {
  id: string;
  tool_name: string;
  state: string;
  agent_name: string;
  arguments: unknown;
  result: unknown;
  started_at_ms?: number;
  finished_at_ms?: number;
  /** v3.1 — shared across every tool_use in the same assistant message. */
  parallel_group_id?: string | null;
  /** v3.1 — Anthropic/OpenAI stable tool_use identifier. */
  tool_use_id?: string | null;
};

export type Collaborator = {
  conversation_id: string;
  actor_id: string;
  actor_username: string;
  role: "owner" | "collaborator" | "viewer";
  added_at_ms: number;
  added_by_actor_id: string;
};

export type ConversationNote = {
  id: string;
  conversation_id: string;
  actor_id: string;
  actor_username?: string;
  body: string;
  created_at_ms: number;
};

export type PresenceEntry = {
  actor_id: string;
  actor_username: string;
  last_seen_ms: number;
  typing: boolean;
  typing_at_ms?: number | null;
};

export type QueuedGuidance = {
  id: string;
  run_id: string;
  actor_id: string;
  actor_username: string;
  body: string;
  target_agent_id?: string | null;
  created_at_ms: number;
  consumed_at_ms?: number | null;
  delivered_at_step?: number | null;
  budget_extension_seconds?: number;
};

export type SubagentInvocation = {
  id: string;
  profile_id: string;
  state: string;
  objective: string;
  agent_name?: string;
  started_at_ms?: number;
};

export type HumanRequest = {
  id: string;
  action: string;
  risk: string;
  state: string;
  choices: string[];
  run_id?: string;
  nonce?: string;
  detail?: string;
};

export type ArtifactRef = {
  id: string;
  filename: string;
  media_type: string;
  language: string;
  size_bytes: number;
  run_id?: string;
  created_at_ms?: number;
};

interface RunEventBase {
  id: string;
  sequence: number;
  created_at_ms: number;
}

/**
 * Discriminated union of run-event payloads streamed over SSE.  Each event's
 * `kind` fixes the shape of `payload` — clients can `switch(event.kind)`
 * without casting.  Unknown kinds are typed as `RunEventUnknown` so the
 * merge site's `default:` branch still gets structural info without an
 * `any`.
 */
export type RunEventKind =
  | "reasoning"
  | "tool_intent"
  | "tool_started"
  | "tool_result"
  | "tool_completed"
  | "tool_failed"
  | "subagent_started"
  | "subagent_state"
  | "human_request"
  | "human_resolved"
  | "artifact"
  | "run_state";

export type RunEventEnvelope =
  | (RunEventBase & { kind: "reasoning"; payload: ReasoningEvent })
  | (RunEventBase & { kind: "tool_intent" | "tool_started" | "tool_result" | "tool_completed" | "tool_failed"; payload: ToolInvocation })
  | (RunEventBase & { kind: "subagent_started" | "subagent_state"; payload: SubagentInvocation })
  | (RunEventBase & { kind: "human_request" | "human_resolved"; payload: HumanRequest })
  | (RunEventBase & { kind: "artifact"; payload: ArtifactRef })
  | (RunEventBase & { kind: "run_state"; payload: Partial<Run> })
  | (RunEventBase & { kind: Exclude<string, RunEventKind>; payload: Record<string, unknown> });

export type RunDetail = {
  run: Run;
  events: RunEventEnvelope[];
  reasoning: ReasoningEvent[];
  tools: ToolInvocation[];
  subagents: SubagentInvocation[];
  human_requests: HumanRequest[];
  artifacts: ArtifactRef[];
};

export type AgentProfile = {
  id: string;
  role: string;
  objective: string;
  risk: string;
  tools: string[];
  description?: string;
};

export type ProviderProfile = {
  id: string;
  label: string;
  provider: string;
  base_url: string;
  model: string;
  uses: string[];
  active: boolean;
  created_at_ms: number;
};

export type ArtifactContent = ArtifactRef & { content?: string };

let csrfToken = "";

/** Typed error thrown when the backend rejects with 401. React Query error
 * handlers use `instanceof AuthError` to differentiate expired session from
 * transient failure — the former must stop polling, the latter must not. */
export class AuthError extends Error {
  constructor(message = "unauthenticated") {
    super(message);
    this.name = "AuthError";
  }
}

/** Request timeout. Long enough for one Turso round-trip + AES-GCM decrypt
 * of a normal conversation, short enough that a stuck request fails before
 * Cloudflare/ngrok proxies kill it (100s / 300s respectively). Streaming
 * endpoints must NOT go through this helper — use EventSource / raw fetch. */
const DEFAULT_TIMEOUT_MS = 15_000;

async function request<T>(
  path: string,
  init: RequestInit = {},
  { timeoutMs = DEFAULT_TIMEOUT_MS }: { timeoutMs?: number } = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`/api/production/${path.replace(/^\/+/, "")}`, {
      ...init,
      credentials: "same-origin",
      signal: init.signal ?? controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...(csrfToken && !["GET", "HEAD"].includes((init.method || "GET").toUpperCase())
          ? { "X-CSRF-Token": csrfToken }
          : {}),
        ...(init.headers || {}),
      },
      cache: "no-store",
    });
    if (response.status === 401) {
      csrfToken = "";
      throw new AuthError();
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload?.ok === false) {
      throw new Error(payload?.error?.message || `Request failed (${response.status})`);
    }
    return payload as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Request timed out after ${timeoutMs}ms: ${path}`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export const productionApi = {
  // ── auth ─────────────────────────────────────────────────────────────
  async session() {
    const payload = await request<{ actor: Actor; csrf_token: string }>("auth/session");
    csrfToken = payload.csrf_token;
    return payload.actor;
  },
  async login(username: string, password: string) {
    csrfToken = (
      await request<{ csrf_token: string }>("auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      })
    ).csrf_token;
  },
  async bootstrap(username: string, password: string) {
    await request("auth/bootstrap", { method: "POST", body: JSON.stringify({ username, password }) });
  },
  async logout() {
    await request("auth/logout", { method: "POST", body: "{}" });
    csrfToken = "";
  },

  // ── conversations ────────────────────────────────────────────────────
  async conversations(query = "") {
    return (
      await request<{ data: { conversations: Conversation[] } }>(
        `conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`
      )
    ).data.conversations;
  },
  async createConversation(title = "New operation") {
    return (
      await request<{ data: Conversation }>("conversations", {
        method: "POST",
        body: JSON.stringify({ title }),
      })
    ).data;
  },
  async conversation(id: string) {
    return (
      await request<{ data: ConversationDetail }>(`conversations/${encodeURIComponent(id)}`)
    ).data;
  },
  async archiveConversation(id: string, version: number, archived: boolean) {
    return (
      await request<{ data: Conversation }>(`conversations/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ version, archived }),
      })
    ).data;
  },
  async renameConversation(id: string, version: number, title: string) {
    return (
      await request<{ data: Conversation }>(`conversations/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ version, title }),
      })
    ).data;
  },
  async exportConversation(id: string) {
    return (
      await request<{ data: unknown }>(`conversations/${encodeURIComponent(id)}/export`)
    ).data;
  },

  // ── turns / runs ─────────────────────────────────────────────────────
  async turn(conversationId: string, content: string, idempotencyKey: string) {
    return (
      await request<{ data: { run: Run } }>(
        `conversations/${encodeURIComponent(conversationId)}/turns`,
        {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: JSON.stringify({ content }),
        }
      )
    ).data;
  },
  async runDetail(id: string) {
    return (await request<{ data: RunDetail }>(`runs/${encodeURIComponent(id)}/detail`)).data;
  },
  async cancelRun(id: string) {
    return (
      await request<{ data: Run }>(`runs/${encodeURIComponent(id)}/cancel`, {
        method: "POST",
        body: "{}",
      })
    ).data;
  },
  async retryRun(id: string) {
    return (
      await request<{ data: Run }>(`runs/${encodeURIComponent(id)}/retry`, {
        method: "POST",
        body: "{}",
      })
    ).data;
  },
  async guideRun(
    id: string,
    guidance: string,
    options: { target_agent_id?: string; budget_extension_seconds?: number } = {},
  ) {
    return await request(`runs/${encodeURIComponent(id)}/guidance`, {
      method: "POST",
      body: JSON.stringify({
        guidance,
        body: guidance,
        target_agent_id: options.target_agent_id,
        budget_extension_seconds: options.budget_extension_seconds,
      }),
    });
  },
  async listRunGuidance(runId: string) {
    return (
      await request<{ data: QueuedGuidance[] }>(
        `runs/${encodeURIComponent(runId)}/guidance`,
      )
    ).data;
  },
  async createReplayBranch(id: string, forkEventId: string, hypothesis: string) {
    return await request(`runs/${encodeURIComponent(id)}/branches`, {
      method: "POST",
      body: JSON.stringify({
        fork_event_id: forkEventId,
        hypothesis,
        replay_mode: "recorded",
      }),
    });
  },

  // ── v3 additions: previously-orphaned endpoints ──────────────────────
  async agents() {
    return (await request<{ data: AgentProfile[] }>("agents")).data;
  },
  async providerProfiles() {
    return (await request<{ data: ProviderProfile[] }>("provider-profiles")).data;
  },
  async saveProviderProfile(profile: {
    label: string;
    provider: string;
    base_url: string;
    model: string;
    uses: string[];
    key: string;
  }) {
    return (
      await request<{ data: ProviderProfile }>("provider-profiles", {
        method: "POST",
        body: JSON.stringify(profile),
      })
    ).data;
  },
  async providerProfileAction(profileId: string, action: "activate" | "revoke" | "rotate", body: Record<string, unknown> = {}) {
    return (
      await request<{ data: unknown }>(
        `provider-profiles/${encodeURIComponent(profileId)}/${action}`,
        { method: "POST", body: JSON.stringify(body) }
      )
    ).data;
  },
  async resolveHumanRequest(requestId: string, choice: string, nonce = "", guidance = "") {
    return (
      await request<{ data: unknown }>(`human-requests/${encodeURIComponent(requestId)}/resolve`, {
        method: "POST",
        body: JSON.stringify({ choice, nonce, guidance }),
      })
    ).data;
  },
  async artifact(id: string) {
    return (await request<{ data: ArtifactContent }>(`artifacts/${encodeURIComponent(id)}`)).data;
  },
  artifactDownloadUrl(id: string) {
    // Same-origin URL — the Next proxy attaches the session cookie.
    return `/api/production/artifacts/${encodeURIComponent(id)}?download=true`;
  },

  // ── v3.1 multi-operator collaboration ────────────────────────────────
  async listCollaborators(conversationId: string) {
    return (
      await request<{ data: Collaborator[] }>(
        `conversations/${encodeURIComponent(conversationId)}/collaborators`,
      )
    ).data;
  },
  async addCollaborator(
    conversationId: string,
    username: string,
    role: "collaborator" | "viewer" | "owner" = "collaborator",
  ) {
    return (
      await request<{ data: Collaborator[] }>(
        `conversations/${encodeURIComponent(conversationId)}/collaborators`,
        {
          method: "POST",
          body: JSON.stringify({ username, role }),
        },
      )
    ).data;
  },
  async listNotes(conversationId: string, afterMs = 0) {
    const suffix = afterMs > 0 ? `?after_ms=${afterMs}` : "";
    return (
      await request<{ data: ConversationNote[] }>(
        `conversations/${encodeURIComponent(conversationId)}/notes${suffix}`,
      )
    ).data;
  },
  async postNote(conversationId: string, body: string) {
    return (
      await request<{ data: ConversationNote }>(
        `conversations/${encodeURIComponent(conversationId)}/notes`,
        { method: "POST", body: JSON.stringify({ body }) },
      )
    ).data;
  },
  async presenceHeartbeat(conversationId: string, typing: boolean) {
    return (
      await request<{ data: PresenceEntry[] }>(
        `conversations/${encodeURIComponent(conversationId)}/presence`,
        { method: "POST", body: JSON.stringify({ typing }) },
      )
    ).data;
  },
};

/** Snapshot of the current CSRF token — hooks that need to include it in
 *  a non-fetch context (e.g. an EventSource sends none, but a POST alongside
 *  needs one) can read it here. */
export function currentCsrfToken(): string {
  return csrfToken;
}
