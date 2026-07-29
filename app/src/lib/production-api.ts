export type Actor = { id: string; username: string; role: "admin" | "operator" | "viewer" };
export type Conversation = { id: string; title: string; status: string; tags: string[]; last_activity_at_ms: number; message_count: number; version: number };
export type Run = { id: string; state: string; attempt: number; fencing_epoch: number; assistant_message_id: string; updated_at_ms: number };
export type TimelineMessage = { id: string; kind: string; status: string; content: string; sequence: number; run_id?: string };
export type ConversationDetail = { conversation: Conversation; messages: TimelineMessage[]; runs: Run[] };
export type ReasoningEvent = { id: string; kind: "provider_reasoning" | "operational_summary" | "tool_intent" | "observation" | "decision" | "model_request"; content: string; agent_name: string; provider: string; step: number; persisted: boolean; created_at_ms: number };
export type RunDetail = { run: Run; events: Array<{ id: string; sequence: number; kind: string; payload: unknown; created_at_ms: number }>; reasoning: ReasoningEvent[]; tools: Array<{ id: string; tool_name: string; state: string; agent_name: string; arguments: unknown; result: unknown }>; subagents: Array<{ id: string; profile_id: string; state: string; objective: string }>; human_requests: Array<{ id: string; action: string; risk: string; state: string; choices: string[] }>; artifacts: Array<{ id: string; filename: string; media_type: string; language: string; size_bytes: number }> };

let csrfToken = "";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/production/${path.replace(/^\/+/, "")}`, {
    ...init,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...(csrfToken && !["GET", "HEAD"].includes((init.method || "GET").toUpperCase()) ? { "X-CSRF-Token": csrfToken } : {}), ...(init.headers || {}) },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok === false) throw new Error(payload?.error?.message || `Request failed (${response.status})`);
  return payload as T;
}

export const productionApi = {
  async session() { const payload = await request<{ actor: Actor; csrf_token: string }>("auth/session"); csrfToken = payload.csrf_token; return payload.actor; },
  async login(username: string, password: string) { csrfToken = (await request<{ csrf_token: string }>("auth/login", { method: "POST", body: JSON.stringify({ username, password }) })).csrf_token; },
  async bootstrap(username: string, password: string) { await request("auth/bootstrap", { method: "POST", body: JSON.stringify({ username, password }) }); },
  async logout() { await request("auth/logout", { method: "POST", body: "{}" }); csrfToken = ""; },
  async conversations(query = "") { return (await request<{ data: { conversations: Conversation[] } }>(`conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`)).data.conversations; },
  async createConversation(title = "New operation") { return (await request<{ data: Conversation }>("conversations", { method: "POST", body: JSON.stringify({ title }) })).data; },
  async conversation(id: string) { return (await request<{ data: ConversationDetail }>(`conversations/${encodeURIComponent(id)}`)).data; },
  async archiveConversation(id: string, version: number, archived: boolean) { return (await request<{ data: Conversation }>(`conversations/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ version, archived }) })).data; },
  async exportConversation(id: string) { return (await request<{ data: unknown }>(`conversations/${encodeURIComponent(id)}/export`)).data; },
  async turn(conversationId: string, content: string, idempotencyKey: string) { return (await request<{ data: { run: Run } }>(`conversations/${encodeURIComponent(conversationId)}/turns`, { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: JSON.stringify({ content }) })).data; },
  async runDetail(id: string) { return (await request<{ data: RunDetail }>(`runs/${encodeURIComponent(id)}/detail`)).data; },
  async cancelRun(id: string) { return (await request<{ data: Run }>(`runs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" })).data; },
  async retryRun(id: string) { return (await request<{ data: Run }>(`runs/${encodeURIComponent(id)}/retry`, { method: "POST", body: "{}" })).data; },
  async guideRun(id: string, guidance: string) { return await request(`runs/${encodeURIComponent(id)}/guidance`, { method: "POST", body: JSON.stringify({ guidance }) }); },
  async createReplayBranch(id: string, forkEventId: string, hypothesis: string) { return await request(`runs/${encodeURIComponent(id)}/branches`, { method: "POST", body: JSON.stringify({ fork_event_id: forkEventId, hypothesis, replay_mode: "recorded" }) }); },
};
