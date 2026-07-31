import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GuidancePartProps {
  text: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Displays guidance injected by an operator into the run.
 * Uses an info left-border accent and italic text to distinguish it from
 * agent-authored reasoning and operator notes.
 */
export function GuidancePart({ text }: GuidancePartProps) {
  return (
    <div
      className={cn(
        "border-l-4 border-info/60 bg-info/10 px-3 py-2 text-sm"
      )}
      role="note"
      aria-label="Operator guidance"
    >
      <p className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-info">
        Guidance
      </p>
      <p className="italic text-body">{text}</p>
    </div>
  );
}
