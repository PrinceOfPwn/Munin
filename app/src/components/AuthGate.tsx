// tags: [ui-component, console-surface, tanstack-query, react-query, lucide-icons, client-component, use-query-client, use-effect, use-state, auth-gate, login-form, licensing-notice]
"use client";

import Image from "next/image";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ExternalLink,
  FileCheck2,
  KeyRound,
  Scale,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import { productionApi, type Actor } from "@/lib/production-api";
import { messageFromError } from "@/lib/utils";

const LEGAL_DOCUMENTS_BASE_URL =
  "https://github.com/PrinceOfPwn/Munin/blob/main";

const legalDocuments = [
  { label: "License", path: "LICENSE" },
  { label: "Acceptable Use Policy", path: "ACCEPTABLE_USE_POLICY.md" },
  { label: "Commercial Licensing", path: "COMMERCIAL_LICENSING.md" },
  { label: "Disclaimer", path: "DISCLAIMER.md" },
] as const;

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
  const [acceptedTerms, setAcceptedTerms] = useState(false);
  const [termsOpen, setTermsOpen] = useState(true);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!acceptedTerms) {
      setError("You must accept the license and use terms before continuing.");
      setTermsOpen(true);
      return;
    }

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
    <>
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

            <div className="grid gap-3 rounded-lg border border-accent/30 bg-accent/5 p-3.5">
              <div className="flex items-start gap-2.5">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                <div className="grid gap-2">
                  <blockquote className="text-xs font-semibold leading-relaxed text-body">
                    “Please do not use in military or secret service
                    organizations, or for illegal purposes — this is
                    non-binding, these *** ignore laws and ethics anyway.”
                  </blockquote>
                  <p className="text-[0.62rem] italic leading-relaxed text-muted">
                    Gracias THC Hydra por una de las mejores intro en herramientas.
                  </p>
                  <p className="text-[0.7rem] leading-relaxed text-secondary">
                    Munin remains licensed for noncommercial and explicitly
                    authorized use only. Commercial benefit requires a separate
                    written license, and each operator is responsible for scope,
                    actions, and results.
                  </p>
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                className="h-8 justify-center text-xs"
                onClick={() => setTermsOpen(true)}
              >
                <Scale className="mr-2 h-3.5 w-3.5" />
                Review license and use terms
              </Button>
              <label className="flex cursor-pointer items-start gap-2.5 text-[0.7rem] leading-relaxed text-secondary">
                <input
                  type="checkbox"
                  checked={acceptedTerms}
                  onChange={(event) => {
                    setAcceptedTerms(event.target.checked);
                    if (event.target.checked) setError("");
                  }}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-current"
                  required
                />
                <span>
                  I have reviewed and accept the License, Acceptable Use
                  Policy, and Disclaimer. I confirm that my use is lawful,
                  authorized, and noncommercial unless separately licensed.
                </span>
              </label>
            </div>

            {error && (
              <p className="flex items-center gap-1.5 text-xs text-danger">
                <TriangleAlert className="h-3.5 w-3.5" /> {error}
              </p>
            )}
            <Button type="submit" disabled={busy || !acceptedTerms}>
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

      <Dialog open={termsOpen} onOpenChange={setTermsOpen}>
        <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileCheck2 className="h-5 w-5 text-accent" />
              Munin use and licensing notice
            </DialogTitle>
            <DialogDescription>
              Review these conditions before signing in or bootstrapping an
              operator account.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 text-sm leading-relaxed text-secondary">
            <section className="grid gap-2 rounded-lg border border-accent/30 bg-accent/5 p-3.5">
              <blockquote className="font-medium italic text-body">
                “Please do not use in military or secret service organizations,
                or for illegal purposes — this is non-binding, these *** ignore
                laws and ethics anyway.”
              </blockquote>
              <p className="text-[0.65rem] italic text-muted">
                Gracias THC Hydra por una de las mejores intro en herramientas.
              </p>
            </section>

            <section className="grid gap-1.5 rounded-lg border border-border bg-bg/40 p-3.5">
              <h2 className="font-semibold text-body">Noncommercial license</h2>
              <p>
                The public source license does not authorize company-internal
                use, paid work, consulting, managed services, SaaS, resale,
                commercial integration, cost reduction, or another direct or
                indirect commercial benefit. Those uses require a separate
                written commercial license.
              </p>
            </section>

            <section className="grid gap-1.5 rounded-lg border border-border bg-bg/40 p-3.5">
              <h2 className="font-semibold text-body">
                Lawful and explicitly authorized use
              </h2>
              <p>
                Technical access or tool availability is not permission. You
                must hold all necessary authorization, remain within scope, and
                stop when authorization expires or impact becomes unclear.
              </p>
            </section>

            <section className="grid gap-1.5 rounded-lg border border-border bg-bg/40 p-3.5">
              <h2 className="font-semibold text-body">
                Your responsibility and risk
              </h2>
              <p>
                Munin is provided as is, without warranty. You are responsible
                for deployment security, credentials, data, targets, model and
                tool outputs, generated actions, human review, and all
                consequences of your use.
              </p>
            </section>

            <nav className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {legalDocuments.map((document) => (
                <a
                  key={document.path}
                  href={`${LEGAL_DOCUMENTS_BASE_URL}/${document.path}`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-xs font-medium text-body transition-colors hover:border-accent/60 hover:bg-accent/5"
                >
                  {document.label}
                  <ExternalLink className="h-3.5 w-3.5 text-muted" />
                </a>
              ))}
            </nav>
          </div>

          <DialogFooter>
            <Button type="button" onClick={() => setTermsOpen(false)}>
              I understand
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
