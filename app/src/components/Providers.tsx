"use client";

import { useEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import { BrowserCacheProvider } from "@/lib/cache";
import { hydrateMuninQueryCache, subscribeMuninQueryCache } from "@/lib/query-cache";

/** Install an RFC 4122 v4 fallback for non-secure tunnel/LAN origins. */
function installRandomUuidFallback(): void {
  const webCrypto = globalThis.crypto;
  if (!webCrypto || typeof webCrypto.randomUUID === "function") return;

  const fallback = (): `${string}-${string}-${string}-${string}-${string}` => {
    const bytes = new Uint8Array(16);
    webCrypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };

  Object.defineProperty(webCrypto, "randomUUID", {
    configurable: true,
    value: fallback,
  });
}

/**
 * App-wide client providers.
 *
 * A snapshot is hydrated before the flight deck mounts, but only when its
 * embedded actor id matches the local authenticated-actor marker.  The
 * subscription resolves that marker dynamically, so the first login can begin
 * persistence without requiring a reload.
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
    installRandomUuidFallback();

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
      <BrowserCacheProvider>
        <TooltipProvider delayDuration={200}>
          {ready ? (
            children
          ) : (
            <div className="grid min-h-screen place-items-center bg-bg font-mono text-xs uppercase tracking-widest text-secondary">
              Restoring the Raven's local cache…
            </div>
          )}
        </TooltipProvider>
      </BrowserCacheProvider>
      <Toaster />
    </QueryClientProvider>
  );
}
