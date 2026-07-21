from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    pending = "pending"
    claimed = "claimed"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskPriority(StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    critical = "critical"


@dataclass
class Task:
    """A task unit dispatched to an agent for execution.

    Lifecycle: pending → claimed → executing → completed | failed
    Cancellation is allowed from pending or claimed states.
    """

    task_id: str
    capability: str
    description: str
    status: TaskStatus = TaskStatus.pending
    priority: TaskPriority = TaskPriority.normal
    agent_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    checkpoint_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if isinstance(self.status, str):
            self.status = TaskStatus(self.status)
        if isinstance(self.priority, str):
            self.priority = TaskPriority(self.priority)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled)

    @property
    def is_claimable(self) -> bool:
        return self.status == TaskStatus.pending

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": self.task_id,
            "capability": self.capability,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.agent_id:
            result["agent_id"] = self.agent_id
        if self.payload:
            result["payload"] = self.payload
        if self.result is not None:
            result["result"] = self.result
        if self.error:
            result["error"] = self.error
        if self.checkpoint_id:
            result["checkpoint_id"] = self.checkpoint_id
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            task_id=data["task_id"],
            capability=data["capability"],
            description=data["description"],
            status=TaskStatus(data.get("status", "pending")),
            priority=TaskPriority(data.get("priority", "normal")),
            agent_id=data.get("agent_id"),
            payload=data.get("payload", {}),
            result=data.get("result"),
            error=data.get("error"),
            checkpoint_id=data.get("checkpoint_id"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


# ---------------------------------------------------------------------------
# Pure lifecycle transitions
# ---------------------------------------------------------------------------


def transition_claim(task: Task, agent_id: str) -> Task:
    """Transition a task from pending to claimed."""
    if task.status != TaskStatus.pending:
        raise ValueError(f"Cannot claim task in status {task.status!r}")
    task.status = TaskStatus.claimed
    task.agent_id = agent_id
    task.updated_at = datetime.now(timezone.utc).isoformat()
    return task


def transition_execute(task: Task) -> Task:
    """Transition a claimed task to executing."""
    if task.status != TaskStatus.claimed:
        raise ValueError(f"Cannot execute task in status {task.status!r}")
    task.status = TaskStatus.executing
    task.updated_at = datetime.now(timezone.utc).isoformat()
    return task


def transition_complete(task: Task, result: dict[str, Any]) -> Task:
    """Transition an executing task to completed."""
    if task.status != TaskStatus.executing:
        raise ValueError(f"Cannot complete task in status {task.status!r}")
    task.status = TaskStatus.completed
    task.result = result
    task.updated_at = datetime.now(timezone.utc).isoformat()
    return task


def transition_fail(task: Task, error: str) -> Task:
    """Transition an executing or claimed task to failed."""
    if task.status not in (TaskStatus.claimed, TaskStatus.executing):
        raise ValueError(f"Cannot fail task in status {task.status!r}")
    task.status = TaskStatus.failed
    task.error = error
    task.updated_at = datetime.now(timezone.utc).isoformat()
    return task


def transition_cancel(task: Task) -> Task:
    """Cancel a pending or claimed task."""
    if task.status not in (TaskStatus.pending, TaskStatus.claimed):
        raise ValueError(f"Cannot cancel task in status {task.status!r}")
    task.status = TaskStatus.cancelled
    task.updated_at = datetime.now(timezone.utc).isoformat()
    return task
