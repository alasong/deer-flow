import { describe, expect, it } from "@rstest/core";

describe("GatesPage", () => {
  it("is a valid module that exports a default function", async () => {
    const mod = await import("@/app/workspace/gates/page");
    expect(typeof mod.default).toBe("function");
  });
});
