// tags: [utility-library, bff-client, csrf, http-client]
// -----------------------------------------------------------------------------
// production-api — Fase 2 (issue #9) trimmed surface.
//
// The Munin BFF no longer proxies dispatcher-era endpoints (turns / runs SSE /
// HITL resolve / collab-notes-presence / branches / dev simulate-forge). The
// frontend uses the production boundary for auth, conversations, and encrypted
// provider-profile metadata; runtime execution itself remains `/api/chat`.
//
//   * auth: session / login / bootstrap / logout
//   * conversations: list / create / rename / archive
//
// Everything runtime-shaped now flows through `/api/chat` (AI SDK v5) — see
// `aiChat.ts` for the transport.
// -----------------------------------------------------------------------------

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

export type ConversationMessage = {
  id: string;
  kind: "user" | "assistant" | string;
  status?: string;
  content: string;
  sequence: number;
  run_id?: string | null;
};

export type ConversationAggregate = {
  conversation: Conversation;
  messages: ConversationMessage[];
};

export type ProviderProfile = {
  id: string;
  label: string;
  provider: string;
  base_url: string;
  model: string;
  uses?: string[];
  key_fingerprint?: string;
  status?: string;
  active: boolean;
};

let csrfToken = "";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/production/${path.replace(/^\/+/, "")}`, {
    ...init,
    credentials: "same-origin",
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
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error?.message || `Request failed (${response.status})`);
  }
  return payload as T;
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
  async conversation(id: string) {
    return (
      await request<{ data: ConversationAggregate }>(
        `conversations/${encodeURIComponent(id)}`,
      )
    ).data;
  },
  async providerProfiles() {
    return (
      await request<{ data: ProviderProfile[] }>("provider-profiles")
    ).data;
  },
  async createProviderProfile(input: {
    label: string;
    provider: string;
    base_url: string;
    model: string;
    api_key: string;
    activate?: boolean;
  }) {
    return (
      await request<{ data: ProviderProfile }>("provider-profiles", {
        method: "POST",
        body: JSON.stringify(input),
      })
    ).data;
  },
  async activateProviderProfile(id: string) {
    return (
      await request<{ data: ProviderProfile }>(
        `provider-profiles/${encodeURIComponent(id || "default")}/activate`,
        { method: "POST", body: "{}" },
      )
    ).data;
  },
  async createConversation(title = "New operation") {
    return (
      await request<{ data: Conversation }>("conversations", {
        method: "POST",
        body: JSON.stringify({ title }),
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
  async archiveConversation(id: string, version: number, archived: boolean) {
    return (
      await request<{ data: Conversation }>(`conversations/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ version, archived }),
      })
    ).data;
  },
  async exportConversation(id: string) {
    return (
      await request<{ data: unknown }>(
        `conversations/${encodeURIComponent(id)}/export`,
      )
    ).data;
  },
};

/** Snapshot of the current CSRF token — hooks that need to include it in
 *  a non-fetch context can read it here. */
export function currentCsrfToken(): string {
  return csrfToken;
}
