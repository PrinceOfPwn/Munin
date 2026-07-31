"use client";

import { create } from "zustand";
import { uuid } from "@/lib/utils";
import { getMcpClient, extractToolResultContent, unwrapToolData } from "@/lib/mcp";
import { log } from "@/lib/logger";
import type {
  ViewKey,
  ChatMessage,
  ToolCall,
  McpTool,
  AgentPresence,
  WakeItem,
  EpisodicEvent,
  ConversationSummary,
  ConversationArtifact,
} from "@/types/mcp";

interface LiveState {
  mcpConnected: boolean;
  toolCount: number;
  presence: AgentPresence[];
  forgedToolCount: number;
  wakePendingCount: number;
  lastEpisodic: EpisodicEvent | null;
  lastError: string | null;
  lastUpdated: number;
}

interface MuninState {
  // Settings
  mcpUrl: string;
  mcpToken: string;
  settingsOpen: boolean;

  // Navigation
  view: ViewKey;

  // Tools
  tools: McpTool[];
  toolsLoading: boolean;

  // Chat
  messages: ChatMessage[];
  chatInput: string;
  conversations: ConversationSummary[];
  conversationsLoading: boolean;
  activeConversationId: string;

  // Live state
  live: LiveState;

  // Actions
  init: () => void;
  setView: (v: ViewKey) => void;
  openSettings: () => void;
  closeSettings: () => void;
  setConfig: (url: string, token: string) => void;
  testConnection: () => Promise<{ ok: boolean; message: string }>;
  refreshTools: () => Promise<void>;
  refreshLive: () => Promise<void>;

  setChatInput: (s: string) => void;
  newConversation: () => void;
  selectConversation: (conversationId: string) => Promise<void>;
  loadConversations: () => Promise<void>;
  sendChatMessage: () => Promise<void>;
  appendToolCallToMessage: (messageId: string, call: ToolCall) => void;
  updateToolCall: (messageId: string, callId: string, patch: Partial<ToolCall>) => void;
}

const LOCAL_MCP_URL = "http://localhost:8890";
const DEFAULT_TOKEN = "";
const L = log.store;

function defaultMcpUrl(): string {
  if (
    typeof window !== "undefined" &&
    process.env.NEXT_PUBLIC_MUNIN_MCP_SAME_ORIGIN === "1"
  ) {
    return window.location.origin;
  }
  return LOCAL_MCP_URL;
}

function loadStoredConfig(): { url: string; token: string } {
  const fallbackUrl = defaultMcpUrl();
  if (typeof window === "undefined") return { url: fallbackUrl, token: DEFAULT_TOKEN };
  try {
    const raw = window.localStorage.getItem("munin.config");
    if (raw) {
      const parsed = JSON.parse(raw);
      const url = parsed.url || fallbackUrl;
      L.info("Config loaded from localStorage", { url, hasToken: !!parsed.token });
      return { url, token: parsed.token || DEFAULT_TOKEN };
    }
  } catch (e) {
    L.warn("Failed to load config from localStorage — using defaults", e);
  }
  L.info("No stored config — using defaults", { url: fallbackUrl });
  return { url: fallbackUrl, token: DEFAULT_TOKEN };
}

function saveStoredConfig(url: string, token: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem("munin.config", JSON.stringify({ url, token }));
    L.debug("Config saved to localStorage", { url, hasToken: !!token });
  } catch (e) {
    L.warn("Failed to save config to localStorage", e);
  }
}

const initialLive: LiveState = {
  mcpConnected: false,
  toolCount: 0,
  presence: [],
  forgedToolCount: 0,
  wakePendingCount: 0,
  lastEpisodic: null,
  lastError: null,
  lastUpdated: 0,
};

