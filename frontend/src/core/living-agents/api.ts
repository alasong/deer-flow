import { fetch } from "@/core/api/fetcher";

import type { Agent, CreateAgentRequest, Gate, GateActionRequest, SubmitTaskRequest, Task } from "./types";

const BASE = "/api/agents";

export async function listAgents(): Promise<Agent[]> {
  const res = await fetch(`${BASE}/agents`);
  if (!res.ok) throw new Error(`Failed to load agents: ${res.statusText}`);
  const data = (await res.json()) as { agents: Agent[] };
  return data.agents;
}

export async function getAgent(agentId: string): Promise<Agent> {
  const res = await fetch(`${BASE}/agents/${encodeURIComponent(agentId)}`);
  if (!res.ok) throw new Error(`Agent '${agentId}' not found`);
  return res.json() as Promise<Agent>;
}

export async function createAgent(request: CreateAgentRequest): Promise<Agent> {
  const res = await fetch(`${BASE}/agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Failed to create agent: ${res.statusText}`);
  }
  return res.json() as Promise<Agent>;
}

export async function deleteAgent(agentId: string): Promise<void> {
  const res = await fetch(`${BASE}/agents/${encodeURIComponent(agentId)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete agent: ${res.statusText}`);
}

export async function listTasks(status?: string): Promise<Task[]> {
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  const res = await fetch(`${BASE}/tasks${params}`);
  if (!res.ok) throw new Error(`Failed to load tasks: ${res.statusText}`);
  return res.json() as Promise<Task[]>;
}

export async function getTask(taskId: string): Promise<Task> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok) throw new Error(`Task '${taskId}' not found`);
  return res.json() as Promise<Task>;
}

export async function submitTask(request: SubmitTaskRequest): Promise<{ task_id: string; status: string }> {
  const res = await fetch(`${BASE}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(err.detail ?? `Failed to submit task: ${res.statusText}`);
  }
  return res.json() as Promise<{ task_id: string; status: string }>;
}

export async function cancelTask(taskId: string): Promise<Task> {
  const res = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to cancel task: ${res.statusText}`);
  return res.json() as Promise<Task>;
}

export async function listGates(status?: string, taskId?: string): Promise<Gate[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (taskId) params.set("task_id", taskId);
  const qs = params.toString();
  const res = await fetch(`${BASE}/gates${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Failed to load gates: ${res.statusText}`);
  const data = (await res.json()) as { gates: Gate[] };
  return data.gates;
}

export async function getGate(gateId: string): Promise<Gate> {
  const res = await fetch(`${BASE}/gates/${encodeURIComponent(gateId)}`);
  if (!res.ok) throw new Error(`Gate '${gateId}' not found`);
  return res.json() as Promise<Gate>;
}

export async function approveGate(gateId: string, request?: GateActionRequest): Promise<Gate> {
  const res = await fetch(`${BASE}/gates/${encodeURIComponent(gateId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request ?? {}),
  });
  if (!res.ok) throw new Error(`Failed to approve gate: ${res.statusText}`);
  return res.json() as Promise<Gate>;
}

export async function rejectGate(gateId: string, request?: GateActionRequest): Promise<Gate> {
  const res = await fetch(`${BASE}/gates/${encodeURIComponent(gateId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request ?? {}),
  });
  if (!res.ok) throw new Error(`Failed to reject gate: ${res.statusText}`);
  return res.json() as Promise<Gate>;
}
