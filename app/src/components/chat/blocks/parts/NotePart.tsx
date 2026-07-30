import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface NotePartProps {
  text: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Displays an operator note with an amber left-border accent.
 * Notes are informational messages injected by a human operator into the run.
 */
export function NotePart({ text }: NotePartProps) {
  return (
    <div
      className={cn(
        "border-l-4 border-amber-400 bg-amber-50 px-3 py-2 text-sm",
        "dark:border-amber-500 dark:bg-amber-950/40"
      )}
      role="note"
      aria-label="Operator note"
    >
      <p className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
        Note
      </p>
      <p className="text-foreground">{text}</p>
    </div>
  );
}
