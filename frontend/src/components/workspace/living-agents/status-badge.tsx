import type { AgentStatus } from "@/core/living-agents/types";
import { cn } from "@/lib/utils";

import { getStatusColorClass, getStatusLabel } from "./helpers";

interface StatusBadgeProps {
  status: AgentStatus;
  className?: string;
}

/**
 * A small coloured badge that displays the agent's current status.
 */
export function StatusBadge({ status, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        getStatusColorClass(status),
        className,
      )}
    >
      {getStatusLabel(status)}
    </span>
  );
}
