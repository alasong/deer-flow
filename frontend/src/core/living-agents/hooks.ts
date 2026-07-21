import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  approveGate,
  cancelTask,
  createAgent,
  deleteAgent,
  getAgent,
  getGate,
  getTask,
  listAgents,
  listGates,
  listTasks,
  rejectGate,
  submitTask,
} from "./api";
import type { CreateAgentRequest, GateActionRequest, SubmitTaskRequest } from "./types";

export function useAgents() {
  return useQuery({
    queryKey: ["living-agents"],
    queryFn: () => listAgents(),
  });
}

export function useAgent(agentId: string | null | undefined) {
  return useQuery({
    queryKey: ["living-agents", agentId],
    queryFn: () => getAgent(agentId!),
    enabled: !!agentId,
  });
}

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateAgentRequest) => createAgent(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["living-agents"] });
    },
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) => deleteAgent(agentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["living-agents"] });
    },
  });
}

export function useTasks(status?: string) {
  return useQuery({
    queryKey: ["living-tasks", status],
    queryFn: () => listTasks(status),
  });
}

export function useTask(taskId: string | null | undefined) {
  return useQuery({
    queryKey: ["living-tasks", taskId],
    queryFn: () => getTask(taskId!),
    enabled: !!taskId,
  });
}

export function useSubmitTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: SubmitTaskRequest) => submitTask(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["living-tasks"] });
    },
  });
}

export function useCancelTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (taskId: string) => cancelTask(taskId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["living-tasks"] });
    },
  });
}

export function useGates(status?: string, taskId?: string) {
  return useQuery({
    queryKey: ["living-gates", status, taskId],
    queryFn: () => listGates(status, taskId),
  });
}

export function useGate(gateId: string | null | undefined) {
  return useQuery({
    queryKey: ["living-gates", gateId],
    queryFn: () => getGate(gateId!),
    enabled: !!gateId,
  });
}

export function useApproveGate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ gateId, request }: { gateId: string; request?: GateActionRequest }) =>
      approveGate(gateId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["living-gates"] });
      void queryClient.invalidateQueries({ queryKey: ["living-tasks"] });
    },
  });
}

export function useRejectGate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ gateId, request }: { gateId: string; request?: GateActionRequest }) =>
      rejectGate(gateId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["living-gates"] });
      void queryClient.invalidateQueries({ queryKey: ["living-tasks"] });
    },
  });
}
