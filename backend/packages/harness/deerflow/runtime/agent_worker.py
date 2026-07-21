from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from deerflow.agents.model import AgentStatus
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.skills.catalog import SkillCatalog
from deerflow.tasks.classifier import classify_task
from deerflow.tasks.gate import GateStatus, HumanGate
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.model import Task, TaskStatus
from deerflow.tasks.orchestrator import PlanStepKind, SkillOrchestrator
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

    When a ``SkillOrchestrator`` is configured, the worker generates an
    execution plan and creates ``HumanGate`` records for gate steps.
    """

    def __init__(
        self,
        task_store: TaskStore,
        agent_registry: AgentRegistry,
        checkpointer: TaskCheckpointer,
        poll_interval: float = 5.0,
        gate_store: HumanGateStore | None = None,
        orchestrator: SkillOrchestrator | None = None,
        skill_catalog: SkillCatalog | None = None,
    ) -> None:
        self._task_store = task_store
        self._agent_registry = agent_registry
        self._checkpointer = checkpointer
        self._poll_interval = poll_interval
        self._gate_store = gate_store
        self._orchestrator = orchestrator
        self._skill_catalog = skill_catalog
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
        agents = self._agent_registry.find_by_capability(task.capability)
        if not agents:
            logger.info("No idle agent for task %s (cap=%s)", task.task_id, task.capability)
            return False

        agent = agents[0]
        self._task_store.claim(task.task_id, agent.agent_id)

        if self._orchestrator and self._skill_catalog:
            return self._dispatch_with_plan(task, agent.agent_id)

        return self._dispatch_simple(task, agent.agent_id)

    def _dispatch_simple(self, task: Task, agent_id: str) -> bool:
        """Legacy dispatch: classify and execute a single skill."""
        classification = classify_task(task.description)
        self._checkpointer.save(task, phase="plan", context={
            "classification": {
                "skill": classification.skill,
                "channel": classification.channel,
            },
            "agent_id": agent_id,
        })

        self._task_store.start_executing(task.task_id)
        executor = self._executor
        if executor is None:
            self._task_store.complete(task.task_id, {"note": "no executor configured; task skipped"})
            return True

        result = executor(task, classification.skill, classification.channel)
        self._checkpointer.save(task, phase="complete", context={"result": result})
        self._checkpointer.clear(task.task_id)

        if result.get("status") in ("completed", "ok"):
            self._task_store.complete(task.task_id, result)
        else:
            self._task_store.fail(task.task_id, result.get("error", result.get("output", "unknown error")))
        return True

    def _dispatch_with_plan(self, task: Task, agent_id: str) -> bool:
        """Dispatch using SkillOrchestrator plan, creating gates for gate steps."""
        assert self._orchestrator is not None
        assert self._skill_catalog is not None

        plan = self._orchestrator.plan(task.description, self._skill_catalog)
        self._checkpointer.save(task, phase="plan", context={
            "plan": {
                "steps": [{"skill": s.skill, "channel": s.channel, "kind": s.kind.value} for s in plan.steps],
                "total_steps": plan.total_steps,
            },
            "agent_id": agent_id,
        })

        self._task_store.start_executing(task.task_id)

        if not plan.steps:
            self._task_store.complete(task.task_id, {"note": "empty plan; no skills matched"})
            return True

        executor = self._executor
        if executor is None:
            self._task_store.complete(task.task_id, {"note": "no executor configured; task skipped"})
            return True

        # Execute steps sequentially. Gate steps create HumanGate records
        # and pause further execution until the gate is approved.
        results: list[dict[str, Any]] = []
        all_succeeded = True
        hit_gate = False

        for i, step in enumerate(plan.steps):
            if step.is_gate:
                if self._gate_store:
                    import uuid
                    gate = HumanGate(
                        gate_id=f"gate_{uuid.uuid4().hex[:12]}",
                        task_id=task.task_id,
                        step_index=i,
                        description=step.description,
                    )
                    self._gate_store.create(gate)
                    logger.info("Gate created for task %s: %s (%s)", task.task_id, gate.gate_id, step.description)

                hit_gate = True
                # Pause after creating the gate — wait for human approval
                break

            logger.info("Executing step %d/%d: %s (%s)", i + 1, plan.total_steps, step.skill, step.channel)
            result = executor(task, step.skill, step.channel)
            results.append(result)

            if result.get("status") not in ("completed", "ok"):
                all_succeeded = False
                error = result.get("error", result.get("output", f"step {i} failed"))
                self._checkpointer.save(task, phase=f"step_{i}_failed", context={"result": result, "step": i})
                self._task_store.fail(task.task_id, error)
                break

        if all_succeeded and not hit_gate:
            self._checkpointer.save(task, phase="complete", context={"results": results})
            self._checkpointer.clear(task.task_id)
            self._task_store.complete(task.task_id, {"results": results, "status": "completed"})

        return True

    def update_agent_status(self, agent_id: str, status: AgentStatus) -> None:
        """Update agent status in the registry."""
        self._agent_registry.update_status(agent_id, status)
