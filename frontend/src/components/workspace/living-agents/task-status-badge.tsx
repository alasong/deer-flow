import { Badge } from "@/components/ui/badge";
import type { TaskStatus } from "@/core/living-agents/types";

import type { StatusConfig } from "./task-helpers";

export function getTaskStatusConfig(status: TaskStatus): StatusConfig {
  switch (status) {
    case "pending":
      return {
        variant: "outline",
        className: "text-muted-foreground border-muted-foreground/30",
      };
    case "claimed":
      return {
        variant: "outline",
        className: "border-blue-500/30 text-blue-600 dark:text-blue-400",
      };
    case "executing":
      return {
        variant: "outline",
        className: "border-amber-500/30 text-amber-600 dark:text-amber-400",
      };
    case "completed":
      return {
        variant: "outline",
        className: "border-green-500/30 text-green-600 dark:text-green-400",
      };
    case "failed":
      return { variant: "destructive", className: "" };
  }
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const config = getTaskStatusConfig(status);
  return (
    <Badge variant={config.variant} className={config.className}>
      {status}
    </Badge>
  );
}
