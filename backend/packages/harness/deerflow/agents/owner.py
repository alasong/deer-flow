"""Lightweight Owner Agent singletons.

Provides process-wide shared instances of AgentRegistry, TaskQueue, and
CoordinationBoard, plus a convenience function for registering the current
lead agent on startup.

Usage::

    from deerflow.agents.owner import get_registry, get_queue, get_board

    registry = get_registry()
    registry.register("agent-1", "Agent One", capabilities=["search"])

    queue = get_queue()
    queue.enqueue(task)

    board = get_board()
    board.post("status.agent-1", {"state": "ready"}, updated_by="agent-1")
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from deerflow.agents.registry import AgentInfo, AgentRegistry
from deerflow.runtime.board import CoordinationBoard
from deerflow.tasks.queue import TaskQueue

logger = logging.getLogger(__name__)

_registry: AgentRegistry | None = None
_queue: TaskQueue | None = None
_board: CoordinationBoard | None = None
_lock = threading.Lock()


def get_registry() -> AgentRegistry:
    """Return the process-wide AgentRegistry singleton."""
    global _registry
    if _registry is None:
        with _lock:
            if _registry is None:
                _registry = AgentRegistry()
    return _registry


def get_queue() -> TaskQueue:
    """Return the process-wide TaskQueue singleton."""
    global _queue
    if _queue is None:
        with _lock:
            if _queue is None:
                _queue = TaskQueue()
    return _queue


def get_board() -> CoordinationBoard:
    """Return the process-wide CoordinationBoard singleton."""
    global _board
    if _board is None:
        with _lock:
            if _board is None:
                _board = CoordinationBoard()
    return _board


def register_agent(
    agent_name: str,
    *,
    capabilities: list[str] | None = None,
) -> AgentInfo:
    """Register the current agent in the shared registry.

    Called by the lead agent factory on startup. Idempotent: re-registering
    the same agent_name updates capabilities and sets status to 'idle'.
    """
    registry = get_registry()
    agent_id = f"agent.{agent_name}" if agent_name else "agent.default"
    try:
        return registry.register(
            agent_id=agent_id,
            name=agent_name or "default",
            capabilities=capabilities or [],
        )
    except ValueError:
        registry.update_status(agent_id, "idle")
        existing = registry.get(agent_id)
        assert existing is not None
        return existing


def _format_registry_summary() -> str:
    """Build a compact summary of registered agents for the system prompt."""
    registry = get_registry()
    agents = registry.list_active()
    if not agents:
        return ""
    lines = [f"  - `{a.agent_id}` — {a.name}  capabilities: {', '.join(a.capabilities) or 'none'}" for a in agents]
    return "Available agents:\n" + "\n".join(lines)
