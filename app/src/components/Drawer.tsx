"use client";

import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  side?: "right" | "left";
  width?: string;
}

export default function Drawer({
  open,
  onClose,
  title,
  children,
  side = "right",
  width = "max-w-xl",
}: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative ml-auto h-full w-full bg-surface border-l border-border flex flex-col animate-fade-slide",
          width
        )}
      >
        {title ? (
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <div className="font-mono text-sm text-accent uppercase tracking-wider">
              {title}
            </div>
            <button
              onClick={onClose}
              className="text-muted hover:text-body transition-colors"
              aria-label="Close drawer"
            >
              <X size={18} />
            </button>
          </div>
        ) : null}
        <div className="flex-1 min-h-0 overflow-auto">{children}</div>
      </div>
    </div>
  );
}
