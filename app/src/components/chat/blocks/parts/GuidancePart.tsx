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
 * Uses a blue left-border accent and italic text to distinguish it from
 * agent-authored reasoning and operator notes.
 */
export function GuidancePart({ text }: GuidancePartProps) {
  return (
    <div
      className={cn(
        "border-l-4 border-blue-400 bg-blue-50 px-3 py-2 text-sm",
        "dark:border-blue-500 dark:bg-blue-950/40"
      )}
      role="note"
      aria-label="Operator guidance"
    >
      <p className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-400">
        Guidance
      </p>
      <p className="italic text-foreground">{text}</p>
    </div>
  );
}
