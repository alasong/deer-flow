import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { GateList } from "@/components/workspace/living-agents/gate-list";
import type { Gate } from "@/core/living-agents/types";

function createGate(overrides: Partial<Gate> = {}): Gate {
  return {
    gate_id: "gate-abc",
    task_id: "task-xyz",
    step_index: 0,
    description: "Review the deployment to production",
    status: "pending",
    created_at: "2025-06-15T10:30:00Z",
    resolved_at: null,
    approved_by: null,
    human_input: null,
    ...overrides,
  };
}

const NOOP = () => undefined;

function render(props: Partial<Parameters<typeof GateList>[0]> = {}) {
  return renderToStaticMarkup(
    createElement(GateList, {
      gates: [],
      onApprove: NOOP,
      onReject: NOOP,
      isApproving: false,
      isRejecting: false,
      ...props,
    }),
  );
}

describe("GateList", () => {
  it("renders gate id and description", () => {
    const gates = [createGate()];
    const html = render({ gates });
    expect(html).toContain("gate-abc");
    expect(html).toContain("Review the deployment to production");
  });

  it("renders task id and step index", () => {
    const gates = [createGate()];
    const html = render({ gates });
    expect(html).toContain("task-xyz");
    expect(html).toContain("Step: 0");
  });

  it("renders pending status badge", () => {
    const gates = [createGate({ status: "pending" })];
    const html = render({ gates });
    expect(html).toContain("Pending");
  });

  it("renders approved status badge", () => {
    const gates = [createGate({ status: "approved" })];
    const html = render({ gates });
    expect(html).toContain("Approved");
  });

  it("renders rejected status badge", () => {
    const gates = [createGate({ status: "rejected" })];
    const html = render({ gates });
    expect(html).toContain("Rejected");
  });

  it("shows action buttons for pending gates in card footer", () => {
    const gates = [createGate({ status: "pending" })];
    const html = render({ gates });
    // Buttons are inside card-footer element
    expect(html).toContain("card-footer");
    expect(html).toContain("Approve");
    expect(html).toContain("Reject");
  });

  it("shows no card footer for approved gates", () => {
    const gates = [createGate({ status: "approved" })];
    const html = render({ gates });
    expect(html).not.toContain("card-footer");
  });

  it("shows no card footer for rejected gates", () => {
    const gates = [createGate({ status: "rejected" })];
    const html = render({ gates });
    expect(html).not.toContain("card-footer");
  });

  it("disables action buttons when isApproving is true", () => {
    const gates = [createGate({ status: "pending" })];
    const html = render({ gates, isApproving: true });
    // Buttons should have disabled attribute
    expect(html).toContain('disabled=""');
  });

  it("disables action buttons when isRejecting is true", () => {
    const gates = [createGate({ status: "pending" })];
    const html = render({ gates, isRejecting: true });
    expect(html).toContain('disabled=""');
  });

  it("shows approved_by for approved gates", () => {
    const gates = [
      createGate({
        status: "approved",
        approved_by: "admin",
        resolved_at: "2025-06-15T11:00:00Z",
        human_input: "Looks good, proceed",
      }),
    ];
    const html = render({ gates });
    expect(html).toContain("admin");
    expect(html).toContain("Looks good, proceed");
    expect(html).toContain("Approved by:");
  });

  it("shows human_input for rejected gates", () => {
    const gates = [
      createGate({
        status: "rejected",
        approved_by: "reviewer",
        resolved_at: "2025-06-15T11:30:00Z",
        human_input: "Need more testing",
      }),
    ];
    const html = render({ gates });
    expect(html).toContain("reviewer");
    expect(html).toContain("Need more testing");
    expect(html).toContain("Rejected by:");
  });

  it("shows empty state when gates array is empty", () => {
    const html = render({ gates: [] });
    expect(html).toContain("No gates");
  });

  it("shows loading state when isLoading is true", () => {
    const html = render({ gates: [], isLoading: true });
    expect(html).toContain("Loading");
  });

  it("renders multiple gates", () => {
    const gates = [
      createGate({ gate_id: "gate-1", description: "First gate" }),
      createGate({ gate_id: "gate-2", description: "Second gate" }),
    ];
    const html = render({ gates });
    expect(html).toContain("gate-1");
    expect(html).toContain("gate-2");
    expect(html).toContain("First gate");
    expect(html).toContain("Second gate");
  });

  it("shows created date", () => {
    const gates = [createGate({ created_at: "2025-06-15T10:30:00Z" })];
    const html = render({ gates });
    expect(html).toContain("Created:");
  });

  it("shows resolved date for resolved gates", () => {
    const gates = [
      createGate({
        status: "approved",
        resolved_at: "2025-06-15T11:00:00Z",
      }),
    ];
    const html = render({ gates });
    expect(html).toContain("Resolved:");
  });
});
