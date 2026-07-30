"use client";

import { cn } from "@/lib/utils";

interface RavenProps {
  className?: string;
  size?: number;
  /** If true, eye is violet (default). Otherwise pass a color. */
  eyeColor?: string;
  title?: string;
}

/**
 * The Munin raven — perched, wings partially open, slightly menacing.
 * Provided asset used verbatim.
 */
export default function Raven({
  className,
  size = 120,
  eyeColor = "#7c3aed",
  title,
}: RavenProps) {
  return (
    <svg
      viewBox="0 0 120 120"
      xmlns="http://www.w3.org/2000/svg"
      fill="currentColor"
      width={size}
      height={size}
      className={cn("text-body", className)}
      role="img"
      aria-label={title || "Munin raven"}
    >
      {title ? <title>{title}</title> : null}
      <ellipse cx="60" cy="72" rx="22" ry="28" />
      <ellipse cx="60" cy="38" rx="14" ry="13" />
      <polygon points="68,36 82,33 68,42" />
      <circle cx="66" cy="34" r="2.5" fill={eyeColor} />
      <path d="M38,65 Q10,50 15,85 Q30,75 38,80 Z" />
      <path d="M82,65 Q110,50 105,85 Q90,75 82,80 Z" />
      <path d="M48,98 Q60,115 72,98 Q60,108 48,98 Z" />
      <line x1="53" y1="98" x2="45" y2="108" stroke="currentColor" strokeWidth="2" />
      <line x1="53" y1="108" x2="41" y2="108" stroke="currentColor" strokeWidth="1.5" />
      <line x1="53" y1="108" x2="53" y2="116" stroke="currentColor" strokeWidth="1.5" />
      <line x1="67" y1="98" x2="75" y2="108" stroke="currentColor" strokeWidth="2" />
      <line x1="75" y1="108" x2="87" y2="108" stroke="currentColor" strokeWidth="1.5" />
      <line x1="75" y1="108" x2="75" y2="116" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
