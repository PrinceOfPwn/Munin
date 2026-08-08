// tags: [ui-component, data-part, chat-stream-part, artifact-part, react-memo, PR-4A, PR-4E, optional-chaining, PR-5B, PR-5D, block-registry]
import { memo } from "react";
import { cn } from "@/lib/utils";
import { BlockRendererFor, lookupBlockRenderer, normalizeMediaType } from "../registry";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ArtifactPartProps {
  artifactId: string;
  mimeType?: string;
  uri?: string;
  /**
   * PR-5B/5D — optional artifact body. When present and the media type has a
   * registered block renderer (markdown, code, json/csv/table, mermaid,
   * sandboxed-html, IOC table), the body is rendered by that renderer instead
   * of the plain chip. Absent in the live stream (pointer-only), populated by
   * consumers holding the full read-model payload (e.g. run-detail views).
   */
  content?: string;
  /** PLAN-6 rich metadata forwarded to block renderers that display them. */
  previewUrl?: string;
  downloadUrl?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Extract a display filename from a URI or fall back to the artifact id. */
function displayName(uri: string | undefined, artifactId: string): string {
  if (!uri) return artifactId;
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
function mimeLabel(mimeType: string | undefined): string {
  if (!mimeType) return "FILE";
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
function isSafeUri(uri: string | undefined): boolean {
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
export const ArtifactPart = memo(function ArtifactPart({
  artifactId,
  mimeType,
  uri,
  content,
  previewUrl,
  downloadUrl,
}: ArtifactPartProps) {
  const filename = displayName(uri, artifactId);
  const label = mimeLabel(mimeType);
  const safeUri = isSafeUri(uri) ? uri : undefined;
  const artifactUri = safeUri || `/api/production/artifacts/${encodeURIComponent(artifactId)}?download=true`;
  const previewUri = `/api/production/artifacts/${encodeURIComponent(artifactId)}?inline=true`;
  const isImage = mimeType?.toLowerCase().startsWith("image/") ?? false;

  // PR-5B/5D — a body-bearing artifact whose media type is registered in the
  // block registry is rendered by its block renderer (sandboxed-html →
  // SandboxedPreview, mermaid → MermaidPart, IOC table → IocTablePart, …).
  // Images and pointer-only artifacts keep the native chip below. The
  // registry itself handles unknown types with logError + a fallback card.
  const hasBody = typeof content === "string" && content.length > 0;
  const blockRenderable = hasBody && lookupBlockRenderer(mimeType) !== null;
  if (blockRenderable) {
    return (
      <BlockRendererFor
        mediaType={mimeType ?? ""}
        data={{
          media_type: normalizeMediaType(mimeType),
          content,
          ...(previewUrl ? { preview_url: previewUrl } : {}),
          ...(downloadUrl ? { download_url: downloadUrl } : {}),
        }}
        extraProps={{ filename }}
      />
    );
  }

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
        <span className="text-xs text-secondary">{mimeType ?? "unknown"}</span>
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
});
