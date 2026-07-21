"""Tests for AgentWorker background execution."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from deerflow.agents.model import Agent
from deerflow.agents.registry import AgentRegistry
from deerflow.runtime.agent_worker import AgentWorker
from deerflow.runtime.checkpointer.task_checkpointer import TaskCheckpointer
from deerflow.tasks.model import Task
from deerflow.tasks.store import TaskStore


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
