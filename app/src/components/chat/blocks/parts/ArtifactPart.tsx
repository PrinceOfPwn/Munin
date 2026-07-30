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

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Renders an artifact chip with filename, MIME type badge, and a download link.
 */
export function ArtifactPart({ artifactId, mimeType, uri }: ArtifactPartProps) {
  const filename = displayName(uri, artifactId);
  const label = mimeLabel(mimeType);

  return (
    <a
      href={uri || undefined}
      download={filename}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm",
        "transition-colors hover:bg-accent hover:text-accent-foreground",
        !uri && "pointer-events-none opacity-60"
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
        <span className="font-medium text-foreground">{filename}</span>
        <span className="text-xs text-muted-foreground">{mimeType}</span>
      </span>

      {/* MIME badge */}
      <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
        {label}
      </span>

      {/* Download arrow */}
      {uri && (
        <span aria-hidden className="text-muted-foreground">
          ↓
        </span>
      )}
    </a>
  );
}
