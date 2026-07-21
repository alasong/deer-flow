import { Loader2, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Task } from "@/core/living-agents/types";

import { canCancelTask } from "./task-helpers";
import { TaskStatusBadge } from "./task-status-badge";

export interface TaskTableProps {
  tasks: Task[];
  onCancel: (taskId: string) => void;
  isCancelling: boolean;
}

export function TaskTable({ tasks, onCancel, isCancelling }: TaskTableProps) {
  if (tasks.length === 0) {
    return (
      <div className="text-muted-foreground flex flex-col items-center gap-2 py-12 text-sm">
        <XCircle className="size-8" />
        <span>No tasks found</span>
      </div>
    );
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Task ID</TableHead>
            <TableHead>Capability</TableHead>
            <TableHead className="max-w-[300px]">Description</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Agent</TableHead>
            <TableHead>Error</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasks.map((task) => (
            <TableRow key={task.task_id}>
              <TableCell className="max-w-[140px] truncate font-mono text-xs">
                {task.task_id}
              </TableCell>
              <TableCell className="font-medium">{task.capability}</TableCell>
              <TableCell className="max-w-[300px] truncate">
                {task.description}
              </TableCell>
              <TableCell>
                <TaskStatusBadge status={task.status} />
              </TableCell>
              <TableCell className="text-muted-foreground text-xs">
                {task.agent_id ?? "--"}
              </TableCell>
              <TableCell className="max-w-[200px] truncate text-xs text-red-500">
                {task.error ?? "--"}
              </TableCell>
              <TableCell className="text-right">
                {canCancelTask(task.status) && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => onCancel(task.task_id)}
                    disabled={isCancelling}
                  >
                    {isCancelling ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : null}
                    Cancel
                  </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
