"use client";

import { cn } from "@/lib/utils";

interface RavenProps {
  className?: string;
  size?: number;
  eyeColor?: string;
  title?: string;
}

/**
 * The official Munin raven logo mark.
 */
export default function Raven({
  className,
  size = 120,
  title = "Munin Raven Logo",
}: RavenProps) {
  return (
    <img
      src="/raven-mark.png"
      alt={title}
      width={size}
      height={size}
      style={{ width: `${size}px`, height: `${size}px` }}
      className={cn("object-contain drop-shadow-[0_0_15px_rgba(124,58,237,0.45)] transition-all duration-300 hover:drop-shadow-[0_0_22px_rgba(168,85,247,0.7)]", className)}
    />
  );
}
