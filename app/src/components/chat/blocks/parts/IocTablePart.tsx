// tags: [ui-component, data-part, chat-stream-part, client-component, ioc-table, indicators-of-compromise, PR-5D, react-memo, lucide-icons, search-filter]
"use client";
// -----------------------------------------------------------------------------
// PR-5D — IOC table renderer for ``application/x-munin-ioc-table`` artifacts.
//
// Displays security indicators (IPs, domains, hashes, URLs, emails) with an
// interactive client-side search/filter. Parsing, classification and filtering
// are pure functions exported for unit tests; the component memoizes the
// derived rows and the filtered projection so typing in the search box never
// re-mounts rows (stable keys + referentially stable filtered arrays).
//
// Art direction: dark tokens only (``bg-*``/``border-*`` from
// ``tailwind.config.ts``), monospace for indicator values — machine content.
// -----------------------------------------------------------------------------
import { memo, useMemo, useState } from "react";
import { Search, ShieldCheck, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Indicator classification (pure)
// ---------------------------------------------------------------------------

export type IocKind =
  | "ipv4"
  | "ipv6"
  | "domain"
  | "url"
  | "email"
  | "md5"
  | "sha1"
  | "sha256"
  | "other";

const IPV4_RE =
  /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])$/;
// Canonical IPv6 (no zones, no embedded IPv4): full form, :: compression,
// and trailing-:: forms.
const IPV6_RE =
  /^(?:(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:(?:(?::[0-9a-fA-F]{1,4}){1,6})|:(?:(?::[0-9a-fA-F]{1,4}){1,7}|:))$/;
const MD5_RE = /^[a-fA-F0-9]{32}$/;
const SHA1_RE = /^[a-fA-F0-9]{40}$/;
const SHA256_RE = /^[a-fA-F0-9]{64}$/;
const DOMAIN_RE = /^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$/;
const URL_RE = /^https?:\/\/\S+$/i;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Map a raw indicator value to its best-known kind. */
export function classifyIoc(value: string): IocKind {
  const v = value.trim();
  if (IPV4_RE.test(v)) return "ipv4";
  if (IPV6_RE.test(v)) return "ipv6";
  if (SHA256_RE.test(v)) return "sha256";
  if (SHA1_RE.test(v)) return "sha1";
  if (MD5_RE.test(v)) return "md5";
  if (URL_RE.test(v)) return "url";
  if (EMAIL_RE.test(v)) return "email";
  if (DOMAIN_RE.test(v)) return "domain";
  return "other";
}

/** Normalize an explicit kind token from a CSV line ("hash" → "sha256" fallback etc.). */
function normalizeKindToken(token: string): IocKind | null {
  const t = token.trim().toLowerCase();
  if (t === "ip" || t === "ipv4") return "ipv4";
  if (t === "ipv6") return "ipv6";
  if (t === "domain" || t === "host") return "domain";
  if (t === "url" || t === "uri") return "url";
  if (t === "email") return "email";
  if (t === "md5") return "md5";
  if (t === "sha1") return "sha1";
  if (t === "sha256" || t === "hash") return "sha256";
  if (t === "other" || t === "unknown") return "other";
  return null;
}

export interface IocRow {
  /** Stable key: ``<index>-<value>`` — the row set is immutable per content. */
  id: string;
  value: string;
  kind: IocKind;
  source?: string;
}

/**
 * Parse raw IOC content into rows. One indicator per line; ``#`` starts a
 * comment. A line may carry an explicit ``value,kind,source`` triple (CSV
 * quoting not supported — values are trimmed and split on the first two
 * commas) or ``value|source``. Otherwise the kind is auto-classified.
 */
export function parseIocContent(content: string): IocRow[] {
  const rows: IocRow[] = [];
  const lines = String(content ?? "").split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.includes(",")) {
      const [value, kindToken, ...rest] = line.split(",");
      const valueTrimmed = value.trim();
      if (!valueTrimmed) continue;
      const explicit = normalizeKindToken(kindToken ?? "");
      rows.push({
        id: `${rows.length}-${valueTrimmed}`,
        value: valueTrimmed,
        kind: explicit ?? classifyIoc(valueTrimmed),
        source: rest.length > 0 ? rest.join(",").trim() || undefined : undefined,
      });
    } else if (line.includes("|")) {
      const [value, source] = line.split("|");
      const valueTrimmed = value.trim();
      if (!valueTrimmed) continue;
      rows.push({
        id: `${rows.length}-${valueTrimmed}`,
        value: valueTrimmed,
        kind: classifyIoc(valueTrimmed),
        source: source.trim() || undefined,
      });
    } else {
      rows.push({ id: `${rows.length}-${line}`, value: line, kind: classifyIoc(line) });
    }
  }
  return rows;
}

