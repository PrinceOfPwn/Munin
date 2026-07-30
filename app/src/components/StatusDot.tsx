"use client";

import { cn } from "@/lib/utils";

interface StatusDotProps {
  status: "ok" | "error" | "warn" | "idle" | "unknown";
  size?: number;
  pulse?: boolean;
  className?: string;
}

const COLORS: Record<StatusDotProps["status"], string> = {
  ok: "bg-success",
  error: "bg-rose",
  warn: "bg-amber",
  idle: "bg-muted",
  unknown: "bg-muted",
};

export default function StatusDot({
  status,
  size = 8,
  pulse = false,
  className,
}: StatusDotProps) {
  return (
    <span
      className={cn(
        "inline-block rounded-full",
        COLORS[status],
        pulse && status === "ok" && "animate-pulse",
        className
      )}
      style={{ width: size, height: size }}
      aria-label={status}
    />
  );
}
