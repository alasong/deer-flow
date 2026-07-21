"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TaskSubmitForm } from "@/components/workspace/living-agents/task-submit-form";
import { TaskTable } from "@/components/workspace/living-agents/task-table";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import { useCancelTask, useTasks } from "@/core/living-agents/hooks";

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "all", label: "All" },
  { value: "pending", label: "Pending" },
  { value: "claimed", label: "Claimed" },
  { value: "executing", label: "Executing" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
];

export default function TasksPage() {
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const tasksQuery = useTasks(
    statusFilter === "all" ? undefined : statusFilter,
  );
  const cancelTask = useCancelTask();

  useEffect(() => {
    document.title = "Tasks - DeerFlow";
  }, []);

  const handleCancel = (taskId: string) => {
    cancelTask.mutate(taskId);
  };

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <div className="mx-auto flex w-full max-w-(--container-width-lg) flex-col gap-4 p-6">
          <h1 className="text-2xl font-semibold">Living Agent Tasks</h1>

          <TaskSubmitForm />

          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium">Task List</h2>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder="Filter by status" />
              </SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {tasksQuery.isLoading ? (
            <div className="text-muted-foreground flex items-center justify-center gap-2 py-12 text-sm">
              <Loader2 className="size-4 animate-spin" />
              Loading tasks...
            </div>
          ) : tasksQuery.error ? (
            <div className="text-destructive flex items-center gap-2 rounded-lg border border-red-500/30 p-4 text-sm">
              <AlertCircle className="size-4" />
              Failed to load tasks: {tasksQuery.error.message}
            </div>
          ) : (
            <TaskTable
              tasks={tasksQuery.data ?? []}
              onCancel={handleCancel}
              isCancelling={cancelTask.isPending}
            />
          )}
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
