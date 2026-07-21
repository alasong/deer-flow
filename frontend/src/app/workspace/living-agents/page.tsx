"use client";

import { BotIcon, RefreshCwIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { AgentTable } from "@/components/workspace/living-agents/agent-table";
import { useAgents, useDeleteAgent } from "@/core/living-agents/hooks";

export default function LivingAgentsPage() {
  const { data: agents, isLoading, error, refetch } = useAgents();
  const deleteAgent = useDeleteAgent();
  const [openDeleteId, setOpenDeleteId] = useState<string | null>(null);

  const handleDelete = async (agentId: string) => {
    try {
      await deleteAgent.mutateAsync(agentId);
      toast.success(`Agent "${agentId}" deleted successfully`);
      setOpenDeleteId(null);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to delete agent",
      );
    }
  };

  return (
    <div className="flex size-full flex-col">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">Living Agents</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            View and manage registered Living Agents. Agents can be assigned
            tasks based on their capabilities.
          </p>
        </div>
        <Button variant="outline" size="icon" onClick={() => refetch()}>
          <RefreshCwIcon className="h-4 w-4" />
          <span className="sr-only">Refresh</span>
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading
          ? (
            <LoadingState />
          )
          : error
          ? (
            <ErrorState
              message={error instanceof Error ? error.message : String(error)}
              onRetry={() => refetch()}
            />
          )
          : !agents || agents.length === 0
          ? (
            <EmptyState />
          )
          : (
            <AgentTable
              agents={agents}
              onDelete={handleDelete}
              deletePending={deleteAgent.isPending}
              openDeleteId={openDeleteId}
              onOpenDeleteChange={setOpenDeleteId}
            />
          )}
      </div>
    </div>
  );
}

/* ─── Sub-components ─── */

function LoadingState() {
  return (
    <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
      Loading agents...
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
      <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
        <BotIcon className="text-muted-foreground h-7 w-7" />
      </div>
      <div>
        <p className="font-medium text-red-600">Failed to load agents</p>
        <p className="text-muted-foreground mt-1 max-w-md text-sm">
          {message}
        </p>
      </div>
      <Button variant="outline" onClick={onRetry}>
        <RefreshCwIcon className="mr-1.5 h-4 w-4" />
        Retry
      </Button>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
      <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
        <BotIcon className="text-muted-foreground h-7 w-7" />
      </div>
      <div>
        <p className="font-medium">No registered agents</p>
        <p className="text-muted-foreground mt-1 max-w-md text-sm">
          No Living Agents are currently registered. Agents will appear here
          once they are registered through the system.
        </p>
      </div>
    </div>
  );
}
