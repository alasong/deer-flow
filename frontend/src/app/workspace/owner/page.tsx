"use client";

import { useEffect, useState, useCallback } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Agent {
  agent_id: string;
  name: string;
  capabilities: string[];
  status: string;
  created_at: string;
}

interface Task {
  task_id: string;
  capability: string;
  status: string;
}

interface BoardEntry {
  key: string;
  value: string;
  updated_at: string;
  updated_by: string;
}

interface Approval {
  task_id: string;
  reason: string;
  requested_by: string;
  status: string;
  created_at: string;
}

interface AgentsResponse {
  agents: Agent[];
}

interface QueueResponse {
  pending: Task[];
  active: Task[];
}

interface BoardResponse {
  entries: BoardEntry[];
}

interface ApprovalsResponse {
  approvals: Approval[];
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function truncate(value: string, max = 80): string {
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

// ---------------------------------------------------------------------------
// Data-fetching helper
// ---------------------------------------------------------------------------

function useApi<T>(url: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(url);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }
      const json = (await res.json()) as T;
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [url]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return { data, error, loading, refetch: fetchData };
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const variant =
    status === "active" || status === "approved"
      ? "default"
      : status === "pending"
        ? "secondary"
        : status === "rejected"
          ? "destructive"
          : "outline";
  return <Badge variant={variant}>{status}</Badge>;
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function OwnerPage() {
  const agentsQuery = useApi<AgentsResponse>("/api/owner/agents");
  const queueQuery = useApi<QueueResponse>("/api/owner/queue");
  const boardQuery = useApi<BoardResponse>("/api/owner/board");
  const approvalsQuery = useApi<ApprovalsResponse>("/api/owner/approvals");

  useEffect(() => {
    document.title = "Owner Dashboard - DeerFlow";
  }, []);

  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const handleApprove = useCallback(async (taskId: string) => {
    setActionLoading(taskId);
    setActionError(null);
    try {
      const res = await fetch(`/api/owner/approvals/${taskId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved_by: "owner-ui" }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail ?? res.statusText;
        throw new Error(detail);
      }
      await approvalsQuery.refetch();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to approve",
      );
    } finally {
      setActionLoading(null);
    }
  }, [approvalsQuery]);

  const handleReject = useCallback(async (taskId: string) => {
    setActionLoading(taskId);
    setActionError(null);
    try {
      const res = await fetch(`/api/owner/approvals/${taskId}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rejected_by: "owner-ui", reason: "" }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail ?? res.statusText;
        throw new Error(detail);
      }
      await approvalsQuery.refetch();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Failed to reject",
      );
    } finally {
      setActionLoading(null);
    }
  }, [approvalsQuery]);

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody>
        <div className="mx-auto flex w-full max-w-(--container-width-lg) flex-col gap-6 p-6">
          <h1 className="text-2xl font-semibold">Owner Dashboard</h1>

          <Tabs defaultValue="agents">
            <TabsList>
              <TabsTrigger value="agents">Agents</TabsTrigger>
              <TabsTrigger value="queue">Queue</TabsTrigger>
              <TabsTrigger value="board">Board</TabsTrigger>
              <TabsTrigger value="approvals">Approvals</TabsTrigger>
            </TabsList>

