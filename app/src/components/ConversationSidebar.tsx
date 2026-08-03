// tags: [ui-component, console-surface, lucide-icons, client-component, use-conversations, use-effect, use-create-conversation, use-state, conversation-sidebar]
"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import {
  LoaderCircle,
  LogOut,
  Plus,
  RefreshCw,
  Search,
  UserRound,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "@/components/ui/sonner";

import { useConversations, useCreateConversation } from "@/lib/queries";
import type { Actor } from "@/lib/production-api";
import { cn, formatWhen, messageFromError } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ConversationSidebarProps {
  actor: Actor;
  activeConversationId: string | null;
  onSelect: (id: string) => void;
  onLogout: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * `ConversationSidebar` — Fase 1b extraction of FlightDeckStable's operations
 * rail. Renders the search box, the recent-conversations list, and the
 * "Ask Munin" (create) + logout buttons.
 *
 * No polling: only `useConversations` (staleTime 30s, refetch on focus/refresh
 * button). No presence, no collab, no notes — those live surfaces have been
 * dropped in the migration (Tabla 4 kill-list).
 */
export default function ConversationSidebar({
  actor,
  activeConversationId,
  onSelect,
  onLogout,
}: ConversationSidebarProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => clearTimeout(handle);
  }, [query]);

  const conversationsQuery = useConversations(debouncedQuery);
  const conversations = conversationsQuery.data || [];
  const createConversation = useCreateConversation();

  async function askMunin() {
    try {
      const conversation = await createConversation.mutateAsync(undefined);
      onSelect(conversation.id);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(
          "munin.activeConversationId",
          conversation.id,
        );
      }
    } catch (cause) {
      toast.error(messageFromError(cause));
    }
  }

  function selectConversation(id: string) {
    onSelect(id);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("munin.activeConversationId", id);
    }
  }

  return (
    <aside className="hidden min-h-0 flex-col gap-3 border-r border-border bg-surface p-3 lg:flex">
      <div className="flex items-center gap-2 px-1">
        <Image
          src="/raven-mark.png"
          width={24}
          height={24}
          alt=""
          className="rounded-sm"
        />
        <div className="flex flex-col leading-tight">
          <b className="text-xs font-semibold tracking-wider">MUNIN</b>
          <small className="text-[0.65rem] text-muted">Agent Console</small>
        </div>
      </div>

      <Button
        onClick={() => void askMunin()}
        disabled={createConversation.isPending}
        className="w-full justify-center"
      >
        {createConversation.isPending ? (
          <LoaderCircle className="h-4 w-4 animate-spin" />
        ) : (
          <Plus className="h-4 w-4" />
        )}
        Ask Munin
      </Button>

      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="flex items-center justify-between px-1 pb-1 text-[0.65rem] font-semibold uppercase tracking-wider text-muted">
          Recent operations
          <Button
            variant="ghost"
            size="icon"
            onClick={() => void conversationsQuery.refetch()}
            aria-label="Refresh operations"
            className="h-5 w-5"
          >
            <RefreshCw
              className={cn(
                "h-3 w-3",
                conversationsQuery.isFetching && "animate-spin",
              )}
            />
          </Button>
        </div>
        <div className="flex items-center gap-1.5 rounded border border-border bg-bg px-2">
          <Search className="h-3.5 w-3.5 text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search durable archive"
            className="w-full bg-transparent py-1.5 text-xs placeholder:text-muted focus:outline-none"
          />
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <ul className="flex flex-col">
            {conversations.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => selectConversation(item.id)}
                  className={cn(
                    "flex w-full flex-col gap-0.5 border-b border-border/50 px-2 py-2 text-left text-xs transition-colors",
                    activeConversationId === item.id
                      ? "bg-active text-body"
                      : "text-secondary hover:bg-raised hover:text-body",
                  )}
                >
                  <span className="truncate">{item.title}</span>
                  <small className="text-[0.65rem] text-muted">
                    {item.message_count} objects ·{" "}
                    {formatWhen(item.last_activity_at_ms)}
                  </small>
                </button>
              </li>
            ))}
            {!conversations.length && conversationsQuery.isFetching && (
              <li className="flex items-center gap-2 p-3 text-xs text-muted">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> Loading
                operations…
              </li>
            )}
            {!conversations.length && !conversationsQuery.isFetching && (
              <li className="p-3 text-xs leading-relaxed text-muted">
                No matching operations. Existing cached chats remain available
                when you clear the search.
              </li>
            )}
          </ul>
        </ScrollArea>
      </div>

      <div className="mt-auto border-t border-border pt-3">
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-between"
          onClick={() => void onLogout()}
        >
          <span className="flex items-center gap-1.5">
            <UserRound className="h-3.5 w-3.5" />
            {actor.username}
          </span>
          <LogOut className="h-3.5 w-3.5" />
        </Button>
      </div>
    </aside>
  );
}
