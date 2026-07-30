"use client";

/**
 * Three-state pill switch above the composer.
 *
 *   Turn     — commits a new user turn.  Disabled when a run is non-terminal
 *              because a fresh turn would race the active run.
 *   Guidance — appends to `run_guidance_queue`; injected on the next ReAct
 *              iteration.  Enabled while a run is running/queued/waiting.
 *   Note     — appends to `conversation_notes`; never reaches the model.
 *              Always enabled.
 *
 * The component is pure UI; the parent decides how the current mode affects
 * `submit()`.
 */
import { MessageSquare, Sparkles, StickyNote } from "lucide-react";
import { cn } from "@/lib/utils";

export type ComposerMode = "turn" | "guidance" | "note";

interface Props {
  mode: ComposerMode;
  onModeChange: (mode: ComposerMode) => void;
  runActive: boolean;
}

interface Option {
  id: ComposerMode;
  label: string;
  Icon: typeof MessageSquare;
  hint: string;
}

const OPTIONS: Option[] = [
  { id: "turn", label: "Turn", Icon: MessageSquare, hint: "New question for Munin" },
  { id: "guidance", label: "Guidance", Icon: Sparkles, hint: "Nudge the active run" },
  { id: "note", label: "Note", Icon: StickyNote, hint: "Sidebar-only, not sent" },
];

export function ComposerModeToggle({ mode, onModeChange, runActive }: Props) {
  return (
    <div
      role="radiogroup"
      aria-label="Composer mode"
      className="inline-flex items-center gap-0.5 rounded-md border border-border bg-raised p-0.5"
    >
      {OPTIONS.map(({ id, label, Icon, hint }) => {
        const disabled =
          (id === "turn" && runActive) || (id === "guidance" && !runActive);
        const active = id === mode;
        return (
          <button
            key={id}
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={
              id === "turn" && runActive
                ? "Turn disabled — run in progress. Use Guidance instead."
                : id === "guidance" && !runActive
                  ? "Guidance available while a run is active."
                  : hint
            }
            disabled={disabled}
            onClick={() => !disabled && onModeChange(id)}
            className={cn(
              "flex items-center gap-1.5 rounded px-2 py-1 text-[0.7rem] font-mono uppercase tracking-wider transition-colors",
              active
                ? "bg-accent text-white shadow-sm"
                : "text-secondary hover:text-body",
              disabled && "cursor-not-allowed opacity-40",
            )}
          >
            <Icon className="h-3 w-3" />
            {label}
          </button>
        );
      })}
    </div>
  );
}
