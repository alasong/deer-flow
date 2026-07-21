"use client";

import { Trash2Icon } from "lucide-react";

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
import type { Agent } from "@/core/living-agents/types";
import { cn } from "@/lib/utils";

import { getAccessLevelVariant } from "./helpers";
import { StatusBadge } from "./status-badge";

interface AgentTableProps {
  agents: Agent[];
  onDelete: (agentId: string) => void;
  deletePending?: boolean;
  openDeleteId?: string | null;
  onOpenDeleteChange?: (id: string | null) => void;
}

/**
 * A responsive table that lists registered Living Agents with their
 * metadata and delete action.
 */
export function AgentTable({
  agents,
  onDelete,
  deletePending = false,
  openDeleteId = null,
  onOpenDeleteChange,
}: AgentTableProps) {
  const handleDeleteDialog = (agentId: string | null) => {
    onOpenDeleteChange?.(agentId);
  };

  return (
    <>
      <div className="overflow-x-auto rounded-lg border">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-muted/50">
            <tr>
              <Th>Agent ID</Th>
              <Th>Name</Th>
              <Th>Capabilities</Th>
              <Th>Status</Th>
              <Th>Access Level</Th>
              <Th>Skills</Th>
              <Th className="w-20 text-right">Actions</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 bg-background">
            {agents.map((agent) => (
              <tr
                key={agent.agent_id}
                className="transition-colors hover:bg-muted/30"
              >
                <Td className="font-mono text-xs">{agent.agent_id}</Td>
                <Td className="font-medium">{agent.name}</Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {agent.capabilities.length > 0
                      ? agent.capabilities.map((cap) => (
                          <Badge
                            key={cap}
                            variant="outline"
                            className="text-xs"
                          >
                            {cap}
                          </Badge>
                        ))
                      : (
                        <span className="text-muted-foreground text-xs">
                          —
                        </span>
                      )}
                  </div>
                </Td>
                <Td>
                  <StatusBadge status={agent.status} />
                </Td>
                <Td>
                  <Badge
                    variant={getAccessLevelVariant(
                      agent.access_level,
                    ) as "default" | "secondary" | "destructive" | "outline"}
                    className="text-xs"
                  >
                    {agent.access_level}
                  </Badge>
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {agent.skills.length > 0
                      ? agent.skills.map((skill) => (
                          <Badge
                            key={skill}
                            variant="secondary"
                            className="text-xs"
                          >
                            {skill}
                          </Badge>
                        ))
                      : (
                        <span className="text-muted-foreground text-xs">
                          —
                        </span>
                      )}
                  </div>
                </Td>
                <Td className="text-right">
                  <Button
                    size="icon"
                    variant="ghost"
                    className="text-destructive hover:text-destructive h-8 w-8"
                    onClick={() => handleDeleteDialog(agent.agent_id)}
                    title="Delete agent"
                  >
                    <Trash2Icon className="h-3.5 w-3.5" />
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Delete confirmation dialog */}
      <Dialog
        open={!!openDeleteId}
        onOpenChange={(open) => {
          if (!open) handleDeleteDialog(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Agent</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete agent{" "}
              <span className="font-medium">{openDeleteId}</span>? This action
              cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => handleDeleteDialog(null)}
              disabled={deletePending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (openDeleteId) {
                  onDelete(openDeleteId);
                }
              }}
              disabled={deletePending}
            >
              {deletePending ? "Deleting..." : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

/* ─── Table cell helpers ─── */

function Th({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <th
      className={cn(
        "px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500",
        className,
      )}
    >
      {children}
    </th>
  );
}

function Td({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <td className={cn("px-4 py-3 text-sm", className)}>{children}</td>
  );
}