/**
 * Instant client-side filter: case-insensitive substring across value, kind
 * and source. An empty query returns the SAME array reference (the memoized
 * projection keeps its identity, so the row list does not re-render).
 */
export function filterIocRows(rows: IocRow[], query: string): IocRow[] {
  const q = query.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter(
    (row) =>
      row.value.toLowerCase().includes(q) ||
      row.kind.includes(q) ||
      (row.source ?? "").toLowerCase().includes(q),
  );
}

// ---------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------

const KIND_VARIANT: Record<IocKind, "info" | "warning" | "success" | "danger" | "neutral"> = {
  ipv4: "info",
  ipv6: "info",
  domain: "warning",
  url: "warning",
  email: "warning",
  md5: "neutral",
  sha1: "neutral",
  sha256: "neutral",
  other: "danger",
};

function kindLabel(kind: IocKind): string {
  return kind === "other" ? "?" : kind;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface IocTablePartProps {
  /** Raw indicator content (one indicator per line, see ``parseIocContent``). */
  content: string;
  filename?: string;
}

export const IocTablePart = memo(function IocTablePart({
  content,
  filename,
}: IocTablePartProps) {
  const [query, setQuery] = useState("");

  // Memoized derivation: rows are stable per content; the filtered
  // projection only recomputes when rows or the query change. Rows keep
  // stable keys, so typing never remounts the list (PR-4D discipline).
  const rows = useMemo(() => parseIocContent(content), [content]);
  const visible = useMemo(() => filterIocRows(rows, query), [rows, query]);

  return (
    <div className="flex w-full max-w-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1">
          <Search className="h-3.5 w-3.5 text-muted" aria-hidden />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="filter indicators…"
            aria-label="Filter indicators"
            className="w-48 bg-transparent text-xs text-body outline-none placeholder:text-muted"
          />
          {query ? (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear indicator filter"
              className="rounded p-0.5 text-muted transition-colors hover:bg-bg hover:text-body"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          ) : null}
        </div>
        <span className="text-xs text-muted">
          {visible.length} / {rows.length} indicators
          {filename ? (
            <>
              {" "}
              <span className="font-mono text-secondary">({filename})</span>
            </>
          ) : null}
        </span>
      </div>

      {visible.length === 0 ? (
        <div className="flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-4 text-xs text-muted">
          <ShieldCheck className="h-3.5 w-3.5 text-accent" aria-hidden />
          {rows.length === 0
            ? "No indicators parsed from this artifact."
            : "No indicators match the filter."}
        </div>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-md border border-border bg-surface">
          {visible.map((row) => (
            <li
              key={row.id}
              className="flex items-center gap-2 px-3 py-1.5 text-xs transition-colors hover:bg-bg/40"
            >
              <Badge variant={KIND_VARIANT[row.kind]} className="w-16 shrink-0 justify-center">
                {kindLabel(row.kind)}
              </Badge>
              <code className="min-w-0 flex-1 break-all font-mono text-body">{row.value}</code>
              {row.source ? (
                <span className="hidden max-w-48 truncate text-muted sm:inline" title={row.source}>
                  {row.source}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
