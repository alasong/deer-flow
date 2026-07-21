"use client";

import {
  CheckCircle2Icon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import type { Gate } from "@/core/living-agents/types";

export interface GateActionDialogProps {
  gate: Gate | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (gateId: string, humanInput: string) => Promise<void>;
  action: "approve" | "reject";
}

export function GateActionDialog({
  gate,
  open,
  onOpenChange,
  onConfirm,
  action,
}: GateActionDialogProps) {
  const [humanInput, setHumanInput] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!gate) return null;

  const isApprove = action === "approve";
  const title = isApprove ? "Approve Gate" : "Reject Gate";
  const confirmLabel = isApprove ? "Approve" : "Reject";
  const description = isApprove
    ? "Review the gate details and approve to proceed."
    : "Review the gate details and reject to block.";

  const handleConfirm = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onConfirm(gate.gate_id, humanInput);
      setHumanInput("");
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (submitting && !newOpen) return;
    if (!newOpen) {
      setHumanInput("");
    }
    onOpenChange(newOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {isApprove ? (
              <CheckCircle2Icon className="size-5 text-primary" />
            ) : (
              <XCircleIcon className="size-5 text-destructive" />
            )}
            {title}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Gate info */}
          <div className="bg-muted/50 rounded-lg p-3 text-sm">
            <div className="flex items-center gap-2">
              <Badge
                variant="secondary"
                className="h-5 rounded-sm px-1.5 text-[10px] capitalize"
              >
              {gate.status.charAt(0).toUpperCase() + gate.status.slice(1)}
              </Badge>
              <code className="text-muted-foreground text-xs">
                {gate.gate_id}
              </code>
            </div>
            <div className="text-muted-foreground mt-2 text-xs">
              Task: <code>{gate.task_id}</code> &middot; Step:{" "}
              {gate.step_index}
            </div>
            <p className="text-foreground mt-1.5 text-sm">
              {gate.description}
            </p>
          </div>

          {/* Human input */}
          <div className="space-y-1.5">
            <label
              htmlFor="gate-human-input"
              className="text-foreground text-sm font-medium"
            >
              Opinion
            </label>
            <Textarea
              id="gate-human-input"
              placeholder={
                isApprove
                  ? "Reason for approval..."
                  : "Reason for rejection..."
              }
              value={humanInput}
              onChange={(e) => setHumanInput(e.target.value)}
              disabled={submitting}
              className="min-h-20 resize-y text-sm"
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            disabled={submitting}
            onClick={() => handleOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            variant={isApprove ? "default" : "destructive"}
            disabled={submitting}
            onClick={handleConfirm}
          >
            {submitting ? (
              <Loader2Icon className="mr-1.5 size-4 animate-spin" />
            ) : isApprove ? (
              <CheckCircle2Icon className="mr-1.5 size-4" />
            ) : (
              <XCircleIcon className="mr-1.5 size-4" />
            )}
            {submitting ? "Processing..." : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
