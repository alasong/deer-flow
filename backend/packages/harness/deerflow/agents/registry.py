from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from deerflow.agents.model import Agent, AgentStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure data-shaping functions
# ---------------------------------------------------------------------------

def agent_matches_capability(agent: Agent, capability: str) -> bool:
    """Check if *agent* can handle *capability* (prefix match)."""
    return any(cap.startswith(capability) for cap in agent.capabilities)


def serialize_registry(agents: list[Agent]) -> dict[str, Any]:
    """Pure: convert Agent list to serializable dict."""
    return {
        "agents": [a.to_dict() for a in agents],
        "count": len(agents),
    }


def deserialize_registry(data: dict[str, Any]) -> list[Agent]:
    """Pure: convert serialized dict back to Agent list."""
    return [Agent.from_dict(a) for a in data.get("agents", [])]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AgentRegistry:
    """Agent registry with capability-based routing.

    Thread-safe in-memory operations, optional file persistence.
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._agents: dict[str, Agent] = {}
        self._lock = Lock()
        self._file_path = Path(file_path) if file_path else None
        if self._file_path:
            self._load()

    # -- CRUD --

    def register(self, agent: Agent) -> Agent:
        """Register a new agent or update an existing one."""
        with self._lock:
            agent.updated_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            self._agents[agent.agent_id] = agent
            self._save()
        return agent

    def get(self, agent_id: str) -> Agent | None:
        with self._lock:
            return self._agents.get(agent_id)

    def list(self, status: AgentStatus | None = None) -> list[Agent]:
        with self._lock:
            agents = list(self._agents.values())
        if status is not None:
            agents = [a for a in agents if a.status == status]
        return agents

    def unregister(self, agent_id: str) -> bool:
        with self._lock:
            existed = agent_id in self._agents
            self._agents.pop(agent_id, None)
            if existed:
                self._save()
        return existed

    def update_status(self, agent_id: str, status: AgentStatus) -> Agent | None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return None
            agent.status = status
            agent.updated_at = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()
            self._save()
            return agent

    # -- Capability routing --

    def find_by_capability(self, capability: str, status: AgentStatus | None = AgentStatus.idle) -> list[Agent]:
        """Find agents matching a capability, optionally filtered by status."""
        agents = self.list(status=status)
        return [a for a in agents if agent_matches_capability(a, capability)]

    # -- Persistence --

    def _load(self) -> None:
        if not self._file_path or not self._file_path.exists():
            return
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            loaded = deserialize_registry(data)
            self._agents = {a.agent_id: a for a in loaded}
            logger.info("Loaded %d agents from %s", len(loaded), self._file_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load agent registry: %s", exc)

    def _save(self) -> None:
        if not self._file_path:
            return
        data = serialize_registry(list(self._agents.values()))
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
