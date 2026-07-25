"use client";

import { useEffect, useState } from "react";
import { X, Eye, EyeOff, Check, Loader2, Save } from "lucide-react";
import { useMuninStore } from "@/store/muninStore";

export default function SettingsModal() {
  const close = useMuninStore((s) => s.closeSettings);
  const mcpUrl = useMuninStore((s) => s.mcpUrl);
  const mcpToken = useMuninStore((s) => s.mcpToken);
  const setConfig = useMuninStore((s) => s.setConfig);
  const testConnection = useMuninStore((s) => s.testConnection);
  const refreshTools = useMuninStore((s) => s.refreshTools);

  const [url, setUrl] = useState(mcpUrl);
  const [token, setToken] = useState(mcpToken);
  const [reveal, setReveal] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    setUrl(mcpUrl);
    setToken(mcpToken);
  }, [mcpUrl, mcpToken]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [close]);

  const handleTest = async () => {
    setTesting(true);
    setResult(null);
    setConfig(url, token);
    const r = await testConnection();
    setResult(r);
    setTesting(false);
    if (r.ok) refreshTools();
  };

  const handleSave = () => {
    setConfig(url, token);
    refreshTools();
    close();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={close} />
      <div className="relative w-full max-w-lg bg-surface border border-border rounded-lg shadow-2xl animate-fade-slide">
        <div className="flex items-center justify-between px-5 py-3 border-b border-border">
          <h2 className="font-mono text-accent uppercase tracking-widest text-sm">
            Settings
          </h2>
          <button onClick={close} className="text-muted hover:text-body" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div>
            <label className="block text-[10px] uppercase tracking-widest text-muted font-mono mb-1.5">
              MCP Base URL
            </label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="http://localhost:8890"
              className="w-full bg-bg border border-border rounded px-3 py-2 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
            />
          </div>

          <div>
            <label className="block text-[10px] uppercase tracking-widest text-muted font-mono mb-1.5">
              Bearer Token
            </label>
            <div className="relative">
              <input
                type={reveal ? "text" : "password"}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="MUNIN_MCP_AUTH_TOKEN"
                className="w-full bg-bg border border-border rounded px-3 py-2 pr-10 text-sm font-mono text-body focus:outline-none focus:border-accent/60"
              />
              <button
                type="button"
                onClick={() => setReveal((r) => !r)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-body"
                aria-label={reveal ? "Hide token" : "Reveal token"}
              >
                {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-[10px] text-muted mt-1.5">
              Stored only in localStorage. Never sent anywhere except the MCP
              server above.
            </p>
          </div>

          {result && (
            <div
              className={`text-xs font-mono px-3 py-2 rounded border ${
                result.ok
                  ? "border-success/50 text-success bg-success/5"
                  : "border-rose/50 text-rose bg-rose/5"
              }`}
            >
              <div className="flex items-center gap-1.5">
                {result.ok ? <Check size={12} /> : <X size={12} />}
                {result.message}
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              onClick={handleTest}
              disabled={testing}
              className="px-3 py-2 text-sm font-mono uppercase tracking-wider border border-border rounded text-body hover:bg-surface disabled:opacity-50 flex items-center gap-1.5"
            >
              {testing ? <Loader2 size={14} className="animate-spin" /> : null}
              Test connection
            </button>
            <button
              onClick={handleSave}
              className="px-3 py-2 text-sm font-mono uppercase tracking-wider rounded bg-accent/20 border border-accent/50 text-accent hover:bg-accent/30 flex items-center gap-1.5"
            >
              <Save size={14} /> Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
