"""Living Agent setup — wires AgentWorker into the Gateway lifespan.

Creates and manages the shared stores (TaskStore, AgentRegistry, HumanGateStore,
TaskCheckpointer) and the AgentWorker background loop.
"""

from __future__ import annotations

import logging
from typing import Any

from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.agent_worker import AgentWorker
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.model import Task
from deerflow.tasks.store import TaskStore

logger = logging.getLogger(__name__)


class _DictCheckpointerStore:
    """Minimal dict-based store satisfying TaskCheckpointer's internal put/get protocol.

    TaskCheckpointer.save() calls put(config, data) where config contains
    the thread_id and data is the checkpoint payload. This store simply keeps
    an in-memory dict keyed by thread_id.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def put(self, config: dict[str, Any], data: dict[str, Any]) -> None:
        self._data[config["configurable"]["thread_id"]] = data

    def get(self, config: dict[str, Any]) -> dict[str, Any] | None:
        return self._data.get(config["configurable"]["thread_id"])


class LivingAgentService:
    """Manages the living agent lifecycle within the Gateway process."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        task_store: TaskStore | None = None,
        checkpointer_parent: TaskCheckpointer | None = None,
        gate_store: HumanGateStore | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.task_store = task_store or TaskStore()
        self.gate_store = gate_store or HumanGateStore()
        self.poll_interval = poll_interval
        self._worker: AgentWorker | None = None

        # Default checkpointer uses a simple in-memory dict store
        # (not langgraph's MemorySaver, which has a different put() signature).
        self.checkpointer = checkpointer_parent or TaskCheckpointer(_DictCheckpointerStore())

    @staticmethod
    def _default_executor(task: Task, skill: str, channel: str) -> dict[str, Any]:
        """Default executor: logs execution and returns a completed result."""
        logger.info("Default executor: task=%s skill=%s channel=%s", task.task_id, skill, channel)
        return {
            "status": "completed",
            "output": f"default_executor:{skill}/{channel}",
            "skill": skill,
            "channel": channel,
        }

    async def start(self) -> None:
        """Start the AgentWorker background loop."""
        self._worker = AgentWorker(
            task_store=self.task_store,
            agent_registry=self.registry,
            checkpointer=self.checkpointer,
            gate_store=self.gate_store,
            poll_interval=self.poll_interval,
        )
        self._worker.set_executor(LivingAgentService._default_executor)

        await self._worker.start()
        logger.info(
            "Living agent worker started (poll_interval=%ss, agents=%d, tasks=%d, gates=%d)",
            self.poll_interval,
            len(self.registry.list()),
            len(self.task_store.list()),
            len(self.gate_store.list()),
        )

    async def stop(self) -> None:
        """Stop the AgentWorker gracefully."""
        if self._worker is not None:
            await self._worker.stop()
            self._worker = None
            logger.info("Living agent worker stopped")
