// tags: [ui-component, data-part, chat-stream-part, client-component, mermaid, diagram-renderer, PR-5D, react-memo, lazy-load]
"use client";
// -----------------------------------------------------------------------------
// PR-5D — Mermaid diagram renderer.
//
// The diagram engine (``mermaid`` ^11.16) is a heavy dependency, so it is
// imported lazily INSIDE the render effect (dynamic import in a client
// component splits it into its own chunk — the main bundle never loads it).
// ``securityLevel: "strict"`` keeps the engine's own HTML sanitizer on.
//
// Failure contract: any render error is surfaced via ``logError`` and the
// diagram degrades to a monospace fenced code block — never a silent catch.
// The effect is cancelled on unmount / content change so a superseded
// diagram cannot paint into a stale container.
// -----------------------------------------------------------------------------
import { memo, useEffect, useRef, useState } from "react";

import { logError } from "@/lib/logError";
import { cn } from "@/lib/utils";

export interface MermaidPartProps {
  /** Raw mermaid diagram source (e.g. a ``graph TD`` / ``sequenceDiagram``). */
  content: string;
  filename?: string;
}

export const MermaidPart = memo(function MermaidPart({
  content,
  filename,
}: MermaidPartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [diagramId] = useState(
    () => `mermaid-${Math.random().toString(36).slice(2, 10)}`,
  );
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setFailed(false);

    async function renderDiagram(): Promise<void> {
      const container = containerRef.current;
      if (!container || cancelled) return;
      try {
        const { default: mermaid } = await import("mermaid");
        if (cancelled || !containerRef.current) return;
        mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
        const { svg } = await mermaid.render(diagramId, content);
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML = svg;
      } catch (error) {
        if (cancelled) return;
        logError({
          context: "mermaid_render",
          error,
          meta: { diagramId, filename },
          ts: new Date().toISOString(),
        });
        setFailed(true);
      }
    }

    void renderDiagram();
    return () => {
      cancelled = true;
    };
  }, [content, diagramId, filename]);

  return (
    <div className="flex w-full max-w-full flex-col gap-2">
      {failed ? (
        <div className="flex flex-col gap-2">
          <p
            role="alert"
            aria-label="Diagram render failed"
            className="rounded-md border border-warning/40 bg-warning/5 px-3 py-1.5 text-xs text-warning"
          >
            Diagram failed to render — showing source
          </p>
          <pre
            className={cn(
              "max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md",
              "border border-border bg-bg p-3 font-mono text-xs leading-relaxed text-secondary",
            )}
          >
            {content}
          </pre>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="max-w-full overflow-auto rounded-md border border-border bg-surface p-3"
        />
      )}
    </div>
  );
});
