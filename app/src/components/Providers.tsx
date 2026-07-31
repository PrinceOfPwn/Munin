"use client";

import { useEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { hydrateMuninQueryCache, subscribeMuninQueryCache } from "@/lib/query-cache";

/**
 * App-wide client providers.
 *
 * Turso remains authoritative, but successful read models are mirrored into
 * IndexedDB.  We hydrate that cache before mounting the flight deck so changing
 * tabs, reconnecting a tunnel, or reloading the page never replaces an existing
 * conversation with an empty screen while a remote request is in flight.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 7 * 24 * 60 * 60 * 1000,
            refetchOnWindowFocus: false,
            refetchOnReconnect: true,
            retry: 2,
            retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
          },
        },
      }),
  );

  useEffect(() => {
    let disposed = false;
    let unsubscribe: (() => void) | undefined;

    void hydrateMuninQueryCache(queryClient).finally(() => {
      if (disposed) return;
      unsubscribe = subscribeMuninQueryCache(queryClient);
      setReady(true);
    });

    return () => {
      disposed = true;
      unsubscribe?.();
    };
  }, [queryClient]);

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>
        {ready ? (
          children
        ) : (
          <div className="grid min-h-screen place-items-center bg-bg font-mono text-xs uppercase tracking-widest text-secondary">
            Restoring the Raven&apos;s local cache…
          </div>
        )}
      </TooltipProvider>
      <Toaster />
    </QueryClientProvider>
  );
}
