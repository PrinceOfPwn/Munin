"use client";

/**
 * Owner-only dialog to inspect and grow the collaborator roster for a
 * conversation.
 *
 * The trigger is a small icon button in the conversation header; only shown
 * to the actor that owns the conversation (checked against the currently
 * loaded collaborator list — if the current actor's row has role="owner",
 * the button renders).  Non-owners see PresenceRow only.
 *
 * The dialog is intentionally minimal — no email invites, no seat
 * management, just add-by-username with a role.  It is the smallest
 * primitive that unblocks "two operators can act on the same conversation"
 * without recreating the entire IAM story.
 */
import { useState } from "react";
import { UserPlus, Users } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Avatar } from "@/components/ui/avatar";
import { toast } from "@/components/ui/sonner";
import { formatWhen, messageFromError } from "@/lib/utils";
import type { Actor, Collaborator } from "@/lib/production-api";
import { useAddCollaborator, useCollaborators } from "@/lib/useCollab";

interface CollaboratorManagerProps {
  conversationId: string;
  actor: Actor;
}

export function CollaboratorManager({ conversationId, actor }: CollaboratorManagerProps) {
  const list = useCollaborators(conversationId);
  const add = useAddCollaborator(conversationId);
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<Collaborator["role"]>("collaborator");

  const roster = list.data ?? [];
  const isOwner =
    roster.some((row) => row.actor_id === actor.id && row.role === "owner") ||
    // Fallback for freshly-migrated conversations where the backend surfaces
    // an owner from `conversations.owner_id` only.
    roster.length === 0;

  // Non-owner: render nothing.
  if (!isOwner && actor.role !== "admin") return null;

  async function submit() {
    const value = username.trim().toLowerCase();
    if (!value) return;
    try {
      await add.mutateAsync({ username: value, role });
      setUsername("");
      toast.success(`Added ${value} as ${role}`);
    } catch (cause) {
      toast.error(messageFromError(cause));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <UserPlus className="h-3.5 w-3.5" />
          Collaborators
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Users className="h-4 w-4" /> Collaborators
          </DialogTitle>
          <DialogDescription>
            Operators listed here can post guidance, notes, and (with the
            <span className="mx-1 font-mono">collaborator</span>
            role) can act on human-in-the-loop requests. Only owners can
            change the roster.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {roster.map((row) => (
            <div
              key={row.actor_id}
              className="flex items-center gap-2 rounded border border-border bg-raised/50 px-2 py-1.5"
            >
              <Avatar name={row.actor_username} size="sm" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-xs font-medium">
                  {row.actor_username}
                </div>
                <div className="text-[0.65rem] text-muted">
                  since {formatWhen(row.added_at_ms)}
                </div>
              </div>
              <Badge variant={row.role === "owner" ? "success" : "neutral"}>
                {row.role}
              </Badge>
            </div>
          ))}
          {roster.length === 0 && (
            <p className="text-xs text-muted">No collaborators yet.</p>
          )}
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
          className="grid grid-cols-[1fr_auto_auto] items-end gap-2 border-t border-border pt-3"
        >
          <div>
            <Label htmlFor="collaborator-username">Username</Label>
            <Input
              id="collaborator-username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="alice"
              autoComplete="off"
            />
          </div>
          <div>
            <Label htmlFor="collaborator-role">Role</Label>
            <select
              id="collaborator-role"
              value={role}
              onChange={(event) => setRole(event.target.value as Collaborator["role"])}
              className="h-9 rounded-md border border-border bg-surface px-2 text-xs"
            >
              <option value="viewer">viewer</option>
              <option value="collaborator">collaborator</option>
              <option value="owner">owner</option>
            </select>
          </div>
          <Button type="submit" disabled={add.isPending || !username.trim()}>
            Add
          </Button>
        </form>

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
