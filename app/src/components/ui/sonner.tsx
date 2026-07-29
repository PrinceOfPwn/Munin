"use client";

import { Toaster as SonnerToaster } from "sonner";

/**
 * Wrapper so callers don't have to remember the dark-theme + Munin styling
 * options.  Everything downstream just imports { Toaster } from here.
 */
export function Toaster() {
  return (
    <SonnerToaster
      theme="dark"
      position="bottom-right"
      className="!z-toast"
      toastOptions={{
        classNames: {
          toast: "z-toast bg-surface border border-border text-body shadow-lg",
          title: "text-body",
          description: "text-secondary",
          actionButton: "bg-accent text-white",
          cancelButton: "bg-raised text-secondary",
          error: "border-danger/40",
          success: "border-success/40",
          warning: "border-warning/40",
          info: "border-info/40",
        },
      }}
    />
  );
}

export { toast } from "sonner";
