"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface JsonViewerProps {
  data: any;
  expanded?: boolean;
  maxExpandDepth?: number;
}

/**
 * Custom collapsible JSON viewer.
 * Keys = violet, strings = emerald, numbers = ice blue, booleans = amber, null = muted.
 */
export default function JsonViewer({
  data,
  expanded = true,
  maxExpandDepth = 3,
}: JsonViewerProps) {
  return (
    <div className="font-mono text-xs leading-relaxed overflow-auto">
      <JsonValue data={data} keyName={null} depth={0} expanded={expanded} maxExpandDepth={maxExpandDepth} />
    </div>
  );
}

function JsonValue({
  data,
  keyName,
  depth,
  expanded,
  maxExpandDepth,
}: {
  data: any;
  keyName: string | null;
  depth: number;
  expanded: boolean;
  maxExpandDepth: number;
}) {
  const isComplex = data !== null && typeof data === "object";
  const [open, setOpen] = useState(expanded && depth < maxExpandDepth);

  if (data === null) {
    return <Line keyName={keyName} value={<span className="json-null">null</span>} />;
  }
  if (typeof data === "boolean") {
    return (
      <Line keyName={keyName} value={<span className="json-boolean">{String(data)}</span>} />
    );
  }
  if (typeof data === "number") {
    return <Line keyName={keyName} value={<span className="json-number">{data}</span>} />;
  }
  if (typeof data === "string") {
    const escaped = JSON.stringify(data);
    return (
      <Line
        keyName={keyName}
        value={<span className="json-string">{escaped}</span>}
      />
    );
  }

  if (isComplex) {
    const isArray = Array.isArray(data);
    const entries = isArray
      ? data.map((v, i) => [i, v] as const)
      : Object.entries(data);
    const openBracket = isArray ? "[" : "{";
    const closeBracket = isArray ? "]" : "}";
    const summary = isArray ? `${entries.length} items` : `${entries.length} keys`;

    return (
      <div>
        <div className="flex items-start">
          <button
            onClick={() => setOpen((o) => !o)}
            className="flex items-center text-muted hover:text-body shrink-0"
            aria-label={open ? "Collapse" : "Expand"}
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </button>
          <div className="ml-1">
            {keyName !== null && (
              <span className="json-key">{JSON.stringify(keyName)}</span>
            )}
            {keyName !== null && <span className="text-muted">: </span>}
            <span className="text-muted">{openBracket}</span>
            {!open && (
              <span className="text-muted ml-1">
                {summary} {closeBracket}
              </span>
            )}
          </div>
        </div>
        {open && (
          <div className="ml-4 border-l border-border pl-3">
            {entries.length === 0 ? (
              <div className="text-muted">{isArray ? "[]" : "{}"}</div>
            ) : (
              entries.map(([k, v], idx) => (
                <div key={String(k) + idx} className="mt-0.5">
                  <JsonValue
                    data={v}
                    keyName={isArray ? null : (k as string)}
                    depth={depth + 1}
                    expanded={expanded}
                    maxExpandDepth={maxExpandDepth}
                  />
                </div>
              ))
            )}
          </div>
        )}
        {open && (
          <div className="text-muted">{closeBracket}</div>
        )}
      </div>
    );
  }

  return <Line keyName={keyName} value={<span>{String(data)}</span>} />;
}

function Line({ keyName, value }: { keyName: string | null; value: React.ReactNode }) {
  return (
    <div>
      {keyName !== null && (
        <>
          <span className="json-key">{JSON.stringify(keyName)}</span>
          <span className="text-muted">: </span>
        </>
      )}
      {value}
    </div>
  );
}
