from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
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
# Dependencies (injected via app state or global singletons)
# ---------------------------------------------------------------------------

_registry: AgentRegistry | None = None
_task_store: TaskStore | None = None
_checkpointer: TaskCheckpointer | None = None
_gate_store: HumanGateStore | None = None


def setup(
    registry: AgentRegistry,
    task_store: TaskStore,
    checkpointer: TaskCheckpointer | None = None,
    gate_store: HumanGateStore | None = None,
) -> None:
    global _registry, _task_store, _checkpointer, _gate_store
    _registry = registry
    _task_store = task_store
    _checkpointer = checkpointer
    _gate_store = gate_store


def _get_registry() -> AgentRegistry:
    if _registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not configured")
    return _registry


def _get_task_store() -> TaskStore:
    if _task_store is None:
        raise HTTPException(status_code=503, detail="Task store not configured")
    return _task_store


def _get_gate_store() -> HumanGateStore:
    if _gate_store is None:
        raise HTTPException(status_code=503, detail="Gate store not configured")
    return _gate_store


# ---------------------------------------------------------------------------
# Agent endpoints
# ---------------------------------------------------------------------------


@router.post("/agents", response_model=AgentResponse)
def create_agent(req: AgentCreateRequest) -> AgentResponse:
    """Register a new agent."""
    registry = _get_registry()
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
def list_agents() -> AgentListResponse:
    """List all registered agents."""
    registry = _get_registry()
    agents = registry.list()
    return AgentListResponse(
        agents=[_agent_to_response(a) for a in agents],
        count=len(agents),
    )


@router.get("/agents/{agent_id}", response_model=AgentResponse)
def get_agent(agent_id: str) -> AgentResponse:
    """Get a specific agent."""
    registry = _get_registry()
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return _agent_to_response(agent)


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str) -> dict[str, str]:
    """Unregister an agent."""
    registry = _get_registry()
    if not registry.unregister(agent_id):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=TaskSubmitResponse)
def submit_task(req: TaskSubmitRequest) -> TaskSubmitResponse:
    """Submit a new task to the task queue."""
    store = _get_task_store()
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
def list_tasks(status: str | None = None) -> list[TaskStatusResponse]:
    """List tasks, optionally filtered by status."""
    store = _get_task_store()
    status_filter = TaskStatus(status) if status else None
    tasks = store.list(status=status_filter)
    return [_task_to_response(t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
def get_task(task_id: str) -> TaskStatusResponse:
    """Get task status and result."""
    store = _get_task_store()
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return _task_to_response(task)


@router.post("/tasks/{task_id}/claim", response_model=TaskStatusResponse)
def claim_task(task_id: str, req: TaskClaimRequest) -> TaskStatusResponse:
    """Claim a pending task for execution."""
    store = _get_task_store()
    try:
        task = store.claim(task_id, req.agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _task_to_response(task)


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatusResponse)
def cancel_task(task_id: str) -> TaskStatusResponse:
    """Cancel a pending or claimed task."""
    store = _get_task_store()
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
def list_gates(status: str | None = None, task_id: str | None = None) -> GateListResponse:
    """List human gates, optionally filtered by status or task_id."""
    gate_store = _get_gate_store()
    status_filter = GateStatus(status) if status else None
    gates = gate_store.list(status=status_filter, task_id=task_id)
    return GateListResponse(
        gates=[_gate_to_response(g) for g in gates],
        count=len(gates),
    )


@router.get("/gates/{gate_id}", response_model=GateResponse)
def get_gate(gate_id: str) -> GateResponse:
    """Get a specific human gate."""
    gate_store = _get_gate_store()
    gate = gate_store.get(gate_id)
    if gate is None:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id!r} not found")
    return _gate_to_response(gate)


@router.post("/gates/{gate_id}/approve", response_model=GateResponse)
def approve_gate(gate_id: str, req: GateApproveRequest) -> GateResponse:
    """Approve a pending human gate, allowing execution to continue."""
    gate_store = _get_gate_store()
    try:
        gate = gate_store.approve(gate_id, req.approved_by, req.human_input)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Gate {gate_id!r} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _gate_to_response(gate)


@router.post("/gates/{gate_id}/reject", response_model=GateResponse)
def reject_gate(gate_id: str, req: GateApproveRequest) -> GateResponse:
    """Reject a pending human gate, halting execution."""
    gate_store = _get_gate_store()
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
