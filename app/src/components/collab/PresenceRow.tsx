"use client";

/**
 * Compact avatar row showing every active collaborator in the conversation.
 *
 * The dot in the corner of each avatar signals presence + typing:
 *   * green (idle)  — last_seen within the freshness window
 *   * amber (typing) — a `typing=true` heartbeat lands within the last 5s
 *
 * The row shows up to 4 avatars inline; overflow collapses to a "+N" chip
 * with a Popover listing the rest.  This keeps the header uncluttered while
 * still surfacing large operator crowds.
 */
import { Users } from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { PresenceEntry } from "@/lib/production-api";

interface PresenceRowProps {
  presence: PresenceEntry[];
  className?: string;
}

const MAX_INLINE = 4;

export function PresenceRow({ presence, className }: PresenceRowProps) {
  if (presence.length === 0) return null;
  const inline = presence.slice(0, MAX_INLINE);
  const overflow = presence.slice(MAX_INLINE);

  return (
    <TooltipProvider delayDuration={150}>
      <div className={"flex items-center gap-1 " + (className || "")}>
        {inline.map((entry) => (
          <Tooltip key={entry.actor_id}>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <Avatar
                  name={entry.actor_username || entry.actor_id}
                  size="sm"
                  ring={entry.typing ? "amber" : "green"}
                />
              </span>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <span className="font-mono text-[0.7rem]">
                {entry.actor_username || entry.actor_id}
                {entry.typing ? " · typing" : " · idle"}
              </span>
            </TooltipContent>
          </Tooltip>
        ))}
        {overflow.length > 0 && (
          <Popover>
            <PopoverTrigger className="inline-flex h-6 items-center gap-1 rounded-full border border-border bg-raised px-1.5 text-[0.65rem] text-secondary transition-colors hover:text-body">
              <Users className="h-3 w-3" />+{overflow.length}
            </PopoverTrigger>
            <PopoverContent align="end" className="w-56 p-2">
              <div className="mb-1 font-mono text-[0.65rem] uppercase tracking-wider text-muted">
                Also present
              </div>
              <ul className="space-y-1">
                {overflow.map((entry) => (
                  <li key={entry.actor_id} className="flex items-center gap-2 text-xs">
                    <Avatar
                      name={entry.actor_username || entry.actor_id}
                      size="xs"
                      ring={entry.typing ? "amber" : "green"}
                    />
                    <span className="truncate">
                      {entry.actor_username || entry.actor_id}
                    </span>
                    {entry.typing && (
                      <span className="ml-auto text-[0.65rem] text-warning">typing</span>
                    )}
                  </li>
                ))}
              </ul>
            </PopoverContent>
          </Popover>
        )}
      </div>
    </TooltipProvider>
  );
}
