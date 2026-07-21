"""Tests for AgentWorker background execution."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deerflow.agents.model import Agent
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.agent_worker import AgentWorker
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.skills.catalog import SkillCatalog
from deerflow.skills.types import Skill, SkillCategory
from deerflow.tasks.gate import GateStatus, HumanGate
from deerflow.tasks.gate_store import HumanGateStore
from deerflow.tasks.model import Task
from deerflow.tasks.orchestrator import ExecutionPlan, ExecutionStep, PlanStepKind, SkillOrchestrator
from deerflow.tasks.store import TaskStore
from pathlib import Path


class _MockCheckpointer:
    def __init__(self):
        self.saves: list[dict] = []
        self._saves_by_task: dict[str, dict] = {}
        self.cleared: list[str] = []

    def save(self, task: Task, phase: str, context: dict | None = None) -> str:
        save = {"task_id": task.task_id, "phase": phase, "context": context or {}}
        self.saves.append(save)
        self._saves_by_task[task.task_id] = save
        return f"cp_{task.task_id}_{phase}"

    def restore(self, task: Task) -> dict | None:
        save = self._saves_by_task.get(task.task_id)
        if save is None:
            return None
        # Return in the format TaskCheckpointer.restore returns: {"task_meta": {...}}
        return {
            "task_id": save["task_id"],
            "phase": save["phase"],
            "context": save["context"],
        }

    def has_checkpoint(self, task_id: str) -> bool:
        return task_id in self._saves_by_task

    def clear(self, task_id: str) -> None:
        self.cleared.append(task_id)
        self._saves_by_task.pop(task_id, None)


def _make_skill(name: str, description: str) -> Skill:
    return Skill(
        name=name,
        description=description,
        license=None,
        skill_dir=Path(f"/skills/{name}"),
        skill_file=Path(f"/skills/{name}/SKILL.md"),
        relative_path=Path(name),
        category=SkillCategory.PUBLIC,
    )


class TestAgentWorker:
    def test_empty_poll_no_dispatches(self):
        worker = AgentWorker(TaskStore(), AgentRegistry(), _MockCheckpointer())
        assert worker._poll() == 0

    def test_dispatches_pending_task(self):
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()

        # Register agent
        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))

        # Submit task
        task = Task(task_id="t1", capability="dev", description="Fix bug")
        store.put(task)

        # Track executions
        executions: list[tuple[str, str, str]] = []

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            executions.append((t.task_id, skill, channel))
            return {"status": "completed", "output": "done"}

        worker = AgentWorker(store, registry, checkpointer)
        worker.set_executor(executor)

        assert worker._poll() == 1
        assert len(executions) == 1

        # Task should be completed
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_no_agent_available_skips(self):
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()

        task = Task(task_id="t1", capability="dev", description="Fix bug")
        store.put(task)

        worker = AgentWorker(store, registry, checkpointer)
        assert worker._poll() == 0  # No agent, no dispatch
        assert store.get("t1").status.value == "pending"  # Still pending

    def test_failed_execution(self):
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        store.put(Task(task_id="t1", capability="dev", description="Fix bug"))

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            return {"status": "failed", "error": "runtime error"}

        worker = AgentWorker(store, registry, checkpointer)
        worker.set_executor(executor)
        assert worker._poll() == 1

        failed = store.get("t1")
        assert failed is not None
        assert failed.status.value == "failed"
        assert failed.error == "runtime error"

    def test_capability_matching(self):
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()

        registry.register(Agent(agent_id="agent.review", name="Review", capabilities=["review"]))
        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))

        store.put(Task(task_id="t1", capability="dev", description="Dev task"))
        store.put(Task(task_id="t2", capability="review", description="Review task"))

        executions: list[str] = []

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            executions.append(t.task_id)
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(store, registry, checkpointer)
        worker.set_executor(executor)
        assert worker._poll() == 2

        assert store.get("t1").agent_id == "agent.dev"
        assert store.get("t2").agent_id == "agent.review"

    def test_max_5_per_poll(self):
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        for i in range(10):
            store.put(Task(task_id=f"t{i}", capability="dev", description=f"Task {i}"))

        executions = []

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            executions.append(t.task_id)
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(store, registry, checkpointer)
        worker.set_executor(executor)
        assert worker._poll() == 5
        assert len(executions) == 5

    def test_classify_and_pass_skill_channel(self):
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        store.put(Task(task_id="t1", capability="dev", description="调研RAG系统"))

        last_skill = ""
        last_channel = ""

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            nonlocal last_skill, last_channel
            last_skill = skill
            last_channel = channel
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(store, registry, checkpointer)
        worker.set_executor(executor)
        worker._poll()
        assert last_skill == "pdf"
        assert last_channel == "analysis"

    def test_start_stop(self):
        worker = AgentWorker(TaskStore(), AgentRegistry(), _MockCheckpointer())

        async def run():
            t = asyncio.create_task(worker.run())
            await asyncio.sleep(0.1)
            await worker.stop()
            await t

        asyncio.run(run())

    # ------------------------------------------------------------------
    # Orchestrator integration tests
    # ------------------------------------------------------------------

    def test_orchestrator_with_gate_store_no_crash(self):
        """gate_store + orchestrator should work even without gate steps in plan."""
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        orchestrator = SkillOrchestrator()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research on any topic using web search and analysis"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        store.put(Task(task_id="t1", capability="dev", description="调研最新AI论文"))

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            return {"status": "completed", "output": "results"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)

        worker._poll()

        # No gates should be created (plan doesn't have gate steps by default)
        all_gates = gate_store.list(task_id="t1")
        assert len(all_gates) == 0

        # Task completes normally
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_orchestrator_no_gate_store_skips_gate_creation(self):
        """Without gate_store, orchestrator path should still succeed (no gate crash)."""
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        orchestrator = SkillOrchestrator()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research on any topic using web search and analysis"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        store.put(Task(task_id="t1", capability="dev", description="调研最新AI论文"))

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            return {"status": "completed", "output": "results"}

        worker = AgentWorker(
            store, registry, checkpointer,
            orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)

        worker._poll()
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_orchestrator_empty_plan_does_not_crash(self):
        """Empty plan should complete the task with a note."""
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        orchestrator = SkillOrchestrator()
        # Empty catalog → empty plan
        catalog = SkillCatalog(skills=())

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        store.put(Task(task_id="t1", capability="dev", description="xyzzy_nonexistent_task"))

        worker = AgentWorker(
            store, registry, checkpointer,
            orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(lambda t, s, c: {"status": "completed", "output": "ok"})
        worker._poll()

        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_dispatch_with_plan_creates_gate_records(self):
        """_dispatch_with_plan should create HumanGate records for gate steps."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research on any topic using web search and analysis"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        # Mock orchestrator to return a plan with gates
        from deerflow.tasks.orchestrator import ExecutionPlan, ExecutionStep
        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
            ExecutionStep(skill="deep-research", channel="analysis", description="Analysis"),
        ])

        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        executed_skills = []

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            executed_skills.append(skill)
            return {"status": "completed", "output": "results"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        # Gate should have been created
        gates = gate_store.find_pending_by_task("t1")
        assert len(gates) == 1
        assert gates[0].description == "Human review"
        assert gates[0].step_index == 1

        # First step (deep-research/full) should have executed before the gate
        assert executed_skills == ["deep-research"]

        # Task should still be in executing status (paused at gate)
        task_state = store.get("t1")
        assert task_state is not None
        assert task_state.status.value == "executing"

    def test_orchestrator_executes_all_steps_in_plan(self):
        """All non-gate steps should be executed."""
        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research on any topic using web search and analysis"),
            _make_skill("data-analysis", "Analyze data, create visualizations, and derive insights"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        store.put(Task(task_id="t1", capability="dev", description="research and analyze"))

        executed: list[str] = []

        def executor(t: Task, skill: str, channel: str) -> dict[str, Any]:
            executed.append(skill)
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store,
            orchestrator=SkillOrchestrator(), skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        assert len(executed) >= 1

    # ------------------------------------------------------------------
    # _execute_steps tests (extracted method)
    # ------------------------------------------------------------------

    def test_execute_steps_all_succeed(self):
        """_execute_steps should execute all steps and return (results, True, None)."""
        worker = AgentWorker(TaskStore(), AgentRegistry(), _MockCheckpointer())

        steps = [
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Analysis"),
        ]

        executed: list[str] = []

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            executed.append(s)
            return {"status": "completed", "output": "ok"}

        worker.set_executor(executor)

        task = Task(task_id="t1", capability="dev", description="test")
        results, ok, gate_idx = worker._execute_steps(steps, task)

        assert len(executed) == 2
        assert ok is True
        assert gate_idx is None
        assert len(results) == 2

    def test_execute_steps_creates_gate(self):
        """_execute_steps should create a HumanGate at the gate step and return gate_index."""
        gate_store = HumanGateStore()
        worker = AgentWorker(TaskStore(), AgentRegistry(), _MockCheckpointer(), gate_store=gate_store)

        steps = [
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review required"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Analysis"),
        ]

        executed: list[str] = []

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            executed.append(s)
            return {"status": "completed", "output": "ok"}

        worker.set_executor(executor)

        task = Task(task_id="t1", capability="dev", description="test")
        results, ok, gate_idx = worker._execute_steps(steps, task)

        assert gate_idx == 1  # gate at index 1
        assert ok is True  # prior steps succeeded
        assert executed == ["deep-research"]  # only first step before gate

        # Gate record should exist
        gates = gate_store.find_pending_by_task("t1")
        assert len(gates) == 1
        assert gates[0].description == "Human review required"

    def test_execute_steps_step_failure(self):
        """_execute_steps should return all_succeeded=False when a step fails."""
        worker = AgentWorker(TaskStore(), AgentRegistry(), _MockCheckpointer())

        steps = [
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Analysis"),
        ]

        call_count = 0

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return {"status": "failed", "error": "analysis error"}
            return {"status": "completed", "output": "ok"}

        worker.set_executor(executor)

        task = Task(task_id="t1", capability="dev", description="test")
        results, ok, gate_idx = worker._execute_steps(steps, task)

        assert ok is False
        assert gate_idx is None
        # All steps up to and including the failure are in results
        assert len(results) == 2  # first step ok, second failed

    def test_execute_steps_no_executor(self):
        """_execute_steps with no executor returns ([], True, None)."""
        worker = AgentWorker(TaskStore(), AgentRegistry(), _MockCheckpointer())
        task = Task(task_id="t1", capability="dev", description="test")
        results, ok, gate_idx = worker._execute_steps([], task)
        assert ok is True
        assert gate_idx is None
        assert results == []

    def test_dispatch_with_plan_saves_gate_pause_state(self):
        """_dispatch_with_plan should save gate_paused checkpoint with remaining steps."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research"),
            _make_skill("data-analysis", "Analyze data"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Analysis"),
        ])

        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        # Verify gate_paused checkpoint exists with remaining steps
        gate_saves = [s for s in checkpointer.saves if s["phase"] == "gate_paused"]
        assert len(gate_saves) == 1, f"Expected 1 gate_paused save, got {len(gate_saves)}"

        ctx = gate_saves[0]["context"]
        assert "remaining_steps" in ctx
        assert len(ctx["remaining_steps"]) == 1  # one step after gate
        assert ctx["remaining_steps"][0]["skill"] == "data-analysis"
        assert ctx["gate_step_index"] == 1

    # ------------------------------------------------------------------
    # Gate resume tests (_check_gate_resumes / _resume_after_gate)
    # ------------------------------------------------------------------

    def test_check_gate_resumes_approve_and_resume(self):
        """_check_gate_resumes should resume task when gate is approved."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research"),
            _make_skill("data-analysis", "Analyze data"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Analysis"),
        ])
        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        executed_skills: list[str] = []

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            executed_skills.append(s)
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        # Task should be paused at gate with step 0 completed
        assert len(executed_skills) == 1
        assert executed_skills[0] == "deep-research"
        assert store.get("t1").status.value == "executing"

        # Approve the gate
        gates = gate_store.find_pending_by_task("t1")
        assert len(gates) == 1
        gate_store.approve(gates[0].gate_id, approved_by="reviewer")

        # Resume should execute remaining steps
        resumed = worker._check_gate_resumes()
        assert resumed == 1
        assert executed_skills == ["deep-research", "data-analysis"]

        # Task should be completed
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_check_gate_resumes_reject_fails_task(self):
        """_check_gate_resumes should fail the task when gate is rejected."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
        ])
        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        assert store.get("t1").status.value == "executing"

        # Reject the gate
        gates = gate_store.find_pending_by_task("t1")
        gate_store.reject(gates[0].gate_id, approved_by="reviewer")

        resumed = worker._check_gate_resumes()
        assert resumed == 1

        # Task should be failed
        failed = store.get("t1")
        assert failed is not None
        assert failed.status.value == "failed"
        assert "rejected" in (failed.error or "").lower()

    def test_check_gate_resumes_no_resolved_gates(self):
        """_check_gate_resumes should not touch tasks with unresolved gates."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(_make_skill("deep-research", "Research"),))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
        ])
        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        assert store.get("t1").status.value == "executing"
        # Gate is still pending — resume should not fire
        resumed = worker._check_gate_resumes()
        assert resumed == 0

    def test_resume_after_gate_no_remaining_steps(self):
        """_resume_after_gate should complete task when no steps remain after gate."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(_make_skill("deep-research", "Research"),))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        # Plan where gate is the last step
        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
        ])
        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        # Approve gate
        gates = gate_store.find_pending_by_task("t1")
        gate_store.approve(gates[0].gate_id)

        # Resume — no remaining steps, task should complete
        resumed = worker._check_gate_resumes()
        assert resumed == 1
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_multiple_gates_in_sequence(self):
        """Task with multiple gates should pause/resume across cycles."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research"),
            _make_skill("data-analysis", "Analyze data"),
            _make_skill("code-documentation", "Write documentation"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        # Three phases separated by gates
        plan_with_gates = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Phase 1"),
            ExecutionStep.gate("Gate 1: review research"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Phase 2"),
            ExecutionStep.gate("Gate 2: review analysis"),
            ExecutionStep(skill="code-documentation", channel="standard", description="Phase 3"),
        ])
        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gates

        executed_skills: list[str] = []

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            executed_skills.append(s)
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)
        worker._poll()

        # Phase 1: first step executed, paused at Gate 1
        assert executed_skills == ["deep-research"]
        task_state = store.get("t1")
        assert task_state is not None
        assert task_state.status.value == "executing"
        pending = gate_store.find_pending_by_task("t1")
        assert len(pending) == 1

        # Approve Gate 1 → resume
        gate_store.approve(pending[0].gate_id, approved_by="reviewer")
        worker._check_gate_resumes()

        # Phase 2: data-analysis executed, paused at Gate 2
        assert executed_skills == ["deep-research", "data-analysis"]
        pending = gate_store.find_pending_by_task("t1")
        assert len(pending) == 1  # Gate 2 is now pending

        # Approve Gate 2 → resume
        gate_store.approve(pending[0].gate_id, approved_by="reviewer")
        worker._check_gate_resumes()

        # Phase 3: code-documentation executed, task completed
        assert executed_skills == ["deep-research", "data-analysis", "code-documentation"]
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"

    def test_poll_integrates_gate_resume(self):
        """_poll should both dispatch pending tasks and resume gate-paused tasks."""
        import unittest.mock as mock

        store = TaskStore()
        registry = AgentRegistry()
        checkpointer = _MockCheckpointer()
        gate_store = HumanGateStore()
        catalog = SkillCatalog(skills=(
            _make_skill("deep-research", "Comprehensive research"),
            _make_skill("data-analysis", "Analyze data"),
        ))

        registry.register(Agent(agent_id="agent.dev", name="Dev", capabilities=["dev"]))
        task = Task(task_id="t1", capability="dev", description="research")
        store.put(task)

        plan_with_gate = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full", description="Research"),
            ExecutionStep.gate("Human review"),
            ExecutionStep(skill="data-analysis", channel="analysis", description="Analysis"),
        ])
        orchestrator = mock.MagicMock(spec=SkillOrchestrator)
        orchestrator.plan.return_value = plan_with_gate

        executed_skills: list[str] = []

        def executor(t: Task, s: str, c: str) -> dict[str, Any]:
            executed_skills.append(s)
            return {"status": "completed", "output": "ok"}

        worker = AgentWorker(
            store, registry, checkpointer,
            gate_store=gate_store, orchestrator=orchestrator, skill_catalog=catalog,
        )
        worker.set_executor(executor)

        # First poll: dispatch the task (pauses at gate)
        count1 = worker._poll()
        assert count1 == 1  # 1 dispatch, 0 resumes
        assert executed_skills == ["deep-research"]

        # Second poll: no pending tasks, no resolved gates → 0
        count2 = worker._poll()
        assert count2 == 0

        # Approve gate
        gates = gate_store.find_pending_by_task("t1")
        gate_store.approve(gates[0].gate_id, approved_by="reviewer")

        # Third poll: should resume the task
        count3 = worker._poll()
        assert count3 == 1  # 0 dispatches, 1 resume
        assert executed_skills == ["deep-research", "data-analysis"]

        # Task should be completed
        completed = store.get("t1")
        assert completed is not None
        assert completed.status.value == "completed"
