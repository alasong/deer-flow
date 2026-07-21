export interface Agent {
  agent_id: string;
  name: string;
  capabilities: string[];
  status: AgentStatus;
  access_level: AccessLevel;
  skills: string[];
}

export type AgentStatus = "idle" | "active" | "paused" | "disabled";
export type AccessLevel = "exec" | "observe" | "admin";

export interface CreateAgentRequest {
  agent_id: string;
  name: string;
  capabilities?: string[];
  access_level?: string;
  skills?: string[];
}

export interface Task {
  task_id: string;
  capability: string;
  description: string;
  status: TaskStatus;
  agent_id: string | null;
  checkpoint_id: string | null;
  result: Record<string, unknown> | null;
  error: string | null;
}

export type TaskStatus =
  | "pending"
  | "claimed"
  | "executing"
  | "completed"
  | "failed";

export interface SubmitTaskRequest {
  capability: string;
  description: string;
  payload?: Record<string, unknown>;
}

export interface Gate {
  gate_id: string;
  task_id: string;
  step_index: number;
  description: string;
  status: GateStatus;
  created_at: string;
  resolved_at: string | null;
  approved_by: string | null;
  human_input: string | null;
}

export type GateStatus = "pending" | "approved" | "rejected";

export interface GateActionRequest {
  approved_by?: string;
  human_input?: string;
}
