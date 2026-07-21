"""Living Agent setup — wires AgentWorker into the Gateway lifespan.

Creates and manages the shared stores (TaskStore, AgentRegistry, HumanGateStore,
TaskCheckpointer) and the AgentWorker background loop.
"""

from __future__ import annotations

import logging

from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.agent_worker import AgentWorker
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.store import TaskStore
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)


class LivingAgentService:
    """Manages the living agent lifecycle within the Gateway process."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        task_store: TaskStore | None = None,
        checkpointer: MemorySaver | None = None,
        gate_store: HumanGateStore | None = None,
        poll_interval: float = 5.0,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.task_store = task_store or TaskStore()
        self.gate_store = gate_store or HumanGateStore()
        self.poll_interval = poll_interval
        self._worker: AgentWorker | None = None

        # Use MemorySaver as the default checkpointer backend
        from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer

        self.checkpointer = checkpointer or MemorySaver()
        if not isinstance(self.checkpointer, TaskCheckpointer):
            self.checkpointer = TaskCheckpointer(self.checkpointer)

    async def start(self) -> None:
        """Start the AgentWorker background loop."""
        self._worker = AgentWorker(
            task_store=self.task_store,
            agent_registry=self.registry,
            checkpointer=self.checkpointer,
            gate_store=self.gate_store,
            poll_interval=self.poll_interval,
        )

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
