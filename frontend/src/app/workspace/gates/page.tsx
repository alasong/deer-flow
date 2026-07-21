"use client";

import {
  ArrowLeftIcon,
  ClipboardCheckIcon,
  XCircleIcon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { GateActionDialog } from "@/components/workspace/living-agents/gate-action-dialog";
import { GateList } from "@/components/workspace/living-agents/gate-list";
import {
  useApproveGate,
  useGates,
  useRejectGate,
} from "@/core/living-agents/hooks";
import type { Gate } from "@/core/living-agents/types";

type FilterValue = "all" | "pending" | "approved" | "rejected";

const FILTERS: { value: FilterValue; label: string }[] = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

export default function GatesPage() {
  const router = useRouter();
  const { data: gates, isLoading, error } = useGates();
  const approveGate = useApproveGate();
  const rejectGate = useRejectGate();

  const [filter, setFilter] = useState<FilterValue>("all");
  const [selectedGate, setSelectedGate] = useState<Gate | null>(null);
  const [dialogAction, setDialogAction] = useState<"approve" | "reject">(
    "approve",
  );
  const [dialogOpen, setDialogOpen] = useState(false);

  const filteredGates = useMemo(() => {
    if (!gates) return [];
    if (filter === "all") return gates;
    return gates.filter((g) => g.status === filter);
  }, [gates, filter]);

  const handleApprove = (gate: Gate) => {
    setSelectedGate(gate);
    setDialogAction("approve");
    setDialogOpen(true);
  };

  const handleReject = (gate: Gate) => {
    setSelectedGate(gate);
    setDialogAction("reject");
    setDialogOpen(true);
  };

  const handleConfirm = async (gateId: string, humanInput: string) => {
    if (dialogAction === "approve") {
      await approveGate.mutateAsync({
        gateId,
        request: { human_input: humanInput || undefined },
      });
    } else {
      await rejectGate.mutateAsync({
        gateId,
        request: { human_input: humanInput || undefined },
      });
    }
    setDialogOpen(false);
    setSelectedGate(null);
  };

  const isPending = approveGate.isPending || rejectGate.isPending;

  return (
    <div className="flex size-full flex-col">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            onClick={() => router.push("/workspace")}
          >
            <ArrowLeftIcon className="size-4" />
          </Button>
          <div>
            <h1 className="text-xl font-semibold">Gates Approval</h1>
            <p className="text-muted-foreground mt-0.5 text-sm">
              Review and manage approval gates for agent tasks.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ClipboardCheckIcon className="text-muted-foreground size-4" />
          <span className="text-muted-foreground text-sm">
            {gates?.filter((g) => g.status === "pending").length ?? 0} pending
          </span>
        </div>
      </div>

      {/* Content */}
      <div className="flex flex-1 flex-col overflow-y-auto">
        {/* Error state */}
        {error && (
          <div className="flex h-40 items-center justify-center">
            <div className="text-destructive flex items-center gap-2 text-sm">
              <XCircleIcon className="size-4" />
              <span>
                Failed to load gates: {error.message ?? "Unknown error"}
              </span>
            </div>
          </div>
        )}

        {/* Filter tabs + gate list */}
        {!error && (
          <div className="flex flex-1 flex-col p-4 pt-3">
            <Tabs
              value={filter}
              onValueChange={(v) => setFilter(v as FilterValue)}
            >
              <TabsList variant="line" className="h-auto">
                {FILTERS.map((f) => (
                  <TabsTrigger key={f.value} value={f.value}>
                    {f.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>

            <div className="mt-4 flex-1">
              <GateList
                gates={filteredGates}
                onApprove={handleApprove}
                onReject={handleReject}
                isApproving={isPending}
                isRejecting={isPending}
                isLoading={isLoading}
              />
            </div>
          </div>
        )}
      </div>

      {/* Action dialog */}
      <GateActionDialog
        gate={selectedGate}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onConfirm={handleConfirm}
        action={dialogAction}
      />
    </div>
  );
}
