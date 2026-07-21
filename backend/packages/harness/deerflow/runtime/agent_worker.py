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
from deerflow.tasks.orchestrator import ExecutionStep, PlanStepKind, SkillOrchestrator
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
    # Step execution
    # ------------------------------------------------------------------

    def _execute_steps(
        self,
        steps: list[ExecutionStep],
        task: Task,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        """Execute a list of steps sequentially.

        Returns:
            results: Execution result dicts for completed steps.
            all_succeeded: True if every executed step completed successfully.
            gate_index: Index of the gate step that paused execution, or None.
        """
        executor = self._executor
        if executor is None:
            return [], True, None

        results: list[dict[str, Any]] = []

        for i, step in enumerate(steps):
            if step.is_gate:
                if self._gate_store is not None:
                    import uuid

                    gate = HumanGate(
                        gate_id=f"gate_{uuid.uuid4().hex[:12]}",
                        task_id=task.task_id,
                        step_index=i,
                        description=step.description,
                    )
                    self._gate_store.create(gate)
                    logger.info(
                        "Gate created for task %s: %s (%s)", task.task_id, gate.gate_id, step.description
                    )
                return results, True, i

            logger.info("Executing step %d: %s (%s)", i + 1, len(steps), step.skill, step.channel)
            result = executor(task, step.skill, step.channel)
            results.append(result)

            if result.get("status") not in ("completed", "ok"):
                logger.warning("Step %d failed for task %s: %s", i, task.task_id, result.get("error", "unknown"))
                return results, False, None

        return results, True, None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _poll(self) -> int:
        """Single poll cycle. Returns number of tasks dispatched."""
        dispatched = 0

        # 1. Dispatch pending tasks
        pending = self._task_store.find_pending()[:5]
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

        # 2. Check for gate-resumable tasks
        resumed = self._check_gate_resumes()

        return dispatched + resumed

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

        if self._executor is None:
            self._task_store.complete(task.task_id, {"note": "no executor configured; task skipped"})
            return True

        # Execute steps. Gate steps create HumanGate records and pause further
        # execution until the gate is approved; the resume path is handled by
        # _check_gate_resumes in a later poll cycle.
        results, all_succeeded, gate_index = self._execute_steps(plan.steps, task)

        if gate_index is not None:
            remaining_steps = plan.steps[gate_index + 1:]
            remaining_data = [
                {
                    "skill": s.skill,
                    "channel": s.channel,
                    "kind": s.kind.value,
                    "description": s.description,
                    "params": s.params,
                }
                for s in remaining_steps
            ]
            self._checkpointer.save(task, phase="gate_paused", context={
                "gate_step_index": gate_index,
                "remaining_steps": remaining_data,
                "agent_id": agent_id,
            })
            logger.info(
                "Task %s paused at gate %d; %d remaining step(s) saved for resume",
                task.task_id, gate_index, len(remaining_data),
            )

        if not all_succeeded and gate_index is None:
            error = "step execution failed"
            if results:
                last = results[-1]
                error = last.get("error", last.get("output", "step execution failed"))
            self._checkpointer.save(task, phase="failed", context={"results": results, "error": error})
            self._task_store.fail(task.task_id, error)

        if all_succeeded and gate_index is None:
            self._checkpointer.save(task, phase="complete", context={"results": results})
            self._checkpointer.clear(task.task_id)
            self._task_store.complete(task.task_id, {"results": results, "status": "completed"})

        return True

    def _check_gate_resumes(self) -> int:
        """Check executing tasks for resolved gates and resume/reject them.

        Returns the number of tasks that were resumed or failed.
        """
        if self._gate_store is None:
            return 0

        resumed = 0
        executing = self._task_store.list(status=TaskStatus.executing)

        for task in executing:
            pending_gates = self._gate_store.find_pending_by_task(task.task_id)

            # Check all gates for this task — are any resolved?
            all_gates = self._gate_store.list(task_id=task.task_id)
            resolved = [g for g in all_gates if g.is_resolved]
            if not resolved:
                continue

            # If any gate was rejected, fail the task
            rejected = [g for g in resolved if g.status == GateStatus.rejected]
            if rejected:
                logger.info(
                    "Gate %s rejected; failing task %s", rejected[0].gate_id, task.task_id,
                )
                self._task_store.fail(task.task_id, f"Gate rejected: {rejected[0].description}")
                resumed += 1
                continue

            # Gate was approved — resume
            approved = [g for g in resolved if g.status == GateStatus.approved]
            if approved:
                ok = self._resume_after_gate(task)
                if ok:
                    resumed += 1

        return resumed

    def _resume_after_gate(self, task: Task) -> bool:
        """Resume task execution after a gate was approved.

        Loads the gate-paused checkpoint and continues executing remaining steps.
        """
        meta = self._checkpointer.restore(task)
        if meta is None or meta.get("phase") != "gate_paused":
            logger.warning("No gate_paused state found for task %s", task.task_id)
            return False

        remaining_data = meta.get("context", {}).get("remaining_steps", [])
        agent_id = meta.get("context", {}).get("agent_id", "")

        if not remaining_data:
            # No more steps — complete the task
            self._checkpointer.save(task, phase="complete", context={"note": "gate approved, no remaining steps"})
            self._checkpointer.clear(task.task_id)
            self._task_store.complete(task.task_id, {"status": "completed", "note": "gate approved, no remaining steps"})
            return True

        # Rebuild ExecutionStep list from saved dict data
        steps = [
            ExecutionStep(
                skill=s.get("skill", ""),
                channel=s.get("channel", ""),
                description=s.get("description", ""),
                kind=PlanStepKind(s.get("kind", "sequence")),
                params=s.get("params", {}),
            )
            for s in remaining_data
        ]

        if self._executor is None:
            self._task_store.complete(task.task_id, {"note": "no executor configured during resume; task skipped"})
            return True

        results, all_succeeded, gate_index = self._execute_steps(steps, task)

        if gate_index is not None:
            # Hit another gate — save updated gate-pause state
            remaining_steps = steps[gate_index + 1:]
            remaining_data = [
                {
                    "skill": s.skill,
                    "channel": s.channel,
                    "kind": s.kind.value,
                    "description": s.description,
                    "params": s.params,
                }
                for s in remaining_steps
            ]
            self._checkpointer.save(task, phase="gate_paused", context={
                "gate_step_index": gate_index,
                "remaining_steps": remaining_data,
                "agent_id": agent_id,
            })
            logger.info(
                "Task %s paused at another gate %d during resume; %d remaining step(s) saved",
                task.task_id, gate_index, len(remaining_data),
            )

        if not all_succeeded and gate_index is None:
            error = "resumed step execution failed"
            if results:
                last = results[-1]
                error = last.get("error", last.get("output", "resumed step execution failed"))
            self._task_store.fail(task.task_id, error)
            return False

        if all_succeeded and gate_index is None:
            self._checkpointer.save(task, phase="complete", context={"results": results})
            self._checkpointer.clear(task.task_id)
            self._task_store.complete(task.task_id, {"results": results, "status": "completed"})

        return True
        """Update agent status in the registry."""
        self._agent_registry.update_status(agent_id, status)
