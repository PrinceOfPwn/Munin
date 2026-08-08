// tags: [ui-component, data-part, chat-stream-part, note-part, react-memo, PR-4A]
import { memo } from "react";
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
export const NotePart = memo(function NotePart({ text }: NotePartProps) {
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
});