            {/* ───── Agents ───── */}
            <TabsContent value="agents">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>Agents</CardTitle>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => agentsQuery.refetch()}
                    disabled={agentsQuery.loading}
                  >
                    {agentsQuery.loading ? "Loading…" : "Refresh"}
                  </Button>
                </CardHeader>
                <CardContent>
                  {agentsQuery.error && (
                    <div className="text-destructive mb-4 text-sm">
                      Failed to load agents: {agentsQuery.error}
                    </div>
                  )}
                  {agentsQuery.loading ? (
                    <div className="text-muted-foreground text-sm">
                      Loading…
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Agent ID</TableHead>
                            <TableHead>Name</TableHead>
                            <TableHead>Capabilities</TableHead>
                            <TableHead>Status</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {agentsQuery.data?.agents.map((agent) => (
                            <TableRow key={agent.agent_id}>
                              <TableCell className="font-mono text-xs">
                                {agent.agent_id}
                              </TableCell>
                              <TableCell className="font-medium">
                                {agent.name}
                              </TableCell>
                              <TableCell>
                                <div className="flex flex-wrap gap-1">
                                  {agent.capabilities.map((cap) => (
                                    <Badge key={cap} variant="outline">
                                      {cap}
                                    </Badge>
                                  ))}
                                </div>
                              </TableCell>
                              <TableCell>
                                <StatusBadge status={agent.status} />
                              </TableCell>
                            </TableRow>
                          ))}
                          {(agentsQuery.data?.agents ?? []).length === 0 && (
                            <TableRow>
                              <TableCell
                                colSpan={4}
                                className="text-muted-foreground text-center"
                              >
                                No agents found
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            {/* ───── Queue ───── */}
            <TabsContent value="queue">
              <div className="grid gap-6 lg:grid-cols-2">
                <Card>
                  <CardHeader className="flex flex-row items-center justify-between">
                    <CardTitle>Pending</CardTitle>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => queueQuery.refetch()}
                      disabled={queueQuery.loading}
                    >
                      {queueQuery.loading ? "Loading…" : "Refresh"}
                    </Button>
                  </CardHeader>
                  <CardContent>
                    {queueQuery.loading ? (
                      <div className="text-muted-foreground text-sm">
                        Loading…
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Task ID</TableHead>
                              <TableHead>Capability</TableHead>
                              <TableHead>Status</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {queueQuery.data?.pending.map((task) => (
                              <TableRow key={task.task_id}>
                                <TableCell className="font-mono text-xs">
                                  {task.task_id}
                                </TableCell>
                                <TableCell>{task.capability}</TableCell>
                                <TableCell>
                                  <StatusBadge status={task.status} />
                                </TableCell>
                              </TableRow>
                            ))}
                            {(queueQuery.data?.pending ?? []).length ===
                              0 && (
                              <TableRow>
                                <TableCell
                                  colSpan={3}
                                  className="text-muted-foreground text-center"
                                >
                                  No pending tasks
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader className="flex flex-row items-center justify-between">
                    <CardTitle>Active</CardTitle>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => queueQuery.refetch()}
                      disabled={queueQuery.loading}
                    >
                      {queueQuery.loading ? "Loading…" : "Refresh"}
                    </Button>
                  </CardHeader>
                  <CardContent>
                    {queueQuery.loading ? (
                      <div className="text-muted-foreground text-sm">
                        Loading…
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Task ID</TableHead>
                              <TableHead>Capability</TableHead>
                              <TableHead>Status</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {queueQuery.data?.active.map((task) => (
                              <TableRow key={task.task_id}>
                                <TableCell className="font-mono text-xs">
                                  {task.task_id}
                                </TableCell>
                                <TableCell>{task.capability}</TableCell>
                                <TableCell>
                                  <StatusBadge status={task.status} />
                                </TableCell>
                              </TableRow>
                            ))}
                            {(queueQuery.data?.active ?? []).length === 0 && (
                              <TableRow>
                                <TableCell
                                  colSpan={3}
                                  className="text-muted-foreground text-center"
                                >
                                  No active tasks
                                </TableCell>
                              </TableRow>
                            )}
                          </TableBody>
                        </Table>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {queueQuery.error && (
                  <div className="text-destructive col-span-full text-sm">
                    Failed to load queue: {queueQuery.error}
                  </div>
                )}
              </div>
            </TabsContent>

            {/* ───── Board ───── */}
            <TabsContent value="board">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>Board</CardTitle>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => boardQuery.refetch()}
                    disabled={boardQuery.loading}
                  >
                    {boardQuery.loading ? "Loading…" : "Refresh"}
                  </Button>
                </CardHeader>
                <CardContent>
                  {boardQuery.error && (
                    <div className="text-destructive mb-4 text-sm">
                      Failed to load board: {boardQuery.error}
                    </div>
                  )}
                  {boardQuery.loading ? (
                    <div className="text-muted-foreground text-sm">
                      Loading…
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Key</TableHead>
                            <TableHead>Value</TableHead>
                            <TableHead>Updated At</TableHead>
                            <TableHead>Updated By</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {boardQuery.data?.entries.map((entry) => (
                            <TableRow key={entry.key}>
                              <TableCell className="font-mono text-xs font-medium">
                                {entry.key}
                              </TableCell>
                              <TableCell className="max-w-xs truncate">
                                {truncate(entry.value)}
                              </TableCell>
                              <TableCell>
                                {formatTimestamp(entry.updated_at)}
                              </TableCell>
                              <TableCell>{entry.updated_by}</TableCell>
                            </TableRow>
                          ))}
                          {(boardQuery.data?.entries ?? []).length === 0 && (
                            <TableRow>
                              <TableCell
                                colSpan={4}
                                className="text-muted-foreground text-center"
                              >
                                No board entries
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            {/* ───── Approvals ───── */}
            <TabsContent value="approvals">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle>Approvals</CardTitle>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => approvalsQuery.refetch()}
                    disabled={approvalsQuery.loading}
                  >
                    {approvalsQuery.loading ? "Loading…" : "Refresh"}
                  </Button>
                </CardHeader>
                <CardContent>
                  {approvalsQuery.error && (
                    <div className="text-destructive mb-4 text-sm">
                      Failed to load approvals: {approvalsQuery.error}
                    </div>
                  )}
                  {actionError && (
                    <div className="text-destructive mb-4 text-sm">
                      Action failed: {actionError}
                    </div>
                  )}
                  {approvalsQuery.loading ? (
                    <div className="text-muted-foreground text-sm">
                      Loading…
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Task ID</TableHead>
                            <TableHead>Reason</TableHead>
                            <TableHead>Requested By</TableHead>
                            <TableHead>Status</TableHead>
                            <TableHead className="text-right">
                              Actions
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {approvalsQuery.data?.approvals.map((approval) => (
                            <TableRow key={approval.task_id}>
                              <TableCell className="font-mono text-xs">
                                {approval.task_id}
                              </TableCell>
                              <TableCell className="max-w-xs truncate">
                                {truncate(approval.reason)}
                              </TableCell>
                              <TableCell>{approval.requested_by}</TableCell>
                              <TableCell>
                                <StatusBadge status={approval.status} />
                              </TableCell>
                              <TableCell className="text-right">
                                {approval.status === "pending" ? (
                                  <div className="flex justify-end gap-1">
                                    <Button
                                      size="sm"
                                      variant="default"
                                      disabled={actionLoading === approval.task_id}
                                      onClick={() => handleApprove(approval.task_id)}
                                    >
                                      {actionLoading === approval.task_id
                                        ? "Loading…"
                                        : "Approve"}
                                    </Button>
                                    <Button
                                      size="sm"
                                      variant="destructive"
                                      disabled={actionLoading === approval.task_id}
                                      onClick={() => handleReject(approval.task_id)}
                                    >
                                      {actionLoading === approval.task_id
                                        ? "Loading…"
                                        : "Reject"}
                                    </Button>
                                  </div>
                                ) : (
                                  <span className="text-muted-foreground text-xs">
                                    {approval.status === "approved"
                                      ? "Approved"
                                      : "Rejected"}
                                  </span>
                                )}
                              </TableCell>
                            </TableRow>
                          ))}
                          {(approvalsQuery.data?.approvals ?? []).length ===
                            0 && (
                            <TableRow>
                              <TableCell
                                colSpan={5}
                                className="text-muted-foreground text-center"
                              >
                                No approvals
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
