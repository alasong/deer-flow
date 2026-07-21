import type { TaskStatus } from "@/core/living-agents/types";

export interface StatusConfig {
  variant: "default" | "secondary" | "destructive" | "outline";
  className: string;
}

export function validateSubmitForm(
  capability: string,
  description: string,
): string | null {
  if (!capability.trim()) {
    return "Capability is required";
  }
  if (!description.trim()) {
    return "Description is required";
  }
  return null;
}

export function canCancelTask(status: TaskStatus): boolean {
  return status === "pending" || status === "claimed";
}
