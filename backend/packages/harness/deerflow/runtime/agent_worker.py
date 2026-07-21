from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from deerflow.agents.model import AgentStatus
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.tasks.classifier import classify_task
from deerflow.tasks.model import Task, TaskStatus
from deerflow.tasks.store import TaskStore

logger = logging.getLogger(__name__)


class AgentWorker:
    """Background worker that polls task queue and dispatches to agents.

    Runs as an asyncio task inside the Gateway process. Each poll cycle:
    1. Finds pending tasks
    2. Matches tasks to idle agents via capability routing
    3. Classifies task → selects skill + channel
    4. Saves checkpoint before execution
    5. Executes via the provided execution callback
    6. Saves result or failure

    Usage::

        worker = AgentWorker(task_store, agent_registry, checkpointer)
        worker.set_executor(my_executor)
        asyncio.create_task(worker.run())
    """

    def __init__(
        self,
        task_store: TaskStore,
        agent_registry: AgentRegistry,
        checkpointer: TaskCheckpointer,
        poll_interval: float = 5.0,
    ) -> None:
        self._task_store = task_store
        self._agent_registry = agent_registry
        self._checkpointer = checkpointer
        self._poll_interval = poll_interval
        self._executor: Callable[[Task, str, str], dict[str, Any]] | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def set_executor(self, executor: Callable[[Task, str, str], dict[str, Any]]) -> None:
        """Set the execution callback.

        The callback receives ``(task, skill, channel)`` and returns a result dict.
        """
        self._executor = executor

    async def run(self) -> None:
        """Start the worker loop. Blocks until stop() is called."""
        logger.info("Agent worker started (poll_interval=%ss)", self._poll_interval)
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception as exc:
                logger.error("Agent worker poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)
        logger.info("Agent worker stopped")

    async def start(self) -> None:
        """Run the worker in the background as an asyncio task."""
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll(self) -> int:
        """Single poll cycle. Returns number of tasks dispatched."""
        # 1. Find pending tasks (limit to 5 per cycle)
        pending = self._task_store.find_pending()[:5]
        if not pending:
            return 0

        dispatched = 0
        for task in pending:
            try:
                if self._dispatch(task):
                    dispatched += 1
            except Exception as exc:
                logger.error("Failed to dispatch task %s: %s", task.task_id, exc)
                try:
                    self._task_store.fail(task.task_id, str(exc))
                except Exception:
                    pass
        return dispatched

    def _dispatch(self, task: Task) -> bool:
        """Dispatch a single task to a matching agent.

        Returns True if the task was dispatched, False if no agent was available.
        """
        # 1. Find matching idle agent
        agents = self._agent_registry.find_by_capability(task.capability)
        if not agents:
            logger.info("No idle agent for task %s (cap=%s)", task.task_id, task.capability)
            return False

        agent = agents[0]

        # 2. Claim the task
        self._task_store.claim(task.task_id, agent.agent_id)

        # 3. Classify task → select skill + channel
        classification = classify_task(task.description)

        # 4. Save pre-execution checkpoint
        self._checkpointer.save(task, phase="plan", context={
            "classification": {
                "skill": classification.skill,
                "channel": classification.channel,
            },
            "agent_id": agent.agent_id,
        })

        # 5. Execute
        self._task_store.start_executing(task.task_id)
        executor = self._executor
        if executor is None:
            self._task_store.complete(task.task_id, {"note": "no executor configured; task skipped"})
            return True

        result = executor(task, classification.skill, classification.channel)

        # 7. Save post-execution checkpoint and complete
        self._checkpointer.save(task, phase="complete", context={"result": result})
        self._checkpointer.clear(task.task_id)

        if result.get("status") in ("completed", "ok"):
            self._task_store.complete(task.task_id, result)
        else:
            error = result.get("error", result.get("output", "unknown error"))
            self._task_store.fail(task.task_id, error)

        return True

    def update_agent_status(self, agent_id: str, status: AgentStatus) -> None:
        """Update agent status in the registry."""
        self._agent_registry.update_status(agent_id, status)
