// tags: [ui-component, console-surface, lucide-icons, client-component, use-state, e-m-p-t-y--d-r-a-f-t, provider-switcher]
"use client";

import { useState } from "react";
import { Plus, Radio } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ProviderProfile } from "@/lib/production-api";

type ProviderDraft = {
  label: string;
  provider: string;
  base_url: string;
  model: string;
  api_key: string;
};

const EMPTY_DRAFT: ProviderDraft = {
  label: "",
  provider: "openai-compatible",
  base_url: "",
  model: "",
  api_key: "",
};

export function ProviderSwitcher({
  profiles,
  onActivate,
  onCreate,
  busy = false,
}: {
  profiles: ProviderProfile[];
  onActivate: (profileId: string) => Promise<void>;
  onCreate: (draft: ProviderDraft) => Promise<void>;
  busy?: boolean;
}) {
  const active = profiles.find((profile) => profile.active);
  const [draft, setDraft] = useState<ProviderDraft>(EMPTY_DRAFT);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      await onCreate(draft);
      setDraft(EMPTY_DRAFT);
      setOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save provider");
    }
  }

  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="relative">
      <summary className="flex cursor-pointer list-none items-center gap-1 rounded border border-border bg-surface px-2 py-1 text-[0.65rem] text-secondary hover:text-body">
        <Radio className="h-3 w-3 text-accent" />
        <span className="max-w-28 truncate">{active?.label ?? "Environment"}</span>
      </summary>
      <div className="absolute right-0 z-30 mt-2 w-80 rounded-md border border-border bg-surface p-3 shadow-xl">
        <label className="mb-1 block text-[0.65rem] uppercase tracking-widest text-muted">Provider endpoint</label>
        <select
          value={active?.id ?? ""}
          disabled={busy}
          onChange={(event) => {
            void onActivate(event.target.value);
          }}
          className="mb-3 w-full rounded border border-border bg-bg px-2 py-1.5 text-xs text-body"
          aria-label="Active AI provider"
        >
          <option value="">Environment defaults</option>
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.label} · {profile.model}
            </option>
          ))}
        </select>

        <form onSubmit={(event) => void submit(event)} className="space-y-2 border-t border-border pt-3">
          <p className="text-[0.7rem] text-secondary">Add a profile; the key is sent once to the backend and never rendered again.</p>
          <Input required placeholder="Label (e.g. Groq)" value={draft.label} onChange={(event) => setDraft({ ...draft, label: event.target.value })} />
          <Input required placeholder="Provider name" value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value })} />
          <Input required type="url" pattern="https://.*" placeholder="https://api.example/v1" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} />
          <Input required placeholder="Model" value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} />
          <Input required type="password" autoComplete="new-password" placeholder="API key" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} />
          {error && <p className="text-xs text-danger">{error}</p>}
          <Button type="submit" size="sm" disabled={busy || !draft.api_key}>
            <Plus className="h-3.5 w-3.5" /> Save profile
          </Button>
        </form>
      </div>
    </details>
  );
}
