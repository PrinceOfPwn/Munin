"use client";

import { useState } from "react";
import { ShieldAlert } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/sonner";
import { useResolveHumanRequest } from "@/lib/queries";
import { messageFromError } from "@/lib/utils";
import type { HumanRequest } from "@/lib/production-api";

interface HitlRequestProps {
  request: HumanRequest;
}

/**
 * A pending human-in-the-loop decision inside the timeline.  Cannot be
 * ignored: it visually dominates with warning-coloured border-left and
 * either "Approve" / "Deny" buttons or, if the backend supplied its own
 * `choices` list, one button per choice.  A shared justification textarea
 * is forwarded as the resolution's `guidance` field.
 */
export function HitlRequest({ request }: HitlRequestProps) {
  const [guidance, setGuidance] = useState("");
  const resolve = useResolveHumanRequest();

  const choices = request.choices.length ? request.choices : ["approve", "deny"];
  const disabled = request.state !== "pending";

  async function send(choice: string) {
    try {
      await resolve.mutateAsync({
        requestId: request.id,
        choice,
        nonce: request.nonce || "",
        guidance,
      });
      toast.success(`Human decision recorded: ${choice}`);
    } catch (cause) {
      toast.error(messageFromError(cause));
    }
  }

  return (
    <div className="rounded-lg border-l-2 border-warning bg-warning/10 p-3">
      <div className="flex items-start gap-2">
        <ShieldAlert className="h-4 w-4 text-warning shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs font-semibold text-body">{request.action}</span>
            <Badge variant={request.risk === "high" ? "danger" : request.risk === "medium" ? "warning" : "neutral"}>
              risk · {request.risk}
            </Badge>
            <Badge variant={disabled ? "success" : "warning"}>{request.state}</Badge>
          </div>
          {request.detail && <p className="mt-2 text-xs text-secondary">{request.detail}</p>}
          {!disabled && (
            <div className="mt-3 flex flex-wrap gap-2">
              {choices.map((choice) => (
                <ChoiceButton
                  key={choice}
                  choice={choice}
                  onConfirm={send}
                  guidance={guidance}
                  setGuidance={setGuidance}
                  pending={resolve.isPending}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ChoiceButton({
  choice,
  onConfirm,
  guidance,
  setGuidance,
  pending,
}: {
  choice: string;
  onConfirm: (choice: string) => Promise<void>;
  guidance: string;
  setGuidance: (value: string) => void;
  pending: boolean;
}) {
  const destructive = choice === "deny" || choice === "reject" || choice === "abort";
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button size="sm" variant={destructive ? "outline" : "primary"} disabled={pending}>
          {choice}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Confirm operator decision: {choice}</AlertDialogTitle>
          <AlertDialogDescription>
            This decision is signed by your session, single-use, and persisted before the run resumes.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2">
          <Label htmlFor="hitl-guidance">Justification (optional)</Label>
          <Textarea
            id="hitl-guidance"
            rows={3}
            value={guidance}
            onChange={(event) => setGuidance(event.target.value)}
            placeholder="Anything the run should remember about this decision…"
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Back</AlertDialogCancel>
          <AlertDialogAction onClick={() => void onConfirm(choice)}>Send</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
