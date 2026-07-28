"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { ArrowDown, Send, Terminal } from "lucide-react";
import Raven from "./Raven";
import ToolCallCard from "./ToolCallCard";
import ArtifactActions, { artifactKindFromLanguage } from "./ArtifactActions";
import EmptyState from "./EmptyState";
import { useMuninStore } from "@/store/muninStore";
import { cn } from "@/lib/utils";

export default function Chat() {
  const messages = useMuninStore((s) => s.messages);
  const chatInput = useMuninStore((s) => s.chatInput);
  const setChatInput = useMuninStore((s) => s.setChatInput);
  const sendChatMessage = useMuninStore((s) => s.sendChatMessage);
  const connected = useMuninStore((s) => s.live.mcpConnected);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);

  // Scroll to bottom on new messages if user is at bottom
  useEffect(() => {
    if (atBottom && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, atBottom]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setAtBottom(distance < 80);
  };

  const jumpToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      setAtBottom(true);
    }
  };

  const handleSend = () => {
    sendChatMessage();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Message stream */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto px-4 py-6"
      >
        <div className="max-w-3xl mx-auto space-y-4">
          {messages.length === 0 ? (
            <EmptyState
              message="Munin awaits your words."
              hint="Type below. Use /tool_name to invoke a tool directly."
            />
          ) : (
            messages.map((m) => <MessageBubble key={m.id} message={m} />)
          )}
        </div>
      </div>

      {/* Jump to bottom FAB */}
      {!atBottom && (
        <button
          onClick={jumpToBottom}
          className="absolute right-8 bottom-24 z-10 w-10 h-10 rounded-full bg-surface border border-border flex items-center justify-center text-accent hover:bg-accent/10 shadow-lg"
          aria-label="Jump to bottom"
        >
          <ArrowDown size={18} />
        </button>
      )}

      {/* Input */}
      <div className="border-t border-border bg-surface/60 px-4 py-3">
        <div className="max-w-3xl mx-auto">
          <div className="flex items-end gap-2">
            <div className="flex-1 relative">
              <textarea
                id="munin-chat-input"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={2}
                placeholder={
                  connected
                    ? "Speak to Munin…  /tool_name key=value  ·  Ctrl+Enter to send"
                    : "Configure MCP server in Settings to begin…"
                }
                className="w-full resize-none bg-bg border border-border rounded px-3 py-2 pr-10 text-sm font-mono text-body focus:outline-none focus:border-accent/60 placeholder:text-muted"
              />
              <Terminal
                size={14}
                className="absolute right-3 top-3 text-muted pointer-events-none"
              />
            </div>
            <button
              onClick={handleSend}
              disabled={!chatInput.trim()}
              className="px-4 py-2 h-[60px] rounded bg-accent/20 border border-accent/50 text-accent hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1.5 font-mono text-sm uppercase tracking-wider"
            >
              <Send size={14} /> Send
            </button>
          </div>
          <div className="text-[10px] text-muted mt-1 font-mono">
            Ctrl+Enter to send · "/" focuses input from anywhere
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: import("@/types/mcp").ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end animate-fade-slide">
        <div className="max-w-[85%] bg-accent/20 border border-accent/40 rounded-full px-4 py-2 text-body text-sm">
          {message.content}
        </div>
      </div>
    );
  }

  // Assistant
  return (
    <div className="flex gap-3 animate-fade-slide">
      <div className="shrink-0 mt-1">
        <Raven size={28} className="text-body" eyeColor="#7c3aed" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="bg-surface border border-border rounded-md px-3 py-2.5">
          {message.thinking ? (
            <ThinkingLine />
          ) : message.content ? (
            <div className="prose-munin">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={{
                  pre({ children }) {
                    // Code blocks are first-class local artifacts: the user can keep a
                    // generated report/script without copying the whole conversation.
                    const child = Array.isArray(children) ? children[0] : children;
                    const props = (child as any)?.props;
                    const className = String(props?.className || "");
                    const language = className.match(/language-([\w+-]+)/)?.[1];
                    const value = typeof props?.children === "string" ? props.children.replace(/\n$/, "") : "";
                    const useful = value.length > 0 && artifactKindFromLanguage(language) !== "text";
                    return (
                      <div className="munin-code-artifact">
                        {useful && <div className="mb-1 flex justify-end"><ArtifactActions content={value} language={language} /></div>}
                        <pre>{children}</pre>
                      </div>
                    );
                  },
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          ) : null}

          {message.toolCalls && message.toolCalls.length > 0 && (
            <div className="mt-2 space-y-1">
              {message.toolCalls.map((tc) => (
                <ToolCallCard key={tc.id} call={tc} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ThinkingLine() {
  return (
    <div className="flex items-center gap-2 text-muted text-sm font-mono">
      <span className="flex items-center gap-1">
        <span className="thinking-dot animate-feather" />
        <span
          className="thinking-dot animate-feather"
          style={{ animationDelay: "0.2s" }}
        />
        <span
          className="thinking-dot animate-feather"
          style={{ animationDelay: "0.4s" }}
        />
      </span>
      <span className="italic">thinking…</span>
    </div>
  );
}
