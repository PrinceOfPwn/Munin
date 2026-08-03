// tags: [ui-component, shadcn-ui, primitive, skeleton]
import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-feather rounded bg-raised", className)} {...props} />;
}
