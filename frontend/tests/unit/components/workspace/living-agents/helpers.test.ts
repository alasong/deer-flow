import { describe, expect, it } from "@rstest/core";

import {
  getAccessLevelVariant,
  getStatusColorClass,
  getStatusLabel,
} from "@/components/workspace/living-agents/helpers";
import type { AccessLevel, AgentStatus } from "@/core/living-agents/types";

describe("getStatusColorClass", () => {
  it('returns gray for "idle"', () => {
    expect(getStatusColorClass("idle")).toBe("bg-gray-100 text-gray-800");
  });

  it('returns green for "active"', () => {
    expect(getStatusColorClass("active")).toBe("bg-green-100 text-green-800");
  });

  it('returns yellow for "paused"', () => {
    expect(getStatusColorClass("paused")).toBe("bg-yellow-100 text-yellow-800");
  });

  it('returns red for "disabled"', () => {
    expect(getStatusColorClass("disabled")).toBe("bg-red-100 text-red-800");
  });

  it("handles unknown status gracefully", () => {
    expect(getStatusColorClass("unknown" as AgentStatus)).toBe(
      "bg-gray-100 text-gray-800",
    );
  });
});

describe("getStatusLabel", () => {
  it("returns human-readable text for each status", () => {
    expect(getStatusLabel("idle")).toBe("Idle");
    expect(getStatusLabel("active")).toBe("Active");
    expect(getStatusLabel("paused")).toBe("Paused");
    expect(getStatusLabel("disabled")).toBe("Disabled");
  });

  it("falls back for unknown status", () => {
    expect(getStatusLabel("unknown" as AgentStatus)).toBe("Unknown");
  });
});

describe("getAccessLevelVariant", () => {
  it('returns "default" for "exec"', () => {
    expect(getAccessLevelVariant("exec")).toBe("default");
  });

  it('returns "secondary" for "observe"', () => {
    expect(getAccessLevelVariant("observe")).toBe("secondary");
  });

  it('returns "destructive" for "admin"', () => {
    expect(getAccessLevelVariant("admin")).toBe("destructive");
  });

  it("falls back for unknown access level", () => {
    expect(getAccessLevelVariant("unknown" as AccessLevel)).toBe("outline");
  });
});
