"use client";

// Minimal Avatar primitive matching shadcn shape.  Uses first-initial fallback
// because collaborators in the Munin production API have usernames but no
// avatar URLs.  When the backend later exposes an image URL, the caller can
// pass an <img> in the `src` slot.
import * as React from "react";
import { cn } from "@/lib/utils";

interface AvatarProps extends React.HTMLAttributes<HTMLSpanElement> {
  name?: string;
  src?: string;
  size?: "xs" | "sm" | "md";
  ring?: "none" | "green" | "amber" | "muted";
}

const SIZE = {
  xs: "h-5 w-5 text-[0.55rem]",
  sm: "h-6 w-6 text-[0.65rem]",
  md: "h-8 w-8 text-xs",
};

const RING = {
  none: "",
  green: "ring-2 ring-success/70 ring-offset-1 ring-offset-surface",
  amber: "ring-2 ring-warning/70 ring-offset-1 ring-offset-surface",
  muted: "ring-1 ring-border ring-offset-0",
};

function initialsOf(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/[\s._-]+/).filter(Boolean);
  if (parts.length === 0) return trimmed.slice(0, 2).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** Deterministic hue from a stable seed so the same operator always gets the
 *  same tint across sessions.  Uses HSL so we stay within the theme's palette
 *  brightness. */
function hueOf(seed: string): number {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash << 5) - hash + seed.charCodeAt(i);
    hash |= 0;
  }
  return Math.abs(hash) % 360;
}

export const Avatar = React.forwardRef<HTMLSpanElement, AvatarProps>(
  ({ name = "", src, size = "sm", ring = "none", className, ...props }, ref) => {
    const initials = initialsOf(name);
    const hue = hueOf(name || "unknown");
    return (
      <span
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center rounded-full font-mono font-medium text-body shrink-0",
          SIZE[size],
          RING[ring],
          className,
        )}
        style={
          src
            ? undefined
            : { backgroundColor: `hsl(${hue} 45% 24%)`, color: `hsl(${hue} 90% 82%)` }
        }
        title={name || undefined}
        {...props}
      >
        {src ? (
          // Avatar URLs may be operator-provided and intentionally do not use
          // Next's server-side optimizer, which would need a broad remote host
          // allowlist. The caller remains responsible for trusted sources.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={name}
            className="h-full w-full rounded-full object-cover"
          />
        ) : (
          initials
        )}
      </span>
    );
  },
);
Avatar.displayName = "Avatar";