export const useMuninStore = create<MuninState>((set, get) => ({
  mcpUrl: LOCAL_MCP_URL,
  mcpToken: DEFAULT_TOKEN,
  settingsOpen: false,
  view: "chat",
  tools: [],
  toolsLoading: false,
  messages: [
    {
      id: uuid(),
      role: "assistant",
      content:
        "I am **Munin**. What was once seen is never forgotten.\n\nAsk me to act — call a tool with `/tool_name key=value`, or browse the **Tool Explorer**. My memory, soul, and forged tools live in the panels above.",
      timestamp: Date.now(),
    },
  ],
  chatInput: "",
  conversations: [],
  conversationsLoading: false,
  activeConversationId: "",
  live: initialLive,

  init: () => {
    const { url, token } = loadStoredConfig();
    set({ mcpUrl: url, mcpToken: token });
    L.info("store.init — triggering initial fetches");
    // Fire and forget — errors are caught inside each fn
    get().refreshTools();
    get().refreshLive();
    get().loadConversations();
  },

  setView: (v) => set({ view: v }),
  openSettings: () => set({ settingsOpen: true }),
  closeSettings: () => set({ settingsOpen: false }),

  setConfig: (url, token) => {
    L.info("setConfig", { url, hasToken: !!token });
    set({ mcpUrl: url, mcpToken: token });
    saveStoredConfig(url, token);
  },

  testConnection: async () => {
    const { mcpUrl, mcpToken } = get();
    L.info("testConnection", { url: mcpUrl, hasToken: !!mcpToken });
    const done = L.time("testConnection");
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const tools = await client.listTools();
      done("ok");
      L.info(`testConnection success — ${tools.length} tools`);
      set((s) => ({
        tools,
        live: {
          ...s.live,
          mcpConnected: true,
          toolCount: tools.length,
          lastError: null,
          lastUpdated: Date.now(),
        },
      }));
      // Conversation data has no browser-side fallback: after a successful
      // connection, hydrate the durable Turso-backed history immediately.
      void get().loadConversations();
      return { ok: true, message: `Connected. ${tools.length} tools available.` };
    } catch (e: any) {
      done("error");
      L.error("testConnection failed", e, { url: mcpUrl });
      set((s) => ({
        live: {
          ...s.live,
          mcpConnected: false,
          lastError: e?.message || String(e),
          lastUpdated: Date.now(),
        },
      }));
      return { ok: false, message: e?.message || String(e) };
    }
  },

  refreshTools: async () => {
    L.debug("refreshTools start");
    set({ toolsLoading: true });
    const done = L.time("refreshTools");
    try {
      const { mcpUrl, mcpToken } = get();
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const tools = await client.listTools();
      done("ok");
      L.info(`refreshTools — ${tools.length} tools loaded`);
      set((s) => ({
        tools,
        toolsLoading: false,
        live: {
          ...s.live,
          mcpConnected: true,
          toolCount: tools.length,
          lastError: null,
          lastUpdated: Date.now(),
        },
      }));
    } catch (e: any) {
      done("error");
      L.error("refreshTools failed", e, {
        hint: "MCP server unreachable — check URL and token in Settings.",
      });
      set((s) => ({
        toolsLoading: false,
        live: {
          ...s.live,
          mcpConnected: false,
          lastError: e?.message || String(e),
          lastUpdated: Date.now(),
        },
      }));
    }
  },

  refreshLive: async () => {
    const { mcpUrl, mcpToken } = get();
    const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
    const close = log.poll.group("refreshLive");

    const next: Partial<LiveState> = { lastUpdated: Date.now() };

    // 1. Verify connection first
    try {
      await client.listTools();
      next.mcpConnected = true;
      next.lastError = null;
      log.poll.debug("connection ok");
    } catch (e: any) {
      next.mcpConnected = false;
      next.lastError = e?.message || String(e);
      log.poll.warn("connection check failed — skipping live queries", { error: next.lastError });
      set((s) => ({ live: { ...s.live, ...next } as LiveState }));
      close();
      return;
    }

    // 2. Fire parallel sidebar queries — best effort, log individual failures
    const tasks: Promise<void>[] = [];

    tasks.push(
      client
        .callTool("list_agent_presence", {})
        .then((r) => {
          const { json } = extractToolResultContent(r);
          const data = unwrapToolData(json);
          if (Array.isArray(data)) next.presence = data;
          else if (data && Array.isArray(data.presence)) next.presence = data.presence;
          else if (data && Array.isArray(data.agents)) next.presence = data.agents;
          else if (data && Array.isArray(data.matches)) next.presence = data.matches;
          else next.presence = [];
          log.poll.debug(`list_agent_presence → ${next.presence?.length ?? 0} agents`);
        })
        .catch((e) => {
          log.poll.warn("list_agent_presence failed (non-critical)", { error: e?.message });
        })
    );

    tasks.push(
      client
        .callTool("list_generated_tools", {})
        .then((r) => {
          const { json } = extractToolResultContent(r);
          const data = unwrapToolData(json);
          if (Array.isArray(data)) next.forgedToolCount = data.length;
          else if (data && Array.isArray(data.tools)) next.forgedToolCount = data.tools.length;
          else next.forgedToolCount = 0;
          log.poll.debug(`list_generated_tools → ${next.forgedToolCount}`);
        })
        .catch((e) => {
          log.poll.warn("list_generated_tools failed (non-critical)", { error: e?.message });
        })
    );

    tasks.push(
      client
        .callTool("munin_wake_list", {})
        .then((r) => {
          const { json } = extractToolResultContent(r);
          const data = unwrapToolData(json);
          const arr = Array.isArray(data)
            ? data
            : data && Array.isArray(data.items)
            ? data.items
            : data && Array.isArray(data.queue)
            ? data.queue
            : [];
          next.wakePendingCount = arr.filter(
            (x: WakeItem) => !x.status || /pending|queued|waiting/i.test(String(x.status))
          ).length;
          log.poll.debug(`munin_wake_list → ${next.wakePendingCount} pending`);
        })
        .catch((e) => {
          log.poll.warn("munin_wake_list failed (non-critical)", { error: e?.message });
        })
    );

    tasks.push(
      client
        .callTool("episodic_query", { limit: 1 })
        .then((r) => {
          const { json } = extractToolResultContent(r);
          const data = unwrapToolData(json);
          if (Array.isArray(data) && data.length > 0) next.lastEpisodic = data[0];
          else if (data && Array.isArray(data.events) && data.events.length > 0)
            next.lastEpisodic = data.events[0];
          else if (data && !Array.isArray(data)) next.lastEpisodic = data;
          else next.lastEpisodic = null;
          log.poll.debug("episodic_query →", next.lastEpisodic ? "1 event" : "empty");
        })
        .catch((e) => {
          log.poll.warn("episodic_query failed (non-critical)", { error: e?.message });
        })
    );

    await Promise.allSettled(tasks);
    set((s) => ({ live: { ...s.live, ...next } as LiveState }));
    close();
  },

  setChatInput: (s) => set({ chatInput: s }),

  newConversation: () => {
    const id = "conv_" + uuid().replace(/-/g, "");
    set({
      activeConversationId: id,
      messages: [
        {
          id: "intro-" + id,
          role: "assistant",
          content:
            "I am **Munin**. This is a new persistent conversation. Its turns and generated files will be stored in Turso once you send the first message.",
          timestamp: Date.now(),
        },
      ],
      chatInput: "",
      view: "chat",
    });
  },

  loadConversations: async () => {
    const { mcpUrl, mcpToken } = get();
    set({ conversationsLoading: true });
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const response = await client.callTool("conversation_list", { limit: 100 });
      const { json, text, isError } = extractToolResultContent(response);
      if (isError) throw new Error(text || "Unable to load conversations");
      const data = unwrapToolData(json);
      const conversations = Array.isArray(data?.conversations) ? data.conversations : [];
      set({ conversations, conversationsLoading: false });
      const active = get().activeConversationId;
      if (!active && conversations.length > 0) {
        await get().selectConversation(conversations[0].id);
      }
    } catch (e: any) {
      // Chat persistence is deliberately Turso-only. Keep the compose surface
      // available, but never synthesize a local history when the remote store
      // is unavailable.
      L.warn("loadConversations failed", { error: e?.message || String(e) });
      set({ conversationsLoading: false });
    }
  },

  selectConversation: async (conversationId) => {
    const { mcpUrl, mcpToken } = get();
    set({ conversationsLoading: true });
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const response = await client.callTool("conversation_get", {
        conversation_id: conversationId,
        message_limit: 1_000,
      });
      const { json, text, isError } = extractToolResultContent(response);
      if (isError) throw new Error(text || "Unable to load conversation");
      const data = unwrapToolData(json);
      const artifacts = Array.isArray(data?.artifacts) ? data.artifacts : [];
      set({
        activeConversationId: conversationId,
        messages: conversationMessagesToChat(data?.messages || [], artifacts),
        conversationsLoading: false,
        view: "chat",
      });
    } catch (e: any) {
      L.warn("selectConversation failed", { conversationId, error: e?.message || String(e) });
      set({ conversationsLoading: false });
    }
  },

  sendChatMessage: async () => {
    const text = get().chatInput.trim();
    if (!text) return;
    log.chat.info("sendChatMessage", { text: text.slice(0, 120) });

    const userMsg: ChatMessage = {
      id: uuid(),
      role: "user",
      content: text,
      timestamp: Date.now(),
    };
    const assistantId = uuid();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: "assistant",
      content: "",
      thinking: true,
      timestamp: Date.now(),
    };
    let conversationId = get().activeConversationId;
    if (!conversationId) {
      conversationId = "conv_" + uuid().replace(/-/g, "");
    }

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      chatInput: "",
      activeConversationId: conversationId,
    }));

    // Slash command -> direct tool call
    if (text.startsWith("/")) {
      const { name, args } = parseSlashCommand(text);
      log.chat.info(`slash command: /${name}`, { args });
      const tools = get().tools;
      if (!tools.some((t) => t.name === name)) {
        log.chat.warn(`Unknown tool: ${name}`, { available: tools.map(t => t.name) });
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  thinking: false,
                  content: `**Unknown tool:** \`${name}\`\n\nUse the Tool Explorer to discover available tools, or type \`/help\`.`,
                }
              : m
          ),
        }));
        return;
      }
      await runToolCallInline(assistantId, name, args, set, get);
      return;
    }

    // Help command
    if (/^\/help$/i.test(text)) {
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                thinking: false,
                content:
                  "Invoke any tool directly with `/tool_name key=value key2=value2`. Example:\n\n```\n/ldap_who_am_i\n/ldap_search filter=(&(objectClass=user))\n/episodic_query limit=5\n```\n\nBrowse the Tool Explorer for the full catalog with auto-generated forms.",
              }
            : m
        ),
      }));
      return;
    }

    // Free text: look for a chat-style tool, otherwise produce a thoughtful canned reply
    const chatTool = get().tools.find(
      (t) =>
        /^(munin_)?chat$|^(ask|ask_munin|munin_ask|query|converse)$|talk/i.test(t.name)
    );

    if (chatTool) {
      if (chatTool.name === "munin_chat") {
        await runMuninChatJob(assistantId, text, conversationId, set, get);
        return;
      }
      await runToolCallInline(
        assistantId,
        chatTool.name,
        { message: text },
        set,
        get
      );
      return;
    }

    // No chat tool — produce Munin's narrative reply listing capabilities
    const tools = get().tools;
    const sample = tools.slice(0, 6).map((t) => `- \`${t.name}\``).join("\n");
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === assistantId
          ? {
              ...m,
              thinking: false,
              content:
                "I have no direct speech channel to the model behind me in this configuration, but I am awake and listening.\n\nInvoke a tool directly with `/tool_name key=value`, or open the **Tool Explorer** to run any of the " +
                tools.length +
                " tools I currently see.\n\nA few you might try:\n\n" +
                sample +
                "\n\n— *Munin*",
            }
          : m
      ),
    }));
  },

  appendToolCallToMessage: (messageId, call) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === messageId
          ? { ...m, toolCalls: [...(m.toolCalls || []), call], thinking: false }
          : m
      ),
    })),

  updateToolCall: (messageId, callId, patch) =>
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === messageId
          ? {
              ...m,
              toolCalls: (m.toolCalls || []).map((c) =>
                c.id === callId ? { ...c, ...patch } : c
              ),
            }
          : m
      ),
    })),
}));

