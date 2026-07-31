"use client";

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";

/**
 * App-wide client providers.
 *
 * The authenticated flight deck owns cache hydration because the persisted
 * snapshot is scoped to an actor.  The QueryClient still lives here so login,
 * workspace navigation, and logout all share one in-memory read model.
 */
export function Providers({ children }: { children: ReactNode }) {
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

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
      <Toaster />
    </QueryClientProvider>
  );
}
