import { describe, expect, test } from "@rstest/core";

import { canCancelTask, validateSubmitForm } from "@/components/workspace/living-agents/task-helpers";
import { getTaskStatusConfig } from "@/components/workspace/living-agents/task-status-badge";
import type { TaskStatus } from "@/core/living-agents/types";

describe("getTaskStatusConfig", () => {
  const statuses: TaskStatus[] = [
    "pending",
    "claimed",
    "executing",
    "completed",
    "failed",
  ];

  for (const status of statuses) {
    test(`returns config for ${status}`, () => {
      const config = getTaskStatusConfig(status);
      expect(config).toBeDefined();
      expect(config.variant).toBeDefined();
      expect(typeof config.className).toBe("string");
    });
  }

  test("failed is destructive", () => {
    const config = getTaskStatusConfig("failed");
    expect(config.variant).toBe("destructive");
  });
});

describe("validateSubmitForm", () => {
  test("returns error when capability is empty", () => {
    const error = validateSubmitForm("", "do something");
    expect(error).toBeDefined();
  });

  test("returns error when description is empty", () => {
    const error = validateSubmitForm("dev", "");
    expect(error).toBeDefined();
  });

  test("returns null when both fields are filled", () => {
    const error = validateSubmitForm("dev", "implement the feature");
    expect(error).toBeNull();
  });

  test("returns error when capability is whitespace-only", () => {
    const error = validateSubmitForm("   ", "do something");
    expect(error).toBeDefined();
  });

  test("returns error when description is whitespace-only", () => {
    const error = validateSubmitForm("dev", "   ");
    expect(error).toBeDefined();
  });

  test("trims values before validation", () => {
    const error = validateSubmitForm("  dev  ", "  implement  ");
    expect(error).toBeNull();
  });
});

describe("canCancelTask", () => {
  test("returns true for pending", () => {
    expect(canCancelTask("pending")).toBe(true);
  });

  test("returns true for claimed", () => {
    expect(canCancelTask("claimed")).toBe(true);
  });

  test("returns false for executing", () => {
    expect(canCancelTask("executing")).toBe(false);
  });

  test("returns false for completed", () => {
    expect(canCancelTask("completed")).toBe(false);
  });

  test("returns false for failed", () => {
    expect(canCancelTask("failed")).toBe(false);
  });
});