// --- helpers ---

function conversationMessagesToChat(rows: any[], artifacts: ConversationArtifact[]): ChatMessage[] {
  const artifactsByMessage = new Map<string, ConversationArtifact[]>();
  for (const artifact of artifacts) {
    const key = String(artifact.message_id);
    artifactsByMessage.set(key, [...(artifactsByMessage.get(key) || []), artifact]);
  }
  return rows
    .filter((row) => row && ["user", "assistant", "tool"].includes(String(row.role)))
    .map((row) => {
      const metadata = row.metadata && typeof row.metadata === "object" ? row.metadata : {};
      const timestamp = Date.parse(String(row.created_at || ""));
      const toolCalls = Array.isArray(metadata.tool_calls)
        ? metadata.tool_calls.map((call: any, index: number) => ({
            id: `persisted-${row.id}-${index}`,
            name: String(call?.name || "tool"),
            arguments: call?.arguments && typeof call.arguments === "object" ? call.arguments : {},
            status: call?.ok === false ? "error" : "success",
            startTime: Number.isFinite(timestamp) ? timestamp : Date.now(),
            endTime: Number.isFinite(timestamp) ? timestamp : Date.now(),
            result: call?.result,
            error: call?.error,
          }))
        : undefined;
      return {
        id: String(row.id),
        role: row.role,
        content: String(row.content || ""),
        toolCalls,
        artifacts: artifactsByMessage.get(String(row.id)) || [],
        timestamp: Number.isFinite(timestamp) ? timestamp : Date.now(),
      } as ChatMessage;
    });
}

