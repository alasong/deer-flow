"use client";

import {
  CheckCircle2Icon,
  ClockIcon,
  Loader2Icon,
  XCircleIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Empty, EmptyContent, EmptyDescription, EmptyTitle } from "@/components/ui/empty";
import type { Gate } from "@/core/living-agents/types";
import { cn } from "@/lib/utils";

export interface GateListProps {
  gates: Gate[];
  onApprove: (gate: Gate) => void;
  onReject: (gate: Gate) => void;
  isApproving: boolean;
  isRejecting: boolean;
  isLoading?: boolean;
}

function formatDate(dateString: string | null): string {
  if (!dateString) return "-";
  try {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMinutes = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);
    if (diffMinutes < 1) return "just now";
    if (diffMinutes < 60) return `${diffMinutes}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toISOString().slice(0, 16).replace("T", " ");
  } catch {
    return dateString;
  }
}

function GateCard({
  gate,
  onApprove,
  onReject,
  isApproving,
  isRejecting,
}: {
  gate: Gate;
  onApprove: (gate: Gate) => void;
  onReject: (gate: Gate) => void;
  isApproving: boolean;
  isRejecting: boolean;
}) {
  const isPending = gate.status === "pending";
  const isApproved = gate.status === "approved";
  const isResolved = !isPending;

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1 space-y-2">
            {/* Header: badge + gate id */}
            <div className="flex flex-wrap items-center gap-2">
              <Badge
                variant={isApproved ? "default" : "destructive"}
                className={cn(
                  "h-6 rounded-md px-2 text-xs font-medium capitalize",
                  isPending &&
                    "bg-secondary/60 text-secondary-foreground border-secondary",
                )}
              >
                {isPending ? (
                  <ClockIcon className="mr-1 size-3" />
                ) : isApproved ? (
                  <CheckCircle2Icon className="mr-1 size-3" />
                ) : (
                  <XCircleIcon className="mr-1 size-3" />
                )}
                {gate.status.charAt(0).toUpperCase() + gate.status.slice(1)}
              </Badge>
              <code className="text-muted-foreground rounded bg-transparent px-0 text-xs">
                {gate.gate_id}
              </code>
            </div>

            {/* Task info */}
            <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 text-xs">
              <span>
                Task: <code className="rounded bg-transparent">{gate.task_id}</code>
              </span>
              <span>Step: {gate.step_index}</span>
            </div>

            {/* Description */}
            <p className="text-foreground text-sm leading-relaxed">
              {gate.description}
            </p>

            {/* Dates */}
            <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 text-xs">
              <span>Created: {formatDate(gate.created_at)}</span>
              {isResolved && (
                <span>Resolved: {formatDate(gate.resolved_at)}</span>
              )}
            </div>

            {/* Resolution info */}
            {isApproved && gate.approved_by && (
              <div className="text-muted-foreground rounded-md bg-primary/5 p-2 text-xs">
                <p className="font-medium text-primary">
                  Approved by: {gate.approved_by}
                </p>
                {gate.human_input && (
                  <p className="mt-0.5 italic">
                    &ldquo;{gate.human_input}&rdquo;
                  </p>
                )}
              </div>
            )}
            {gate.status === "rejected" && (
              <div className="text-muted-foreground rounded-md bg-destructive/5 p-2 text-xs">
                <p className="font-medium text-destructive">
                  Rejected by: {gate.approved_by ?? "Unknown"}
                </p>
                {gate.human_input && (
                  <p className="mt-0.5 italic">
                    &ldquo;{gate.human_input}&rdquo;
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </CardContent>
      {isPending && (
        <CardFooter className="border-t bg-muted/30 px-4 py-2.5">
          <div className="flex w-full items-center justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={isApproving || isRejecting}
              onClick={() => onReject(gate)}
            >
              <XCircleIcon className="mr-1 size-3.5" />
              Reject
            </Button>
            <Button
              size="sm"
              disabled={isApproving || isRejecting}
              onClick={() => onApprove(gate)}
            >
              <CheckCircle2Icon className="mr-1 size-3.5" />
              Approve
            </Button>
          </div>
        </CardFooter>
      )}
    </Card>
  );
}

export function GateList({
  gates,
  onApprove,
  onReject,
  isApproving,
  isRejecting,
  isLoading = false,
}: GateListProps) {
  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2Icon className="size-4 animate-spin" />
          <span>Loading gates...</span>
        </div>
      </div>
    );
  }

  if (gates.length === 0) {
    return (
      <Empty>
        <EmptyContent>
          <EmptyTitle>No gates to review</EmptyTitle>
          <EmptyDescription>
            There are no approval gates requiring your attention at the
            moment.
          </EmptyDescription>
        </EmptyContent>
      </Empty>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {gates.map((gate) => (
        <GateCard
          key={gate.gate_id}
          gate={gate}
          onApprove={onApprove}
          onReject={onReject}
          isApproving={isApproving}
          isRejecting={isRejecting}
        />
      ))}
    </div>
  );
}
