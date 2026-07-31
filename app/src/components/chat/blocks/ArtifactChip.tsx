"use client";

import { Download, FileBox } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { formatBytes } from "@/lib/utils";
import { productionApi, type ArtifactRef } from "@/lib/production-api";

interface ArtifactChipProps {
  artifact: ArtifactRef;
}

/**
 * A small file chip: filename + size + language, with a download button that
 * hits the same-origin Next proxy so the session cookie carries the auth.
 */
export function ArtifactChip({ artifact }: ArtifactChipProps) {
  const href = productionApi.artifactDownloadUrl(artifact.id);
  return (
    <div className="inline-flex max-w-full items-center gap-2 rounded border border-border bg-raised px-2 py-1 text-xs">
      <FileBox className="h-3.5 w-3.5 text-accent shrink-0" />
      <span className="font-mono truncate text-body">{artifact.filename}</span>
      {artifact.language && <Badge variant="outline">{artifact.language}</Badge>}
      <span className="text-muted">{formatBytes(artifact.size_bytes)}</span>
      <a
        href={href}
        download={artifact.filename}
        className="ml-1 inline-flex items-center gap-1 text-secondary hover:text-body transition-colors"
      >
        <Download className="h-3.5 w-3.5" />
        <span className="sr-only">Download {artifact.filename}</span>
      </a>
    </div>
  );
}
