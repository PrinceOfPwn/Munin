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
 * Displays an operator note with a warning left-border accent.
 * Notes are informational messages injected by a human operator into the run.
 */
export function NotePart({ text }: NotePartProps) {
  return (
    <div
      className={cn(
        "border-l-4 border-warning/60 bg-warning/10 px-3 py-2 text-sm"
      )}
      role="note"
      aria-label="Operator note"
    >
      <p className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-warning">
        Note
      </p>
      <p className="text-body">{text}</p>
    </div>
  );
}