function parseSlashCommand(input: string): {
  name: string;
  args: Record<string, any>;
} {
  // Remove leading slash, split on whitespace
  const stripped = input.replace(/^\//, "");
  const parts = stripped.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
  if (parts.length === 0) return { name: "", args: {} };
  const name = parts[0] ?? "";
  const args: Record<string, any> = {};
  for (let i = 1; i < parts.length; i++) {
    const p = parts[i];
    if (!p) continue;
    const eq = p.indexOf("=");
    if (eq === -1) {
      // positional — try to fill by index later; skip for now
      args[`_pos_${i}`] = coerce(p);
    } else {
      const k = p.slice(0, eq);
      let v = p.slice(eq + 1);
      v = v.replace(/^"(.*)"$/, "$1").replace(/^'(.*)'$/, "$1");
      args[k] = coerce(v);
    }
  }
  return { name, args };
}

/**
 * Start a ReAct conversation as a server-side job and render its observable
 * lifecycle as it happens. We deliberately expose execution events (model
 * request/tool start/tool result), not private model chain-of-thought.
 */
async function runMuninChatJob(
  assistantId: string,
  message: string,
  conversationId: string,
  set: (fn: (s: MuninState) => Partial<MuninState>) => void,
  get: () => MuninState
) {
  const callId = uuid();
  const startTime = Date.now();
  get().appendToolCallToMessage(assistantId, {
    id: callId,
    name: "munin_chat",
    arguments: { message },
    status: "running",
    startTime,
    result: { status: "starting", progress: [] },
  });

  const client = getMcpClient({ baseUrl: get().mcpUrl, token: get().mcpToken });
  try {
    const started = await client.callTool("munin_chat", {
      message,
      mode: "async",
      conversation_id: conversationId,
    });
    const { json, text, isError } = extractToolResultContent(started);
    if (isError) throw new Error(text || "Munin rejected the conversation");
    const jobId = json?.data?.job_id || json?.job_id;
    if (!jobId) throw new Error("Munin did not return a conversation job ID");

    // A 60-iteration Evidence Mesh run can legitimately outlive twelve
    // minutes. Keep observing it for the same practical ceiling as the live
    // session instead of replacing a healthy in-flight conversation with a
    // false error at minute twelve.
    const deadline = Date.now() + 55 * 60_000;
    while (Date.now() < deadline) {
      await sleep(1_500);
      const statusResponse = await client.callTool("job_status", { job_id: jobId, include_result: true });
      const { json: statusJson, text: statusText, isError: statusError } = extractToolResultContent(statusResponse);
      if (statusError) throw new Error(statusText || "Unable to read Munin conversation status");
      const data = statusJson?.data || statusJson;
      const progress = Array.isArray(data?.progress) ? data.progress : [];
      get().updateToolCall(assistantId, callId, {
        result: { job_id: jobId, status: data?.status || "running", progress },
      });

      if (data?.status === "queued" || data?.status === "running") continue;

      const result = data?.result;
      const failed = data?.status !== "succeeded" || !result?.ok;
      if (failed) {
        throw new Error(result?.error?.message || data?.stderr_tail || `Conversation ${data?.status || "failed"}`);
      }

      const output = result?.data || {};
      const finishTime = Date.now();
      get().updateToolCall(assistantId, callId, {
        status: "success",
        endTime: finishTime,
        result: { job_id: jobId, ...output, progress },
      });
      for (const tool of output.tool_calls || []) {
        get().appendToolCallToMessage(assistantId, {
          id: uuid(),
          name: tool.name || "unknown_tool",
          arguments: tool.arguments || {},
          status: tool.ok === false ? "error" : "success",
          startTime: finishTime - (tool.elapsed_ms || 0),
          endTime: finishTime,
          result: tool.result,
          error: tool.error,
        });
      }
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                content: output.content || "(no response)",
                artifacts: Array.isArray(output.artifacts) ? output.artifacts : [],
                thinking: false,
              }
            : m
        ),
      }));
      void get().loadConversations();
      return;
    }
    throw new Error("Conversation is still running after 55 minutes; it continues on the server.");
  } catch (e: any) {
    get().updateToolCall(assistantId, callId, {
      status: "error",
      endTime: Date.now(),
      error: { code: "conversation_error", message: e?.message || String(e) },
    });
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === assistantId
          ? { ...m, content: `**Error** calling \`munin_chat\`: ${e?.message || String(e)}`, thinking: false }
          : m
      ),
    }));
  } finally {
    // A conversation can forge/register capabilities while it runs. Refresh
    // the client catalog as it settles so the new tool is usable immediately.
    void get().refreshTools();
    void get().refreshLive();
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function coerce(v: string): any {
  if (v === "true") return true;
  if (v === "false") return false;
  if (v === "null") return null;
  if (/^-?\d+$/.test(v)) return parseInt(v, 10);
  if (/^-?\d+\.\d+$/.test(v)) return parseFloat(v);
  if (
    (v.startsWith("{") && v.endsWith("}")) ||
    (v.startsWith("[") && v.endsWith("]"))
  ) {
    try {
      return JSON.parse(v);
    } catch {}
  }
  return v;
}

async function runToolCallInline(
  assistantId: string,
  toolName: string,
  args: Record<string, any>,
  set: (fn: (s: MuninState) => Partial<MuninState>) => void,
  get: () => MuninState
) {
  const L = log.chat;
  const callId = uuid();
  const done = L.time(`tool:${toolName}`);
  L.info(`runToolCallInline → ${toolName}`, { args });

  const call: ToolCall = {
    id: callId,
    name: toolName,
    arguments: args,
    status: "running",
    startTime: Date.now(),
  };
  get().appendToolCallToMessage(assistantId, call);

  const { mcpUrl, mcpToken } = get();
  const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });

  try {
    const result = await client.callTool(toolName, args);
    const { text, json, isError } = extractToolResultContent(result);
    if (isError) {
      const ms = done("tool-error");
      L.warn(`${toolName} returned isError=true in ${ms}ms`, { text, json });
      get().updateToolCall(assistantId, callId, {
        status: "error",
        endTime: Date.now(),
        error: {
          code: "tool_error",
          message: text || "Tool returned an error",
          data: json,
        },
      });
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === assistantId && !m.content
            ? { ...m, content: `Tool \`${toolName}\` returned an error.`, thinking: false }
            : m
        ),
      }));
    } else {
      const ms = done("ok");
      L.info(`${toolName} success in ${ms}ms`, {
        textLength: text.length,
        jsonType: json !== undefined ? typeof json : "none",
      });
      get().updateToolCall(assistantId, callId, {
        status: "success",
        endTime: Date.now(),
        result: json !== undefined ? json : text,
      });
      set((s) => ({
        messages: s.messages.map((m) =>
          m.id === assistantId && !m.content
            ? {
                ...m,
                content: text
                  ? truncateForInline(text)
                  : "Tool completed. See output below.",
                thinking: false,
              }
            : m
        ),
      }));
      if (["tool_forge", "graph_forge", "munin_wake"].includes(toolName)) {
        void get().refreshTools();
        void get().refreshLive();
      }
    }
  } catch (e: any) {
    done("exception");
    L.error(`${toolName} threw an exception`, e, {
      code: e?.code,
      hint: "Check DevTools Network tab for the failed request.",
    });
    get().updateToolCall(assistantId, callId, {
      status: "error",
      endTime: Date.now(),
      error: {
        code: e?.code ?? "exception",
        message: e?.message || String(e),
        data: e?.data,
      },
    });
    set((s) => ({
      messages: s.messages.map((m) =>
        m.id === assistantId && !m.content
          ? {
              ...m,
              content: `**Error** calling \`${toolName}\`: ${e?.message || String(e)}`,
              thinking: false,
            }
          : m
      ),
    }));
  }
}

function truncateForInline(s: string): string {
  // For long text results, embed as code block, truncated
  if (s.length > 2000) {
    return "```\n" + s.slice(0, 2000) + "\n…(truncated)\n```";
  }
  return "```\n" + s + "\n```";
}
