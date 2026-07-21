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
from deerflow.tasks.orchestrator import PlanStepKind, SkillOrchestrator
from deerflow.tasks.store import TaskStore
from pathlib import Path


class _MockCheckpointer:
    def __init__(self):
        self.saves: list[dict] = []
        self.cleared: list[str] = []

    def save(self, task: Task, phase: str, context: dict | None = None) -> str:
        self.saves.append({"task_id": task.task_id, "phase": phase, "context": context})
        return f"cp_{task.task_id}_{phase}"

    def restore(self, task: Task) -> dict | None:
        return None

    def has_checkpoint(self, task_id: str) -> bool:
        return False

    def clear(self, task_id: str) -> None:
        self.cleared.append(task_id)


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
