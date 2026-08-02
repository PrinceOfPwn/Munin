import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ArtifactPartProps {
  artifactId: string;
  mimeType: string;
  uri: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract a display filename from a URI or fall back to the artifact id. */
function displayName(uri: string, artifactId: string): string {
  try {
    const url = new URL(uri);
    const segments = url.pathname.split("/").filter(Boolean);
    if (segments.length > 0) return decodeURIComponent(segments[segments.length - 1]);
  } catch {
    // uri is not a full URL — try splitting on slashes
    const parts = uri.split("/").filter(Boolean);
    if (parts.length > 0) return parts[parts.length - 1];
  }
  return artifactId;
}

/** Return a short human-readable label for common MIME types. */
function mimeLabel(mimeType: string): string {
  const map: Record<string, string> = {
    "application/pdf": "PDF",
    "text/plain": "TXT",
    "text/html": "HTML",
    "text/csv": "CSV",
    "application/json": "JSON",
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/gif": "GIF",
    "application/zip": "ZIP",
    "application/octet-stream": "BIN",
  };
  return map[mimeType] ?? mimeType.split("/")[1]?.toUpperCase() ?? "FILE";
}

/** Validate URI to prevent script execution via javascript: or other unsafe protocols. */
function isSafeUri(uri: string): boolean {
  if (!uri) return false;

  const safeProtocols = ["http:", "https:", "data:", "blob:"];

  try {
    const url = new URL(uri, "https://safe.example");
    return safeProtocols.includes(url.protocol);
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Renders an artifact chip with filename, MIME type badge, and a download link.
 */
export function ArtifactPart({ artifactId, mimeType, uri }: ArtifactPartProps) {
  const filename = displayName(uri, artifactId);
  const label = mimeLabel(mimeType);
  const safeUri = isSafeUri(uri) ? uri : undefined;
  const artifactUri = safeUri || `/api/production/artifacts/${encodeURIComponent(artifactId)}?download=true`;
  const previewUri = `/api/production/artifacts/${encodeURIComponent(artifactId)}?inline=true`;
  const isImage = mimeType.toLowerCase().startsWith("image/");

  return (
    <div className="flex max-w-full flex-col items-start gap-2">
      {isImage && (
        <div className="overflow-hidden rounded-md border border-border bg-bg">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={previewUri} alt={filename} className="max-h-96 max-w-full object-contain" />
        </div>
      )}
      <a
      href={artifactUri}
      download={filename}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-2 text-sm",
        "transition-colors hover:bg-accent/10 hover:text-accent",
        !artifactUri && "pointer-events-none opacity-60"
      )}
      data-artifact-id={artifactId}
      aria-label={`Download ${filename}`}
    >
      {/* File icon */}
      <span aria-hidden className="text-base">
        📄
      </span>

      {/* Name and type */}
      <span className="flex flex-col leading-tight">
        <span className="font-medium text-body">{filename}</span>
        <span className="text-xs text-secondary">{mimeType}</span>
      </span>

      {/* MIME badge */}
      <span className="ml-auto rounded bg-raised px-1.5 py-0.5 text-xs font-mono text-muted">
        {label}
      </span>

      {/* Download arrow */}
      {artifactUri && (
        <span aria-hidden className="text-muted">
          ↓
        </span>
      )}
      </a>
    </div>
  );
}
