// -----------------------------------------------------------------------------
// production-api — Fase 2 (issue #9) trimmed surface.
//
// The Munin BFF no longer proxies dispatcher-era endpoints (turns / runs SSE /
// HITL resolve / provider profiles / collab-notes-presence / branches / dev
// simulate-forge).  The only Python routes the frontend hits directly are:
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
};

/** Snapshot of the current CSRF token — hooks that need to include it in
 *  a non-fetch context can read it here. */
export function currentCsrfToken(): string {
  return csrfToken;
}
