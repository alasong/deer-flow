from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PipelineStatus(str, Enum):
    active = "active"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class PipelineStepRunStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


class PipelineStepRun(BaseModel):
    id: str
    step_id: str
    agent_name: str
    status: PipelineStepRunStatus = PipelineStepRunStatus.pending
    manifest_path: str | None = None


class PipelineRun(BaseModel):
    id: str
    definition_id: str
    thread_id: str
    status: PipelineStatus = PipelineStatus.active
    current_step_index: int = 0
    steps: list[PipelineStepRun] = Field(default_factory=list)
    created_at: str = ""


def validate_status_transition(
    current: PipelineStatus, target: PipelineStatus
) -> bool:
    """Return True if *target* is a valid successor of *current*.

    Valid transitions:

    - active     → completed, failed, cancelled
    - completed  → (terminal — no outgoing transitions)
    - failed     → (terminal — no outgoing transitions)
    - cancelled  → (terminal — no outgoing transitions)
    """
    allowed: dict[PipelineStatus, set[PipelineStatus]] = {
        PipelineStatus.active: {
            PipelineStatus.completed,
            PipelineStatus.failed,
            PipelineStatus.cancelled,
        },
        PipelineStatus.completed: set(),
        PipelineStatus.failed: set(),
        PipelineStatus.cancelled: set(),
    }
    return target in allowed.get(current, set())


def validate_step_run_transition(
    current: PipelineStepRunStatus, target: PipelineStepRunStatus
) -> bool:
    """Return True if *target* is a valid successor of *current*.

    Valid transitions:

    - pending  → running, skipped
    - running  → completed, failed
    - completed → (terminal)
    - failed   → (terminal)
    - skipped  → (terminal)
    """
    allowed: dict[PipelineStepRunStatus, set[PipelineStepRunStatus]] = {
        PipelineStepRunStatus.pending: {
            PipelineStepRunStatus.running,
            PipelineStepRunStatus.skipped,
        },
        PipelineStepRunStatus.running: {
            PipelineStepRunStatus.completed,
            PipelineStepRunStatus.failed,
        },
        PipelineStepRunStatus.completed: set(),
        PipelineStepRunStatus.failed: set(),
        PipelineStepRunStatus.skipped: set(),
    }
    return target in allowed.get(current, set())
