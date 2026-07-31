import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ReasoningPartProps {
  id: string;
  text: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Renders a single reasoning block emitted by the Munin agent.
 * Displayed in an italicised, muted style with a small "thinking" icon to
 * distinguish it from final assistant messages.
 */
export function ReasoningPart({ text }: ReasoningPartProps) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-md border border-dashed border-muted px-3 py-2",
        "bg-muted/10 text-secondary"
      )}
      role="note"
      aria-label="Agent reasoning"
    >
      {/* Thinking indicator */}
      <span className="mt-0.5 shrink-0 text-xs" aria-hidden>
        💭
      </span>

      <p className={cn("text-sm italic leading-relaxed whitespace-pre-wrap")}>
        {text}
      </p>
    </div>
  );
}
