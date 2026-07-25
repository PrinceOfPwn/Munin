"use client";

import { create } from "zustand";
import { uuid } from "@/lib/utils";
import { getMcpClient, extractToolResultContent } from "@/lib/mcp";
import { log } from "@/lib/logger";
import type {
  ViewKey,
  ChatMessage,
  ToolCall,
  McpTool,
  AgentPresence,
  WakeItem,
  EpisodicEvent,
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
  sendChatMessage: () => Promise<void>;
  appendToolCallToMessage: (messageId: string, call: ToolCall) => void;
  updateToolCall: (messageId: string, callId: string, patch: Partial<ToolCall>) => void;
}

const DEFAULT_URL = typeof window !== "undefined" ? window.location.origin : "http://localhost:8890";
const DEFAULT_TOKEN = "munin2024";
const L = log.store;

function loadStoredConfig(): { url: string; token: string } {
  if (typeof window === "undefined") return { url: DEFAULT_URL, token: DEFAULT_TOKEN };
  try {
    const raw = window.localStorage.getItem("munin.config");
    if (raw) {
      const parsed = JSON.parse(raw);
      const url = parsed.url || DEFAULT_URL;
      L.info("Config loaded from localStorage", { url, hasToken: !!parsed.token });
      return { url, token: parsed.token || DEFAULT_TOKEN };
    }
  } catch (e) {
    L.warn("Failed to load config from localStorage — using defaults", e);
  }
  L.info("No stored config — using defaults", { url: DEFAULT_URL });
  return { url: DEFAULT_URL, token: DEFAULT_TOKEN };
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
  mcpUrl: DEFAULT_URL,
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
  live: initialLive,

  init: () => {
    const { url, token } = loadStoredConfig();
    set({ mcpUrl: url, mcpToken: token });
    L.info("store.init — triggering initial fetches");
    // Fire and forget — errors are caught inside each fn
    get().refreshTools();
    get().refreshLive();
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
          if (Array.isArray(json)) next.presence = json;
          else if (json && Array.isArray(json.presence)) next.presence = json.presence;
          else if (json && Array.isArray(json.agents)) next.presence = json.agents;
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
          if (Array.isArray(json)) next.forgedToolCount = json.length;
          else if (json && Array.isArray(json.tools)) next.forgedToolCount = json.tools.length;
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
          const arr = Array.isArray(json)
            ? json
            : json && Array.isArray(json.items)
            ? json.items
            : json && Array.isArray(json.queue)
            ? json.queue
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
          if (Array.isArray(json) && json.length > 0) next.lastEpisodic = json[0];
          else if (json && Array.isArray(json.events) && json.events.length > 0)
            next.lastEpisodic = json.events[0];
          else if (json && !Array.isArray(json)) next.lastEpisodic = json;
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

    set((s) => ({
      messages: [...s.messages, userMsg, assistantMsg],
      chatInput: "",
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
      await runToolCallInline(
        assistantId,
        chatTool.name,
        { message: text, prompt: text, input: text, text },
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

function parseSlashCommand(input: string): {
  name: string;
  args: Record<string, any>;
} {
  // Remove leading slash, split on whitespace
  const stripped = input.replace(/^\//, "");
  const parts = stripped.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
  if (parts.length === 0) return { name: "", args: {} };
  const name = parts[0];
  const args: Record<string, any> = {};
  for (let i = 1; i < parts.length; i++) {
    const p = parts[i];
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
