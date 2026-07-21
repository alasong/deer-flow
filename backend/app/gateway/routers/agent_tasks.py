from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from deerflow.agents.model import Agent as AgentModel
from deerflow.agents.model import AgentStatus
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.tasks.gate import GateStatus, HumanGate
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.model import Task, TaskStatus
from deerflow.tasks.store import TaskStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TaskSubmitRequest(BaseModel):
    capability: str
    description: str
    payload: dict[str, Any] = {}


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: str


class TaskClaimRequest(BaseModel):
    agent_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    capability: str
    description: str
    status: str
    agent_id: str | None = None
    checkpoint_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class AgentCreateRequest(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str] = []
    access_level: str = "exec"
    skills: list[str] = []


class AgentResponse(BaseModel):
    agent_id: str
    name: str
    capabilities: list[str]
    status: str
    access_level: str
    skills: list[str]


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
    count: int


# ---------------------------------------------------------------------------
# Dependencies (stores injected via app.state)
# ---------------------------------------------------------------------------


def get_registry(request: Request) -> AgentRegistry:
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not configured")
    return registry


def get_task_store(request: Request) -> TaskStore:
    store = getattr(request.app.state, "task_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Task store not configured")
    return store


def get_gate_store(request: Request) -> HumanGateStore:
    store = getattr(request.app.state, "gate_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Gate store not configured")
    return store


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------


@router.post("/agents", response_model=AgentResponse)
def create_agent(req: AgentCreateRequest, registry: AgentRegistry = Depends(get_registry)) -> AgentResponse:
    """Register a new agent."""
    existing = registry.get(req.agent_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Agent {req.agent_id!r} already exists")
    agent = AgentModel(
        agent_id=req.agent_id,
        name=req.name,
        capabilities=req.capabilities,
        access_level=req.access_level,
        skills=req.skills,
    )
    registry.register(agent)
    return _agent_to_response(agent)


@router.get("/agents", response_model=AgentListResponse)
def list_agents(registry: AgentRegistry = Depends(get_registry)) -> AgentListResponse:
    """List all registered agents."""
    agents = registry.list()
    return AgentListResponse(
        agents=[_agent_to_response(a) for a in agents],
        count=len(agents),
    )


@router.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str, registry: AgentRegistry = Depends(get_registry)) -> AgentResponse:
    """Get a specific agent."""
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return _agent_to_response(agent)


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, registry: AgentRegistry = Depends(get_registry)) -> dict[str, str]:
    """Unregister an agent."""
    if not registry.unregister(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=TaskSubmitResponse)
def submit_task(req: TaskSubmitRequest, store: TaskStore = Depends(get_task_store)) -> TaskSubmitResponse:
    """Submit a new task to the task queue."""
    import uuid

    task_id = f"task_{uuid.uuid4().hex[:12]}"
    task = Task(
        task_id=task_id,
        capability=req.capability,
        description=req.description,
        payload=req.payload,
    )
    store.put(task)
    logger.info("Task submitted: %s (cap=%s)", task_id, req.capability)
    return TaskSubmitResponse(task_id=task_id, status=task.status.value)


@router.get("/tasks", response_model=list[TaskStatusResponse])
def list_tasks(
    status: str | None = None,
    store: TaskStore = Depends(get_task_store),
) -> list[TaskStatusResponse]:
    """List tasks, optionally filtered by status."""
    status_filter = TaskStatus(status) if status else None
    tasks = store.list(status=status_filter)
    return [_task_to_response(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str, store: TaskStore = Depends(get_task_store)) -> TaskStatusResponse:
    """Get task status and result."""
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return _task_to_response(task)


@router.post("/tasks/{task_id}/claim", response_model=TaskStatusResponse)
def claim_task(
    task_id: str,
    req: TaskClaimRequest,
    store: TaskStore = Depends(get_task_store),
) -> TaskStatusResponse:
    """Claim a pending task for execution."""
    try:
        task = store.claim(task_id, req.agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _task_to_response(task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatusResponse)
def cancel_task(task_id: str, store: TaskStore = Depends(get_task_store)) -> TaskStatusResponse:
    """Cancel a pending or claimed task."""
    try:
        task = store.cancel(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _task_to_response(task)


# ---------------------------------------------------------------------------
# Gate endpoints
# ---------------------------------------------------------------------------


class GateResponse(BaseModel):
    gate_id: str
    task_id: str
    step_index: int
    description: str
    status: str
    created_at: str
    resolved_at: str | None = None
    approved_by: str | None = None
    human_input: str | None = None


class GateApproveRequest(BaseModel):
    approved_by: str = ""
    human_input: str = ""


class GateListResponse(BaseModel):
    gates: list[GateResponse]
    count: int


def _gate_to_response(gate: HumanGate) -> GateResponse:
    return GateResponse(
        gate_id=gate.gate_id,
        task_id=gate.task_id,
        step_index=gate.step_index,
        description=gate.description,
        status=gate.status.value,
        created_at=gate.created_at,
        resolved_at=gate.resolved_at or None,
        approved_by=gate.approved_by or None,
        human_input=gate.human_input or None,
    )


@router.get("/gates", response_model=GateListResponse)
def list_gates(
    status: str | None = None,
    task_id: str | None = None,
    gate_store: HumanGateStore = Depends(get_gate_store),
) -> GateListResponse:
    """List human gates, optionally filtered by status or task_id."""
    status_filter = GateStatus(status) if status else None
    gates = gate_store.list(status=status_filter, task_id=task_id)
    return GateListResponse(
        gates=[_gate_to_response(g) for g in gates],
        count=len(gates),
    )


@router.get("/gates/{gate_id}", response_model=GateResponse)
def get_gate(gate_id: str, gate_store: HumanGateStore = Depends(get_gate_store)) -> GateResponse:
    """Get a specific human gate."""
    gate = gate_store.get(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id!r} not found")
    return _gate_to_response(gate)


@router.post("/gates/{gate_id}/approve", response_model=GateResponse)
def approve_gate(
    gate_id: str,
    req: GateApproveRequest,
    gate_store: HumanGateStore = Depends(get_gate_store),
) -> GateResponse:
    """Approve a pending human gate, allowing execution to continue."""
    try:
        gate = gate_store.approve(gate_id, req.approved_by, req.human_input)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _gate_to_response(gate)


@router.post("/gates/{gate_id}/reject", response_model=GateResponse)
def reject_gate(
    gate_id: str,
    req: GateApproveRequest,
    gate_store: HumanGateStore = Depends(get_gate_store),
) -> GateResponse:
    """Reject a pending human gate, halting execution."""
    try:
        gate = gate_store.reject(gate_id, req.approved_by, req.human_input)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _gate_to_response(gate)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_to_response(task: Task) -> TaskStatusResponse:
    return TaskStatusResponse(
        task_id=task.task_id,
        capability=task.capability,
        description=task.description,
        status=task.status.value,
        agent_id=task.agent_id,
        checkpoint_id=task.checkpoint_id,
        result=task.result,
        error=task.error,
    )


def _agent_to_response(agent: AgentModel) -> AgentResponse:
    return AgentResponse(
        agent_id=agent.agent_id,
        name=agent.name,
        capabilities=agent.capabilities,
        status=agent.status.value,
        access_level=agent.access_level.value,
        skills=agent.skills,
    )
