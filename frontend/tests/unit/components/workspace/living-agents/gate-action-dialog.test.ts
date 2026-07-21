import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { GateActionDialog } from "@/components/workspace/living-agents/gate-action-dialog";

const NOOP = () => undefined;

function render(props: Partial<Parameters<typeof GateActionDialog>[0]> = {}) {
  return renderToStaticMarkup(
    createElement(GateActionDialog, {
      gate: null,
      open: false,
      onOpenChange: NOOP,
      onConfirm: () => Promise.resolve(),
      action: "approve",
      ...props,
    }),
  );
}

describe("GateActionDialog", () => {
  it("returns null when gate is null (regardless of open state)", () => {
    const html = render({ open: true, gate: null });
    // DialogPrimitive.Portal (createPortal) does not render in
    // renderToStaticMarkup, but the early return for null gate
    // is testable: with gate=null we get empty string.
    expect(html).toBe("");
  });

  it("returns null when gate is null and closed", () => {
    const html = render({ open: false, gate: null });
    expect(html).toBe("");
  });
});
