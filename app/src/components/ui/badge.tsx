// tags: [ui-component, shadcn-ui, primitive, badge]
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wider transition-colors",
  {
    variants: {
      variant: {
        neutral: "border-border bg-raised text-secondary",
        accent: "border-transparent bg-accent-soft text-accent",
        success: "border-transparent bg-success/10 text-success",
        warning: "border-transparent bg-warning/10 text-warning",
        danger: "border-transparent bg-danger/10 text-danger",
        info: "border-transparent bg-info/10 text-info",
        outline: "border-border bg-transparent text-secondary",
      },
    },
    defaultVariants: { variant: "neutral" },
  }
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

// Map a run/tool/message state to the appropriate badge variant.
export function stateBadgeVariant(state: string | undefined): BadgeProps["variant"] {
  switch (state) {
    case "completed":
    case "success":
    case "done":
    case "resolved":
      return "success";
    case "running":
    case "queued":
      return "info";
    case "waiting_for_human":
    case "pending":
      return "warning";
    case "failed":
    case "cancelled":
    case "interrupted":
    case "error":
      return "danger";
    default:
      return "neutral";
  }
}
