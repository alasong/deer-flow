from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    idle = "idle"
    active = "active"
    paused = "paused"
    disabled = "disabled"


class AgentAccessLevel(StrEnum):
    info = "info"
    exec = "exec"
    mutate = "mutate"
    admin = "admin"


@dataclass
class Agent:
    """A living agent that can accept and execute tasks.

    Attributes:
        agent_id: Unique identifier (e.g. ``"agent.dev-owner"``).
        name: Human-readable name.
        capabilities: List of capability prefixes for task routing.
        status: Current agent status.
        access_level: Permission level for operations.
        skills: Assigned skill names (from the skill system).
        metadata: Arbitrary additional metadata.
        created_at: ISO-8601 timestamp.
        updated_at: ISO-8601 timestamp.
    """

    agent_id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.idle
    access_level: AgentAccessLevel = AgentAccessLevel.exec
    skills: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if isinstance(self.status, str):
            self.status = AgentStatus(self.status)
        if isinstance(self.access_level, str):
            self.access_level = AgentAccessLevel(self.access_level)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "agent_id": self.agent_id,
            "name": self.name,
            "capabilities": self.capabilities,
            "status": self.status.value,
            "access_level": self.access_level.value,
            "skills": self.skills,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Agent:
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            capabilities=data.get("capabilities", []),
            status=AgentStatus(data.get("status", "idle")),
            access_level=AgentAccessLevel(data.get("access_level", "exec")),
            skills=data.get("skills", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
