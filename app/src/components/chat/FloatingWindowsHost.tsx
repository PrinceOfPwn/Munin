"use client";

/**
 * Host component that mounts one instance per open floating window.
 *
 * The store in :file:`floatingWindows.ts` keeps only the metadata a window
 * needs to bootstrap (id, kind, runId, subagentId, profileId).  The host
 * looks up the live subagent + run detail from TanStack Query cache and
 * hands both down to `<ForgeFloatingChat>`.
 *
 * Mount this component ONCE, near the top of the layout tree (inside
 * `<Desk>` in :file:`FlightDeck.tsx`).  Duplicate mounts would render each
 * window twice.
 */
import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  closeFloatingWindow,
  useFloatingWindows,
  type FloatingWindowEntry,
} from "@/store/floatingWindows";
import { ForgeFloatingChat } from "./ForgeFloatingChat";
import { FloatingWindow } from "@/components/ui/floating-window";
import type { RunDetail } from "@/lib/production-api";

export function FloatingWindowsHost() {
  const windows = useFloatingWindows();
  const qc = useQueryClient();

  const resolved = useMemo(() => {
    return windows.map((entry) => {
      const detail = qc.getQueryData<RunDetail>(["run", entry.runId, "detail"]);
      const subagent = detail?.subagents.find((s) => s.id === entry.subagentId);
      return { entry, detail, subagent };
    });
  }, [windows, qc]);

  if (windows.length === 0) return null;

  return (
    <>
      {resolved.map(({ entry, detail, subagent }) =>
        subagent ? (
          <ForgeFloatingChat
            key={entry.id}
            windowId={entry.id}
            runId={entry.runId}
            subagent={subagent}
            runDetail={detail}
            onClose={() => closeFloatingWindow(entry.id)}
          />
        ) : (
          // Subagent not yet loaded — render a lightweight placeholder so
          // the window still respects operator's open state.
          <MissingSubagentPlaceholder
            key={entry.id}
            entry={entry}
            onClose={() => closeFloatingWindow(entry.id)}
          />
        ),
      )}
    </>
  );
}

function MissingSubagentPlaceholder({
  entry,
  onClose,
}: {
  entry: FloatingWindowEntry;
  onClose: () => void;
}) {
  return (
    <FloatingWindow
      id={entry.id}
      title={entry.subagentProfileId}
      onClose={onClose}
      defaultSize={{ width: 360, height: 200 }}
    >
      <div className="p-3 text-xs text-muted">Loading subagent trace…</div>
    </FloatingWindow>
  );
}
