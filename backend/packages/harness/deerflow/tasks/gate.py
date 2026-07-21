from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class GateStatus(StrEnum):
    """Status of a human-in-the-loop gate."""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"


@dataclass
class HumanGate:
    """A gate that pauses execution until human approval.

    Gates are inserted into execution plans by the orchestrator when
    ``human_review=True``. The worker creates a ``HumanGate`` record
    when it encounters a gate step, and external consumers (API clients,
    webhook handlers) approve or reject it.
    """

    gate_id: str
    task_id: str
    step_index: int
    description: str = ""
    status: GateStatus = GateStatus.pending
    created_at: str = ""
    resolved_at: str = ""
    approved_by: str = ""
    human_input: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if isinstance(self.status, str):
            self.status = GateStatus(self.status)

    @property
    def is_resolved(self) -> bool:
        return self.status in (GateStatus.approved, GateStatus.rejected)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "gate_id": self.gate_id,
            "task_id": self.task_id,
            "step_index": self.step_index,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
        }
        if self.resolved_at:
            result["resolved_at"] = self.resolved_at
        if self.approved_by:
            result["approved_by"] = self.approved_by
        if self.human_input:
            result["human_input"] = self.human_input
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HumanGate:
        return cls(
            gate_id=data["gate_id"],
            task_id=data["task_id"],
            step_index=data.get("step_index", 0),
            description=data.get("description", ""),
            status=GateStatus(data.get("status", "pending")),
            created_at=data.get("created_at", ""),
            resolved_at=data.get("resolved_at", ""),
            approved_by=data.get("approved_by", ""),
            human_input=data.get("human_input", ""),
        )


# ---------------------------------------------------------------------------
# Pure lifecycle transitions
# ---------------------------------------------------------------------------


def transition_approve(gate: HumanGate, approved_by: str = "", human_input: str = "") -> HumanGate:
    """Approve a pending gate."""
    if gate.status != GateStatus.pending:
        raise ValueError(f"Cannot approve gate in status {gate.status!r}")
    gate.status = GateStatus.approved
    gate.resolved_at = datetime.now(timezone.utc).isoformat()
    gate.approved_by = approved_by
    gate.human_input = human_input
    return gate


def transition_reject(gate: HumanGate, approved_by: str = "", human_input: str = "") -> HumanGate:
    """Reject a pending gate."""
    if gate.status != GateStatus.pending:
        raise ValueError(f"Cannot reject gate in status {gate.status!r}")
    gate.status = GateStatus.rejected
    gate.resolved_at = datetime.now(timezone.utc).isoformat()
    gate.approved_by = approved_by
    gate.human_input = human_input
    return gate
