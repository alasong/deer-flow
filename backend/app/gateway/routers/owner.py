"""Owner Agent REST API router.

Provides visibility into and management of the lightweight Owner Agent system:
- Agent registry (listing registered agents)
- Task queue (pending and active tasks)
- Coordination board (shared state)
- Approval gate (approval workflow)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deerflow.agents.owner import get_board, get_queue, get_registry
from deerflow.tasks.approval import ApprovalGate
from deerflow.tasks.model import Task

router = APIRouter(prefix="/api/owner", tags=["owner"])

# Lazy-init approval gate singleton (reset by tests between runs)
_approval_gate: ApprovalGate | None = None


def _get_approval_gate() -> ApprovalGate:
    """Return the process-level ApprovalGate singleton, creating it if needed."""
    global _approval_gate
    if _approval_gate is None:
        _approval_gate = ApprovalGate()
    return _approval_gate


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ApprovalCreateRequest(BaseModel):
    task_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)


class ApprovalActionRequest(BaseModel):
    approved_by: str | None = Field(default=None, min_length=1)
    rejected_by: str | None = Field(default=None, min_length=1)
    reason: str = ""


class RegisterAgentRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)


class EnqueueTaskRequest(BaseModel):
    task_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: str = "normal"


class PostBoardRequest(BaseModel):
    key: str = Field(min_length=1)
    value: str = "true"
    updated_by: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Agent Registry
# ---------------------------------------------------------------------------


@router.get("/agents")
async def list_agents():
    """List all active registered agents."""
    registry = get_registry()
    agents = registry.list_active()
    return {
        "agents": [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "capabilities": a.capabilities,
                "status": a.status,
                "registered_at": a.registered_at,
            }
            for a in agents
        ]
    }


# ---------------------------------------------------------------------------
# Task Queue
# ---------------------------------------------------------------------------


@router.get("/queue")
async def get_queue_status():
    """List pending and active tasks."""
    queue = get_queue()
    pending = queue.list_pending()
    active = queue.list_active()
    return {
        "pending": [_task_to_dict(t) for t in pending],
        "active": [_task_to_dict(t) for t in active],
    }


# ---------------------------------------------------------------------------
# Coordination Board
# ---------------------------------------------------------------------------


@router.get("/board")
async def list_board(prefix: str = ""):
    """List coordination board entries, optionally filtered by key prefix."""
    board = get_board()
    entries = board.list(prefix=prefix)
    return {
        "entries": [
            {
                "key": e.key,
                "value": e.value,
                "updated_at": e.updated_at,
                "updated_by": e.updated_by,
            }
            for e in entries
        ]
    }


# ---------------------------------------------------------------------------
# Approval Gate
# ---------------------------------------------------------------------------


@router.get("/approvals")
async def list_approvals():
    """List all pending approval requests."""
    gate = _get_approval_gate()
    approvals = gate.list_pending()
    return {"approvals": [_approval_to_dict(a) for a in approvals]}


@router.post("/approvals/new")
async def request_approval(body: ApprovalCreateRequest):
    """Create a new approval request."""
    gate = _get_approval_gate()
    try:
        approval = gate.request_approval(
            task_id=body.task_id,
            reason=body.reason,
            requested_by=body.requested_by,
        )
        return {"approval": _approval_to_dict(approval)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/approvals/{task_id}/approve")
async def approve_request(task_id: str, body: ApprovalActionRequest):
    """Approve a pending approval request.

    ``approved_by`` can be passed in the request body. Defaults to ``"owner-ui"``
    when not provided.
    """
    gate = _get_approval_gate()
    try:
        approval = gate.approve(
            task_id=task_id,
            approved_by=body.approved_by or "owner-ui",
        )
        return {"approval": _approval_to_dict(approval)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/approvals/{task_id}/reject")
async def reject_request(task_id: str, body: ApprovalActionRequest):
    """Reject a pending approval request.

    ``rejected_by`` can be passed in the request body. Defaults to ``"owner-ui"``
    when not provided.
    """
    gate = _get_approval_gate()
    try:
        approval = gate.reject(
            task_id=task_id,
            rejected_by=body.rejected_by or "owner-ui",
            reason=body.reason or "",
        )
        return {"approval": _approval_to_dict(approval)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# Agent Registry — write
# ---------------------------------------------------------------------------


@router.post("/agents/register")
async def register_agent(body: RegisterAgentRequest):
    """Register a new agent."""
    registry = get_registry()
    try:
        info = registry.register(
            agent_id=body.agent_id,
            name=body.name,
            capabilities=body.capabilities,
        )
        return {
            "agent": {
                "agent_id": info.agent_id,
                "name": info.name,
                "capabilities": info.capabilities,
                "status": info.status,
                "registered_at": info.registered_at,
            }
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------
# Task Queue — write
# ---------------------------------------------------------------------------


@router.post("/queue/enqueue")
async def enqueue_task(body: EnqueueTaskRequest):
    """Enqueue a new task."""
    queue = get_queue()
    from deerflow.tasks.model import TaskPriority

    priority = TaskPriority(body.priority) if body.priority in ("low", "normal", "high", "critical") else TaskPriority.normal
    task = Task(
        task_id=body.task_id,
        capability=body.capability,
        description=body.description,
        priority=priority,
    )
    queue.enqueue(task)
    return {"task": _task_to_dict(task)}


@router.post("/queue/claim")
async def claim_task(agent_id: str):
    """Claim the next pending task for an agent."""
    queue = get_queue()
    task = queue.claim(agent_id)
    if task is None:
        raise HTTPException(status_code=404, detail="No pending tasks available")
    return {"task": _task_to_dict(task)}


# ---------------------------------------------------------------------------
# Coordination Board — write
# ---------------------------------------------------------------------------


@router.post("/board/post")
async def post_board_entry(body: PostBoardRequest):
    """Post a key/value entry to the coordination board."""
    board = get_board()
    entry = board.post(key=body.key, value=body.value, updated_by=body.updated_by)
    return {
        "entry": {
            "key": entry.key,
            "value": entry.value,
            "updated_at": entry.updated_at,
            "updated_by": entry.updated_by,
        }
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _task_to_dict(task: "Task") -> dict:
    from dataclasses import asdict

    d = asdict(task)
    d["status"] = str(task.status.value) if hasattr(task.status, "value") else task.status
    d["priority"] = str(task.priority.value) if hasattr(task.priority, "value") else task.priority
    return d


def _approval_to_dict(approval: "ApprovalRequest") -> dict:
    return {
        "task_id": approval.task_id,
        "reason": approval.reason,
        "requested_by": approval.requested_by,
        "status": approval.status,
        "approved_by": approval.approved_by,
        "rejected_by": approval.rejected_by,
        "rejection_reason": approval.rejection_reason,
        "created_at": approval.created_at,
        "updated_at": approval.updated_at,
    }
