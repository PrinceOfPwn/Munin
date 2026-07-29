"use client";

/**
 * Sidebar note posted by a collaborator.  Never reaches the model.
 *
 * Visual language:
 *   * Warm-secondary tint (post-it feel) so the operator can eyeball at a
 *     glance that this is *not* something the model saw.
 *   * "not sent to Munin" chip in the header keeps the boundary explicit.
 */
import { MessageSquareDashed } from "lucide-react";
import { Avatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { formatWhen } from "@/lib/utils";
import type { ConversationNote } from "@/lib/production-api";

interface Props {
  note: ConversationNote;
}

export function NoteBlock({ note }: Props) {
  return (
    <div className="rounded-md border border-secondary/30 bg-secondary/5 px-3 py-2">
      <header className="flex items-center gap-2">
        <Avatar name={note.actor_username || note.actor_id} size="xs" />
        <span className="flex items-center gap-1 font-mono text-[0.65rem] uppercase tracking-wider text-secondary">
          <MessageSquareDashed className="h-3 w-3" /> Operator note
        </span>
        <span className="text-[0.65rem] text-muted">
          {note.actor_username || note.actor_id} · {formatWhen(note.created_at_ms)}
        </span>
        <Badge variant="outline" className="ml-auto text-[0.6rem]">
          not sent to Munin
        </Badge>
      </header>
      <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-body">
        {note.body}
      </p>
    </div>
  );
}
