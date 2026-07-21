import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";

const mockedFetch = rs.mocked(fetcher);

describe("living-agents api", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  function jsonResponse(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  // -----------------------------------------------------------------------
  // Agents
  // -----------------------------------------------------------------------
  test("listAgents returns agents", async () => {
    const { listAgents } = await import("@/core/living-agents/api");
    const body = {
      agents: [
        { agent_id: "a1", name: "Agent 1", capabilities: ["dev"], status: "idle", access_level: "exec", skills: [] },
      ],
      count: 1,
    };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const agents = await listAgents();
    expect(agents).toEqual(body.agents);
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/agents");
  });

  test("listAgents rejects non-ok", async () => {
    const { listAgents } = await import("@/core/living-agents/api");
    mockedFetch.mockResolvedValue(new Response(null, { status: 500 }));
    await expect(listAgents()).rejects.toThrow("Failed to load agents");
  });

  test("getAgent returns single agent", async () => {
    const { getAgent } = await import("@/core/living-agents/api");
    const body = { agent_id: "a1", name: "Agent 1", capabilities: ["dev"], status: "idle", access_level: "exec", skills: [] };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const agent = await getAgent("a1");
    expect(agent.agent_id).toBe("a1");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/agents/a1");
  });

  test("getAgent rejects not-found", async () => {
    const { getAgent } = await import("@/core/living-agents/api");
    mockedFetch.mockResolvedValue(new Response(null, { status: 404 }));
    await expect(getAgent("nonexistent")).rejects.toThrow("not found");
  });

  test("createAgent sends POST and returns agent", async () => {
    const { createAgent } = await import("@/core/living-agents/api");
    const body = { agent_id: "a2", name: "Agent 2", capabilities: ["ops"], status: "idle", access_level: "exec", skills: [] };
    mockedFetch.mockResolvedValue(jsonResponse(201, body));
    const agent = await createAgent({ agent_id: "a2", name: "Agent 2", capabilities: ["ops"] });
    expect(agent.agent_id).toBe("a2");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/agents", expect.objectContaining({ method: "POST" }));
  });

  test("deleteAgent sends DELETE", async () => {
    const { deleteAgent } = await import("@/core/living-agents/api");
    mockedFetch.mockResolvedValue(jsonResponse(200, { status: "deleted" }));
    await deleteAgent("a1");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/agents/a1", expect.objectContaining({ method: "DELETE" }));
  });

  // -----------------------------------------------------------------------
  // Tasks
  // -----------------------------------------------------------------------
  test("submitTask sends POST", async () => {
    const { submitTask } = await import("@/core/living-agents/api");
    mockedFetch.mockResolvedValue(jsonResponse(201, { task_id: "t1", status: "pending" }));
    const result = await submitTask({ capability: "dev", description: "test task" });
    expect(result.task_id).toBe("t1");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/tasks", expect.objectContaining({ method: "POST" }));
  });

  test("listTasks returns tasks", async () => {
    const { listTasks } = await import("@/core/living-agents/api");
    const tasks = [
      { task_id: "t1", capability: "dev", description: "task", status: "pending", agent_id: null, checkpoint_id: null, result: null, error: null },
    ];
    mockedFetch.mockResolvedValue(jsonResponse(200, tasks));
    const result = await listTasks();
    expect(result).toEqual(tasks);
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/tasks");
  });

  test("listTasks with status filter", async () => {
    const { listTasks } = await import("@/core/living-agents/api");
    mockedFetch.mockResolvedValue(jsonResponse(200, []));
    await listTasks("pending");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/tasks?status=pending");
  });

  test("getTask returns task", async () => {
    const { getTask } = await import("@/core/living-agents/api");
    const body = { task_id: "t1", capability: "dev", description: "task", status: "pending", agent_id: null, checkpoint_id: null, result: null, error: null };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const task = await getTask("t1");
    expect(task.task_id).toBe("t1");
  });

  test("cancelTask sends POST to cancel", async () => {
    const { cancelTask } = await import("@/core/living-agents/api");
    const body = { task_id: "t1", capability: "dev", description: "task", status: "cancelled", agent_id: null, checkpoint_id: null, result: null, error: null };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const result = await cancelTask("t1");
    expect(result.status).toBe("cancelled");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/tasks/t1/cancel", expect.objectContaining({ method: "POST" }));
  });

  // -----------------------------------------------------------------------
  // Gates
  // -----------------------------------------------------------------------
  test("listGates returns gates", async () => {
    const { listGates } = await import("@/core/living-agents/api");
    const body = {
      gates: [
        { gate_id: "g1", task_id: "t1", step_index: 0, description: "approve?", status: "pending", created_at: "2025-01-01", resolved_at: null, approved_by: null, human_input: null },
      ],
      count: 1,
    };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const gates = await listGates();
    expect(gates).toEqual(body.gates);
  });

  test("listGates with filters", async () => {
    const { listGates } = await import("@/core/living-agents/api");
    mockedFetch.mockResolvedValue(jsonResponse(200, { gates: [], count: 0 }));
    await listGates("pending", "t1");
    expect(mockedFetch).toHaveBeenCalledWith("/api/agents/gates?status=pending&task_id=t1");
  });

  test("approveGate sends POST", async () => {
    const { approveGate } = await import("@/core/living-agents/api");
    const body = { gate_id: "g1", task_id: "t1", step_index: 0, description: "approve?", status: "approved", created_at: "2025-01-01", resolved_at: "2025-01-02", approved_by: "admin", human_input: "ok" };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const gate = await approveGate("g1", { approved_by: "admin", human_input: "ok" });
    expect(gate.status).toBe("approved");
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/agents/gates/g1/approve",
      expect.objectContaining({ method: "POST" }),
    );
  });

  test("rejectGate sends POST", async () => {
    const { rejectGate } = await import("@/core/living-agents/api");
    const body = { gate_id: "g1", task_id: "t1", step_index: 0, description: "approve?", status: "rejected", created_at: "2025-01-01", resolved_at: "2025-01-02", approved_by: "admin", human_input: "no" };
    mockedFetch.mockResolvedValue(jsonResponse(200, body));
    const gate = await rejectGate("g1");
    expect(gate.status).toBe("rejected");
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/agents/gates/g1/reject",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
