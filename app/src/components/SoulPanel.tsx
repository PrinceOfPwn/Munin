"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { FileText, Pencil, X, Loader2, AlertTriangle } from "lucide-react";
import EmptyState from "./EmptyState";
import Drawer from "./Drawer";
import { useMuninStore } from "@/store/muninStore";
import { getMcpClient, extractToolResultContent } from "@/lib/mcp";
import { cn } from "@/lib/utils";

export default function SoulPanel() {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const loadFiles = async () => {
    setLoading(true);
    setError(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("soul_list", {});
      const { json, text } = extractToolResultContent(r);
      let arr: string[] = [];
      if (Array.isArray(json)) {
        arr = json.map((x: any) => (typeof x === "string" ? x : x.name || x.path || String(x)));
      } else if (json && Array.isArray(json.files)) {
        arr = json.files.map((x: any) => (typeof x === "string" ? x : x.name || x.path || String(x)));
      } else if (json && Array.isArray(json.items)) {
        arr = json.items.map((x: any) => (typeof x === "string" ? x : x.name || x.path || String(x)));
      } else if (text) {
        arr = text.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      }
      setFiles(arr);
      if (arr.length > 0 && !selected) setSelected(arr[0]);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const loadContent = async (name: string) => {
    setLoading(true);
    setError(null);
    setContent("");
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("soul_read", { name });
      const { text } = extractToolResultContent(r);
      setContent(text || "");
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mcpUrl, mcpToken]);

  useEffect(() => {
    if (selected) loadContent(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  return (
    <div className="flex-1 flex min-h-0">
      {/* File list */}
      <div className="w-56 shrink-0 border-r border-border overflow-y-auto">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="font-mono text-accent uppercase tracking-widest text-sm flex items-center gap-2">
            <FileText size={14} /> Soul
          </h2>
          <p className="text-[10px] text-muted mt-1">
            Munin's identity files.
          </p>
        </div>
        {loading && files.length === 0 ? (
          <div className="p-4 text-muted text-xs font-mono flex items-center gap-1.5">
            <Loader2 size={12} className="animate-spin" /> Loading…
          </div>
        ) : files.length === 0 ? (
          <div className="p-4 text-muted text-xs">No soul files.</div>
        ) : (
          <ul className="py-1">
            {files.map((f) => (
              <li key={f}>
                <button
                  onClick={() => setSelected(f)}
                  className={cn(
                    "w-full text-left px-4 py-1.5 text-sm font-mono truncate transition-colors border-l-2",
                    selected === f
                      ? "border-accent bg-accent/5 text-body"
                      : "border-transparent text-muted hover:text-body hover:bg-surface"
                  )}
                >
                  {f}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 flex flex-col">
        <div className="border-b border-border px-4 py-2 flex items-center justify-between">
          <div className="font-mono text-sm text-body">
            {selected || "—"}
          </div>
          <button
            onClick={() => setEditing(true)}
            disabled={!selected || !content}
            className="flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider px-2 py-1 rounded border border-border text-muted hover:text-accent hover:border-accent/50 disabled:opacity-40"
          >
            <Pencil size={12} /> Propose edit
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {error ? (
            <div className="text-xs text-rose font-mono border border-rose/40 bg-rose/5 px-3 py-2 rounded">
              {error}
            </div>
          ) : loading ? (
            <div className="text-muted text-sm font-mono flex items-center gap-1.5">
              <Loader2 size={14} className="animate-spin" /> Reading soul…
            </div>
          ) : !selected ? (
            <EmptyState message="Select a soul file." />
          ) : content ? (
            <div className="prose-munin max-w-3xl">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
              >
                {content}
              </ReactMarkdown>
            </div>
          ) : (
            <EmptyState message="File is empty." />
          )}
        </div>

        {/* Human approval callout */}
        <div className="border-t border-border bg-amber/5 px-4 py-2 flex items-start gap-2">
          <AlertTriangle size={14} className="text-amber mt-0.5 shrink-0" />
          <p className="text-[11px] text-muted">
            Munin cannot rewrite its own soul at runtime. All edits require{" "}
            <span className="text-amber">human approval</span>.
          </p>
        </div>
      </div>

      <Drawer
        open={editing}
        onClose={() => setEditing(false)}
        title={selected ? `Propose edit: ${selected}` : ""}
        width="max-w-2xl"
      >
        {selected && (
          <SoulEditor
            name={selected}
            original={content}
            onDone={() => setEditing(false)}
          />
        )}
      </Drawer>
    </div>
  );
}

function SoulEditor({
  name,
  original,
  onDone,
}: {
  name: string;
  original: string;
  onDone: () => void;
}) {
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const [draft, setDraft] = useState(original);
  const [summary, setSummary] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    setDraft(original);
  }, [original]);

  const submit = async () => {
    setSubmitting(true);
    setResult(null);
    try {
      const client = getMcpClient({ baseUrl: mcpUrl, token: mcpToken });
      const r = await client.callTool("soul_propose_edit", {
        name,
        content: draft,
        summary: summary || undefined,
      });
      const { text, isError } = extractToolResultContent(r);
      if (isError) {
        setResult({ ok: false, message: text || "Proposal rejected." });
      } else {
        setResult({
          ok: true,
          message: text || "Proposal submitted for human review.",
        });
      }
    } catch (e: any) {
      setResult({ ok: false, message: e?.message || String(e) });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="p-4 space-y-3">
      <div>
        <label className="block text-[10px] uppercase tracking-widest text-muted font-mono mb-1">
          Summary (optional)
        </label>
        <input
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          placeholder="Brief rationale for the change…"
          className="w-full bg-bg border border-border rounded px-2 py-1.5 text-sm text-body focus:outline-none focus:border-accent/60"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[10px] uppercase tracking-widest text-muted font-mono">
            Proposed content
          </label>
          <button
            onClick={() => setDraft(original)}
            className="text-[10px] text-muted hover:text-rose font-mono uppercase tracking-wider"
          >
            Reset
          </button>
        </div>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={20}
          className="w-full bg-bg border border-border rounded px-3 py-2 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
        />
      </div>

      {result && (
        <div
          className={cn(
            "text-xs font-mono px-3 py-2 rounded border",
            result.ok
              ? "border-success/50 text-success bg-success/5"
              : "border-rose/50 text-rose bg-rose/5"
          )}
        >
          {result.message}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={onDone}
          className="px-3 py-1.5 text-xs font-mono uppercase tracking-wider border border-border rounded text-muted hover:text-body"
        >
          Close
        </button>
        <button
          onClick={submit}
          disabled={submitting}
          className="px-3 py-1.5 text-xs font-mono uppercase tracking-wider rounded bg-accent/20 border border-accent/50 text-accent hover:bg-accent/30 disabled:opacity-50 flex items-center gap-1.5"
        >
          {submitting ? <Loader2 size={12} className="animate-spin" /> : null}
          Submit proposal
        </button>
      </div>
    </div>
  );
}
