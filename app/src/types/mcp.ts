export interface McpTool {
  name: string;
  description?: string;
  inputSchema?: {
    type?: string;
    properties?: Record<string, JsonSchemaProperty>;
    required?: string[];
    $schema?: string;
    additionalProperties?: boolean;
  };
}

export interface JsonSchemaProperty {
  type?: string;
  description?: string;
  default?: any;
  enum?: any[];
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
}

export interface McpToolResult {
  content?: Array<{
    type: string;
    text?: string;
    json?: any;
  }>;
  isError?: boolean;
  _raw?: any;
}

export type ToolCallStatus = "running" | "success" | "error";

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, any>;
  status: ToolCallStatus;
  result?: any;
  error?: { code: number | string; message: string; data?: any };
  startTime: number;
  endTime?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  toolCalls?: ToolCall[];
  artifacts?: ConversationArtifact[];
  thinking?: boolean;
  timestamp: number;
}

export interface ConversationSummary {
  id: string;
  title: string;
  summary?: string;
  created_at?: string;
  updated_at?: string;
  archived_at?: string;
  message_count?: number;
}

export interface ConversationArtifact {
  id: number | string;
  conversation_id: string;
  message_id: number | string;
  filename: string;
  language?: string;
  media_type?: string;
  content: string;
  created_at?: string;
}

export type ViewKey = "chat" | "tools" | "memory" | "soul" | "agents";

export interface AgentPresence {
  agent: string;
  status?: string;
  last_seen?: string | number;
  [k: string]: any;
}

export interface WakeItem {
  id?: string;
  target_agent?: string;
  priority?: number | string;
  task?: string;
  status?: string;
  claimed_by?: string | null;
  [k: string]: any;
}

export interface SemanticFact {
  key: string;
  value: string;
  updated_at?: string | number;
  [k: string]: any;
}

export interface EpisodicEvent {
  timestamp?: string | number;
  agent?: string;
  action?: string;
  summary?: string;
  [k: string]: any;
}

export interface ForgedGraph {
  name: string;
  purpose?: string;
  tool_whitelist?: string[] | string;
  [k: string]: any;
}

export interface SoulFile {
  name: string;
  path?: string;
}
