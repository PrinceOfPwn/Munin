// tags: [ui-component, console-surface, tanstack-query, react-query, lucide-icons, client-component, use-query-client, use-effect, use-state, auth-gate, login-form]
"use client";

import Image from "next/image";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { productionApi, type Actor } from "@/lib/production-api";
import { messageFromError } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface AuthGateProps {
  /** Rendered after a successful bootstrap/login/session. Receives the
   *  authenticated actor plus a logout callback so parents don't have to
   *  duplicate session teardown. */
  children: (actor: Actor, logout: () => Promise<void>) => ReactNode;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * `AuthGate` — Fase 1b extraction of FlightDeckStable's login shell.
 *
 * Owns the session/login/bootstrap flow so `AppShell` can compose it with the
 * new `AgentConsole` transport. Purely UI + production API calls: no dispatcher,
 * no polling, no legacy state stores.
 */
export default function AuthGate({ children }: AuthGateProps) {
  const qc = useQueryClient();
  const [actor, setActor] = useState<Actor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    productionApi
      .session()
      .then((next) => {
        if (!cancelled) setActor(next);
      })
      .catch(() => {
        if (!cancelled) setActor(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function logout() {
    try {
      await productionApi.logout();
    } catch (cause) {
      setError(messageFromError(cause));
    } finally {
      qc.clear();
      if (typeof window !== "undefined") {
        window.localStorage.removeItem("munin.activeConversationId");
      }
      setActor(null);
    }
  }

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center bg-bg font-mono text-xs uppercase tracking-widest text-secondary">
        Opening the Raven&apos;s Memory…
      </div>
    );
  }

  if (!actor) {
    return (
      <LoginForm
        error={error}
        setError={setError}
        onAuthenticated={async () => {
          const next = await productionApi.session();
          setActor(next);
        }}
      />
    );
  }

  return <>{children(actor, logout)}</>;
}

// ---------------------------------------------------------------------------
// Login form (bootstrap toggle + submit)
// ---------------------------------------------------------------------------

function LoginForm({
  error,
  setError,
  onAuthenticated,
}: {
  error: string;
  setError: (error: string) => void;
  onAuthenticated: () => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [bootstrap, setBootstrap] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (bootstrap) await productionApi.bootstrap(username, password);
      await productionApi.login(username, password);
      await onAuthenticated();
    } catch (cause) {
      setError(messageFromError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen bg-bg text-body md:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
      <section className="hidden flex-col justify-center border-r border-border px-16 py-24 md:flex">
        <Image
          src="/raven-mark.png"
          width={76}
          height={76}
          alt="Munin raven mark"
          className="mb-8 opacity-90"
        />
        <p className="font-mono text-[0.65rem] uppercase tracking-[0.16em] text-muted">
          MUNIN / OPERATOR ARCHIVE
        </p>
        <h1 className="mt-3 text-6xl font-medium leading-none tracking-tighter">
          The Raven&apos;s Memory
        </h1>
        <p className="mt-6 max-w-lg text-base leading-relaxed text-secondary">
          Cache-first navigation over an authoritative durable archive.
        </p>
      </section>
      <form onSubmit={submit} className="grid place-content-center px-6 py-12">
        <div className="grid w-full max-w-sm gap-4">
          <div className="flex items-center gap-2 text-base font-semibold">
            <KeyRound className="h-4 w-4 text-accent" />
            {bootstrap ? "Bootstrap the archive" : "Operator sign in"}
          </div>
          <label className="grid gap-1.5 text-[0.7rem] font-medium uppercase tracking-wider text-secondary">
            Operator ID
            <Input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label className="grid gap-1.5 text-[0.7rem] font-medium uppercase tracking-wider text-secondary">
            Passphrase
            <Input
              type="password"
              minLength={12}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete={bootstrap ? "new-password" : "current-password"}
              required
            />
          </label>
          {error && (
            <p className="flex items-center gap-1.5 text-xs text-danger">
              <TriangleAlert className="h-3.5 w-3.5" /> {error}
            </p>
          )}
          <Button type="submit" disabled={busy}>
            {busy
              ? "Verifying…"
              : bootstrap
                ? "Establish admin"
                : "Enter workspace"}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => setBootstrap((value) => !value)}
          >
            {bootstrap
              ? "Use existing account"
              : "First deployment? Bootstrap admin"}
          </Button>
        </div>
      </form>
    </main>
  );
}
