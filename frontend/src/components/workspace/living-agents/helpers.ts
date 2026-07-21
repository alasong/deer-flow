import type { AccessLevel, AgentStatus } from "@/core/living-agents/types";

/**
 * Returns Tailwind colour classes for a given agent status badge.
 */
export function getStatusColorClass(status: AgentStatus): string {
  switch (status) {
    case "idle":
      return "bg-gray-100 text-gray-800";
    case "active":
      return "bg-green-100 text-green-800";
    case "paused":
      return "bg-yellow-100 text-yellow-800";
    case "disabled":
      return "bg-red-100 text-red-800";
    default:
      return "bg-gray-100 text-gray-800";
  }
}

/**
 * Returns a human-readable label for an agent status.
 */
export function getStatusLabel(status: AgentStatus): string {
  switch (status) {
    case "idle":
      return "Idle";
    case "active":
      return "Active";
    case "paused":
      return "Paused";
    case "disabled":
      return "Disabled";
    default:
      return "Unknown";
  }
}

/**
 * Returns a Badge variant for a given access level.
 */
export function getAccessLevelVariant(level: AccessLevel): string {
  switch (level) {
    case "exec":
      return "default";
    case "observe":
      return "secondary";
    case "admin":
      return "destructive";
    default:
      return "outline";
  }
}
