"""Thread-safe in-memory Agent registry.

Provides ``AgentRegistry`` — a simple thread-safe registry for agent metadata,
independent of the ``deerflow.agents.model`` layer. Uses a ``threading.Lock``
to serialise all mutations and a plain ``dict`` for storage.

Usage::

    registry = AgentRegistry()
    info = registry.register("agent-1", "Agent One", capabilities=["search"])
    agent = registry.get("agent-1")
    search_agents = registry.lookup(capability="search")
    active = registry.list_active()
    registry.update_status("agent-1", "paused")
    registry.unregister("agent-1")
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentInfo:
    """Immutable metadata snapshot for a registered agent."""

    agent_id: str
    name: str
    capabilities: list[str] = field(default_factory=list)
    status: str = "active"
    registered_at: str = ""


class AgentRegistry:
    """Thread-safe in-memory agent registry backed by a plain dict."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentInfo] = {}
        self._lock = threading.Lock()

    def register(
        self,
        agent_id: str,
        name: str,
        capabilities: list[str] | None = None,
    ) -> AgentInfo:
        """Register a new agent.

        Raises ``ValueError`` if ``agent_id`` is already registered.
        """
        now = datetime.now(timezone.utc).isoformat()
        info = AgentInfo(
            agent_id=agent_id,
            name=name,
            capabilities=capabilities or [],
            status="active",
            registered_at=now,
        )
        with self._lock:
            if agent_id in self._agents:
                raise ValueError(f"Agent {agent_id!r} is already registered")
            self._agents[agent_id] = info
        return info

    def unregister(self, agent_id: str) -> None:
        """Remove an agent from the registry.

        No-op if the agent does not exist.
        """
        with self._lock:
            self._agents.pop(agent_id, None)

    def get(self, agent_id: str) -> AgentInfo | None:
        """Look up a single agent by id, or ``None`` if not found."""
        with self._lock:
            return self._agents.get(agent_id)

    def lookup(self, capability: str | None = None) -> list[AgentInfo]:
        """Return all agents, optionally filtered by capability.

        When ``capability`` is ``None``, returns every registered agent.
        """
        with self._lock:
            if capability is None:
                return list(self._agents.values())
            return [
                a for a in self._agents.values() if capability in a.capabilities
            ]

    def update_status(self, agent_id: str, status: str) -> None:
        """Update the status of a registered agent.

        Raises ``KeyError`` if the agent is not found.
        """
        with self._lock:
            if agent_id not in self._agents:
                raise KeyError(f"Agent {agent_id!r} not found")
            old = self._agents[agent_id]
            self._agents[agent_id] = AgentInfo(
                agent_id=old.agent_id,
                name=old.name,
                capabilities=list(old.capabilities),
                status=status,
                registered_at=old.registered_at,
            )

    def list_active(self) -> list[AgentInfo]:
        """Return all agents whose status is ``"active"``."""
        with self._lock:
            return [a for a in self._agents.values() if a.status == "active"]
