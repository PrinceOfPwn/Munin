"use client";

import Raven from "./Raven";

interface EmptyStateProps {
  message: string;
  hint?: string;
  size?: number;
}

export default function EmptyState({
  message,
  hint,
  size = 80,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-6 text-center">
      <Raven size={size} className="text-muted/60 mb-4" eyeColor="#6b7280" />
      <p className="text-muted text-sm">{message}</p>
      {hint ? <p className="text-muted/70 text-xs mt-1">{hint}</p> : null}
    </div>
  );
}
