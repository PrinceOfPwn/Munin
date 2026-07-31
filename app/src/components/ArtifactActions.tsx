"use client";

import { Check, Copy, Download } from "lucide-react";
import { useState } from "react";

type ArtifactKind = "markdown" | "python" | "json" | "text";

export function artifactKindFromLanguage(language?: string): ArtifactKind {
  const normalized = (language || "").toLowerCase();
  if (["md", "markdown", "mdx"].includes(normalized)) return "markdown";
  if (["py", "python", "python3"].includes(normalized)) return "python";
  if (["json", "jsonc"].includes(normalized)) return "json";
  return "text";
}

function extension(kind: ArtifactKind) {
  return kind === "markdown" ? "md" : kind === "python" ? "py" : kind === "json" ? "json" : "txt";
}

function safeFilename(value?: string) {
  const cleaned = (value || "munin-artifact").replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "");
  return cleaned || "munin-artifact";
}

/** Download/copy controls for model and tool output. Content stays local to the browser. */
export default function ArtifactActions({
  content,
  language,
  filename,
}: {
  content: string;
  language?: string;
  filename?: string;
}) {
  const [copied, setCopied] = useState(false);
  const kind = artifactKindFromLanguage(language);
  const label = kind === "python" ? "Python" : kind === "markdown" ? "Markdown" : kind === "json" ? "JSON" : "Text";

  const copy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_600);
  };
  const download = () => {
    const blob = new Blob([content], { type: kind === "json" ? "application/json" : "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${safeFilename(filename)}.${extension(kind)}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex items-center gap-1.5 text-[10px] font-mono">
      <span className="text-muted uppercase tracking-wider">{label}</span>
      <button onClick={() => void copy()} className="artifact-action" title="Copy artifact">
        {copied ? <Check size={11} /> : <Copy size={11} />} {copied ? "Copied" : "Copy"}
      </button>
      <button onClick={download} className="artifact-action" title="Download artifact">
        <Download size={11} /> Download
      </button>
    </div>
  );
}
